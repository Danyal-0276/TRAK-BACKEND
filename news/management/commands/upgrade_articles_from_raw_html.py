"""
One-time helper: documents that only have `raw_html` are re-parsed into structured fields.

Usage:
  python manage.py upgrade_articles_from_raw_html
"""

from django.core.management.base import BaseCommand

from news.scrapers.document import build_article_document
from news.scrapers.extract.dawn import extract_dawn
from news.scrapers.extract.dunya import extract_dunya
from news.scrapers.extract.generic import extract_generic
from news.scrapers import storage


def _extract(source_key: str, html: str, url: str, extra: dict):
    if source_key == "dawn":
        return extract_dawn(html, url)
    if source_key == "dunya":
        return extract_dunya(html, url)
    if source_key == "rss":
        hint = (extra or {}).get("entry_title") or ""
        return extract_generic(html, url, fallback_title=hint)
    return None


class Command(BaseCommand):
    help = "Populate title/body_text/etc. from legacy raw_html documents."

    def handle(self, *args, **options):
        storage.ensure_indexes()
        col = storage.raw_collection()
        q = {"raw_html": {"$exists": True}, "body_text": {"$exists": False}}
        n = 0
        for doc in col.find(q):
            url = doc.get("canonical_url") or ""
            sk = doc.get("source_key") or ""
            html = doc.get("raw_html") or ""
            if not url or not html:
                continue
            extracted = _extract(sk, html, url, doc.get("extra") or {})
            if not extracted:
                self.stdout.write(self.style.WARNING(f"skip (no extract): {url}"))
                continue
            built = build_article_document(
                canonical_url=url,
                source_key=sk,
                extracted=extracted,
                http_status=int(doc.get("http_status") or 200),
                content_type=str(doc.get("content_type") or ""),
                extra={**(doc.get("extra") or {}), "upgraded_from_raw_html": True},
                raw_html=None,
            )
            to_set = dict(built)
            if doc.get("fetched_at") is not None:
                to_set["fetched_at"] = doc["fetched_at"]
            col.update_one(
                {"_id": doc["_id"]},
                {"$set": to_set, "$unset": {"raw_html": ""}},
            )
            n += 1
            self.stdout.write(self.style.SUCCESS(f"upgraded: {url}"))
        self.stdout.write(self.style.SUCCESS(f"done. upgraded {n} document(s)."))
