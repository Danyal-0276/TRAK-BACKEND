# TRAK Backend

![Django](https://img.shields.io/badge/Django-5.2-092E20?style=for-the-badge&logo=django&logoColor=white)
![DRF](https://img.shields.io/badge/DRF-REST_API-ff1709?style=for-the-badge&logo=django&logoColor=white)
![Python](https://img.shields.io/badge/Python-3.12-3776AB?style=for-the-badge&logo=python&logoColor=white)
![MongoDB](https://img.shields.io/badge/MongoDB-Atlas%20%2F%20Local-47A248?style=for-the-badge&logo=mongodb&logoColor=white)
![Hugging Face](https://img.shields.io/badge/Hugging_Face-Spaces-FFD21E?style=for-the-badge&logo=huggingface&logoColor=black)
![Channels](https://img.shields.io/badge/Channels-WebSockets-635BFF?style=for-the-badge)
![FYP](https://img.shields.io/badge/Project-FYP-8B5CF6?style=for-the-badge)

**Truth & Realtime Alert Keeper** - Django REST API and AI pipeline for the TRAK news platform. Scrapes sources, runs remote Hugging Face models for **fake detection** and **summarization**, enriches articles in MongoDB, and serves personalized feeds, notifications, TTS, and a grounded chatbot to the mobile/web clients.

| | |
|---|---|
| **Frontend** | [Danyal-0276/TRAK](https://github.com/Danyal-0276/TRAK) |
| **Fake detection Space** | `abd8433/TRAK-Fake-Detection-Model` (`/detect`) |
| **Owner** | [Danyal-0276](https://github.com/Danyal-0276) |

## Problem

Generic news backends only store and list articles. TRAK Backend also:

1. **Scores trust** - real / fake / suspicious via an HF ensemble + fact-check merge
2. **Personalizes** - matches user keywords (and embeddings) to processed articles
3. **Operates end-to-end** - scrape → queue → enrich → feed / alerts / admin

Without this layer, the frontend would be a thin browser. With it, TRAK is an intelligence-driven product.

## Features

- **Auth** - email users, JWT (SimpleJWT), OTP, social OAuth, Firebase login, admin / super-admin roles
- **Ingestion** - HTML scrapers, RSS, Currents / NewsData / GNews APIs → `raw_articles`
- **AI pipeline** - clean text → HF fake detection → fact-check → BART summary → NER → categories + MiniLM embeddings → moderation
- **Feeds** - personalized, explore, search, bookmarks, reactions
- **Chatbot** - Gemini with lightweight **keyword RAG** over `processed_articles`
- **Notifications** - Mongo store, Channels WebSockets, FCM, email fan-out, keyword alerts
- **Admin API** - articles, scrape/pipeline runs, users, taxonomy, analytics, feedback
- **TTS** - Edge TTS and/or HF Urdu TTS Space
- **Ops** - management commands, Render cron, Docker / systemd deploy docs

## Tech stack

| Layer | Technology |
|-------|------------|
| Framework | Django **5.2** + Django REST Framework |
| Auth | SimpleJWT (email `USERNAME_FIELD`) |
| Database | MongoDB via `django-mongodb-backend` + **pymongo** (`TRAK_DB`) |
| Realtime | Django Channels + Daphne (Redis channel layer optional) |
| Scraping | `curl_cffi`, BeautifulSoup, feedparser |
| Remote ML | Hugging Face Spaces (fake detection ensemble, BART summarizer, TTS) |
| Chat | Google Gemini (grounded on retrieved articles) |
| Deploy | Render, Docker, ASGI (`daphne`) |

## Architecture

```
Scrapers ──► raw_articles (pipeline_status)
                 │
                 ▼
         AI pipeline (workers / cron)
         ├─ HF Fake Detection Space (5-model ensemble + NewsAPI)
         ├─ Fact-check merge
         ├─ HF BART summarizer
         ├─ spaCy NER + categories + embeddings
         └─ moderation + keyword alerts
                 │
                 ▼
         processed_articles
                 │
     ┌───────────┼───────────┬────────────┐
     ▼           ▼           ▼            ▼
  REST feeds   Chatbot    Notifications  Admin
  TTS / user   (Gemini)   (WS + FCM)     panel
```

### Data split

| Store | Contents |
|-------|----------|
| `trak_django` (ORM) | `User`, `EmailOtp`, sessions |
| `TRAK_DB` (pymongo) | `raw_articles`, `processed_articles`, profiles, keywords, notifications, bookmarks, reactions, chatbot history |

### Fake detection Space contract

The Space returns Gradio outputs that the backend parses in `news/spaces/client.py`:

```text
(verdict, real_confidence%, fake_confidence%, news_info, debug)
```

Configured via `FAKE_DETECTION_SPACE_ID` + `FAKE_DETECTION_SPACE_API_NAME=/detect`.

## Project structure

```
TRAK-BACKEND/
├── accounts/              # Users, OTP, JWT, social auth
├── news/                  # Scrapers, pipeline, feeds, TTS, chatbot
│   ├── scrapers/
│   ├── pipeline/
│   ├── credibility/
│   ├── summarization/
│   ├── categorization/
│   └── chatbot/
├── notifications/         # In-app, FCM, WebSockets, email
├── admin_panel/           # Admin REST API
├── scripts/               # Training + ops helpers
├── huggingface-space/     # BART summarizer Space source
├── deploy/                # Docker / VPS notes
├── TRAK_Backend/          # settings, urls, asgi
├── requirements.txt
└── requirements-ml.txt
```

## Getting started

**Requirements:** Python **3.12**, MongoDB, env file from `.env.example`.

```bash
git clone https://github.com/Danyal-0276/TRAK-BACKEND.git
cd TRAK-BACKEND

python -m venv venv
# Windows
venv\Scripts\activate
# macOS / Linux
source venv/bin/activate

pip install -r requirements.txt
copy .env.example .env   # or: cp .env.example .env
```

```bash
python manage.py migrate
python manage.py ensure_mongo_indexes
python manage.py runserver
```

### ASGI (WebSockets)

```bash
daphne -b 0.0.0.0 -p 8000 TRAK_Backend.asgi:application
```

Optional ML extras for local training/inference:

```bash
pip install -r requirements-ml.txt
```

### Important environment variables

| Variable | Purpose |
|----------|---------|
| `MONGODB_URI` | Mongo connection string |
| `MONGODB_DJANGO_DATABASE` | ORM DB (default `trak_django`) |
| `MONGODB_RAW_DATABASE` | Content DB (default `TRAK_DB`) |
| `DJANGO_SECRET_KEY` | Django secret (required in production) |
| `CORS_ALLOWED_ORIGINS` | Frontend origins |
| `FAKE_DETECTION_SPACE_ID` | HF Space for fake detection |
| `FAKE_DETECTION_SPACE_API_NAME` | Usually `/detect` |
| `SUMMARIZER_SPACE_ID` | HF BART summarizer Space |
| `HF_TOKEN` | Private Spaces / Hub access |
| `GEMINI_API_KEY` | Chatbot |
| `TTS_API_BASE_URL` | Optional HF TTS API |

## Useful commands

```bash
python manage.py scrape_raw_news
python manage.py run_ai_pipeline --limit 20
python manage.py run_news_cycle
python manage.py run_scheduled_scrape
python manage.py requeue_failed_pipeline
python manage.py seed_default_admins
python manage.py seed_platform_taxonomy
python manage.py trak_diagnostics
```

## API map

| Prefix | Purpose |
|--------|---------|
| `/api/auth/` | Register, login, OTP, social, profile, follows |
| `/api/user/` | Feed, explore, articles, keywords, TTS, chatbot, bookmarks |
| `/api/notifications/` | List, unread, mark-read, device tokens, prefs |
| `/api/admin/` | Articles, scrape/pipeline, users, settings, analytics |
| `ws/notifications/` | User WebSocket notifications |
| `ws/admin/notifications/` | Admin WebSocket notifications |

## Pipeline stages (per article)

1. Claim `raw_articles` with `pipeline_status=pending`
2. Clean / normalize text
3. Call HF fake-detection Space → merge with fact-check
4. Summarize (HF BART → fallback extractive)
5. NER + topic keywords
6. Zero-shot categories + `match_embedding`
7. Moderation status → upsert `processed_articles`
8. Keyword alerts for visible articles

Background work uses Mongo status fields + in-process threads / cron (**no Celery**).

## GitHub topics

`django` · `django-rest-framework` · `mongodb` · `news-scraper` · `fake-news-detection` · `huggingface` · `jwt-auth` · `python` · `channels` · `fyp` · `ai-pipeline` · `portfolio`

### About description

```
Django REST + MongoDB backend for TRAK - news scraping, HF fake-detection/summarization pipeline, JWT auth, feeds, and chatbot.
```

## More documentation

| Doc | Contents |
|-----|----------|
| `README-AUTH-JWT.md` | JWT auth details |
| `README-MONGO-PIPELINE.md` | Scrape → process flow |
| `README-DEFAULT-ADMINS.md` | Seed admin accounts |
| `deploy/` | Docker / VPS runbooks |
| `docs/CATEGORY_ADMIN.md` | Category admin notes |

## Related repositories

- Frontend (RN + Vite web): [TRAK](https://github.com/Danyal-0276/TRAK)

## License

Private university FYP project unless otherwise stated. Not open-sourced by default.
