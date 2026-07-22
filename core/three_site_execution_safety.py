"""Closed execution classes for the two-part authoritative three-site Matrix."""

from __future__ import annotations


SHARED_HOST_SAFE = "shared-host-safe"
DEDICATED_HOST_DESTRUCTIVE = "dedicated-host-destructive"
EXECUTION_CLASSES = frozenset({SHARED_HOST_SAFE, DEDICATED_HOST_DESTRUCTIVE})


def execution_class_is_host_destructive(execution_class: object) -> bool:
    if execution_class not in EXECUTION_CLASSES:
        raise ValueError("unknown three-site execution class")
    return execution_class == DEDICATED_HOST_DESTRUCTIVE
