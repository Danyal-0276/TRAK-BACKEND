"""Extract article fields using per-site CSS selectors (see SCRAPER_GENERIC_SOURCES)."""

from __future__ import annotations

import re
from typing import Any, Optional
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup, Tag

from news.scrapers.extract.utils import (
    collect_links,
    normalize_ws,
    parse_published_datetime,
    soup_json_ld_news_article,
)
from news.scrapers.extract.generic import extract_generic

META_SEL = re.compile(r"^meta\s*\[\s*property\s*=\s*['\"]([^'\"]+)['\"]\s*\]$", re.I)


def _parse_selector(sel: str) -> tuple[str, Optional[str]]:
    """Return ('meta', property) or ('css', selector)."""
    s = (sel or "").strip()
    m = META_SEL.match(s)
    if m:
        return "meta", m.group(1)
    return "css", s


def _pick_meta(soup: BeautifulSoup, prop: str) -> Optional[str]:
    tag = soup.find("meta", property=prop) or soup.find("meta", attrs={"name": prop})
    if tag and tag.get("content"):
        return tag["content"].strip()
    return None


def _first_text(soup: BeautifulSoup, selector: Optional[str]) -> Optional[str]:
    if not selector:
        return None
    kind, val = _parse_selector(selector)
    if kind == "meta":
        return _pick_meta(soup, val) if val else None
    el = soup.select_one(val)
    if not el:
        return None
    return normalize_ws(el.get_text()) or None


def _extract_body_from_container(container: Tag, page_url: str) -> tuple[str, list[str]]:
    for tag in container.find_all(["script", "style", "iframe", "noscript"]):
        tag.decompose()
    links = collect_links(container, page_url)
    parts: list[str] = []
    for child in container.find_all(["p", "h2", "h3", "blockquote", "li"]):
        t = normalize_ws(child.get_text(" ", strip=True))
        if t:
            parts.append(t)
    body = "\n\n".join(parts)

    if not body:
        body = normalize_ws(container.get_text("\n", strip=True))
    return body, links


def extract_site_config(html: str, page_url: str, cfg: dict[str, Any]) -> Optional[dict[str, Any]]:
    """
    cfg keys (all optional except body or title+body via meta):
      selectors.title, selectors.body, selectors.summary,
      selectors.author, selectors.author_is_link (bool),
      selectors.published, selectors.published_attr (e.g. 'datetime'),
      selectors.image, selectors.category
      fallback_generic: default True — use extract_generic if body empty
    """
    selectors = cfg.get("selectors") or {}
    soup = BeautifulSoup(html, "html.parser")

    title = _first_text(soup, selectors.get("title"))
    summary = _first_text(soup, selectors.get("summary"))
    if not summary:
        summary = _pick_meta(soup, "og:description")

    image_url = _first_text(soup, selectors.get("image"))
    if not image_url:
        image_url = _pick_meta(soup, "og:image")

    category = _first_text(soup, selectors.get("category"))

    author_name = None
    author_url = None
    auth_sel = selectors.get("author")
    if auth_sel:
        kind, val = _parse_selector(auth_sel)
        if kind == "meta":
            author_name = _pick_meta(soup, val) if val else None
        else:
            el = soup.select_one(val)
            if el:
                author_name = normalize_ws(el.get_text())
                if selectors.get("author_is_link") and el.name == "a" and el.get("href"):
                    author_url = urljoin(page_url, el["href"].strip())

    published_at = None
    pub_sel = selectors.get("published")
    pub_attr = selectors.get("published_attr")
    if pub_sel:
        kind, val = _parse_selector(pub_sel)
        if kind == "meta" and val:
            raw = _pick_meta(soup, val)
            if raw:
                published_at = parse_published_datetime(raw)
        elif kind == "css" and val:
            el = soup.select_one(val)
            if el:
                if pub_attr and el.get(pub_attr):
                    published_at = parse_published_datetime(el[pub_attr])
                else:
                    published_at = parse_published_datetime(el.get_text())

    # Many outlets only expose date/author/category in JSON-LD, not in visible CSS.
    ld = soup_json_ld_news_article(html)
    if ld:
        if not published_at:
            for key in ("datePublished", "dateModified", "dateCreated"):
                raw = ld.get(key)
                if raw:
                    published_at = parse_published_datetime(str(raw))
                    if published_at:
                        break
        if not author_name:
            auth = ld.get("author")
            if isinstance(auth, dict):
                author_name = normalize_ws(str(auth.get("name") or "")) or None
                if auth.get("url"):
                    author_url = str(auth["url"]).strip()
            elif isinstance(auth, list) and auth:
                a0 = auth[0]
                if isinstance(a0, dict):
                    author_name = normalize_ws(str(a0.get("name") or "")) or None
                    if a0.get("url"):
                        author_url = str(a0["url"]).strip()
            elif isinstance(auth, str):
                author_name = normalize_ws(auth)
        if not category and ld.get("articleSection"):
            category = normalize_ws(str(ld["articleSection"]))

    body_text = ""
    links: list[str] = []
    body_sel = selectors.get("body")
    if body_sel:
        kind, val = _parse_selector(body_sel)
        if kind == "css" and val:
            container = soup.select_one(val)
            if container:
                body_text, links = _extract_body_from_container(container, page_url)

    if not title:
        title = _pick_meta(soup, "og:title")

    min_body = int(cfg.get("min_body_chars") or 120)
    body_too_short = bool(body_text) and len(body_text.strip()) < min_body
    if (
        (not body_text or not title or body_too_short)
        and cfg.get("fallback_generic", True)
    ):
        fb = extract_generic(html, page_url, fallback_title=title or "")
        if fb:
            if not title:
                title = fb.get("title")
            fb_body = (fb.get("body_text") or "").strip()
            if not body_text or len(fb_body) > len((body_text or "").strip()):
                body_text = fb.get("body_text") or ""
            if not summary:
                summary = fb.get("summary")
            if not published_at:
                published_at = fb.get("published_at")
            if not author_name:
                author_name = fb.get("author_name")
            if not author_url:
                author_url = fb.get("author_url")
            if not image_url:
                image_url = fb.get("image_url")
            if not category:
                category = fb.get("category")
            if not links:
                links = fb.get("links") or []

    if not title or not body_text:
        return None

    return {
        "title": title,
        "summary": summary,
        "body_text": body_text,
        "published_at": published_at,
        "author_name": author_name,
        "author_url": author_url,
        "category": category,
        "image_url": image_url,
        "links": links,
    }
