import os
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "TRAK_Backend.settings")
django.setup()

from news.mongo_db import processed_collection

col = processed_collection()
print("=== primary_category counts (all articles) ===")
for r in col.aggregate(
    [{"$group": {"_id": "$primary_category", "n": {"$sum": 1}}}, {"$sort": {"n": -1}}]
):
    print(f"  {r['_id'] or '(empty)'}: {r['n']}")
print("Total:", col.count_documents({}))
for slug in ("business", "health", "finance", "technology", "education", "entertainment"):
    print(f"{slug}:", col.count_documents({"primary_category": slug}))
