# Data and Models

Folders under this directory hold **public fake-news / stance datasets** used to build a **3-class** training file (real / fake / suspicious).

## Layout (typical)

- `archive/` — LIAR (`train.tsv`, `test.tsv`, …)
- `archive (1)/` — BuzzFeed & PolitiFact-style `*real*.csv` / `*fake*.csv`
- `archive (2)/News_Dataset/` — ISOT-style `True.csv` / `Fake.csv`
- `dataset_unify.py` — shared merge / normalize / balance logic (also runnable: `python dataset_unify.py` from this directory)
- `notebooks/01_unify_training_dataset.ipynb` — calls `dataset_unify.save_unified_outputs()` → `processed/`
- `DATASET_MAPPINGS.md` — raw column → unified label/text/source reference

## Workflow

1. Install Parquet support for the notebook / script: `pip install pyarrow` (included in `Backend/TRAK_Backend/requirements-ml.txt`).
2. Open the notebook (or run `python dataset_unify.py` from `Data and Models`), adjust paths if your folders differ.
3. Outputs:
   - `processed/unified_multiclass.csv` — balanced; columns `text`, `label`
   - `processed/unified_multiclass_balanced.parquet` — balanced + `source`, `label_name`
   - `processed/unified_multiclass_full.parquet` — pre-balance pool with provenance
4. From repo backend folder:

   ```bash
  set CREDIBILITY_TRAIN_CSV=Data and Models\processed\unified_multiclass.csv
   python scripts/train_credibility.py
   ```

   Or use Parquet:

   ```bash
  set CREDIBILITY_TRAIN_PARQUET=Data and Models\processed\unified_multiclass_balanced.parquet
   python scripts/train_credibility.py
   ```

5. Point Django `CREDIBILITY_MODEL_PATH` at the saved HuggingFace directory (e.g. `ml_artifacts/credibility/latest`).

## Labels

- `0` — Real  
- `1` — Fake  
- `2` — Suspicious (LIAR “half-true”, “barely-true”, etc.; plus low-confidence overrides at inference time)
