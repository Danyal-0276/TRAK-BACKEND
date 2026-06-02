"""Context for pipeline worker threads (avoids nested ThreadPoolExecutors)."""

from __future__ import annotations

import contextvars

pipeline_worker_active: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "pipeline_worker_active",
    default=False,
)
