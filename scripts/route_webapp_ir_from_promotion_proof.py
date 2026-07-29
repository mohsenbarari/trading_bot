#!/usr/bin/env python3
"""Route public traffic to WA-IR only from a fresh, verified promotion proof.

This is the narrow bridge between the host-local Writer Witness agent and the
Arvan routing primitive.  It has no Witness credentials and it never starts a
container.  The caller is expected to run it only after the initial Arvan
proxy bootstrap has retained WA-FI as origin.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
from typing import Any, Callable, Mapping


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.manage_three_site_mvp_arvan_routing import (  # noqa: E402
    SITE_ORIGINS,
    ThreeSiteRoutingError,
    append_audit_event,
    _secure_read,
    inspect_or_route,
    load_promotion_proof,
    load_token,
    verify_promotion_proof,
)


MAX_PROOF_FILES = 128
PROOF_FILENAME = re.compile(r"^promote_ir-[A-Za-z0-9][A-Za-z0-9._:-]{0,127}-[0-9a-f]{64}\.json$")
LISTENER_RECEIPT_SCHEMA = "gold-trade-wa-ir-promoted-listener-activation-v1"
MAX_LISTENER_RECEIPT_AGE_SECONDS = 30
MAX_LISTENER_CLOCK_SKEW_SECONDS = 5
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
LISTENER_RECEIPT_FIELDS = frozenset(
    {
        "schema",
        "status",
        "release_sha",
        "server_name",
        "loopback_upstream",
        "site_config_sha256",
        "certificate_sha256",
        "activated_at",
    }
)


class PromotionRouteError(RuntimeError):
    """Raised when no fresh proof can safely route traffic to WA-IR."""


RequestFn = Callable[[str, str, str, Mapping[str, Any] | None], dict[str, Any]]
WallClock = Callable[[], datetime]
MonotonicClock = Callable[[], float]


def _require_root() -> None:
    if os.geteuid() != 0:
        raise PromotionRouteError("this command must run as root")


def _private_directory(path: Path) -> None:
    try:
        state = path.lstat()
    except OSError as exc:
        raise PromotionRouteError("promotion proof directory does not exist") from exc
    if (
        not stat.S_ISDIR(state.st_mode)
        or stat.S_ISLNK(state.st_mode)
        or state.st_uid != 0
        or state.st_mode & (stat.S_IRWXG | stat.S_IRWXO)
    ):
        raise PromotionRouteError("promotion proof directory is not root-owned and private")


def _strict_json_object(raw: bytes, *, label: str) -> dict[str, Any]:
    def no_duplicate_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise PromotionRouteError(f"{label} contains duplicate JSON keys")
            result[key] = value
        return result

    try:
        payload = json.loads(raw.decode("utf-8"), object_pairs_hook=no_duplicate_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PromotionRouteError(f"{label} is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise PromotionRouteError(f"{label} must be a JSON object")
    return payload


def _utc_timestamp(value: object, *, label: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise PromotionRouteError(f"{label} is missing")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise PromotionRouteError(f"{label} is invalid") from exc
    if parsed.tzinfo is None:
        raise PromotionRouteError(f"{label} must include a timezone")
    return parsed.astimezone(timezone.utc)


def verify_promoted_listener_receipt(
    path: Path,
    *,
    proof: Mapping[str, Any],
    now: datetime,
) -> dict[str, Any]:
    """Require a fresh local WA-IR listener reload after this promotion term."""

    try:
        raw = _secure_read(path, label="WA-IR promoted listener receipt", max_bytes=64 * 1024)
    except ThreeSiteRoutingError as exc:
        raise PromotionRouteError(str(exc)) from exc
    receipt = _strict_json_object(raw, label="WA-IR promoted listener receipt")
    if set(receipt) != LISTENER_RECEIPT_FIELDS:
        raise PromotionRouteError("WA-IR promoted listener receipt schema is invalid")
    if (
        receipt.get("schema") != LISTENER_RECEIPT_SCHEMA
        or receipt.get("status") != "reloaded"
        or receipt.get("release_sha") != proof.get("release_sha")
        or receipt.get("server_name") != "coin.gold-trade.ir"
        or receipt.get("loopback_upstream") != "http://127.0.0.1:18000"
    ):
        raise PromotionRouteError("WA-IR promoted listener receipt does not match the promotion runtime")
    for field in ("site_config_sha256", "certificate_sha256"):
        if not isinstance(receipt.get(field), str) or not SHA256_RE.fullmatch(receipt[field]):
            raise PromotionRouteError(f"WA-IR promoted listener receipt {field} is invalid")
    activated_at = _utc_timestamp(receipt.get("activated_at"), label="listener receipt activated_at")
    issued_at = _utc_timestamp(proof.get("issued_at"), label="promotion proof issued_at")
    if activated_at < issued_at - timedelta(seconds=MAX_LISTENER_CLOCK_SKEW_SECONDS):
        raise PromotionRouteError("WA-IR promoted listener receipt predates the promotion term")
    if activated_at > now + timedelta(seconds=MAX_LISTENER_CLOCK_SKEW_SECONDS):
        raise PromotionRouteError("WA-IR promoted listener receipt is in the future")
    if (now - activated_at).total_seconds() > MAX_LISTENER_RECEIPT_AGE_SECONDS:
        raise PromotionRouteError("WA-IR promoted listener receipt is stale")
    return {
        "receipt_sha256": hashlib.sha256(raw).hexdigest(),
        "activated_at": activated_at.isoformat(),
        "site_config_sha256": receipt["site_config_sha256"],
    }


def _proof_candidates(directory: Path) -> list[Path]:
    _private_directory(directory)
    candidates: list[Path] = []
    try:
        with os.scandir(directory) as entries:
            for entry in entries:
                if len(candidates) >= MAX_PROOF_FILES:
                    raise PromotionRouteError("promotion proof directory has too many entries")
                if not PROOF_FILENAME.fullmatch(entry.name):
                    continue
                try:
                    state = entry.stat(follow_symlinks=False)
                except OSError as exc:
                    raise PromotionRouteError("cannot inspect promotion proof") from exc
                if (
                    not stat.S_ISREG(state.st_mode)
                    or state.st_uid != 0
                    or state.st_mode & (stat.S_IRWXG | stat.S_IRWXO)
                    or state.st_nlink != 1
                ):
                    raise PromotionRouteError("promotion proof file is not root-owned and private")
                candidates.append(Path(entry.path))
    except OSError as exc:
        raise PromotionRouteError("cannot scan promotion proof directory") from exc
    return sorted(candidates, key=lambda candidate: candidate.name)


def select_latest_fresh_promotion_proof(
    directory: Path,
    *,
    now: datetime | None = None,
) -> tuple[dict[str, Any], dict[str, Any], Path] | None:
    """Return one valid fresh WA-IR proof, preferring latest issuance time."""
    reference = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    selected: tuple[datetime, str, dict[str, Any], dict[str, Any], Path] | None = None
    for candidate in _proof_candidates(directory):
        try:
            proof = load_promotion_proof(candidate)
            summary = verify_promotion_proof(proof, target_site="webapp_ir", now=reference)
            issued_at = datetime.fromisoformat(str(proof["issued_at"]).replace("Z", "+00:00")).astimezone(
                timezone.utc
            )
        except ThreeSiteRoutingError:
            continue
        item = (issued_at, candidate.name, proof, summary, candidate)
        if selected is None or item[:2] > selected[:2]:
            selected = item
    if selected is None:
        return None
    return selected[2], selected[3], selected[4]


def route_from_latest_proof(
    *,
    proof_directory: Path,
    listener_receipt: Path,
    token: str,
    apply: bool,
    request_fn: RequestFn = None,  # type: ignore[assignment]
    now: datetime | None = None,
    wall_clock: WallClock | None = None,
    monotonic_clock: MonotonicClock | None = None,
) -> dict[str, Any]:
    selected = select_latest_fresh_promotion_proof(proof_directory, now=now)
    if selected is None:
        return {"status": "no_fresh_promotion_proof", "applied": False}
    proof, proof_summary, path = selected
    reference = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    listener_summary = verify_promoted_listener_receipt(
        listener_receipt,
        proof=proof,
        now=reference,
    )
    kwargs: dict[str, Any] = {}
    if request_fn is not None:
        kwargs["request_fn"] = request_fn
    try:
        route_result = inspect_or_route(
            target_site="webapp_ir",
            token=token,
            expected_current_ip=SITE_ORIGINS["webapp_fi"],
            apply=apply,
            bootstrap_proxy=False,
            proof=proof,
            now=now,
            wall_clock=wall_clock,
            monotonic_clock=monotonic_clock,
            **kwargs,
        )
    except ThreeSiteRoutingError as exc:
        raise PromotionRouteError(str(exc)) from exc
    return {
        "status": route_result["status"],
        "applied": route_result["applied"],
        "proof_sha256": proof_summary["proof_sha256"],
        "snapshot_id": proof_summary["snapshot_id"],
        "proof_file": path.name,
        "listener": listener_summary,
        "route": route_result,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Route WA-IR from the latest verified Witness proof.")
    parser.add_argument("--proof-directory", required=True, type=Path)
    parser.add_argument("--listener-receipt", required=True, type=Path)
    parser.add_argument("--token-file", required=True, type=Path)
    parser.add_argument("--audit-log", required=True, type=Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--operator", default="three-site-mvp-promotion-watch")
    parser.add_argument("--reason", default="witness-bound-webapp-ir-promotion")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if not args.apply:
            raise PromotionRouteError("--apply is mandatory for an Arvan route change")
        _require_root()
        token = load_token(args.token_file)
        result = route_from_latest_proof(
            proof_directory=args.proof_directory,
            listener_receipt=args.listener_receipt,
            token=token,
            apply=True,
        )
        if result["status"] == "no_fresh_promotion_proof":
            raise PromotionRouteError("no fresh promotion proof is available")
        append_audit_event(
            args.audit_log,
            {
                "event": "three_site_mvp.route_from_promotion_proof.applied",
                "operator": args.operator,
                "reason": args.reason,
                "result": result,
            },
        )
    except (PromotionRouteError, ThreeSiteRoutingError) as exc:
        if args.apply:
            try:
                append_audit_event(
                    args.audit_log,
                    {
                        "event": "three_site_mvp.route_from_promotion_proof.failed",
                        "operator": args.operator,
                        "reason": args.reason,
                        "error": str(exc),
                    },
                )
            except ThreeSiteRoutingError:
                pass
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False, sort_keys=True))
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
