"""Structured fields from Dawn article HTML."""

from __future__ import annotations

from typing import Any, Optional

from bs4 import BeautifulSoup

from news.scrapers.extract.utils import (
    collect_links,
    normalize_ws,
    parse_published_datetime,
    soup_json_ld_news_article,
)


def extract_dawn(html: str, page_url: str) -> Optional[dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    ld = soup_json_ld_news_article(html)

    title = None
    og = soup.find("meta", property="og:title")
    if og and og.get("content"):
        title = normalize_ws(og["content"])
    h2 = soup.select_one("h2.story__title a.story__link")
    if not title and h2:
        title = normalize_ws(h2.get_text())

    summary = None
    ogd = soup.find("meta", property="og:description")
    if ogd and ogd.get("content"):
        summary = normalize_ws(ogd["content"])

    author_name = None
    ma = soup.find("meta", attrs={"name": "author"})
    if ma and ma.get("content"):
        author_name = normalize_ws(ma["content"])
    if not author_name and ld and ld.get("author"):
        auth = ld["author"]
        if isinstance(auth, dict):
            author_name = auth.get("name")

    author_url = None
    aa = soup.find("meta", property="article:author")
    if aa and aa.get("content"):
        author_url = aa["content"].strip()

    published_at = None
    ap = soup.find("meta", property="article:published_time")
    if ap and ap.get("content"):
        published_at = parse_published_datetime(ap["content"])
    if not published_at and ld:
        published_at = parse_published_datetime(ld.get("datePublished"))

    category = None
    sec = soup.find("meta", property="article:section")
    if sec and sec.get("content"):
        category = normalize_ws(sec["content"])

    image_url = None
    ogi = soup.find("meta", property="og:image")
    if ogi and ogi.get("content"):
        image_url = ogi["content"].strip()

    body_text = ""
    links: list[str] = []
    content = soup.select_one("div.story__content")
    if content:
        for tag in content.find_all(["script", "style", "iframe", "noscript"]):
            tag.decompose()
        links = collect_links(content, page_url)
        parts: list[str] = []
        for child in content.find_all(["p", "h2", "h3", "blockquote", "li"]):
            t = normalize_ws(child.get_text(" ", strip=True))
            if t:
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
