#!/usr/bin/env python3
"""Manual Melipayamak SMS probe, isolated from the production SMS path."""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

import httpx


REST_SEND_SMS_URL = "https://rest.payamak-panel.com/api/SendSMS/SendSMS"
SIMPLE_SMS2_URL = "https://api.payamak-panel.com/post/Send.asmx/SendSimpleSMS2"

KNOWN_FAILURE_CODES = {
    -110,
    -109,
    -108,
    0,
    2,
    3,
    4,
    5,
    6,
    7,
    9,
    10,
    11,
    12,
    14,
    15,
    16,
    17,
    18,
    35,
}


def mask(value: str | None, *, visible_tail: int = 4) -> str | None:
    if not value:
        return value
    text = str(value)
    if len(text) <= visible_tail:
        return "*" * len(text)
    return f"{'*' * (len(text) - visible_tail)}{text[-visible_tail:]}"


def parse_bool(value: str | bool | None) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def pick_credential(args: argparse.Namespace) -> tuple[str, str]:
    password = args.password or os.getenv("MELIPAYAMAK_PASSWORD")
    api_key = args.api_key or os.getenv("MELIPAYAMAK_API_KEY")
    mode = args.credential_mode

    if mode == "api-key":
        if not api_key:
            raise ValueError("MELIPAYAMAK_API_KEY is required for --credential-mode api-key")
        return "api-key", api_key
    if mode == "password":
        if not password:
            raise ValueError("MELIPAYAMAK_PASSWORD is required for --credential-mode password")
        return "password", password

    if api_key:
        return "api-key", api_key
    if password:
        return "password", password
    raise ValueError("Set MELIPAYAMAK_API_KEY or MELIPAYAMAK_PASSWORD")


def coerce_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def provider_result_code(payload: Any) -> int | None:
    if isinstance(payload, dict):
        for key in ("RetStatus", "retStatus", "status", "Status", "Value", "value"):
            code = coerce_int(payload.get(key))
            if code is not None:
                return code
    return coerce_int(payload)


def is_success(payload: Any) -> bool:
    if isinstance(payload, dict):
        ret_status = coerce_int(payload.get("RetStatus") or payload.get("retStatus"))
        if ret_status == 1:
            return True

        value = coerce_int(payload.get("Value") or payload.get("value"))
        if value is not None:
            return value > 0 and value not in KNOWN_FAILURE_CODES

        status_code = coerce_int(payload.get("status") or payload.get("Status"))
        return status_code is not None and status_code > 0 and status_code not in KNOWN_FAILURE_CODES

    code = coerce_int(payload)
    return code is not None and code > 0 and code not in KNOWN_FAILURE_CODES


def parse_response(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return response.text.strip()


def build_payload(args: argparse.Namespace, credential: str) -> dict[str, Any]:
    return {
        "username": args.username or os.getenv("MELIPAYAMAK_USERNAME"),
        "password": credential,
        "to": args.to,
        "from": args.from_number or os.getenv("MELIPAYAMAK_FROM"),
        "text": args.text,
        "isFlash": parse_bool(args.is_flash),
    }


def redacted_payload(payload: dict[str, Any], credential_mode: str) -> dict[str, Any]:
    return {
        "username": mask(str(payload.get("username") or "")),
        "password": f"[{credential_mode}]",
        "to": mask(str(payload.get("to") or "")),
        "from": mask(str(payload.get("from") or "")),
        "text_length": len(str(payload.get("text") or "")),
        "isFlash": payload.get("isFlash"),
    }


def send_probe(args: argparse.Namespace, payload: dict[str, Any]) -> tuple[httpx.Response, Any]:
    timeout = httpx.Timeout(args.timeout)
    if args.endpoint == "send-simple-sms2":
        response = httpx.get(SIMPLE_SMS2_URL, params=payload, timeout=timeout)
    else:
        response = httpx.post(REST_SEND_SMS_URL, data=payload, timeout=timeout)
    return response, parse_response(response)


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe Melipayamak SMS delivery without changing app SMS code.")
    parser.add_argument("--endpoint", choices=("package-rest", "send-simple-sms2"), default="package-rest")
    parser.add_argument("--credential-mode", choices=("auto", "api-key", "password"), default="auto")
    parser.add_argument("--username", default=None)
    parser.add_argument("--password", default=None)
    parser.add_argument("--api-key", default=None)
    parser.add_argument("--from-number", default=None)
    parser.add_argument("--to", default="09370809280")
    parser.add_argument("--text", default="تست اتصال پنل ملی پیامک سامانه معاملاتی")
    parser.add_argument("--is-flash", default="false")
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("--send", action="store_true", help="Actually send the SMS. Default is dry-run.")
    args = parser.parse_args()

    try:
        credential_mode, credential = pick_credential(args)
        payload = build_payload(args, credential)
        missing = [key for key in ("username", "password", "from", "to", "text") if not payload.get(key)]
        if missing:
            raise ValueError(f"Missing required values: {', '.join(missing)}")
    except ValueError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        return 2

    summary = {
        "endpoint": args.endpoint,
        "send": args.send,
        "credential_mode": credential_mode,
        "payload": redacted_payload(payload, credential_mode),
    }
    if not args.send:
        print(json.dumps({**summary, "ok": True, "dry_run": True}, ensure_ascii=False, indent=2))
        return 0

    try:
        response, parsed = send_probe(args, payload)
    except Exception as exc:
        print(json.dumps({**summary, "ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        return 1

    accepted = 200 <= response.status_code < 300 and is_success(parsed)
    result = {
        **summary,
        "ok": accepted,
        "http_status": response.status_code,
        "provider_code": provider_result_code(parsed),
        "provider_response": parsed,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if accepted else 1


if __name__ == "__main__":
    sys.exit(main())
