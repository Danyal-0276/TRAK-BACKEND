"""Print raw_articles pipeline queue breakdown (pending / processing / failed)."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "TRAK_Backend.settings")

import django

django.setup()

from news.mongo_db import raw_collection

col = raw_collection()
print("pipeline_status counts:")
for doc in col.aggregate(
    [{"$group": {"_id": "$pipeline_status", "count": {"$sum": 1}}}]
):
    print(f"  {doc['_id']!r}: {doc['count']}")

for status in ("pending", "processing", "failed"):
    rows = list(
        col.find(
            {"pipeline_status": status},
            {
                "title": 1,
                "canonical_url": 1,
                "processing_started_at": 1,
                "pipeline_error": 1,
            },
        ).limit(5)
    )
    if not rows:
        continue
    print(f"\n--- {status} ({len(rows)} shown) ---")
    for row in rows:
        print(
            f"  title={row.get('title')!r}\n"
            f"  url={row.get('canonical_url')!r}\n"
            f"  started={row.get('processing_started_at')!r}\n"
            f"  error={row.get('pipeline_error')!r}\n"
        )
