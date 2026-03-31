# TRAK Backend (Django)

Django 3.2 + **djongo** (MongoDB) for the default database (users), **pymongo** for `raw_articles`, `processed_articles`, and `user_keywords`.

## Setup

```bash
cd Backend/TRAK_Backend
python -m venv venv
venv\Scripts\activate   # Windows
pip install -r requirements.txt
copy .env.example .env   # edit ADMIN_EMAILS, DJANGO_SECRET_KEY, MONGODB_URI
python manage.py migrate
python manage.py ensure_mongo_indexes
python manage.py createsuperuser   # optional; email = USERNAME_FIELD
python manage.py runserver
```

Optional ML stack (training / GPU inference): `pip install -r requirements-ml.txt`

## Important environment variables

See `.env.example`. Notable:

- `ADMIN_EMAILS` — comma-separated; these emails get `role=admin` on **register**.
- `MONGODB_URI`, `MONGODB_RAW_DATABASE`
- `CREDIBILITY_MODEL_PATH` — folder with saved HuggingFace model (optional); otherwise pipeline uses heuristic stub.
- `CREDIBILITY_CONFIDENCE_THRESHOLD` — default `0.6`; max softmax below this → label suspicious (`2`).
- `DJANGO_DEBUG`, `CORS_ALLOWED_ORIGINS` — tighten for production.

## Management commands

| Command | Purpose |
|---------|---------|
| `python manage.py ensure_mongo_indexes` | Indexes on raw / processed / user_keywords |
| `python manage.py run_ai_pipeline --limit 20` | Process pending `raw_articles` → `processed_articles` |
| `python manage.py scrape_raw_news` | Existing scraper (unchanged) |

## Production notes

Set `DJANGO_DEBUG=False`, non-default `DJANGO_SECRET_KEY`, explicit `CORS_ALLOWED_ORIGINS` (default with `DEBUG=False` turns off `CORS_ALLOW_ALL_ORIGINS` unless you set it). HTTPS extras: `DJANGO_SECURE_SSL_REDIRECT`, `SECURE_HSTS_*`. Auth endpoints use scoped rate limits (`THROTTLE_*` in `.env.example`).

## Default admins & diagnostics

- [README-DEFAULT-ADMINS.md](README-DEFAULT-ADMINS.md) — Danyal, Shahroz, Abdullah accounts; `seed_default_admins`, `trak_diagnostics`
- Start **MongoDB** before `migrate` (djongo).

## Related READMEs

- [README-DJONGO-MIGRATIONS.md](README-DJONGO-MIGRATIONS.md) — djongo + Mongo quirks (`--fake` for `contenttypes.0002`, exit code 1 on close)
- [README-AUTH-JWT.md](README-AUTH-JWT.md) — `/api/auth/*`, `/api/user/*`, `/api/admin/*`
- [README-MONGO-PIPELINE.md](README-MONGO-PIPELINE.md) — document shapes & pipeline stages
