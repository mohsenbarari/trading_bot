#!/usr/bin/env python3
"""Manual SMS.ir Verify-template probe, isolated from the production OTP path."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.log_redaction import mask_mobile
from core.sms import _normalize_mobile, _post_smsir


DEFAULT_TEMPLATE_ID = 232000
DEFAULT_PARAMETER_NAME = "CODE"


def build_verify_payload(
    *,
    mobile: str,
    code: str,
    template_id: int,
    parameter_name: str,
    payload_style: str,
) -> dict[str, Any]:
    normalized_mobile = _normalize_mobile(mobile)
    parameter = {"name": parameter_name, "value": str(code)}

    if payload_style == "package":
        return {
            "Mobile": normalized_mobile,
            "TemplateId": template_id,
            "Parameters": [parameter],
        }

    if payload_style == "rest":
        return {
            "mobile": normalized_mobile,
            "templateId": template_id,
            "parameters": [parameter],
        }

    raise ValueError(f"Unsupported payload_style: {payload_style}")


def endpoint_for_style(payload_style: str) -> str:
    return "v1/send/verify/" if payload_style == "package" else "v1/send/verify"


def redacted_payload(payload: dict[str, Any]) -> dict[str, Any]:
    safe_payload = dict(payload)
    mobile_key = "Mobile" if "Mobile" in safe_payload else "mobile"
    if mobile_key in safe_payload:
        safe_payload[mobile_key] = mask_mobile(str(safe_payload[mobile_key]))

    parameters_key = "Parameters" if "Parameters" in safe_payload else "parameters"
    parameters = safe_payload.get(parameters_key)
    if isinstance(parameters, list):
        safe_payload[parameters_key] = [
            {**item, "value": "<code>"} if isinstance(item, dict) else item
            for item in parameters
        ]

    return safe_payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Probe SMS.ir Verify template delivery.")
    parser.add_argument("mobile", help="Target mobile number, for example 09370000000.")
    parser.add_argument("--code", default="12345", help="Test code value. Default: 12345.")
    parser.add_argument("--template-id", type=int, default=DEFAULT_TEMPLATE_ID)
    parser.add_argument("--parameter-name", default=DEFAULT_PARAMETER_NAME)
    parser.add_argument(
        "--payload-style",
        choices=("package", "rest"),
        default="package",
        help="package matches smsir-python; rest matches the app's legacy REST payload.",
    )
    parser.add_argument("--send", action="store_true", help="Actually send the SMS. Default is dry-run.")
    args = parser.parse_args(argv)

    payload = build_verify_payload(
        mobile=args.mobile,
        code=args.code,
        template_id=args.template_id,
        parameter_name=args.parameter_name,
        payload_style=args.payload_style,
    )
    endpoint = endpoint_for_style(args.payload_style)

    if not args.send:
        print(
            json.dumps(
                {
                    "dry_run": True,
                    "endpoint": endpoint,
                    "payload": redacted_payload(payload),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    data = _post_smsir(endpoint, payload)
    if data is None:
        print("FAILED")
        return 1

    print(
        json.dumps(
            {
                "status": data.get("status"),
                "message": data.get("message"),
                "data": data.get("data"),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
