"""Background worker: scrape + pipeline on a fixed interval (default every 24 hours)."""

from __future__ import annotations

import logging
import os
import sys
import threading
from datetime import datetime, timedelta, timezone
from typing import Optional

from django.conf import settings
from django.core.management import call_command
from django.db import close_old_connections
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from news.management.commands.scrape_raw_news import get_last_scrape_insert_count
from news.mongo_db import get_db

logger = logging.getLogger("news.schedule.scrape")

# Hard policy: one scheduled scrape every 24 hours, at most SCRAPE_SCHEDULE_TOTAL_LIMIT new articles per run.
DAILY_SCRAPE_INTERVAL_HOURS = 24
DAILY_SCRAPE_ARTICLE_LIMIT = 150

_LOCK_ID = "scheduled_scrape"
_thread_started = False
_thread_guard = threading.Lock()
_local_guard = threading.Lock()
_wake = threading.Event()
_CHECK_INTERVAL_SECONDS = 3600

_DEFAULT_SOURCES = [
    "currents",
    "newsdata",
    "gnews",
    "rss",
    "generic_sites",
    "dunya",
    "dawn",
]


def _locks_collection():
    return get_db()["pipeline_locks"]


def _lock_ttl_seconds() -> int:
    return max(3600, int(getattr(settings, "SCRAPE_SCHEDULE_LOCK_TTL_SECONDS", 10800)))


def _interval_hours() -> int:
    return DAILY_SCRAPE_INTERVAL_HOURS


def _article_limit() -> int:
    configured = int(getattr(settings, "SCRAPE_SCHEDULE_TOTAL_LIMIT", DAILY_SCRAPE_ARTICLE_LIMIT))
    return max(1, min(500, configured))


def _schedule_sources() -> list[str]:
    raw = os.environ.get("SCRAPE_SCHEDULE_SOURCES", "").strip()
    if raw:
        return [s.strip() for s in raw.split(",") if s.strip()]
    return list(_DEFAULT_SOURCES)


def _holder_tag() -> str:
    return f"{os.getpid()}:{threading.current_thread().name}"


def _holder_pid_alive(holder: str) -> bool:
    try:
        pid = int(str(holder or "").split(":", 1)[0])
    except (TypeError, ValueError):
        return True
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x1000, False, pid)
        if handle:
            kernel32.CloseHandle(handle)
            return True
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _last_run_at() -> Optional[datetime]:
    doc = _locks_collection().find_one({"_id": _LOCK_ID})
    if not doc:
        return None
    last = doc.get("last_run_at")
    if not last:
        return None
    if getattr(last, "tzinfo", None) is None:
        last = last.replace(tzinfo=timezone.utc)
    return last


def _interval_elapsed() -> bool:
    last = _last_run_at()
    if last is None:
        # Never auto-scrape on first API boot / missing last_run_at — use cron or admin instead.
        return False
    return datetime.now(timezone.utc) - last >= timedelta(hours=_interval_hours())


def get_scrape_schedule_status() -> dict:
    """Snapshot for CLI / ops: last run, insert totals, and whether a new run is allowed."""
    doc = _locks_collection().find_one({"_id": _LOCK_ID}) or {}
    last = _last_run_at()
    interval = _interval_hours()
    next_allowed = None
    if last is not None:
        next_allowed = (last + timedelta(hours=interval)).isoformat()
    return {
        "enabled": bool(getattr(settings, "SCRAPE_SCHEDULE_ENABLED", False)),
        "interval_hours": interval,
        "article_limit": _article_limit(),
        "can_run_now": _interval_elapsed(),
        "last_run_at": last.isoformat() if last else None,
        "last_scrape_inserted": int(doc.get("last_scrape_inserted") or 0),
        "next_allowed_at": next_allowed,
    }


def clear_stale_scrape_lock() -> bool:
    col = _locks_collection()
    doc = col.find_one({"_id": _LOCK_ID})
    if not doc:
        return False
    now = datetime.now(timezone.utc)
    locked_until = doc.get("locked_until")
    if locked_until and getattr(locked_until, "tzinfo", None) is None:
        locked_until = locked_until.replace(tzinfo=timezone.utc)
    expired = bool(locked_until and locked_until <= now)
    holder_dead = not _holder_pid_alive(str(doc.get("holder") or ""))
    if expired or holder_dead:
        col.update_one(
            {"_id": _LOCK_ID},
            {"$unset": {"locked_until": "", "locked_at": "", "holder": ""}},
        )
        return True
    return False


def try_acquire_scrape_lock() -> bool:
    clear_stale_scrape_lock()
    col = _locks_collection()
    now = datetime.now(timezone.utc)
    holder = _holder_tag()
    ttl = _lock_ttl_seconds()
    lock_update = {
        "locked_until": now + timedelta(seconds=ttl),
        "locked_at": now,
        "holder": holder,
    }
    doc = col.find_one_and_update(
        {
            "_id": _LOCK_ID,
            "$or": [
                {"locked_until": {"$lte": now}},
                {"locked_until": {"$exists": False}},
            ],
        },
        {"$set": lock_update},
        return_document=ReturnDocument.AFTER,
    )
    if doc and doc.get("holder") == holder:
        return True
    try:
        col.insert_one({"_id": _LOCK_ID, **lock_update})
        return True
    except DuplicateKeyError:
        return False


def release_scrape_lock() -> None:
    col = _locks_collection()
    now = datetime.now(timezone.utc)
    col.update_one(
        {"_id": _LOCK_ID, "holder": _holder_tag()},
        {"$set": {"locked_until": now}},
    )


def _record_last_run(*, inserted: int = 0) -> None:
    """Mark the daily slot used (complete or incomplete scrape/pipeline)."""
    now = datetime.now(timezone.utc)
    _locks_collection().update_one(
        {"_id": _LOCK_ID},
        {
            "$set": {
                "last_run_at": now,
                "last_scrape_inserted": max(0, int(inserted)),
                "last_scrape_limit": _article_limit(),
            }
        },
        upsert=True,
    )


def run_scheduled_scrape_cycle() -> int:
    """
    Scrape up to SCRAPE_SCHEDULE_TOTAL_LIMIT new articles, then pipeline up to the same cap.
    Returns the number of new raw articles inserted (<= configured limit).
    """
    total = _article_limit()
    workers = max(1, min(8, int(getattr(settings, "PIPELINE_WORKERS", 1))))
    sources = _schedule_sources()

    call_command(
        "run_news_cycle",
        sources=sources,
        scrape_limit=total,
        total_limit=total,
        skip_pipeline=True,
    )
    inserted = get_last_scrape_insert_count()
    logger.info("scheduled scrape phase done: inserted=%s/%s", inserted, total)

    try:
        call_command(
            "run_news_cycle",
            skip_scrape=True,
            pipeline_all=False,
            pipeline_limit=total,
            workers=workers,
            requeue_stale=True,
        )
    except Exception:
        logger.exception(
            "scheduled pipeline phase failed (scrape inserted=%s/%s)",
            inserted,
            total,
        )

    return inserted


def maybe_run_scheduled_scrape(*, reason: str = "interval", force: bool = False) -> bool:
    """
    Run scrape + pipeline when enabled and the interval has elapsed.
    Returns True if a cycle ran, False if skipped.
    """
    if not getattr(settings, "SCRAPE_SCHEDULE_ENABLED", False):
        return False
    if not force and not _interval_elapsed():
        last = _last_run_at()
        logger.info(
            "scheduled scrape skipped (%s): once-per-%sh policy (last_run=%s)",
            reason,
            _interval_hours(),
            last.isoformat() if last else "never",
        )
        return False

    close_old_connections()
    if not _local_guard.acquire(blocking=False):
        logger.debug("scheduled scrape already running in this process (%s)", reason)
        return False

    try:
        if not try_acquire_scrape_lock():
            logger.debug("scheduled scrape lock held elsewhere (%s)", reason)
            return False

        inserted = 0
        try:
            total = _article_limit()
            logger.info(
                "scheduled scrape starting (%s): total_limit=%s (hard cap) interval=%sh",
                reason,
                total,
                _interval_hours(),
            )
            try:
                inserted = run_scheduled_scrape_cycle()
                logger.info(
                    "scheduled scrape cycle done (%s): inserted=%s/%s",
                    reason,
                    inserted,
                    total,
                )
            except Exception:
                inserted = get_last_scrape_insert_count()
                logger.exception(
                    "scheduled scrape cycle failed (%s): inserted=%s/%s",
                    reason,
                    inserted,
                    total,
                )
            finally:
                # Daily slot is consumed even if scrape or pipeline did not fully finish.
                _record_last_run(inserted=inserted)
            return True
        finally:
            release_scrape_lock()
    finally:
        _local_guard.release()


def _scrape_loop() -> None:
    logger.info(
        "scheduled scrape loop started (interval=%sh, check_every=%ss)",
        _interval_hours(),
        _CHECK_INTERVAL_SECONDS,
    )
    while True:
        try:
            maybe_run_scheduled_scrape(reason="interval")
        except Exception:
            logger.exception("scheduled scrape interval run failed")
        finally:
            close_old_connections()
        _wake.wait(timeout=_CHECK_INTERVAL_SECONDS)
        _wake.clear()


def should_start_scrape_scheduler() -> bool:
    if not getattr(settings, "SCRAPE_SCHEDULE_ENABLED", False):
        return False
    argv = sys.argv
    blocked = (
        "migrate",
        "collectstatic",
        "test",
        "shell",
        "run_ai_pipeline",
        "run_news_cycle",
        "run_scheduled_scrape",
        "scrape_raw_news",
        "run_pipeline_daemon",
        "makemigrations",
        "createsuperuser",
        "sendtestemail",
    )
    if any(cmd in argv for cmd in blocked):
        return False
    if "runserver" in argv:
        return os.environ.get("RUN_MAIN") == "true"
    return True


def start_scrape_scheduler() -> None:
    global _thread_started
    with _thread_guard:
        if _thread_started or not should_start_scrape_scheduler():
            return
        thread = threading.Thread(
            target=_scrape_loop,
            name="trak-scrape-schedule",
            daemon=True,
        )
        thread.start()
        _thread_started = True
        logger.info("scheduled scrape background worker started")
