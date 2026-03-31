"""
Build a unified, class-balanced 3-class dataset (real=0, fake=1, suspicious=2)
from LIAR (archive/), ISOT (archive (2)/), and BuzzFeed/PolitiFact CSVs (archive (1)/).

Used by notebooks/01_unify_training_dataset.ipynb; can be imported or run as __main__.
"""
from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from typing import List

import pandas as pd
from sklearn.utils import resample

# Unified integer labels (match train_credibility.py / README)
LABEL_REAL = 0
LABEL_FAKE = 1
LABEL_SUSPICIOUS = 2

REQUIRED_LABELS = {LABEL_REAL, LABEL_FAKE, LABEL_SUSPICIOUS}


def normalize_text(text) -> str:
    """Normalize free text for classifier training (documented in DATASET_MAPPINGS.md)."""
    if text is None or (isinstance(text, float) and pd.isna(text)):
        return ""
    text = str(text)
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", text).strip().lower()
    text = re.sub(r"https?://\S+|www\.\S+", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"[^\w\s]", " ", text, flags=re.UNICODE)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def map_liar_label(raw_label) -> int:
    lbl = str(raw_label).strip().lower()
    if lbl in ("true", "mostly-true"):
        return LABEL_REAL
    if lbl in ("false", "pants-fire"):
        return LABEL_FAKE
    return LABEL_SUSPICIOUS


def _combine_title_body(title, body) -> str:
    t = "" if pd.isna(title) else str(title).strip()
    b = "" if pd.isna(body) else str(body).strip()
    if t and b:
        return f"{t} {b}"
    return t or b


def _infer_archive1_source(path: Path) -> str:
    n = path.name.lower()
    if "buzzfeed" in n:
        return "buzzfeed"
    if "politifact" in n:
        return "politifact"
    return "archive1_other"


def load_liar_frames(root: Path) -> List[pd.DataFrame]:
    frames: List[pd.DataFrame] = []
    archive = root / "archive"
    for name in ("train.tsv", "valid.tsv", "test.tsv"):
        p = archive / name
        if not p.exists():
            continue
        # LIAR TSV: col0=id, col1=label, col2=statement
        liar = pd.read_csv(p, sep="\t", header=None, dtype=str)
        liar = liar.rename(columns={1: "label_raw", 2: "text_raw"})[
            ["text_raw", "label_raw"]
        ]
        liar["text"] = liar["text_raw"].map(normalize_text)
        liar["label"] = liar["label_raw"].map(map_liar_label)
        liar["source"] = "liar"
        frames.append(liar[["text", "label", "source"]])
    return frames


def load_isot_frames(root: Path) -> List[pd.DataFrame]:
    frames: List[pd.DataFrame] = []
    base = root / "archive (2)" / "News_Dataset"
    true_p, fake_p = base / "True.csv", base / "Fake.csv"
    for path, label in ((true_p, LABEL_REAL), (fake_p, LABEL_FAKE)):
        if not path.exists():
            continue
        t = pd.read_csv(path)
        title_c = "title" if "title" in t.columns else None
        body_c = "text" if "text" in t.columns else t.columns[0]
        if title_c and title_c in t.columns:
            text_series = t.apply(lambda r: _combine_title_body(r[title_c], r[body_c]), axis=1)
        else:
            text_series = t[body_c]
        frames.append(
            pd.DataFrame(
                {
                    "text": text_series.map(normalize_text),
                    "label": label,
                    "source": "isot",
                }
            )
        )
    return frames


def load_archive1_frames(root: Path) -> List[pd.DataFrame]:
    frames: List[pd.DataFrame] = []
    a1 = root / "archive (1)"
    if not a1.is_dir():
        return frames
    for real_f in sorted(a1.glob("*real*.csv")):
        df = pd.read_csv(real_f)
        title_c, body_c = ("title" in df.columns), ("text" in df.columns)
        if title_c and body_c:
            text_series = df.apply(
                lambda r: _combine_title_body(r.get("title"), r.get("text")), axis=1
            )
        elif body_c:
            text_series = df["text"]
        else:
            text_series = df.iloc[:, -1]
        src = _infer_archive1_source(real_f)
        frames.append(
            pd.DataFrame(
                {
                    "text": text_series.map(normalize_text),
                    "label": LABEL_REAL,
                    "source": src,
                }
            )
        )
    for fake_f in sorted(a1.glob("*fake*.csv")):
        df = pd.read_csv(fake_f)
        if "title" in df.columns and "text" in df.columns:
            text_series = df.apply(
                lambda r: _combine_title_body(r.get("title"), r.get("text")), axis=1
            )
        elif "text" in df.columns:
            text_series = df["text"]
        else:
            text_series = df.iloc[:, -1]
        src = _infer_archive1_source(fake_f)
        frames.append(
            pd.DataFrame(
                {
                    "text": text_series.map(normalize_text),
                    "label": LABEL_FAKE,
                    "source": src,
                }
            )
        )
    return frames


def concat_and_filter(frames: List[pd.DataFrame], min_chars: int = 20) -> pd.DataFrame:
    if not frames:
        raise ValueError("No dataset frames loaded; check archive paths under Data and Models.")
    df = pd.concat(frames, ignore_index=True)
    df = df.dropna(subset=["text"])
    df = df[df["text"].str.len() >= min_chars]
    df = df.drop_duplicates(subset=["text"])
    return df.reset_index(drop=True)


def balance_multiclass(
    df: pd.DataFrame, label_col: str = "label", random_state: int = 42
) -> pd.DataFrame:
    present = set(df[label_col].unique())
    missing = REQUIRED_LABELS - present
    if missing:
        raise ValueError(
            f"Missing labels {missing}. Class 2 (suspicious) comes from LIAR; include archive/train.tsv (and splits)."
        )
    counts = df[label_col].value_counts()
    m = int(counts.min())
    parts = [
        resample(
            df[df[label_col] == i],
            replace=True,
            n_samples=m,
            random_state=random_state,
        )
        for i in sorted(REQUIRED_LABELS)
    ]
    out = pd.concat(parts, ignore_index=True)
    return out.sample(frac=1, random_state=random_state).reset_index(drop=True)


def build_unified(root: Path | None = None, min_chars: int = 20) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Returns (full_before_balance, balanced) with columns text, label, source.
    """
    root = root or Path(__file__).resolve().parent
    frames: List[pd.DataFrame] = []
    frames.extend(load_liar_frames(root))
    frames.extend(load_isot_frames(root))
    frames.extend(load_archive1_frames(root))
    full = concat_and_filter(frames, min_chars=min_chars)
    balanced = balance_multiclass(full)
    return full, balanced


def save_unified_outputs(
    root: Path | None = None, min_chars: int = 20, verbose: bool = True
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Build datasets, write CSV + Parquet under ``<root>/processed/``. Returns (full, balanced)."""
    root = root or Path(__file__).resolve().parent
    processed = root / "processed"
    processed.mkdir(exist_ok=True)
    full, balanced = build_unified(root, min_chars=min_chars)
    if verbose:
        print("Class counts (before balance):\n", full["label"].value_counts().sort_index())
        print("Sources (before balance):\n", full["source"].value_counts())
        print("Class counts (after balance):\n", balanced["label"].value_counts().sort_index())

    csv_path = processed / "unified_multiclass.csv"
    parquet_path = processed / "unified_multiclass_balanced.parquet"
    full_parquet = processed / "unified_multiclass_full.parquet"

    balanced[["text", "label"]].to_csv(csv_path, index=False)
    try:
        full.assign(
            label_name=full["label"].map(
                {0: "real", 1: "fake", 2: "suspicious"}
            )
        ).to_parquet(full_parquet, index=False)
        balanced.assign(
            label_name=balanced["label"].map(
                {0: "real", 1: "fake", 2: "suspicious"}
            )
        ).to_parquet(parquet_path, index=False)
    except ImportError:
        print("Install pyarrow to write parquet: pip install pyarrow")
        raise
    if verbose:
        print("Wrote", csv_path.resolve())
        print("Wrote", parquet_path.resolve())
        print("Wrote", full_parquet.resolve())
    return full, balanced


def main():
    save_unified_outputs()


if __name__ == "__main__":
    main()
