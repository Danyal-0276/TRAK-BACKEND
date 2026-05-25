"""System and pipeline error alerts for admin users."""

from __future__ import annotations

from notifications.delivery import notify_all_admins


def notify_admin_pipeline_error(*, error: str, canonical_url: str = "", context: str = "") -> int:
    text = "News pipeline error"
    if canonical_url:
        text = f"Pipeline failed for article: {canonical_url[:120]}"
    details = (error or "Unknown error")[:500]
    if context:
        details = f"{context}\n{details}"
    return notify_all_admins(
        ntype="admin_pipeline_error",
        text=text,
        details=details,
        important=True,
        meta={"canonical_url": canonical_url, "context": context},
        dedupe_key=f"pipeline_err:{canonical_url or context}"[:200],
    )


def notify_admin_pipeline_batch(*, processed_ok: int, errors: int) -> int:
    if errors <= 0:
        return 0
    return notify_all_admins(
        ntype="admin_system",
        text=f"Pipeline batch finished with {errors} error(s) ({processed_ok} ok).",
        details="Open Admin → Articles or logs for details.",
        important=errors > 0,
        dedupe_key=f"pipeline_batch:{processed_ok}:{errors}",
    )
