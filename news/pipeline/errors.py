"""Classify pipeline errors for retry vs permanent failure."""

from __future__ import annotations

# Substrings of errors that should requeue to pending, not mark failed.
_TRANSIENT_SUBSTRINGS = (
    "cannot schedule new futures after interpreter shutdown",
    "interpreter shut down",
    "broken process pool",
    "connection already closed",
    "server selection timeout",
    "network is unreachable",
    "temporary failure in name resolution",
)


def is_transient_pipeline_error(exc: BaseException | str) -> bool:
    msg = str(exc).lower()
    return any(s in msg for s in _TRANSIENT_SUBSTRINGS)
