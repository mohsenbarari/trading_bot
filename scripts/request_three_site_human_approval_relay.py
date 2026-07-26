#!/usr/bin/env python3
"""Request one action-bound staging approval receipt from the Witness.

This command never receives, copies, or writes the 48-hour issuer session.
The session remains owner-only on the Witness.  The controller presents its
dedicated HMAC credential over private TLS and receives an exact receipt that
can be included in the corresponding immutable staging artifact.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

import httpx

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.human_approval import (  # noqa: E402
    RELAY_COMMAND_SCHEMA,
    HumanApprovalError,
    human_approval_relay_command_bytes,
    parse_human_approval_relay_command,
    verify_human_approval,
)
from core.secure_file_io import (  # noqa: E402
    SecureFileError,
    read_secure_text,
    write_secure_new_bytes,
)
from core.writer_witness_auth import (  # noqa: E402
    WITNESS_HUMAN_APPROVAL_RELAY_PATH,
    WITNESS_RELAY_ORCHESTRATOR_SITE,
    WitnessClientCredential,
    sign_witness_request,
)


class RelayRequestError(RuntimeError):
    """Raised when a controller cannot obtain a safe relay receipt."""


_ENV_KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
_REQUIRED_CREDENTIAL_KEYS = frozenset(
    {
        "HUMAN_APPROVAL_RELAY_WITNESS_URL",
        "HUMAN_APPROVAL_RELAY_ORCHESTRATOR_KEY_ID",
        "HUMAN_APPROVAL_RELAY_ORCHESTRATOR_SECRET",
        "HUMAN_APPROVAL_RELAY_CA_BUNDLE",
        "WRITER_WITNESS_PUBLIC_KEY",
    }
)


def _strict_json(path: Path, *, label: str) -> dict[str, Any]:
    def hook(pairs):  # noqa: ANN001
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise RelayRequestError(f"{label} contains duplicate fields")
            value[key] = item
        return value

    try:
        value = json.loads(read_secure_text(path, label=label), object_pairs_hook=hook)
    except (SecureFileError, OSError, json.JSONDecodeError) as exc:
        raise RelayRequestError(f"{label} is unavailable or invalid") from exc
    if not isinstance(value, dict):
        raise RelayRequestError(f"{label} must be a JSON object")
    return value


def _credential_values(path: Path) -> dict[str, str]:
    try:
        source = read_secure_text(path, label="human approval relay credential")
    except (SecureFileError, OSError) as exc:
        raise RelayRequestError("human approval relay credential is unavailable") from exc
    values: dict[str, str] = {}
    for raw in source.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if not separator or not _ENV_KEY_RE.fullmatch(key) or key in values:
            raise RelayRequestError("human approval relay credential format is invalid")
        if "\x00" in value or "\r" in value or "\n" in value:
            raise RelayRequestError("human approval relay credential value is unsafe")
        values[key] = value
    if set(values) != _REQUIRED_CREDENTIAL_KEYS:
        raise RelayRequestError("human approval relay credential fields are invalid")
    url = values["HUMAN_APPROVAL_RELAY_WITNESS_URL"]
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise RelayRequestError("human approval relay Witness URL is unsafe")
    if len(values["HUMAN_APPROVAL_RELAY_ORCHESTRATOR_SECRET"].encode("utf-8")) < 32:
        raise RelayRequestError("human approval relay HMAC secret is unsafe")
    ca_bundle = Path(values["HUMAN_APPROVAL_RELAY_CA_BUNDLE"])
    if not ca_bundle.is_absolute() or not ca_bundle.is_file() or ca_bundle.is_symlink():
        raise RelayRequestError("human approval relay CA bundle is unsafe")
    return values


def request_receipt(args: argparse.Namespace) -> dict[str, Any]:
    subject = _strict_json(args.subject, label="human approval relay subject")
    policy = _strict_json(args.policy, label="human approval relay policy")
    credentials = _credential_values(args.credentials)
    command = parse_human_approval_relay_command(
        {
            "schema": RELAY_COMMAND_SCHEMA,
            "action": args.action,
            "environment": "staging",
            "subject": subject,
            "request_id": f"relay-{uuid4()}",
        }
    )
    body = human_approval_relay_command_bytes(command)
    witness_url = credentials["HUMAN_APPROVAL_RELAY_WITNESS_URL"].rstrip("/")
    headers = sign_witness_request(
        credential=WitnessClientCredential(
            key_id=credentials["HUMAN_APPROVAL_RELAY_ORCHESTRATOR_KEY_ID"],
            site=WITNESS_RELAY_ORCHESTRATOR_SITE,
            secret=credentials["HUMAN_APPROVAL_RELAY_ORCHESTRATOR_SECRET"],
        ),
        method="POST",
        path=WITNESS_HUMAN_APPROVAL_RELAY_PATH,
        body=body,
        request_id=command.request_id,
        timestamp=int(datetime.now(timezone.utc).timestamp()),
    )
    try:
        with httpx.Client(
            base_url=witness_url,
            timeout=max(0.1, args.timeout_seconds),
            verify=credentials["HUMAN_APPROVAL_RELAY_CA_BUNDLE"],
        ) as client:
            response = client.post(
                WITNESS_HUMAN_APPROVAL_RELAY_PATH,
                content=body,
                headers=headers,
            )
        receipt = response.json()
    except (httpx.HTTPError, ValueError, json.JSONDecodeError) as exc:
        raise RelayRequestError("human approval relay is unreachable or returned invalid JSON") from exc
    if response.status_code != 200 or not isinstance(receipt, dict):
        raise RelayRequestError("human approval relay rejected the requested action")
    try:
        verified = verify_human_approval(
            receipt,
            policy_payload=policy,
            expected_action=command.action,
            expected_environment=command.environment,
            expected_subject=command.subject,
            witness_relay_public_key=credentials["WRITER_WITNESS_PUBLIC_KEY"],
        )
    except HumanApprovalError as exc:
        raise RelayRequestError("human approval relay receipt verification failed") from exc
    try:
        write_secure_new_bytes(
            args.output,
            json.dumps(receipt, sort_keys=True, indent=2).encode("utf-8") + b"\n",
            label="human approval relay receipt",
            mode=0o600,
        )
    except SecureFileError as exc:
        raise RelayRequestError("human approval relay receipt output is unsafe") from exc
    return {
        "status": "approved",
        "approval_id": verified.approval_id,
        "action": verified.action,
        "environment": verified.environment,
        "expires_at": verified.expires_at.isoformat(),
        "output": str(args.output),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--action", required=True)
    parser.add_argument("--subject", type=Path, required=True)
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--credentials", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout-seconds", type=float, default=5.0)
    args = parser.parse_args(argv)
    try:
        print(json.dumps(request_receipt(args), sort_keys=True))
        return 0
    except Exception as exc:
        print(
            json.dumps(
                {"status": "blocked", "error_class": type(exc).__name__},
                sort_keys=True,
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
