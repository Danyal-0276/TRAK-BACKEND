import os
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "TRAK_Backend.settings")
django.setup()

from news.services import article_query
from news.platform_taxonomy import get_public_taxonomy

counts = article_query.get_primary_category_counts()
tax = get_public_taxonomy()
print("category_counts keys:", len(counts), "sum:", sum(counts.values()))
for slug, n in sorted(counts.items(), key=lambda x: -x[1])[:15]:
    print(f"  {slug}: {n}")
print("business:", counts.get("business", 0))
print("health:", counts.get("health", 0))
print("technology:", counts.get("technology", 0))
