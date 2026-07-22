"""Dependency-free canonical JSON serialization for control-plane artifacts."""

from __future__ import annotations

import json
from typing import Any


class CanonicalJSONError(ValueError):
    pass


def canonical_json_bytes(payload: Any) -> bytes:
    try:
        return json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise CanonicalJSONError("payload is not canonical-JSON compatible") from exc
