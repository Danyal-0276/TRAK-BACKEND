"""One-off: copy raw_articles to backup DB, drop TRAK_DB, re-run migrate separately."""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "TRAK_Backend.settings")
django.setup()

from django.conf import settings
from pymongo import MongoClient

client = MongoClient(settings.MONGODB_URI)
src = client[settings.MONGODB_RAW_DATABASE]
backup_db = client["_trak_migrate_backup"]
raw = src["raw_articles"]
n = raw.estimated_document_count()
print(f"raw_articles documents: {n}")
if n:
    backup_db["raw_articles"].drop()
    docs = list(raw.find())
    if docs:
        backup_db["raw_articles"].insert_many(docs)
    print(f"Backed up to _trak_migrate_backup.raw_articles ({len(docs)} docs)")
client.drop_database(settings.MONGODB_RAW_DATABASE)
print(f"Dropped database {settings.MONGODB_RAW_DATABASE}")
