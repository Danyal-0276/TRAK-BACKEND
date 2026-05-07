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
import random
from datetime import datetime, timezone

import numpy as np
import pandas as pd
from sklearn.metrics import classification_report, confusion_matrix, roc_auc_score
from sklearn.model_selection import train_test_split
from sklearn.utils.class_weight import compute_class_weight

_ID2LABEL = {0: "real", 1: "fake", 2: "suspicious"}
_LABEL_NAMES = ["real", "fake", "suspicious"]


def _inject_ner_tags(text: str, nlp, max_chars: int) -> str:
    """Inject lightweight entity tags inline: 'Joe Biden [PERSON]'."""
    src = (text or "")[:max_chars]
    if not src.strip():
        return src
    doc = nlp(src)
    out = src
    for ent in reversed(doc.ents):
        out = out[: ent.end_char] + f" [{ent.label_}]" + out[ent.end_char :]
    return out


def main():
    seed = int(os.environ.get("SEED", "42"))
    random.seed(seed)
    np.random.seed(seed)

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
    df["text"] = df["text"].astype(str).str.strip()
    df = df[df["text"] != ""]
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
            EarlyStoppingCallback,
            AutoTokenizer,
            Trainer,
            TrainingArguments,
        )
    except ImportError as e:
        raise SystemExit("pip install -r requirements-ml.txt") from e

    import torch
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)

    model_name = os.environ.get("CREDIBILITY_BASE_MODEL", "roberta-base")
    out_dir = os.environ.get("CREDIBILITY_OUT_DIR", "ml_artifacts/credibility/latest")
    use_ner = os.environ.get("USE_NER", "false").strip().lower() in {"1", "true", "yes", "on"}
    ner_strategy = os.environ.get("NER_STRATEGY", "inject").strip().lower()
    max_ner_chars = int(os.environ.get("NER_MAX_CHARS", "1200"))

    if use_ner and ner_strategy == "inject":
        try:
            import spacy
            nlp = spacy.load(os.environ.get("SPACY_MODEL", "en_core_web_sm"))
            print("Applying NER tag injection to training/eval text...")
            df["text"] = df["text"].map(lambda t: _inject_ner_tags(t, nlp, max_ner_chars))
        except Exception as e:
            raise SystemExit(
                f"USE_NER=true with inject strategy requires spaCy model. Error: {e}"
            ) from e

    try:
        train_df, eval_df = train_test_split(
            df, test_size=0.1, random_state=seed, stratify=df["label"]
        )
    except ValueError:
        train_df, eval_df = train_test_split(df, test_size=0.1, random_state=seed)
    tokenizer = AutoTokenizer.from_pretrained(model_name)
    max_seq = int(os.environ.get("CREDIBILITY_MAX_LENGTH", os.environ.get("MAX_LEN", "256")))

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
    lr = float(os.environ.get("LR", "2e-5"))
    warmup_ratio = float(os.environ.get("WARMUP", "0.1"))
    weight_decay = float(os.environ.get("WEIGHT_DECAY", "0.01"))
    fp16 = os.environ.get("FP16", "false").strip().lower() in {"1", "true", "yes", "on"}
    max_steps_env = os.environ.get("CREDIBILITY_MAX_STEPS", "").strip()
    grad_acc_steps = int(os.environ.get("GRAD_ACCUM_STEPS", "1"))

    train_labels = train_df["label"].to_numpy()
    uniq = np.array(sorted(np.unique(train_labels)))
    class_weights_np = compute_class_weight(class_weight="balanced", classes=uniq, y=train_labels)
    class_weights = torch.ones(3, dtype=torch.float)
    for idx, cls in enumerate(uniq.tolist()):
        class_weights[int(cls)] = float(class_weights_np[idx])

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
            learning_rate=lr,
            warmup_ratio=warmup_ratio,
            weight_decay=weight_decay,
            lr_scheduler_type="linear",
            gradient_accumulation_steps=grad_acc_steps,
            fp16=fp16 and torch.cuda.is_available(),
            seed=seed,
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
            learning_rate=lr,
            warmup_ratio=warmup_ratio,
            weight_decay=weight_decay,
            lr_scheduler_type="linear",
            gradient_accumulation_steps=grad_acc_steps,
            fp16=fp16 and torch.cuda.is_available(),
            seed=seed,
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
    class WeightedTrainer(Trainer):
        def compute_loss(self, model, inputs, return_outputs=False, **kwargs):
            labels = inputs.get("labels")
            outputs = model(
                input_ids=inputs.get("input_ids"),
                attention_mask=inputs.get("attention_mask"),
            )
            logits = outputs.get("logits")
            cw = class_weights.to(logits.device)
            loss_fct = torch.nn.CrossEntropyLoss(weight=cw)
            loss = loss_fct(logits.view(-1, model.config.num_labels), labels.view(-1))
            return (loss, outputs) if return_outputs else loss

    callbacks = []
    if not max_steps_env:
        callbacks.append(EarlyStoppingCallback(early_stopping_patience=2))

    trainer = WeightedTrainer(
        model=model,
        args=args,
        train_dataset=train_ds,
        eval_dataset=eval_ds if not max_steps_env else None,
        compute_metrics=compute_metrics if not max_steps_env else None,
        callbacks=callbacks,
        **_tok_kw,
    )
    trainer.train()

    # Final eval: full sklearn report + confusion matrix on best checkpoint
    eval_preds = trainer.predict(eval_ds)
    y_true = np.array(eval_ds["labels"])
    logits = np.array(eval_preds.predictions)
    y_pred = np.argmax(logits, axis=-1)
    exp_logits = np.exp(logits - logits.max(axis=1, keepdims=True))
    y_proba = exp_logits / np.clip(exp_logits.sum(axis=1, keepdims=True), 1e-12, None)

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

    try:
        roc_auc_ovr = float(
            roc_auc_score(y_true, y_proba, multi_class="ovr", labels=[0, 1, 2])
        )
    except ValueError:
        roc_auc_ovr = None

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
        "seed": seed,
        "learning_rate": lr,
        "warmup_ratio": warmup_ratio,
        "weight_decay": weight_decay,
        "gradient_accumulation_steps": grad_acc_steps,
        "fp16": bool(fp16 and torch.cuda.is_available()),
        "class_weights": [float(x) for x in class_weights.cpu().tolist()],
        "use_ner": use_ner,
        "ner_strategy": ner_strategy if use_ner else None,
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
        "roc_auc_ovr": roc_auc_ovr,
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
