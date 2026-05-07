from __future__ import annotations

import argparse
import os

import pandas as pd


def main():
    parser = argparse.ArgumentParser(description="Validate credibility training dataset.")
    parser.add_argument(
        "--path",
        default=os.environ.get(
            "CREDIBILITY_TRAIN_CSV",
            r"Data and Models\processed\unified_multiclass.csv",
        ),
        help="CSV/Parquet path with at least columns: text, label",
    )
    args = parser.parse_args()

    path = args.path
    if not os.path.isfile(path):
        raise SystemExit(f"Dataset not found: {path}")

    if path.lower().endswith(".parquet"):
        df = pd.read_parquet(path)
    else:
        df = pd.read_csv(path)

    required = {"text", "label"}
    missing = required - set(df.columns)
    if missing:
        raise SystemExit(f"Missing required columns: {sorted(missing)}")

    total = len(df)
    null_text = int(df["text"].isna().sum())
    null_label = int(df["label"].isna().sum())

    work = df[["text", "label"]].copy()
    work["text"] = work["text"].astype(str).str.strip()

    empty_text = int((work["text"] == "").sum())
    dup_rows = int(work.duplicated().sum())
    dup_text_only = int(work["text"].duplicated().sum())
    label_counts = work["label"].value_counts(dropna=False).sort_index()
    label_pct = (label_counts / max(len(work), 1) * 100.0).round(2)

    print("=== DATASET VALIDATION ===")
    print(f"path: {path}")
    print(f"rows_total: {total}")
    print(f"null_text: {null_text}")
    print(f"null_label: {null_label}")
    print(f"empty_text_after_strip: {empty_text}")
    print(f"duplicate_rows(text+label): {dup_rows}")
    print(f"duplicate_text_only: {dup_text_only}")
    print("\nlabel_distribution:")
    for label in label_counts.index.tolist():
        print(f"  label={label}: count={int(label_counts[label])}, pct={float(label_pct[label])}%")

    min_count = int(label_counts.min()) if len(label_counts) else 0
    max_count = int(label_counts.max()) if len(label_counts) else 0
    imbalance_ratio = round((max_count / max(min_count, 1)), 2) if len(label_counts) > 1 else 1.0
    print(f"\nclass_imbalance_ratio(max/min): {imbalance_ratio}")

    if null_text or null_label or empty_text:
        print("\nWARNING: null/empty values found. Clean dataset before training.")
    if imbalance_ratio >= 2.0:
        print("WARNING: class imbalance detected (ratio >= 2.0).")


if __name__ == "__main__":
    main()
