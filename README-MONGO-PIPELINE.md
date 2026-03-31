# MongoDB collections & AI pipeline

**End-to-end automation:** scrapers ingest into **`raw_articles`** (`pipeline_status: pending`) → **`run_ai_pipeline`** normalizes text, runs the **credibility (fake/suspicious/real)** model, builds **summary**, **entities**, and **`topic_keywords`** → upserts **`processed_articles`** and marks raw **`done`**. The app serves **`/api/user/feed`**: users’ saved **keywords** (`user_keywords`) are matched against each article’s **full haystack** (title, body, `topic_keywords`, entities); optional **`?q=`** narrows results. Re-process old docs by setting raw status back to `pending` or re-running pipeline on new fields only via a future backfill command.

Same database name as `MONGODB_RAW_DATABASE` (default `TRAK_DB`).

## Collections

| Collection | Purpose |
|------------|---------|
| `raw_articles` | Scraper output; `pipeline_status`: `pending` / `processing` / `done` / `failed` |
| `processed_articles` | NLP + credibility + summary + **topic_keywords** + entities; unique `canonical_url` where possible |
| `user_keywords` | One doc per `user_id` (Django PK): `keywords[]`, timestamps |

## `processed_articles` fields (main)

- `canonical_url`, `title`, `source_key`, `published_at`, `clean_text`, `summary`, `entities`, **`topic_keywords`** (for feed matching)
- `credibility_label` — `0` real, `1` fake, `2` suspicious
- `credibility_probs`, `credibility_max_prob`, `credibility_model_id`, `credibility_labels_map`
- `processed_at`, `model_versions`

## Pipeline code

- Orchestration: `news/pipeline/orchestrator.py`
- Credibility: `news/credibility/inference.py` (HuggingFace if `CREDIBILITY_MODEL_PATH` set + deps installed)
- Shared DB helpers: `news/mongo_db.py`
- Raw insert helpers: `news/scrapers/storage.py`

## Commands

```bash
python manage.py ensure_mongo_indexes
python manage.py run_ai_pipeline --limit 50
```

Optional **`metadata.json`** next to the saved model (same folder as `CREDIBILITY_MODEL_PATH`):

```json
{ "confidence_threshold": 0.55, "model_name": "roberta-base", "trained_at": "2026-03-01" }
```

If `confidence_threshold` is set, inference uses it instead of the `CREDIBILITY_CONFIDENCE_THRESHOLD` env var.

Training script (offline): `scripts/train_credibility.py` + `requirements-ml.txt`.
