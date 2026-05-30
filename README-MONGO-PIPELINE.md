# MongoDB collections & AI pipeline

**End-to-end automation:** scrapers ingest into **`raw_articles`** (`pipeline_status: pending`) → **`run_ai_pipeline`** normalizes text, runs **fake detection (HF Space)**, **Google Fact Check API** (second pass), **BART summary (HF Space)**, **entities**, and **`topic_keywords`** → upserts **`processed_articles`** and marks raw **`done`**.

## AI stages (in order)

1. **Clean text** — strip HTML/URLs
2. **Fake detection** — `FAKE_DETECTION_SPACE_ID` (Gradio Space)
3. **Fact checker** — `GOOGLE_FACT_CHECK_API_KEY` + `FACT_CHECKER_ENABLED=true` (Claim Search API)
4. **Merge** — final `credibility_label` (real/fake/suspicious)
5. **Summary** — `SUMMARIZER_SPACE_ID` (Gradio Space)
6. **NER + topic keywords**

Env vars: see `.env.example` (`SUMMARIZER_SPACE_ID`, `FAKE_DETECTION_SPACE_ID`, `GOOGLE_FACT_CHECK_API_KEY`).

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

## Dashboard counts vs queue

| Metric | Meaning |
|--------|---------|
| `raw_total` | All rows in `raw_articles` (pending, processing, done, failed) |
| `processed_total` | All rows in `processed_articles` (feed source) |
| `pipeline_summary.queued` | **Backlog** — raw `pending` + `processing` |
| `pipeline_summary.processed_stale` | Raw `pending`/`failed` that still have an old processed row |

`raw_total` and `processed_total` are often **equal** after every article was processed at least once. Re-queueing raw to `pending` does **not** delete `processed_articles`; run the pipeline to refresh AI fields in place.

## Commands

```bash
python manage.py ensure_mongo_indexes

# Serial (default)
python manage.py run_ai_pipeline --limit 50

# Parallel workers (cron / VPS — not Admin HTTP)
python manage.py run_ai_pipeline --all --batch-size 50 --workers 3
python manage.py run_ai_pipeline --requeue-stale   # stuck processing → pending

# Scrape + pipeline (scheduled 1–3x/day)
python manage.py run_news_cycle --scrape-limit 35 --pipeline-all --workers 3 --requeue-stale
```

**Hosting:** API runs under Daphne only. Run heavy pipeline via CLI — see `deploy/vps-systemd.md` (VPS timer) or Render Cron (same command, separate service).

Optional **`metadata.json`** next to the saved model (same folder as `CREDIBILITY_MODEL_PATH`):

```json
{ "confidence_threshold": 0.55, "model_name": "roberta-base", "trained_at": "2026-03-01" }
```

If `confidence_threshold` is set, inference uses it instead of the `CREDIBILITY_CONFIDENCE_THRESHOLD` env var.

Training script (offline): `scripts/train_credibility.py` + `requirements-ml.txt`.
