"""
Multi-provider fact checking after fake-detection Space.

Default providers (all free, no API key):
  wikipedia, wikidata, openalex

Configure with FACT_CHECKER_PROVIDERS=wikipedia,wikidata,openalex
Optional: google (needs GOOGLE_FACT_CHECK_API_KEY)
"""

from __future__ import annotations

import json
import logging
import re
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from typing import Any, Callable, Optional

from django.conf import settings

logger = logging.getLogger(__name__)

_GOOGLE_SEARCH_URL = "https://factchecktools.googleapis.com/v1alpha1/claims:search"
_WIKIPEDIA_API = "https://en.wikipedia.org/w/api.php"
_WIKIDATA_API = "https://www.wikidata.org/w/api.php"
_OPENALEX_API = "https://api.openalex.org/works"

_RATING_TO_LABEL = {
    "false": 1,
    "mostlyfalse": 1,
    "firehoseoffalsehood": 1,
    "pantsfire": 1,
    "misleading": 1,
    "disputed": 2,
    "mixed": 2,
    "halftrue": 2,
    "mostlytrue": 0,
    "true": 0,
    "correct": 0,
}

VerifyFn = Callable[[str, Optional[int]], dict[str, Any]]


def _enabled() -> bool:
    raw = str(getattr(settings, "FACT_CHECKER_ENABLED", "false")).strip().lower()
    return raw in ("1", "true", "yes", "on")


def _google_api_key() -> str:
    return (getattr(settings, "GOOGLE_FACT_CHECK_API_KEY", None) or "").strip()


def _providers_list() -> list[str]:
    multi = (getattr(settings, "FACT_CHECKER_PROVIDERS", None) or "").strip()
    if multi:
        return [p.strip().lower() for p in multi.split(",") if p.strip()]
    single = (getattr(settings, "FACT_CHECKER_PROVIDER", "wikipedia") or "wikipedia").strip().lower()
    return [single] if single else ["wikipedia"]


def preload_fact_checker() -> dict[str, Any]:
    if not _enabled():
        return {"mode": "disabled", "loaded": False, "reason": "FACT_CHECKER_ENABLED=false"}

    providers = _providers_list()
    loaded: list[dict[str, Any]] = []
    for name in providers:
        if name == "google" and not _google_api_key():
            loaded.append({"provider": name, "loaded": False, "reason": "GOOGLE_FACT_CHECK_API_KEY unset"})
            continue
        if name in _PROVIDER_REGISTRY:
            loaded.append({"provider": name, "loaded": True, "free": name != "google"})
        else:
            loaded.append({"provider": name, "loaded": False, "reason": "unknown provider"})

    any_loaded = any(item.get("loaded") for item in loaded)
    return {
        "mode": "multi" if len(providers) > 1 else (providers[0] if providers else "none"),
        "loaded": any_loaded,
        "providers": loaded,
    }


def _empty_result(provider: str) -> dict[str, Any]:
    return {
        "fact_check_enabled": _enabled(),
        "fact_check_provider": provider,
        "fact_check_verdict": "skipped",
        "fact_check_hits": 0,
        "fact_check_query": "",
        "fact_check_textual_ratings": [],
        "fact_check_publishers": [],
        "fact_check_urls": [],
        "fact_check_claims": [],
        "fact_check_suggested_label": None,
        "fact_check_trust_score": None,
    }


def _build_query(title: str, text: str) -> str:
    title = (title or "").strip()
    body = re.sub(r"\s+", " ", (text or "").strip())
    snippet = body[:280]
    if title and snippet:
        return f"{title}. {snippet}"
    return title or snippet


def _search_query(title: str, text: str) -> str:
    """Shorter query for entity / academic lookups."""
    title = (title or "").strip()
    if title:
        return title[:120]
    body = re.sub(r"\s+", " ", (text or "").strip())
    return body[:120]


def _finalize_verdict(base: dict[str, Any], ml_label: Optional[int]) -> dict[str, Any]:
    suggested = base.get("fact_check_suggested_label")
    verdict = base.get("fact_check_verdict")

    if verdict in ("no_hits", "api_error", "empty_query", "no_api_key", "disabled", "skipped"):
        return base

    if suggested is None:
        base["fact_check_verdict"] = verdict or "inconclusive"
        return base

    if ml_label is None:
        base["fact_check_verdict"] = "standalone"
    elif suggested == 1 and ml_label in (0, 2):
        base["fact_check_verdict"] = "contradicts_ml"
    elif suggested == 0 and ml_label == 1:
        base["fact_check_verdict"] = "contradicts_ml"
    elif suggested == ml_label:
        base["fact_check_verdict"] = "supports_ml"
    else:
        base["fact_check_verdict"] = "mixed"
    return base


def _normalize_rating(text: str) -> str:
    return re.sub(r"[^a-z]", "", (text or "").lower())


def _rating_to_label(rating: str) -> Optional[int]:
    return _RATING_TO_LABEL.get(_normalize_rating(rating))


def _http_json_get(url: str) -> Any:
    req = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "User-Agent": "TRAK-Backend/1.0 (fact-check)"},
    )
    timeout = float(getattr(settings, "FACT_CHECKER_TIMEOUT", 20))
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _knowledge_hit_label(*, hits: int, ml_label: Optional[int]) -> tuple[Optional[int], str]:
    if hits <= 0:
        if ml_label == 1:
            return 1, "supports_ml"
        return None, "no_hits"
    if ml_label == 1:
        return 2, "mixed"
    return 0, "supports_ml"


def _verify_wikipedia(query: str, ml_label: Optional[int]) -> dict[str, Any]:
    base = _empty_result("wikipedia")
    base["fact_check_query"] = query
    params = urllib.parse.urlencode(
        {
            "action": "opensearch",
            "search": query[:240],
            "limit": 5,
            "namespace": 0,
            "format": "json",
        }
    )
    try:
        payload = _http_json_get(f"{_WIKIPEDIA_API}?{params}")
    except Exception as exc:
        logger.exception("Wikipedia fact-check failed: %s", exc)
        base["fact_check_verdict"] = "api_error"
        base["fact_check_error"] = str(exc)[:200]
        return base

    titles = payload[1] if isinstance(payload, list) and len(payload) > 1 else []
    descriptions = payload[2] if isinstance(payload, list) and len(payload) > 2 else []
    urls = payload[3] if isinstance(payload, list) and len(payload) > 3 else []

    base["fact_check_hits"] = len(titles)
    base["fact_check_claims"] = [str(t) for t in titles[:5]]
    base["fact_check_textual_ratings"] = [str(d) for d in descriptions[:5]]
    base["fact_check_urls"] = [str(u) for u in urls[:5]]

    suggested, verdict = _knowledge_hit_label(hits=len(titles), ml_label=ml_label)
    base["fact_check_suggested_label"] = suggested
    base["fact_check_verdict"] = verdict
    return _finalize_verdict(base, ml_label)


def _verify_wikidata(query: str, ml_label: Optional[int]) -> dict[str, Any]:
    base = _empty_result("wikidata")
    base["fact_check_query"] = query
    params = urllib.parse.urlencode(
        {
            "action": "wbsearchentities",
            "search": query[:120],
            "language": "en",
            "limit": 5,
            "format": "json",
        }
    )
    try:
        payload = _http_json_get(f"{_WIKIDATA_API}?{params}")
    except Exception as exc:
        logger.exception("Wikidata fact-check failed: %s", exc)
        base["fact_check_verdict"] = "api_error"
        base["fact_check_error"] = str(exc)[:200]
        return base

    entities = payload.get("search") or []
    labels: list[str] = []
    descriptions: list[str] = []
    urls: list[str] = []
    for ent in entities:
        if not isinstance(ent, dict):
            continue
        label = str(ent.get("label") or "").strip()
        desc = str(ent.get("description") or "").strip()
        eid = str(ent.get("id") or "").strip()
        if label:
            labels.append(label)
        if desc:
            descriptions.append(desc)
        if eid:
            urls.append(f"https://www.wikidata.org/wiki/{eid}")

    base["fact_check_hits"] = len(entities)
    base["fact_check_claims"] = labels[:5]
    base["fact_check_textual_ratings"] = descriptions[:5]
    base["fact_check_urls"] = urls[:5]

    suggested, verdict = _knowledge_hit_label(hits=len(entities), ml_label=ml_label)
    base["fact_check_suggested_label"] = suggested
    base["fact_check_verdict"] = verdict
    return _finalize_verdict(base, ml_label)


def _verify_openalex(query: str, ml_label: Optional[int]) -> dict[str, Any]:
    """Academic literature cross-check (science/health claims)."""
    base = _empty_result("openalex")
    base["fact_check_query"] = query
    params = urllib.parse.urlencode(
        {
            "search": query[:200],
            "per_page": 5,
            "mailto": getattr(settings, "OPENALEX_MAILTO", "trak@example.com"),
        }
    )
    try:
        payload = _http_json_get(f"{_OPENALEX_API}?{params}")
    except Exception as exc:
        logger.exception("OpenAlex fact-check failed: %s", exc)
        base["fact_check_verdict"] = "api_error"
        base["fact_check_error"] = str(exc)[:200]
        return base

    results = payload.get("results") or []
    titles: list[str] = []
    urls: list[str] = []
    years: list[str] = []
    for work in results:
        if not isinstance(work, dict):
            continue
        title = str(work.get("display_name") or "").strip()
        if title:
            titles.append(title)
        wid = work.get("id")
        if wid:
            urls.append(str(wid))
        pub = work.get("publication_year")
        if pub:
            years.append(str(pub))

    base["fact_check_hits"] = len(results)
    base["fact_check_claims"] = titles[:5]
    base["fact_check_textual_ratings"] = [f"year={y}" for y in years[:5]]
    base["fact_check_urls"] = urls[:5]

    suggested, verdict = _knowledge_hit_label(hits=len(results), ml_label=ml_label)
    base["fact_check_suggested_label"] = suggested
    base["fact_check_verdict"] = verdict
    return _finalize_verdict(base, ml_label)


def _verify_google(query: str, ml_label: Optional[int]) -> dict[str, Any]:
    base = _empty_result("google")
    base["fact_check_query"] = query

    if not _google_api_key():
        base["fact_check_verdict"] = "no_api_key"
        return base

    params = {
        "query": query[:500],
        "languageCode": getattr(settings, "FACT_CHECKER_LANGUAGE", "en-US"),
        "maxAgeDays": int(getattr(settings, "FACT_CHECKER_MAX_AGE_DAYS", 30)),
        "pageSize": int(getattr(settings, "FACT_CHECKER_PAGE_SIZE", 5)),
        "key": _google_api_key(),
    }
    url = f"{_GOOGLE_SEARCH_URL}?{urllib.parse.urlencode(params)}"
    try:
        payload = _http_json_get(url)
    except urllib.error.HTTPError as exc:
        logger.warning("Google Fact Check API HTTP error: %s", exc)
        base["fact_check_verdict"] = "api_error"
        base["fact_check_error"] = str(exc)[:200]
        return base
    except Exception as exc:
        logger.exception("Google Fact Check API failed: %s", exc)
        base["fact_check_verdict"] = "api_error"
        base["fact_check_error"] = str(exc)[:200]
        return base

    claims = payload.get("claims") or []
    base["fact_check_hits"] = len(claims)

    ratings: list[str] = []
    publishers: list[str] = []
    urls: list[str] = []
    claim_snippets: list[str] = []
    label_votes: list[int] = []

    for item in claims:
        review = (item or {}).get("claimReview") or {}
        rating = str(review.get("textualRating") or "").strip()
        if rating:
            ratings.append(rating)
            mapped = _rating_to_label(rating)
            if mapped is not None:
                label_votes.append(mapped)
        publisher = ((review.get("publisher") or {}).get("name") or "").strip()
        if publisher:
            publishers.append(publisher)
        link = str(review.get("url") or "").strip()
        if link:
            urls.append(link)
        claim_text = str((item.get("claim") or {}).get("text") or "").strip()
        if claim_text:
            claim_snippets.append(claim_text[:300])

    base["fact_check_textual_ratings"] = ratings[:10]
    base["fact_check_publishers"] = publishers[:10]
    base["fact_check_urls"] = urls[:10]
    base["fact_check_claims"] = claim_snippets[:5]

    if not claims:
        base["fact_check_verdict"] = "no_hits"
        return base

    if label_votes:
        base["fact_check_suggested_label"] = max(label_votes)
    return _finalize_verdict(base, ml_label)


_PROVIDER_REGISTRY: dict[str, VerifyFn] = {
    "wikipedia": _verify_wikipedia,
    "wiki": _verify_wikipedia,
    "wikidata": _verify_wikidata,
    "openalex": _verify_openalex,
    "google": _verify_google,
}


def _aggregate_results(results: list[dict[str, Any]], ml_label: Optional[int]) -> dict[str, Any]:
    if not results:
        out = _empty_result("none")
        out["fact_check_verdict"] = "disabled"
        return out

    labels = [r.get("fact_check_suggested_label") for r in results if r.get("fact_check_suggested_label") is not None]
    verdicts = [str(r.get("fact_check_verdict") or "") for r in results]
    total_hits = sum(int(r.get("fact_check_hits") or 0) for r in results)

    contradicts = sum(1 for v in verdicts if v == "contradicts_ml")
    supports = sum(1 for v in verdicts if v == "supports_ml")
    mixed = sum(1 for v in verdicts if v == "mixed")
    errors = sum(1 for v in verdicts if v == "api_error")

    if labels:
        suggested = Counter(labels).most_common(1)[0][0]
    else:
        suggested = None

    if contradicts > supports:
        agg_verdict = "contradicts_ml"
    elif supports > contradicts:
        agg_verdict = "supports_ml"
    elif mixed > 0:
        agg_verdict = "mixed"
    elif errors == len(results):
        agg_verdict = "api_error"
    else:
        agg_verdict = "inconclusive"

    all_urls: list[str] = []
    all_claims: list[str] = []
    all_ratings: list[str] = []
    for r in results:
        all_urls.extend(r.get("fact_check_urls") or [])
        all_claims.extend(r.get("fact_check_claims") or [])
        all_ratings.extend(r.get("fact_check_textual_ratings") or [])

    provider_names = [str(r.get("fact_check_provider") or "") for r in results]

    merged = {
        "fact_check_enabled": True,
        "fact_check_provider": "+".join(provider_names),
        "fact_check_providers_used": provider_names,
        "fact_check_results": [
            {
                "provider": r.get("fact_check_provider"),
                "verdict": r.get("fact_check_verdict"),
                "hits": r.get("fact_check_hits"),
                "suggested_label": r.get("fact_check_suggested_label"),
                "urls": (r.get("fact_check_urls") or [])[:3],
            }
            for r in results
        ],
        "fact_check_verdict": agg_verdict,
        "fact_check_hits": total_hits,
        "fact_check_query": results[0].get("fact_check_query") or "",
        "fact_check_suggested_label": suggested,
        "fact_check_urls": list(dict.fromkeys(all_urls))[:15],
        "fact_check_claims": list(dict.fromkeys(all_claims))[:10],
        "fact_check_textual_ratings": list(dict.fromkeys(all_ratings))[:10],
        "fact_check_publishers": [],
        "fact_check_trust_score": None,
        "fact_check_support_count": supports,
        "fact_check_contradict_count": contradicts,
    }
    return _finalize_verdict(merged, ml_label)


def verify_claim(
    text: str,
    *,
    title: str = "",
    ml_label: Optional[int] = None,
) -> dict[str, Any]:
    """Run all configured fact-check providers and merge results."""
    if not _enabled():
        result = _empty_result("none")
        result["fact_check_verdict"] = "disabled"
        result["fact_check_enabled"] = False
        return result

    full_query = _build_query(title, text)
    short_query = _search_query(title, text)
    if not full_query:
        result = _empty_result("none")
        result["fact_check_verdict"] = "empty_query"
        return result

    tasks: list[tuple[str, str, VerifyFn]] = []
    for name in _providers_list():
        fn = _PROVIDER_REGISTRY.get(name)
        if not fn:
            logger.warning("Unknown fact-check provider: %s", name)
            continue
        query = short_query if name in ("wikidata", "openalex") else full_query
        tasks.append((name, query, fn))

    results: list[dict[str, Any]] = []
    try:
        from news.pipeline.worker_context import pipeline_worker_active

        in_pipeline_worker = pipeline_worker_active.get()
    except Exception:
        in_pipeline_worker = False
    parallel = (
        getattr(settings, "FACT_CHECKER_PARALLEL", True)
        and len(tasks) > 1
        and not in_pipeline_worker
    )
    if parallel:
        from concurrent.futures import ThreadPoolExecutor, as_completed

        with ThreadPoolExecutor(max_workers=min(4, len(tasks))) as pool:
            futures = {
                pool.submit(fn, query, ml_label): name for name, query, fn in tasks
            }
            for fut in as_completed(futures):
                name = futures[fut]
                try:
                    results.append(fut.result())
                except Exception as exc:
                    logger.exception("Fact-check provider %s failed: %s", name, exc)
                    err = _empty_result(name)
                    err["fact_check_verdict"] = "api_error"
                    results.append(err)
    else:
        for name, query, fn in tasks:
            results.append(fn(query, ml_label))

    return _aggregate_results(results, ml_label)
