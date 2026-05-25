"""
Single-file Colab-ready training script for fake detection.

Features:
- Reads CSV or Parquet with required columns: text, label
- Supports binary or multi-class labels automatically
- Class-weighted loss for imbalance
- Periodic checkpoint saving and resume training
- Best-model loading and final sklearn report export

Example (Colab):
  !python train_fake_detection_colab.py \
    --data "/content/unified_multiclass.csv" \
    --output_dir "/content/drive/MyDrive/fake_detection_model" \
    --model_name "roberta-base" \
    --epochs 3 \
    --batch_size 8 \
    --save_steps 200 \
    --eval_steps 200 \
    --logging_steps 50 \
    --resume auto
"""
from __future__ import annotations

import argparse
import json
import os
import random
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import torch
from datasets import Dataset
from sklearn.metrics import classification_report, confusion_matrix, roc_auc_score
from sklearn.model_selection import train_test_split
from sklearn.utils.class_weight import compute_class_weight
from transformers import (
    AutoModelForSequenceClassification,
    AutoTokenizer,
    Trainer,
    TrainingArguments,
)
from transformers.trainer_utils import get_last_checkpoint


def set_seed(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)


def load_dataset(path: str) -> pd.DataFrame:
    if not os.path.isfile(path):
        raise SystemExit(f"Dataset not found: {path}")
    if path.lower().endswith(".parquet"):
        df = pd.read_parquet(path)
    else:
        df = pd.read_csv(path)
    for col in ("text", "label"):
        if col not in df.columns:
            raise SystemExit(f"Dataset must have column: {col}")
    df = df[["text", "label"]].copy()
    df = df.dropna(subset=["text", "label"])
    df["text"] = df["text"].astype(str).str.strip()
    df = df[df["text"] != ""]
    df["label"] = df["label"].astype(int)
    return df.reset_index(drop=True)


def maybe_sample(df: pd.DataFrame, max_train_rows: int | None, seed: int) -> pd.DataFrame:
    if not max_train_rows or max_train_rows <= 0 or len(df) <= max_train_rows:
        return df
    labels = sorted(df["label"].unique().tolist())
    per_class = max(1, max_train_rows // max(1, len(labels)))
    chunks = []
    for _, group in df.groupby("label", sort=False):
        chunks.append(group.sample(n=min(len(group), per_class), random_state=seed))
    out = pd.concat(chunks, ignore_index=True)
    return out.sample(frac=1.0, random_state=seed).reset_index(drop=True)


class WeightedTrainer(Trainer):
    def __init__(self, *args, class_weights: torch.Tensor | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self.class_weights = class_weights

    def compute_loss(self, model, inputs, return_outputs=False, **kwargs):
        labels = inputs.get("labels")
        outputs = model(
            input_ids=inputs.get("input_ids"),
            attention_mask=inputs.get("attention_mask"),
        )
        logits = outputs.get("logits")
        if self.class_weights is not None:
            cw = self.class_weights.to(logits.device)
            loss_fct = torch.nn.CrossEntropyLoss(weight=cw)
        else:
            loss_fct = torch.nn.CrossEntropyLoss()
        loss = loss_fct(logits.view(-1, model.config.num_labels), labels.view(-1))
        return (loss, outputs) if return_outputs else loss


def main() -> None:
    parser = argparse.ArgumentParser(description="Train fake detection model with checkpoints.")
    parser.add_argument("--data", required=True, help="Path to CSV/Parquet with text,label")
    parser.add_argument("--output_dir", default="./ml_artifacts/fake_detection", help="Output folder")
    parser.add_argument("--model_name", default="roberta-base", help="HF base model")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--max_length", type=int, default=256)
    parser.add_argument("--test_size", type=float, default=0.1)
    parser.add_argument("--epochs", type=float, default=3.0)
    parser.add_argument("--batch_size", type=int, default=8)
    parser.add_argument("--learning_rate", type=float, default=2e-5)
    parser.add_argument("--warmup_ratio", type=float, default=0.1)
    parser.add_argument("--weight_decay", type=float, default=0.01)
    parser.add_argument("--grad_accum_steps", type=int, default=1)
    parser.add_argument("--fp16", action="store_true")
    parser.add_argument("--max_train_rows", type=int, default=0)
    parser.add_argument("--save_steps", type=int, default=200)
    parser.add_argument("--eval_steps", type=int, default=200)
    parser.add_argument("--logging_steps", type=int, default=50)
    parser.add_argument("--save_total_limit", type=int, default=3)
    parser.add_argument(
        "--resume",
        default="auto",
        choices=["auto", "none"],
        help="auto: resume from latest checkpoint in output_dir; none: start fresh",
    )
    args = parser.parse_args()

    set_seed(args.seed)
    df = load_dataset(args.data)
    df = maybe_sample(df, args.max_train_rows if args.max_train_rows > 0 else None, args.seed)

    unique_labels = sorted(df["label"].unique().tolist())
    num_labels = len(unique_labels)
    if num_labels < 2:
        raise SystemExit("Need at least 2 unique labels for training.")

    # Build contiguous label map to avoid sparse ids in training.
    old_to_new = {old: i for i, old in enumerate(unique_labels)}
    new_to_old = {i: old for old, i in old_to_new.items()}
    df["label"] = df["label"].map(old_to_new).astype(int)

    label_names = []
    default_names = {0: "real", 1: "fake", 2: "suspicious"}
    for i in range(num_labels):
        label_names.append(default_names.get(new_to_old[i], f"class_{new_to_old[i]}"))

    id2label = {i: name for i, name in enumerate(label_names)}
    label2id = {v: k for k, v in id2label.items()}

    try:
        train_df, eval_df = train_test_split(
            df, test_size=args.test_size, random_state=args.seed, stratify=df["label"]
        )
    except ValueError:
        train_df, eval_df = train_test_split(df, test_size=args.test_size, random_state=args.seed)

    tokenizer = AutoTokenizer.from_pretrained(args.model_name)

    def tok(batch):
        return tokenizer(
            batch["text"],
            truncation=True,
            padding=True,
            max_length=args.max_length,
        )

    train_ds = Dataset.from_pandas(train_df.reset_index(drop=True))
    eval_ds = Dataset.from_pandas(eval_df.reset_index(drop=True))
    train_ds = train_ds.map(tok, batched=True).remove_columns(["text"])
    eval_ds = eval_ds.map(tok, batched=True).remove_columns(["text"])
    train_ds = train_ds.rename_column("label", "labels")
    eval_ds = eval_ds.rename_column("label", "labels")

    model = AutoModelForSequenceClassification.from_pretrained(
        args.model_name,
        num_labels=num_labels,
        id2label=id2label,
        label2id=label2id,
    )

    train_labels = train_df["label"].to_numpy()
    uniq = np.array(sorted(np.unique(train_labels)))
    cw_np = compute_class_weight(class_weight="balanced", classes=uniq, y=train_labels)
    class_weights = torch.ones(num_labels, dtype=torch.float)
    for idx, cls in enumerate(uniq.tolist()):
        class_weights[int(cls)] = float(cw_np[idx])

    training_args = TrainingArguments(
        output_dir=args.output_dir,
        per_device_train_batch_size=args.batch_size,
        per_device_eval_batch_size=args.batch_size,
        num_train_epochs=args.epochs,
        eval_strategy="steps",
        save_strategy="steps",
        eval_steps=max(1, args.eval_steps),
        save_steps=max(1, args.save_steps),
        logging_strategy="steps",
        logging_steps=max(1, args.logging_steps),
        save_total_limit=max(1, args.save_total_limit),
        load_best_model_at_end=True,
        metric_for_best_model="f1_macro",
        greater_is_better=True,
        learning_rate=args.learning_rate,
        warmup_ratio=args.warmup_ratio,
        weight_decay=args.weight_decay,
        gradient_accumulation_steps=max(1, args.grad_accum_steps),
        fp16=bool(args.fp16 and torch.cuda.is_available()),
        dataloader_pin_memory=torch.cuda.is_available(),
        seed=args.seed,
    )

    def compute_metrics(pred):
        from sklearn.metrics import accuracy_score, f1_score

        logits = pred.predictions
        labels = pred.label_ids
        pred_ids = np.argmax(logits, axis=-1)
        f1_macro = f1_score(labels, pred_ids, average="macro", zero_division=0)
        f1_weighted = f1_score(labels, pred_ids, average="weighted", zero_division=0)
        return {
            "accuracy": accuracy_score(labels, pred_ids),
            "f1_macro": f1_macro,
            "f1_weighted": f1_weighted,
        }

    trainer = WeightedTrainer(
        model=model,
        args=training_args,
        train_dataset=train_ds,
        eval_dataset=eval_ds,
        compute_metrics=compute_metrics,
        tokenizer=tokenizer,
        class_weights=class_weights,
    )

    os.makedirs(args.output_dir, exist_ok=True)
    resume_checkpoint = None
    if args.resume == "auto":
        resume_checkpoint = get_last_checkpoint(args.output_dir)
        if resume_checkpoint:
            print(f"Resuming from checkpoint: {resume_checkpoint}")
        else:
            print("No checkpoint found. Starting new training.")

    trainer.train(resume_from_checkpoint=resume_checkpoint)

    eval_preds = trainer.predict(eval_ds)
    y_true = np.array(eval_ds["labels"])
    logits = np.array(eval_preds.predictions)
    y_pred = np.argmax(logits, axis=-1)

    exp_logits = np.exp(logits - logits.max(axis=1, keepdims=True))
    y_proba = exp_logits / np.clip(exp_logits.sum(axis=1, keepdims=True), 1e-12, None)

    report = classification_report(
        y_true,
        y_pred,
        labels=list(range(num_labels)),
        target_names=label_names,
        output_dict=True,
        zero_division=0,
    )
    cm = confusion_matrix(y_true, y_pred, labels=list(range(num_labels))).tolist()

    roc_auc = None
    try:
        if num_labels == 2:
            roc_auc = float(roc_auc_score(y_true, y_proba[:, 1]))
        else:
            roc_auc = float(roc_auc_score(y_true, y_proba, multi_class="ovr"))
    except ValueError:
        roc_auc = None

    per_class = {
        label_names[i]: {
            "precision": float(report[label_names[i]]["precision"]),
            "recall": float(report[label_names[i]]["recall"]),
            "f1-score": float(report[label_names[i]]["f1-score"]),
            "support": int(report[label_names[i]]["support"]),
        }
        for i in range(num_labels)
    }

    metrics_doc = {
        "schema": "fake-detection-eval/v1",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "model_name": args.model_name,
        "num_labels": num_labels,
        "id2label": {str(k): v for k, v in id2label.items()},
        "original_label_ids": {str(k): int(v) for k, v in new_to_old.items()},
        "train_rows": int(len(train_df)),
        "eval_rows": int(len(eval_df)),
        "max_length": args.max_length,
        "seed": args.seed,
        "learning_rate": args.learning_rate,
        "warmup_ratio": args.warmup_ratio,
        "weight_decay": args.weight_decay,
        "gradient_accumulation_steps": args.grad_accum_steps,
        "fp16": bool(args.fp16 and torch.cuda.is_available()),
        "class_weights": [float(x) for x in class_weights.cpu().tolist()],
        "per_class": per_class,
        "macro_avg": {
            "precision": float(report["macro avg"]["precision"]),
            "recall": float(report["macro avg"]["recall"]),
            "f1-score": float(report["macro avg"]["f1-score"]),
            "support": int(report["macro avg"]["support"]),
        },
        "weighted_avg": {
            "precision": float(report["weighted avg"]["precision"]),
            "recall": float(report["weighted avg"]["recall"]),
            "f1-score": float(report["weighted avg"]["f1-score"]),
            "support": int(report["weighted avg"]["support"]),
        },
        "accuracy": float(report["accuracy"]),
        "roc_auc": roc_auc,
        "confusion_matrix": cm,
        "eval_loss": float(eval_preds.metrics.get("test_loss", 0.0))
        if eval_preds.metrics.get("test_loss") is not None
        else None,
    }

    final_dir = os.path.join(args.output_dir, "final")
    os.makedirs(final_dir, exist_ok=True)
    trainer.save_model(final_dir)
    tokenizer.save_pretrained(final_dir)

    metrics_path = os.path.join(final_dir, "metrics.json")
    with open(metrics_path, "w", encoding="utf-8") as f:
        json.dump(metrics_doc, f, indent=2, ensure_ascii=False)

    print(f"Saved final model to: {os.path.abspath(final_dir)}")
    print(f"Saved metrics to: {os.path.abspath(metrics_path)}")
    print("Checkpoint folders are inside output_dir as checkpoint-*")


if __name__ == "__main__":
    main()
