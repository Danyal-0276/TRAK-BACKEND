# TRAK Backend

**Truth & Realtime Alert Keeper** — Django REST API and AI pipeline for the TRAK news platform. Scrapes sources, scores credibility via Hugging Face models, enriches articles, and serves personalized feeds to mobile and web clients.

**Frontend:** [Danyal-0276/TRAK](https://github.com/Danyal-0276/TRAK)

## Features

- Email JWT auth, OTP, social / Firebase login, admin roles
- News scrapers (HTML, RSS, third-party APIs) → MongoDB `raw_articles`
- AI pipeline: fake detection (HF Space ensemble), fact-check, BART summarization, NER, categories, embeddings
- Personalized feeds, keyword alerts, bookmarks, reactions
- Gemini-grounded chatbot over processed articles (lightweight keyword RAG)
- Notifications: in-app, WebSockets (Channels), FCM, email
- Admin REST API for articles, users, scrape/pipeline, taxonomy
- TTS via Edge / optional HF Urdu TTS Space

## Tech stack

| Layer | Stack |
|-------|--------|
| Framework | Django 5.2 + Django REST Framework |
| Auth | SimpleJWT (email users) |
| Database | MongoDB (`django-mongodb-backend` + pymongo) |
| Realtime | Django Channels + Daphne (Redis optional) |
| Scraping | curl_cffi, BeautifulSoup, feedparser |
| Remote ML | Hugging Face Spaces (fake detection, summarizer, TTS) |
| Deploy | Render / Docker / ASGI (`daphne`) |

## High-level pipeline

```
Scrapers → raw_articles (pending)
        → AI pipeline (HF fake detect + summarize + NER + categories)
        → processed_articles
        → feeds / keyword alerts / chatbot / admin
```

Fake detection runs on Space `abd8433/TRAK-Fake-Detection-Model` (5-model ensemble + NewsAPI check). The backend parses the Gradio `/detect` response and merges it with fact-check results.

## Project layout

```
TRAK-BACKEND/
├── accounts/         # Users, OTP, JWT, social auth
├── news/             # Scrapers, pipeline, feeds, TTS, chatbot
├── notifications/    # In-app, FCM, WebSockets, email
├── admin_panel/      # Admin REST API
├── scripts/          # Training & ops helpers
├── TRAK_Backend/     # Settings, URLs, ASGI
└── requirements.txt
```

## Setup

**Requirements:** Python 3.12, MongoDB, `.env` from `.env.example`.

```bash
python -m venv venv
# Windows
venv\Scripts\activate
# macOS / Linux
source venv/bin/activate

pip install -r requirements.txt
copy .env.example .env   # or: cp .env.example .env

python manage.py migrate
python manage.py ensure_mongo_indexes
python manage.py runserver
```

ASGI (WebSockets):

```bash
daphne -b 0.0.0.0 -p 8000 TRAK_Backend.asgi:application
```

Optional ML extras: `pip install -r requirements-ml.txt`

### Important env vars

- `MONGODB_URI`, `MONGODB_DJANGO_DATABASE`, `MONGODB_RAW_DATABASE`
- `DJANGO_SECRET_KEY`, `CORS_ALLOWED_ORIGINS`
- `FAKE_DETECTION_SPACE_ID` / `FAKE_DETECTION_SPACE_API_NAME` (default `/detect`)
- `SUMMARIZER_SPACE_ID`, `HF_TOKEN` (if Spaces are private)
- `GEMINI_API_KEY` for the chatbot
- `TTS_API_BASE_URL` or Edge TTS settings

## Useful commands

```bash
python manage.py scrape_raw_news
python manage.py run_ai_pipeline --limit 20
python manage.py run_news_cycle
python manage.py run_scheduled_scrape
python manage.py seed_default_admins
python manage.py trak_diagnostics
```

## API map

| Prefix | Purpose |
|--------|---------|
| `/api/auth/` | Register, login, OTP, social, profile |
| `/api/user/` | Feed, explore, articles, keywords, TTS, chatbot |
| `/api/notifications/` | List, read, device tokens, prefs |
| `/api/admin/` | Articles, scrape/pipeline, users, settings |

## More docs

- `README-AUTH-JWT.md` — JWT details  
- `README-MONGO-PIPELINE.md` — scrape → process flow  
- `README-DEFAULT-ADMINS.md` — seed admins  
- `deploy/` — Docker / VPS notes  

## License

Private FYP project unless otherwise stated.
