#!/usr/bin/env python3
"""Safely inspect or change one proxied Arvan A-record origin.

This is a low-level, operator-controlled routing primitive.  It deliberately
does not decide whether a standby is safe to promote; callers must establish
application readiness and fencing before using ``--apply``.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
import socket
import stat
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable


DEFAULT_API_BASE = "https://napi.arvancloud.ir/cdn/4.0"
PRODUCTION_ROOT_DOMAIN = "gold-trade.ir"
FAILOVER_TEST_ROOT_DOMAIN = "gold-trading.ir"


class ArvanOriginSwitchError(RuntimeError):
    """Raised when an origin switch cannot be proven safe or successful."""


def _json_bytes(payload: dict[str, Any] | None) -> bytes | None:
    if payload is None:
        return None
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def api_request(
    method: str,
    url: str,
    token: str,
    payload: dict[str, Any] | None = None,
    *,
    timeout: float = 20.0,
) -> dict[str, Any]:
    headers = {
        "Accept": "application/json",
        "Authorization": f"Apikey {token}",
        "User-Agent": "trading-bot-arvan-origin-switch/1",
    }
    body = _json_bytes(payload)
    if body is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:2000]
        raise ArvanOriginSwitchError(f"Arvan API returned HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise ArvanOriginSwitchError(f"Arvan API is unreachable: {exc.reason}") from exc
    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ArvanOriginSwitchError("Arvan API returned a non-JSON response") from exc
    if not isinstance(decoded, dict):
        raise ArvanOriginSwitchError("Arvan API returned an unexpected response shape")
    return decoded


def load_token(path: Path) -> str:
    try:
        mode = stat.S_IMODE(path.stat().st_mode)
    except FileNotFoundError as exc:
        raise ArvanOriginSwitchError(f"Token file does not exist: {path}") from exc
    if mode & 0o077:
        raise ArvanOriginSwitchError(
            f"Token file permissions are too broad ({mode:o}); expected owner-only access such as 600"
        )
    token = path.read_text(encoding="utf-8").strip()
    if not token:
        raise ArvanOriginSwitchError("Token file is empty")
    return token


def _records_from_response(response: dict[str, Any]) -> list[dict[str, Any]]:
    records = response.get("data")
    if not isinstance(records, list):
        raise ArvanOriginSwitchError("DNS record list has an unexpected response shape")
    return [record for record in records if isinstance(record, dict)]


def find_exact_a_record(response: dict[str, Any], name: str) -> dict[str, Any]:
    matches = [
        record
        for record in _records_from_response(response)
        if record.get("type") == "a" and record.get("name") == name
    ]
    if len(matches) != 1:
        raise ArvanOriginSwitchError(
            f"Expected exactly one A record named {name!r}, found {len(matches)}"
        )
    return matches[0]


def single_origin_ip(record: dict[str, Any]) -> str:
    values = record.get("value")
    if not isinstance(values, list) or len(values) != 1 or not isinstance(values[0], dict):
        raise ArvanOriginSwitchError("Origin switching requires exactly one current A-record value")
    ip = values[0].get("ip")
    if not isinstance(ip, str) or not ip:
        raise ArvanOriginSwitchError("A record does not contain a valid origin IP")
    return ip


def public_record_summary(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": record.get("id"),
        "type": record.get("type"),
        "name": record.get("name"),
        "origin_ip": single_origin_ip(record),
        "cloud": record.get("cloud"),
        "ttl": record.get("ttl"),
        "upstream_https": record.get("upstream_https"),
    }


def build_update_payload(record: dict[str, Any], target_ip: str) -> dict[str, Any]:
    if record.get("cloud") is not True:
        raise ArvanOriginSwitchError("Refusing to switch a record that is not proxied by Arvan")
    if record.get("upstream_https") != "https":
        raise ArvanOriginSwitchError("Refusing to switch a record without HTTPS edge-to-origin")
    current_value = record["value"][0]
    value = {
        "ip": target_ip,
        "port": current_value.get("port"),
        "weight": current_value.get("weight", 100),
        "country": current_value.get("country", ""),
    }
    payload = {
        "type": "a",
        "name": record.get("name"),
        "value": [value],
        "ttl": record.get("ttl"),
        "cloud": True,
        "upstream_https": "https",
    }
    if record.get("ip_filter_mode") is not None:
        payload["ip_filter_mode"] = record["ip_filter_mode"]
    return payload


def confirmation_phrase(domain: str, record_name: str, target_ip: str) -> str:
    return f"switch:{domain}:{record_name}:{target_ip}"


def enforce_apply_domain_scope(domain: str, *, apply: bool) -> None:
    """Keep this pre-production primitive confined to the isolated test zone."""
    if not apply:
        return
    if domain != FAILOVER_TEST_ROOT_DOMAIN:
        raise ArvanOriginSwitchError(
            "Applied Arvan changes are currently restricted to the failover-test "
            f"domain {FAILOVER_TEST_ROOT_DOMAIN!r}; production domain "
            f"{PRODUCTION_ROOT_DOMAIN!r} requires a separate post-matrix authorization"
        )


def append_audit_event(path: Path, event: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "host": socket.gethostname(),
        **event,
    }
    descriptor = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        os.write(
            descriptor,
            (json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8"),
        )
    finally:
        os.close(descriptor)


RequestFn = Callable[[str, str, str, dict[str, Any] | None], dict[str, Any]]


def inspect_or_switch(
    *,
    domain: str,
    record_name: str,
    target_ip: str,
    token: str,
    expected_current_ip: str | None,
    apply: bool,
    confirmation: str | None,
    api_base: str = DEFAULT_API_BASE,
    request_fn: RequestFn = api_request,
) -> dict[str, Any]:
    enforce_apply_domain_scope(domain, apply=apply)
    encoded_domain = urllib.parse.quote(domain, safe="")
    records_url = f"{api_base.rstrip('/')}/domains/{encoded_domain}/dns-records"
    current = find_exact_a_record(request_fn("GET", records_url, token, None), record_name)
    current_ip = single_origin_ip(current)
    result: dict[str, Any] = {
        "status": "already_at_target" if current_ip == target_ip else "planned",
        "applied": False,
        "domain": domain,
        "record": record_name,
        "before": public_record_summary(current),
        "target_ip": target_ip,
    }
    if current_ip == target_ip:
        result["after"] = result["before"]
        return result
    if not apply:
        return result
    if not expected_current_ip:
        raise ArvanOriginSwitchError("--expected-current-ip is mandatory with --apply")
    if current_ip != expected_current_ip:
        raise ArvanOriginSwitchError(
            f"Current origin is {current_ip}, not the explicitly expected {expected_current_ip}; no change made"
        )
    required_confirmation = confirmation_phrase(domain, record_name, target_ip)
    if confirmation != required_confirmation:
        raise ArvanOriginSwitchError(
            f"Confirmation mismatch; use --confirm {required_confirmation!r} after reviewing the dry run"
        )
    record_id = current.get("id")
    if not isinstance(record_id, str) or not record_id:
        raise ArvanOriginSwitchError("DNS record is missing its immutable record id")
    record_url = f"{records_url}/{urllib.parse.quote(record_id, safe='')}"
    request_fn("PUT", record_url, token, build_update_payload(current, target_ip))
    verified_response = request_fn("GET", records_url, token, None)
    verified = find_exact_a_record(verified_response, record_name)
    verified_ip = single_origin_ip(verified)
    if verified_ip != target_ip:
        raise ArvanOriginSwitchError(
            f"Arvan accepted the update but read-back returned {verified_ip}; expected {target_ip}"
        )
    result.update(status="switched", applied=True, after=public_record_summary(verified))
    return result


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Dry-run or explicitly change one proxied Arvan A-record origin."
    )
    parser.add_argument("--domain", required=True)
    parser.add_argument("--record", required=True, help="Relative record name, for example app.")
    parser.add_argument("--target-ip", required=True)
    parser.add_argument("--expected-current-ip")
    parser.add_argument(
        "--token-file",
        default=os.getenv("ARVAN_API_TOKEN_FILE"),
        help="Owner-readable file containing the Arvan API token.",
    )
    parser.add_argument("--api-base", default=DEFAULT_API_BASE)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm")
    parser.add_argument("--operator", help="Named operator responsible for an applied change.")
    parser.add_argument("--reason", help="Incident, drill, or change reason for an applied change.")
    parser.add_argument("--audit-log", help="Owner-only JSONL audit log; mandatory with --apply.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not args.token_file:
        print("error: --token-file or ARVAN_API_TOKEN_FILE is required", file=sys.stderr)
        return 2
    if args.apply and not (args.operator and args.reason and args.audit_log):
        print(
            "error: --operator, --reason, and --audit-log are mandatory with --apply",
            file=sys.stderr,
        )
        return 2
    try:
        result = inspect_or_switch(
            domain=args.domain,
            record_name=args.record,
            target_ip=args.target_ip,
            token=load_token(Path(args.token_file)),
            expected_current_ip=args.expected_current_ip,
            apply=args.apply,
            confirmation=args.confirm,
            api_base=args.api_base,
        )
    except ArvanOriginSwitchError as exc:
        if args.apply and args.audit_log:
            append_audit_event(
                Path(args.audit_log),
                {
                    "event": "arvan.origin_switch.failed",
                    "operator": args.operator,
                    "reason": args.reason,
                    "domain": args.domain,
                    "record": args.record,
                    "target_ip": args.target_ip,
                    "expected_current_ip": args.expected_current_ip,
                    "error": str(exc),
                },
            )
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False, sort_keys=True))
        return 1
    if args.apply:
        append_audit_event(
            Path(args.audit_log),
            {
                "event": "arvan.origin_switch.applied",
                "operator": args.operator,
                "reason": args.reason,
                "result": result,
            },
        )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
