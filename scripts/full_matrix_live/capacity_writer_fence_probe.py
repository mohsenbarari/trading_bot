#!/usr/bin/env python3
"""Prove the running WebApp API rejects a real unsafe write under capacity guard.

The request is deliberately stopped by middleware before authentication or a
database transaction.  Its JWT and idempotency key are ephemeral and are never
printed; the retained result contains only the controlled response identity.
"""

from __future__ import annotations

import argparse
from datetime import timedelta
import hashlib
import json
import sys

import httpx

from core.security import create_access_token


SCHEMA = "three-site-full-matrix-capacity-writer-fence-probe-v1"
API_BASE_URL = "http://webapp_fi_api:8000"
EXPECTED_REASON = "full_matrix_capacity_hard_limit"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-reason", choices=(EXPECTED_REASON,), required=True)
    args = parser.parse_args(argv)
    token = create_access_token(subject=1, expires_delta=timedelta(seconds=60))
    payload = {
        "offer_type": "sell",
        "settlement_type": "cash",
        "commodity_id": 1,
        "quantity": 1,
        "price": 1,
        "is_wholesale": True,
        "idempotency_key": "fm-capacity-fence-probe-1",
    }
    with httpx.Client(base_url=API_BASE_URL, timeout=15.0, trust_env=False) as client:
        response = client.post(
            "/api/offers/",
            headers={"Authorization": f"Bearer {token}"},
            json=payload,
        )
    try:
        body = response.json()
    except (ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError("capacity writer fence returned non-JSON") from exc
    if (
        response.status_code != 503
        or not isinstance(body, dict)
        or body.get("code") != "webapp_writer_fenced"
        or body.get("reasons") != [args.expected_reason]
        or response.headers.get("cache-control") != "no-store"
        or response.headers.get("x-webapp-writer-state") != "fenced"
    ):
        raise RuntimeError("capacity writer fence was not a controlled fail-closed response")
    print(
        json.dumps(
            {
                "schema": SCHEMA,
                "status": "passed",
                "http_status": int(response.status_code),
                "reason": args.expected_reason,
                "response_sha256": hashlib.sha256(response.content).hexdigest(),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, httpx.HTTPError):
        raise SystemExit(1)
