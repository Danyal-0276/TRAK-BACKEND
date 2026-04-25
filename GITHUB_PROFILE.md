# TRAK Backend (Django + MongoDB)

TRAK Backend powers the full intelligence layer behind the TRAK platform: authentication, user personalization, news ingestion, AI processing, and API delivery for mobile/web clients.

## What This Project Is For

This repository exists to provide a complete data and intelligence pipeline for trustworthy, personalized news delivery. It handles:

- User identity and role management (including admin assignment logic)
- JWT-based API authentication and authorization
- Raw news ingestion from scrapers into MongoDB
- AI pipeline processing for normalization, credibility scoring, summarization, and keyword extraction
- User feed generation based on tracked topic keywords

It is the operational core of TRAK.

## Problem It Solves

Modern news systems often fail in two ways:

1. **Trust gap**: users cannot quickly assess article reliability
2. **Relevance gap**: generic feeds do not match user interests

This backend addresses both:

- It evaluates ingested articles through a credibility pipeline (real/fake/suspicious)
- It stores user keyword preferences and returns personalized feed results
- It exposes clean API endpoints so frontend clients can serve this intelligence in real time

## Core Capabilities

- **Auth and user APIs**
  - Register, login, profile bootstrap, protected user routes
  - JWT flows using `djangorestframework-simplejwt`
  - Role-aware controls and admin-specific endpoints

- **News ingestion**
  - Scrapers insert into `raw_articles`
  - Articles move through pipeline states (`pending`, `processing`, `done`, `failed`)

- **AI enrichment**
  - Text normalization and processing
  - Credibility inference with confidence threshold logic
  - Summary/entity/topic keyword extraction
  - Upsert into `processed_articles`

- **Personalized feed**
  - Matches user keyword sets against processed article signal fields
  - Supports optional query narrowing for targeted retrieval

## Data Model Overview

Primary collections/tables involved in platform behavior:

- `raw_articles`: unprocessed scraper output with pipeline status
- `processed_articles`: enriched, scored articles ready for consumption
- `user_keywords`: per-user tracked interests used for feed matching
- Django auth/account models for user identities and roles

The result is a clear separation between ingestion data and user-consumable intelligence.

## High-Level Pipeline

1. Scrapers ingest source content into `raw_articles`
2. `run_ai_pipeline` reads pending records
3. Pipeline computes credibility + enrichment fields
4. Processed records are written to `processed_articles`
5. User feed endpoints query processed data based on saved keywords

This architecture enables repeatable processing and future model upgrades without redesigning client apps.

## Tech Stack

- **Backend framework**: Django `3.2+`
- **API layer**: Django REST Framework
- **Auth**: Simple JWT
- **Database strategy**:
  - `djongo` for default Django-compatible data paths
  - `pymongo` for direct pipeline collections
- **Scraping/parsing**: `curl_cffi`, `beautifulsoup4`, `feedparser`
- **Optional ML stack**: separate requirements for training/inference workflows

## Setup & Local Run

```bash
cd Backend/TRAK_Backend
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
python manage.py migrate
python manage.py ensure_mongo_indexes
python manage.py runserver
```

Optional ML dependencies:

```bash
pip install -r requirements-ml.txt
```

## Important Environment Configuration

Use `.env.example` as the base and define:

- `MONGODB_URI` and database names
- `DJANGO_SECRET_KEY`
- `ADMIN_EMAILS` for automatic admin-role assignment on register
- `CORS_ALLOWED_ORIGINS` and secure settings for production
- Model path/threshold settings for credibility inference behavior

## Operational Commands

- `python manage.py ensure_mongo_indexes`: creates/validates Mongo indexes
- `python manage.py scrape_raw_news`: runs ingestion stage
- `python manage.py run_ai_pipeline --limit 20`: processes pending raw articles

## Why This Backend Matters

This service transforms unstructured, high-volume news input into structured, trustworthy, and user-specific output. It directly enables:

- Better trust signals for end users
- More relevant content experiences
- Scalable expansion into richer AI features and moderation workflows

Without this layer, the frontend would only be a generic news browser. With it, TRAK becomes an intelligence-driven product.

## Production Considerations

- Disable debug mode and use secure secrets
- Set strict CORS origin allowlists
- Configure HTTPS and security headers
- Monitor pipeline throughput and failed item retry workflows
- Keep model artifacts versioned and compatible with inference code

## Future Improvements

- Better observability around pipeline latency and failure classes
- Backfill/reprocessing commands for model upgrades
- Queue-based worker orchestration for large-scale ingestion
- Expanded credibility explanations for downstream clients

## Related Documentation

- `README-AUTH-JWT.md`
- `README-MONGO-PIPELINE.md`
- `README-DJONGO-MIGRATIONS.md`
- `README-DEFAULT-ADMINS.md`
