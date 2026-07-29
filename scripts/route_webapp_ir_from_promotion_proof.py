#!/usr/bin/env python3
"""Route public traffic to WA-IR only from a fresh, verified promotion proof.

This is the narrow bridge between the host-local Writer Witness agent and the
Arvan routing primitive.  It has no Witness credentials and it never starts a
container.  The caller is expected to run it only after the initial Arvan
proxy bootstrap has retained WA-FI as origin.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
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
    inspect_or_route,
    load_promotion_proof,
    load_token,
    verify_promotion_proof,
)


MAX_PROOF_FILES = 128
PROOF_FILENAME = re.compile(r"^promote_ir-[A-Za-z0-9][A-Za-z0-9._:-]{0,127}-[0-9a-f]{64}\.json$")


class PromotionRouteError(RuntimeError):
    """Raised when no fresh proof can safely route traffic to WA-IR."""


RequestFn = Callable[[str, str, str, Mapping[str, Any] | None], dict[str, Any]]


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
    token: str,
    apply: bool,
    request_fn: RequestFn = None,  # type: ignore[assignment]
    now: datetime | None = None,
) -> dict[str, Any]:
    selected = select_latest_fresh_promotion_proof(proof_directory, now=now)
    if selected is None:
        return {"status": "no_fresh_promotion_proof", "applied": False}
    proof, proof_summary, path = selected
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
        "route": route_result,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Route WA-IR from the latest verified Witness proof.")
    parser.add_argument("--proof-directory", required=True, type=Path)
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
