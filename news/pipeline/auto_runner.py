"""Background worker: drain raw_articles pending queue when the API / scrapers are running."""

from __future__ import annotations

import logging
import os
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from django.conf import settings
from django.db import close_old_connections
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from news.mongo_db import get_db, raw_collection
from news.pipeline import orchestrator
from news.services.feed_cache import invalidate_explore_cache

logger = logging.getLogger("news.pipeline.auto")

_LOCK_ID = "auto_drain"
_thread_started = False
_thread_guard = threading.Lock()
_local_guard = threading.Lock()
_wake = threading.Event()


def _locks_collection():
    return get_db()["pipeline_locks"]


def _lock_ttl_seconds() -> int:
    return max(300, int(getattr(settings, "PIPELINE_AUTO_LOCK_TTL_SECONDS", 7200)))


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


def clear_stale_auto_lock() -> bool:
    """Drop lock when expired or the holder process no longer exists."""
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
        col.delete_one({"_id": _LOCK_ID})
        return True
    return False


def try_acquire_auto_lock() -> bool:
    """Mongo-backed lock so only one drain runs across threads/processes."""
    clear_stale_auto_lock()
    col = _locks_collection()
    now = datetime.now(timezone.utc)
    holder = _holder_tag()
    ttl = min(_lock_ttl_seconds(), 900)
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


def release_auto_lock() -> None:
    col = _locks_collection()
    now = datetime.now(timezone.utc)
    col.update_one(
        {"_id": _LOCK_ID, "holder": _holder_tag()},
        {"$set": {"locked_until": now}},
    )


def pending_count() -> int:
    return int(raw_collection().count_documents({"pipeline_status": "pending"}))


def pipeline_backlog_count() -> int:
    """Pending plus in-flight processing rows (dashboard 'in queue')."""
    col = raw_collection()
    pending = int(col.count_documents({"pipeline_status": "pending"}))
    processing = int(col.count_documents({"pipeline_status": "processing"}))
    return pending + processing


def drain_pending_queue_if_needed(*, reason: str = "auto") -> Optional[dict[str, Any]]:
    """
    If pending raw articles exist, run the pipeline until the queue is empty.
    Returns the orchestrator result dict, or None if skipped.
    """
    close_old_connections()
    min_pending = max(1, int(getattr(settings, "PIPELINE_AUTO_MIN_PENDING", 1)))
    pending = pending_count()
    backlog = pipeline_backlog_count()
    if backlog < min_pending:
        return None

    if not _local_guard.acquire(blocking=False):
        logger.debug("pipeline drain already running in this process (%s)", reason)
        return None

    try:
        if not try_acquire_auto_lock():
            logger.debug("pipeline drain lock held elsewhere (%s)", reason)
            return None

        try:
            logger.info(
                "auto pipeline starting (%s): %s pending, %s backlog",
                reason,
                pending,
                backlog,
            )
            try:
                orchestrator.heal_stuck_raw_pipeline(
                    stale_minutes=getattr(settings, "PIPELINE_STALE_MINUTES", 30)
                )
            except Exception:
                logger.exception("heal_stuck_raw_pipeline failed")

            workers = max(1, min(8, int(getattr(settings, "PIPELINE_WORKERS", 1))))
            batch_size = max(
                1,
                min(500, int(getattr(settings, "PIPELINE_AUTO_BATCH_SIZE", 50))),
            )
            result = orchestrator.run_until_empty(batch_size=batch_size, workers=workers)
            try:
                invalidate_explore_cache()
            except Exception:
                logger.exception("invalidate_explore_cache failed")

            left = int(result.get("pending_remaining") or 0)
            logger.info(
                "auto pipeline finished (%s): processed_ok=%s errors=%s pending_remaining=%s",
                reason,
                result.get("processed_ok"),
                result.get("errors"),
                left,
            )
            return result
        finally:
            release_auto_lock()
    finally:
        _local_guard.release()


def _auto_loop() -> None:
    interval = max(30, int(getattr(settings, "PIPELINE_AUTO_INTERVAL_SECONDS", 90)))
    logger.info("auto pipeline loop started (interval=%ss)", interval)
    while True:
        try:
            drain_pending_queue_if_needed(reason="interval")
        except Exception:
            logger.exception("auto pipeline interval drain failed")
        finally:
            close_old_connections()
        _wake.wait(timeout=interval)
        _wake.clear()


def schedule_immediate_drain() -> None:
    """Wake the background loop right after new raw articles are ingested."""
    if not getattr(settings, "PIPELINE_AUTO_ENABLED", True):
        return
    _wake.set()
    threading.Thread(
        target=lambda: drain_pending_queue_if_needed(reason="scrape"),
        name="trak-pipeline-scrape-kick",
        daemon=True,
    ).start()


def should_start_auto_pipeline() -> bool:
    if not getattr(settings, "PIPELINE_AUTO_ENABLED", True):
        return False
    argv = sys.argv
    blocked = (
        "migrate",
        "collectstatic",
        "test",
        "shell",
        "run_ai_pipeline",
        "run_news_cycle",
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


def start_auto_pipeline_worker() -> None:
    global _thread_started
    with _thread_guard:
        if _thread_started or not should_start_auto_pipeline():
            return
        thread = threading.Thread(
            target=_auto_loop,
            name="trak-pipeline-auto",
            daemon=True,
        )
        thread.start()
        _thread_started = True
        logger.info("auto pipeline background worker started")
