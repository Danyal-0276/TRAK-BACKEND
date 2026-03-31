# Djongo migrations (MongoDB + Django)

## If `migrate` exits with code 1 but shows “OK” for each step

Djongo 1.3.x with **PyMongo 4.x** can raise `NotImplementedError` on **connection close** (`Database` objects no longer support `bool()`). The migrations usually **did apply** — confirm with:

```bash
python manage.py showmigrations
```

or inspect collection `django_migrations` and `accounts_user` in MongoDB Compass.

## `contenttypes.0002_remove_content_type_name`

Djongo often **cannot** run this migration (it issues `ALTER TABLE … DROP COLUMN`). After a **fresh** database:

```bash
python manage.py migrate contenttypes 0001
python manage.py migrate contenttypes 0002 --fake
python manage.py migrate
```

## Inconsistent history (`admin` before `accounts`)

Happens if the DB was created with Django’s default `auth_user` before `AUTH_USER_MODEL = accounts.User`. For local dev, a **reset** of database `TRAK_DB` plus full migrate is the reliable fix (backup `raw_articles` first). See `scripts/_backup_raw_then_reset_db.py` as a reference (destructive).

## Long-term

Moving **Django users** to **SQLite/PostgreSQL** and keeping **MongoDB only for articles** (pymongo) avoids most djongo migration pain. See `docs/TRAKL/12-production-readiness.md`.
