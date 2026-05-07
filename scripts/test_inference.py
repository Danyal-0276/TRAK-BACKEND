from __future__ import annotations

import argparse
import json
import os

import torch
from transformers import AutoModelForSequenceClassification, AutoTokenizer


def main():
    parser = argparse.ArgumentParser(description="Smoke test a saved credibility model.")
    parser.add_argument("--model", required=True, help="Path to HF model directory")
    parser.add_argument("--text", required=True, help="Text to classify")
    parser.add_argument("--max-length", type=int, default=512)
    args = parser.parse_args()

    model_dir = args.model
    if not os.path.isdir(model_dir):
        raise SystemExit(f"Model path not found: {model_dir}")

    tokenizer = AutoTokenizer.from_pretrained(model_dir)
    model = AutoModelForSequenceClassification.from_pretrained(model_dir)
    model.eval()

    inputs = tokenizer(
        args.text,
        return_tensors="pt",
        truncation=True,
        max_length=args.max_length,
        padding=True,
    )

    with torch.no_grad():
        logits = model(**inputs).logits[0]
        probs = torch.softmax(logits, dim=-1).tolist()

    pred = int(max(range(len(probs)), key=lambda i: probs[i]))
    id2label = getattr(model.config, "id2label", {}) or {}
    label = str(id2label.get(pred, pred))
    confidence = float(max(probs))

    out = {
        "pred_label_id": pred,
        "pred_label": label,
        "confidence": confidence,
        "probs": probs,
    }
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
