#!/usr/bin/env python3
"""Create and remove the one DNS-01 TXT record needed by the WA-IR certificate.

Use this only as Certbot's manual DNS authenticator/cleanup hook.  It is
intentionally unable to modify any other domain or record type, and stores the
immutable Arvan record id in a root-only local state file before cleanup.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
import time
import urllib.parse
from typing import Any, Callable, Mapping


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.manage_three_site_mvp_arvan_routing import (  # noqa: E402
    ARVAN_API_BASE,
    ThreeSiteRoutingError,
    api_request,
    load_token,
)


CERTIFICATE_DOMAIN = "coin.gold-trade.ir"
ROOT_DOMAIN = "gold-trade.ir"
CHALLENGE_RECORD = "_acme-challenge.coin"
DEFAULT_TTL = 120
MAX_STATE_BYTES = 16 * 1024
_VALIDATION = re.compile(r"^[A-Za-z0-9_-]{20,256}$")
_STATE_FIELDS = {"schema", "domain", "record_name", "validation", "record_id"}


class AcmeDnsError(RuntimeError):
    """Raised when the limited DNS-01 workflow cannot be safely completed."""


RequestFn = Callable[[str, str, str, Mapping[str, Any] | None], dict[str, Any]]


def _records_url() -> str:
    return f"{ARVAN_API_BASE}/domains/{urllib.parse.quote(ROOT_DOMAIN, safe='')}/dns-records"


def _require_root() -> None:
    if os.geteuid() != 0:
        raise AcmeDnsError("this command must run as root")


def _validate_challenge(domain: str, validation: str) -> None:
    if domain != CERTIFICATE_DOMAIN:
        raise AcmeDnsError("DNS-01 hook is restricted to coin.gold-trade.ir")
    if not _VALIDATION.fullmatch(validation):
        raise AcmeDnsError("Certbot DNS validation token is not accepted")


def _record_value_text(record: Mapping[str, Any]) -> str | None:
    value = record.get("value")
    if not isinstance(value, Mapping):
        return None
    text = value.get("text")
    return text if isinstance(text, str) else None


def _matching_record(
    response: Mapping[str, Any], *, validation: str, require_id: str | None = None
) -> dict[str, Any]:
    records = response.get("data")
    if not isinstance(records, list):
        raise AcmeDnsError("Arvan DNS record response has an unexpected shape")
    matches = [
        record
        for record in records
        if isinstance(record, dict)
        and str(record.get("type", "")).lower() == "txt"
        and record.get("name") == CHALLENGE_RECORD
        and _record_value_text(record) == validation
        and (require_id is None or record.get("id") == require_id)
    ]
    if len(matches) != 1:
        raise AcmeDnsError(f"expected exactly one matching ACME TXT record, found {len(matches)}")
    return matches[0]


def _secure_state_path(state_dir: Path) -> Path:
    if state_dir.is_symlink():
        raise AcmeDnsError("ACME state directory must not be a symlink")
    state_dir.mkdir(parents=True, mode=0o700, exist_ok=True)
    directory_stat = state_dir.stat()
    if (
        not stat.S_ISDIR(directory_stat.st_mode)
        or directory_stat.st_uid != 0
        or directory_stat.st_mode & (stat.S_IRWXG | stat.S_IRWXO)
    ):
        raise AcmeDnsError("ACME state directory must be root-owned and private")
    return state_dir / "coin.gold-trade.ir.json"


def _write_state(path: Path, state: Mapping[str, str]) -> None:
    if set(state) != _STATE_FIELDS:
        raise AcmeDnsError("ACME state field set is invalid")
    encoded = json.dumps(
        state, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    if len(encoded) > MAX_STATE_BYTES:
        raise AcmeDnsError("ACME state exceeds the size limit")
    descriptor, temporary_name = tempfile.mkstemp(prefix=".acme-", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        os.write(descriptor, encoded + b"\n")
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        os.replace(temporary, path)
    except OSError as exc:
        raise AcmeDnsError("cannot atomically write ACME state") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary.exists():
            temporary.unlink()


def _read_state(path: Path) -> dict[str, str]:
    try:
        file_stat = path.lstat()
    except OSError as exc:
        raise AcmeDnsError("ACME cleanup state does not exist") from exc
    if (
        not stat.S_ISREG(file_stat.st_mode)
        or file_stat.st_uid != 0
        or file_stat.st_mode & (stat.S_IRWXG | stat.S_IRWXO)
        or file_stat.st_nlink != 1
        or file_stat.st_size > MAX_STATE_BYTES
    ):
        raise AcmeDnsError("ACME cleanup state is not a private regular root-owned file")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise AcmeDnsError("cannot securely open ACME cleanup state") from exc
    try:
        opened_stat = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened_stat.st_mode)
            or opened_stat.st_uid != 0
            or opened_stat.st_nlink != 1
            or opened_stat.st_mode & (stat.S_IRWXG | stat.S_IRWXO)
            or opened_stat.st_ino != file_stat.st_ino
            or opened_stat.st_dev != file_stat.st_dev
        ):
            raise AcmeDnsError("ACME cleanup state changed while being opened")
        raw = os.read(descriptor, MAX_STATE_BYTES + 1)
    finally:
        os.close(descriptor)
    if len(raw) > MAX_STATE_BYTES:
        raise AcmeDnsError("ACME cleanup state exceeds the size limit")
    try:
        state = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AcmeDnsError("ACME cleanup state is not valid JSON") from exc
    if not isinstance(state, dict) or set(state) != _STATE_FIELDS or not all(
        isinstance(value, str) for value in state.values()
    ):
        raise AcmeDnsError("ACME cleanup state schema is invalid")
    return state


def _remove_state(path: Path) -> None:
    try:
        path.unlink()
    except OSError as exc:
        raise AcmeDnsError("ACME TXT record was removed but cleanup state could not be removed") from exc


def present(
    *,
    domain: str,
    validation: str,
    token: str,
    state_dir: Path,
    request_fn: RequestFn = api_request,
    propagation_seconds: int = 30,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> dict[str, str]:
    _validate_challenge(domain, validation)
    if propagation_seconds < 0 or propagation_seconds > 600:
        raise AcmeDnsError("DNS propagation wait must be between 0 and 600 seconds")
    state_path = _secure_state_path(state_dir)
    if state_path.exists():
        existing = _read_state(state_path)
        if existing["validation"] == validation:
            return existing
        raise AcmeDnsError("a different ACME TXT validation is already active")

    records_url = _records_url()
    payload: dict[str, Any] = {
        "type": "TXT",
        "name": CHALLENGE_RECORD,
        "value": {"text": validation},
        "ttl": DEFAULT_TTL,
    }
    try:
        request_fn("POST", records_url, token, payload)
        matching = _matching_record(request_fn("GET", records_url, token, None), validation=validation)
    except ThreeSiteRoutingError as exc:
        raise AcmeDnsError(str(exc)) from exc
    record_id = matching.get("id")
    if not isinstance(record_id, str) or not record_id:
        raise AcmeDnsError("matching ACME TXT record has no immutable id")
    state = {
        "schema": "gold-trade-acme-dns-state-v1",
        "domain": domain,
        "record_name": CHALLENGE_RECORD,
        "validation": validation,
        "record_id": record_id,
    }
    _write_state(state_path, state)
    if propagation_seconds:
        sleep_fn(propagation_seconds)
    return state


def cleanup(
    *,
    domain: str,
    validation: str,
    token: str,
    state_dir: Path,
    request_fn: RequestFn = api_request,
) -> dict[str, str]:
    _validate_challenge(domain, validation)
    state_path = _secure_state_path(state_dir)
    state = _read_state(state_path)
    if (
        state["schema"] != "gold-trade-acme-dns-state-v1"
        or state["domain"] != domain
        or state["record_name"] != CHALLENGE_RECORD
        or state["validation"] != validation
    ):
        raise AcmeDnsError("ACME cleanup state does not bind this challenge")
    records_url = _records_url()
    try:
        _matching_record(
            request_fn("GET", records_url, token, None),
            validation=validation,
            require_id=state["record_id"],
        )
        request_fn(
            "DELETE",
            f"{records_url}/{urllib.parse.quote(state['record_id'], safe='')}",
            token,
            None,
        )
        records_after_delete = request_fn("GET", records_url, token, None)
        try:
            _matching_record(
                records_after_delete,
                validation=validation,
                require_id=state["record_id"],
            )
        except AcmeDnsError as exc:
            if "found 0" not in str(exc):
                raise
        else:
            raise AcmeDnsError("Arvan did not remove the exact ACME TXT record")
    except ThreeSiteRoutingError as exc:
        raise AcmeDnsError(str(exc)) from exc
    _remove_state(state_path)
    return state


def _hook_value(argument: str | None, environment_name: str) -> str:
    value = argument or os.getenv(environment_name)
    if not value:
        raise AcmeDnsError(f"--{environment_name.lower().replace('_', '-')} or {environment_name} is required")
    return value


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Arvan DNS-01 hook limited to coin.gold-trade.ir.")
    parser.add_argument("command", choices=("present", "cleanup"))
    parser.add_argument("--token-file", required=True, help="Root-only Arvan API token path.")
    parser.add_argument("--state-dir", required=True, help="Root-only directory for temporary record id state.")
    parser.add_argument("--domain")
    parser.add_argument("--validation")
    parser.add_argument("--propagation-seconds", type=int, default=30)
    parser.add_argument("--apply", action="store_true", help="Required for DNS mutation.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if not args.apply:
            raise AcmeDnsError("--apply is mandatory for DNS mutation")
        _require_root()
        domain = _hook_value(args.domain, "CERTBOT_DOMAIN")
        validation = _hook_value(args.validation, "CERTBOT_VALIDATION")
        token = load_token(Path(args.token_file))
        if args.command == "present":
            result = present(
                domain=domain,
                validation=validation,
                token=token,
                state_dir=Path(args.state_dir),
                propagation_seconds=args.propagation_seconds,
            )
        else:
            result = cleanup(
                domain=domain,
                validation=validation,
                token=token,
                state_dir=Path(args.state_dir),
            )
    except (AcmeDnsError, ThreeSiteRoutingError) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False, sort_keys=True))
        return 1
    print(
        json.dumps(
            {
                "status": "ok",
                "command": args.command,
                "domain": result["domain"],
                "record_name": result["record_name"],
                "record_id": result["record_id"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
