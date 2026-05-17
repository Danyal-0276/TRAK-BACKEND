import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "TRAK_Backend.settings")

import django

django.setup()

from news.mongo_db import processed_collection, raw_collection

p = processed_collection()
r = raw_collection()

total_proc = p.count_documents({})
bart = p.count_documents({"model_versions.summarizer_mode": "bart"})
extractive = p.count_documents({"model_versions.summarizer_mode": "extractive"})
no_mode = p.count_documents({"model_versions.summarizer_mode": {"$exists": False}})
no_mv = p.count_documents({"model_versions": {"$exists": False}})
other = total_proc - bart - extractive - no_mode

raw_total = r.count_documents({})
raw_done = r.count_documents({"pipeline_status": "done"})
raw_pending = r.count_documents({"pipeline_status": "pending"})
raw_processing = r.count_documents({"pipeline_status": "processing"})
raw_failed = r.count_documents({"pipeline_status": "failed"})

print("=== processed_articles ===")
print(f"total: {total_proc}")
print(f"bart_summarized: {bart}")
print(f"extractive: {extractive}")
print(f"no_summarizer_mode: {no_mode}")
print(f"no_model_versions: {no_mv}")
print(f"other_modes: {other}")
print(f"remaining_not_bart: {total_proc - bart}")
print()
print("=== raw_articles ===")
print(f"total: {raw_total}")
print(f"done: {raw_done}")
print(f"pending: {raw_pending}")
print(f"processing: {raw_processing}")
print(f"failed: {raw_failed}")
