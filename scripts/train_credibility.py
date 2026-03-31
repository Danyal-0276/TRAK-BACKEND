"""
Train 3-class credibility model (real=0, fake=1, suspicious=2).
Prepare unified dataset in `Data and Models` (`dataset_unify.py` or notebook), then:

  set CREDIBILITY_TRAIN_CSV=path/to/unified.csv
  rem or: set CREDIBILITY_TRAIN_PARQUET=path/to/unified_multiclass_balanced.parquet
  python scripts/train_credibility.py

Base model (env): CREDIBILITY_BASE_MODEL — default ``roberta-base``; use
``microsoft/deberta-v3-base`` for DeBERTa-v3.
Optional: CREDIBILITY_MAX_STEPS (e.g. smoke runs on CPU); CREDIBILITY_MAX_LENGTH (default 256).

Outputs:
  - ./ml_artifacts/credibility/latest — model + tokenizer (save_pretrained)
  - ./ml_artifacts/credibility/latest/metrics.json — macro + weighted + per-class
    precision / recall / F1 / support, confusion matrix, training meta
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone

import numpy as np
import pandas as pd
from sklearn.metrics import classification_report, confusion_matrix
from sklearn.model_selection import train_test_split

_ID2LABEL = {0: "real", 1: "fake", 2: "suspicious"}
_LABEL_NAMES = ["real", "fake", "suspicious"]


def main():
    csv_path = os.environ.get("CREDIBILITY_TRAIN_CSV", "").strip()
    parquet_path = os.environ.get("CREDIBILITY_TRAIN_PARQUET", "").strip()
    path = csv_path or parquet_path
    if not path or not os.path.isfile(path):
        print(
            "Set CREDIBILITY_TRAIN_CSV (CSV) or CREDIBILITY_TRAIN_PARQUET (.parquet) "
            "with columns including: text, label"
        )
        return
    if path.lower().endswith(".parquet"):
        df = pd.read_parquet(path)
    else:
        df = pd.read_csv(path)
    for col in ("text", "label"):
        if col not in df.columns:
            raise SystemExit(f"Dataset must have column: {col}")
    df = df[["text", "label"]].copy()
    df = df.dropna(subset=["text", "label"])
    df["label"] = df["label"].astype(int)

    max_train = os.environ.get("CREDIBILITY_MAX_TRAIN", "").strip()
    if max_train:
        n = int(max_train)
        per = max(1, n // 3)
        chunks = [
            g.sample(min(len(g), per), random_state=42)
            for _, g in df.groupby("label", sort=False)
        ]
        df = pd.concat(chunks, ignore_index=True).sample(
            frac=1.0, random_state=42
        ).reset_index(drop=True)

    try:
        import inspect

        from datasets import Dataset
        from transformers import (
            AutoModelForSequenceClassification,
            AutoTokenizer,
            Trainer,
            TrainingArguments,
        )
    except ImportError as e:
        raise SystemExit("pip install -r requirements-ml.txt") from e

    import torch

    model_name = os.environ.get("CREDIBILITY_BASE_MODEL", "roberta-base")
    out_dir = os.environ.get("CREDIBILITY_OUT_DIR", "ml_artifacts/credibility/latest")

    try:
        train_df, eval_df = train_test_split(
            df, test_size=0.1, random_state=42, stratify=df["label"]
        )
    except ValueError:
        train_df, eval_df = train_test_split(df, test_size=0.1, random_state=42)
    tokenizer = AutoTokenizer.from_pretrained(model_name)
    max_seq = int(os.environ.get("CREDIBILITY_MAX_LENGTH", "256"))

    def tok(batch):
        return tokenizer(
            batch["text"], truncation=True, padding=True, max_length=max_seq
        )

    train_ds = Dataset.from_pandas(train_df.reset_index(drop=True))
    eval_ds = Dataset.from_pandas(eval_df.reset_index(drop=True))
    train_ds = train_ds.map(tok, batched=True).remove_columns(["text"])
    eval_ds = eval_ds.map(tok, batched=True).remove_columns(["text"])
    train_ds = train_ds.rename_column("label", "labels")
    eval_ds = eval_ds.rename_column("label", "labels")

    model = AutoModelForSequenceClassification.from_pretrained(
        model_name,
        num_labels=3,
        id2label=_ID2LABEL,
        label2id={"real": 0, "fake": 1, "suspicious": 2},
    )

    runs_dir = out_dir + "_runs"
    pin_mem = torch.cuda.is_available()
    dl_workers = int(os.environ.get("DATALOADER_WORKERS", "0"))
    batch = int(os.environ.get("BATCH", 8))
    max_steps_env = os.environ.get("CREDIBILITY_MAX_STEPS", "").strip()

    if max_steps_env:
        ms = int(max_steps_env)
        args = TrainingArguments(
            output_dir=runs_dir,
            per_device_train_batch_size=batch,
            max_steps=ms,
            eval_strategy="no",
            save_strategy="no",
            load_best_model_at_end=False,
            logging_strategy="steps",
            logging_steps=max(1, ms // 4 or 1),
            dataloader_pin_memory=pin_mem,
            dataloader_num_workers=dl_workers,
        )
        training_meta = {"mode": "max_steps", "max_steps": ms}
    else:
        args = TrainingArguments(
            output_dir=runs_dir,
            per_device_train_batch_size=batch,
            num_train_epochs=float(os.environ.get("EPOCHS", 3)),
            eval_strategy="epoch",
            save_strategy="epoch",
            load_best_model_at_end=True,
            metric_for_best_model="f1_macro",
            greater_is_better=True,
            save_total_limit=2,
            logging_strategy="epoch",
            dataloader_pin_memory=pin_mem,
            dataloader_num_workers=dl_workers,
        )
        training_meta = {
            "mode": "epochs",
            "num_train_epochs": float(args.num_train_epochs),
        }

    def compute_metrics(pred):
        from sklearn.metrics import accuracy_score, f1_score

        logits = pred.predictions
        labels = pred.label_ids
        pred_ids = np.argmax(logits, axis=-1)
        f1_macro = f1_score(labels, pred_ids, average="macro", zero_division=0)
        f1_weighted = f1_score(labels, pred_ids, average="weighted", zero_division=0)
        f1_each = f1_score(
            labels, pred_ids, average=None, labels=[0, 1, 2], zero_division=0
        )
        metrics = {
            "accuracy": accuracy_score(labels, pred_ids),
            "f1_macro": f1_macro,
            "f1_weighted": f1_weighted,
            "f1_real": float(f1_each[0]),
            "f1_fake": float(f1_each[1]),
            "f1_suspicious": float(f1_each[2]),
        }
        return metrics

    _tok_kw = (
        {"processing_class": tokenizer}
        if "processing_class" in inspect.signature(Trainer.__init__).parameters
        else {"tokenizer": tokenizer}
    )
    trainer = Trainer(
        model=model,
        args=args,
        train_dataset=train_ds,
        eval_dataset=eval_ds if not max_steps_env else None,
        compute_metrics=compute_metrics if not max_steps_env else None,
        **_tok_kw,
    )
    trainer.train()

    # Final eval: full sklearn report + confusion matrix on best checkpoint
    eval_preds = trainer.predict(eval_ds)
    y_true = np.array(eval_ds["labels"])
    y_pred = np.argmax(eval_preds.predictions, axis=-1)

    report = classification_report(
        y_true,
        y_pred,
        labels=[0, 1, 2],
        target_names=_LABEL_NAMES,
        output_dict=True,
        zero_division=0,
    )
    cm = confusion_matrix(y_true, y_pred, labels=[0, 1, 2]).tolist()

    per_class = {
        name: {
            "precision": float(report[name]["precision"]),
            "recall": float(report[name]["recall"]),
            "f1-score": float(report[name]["f1-score"]),
            "support": int(report[name]["support"]),
        }
        for name in _LABEL_NAMES
    }

    metrics_doc = {
        "schema": "trak-credibility-eval/v1",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "base_model": model_name,
        "num_labels": 3,
        "id2label": {str(k): v for k, v in _ID2LABEL.items()},
        "train_rows": int(len(train_df)),
        "eval_rows": int(len(eval_df)),
        "max_sequence_length": max_seq,
        "training_schedule": training_meta,
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
        "confusion_matrix": {
            "labels_row_col": [0, 1, 2],
            "matrix": cm,
        },
        "eval_loss": float(eval_preds.metrics.get("test_loss", 0.0))
        if eval_preds.metrics.get("test_loss") is not None
        else None,
    }

    os.makedirs(out_dir, exist_ok=True)
    trainer.save_model(out_dir)
    tokenizer.save_pretrained(out_dir)
    metrics_path = os.path.join(out_dir, "metrics.json")
    with open(metrics_path, "w", encoding="utf-8") as f:
        json.dump(metrics_doc, f, indent=2, ensure_ascii=False)
    print("Saved model + tokenizer to", os.path.abspath(out_dir))
    print("Saved metrics to", os.path.abspath(metrics_path))


if __name__ == "__main__":
    main()
