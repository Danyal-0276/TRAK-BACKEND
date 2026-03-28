"""Structured fields from Dunya News English article HTML."""

from __future__ import annotations

import re
from typing import Any, Optional
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from news.scrapers.extract.utils import collect_links, normalize_ws, parse_published_datetime


def extract_dunya(html: str, page_url: str) -> Optional[dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")

    title = None
    h1 = soup.select_one("h1.article__heading")
    if h1:
        title = normalize_ws(h1.get_text())
    og = soup.find("meta", property="og:title")
    if not title and og and og.get("content"):
        title = normalize_ws(og["content"])

    summary = None
    ogd = soup.find("meta", property="og:description")
    if ogd and og.get("content"):
        summary = normalize_ws(ogd["content"])

    tag = soup.find("meta", property="article:tag")
    category = normalize_ws(tag["content"]) if tag and tag.get("content") else None

    image_url = None
    ogi = soup.find("meta", property="og:image")
    if ogi and ogi.get("content"):
        image_url = ogi["content"].strip()

    published_at = None
    t_el = soup.select_one(".article__data__publish__date time")
    if t_el:
        if t_el.get("datetime"):
            published_at = parse_published_datetime(t_el["datetime"])
        if not published_at:
            txt = normalize_ws(t_el.get_text())
            # e.g. "27 Mar 26, 15:00:06 PKT" — best-effort
            m = re.search(
                r"(\d{1,2})\s+([A-Za-z]{3})\s+(\d{2,4}),\s+(\d{1,2}):(\d{2}):(\d{2})",
                txt,
            )
            if m:
                # keep None if parsing fails; store raw in caller if needed
                pass

    body_text = ""
    links: list[str] = []
    container = soup.select_one("#speechcontentDiv .news_html_desc") or soup.select_one(
        "#speechcontentDiv"
    )
    if container:
        for tag in container.find_all(["script", "style", "iframe", "noscript"]):
            tag.decompose()
        links = collect_links(container, page_url)
        parts: list[str] = []
        for child in container.find_all(["p", "h2", "h3", "blockquote", "li"]):
            t = normalize_ws(child.get_text(" ", strip=True))
            if t:
                parts.append(t)
        body_text = "\n\n".join(parts)

    author_name = None
    author_url = None
    # Some Dunya pages include a byline; pattern varies — extend when stable selector exists
    by = soup.select_one(".article__author a, .author__name a, a.article__author")
    if by and by.get("href"):
        author_url = urljoin(page_url, by["href"].strip())
        author_name = normalize_ws(by.get_text())

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
