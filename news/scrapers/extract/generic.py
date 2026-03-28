"""Best-effort extraction for arbitrary article pages (e.g. RSS feed entries)."""

from __future__ import annotations

from typing import Any, Optional

from bs4 import BeautifulSoup

from news.scrapers.extract.utils import collect_links, normalize_ws, parse_published_datetime, soup_json_ld_news_article


def extract_generic(html: str, page_url: str, fallback_title: str = "") -> Optional[dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    ld = soup_json_ld_news_article(html)

    title = None
    og = soup.find("meta", property="og:title")
    if og and og.get("content"):
        title = normalize_ws(og["content"])
    if not title:
        t = soup.find("title")
        if t:
            title = normalize_ws(t.get_text())
    if not title and ld:
        title = normalize_ws(str(ld.get("headline", "")))
    if not title and fallback_title:
        title = normalize_ws(fallback_title)

    summary = None
    ogd = soup.find("meta", property="og:description")
    if ogd and ogd.get("content"):
        summary = normalize_ws(ogd["content"])

    published_at = None
    for prop in ("article:published_time", "og:updated_time", "article:modified_time"):
        m = soup.find("meta", property=prop)
        if m and m.get("content"):
            published_at = parse_published_datetime(m["content"])
            if published_at:
                break
    if not published_at and ld:
        published_at = parse_published_datetime(ld.get("datePublished"))

    author_name = None
    author_url = None
    ma = soup.find("meta", attrs={"name": "author"})
    if ma and ma.get("content"):
        author_name = normalize_ws(ma["content"])
    aa = soup.find("meta", property="article:author")
    if aa and aa.get("content"):
        author_url = aa["content"].strip()
    if not author_name and ld and ld.get("author"):
        auth = ld["author"]
        if isinstance(auth, dict):
            author_name = auth.get("name")

    image_url = None
    ogi = soup.find("meta", property="og:image")
    if ogi and ogi.get("content"):
        image_url = ogi["content"].strip()

    category = None
    sec = soup.find("meta", property="article:section")
    if sec and sec.get("content"):
        category = normalize_ws(sec["content"])

    body_text = ""
    links: list[str] = []
    main = (
        soup.find("main")
        or soup.find("article")
        or soup.find(attrs={"role": "main"})
        or soup.select_one(".post-content, .entry-content, .article-content, .content")
    )
    if main:
        for tag in main.find_all(["script", "style", "iframe", "noscript", "nav", "footer"]):
            tag.decompose()
        links = collect_links(main, page_url)
        parts: list[str] = []
        for child in main.find_all(["p", "h2", "h3", "blockquote", "li"]):
            t = normalize_ws(child.get_text(" ", strip=True))
            if len(t) > 40:
                parts.append(t)
        body_text = "\n\n".join(parts)

    if not body_text and ld and ld.get("articleBody"):
        body_text = normalize_ws(str(ld["articleBody"]))

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
