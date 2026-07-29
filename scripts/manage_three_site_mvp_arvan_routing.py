#!/usr/bin/env python3
"""Conservatively route the production web record between the two web sites.

This is deliberately a narrow operational primitive for the three-site MVP.
It does not decide whether an outage exists.  A normal route change requires a
fresh, root-owned Witness promotion proof and an exact read-before-write check
of the Arvan record.  The one-time proxy bootstrap has a separate, more
restrictive path that can only retain WA-FI as the origin.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import fcntl
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import socket
import stat
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
from typing import Any, Callable, Mapping


ARVAN_API_BASE = "https://napi.arvancloud.ir/cdn/4.0"
PRODUCTION_DOMAIN = "gold-trade.ir"
PRODUCTION_RECORD = "coin"
SITE_ORIGINS = {
    "webapp_fi": "65.109.220.59",
    "webapp_ir": "95.38.164.29",
}
PROMOTION_PROOF_SCHEMA = "gold-trade-writer-promotion-proof-v1"
EXPECTED_RELEASE_SHA = "2c08da14bfa0ef94d9c788e478d30ddc3f31a3c5"
EXPECTED_ALEMBIC_REVISION = "f2c7d8e9a0b1"
MAX_PROOF_AGE_SECONDS = 120
MAX_SNAPSHOT_AGE_SECONDS = 30
MAX_CLOCK_SKEW_SECONDS = 15
MAX_SECRET_BYTES = 16 * 1024
MAX_PROOF_BYTES = 64 * 1024
MAX_AUDIT_BYTES = 4 * 1024 * 1024
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_RELEASE_SHA = re.compile(r"^[0-9a-f]{40}$")
_ASCII_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")

_BASE_PROOF_FIELDS = {
    "schema",
    "action",
    "operation_id",
    "source_site",
    "target_site",
    "lease_id",
    "epoch",
    "lease_expires_at",
    "issued_at",
    "snapshot_id",
    "source_generation",
    "release_sha",
    "alembic_revision",
    "snapshot_age_seconds",
    "snapshot_published_at",
    "snapshot_ready_at",
    "snapshot_restore_receipt_sha256",
    "snapshot_stage_receipt_sha256",
    "witness_proof_sha256",
    "proof_sha256",
}


class ThreeSiteRoutingError(RuntimeError):
    """Raised when a route change cannot be proven safe."""


def _canonical_json_bytes(value: Mapping[str, Any]) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ThreeSiteRoutingError("proof is not canonical JSON") from exc


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _require_root() -> None:
    if os.geteuid() != 0:
        raise ThreeSiteRoutingError("this command must run as root")


def _secure_read(path: Path, *, label: str, max_bytes: int) -> bytes:
    """Read one root-owned regular file without following a link."""
    try:
        path_stat = path.lstat()
    except OSError as exc:
        raise ThreeSiteRoutingError(f"cannot stat {label}: {path}") from exc
    if not stat.S_ISREG(path_stat.st_mode):
        raise ThreeSiteRoutingError(f"{label} must be a regular file")
    if path_stat.st_uid != 0:
        raise ThreeSiteRoutingError(f"{label} must be owned by root")
    if path_stat.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise ThreeSiteRoutingError(f"{label} is group/world accessible")
    if path_stat.st_nlink != 1:
        raise ThreeSiteRoutingError(f"{label} must not have a hard link")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ThreeSiteRoutingError(f"cannot securely open {label}: {path}") from exc
    try:
        opened_stat = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened_stat.st_mode)
            or opened_stat.st_uid != 0
            or opened_stat.st_nlink != 1
            or opened_stat.st_mode & (stat.S_IRWXG | stat.S_IRWXO)
            or opened_stat.st_ino != path_stat.st_ino
            or opened_stat.st_dev != path_stat.st_dev
        ):
            raise ThreeSiteRoutingError(f"{label} changed while being opened")
        chunks: list[bytes] = []
        remaining = max_bytes + 1
        while remaining:
            chunk = os.read(descriptor, min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
    finally:
        os.close(descriptor)
    if not payload:
        raise ThreeSiteRoutingError(f"{label} is empty")
    if len(payload) > max_bytes:
        raise ThreeSiteRoutingError(f"{label} exceeds the size limit")
    return payload


def load_token(path: Path) -> str:
    token = _secure_read(path, label="Arvan API token", max_bytes=MAX_SECRET_BYTES).decode(
        "utf-8"
    ).strip()
    if not token:
        raise ThreeSiteRoutingError("Arvan API token is empty")
    return token


def load_promotion_proof(path: Path) -> dict[str, Any]:
    raw = _secure_read(path, label="Witness promotion proof", max_bytes=MAX_PROOF_BYTES)

    def no_duplicate_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ThreeSiteRoutingError("Witness promotion proof contains duplicate JSON keys")
            result[key] = value
        return result

    try:
        proof = json.loads(raw.decode("utf-8"), object_pairs_hook=no_duplicate_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ThreeSiteRoutingError("Witness promotion proof is not valid JSON") from exc
    if not isinstance(proof, dict):
        raise ThreeSiteRoutingError("Witness promotion proof must be a JSON object")
    return proof


def _parse_timestamp(value: object, *, field: str) -> datetime:
    if not isinstance(value, str):
        raise ThreeSiteRoutingError(f"proof field {field} must be an RFC3339 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ThreeSiteRoutingError(f"proof field {field} is not a valid timestamp") from exc
    if parsed.tzinfo is None:
        raise ThreeSiteRoutingError(f"proof field {field} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _require_uuid(value: object, *, field: str) -> str:
    if not isinstance(value, str):
        raise ThreeSiteRoutingError(f"proof field {field} must be a UUID")
    try:
        parsed = uuid.UUID(value)
    except ValueError as exc:
        raise ThreeSiteRoutingError(f"proof field {field} must be a UUID") from exc
    if value != value.lower() or str(parsed) != value:
        raise ThreeSiteRoutingError(f"proof field {field} must use canonical UUID form")
    return value


def _require_sha256(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise ThreeSiteRoutingError(f"proof field {field} must be a lowercase SHA-256")
    return value


def _require_ascii_id(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not _ASCII_ID.fullmatch(value):
        raise ThreeSiteRoutingError(f"proof field {field} must be a bounded ASCII identifier")
    return value


def verify_promotion_proof(
    proof: Mapping[str, Any],
    *,
    target_site: str,
    now: datetime | None = None,
    max_proof_age_seconds: int = MAX_PROOF_AGE_SECONDS,
    max_snapshot_age_seconds: int = MAX_SNAPSHOT_AGE_SECONDS,
) -> dict[str, Any]:
    """Validate the narrow Witness proof contract needed for a route switch."""
    if target_site not in SITE_ORIGINS:
        raise ThreeSiteRoutingError("target site is not an allowed production site")
    actual_fields = set(proof)
    expected_fields = set(_BASE_PROOF_FIELDS)
    if actual_fields != expected_fields:
        missing = sorted(expected_fields - actual_fields)
        unexpected = sorted(actual_fields - expected_fields)
        raise ThreeSiteRoutingError(
            f"Witness promotion proof has an unexpected field set; missing={missing}, unexpected={unexpected}"
        )
    if proof.get("schema") != PROMOTION_PROOF_SCHEMA:
        raise ThreeSiteRoutingError("Witness promotion proof schema is not accepted")

    action = proof.get("action")
    expected_action = "promote_ir" if target_site == "webapp_ir" else "failback_fi"
    expected_source = "webapp_fi" if target_site == "webapp_ir" else "webapp_ir"
    if action != expected_action:
        raise ThreeSiteRoutingError("Witness promotion proof action does not match the requested target")
    if proof.get("target_site") != target_site or proof.get("source_site") != expected_source:
        raise ThreeSiteRoutingError("Witness promotion proof site binding is invalid")

    _require_uuid(proof.get("operation_id"), field="operation_id")
    _require_ascii_id(proof.get("snapshot_id"), field="snapshot_id")
    lease_id = proof.get("lease_id")
    if not isinstance(lease_id, str) or not lease_id.strip() or len(lease_id) > 128:
        raise ThreeSiteRoutingError("proof field lease_id must be a bounded non-empty string")
    epoch = proof.get("epoch")
    if isinstance(epoch, bool) or not isinstance(epoch, int) or epoch < 1:
        raise ThreeSiteRoutingError("proof field epoch must be a positive integer")
    _require_ascii_id(proof.get("source_generation"), field="source_generation")
    snapshot_age = proof.get("snapshot_age_seconds")
    if isinstance(snapshot_age, bool) or not isinstance(snapshot_age, int) or snapshot_age < 0:
        raise ThreeSiteRoutingError("proof field snapshot_age_seconds must be a non-negative integer")
    if snapshot_age > max_snapshot_age_seconds:
        raise ThreeSiteRoutingError("Witness proof snapshot is older than the allowed recovery point")
    for field in (
        "snapshot_restore_receipt_sha256",
        "snapshot_stage_receipt_sha256",
        "witness_proof_sha256",
        "proof_sha256",
    ):
        _require_sha256(proof.get(field), field=field)
    if not isinstance(proof["release_sha"], str) or not _RELEASE_SHA.fullmatch(proof["release_sha"]):
        raise ThreeSiteRoutingError("proof field release_sha must be a full lowercase Git SHA")
    _require_ascii_id(proof["alembic_revision"], field="alembic_revision")
    if proof["release_sha"] != EXPECTED_RELEASE_SHA:
        raise ThreeSiteRoutingError("Witness promotion proof release does not match the deployed MVP release")
    if proof["alembic_revision"] != EXPECTED_ALEMBIC_REVISION:
        raise ThreeSiteRoutingError("Witness promotion proof database revision does not match production")

    unsigned = dict(proof)
    supplied_hash = unsigned.pop("proof_sha256")
    if _sha256(_canonical_json_bytes(unsigned)) != supplied_hash:
        raise ThreeSiteRoutingError("Witness promotion proof SHA-256 does not verify")

    reference = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    issued_at = _parse_timestamp(proof.get("issued_at"), field="issued_at")
    lease_expires_at = _parse_timestamp(proof.get("lease_expires_at"), field="lease_expires_at")
    published_at = _parse_timestamp(proof.get("snapshot_published_at"), field="snapshot_published_at")
    ready_at = _parse_timestamp(proof.get("snapshot_ready_at"), field="snapshot_ready_at")
    if issued_at > reference + timedelta(seconds=MAX_CLOCK_SKEW_SECONDS):
        raise ThreeSiteRoutingError("Witness promotion proof is issued in the future")
    if (reference - issued_at).total_seconds() > max_proof_age_seconds:
        raise ThreeSiteRoutingError("Witness promotion proof is stale")
    if lease_expires_at <= reference:
        raise ThreeSiteRoutingError("Witness promotion proof lease has expired")
    if lease_expires_at <= issued_at:
        raise ThreeSiteRoutingError("Witness promotion proof lease does not outlive issuance")
    if published_at > reference + timedelta(seconds=MAX_CLOCK_SKEW_SECONDS):
        raise ThreeSiteRoutingError("snapshot publication time is in the future")
    if ready_at < published_at or ready_at > issued_at:
        raise ThreeSiteRoutingError("Witness promotion proof snapshot timing is inconsistent")
    actual_snapshot_age = (reference - published_at).total_seconds()
    if actual_snapshot_age > max_snapshot_age_seconds + MAX_CLOCK_SKEW_SECONDS:
        raise ThreeSiteRoutingError("snapshot publication is older than the allowed recovery point")
    return {
        "action": action,
        "operation_id": proof["operation_id"],
        "target_site": target_site,
        "target_ip": SITE_ORIGINS[target_site],
        "lease_id": lease_id,
        "epoch": epoch,
        "snapshot_id": proof["snapshot_id"],
        "proof_sha256": supplied_hash,
        "snapshot_age_seconds": round(actual_snapshot_age, 3),
    }


def validate_api_url(url: str) -> None:
    reviewed = urllib.parse.urlsplit(ARVAN_API_BASE)
    candidate = urllib.parse.urlsplit(url)
    if candidate.scheme != "https" or candidate.hostname != reviewed.hostname:
        raise ThreeSiteRoutingError("Arvan API URL must use the reviewed HTTPS endpoint")
    prefix = reviewed.path.rstrip("/") + "/"
    if not candidate.path.startswith(prefix):
        raise ThreeSiteRoutingError("Arvan API URL is outside the reviewed CDN API path")
    if candidate.username or candidate.password or candidate.port not in (None, 443):
        raise ThreeSiteRoutingError("Arvan API URL contains a forbidden authority component")


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        raise ThreeSiteRoutingError("Arvan API redirects are forbidden")


def _json_bytes(payload: Mapping[str, Any] | None) -> bytes | None:
    if payload is None:
        return None
    return _canonical_json_bytes(payload)


def api_request(
    method: str,
    url: str,
    token: str,
    payload: Mapping[str, Any] | None = None,
    *,
    timeout: float = 20.0,
) -> dict[str, Any]:
    validate_api_url(url)
    headers = {
        "Accept": "application/json",
        "Authorization": f"Apikey {token}",
        "User-Agent": "trading-bot-three-site-mvp-routing/1",
    }
    body = _json_bytes(payload)
    if body is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        opener = urllib.request.build_opener(_NoRedirectHandler())
        with opener.open(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:1000]
        raise ThreeSiteRoutingError(f"Arvan API returned HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise ThreeSiteRoutingError(f"Arvan API is unreachable: {exc.reason}") from exc
    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ThreeSiteRoutingError("Arvan API returned non-JSON data") from exc
    if not isinstance(decoded, dict):
        raise ThreeSiteRoutingError("Arvan API returned an unexpected response shape")
    return decoded


def _records(response: Mapping[str, Any]) -> list[dict[str, Any]]:
    raw_records = response.get("data")
    if not isinstance(raw_records, list):
        raise ThreeSiteRoutingError("Arvan DNS record response has an unexpected shape")
    return [record for record in raw_records if isinstance(record, dict)]


def find_production_record(response: Mapping[str, Any]) -> dict[str, Any]:
    matches = [
        record
        for record in _records(response)
        if record.get("type") == "a" and record.get("name") == PRODUCTION_RECORD
    ]
    if len(matches) != 1:
        raise ThreeSiteRoutingError(
            f"expected exactly one production A record named {PRODUCTION_RECORD!r}, found {len(matches)}"
        )
    return matches[0]


def record_origin_ip(record: Mapping[str, Any]) -> str:
    values = record.get("value")
    if not isinstance(values, list) or len(values) != 1 or not isinstance(values[0], dict):
        raise ThreeSiteRoutingError("route changes require exactly one A-record origin")
    ip = values[0].get("ip")
    if not isinstance(ip, str):
        raise ThreeSiteRoutingError("A record does not contain an origin IP")
    try:
        return str(ipaddress.IPv4Address(ip))
    except ipaddress.AddressValueError as exc:
        raise ThreeSiteRoutingError("A record origin is not an IPv4 address") from exc


def public_record_summary(record: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": record.get("id"),
        "type": record.get("type"),
        "name": record.get("name"),
        "origin_ip": record_origin_ip(record),
        "cloud": record.get("cloud"),
        "ttl": record.get("ttl"),
        "upstream_https": record.get("upstream_https"),
    }


def build_update_payload(record: Mapping[str, Any], *, target_ip: str) -> dict[str, Any]:
    if record.get("upstream_https") != "https":
        raise ThreeSiteRoutingError("record must use HTTPS from Arvan to the origin")
    values = record.get("value")
    if not isinstance(values, list) or not isinstance(values[0], dict):
        raise ThreeSiteRoutingError("record values are invalid")
    current_value = values[0]
    return {
        "type": "a",
        "name": PRODUCTION_RECORD,
        "value": [
            {
                "ip": target_ip,
                "port": current_value.get("port"),
                "weight": current_value.get("weight", 100),
                "country": current_value.get("country", ""),
            }
        ],
        "ttl": record.get("ttl"),
        "cloud": True,
        "upstream_https": "https",
        **(
            {"ip_filter_mode": record["ip_filter_mode"]}
            if record.get("ip_filter_mode") is not None
            else {}
        ),
    }


RequestFn = Callable[[str, str, str, Mapping[str, Any] | None], dict[str, Any]]


def _records_url() -> str:
    return f"{ARVAN_API_BASE}/domains/{urllib.parse.quote(PRODUCTION_DOMAIN, safe='')}/dns-records"


def inspect_or_route(
    *,
    target_site: str,
    token: str,
    expected_current_ip: str | None,
    apply: bool,
    bootstrap_proxy: bool,
    proof: Mapping[str, Any] | None,
    request_fn: RequestFn = api_request,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Inspect or change the single production origin after strict fencing."""
    if target_site not in SITE_ORIGINS:
        raise ThreeSiteRoutingError("target site is not allowed")
    if bootstrap_proxy and target_site != "webapp_fi":
        raise ThreeSiteRoutingError("proxy bootstrap may only retain WA-FI as origin")
    proof_summary: dict[str, Any] | None = None
    if apply and not bootstrap_proxy:
        if proof is None:
            raise ThreeSiteRoutingError("a Witness promotion proof is mandatory for a route switch")
        proof_summary = verify_promotion_proof(proof, target_site=target_site, now=now)
    if apply and not expected_current_ip:
        raise ThreeSiteRoutingError("--expected-current-ip is mandatory with --apply")

    records_url = _records_url()
    current = find_production_record(request_fn("GET", records_url, token, None))
    current_ip = record_origin_ip(current)
    target_ip = SITE_ORIGINS[target_site]
    result: dict[str, Any] = {
        "status": "already_at_target" if current_ip == target_ip and current.get("cloud") is True else "planned",
        "applied": False,
        "domain": PRODUCTION_DOMAIN,
        "record": PRODUCTION_RECORD,
        "target_site": target_site,
        "target_ip": target_ip,
        "before": public_record_summary(current),
    }
    if proof_summary is not None:
        result["proof"] = proof_summary
    if current_ip == target_ip and current.get("cloud") is True:
        result["after"] = result["before"]
        return result
    if not apply:
        return result
    if current_ip != expected_current_ip:
        raise ThreeSiteRoutingError(
            f"current origin is {current_ip}, not explicitly expected {expected_current_ip}; no change made"
        )
    if bootstrap_proxy:
        if current.get("cloud") is not False:
            raise ThreeSiteRoutingError("proxy bootstrap requires the current record to be explicitly unproxied")
        if current_ip != SITE_ORIGINS["webapp_fi"]:
            raise ThreeSiteRoutingError("proxy bootstrap requires WA-FI to remain the origin")
    elif current.get("cloud") is not True:
        raise ThreeSiteRoutingError("normal route switching requires an already proxied record")

    record_id = current.get("id")
    if not isinstance(record_id, str) or not record_id:
        raise ThreeSiteRoutingError("production record has no immutable id")
    update_url = f"{records_url}/{urllib.parse.quote(record_id, safe='')}"
    request_fn("PUT", update_url, token, build_update_payload(current, target_ip=target_ip))
    verified = find_production_record(request_fn("GET", records_url, token, None))
    if record_origin_ip(verified) != target_ip or verified.get("cloud") is not True:
        raise ThreeSiteRoutingError("Arvan route read-back did not match the requested origin/proxy state")
    result.update(status="switched", applied=True, after=public_record_summary(verified))
    return result


def _ensure_private_parent(path: Path) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    parent_stat = path.parent.stat()
    if parent_stat.st_uid != 0 or parent_stat.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise ThreeSiteRoutingError("audit directory must be root-owned and private")


def append_audit_event(path: Path, event: Mapping[str, Any]) -> None:
    _ensure_private_parent(path)
    flags = (
        os.O_RDWR
        | os.O_CREAT
        | os.O_APPEND
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        descriptor = os.open(path, flags, 0o600)
    except OSError as exc:
        raise ThreeSiteRoutingError(f"cannot securely open route audit log: {path}") from exc
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        file_stat = os.fstat(descriptor)
        if (
            not stat.S_ISREG(file_stat.st_mode)
            or file_stat.st_uid != 0
            or file_stat.st_nlink != 1
            or file_stat.st_mode & (stat.S_IRWXG | stat.S_IRWXO)
            or file_stat.st_size > MAX_AUDIT_BYTES
        ):
            raise ThreeSiteRoutingError("route audit log is not a private regular root-owned file")
        os.fchmod(descriptor, 0o600)
        os.lseek(descriptor, 0, os.SEEK_SET)
        previous_contents = os.read(descriptor, MAX_AUDIT_BYTES + 1)
        if len(previous_contents) > MAX_AUDIT_BYTES:
            raise ThreeSiteRoutingError("route audit log exceeds the size limit")
        previous_hash = "0" * 64
        lines = [line for line in previous_contents.decode("utf-8").splitlines() if line]
        if lines:
            try:
                previous = json.loads(lines[-1])
            except json.JSONDecodeError as exc:
                raise ThreeSiteRoutingError("route audit log contains invalid JSON") from exc
            if not isinstance(previous, dict) or not _SHA256.fullmatch(str(previous.get("event_hash", ""))):
                raise ThreeSiteRoutingError("route audit log hash chain is invalid")
            previous_hash = previous["event_hash"]
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "host": socket.gethostname(),
            "previous_hash": previous_hash,
            **event,
        }
        payload["event_hash"] = _sha256(_canonical_json_bytes(payload))
        os.lseek(descriptor, 0, os.SEEK_END)
        os.write(descriptor, _canonical_json_bytes(payload) + b"\n")
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Inspect or safely route coin.gold-trade.ir between WA-FI and WA-IR."
    )
    parser.add_argument("--target-site", required=True, choices=sorted(SITE_ORIGINS))
    parser.add_argument("--token-file", required=True, help="Root-only Arvan CDN API token file.")
    parser.add_argument("--expected-current-ip")
    parser.add_argument("--promotion-proof-file", help="Root-only Witness promotion proof JSON.")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument(
        "--bootstrap-proxy",
        action="store_true",
        help="One-time proxy bootstrap only; target must remain webapp_fi.",
    )
    parser.add_argument("--operator", help="Named operator for an applied route change.")
    parser.add_argument("--reason", help="Incident, drill, or cutover reason for an applied route change.")
    parser.add_argument("--audit-log", help="Root-only JSONL audit log, mandatory with --apply.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if args.apply:
            _require_root()
            if not (args.operator and args.reason and args.audit_log):
                raise ThreeSiteRoutingError("--operator, --reason, and --audit-log are mandatory with --apply")
            if args.bootstrap_proxy and args.promotion_proof_file:
                raise ThreeSiteRoutingError("proxy bootstrap does not accept a Witness promotion proof")
            if not args.bootstrap_proxy and not args.promotion_proof_file:
                raise ThreeSiteRoutingError("--promotion-proof-file is mandatory for a normal route switch")
        token = load_token(Path(args.token_file))
        proof = (
            load_promotion_proof(Path(args.promotion_proof_file))
            if args.promotion_proof_file
            else None
        )
        result = inspect_or_route(
            target_site=args.target_site,
            token=token,
            expected_current_ip=args.expected_current_ip,
            apply=args.apply,
            bootstrap_proxy=args.bootstrap_proxy,
            proof=proof,
        )
    except ThreeSiteRoutingError as exc:
        if args.apply and args.audit_log:
            try:
                append_audit_event(
                    Path(args.audit_log),
                    {
                        "event": "three_site_mvp.route.failed",
                        "operator": args.operator,
                        "reason": args.reason,
                        "target_site": args.target_site,
                        "expected_current_ip": args.expected_current_ip,
                        "bootstrap_proxy": args.bootstrap_proxy,
                        "error": str(exc),
                    },
                )
            except ThreeSiteRoutingError:
                pass
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False, sort_keys=True))
        return 1
    if args.apply:
        append_audit_event(
            Path(args.audit_log),
            {
                "event": "three_site_mvp.route.applied",
                "operator": args.operator,
                "reason": args.reason,
                "bootstrap_proxy": args.bootstrap_proxy,
                "result": result,
            },
        )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
