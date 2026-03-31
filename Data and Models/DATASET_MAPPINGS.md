# Unified training data: source mappings

This document describes how raw public datasets under `Data and Models` map into the **unified 3-class** training file (`processed/unified_multiclass_balanced.parquet` and `processed/unified_multiclass.csv`). Code: `dataset_unify.py`, notebook: `notebooks/01_unify_training_dataset.ipynb`.

## Unified schema

| Column        | Type   | Description |
|---------------|--------|-------------|
| `text`        | string | Normalized statement / article text used as input to the classifier. |
| `label`       | int    | **0** = real, **1** = fake, **2** = suspicious (mixed or non-binary verdict). |
| `source`      | string | Dataset lineage: `liar`, `isot`, `buzzfeed`, `politifact`, or `archive1_other`. Present in Parquet full/balanced outputs. |
| `label_name`  | string | `real` / `fake` / `suspicious` — convenience column in Parquet only. |

Training script `train_credibility.py` expects **`text`** and **`label`** only; use either CSV or Parquet (see Backend README).

## Text normalization (all sources)

Applied in `normalize_text()` after raw fields are combined:

1. **Unicode:** NFKC normalization.
2. **Case:** lowercased.
3. **URLs:** `http(s)://…` and `www.…` tokens removed (replaced with space).
4. **Characters:** non word-characters (Unicode `\w` versus whitespace) replaced with a space.
5. **Whitespace:** collapsed to single spaces, trimmed.

Rows with `text` shorter than **20** characters after normalization are dropped. **Duplicate** `text` values are dropped (first occurrence kept).

## Source: LIAR (`archive/`)

Reference: `archive/README` (ACL 2017 LIAR benchmark).

| Raw TSV column (1-based) | Field        | Usage |
|--------------------------|--------------|--------|
| 1                        | ID           | Ignored for training. |
| 2                        | verdict label| Mapped to unified `label` (below). |
| 3                        | statement    | Becomes `text` after normalization. |
| 4+                       | metadata     | Ignored. |

**Files loaded:** `train.tsv`, `valid.tsv`, `test.tsv` (all concatenated). `source` = `liar`.

### LIAR verdict → `label`

| Raw label (lowercase) | `label` | `label_name`   |
|-----------------------|---------|----------------|
| `true`, `mostly-true` | 0       | real           |
| `false`, `pants-fire` | 1       | fake           |
| `half-true`, `barely-true`, and any other value | 2 | suspicious |

## Source: ISOT-style (`archive (2)/News_Dataset/`)

Binary **real vs fake** news CSVs (e.g. Kaggle ISOT “True.csv” / “Fake.csv” layout).

| Raw CSV column | Usage |
|----------------|--------|
| `title`        | Concatenated with `text` as `"title text"` when both exist. |
| `text`         | Body; if `title` missing, body alone is used. |
| `subject`, `date` | Ignored. |

| File     | Unified `label` | `source` |
|----------|-----------------|----------|
| `True.csv`  | 0 (real)    | `isot` |
| `Fake.csv`  | 1 (fake)    | `isot` |

## Source: BuzzFeed & PolitiFact (`archive (1)/`)

| File pattern        | Unified `label` | `source`   |
|--------------------|-----------------|------------|
| `*real*.csv`      | 0               | `buzzfeed` or `politifact` (from filename substring). |
| `*fake*.csv`      | 1               | same       |

| Raw CSV column | Usage |
|----------------|--------|
| `title`        | Concatenated with `text` when both present. |
| `text`         | Article body. |
| Other columns (`id`, `url`, …) | Ignored. |

If `title` / `text` are missing, fallback is the last column (legacy behavior).

## Balancing

After merge and filtering, **each class** (0, 1, 2) is upsampled with replacement to the **minimum** class count, then rows are shuffled (`random_state=42`). **All three labels must be present** (LIAR is required for class **2**).

## Output files

| Path | Contents |
|------|-----------|
| `processed/unified_multiclass_full.parquet` | Pre-balance, deduped; `text`, `label`, `source`, `label_name`. |
| `processed/unified_multiclass_balanced.parquet` | Balanced; same columns. |
| `processed/unified_multiclass.csv` | Balanced; **`text`, `label` only** (backward compatible). |
