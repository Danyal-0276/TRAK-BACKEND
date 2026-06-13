"""Quick diagnostic: category labels vs titles."""
import os
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "TRAK_Backend.settings")
django.setup()

from news.mongo_db import processed_collection
from news.categorization.matching import article_matches_category

col = processed_collection()

print("=== ML-tagged technology (sample) ===")
for d in col.find(
    {"$or": [{"primary_category": "technology"}, {"categories": "technology"}]},
    {"title": 1, "primary_category": 1, "categories": 1, "category_confidence": 1},
).limit(12):
    title = (d.get("title") or "")[:65]
    print(f"  {d.get('primary_category')!r} | {d.get('categories')} | conf={d.get('category_confidence')} | {title}")

print("\n=== Partey / budget / World Cup ===")
for d in col.find(
    {"title": {"$regex": "Partey|Finance minister|World Cup opener", "$options": "i"}},
    {"title": 1, "primary_category": 1, "categories": 1},
).limit(8):
    title = (d.get("title") or "")[:65]
    print(f"  {d.get('primary_category')!r} | {d.get('categories')} | {title}")

labeled = col.count_documents({"primary_category": {"$exists": True, "$nin": ["", None]}})
total = col.count_documents({})
print(f"\nLabeled: {labeled}/{total}")

recent = list(
    col.find(
        {},
        {"title": 1, "primary_category": 1, "categories": 1, "summary": 1, "clean_text": 1, "topic_keywords": 1},
    )
    .sort("processed_at", -1)
    .limit(200)
)
matched = [d for d in recent if article_matches_category(d, "technology")]
print(f"\nRecent 200 via article_matches_category(technology): {len(matched)}")
for d in matched[:10]:
    title = (d.get("title") or "")[:55]
    print(f"  {d.get('primary_category')!r} | {d.get('categories')} | {title}")

unlabeled_match = [d for d in matched if not d.get("primary_category") and not d.get("categories")]
print(f"  (unlabeled legacy matches: {len(unlabeled_match)})")
