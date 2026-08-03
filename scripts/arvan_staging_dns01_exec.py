#!/usr/bin/env python3
"""Strict external DNS-01 hook for ``staging.gold-trade.ir``.

This is used only by lego's ``exec`` provider.  The bundled Arvan provider in
the host's lego binary still targets the retired ``napi.arvancloud.com`` API.
This hook uses the reviewed ``.ir`` CDN API through the existing hardened
client, and refuses every record except the one ACME TXT name needed for the
owner-authorized staging hostname.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.arvan_origin_switch import DEFAULT_API_BASE, api_request, load_token


STAGING_ZONE = "gold-trade.ir"
STAGING_FQDN = "_acme-challenge.staging.gold-trade.ir."
DEFAULT_TOKEN_FILE = Path("/root/secure-envs/trading-bot/arvan-cdn-token")
TTL_SECONDS = 600
VALUE_RE = re.compile(r"^[A-Za-z0-9_-]{32,128}$")
RequestFn = Callable[[str, str, str, dict[str, Any] | None], dict[str, Any]]


class ArvanDNS01Error(RuntimeError):
    """Raised when the exact staging ACME TXT record cannot be managed safely."""


def _token_file() -> Path:
    configured = Path(os.getenv("ARVAN_STAGING_DNS01_TOKEN_FILE", str(DEFAULT_TOKEN_FILE)))
    if configured != DEFAULT_TOKEN_FILE:
        raise ArvanDNS01Error("DNS-01 token path differs from the reviewed owner-only token")
    return configured


def _record_name(fqdn: str) -> str:
    if fqdn != STAGING_FQDN:
        raise ArvanDNS01Error("DNS-01 hook is restricted to the exact staging ACME FQDN")
    return fqdn.removesuffix(STAGING_ZONE + ".").rstrip(".")


def _validate_value(value: str) -> str:
    if not VALUE_RE.fullmatch(value):
        raise ArvanDNS01Error("DNS-01 TXT value has an unexpected format")
    return value


def _records(response: dict[str, Any]) -> list[dict[str, Any]]:
    records = response.get("data")
    if not isinstance(records, list) or not all(isinstance(item, dict) for item in records):
        raise ArvanDNS01Error("Arvan DNS record list has an unexpected response shape")
    return records


def _matching_records(records: list[dict[str, Any]], *, name: str, value: str) -> list[dict[str, Any]]:
    matches = []
    for record in records:
        record_value = record.get("value")
        if (
            record.get("type") == "txt"
            and record.get("name") == name
            and isinstance(record_value, dict)
            and record_value.get("text") == value
        ):
            record_id = record.get("id")
            if not isinstance(record_id, str) or not record_id:
                raise ArvanDNS01Error("matching ACME TXT record has no immutable ID")
            matches.append(record)
    return matches


def _records_url() -> str:
    return f"{DEFAULT_API_BASE}/domains/{quote(STAGING_ZONE, safe='')}/dns-records"


def manage_dns01(
    *,
    action: str,
    fqdn: str,
    value: str,
    token: str,
    request_fn: RequestFn = api_request,
) -> dict[str, Any]:
    """Idempotently create or remove one exact ACME TXT record."""
    if action not in {"present", "cleanup"}:
        raise ArvanDNS01Error("DNS-01 action must be present or cleanup")
    name = _record_name(fqdn)
    value = _validate_value(value)
    records_url = _records_url()
    matches = _matching_records(
        _records(request_fn("GET", records_url, token, None)), name=name, value=value
    )
    if action == "present":
        if matches:
            return {"status": "already_present", "record_count": len(matches)}
        payload = {
            "type": "txt",
            "name": name,
            "value": {"text": value},
            "ttl": TTL_SECONDS,
            "upstream_https": "default",
            "ip_filter_mode": {"count": "single", "order": "none", "geo_filter": "none"},
        }
        created = request_fn("POST", records_url, token, payload)
        data = created.get("data")
        if (
            not isinstance(data, dict)
            or data.get("type") != "txt"
            or data.get("name") != name
            or not isinstance(data.get("value"), dict)
            or data["value"].get("text") != value
            or not isinstance(data.get("id"), str)
            or not data["id"]
        ):
            raise ArvanDNS01Error("Arvan did not confirm the exact ACME TXT record")
        return {"status": "created", "record_count": 1}

    for record in matches:
        request_fn("DELETE", f"{records_url}/{quote(record['id'], safe='')}", token, None)
    return {"status": "cleaned", "record_count": len(matches)}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("present", "cleanup"))
    parser.add_argument("fqdn")
    parser.add_argument("value")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        result = manage_dns01(
            action=args.action,
            fqdn=args.fqdn,
            value=args.value,
            token=load_token(_token_file()),
        )
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(result["status"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
