#!/usr/bin/env python3
"""Build the real two-server staging full-matrix manifest.

This manifest is side-effect free. It reuses the broad production full-matrix
catalog, converts it to a no-pressure staging profile, and adds explicit
branch-change regression scenarios for `candidate/sync-parity-hardening`.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts import build_production_full_matrix_manifest as production_manifest
from scripts.plan_production_full_matrix import default_prefix, validate_prefix


SCHEMA_VERSION = "staging_two_server_full_matrix_manifest_v1"
DESIGN_DOC_PATH = "docs/STAGING_TWO_SERVER_FULL_MATRIX_DESIGN.md"
EXPECTED_BRANCH = "candidate/sync-parity-hardening"
DEFAULT_STRESS_MAX_PARALLEL = 12
DEFAULT_MARKET_ATTEMPTS = 3

BRANCH_CHANGE_REQUIREMENTS: dict[str, list[str]] = {
    "sync_receiver_worker_parity": [
        "normal apply Iran->foreign and foreign->Iran",
        "duplicate and stale apply do not mutate twice",
        "terminal policy rejection does not block later queue items",
        "bad signature/timestamp/source authority rejects without partial mutation",
        "watermark advance and aggregate identity collision guards",
        "parity clean/drift/incomplete/local-only/critical states",
        "repair dry-run, manifest apply guard, row-count mismatch guard",
        "real worker-to-remote-receiver HTTPS/TLS/routing probe",
    ],
    "market_schedule_runtime_notices_foreign_autonomy": [
        "fresh open notice sends once",
        "fresh close notice sends once",
        "stale open/close notice is suppressed",
        "failed notice retry is idempotent",
        "foreign never writes authoritative market_runtime_state",
        "synced Iran close expires foreign-home offers exactly once",
        "foreign local side effects run after short-outage grace",
        "reconnect/replay does not duplicate notice or expiry",
    ],
    "offer_publication_public_id_commodity": [
        "WebApp offer public id survives foreign sync",
        "Telegram offer public id survives Iran sync",
        "publication state is idempotent after duplicate sync",
        "public-id drift report detects missing or drifted rows",
        "commodity alias is accepted only at input time",
        "canonical commodity display is used everywhere after creation",
        "invalid commodity input rejects without creating an offer",
        "offer warning and metadata sync fields are preserved",
    ],
    "trade_forwarding_authority_routing": [
        "Iran-home WebApp request mutates locally on Iran",
        "Iran-home Telegram request forwards to Iran",
        "foreign-home WebApp request forwards to foreign",
        "foreign-home Telegram request mutates locally on foreign",
        "remote authority unavailable fails without duplicate risk",
        "remote success creates one trade and one terminal request state",
        "forwarding preserves actor, platform, idempotency, and customer owner route",
        "ambiguous remote retry cannot create a second trade",
    ],
    "notification_durability_sync_side_effects": [
        "notification rows commit before delivery workers process",
        "committed change_log outbox is sync source of truth",
        "WebApp recipients cover owner/customer/linked/unlinked/accountant cases",
        "Telegram recipients cover linked, unlinked not_required, broken id, and recovery",
        "duplicate repair does not duplicate visible messages",
        "delivery failure does not roll back the trade",
        "stale opposite-server delivery follows outage suppression policy",
    ],
    "auth_session_security_deployment_surface": [
        "dev login is staging-only",
        "OTP wrong-code throttle locks at threshold",
        "trusted-proxy attribution is used for OTP per-IP keys",
        "empty environment does not allow localhost CORS",
        "chat file authorization checks direct/group/channel membership",
        "unauthorized chat file download does not leak file existence",
        "deployment surface guard rejects production identities before mutation",
        "retired peer URL, mismatched domain, production DB/Redis/bot/channel stop the runner",
        "production data hygiene guard remains read-only",
    ],
    "staging_topology_runtime_config": [
        "Iran staging reports Iran role and candidate release metadata",
        "foreign staging reports foreign role and candidate release metadata",
        "Iran staging has no Telegram token or channel id",
        "foreign staging does not serve WebApp/frontend",
        "both sync workers expose matching release metadata",
        "staging trusted proxy CIDRs are applied",
        "real staging peer URL/TLS works both directions",
        "single-host shared-DB staging is rejected as final evidence",
    ],
}

SECTION_BRANCH_AREAS: dict[str, list[str]] = {
    "market_behavior": ["offer_publication_public_id_commodity", "trade_forwarding_authority_routing"],
    "delivery_contract": ["notification_durability_sync_side_effects"],
    "targeted_trade_delivery_join": ["notification_durability_sync_side_effects", "trade_forwarding_authority_routing"],
    "production_base_trade_shape": ["trade_forwarding_authority_routing", "notification_durability_sync_side_effects"],
    "production_stress_overlay": ["trade_forwarding_authority_routing"],
    "negative_business_guard": ["auth_session_security_deployment_surface"],
}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def controlled_prefix(prefix: str | None) -> str:
    raw = prefix or default_prefix().replace("PFM_", "FMX_STAGE_")
    if not raw.startswith(("PFM_", "PRODTEST_", "FMX_")):
        raw = f"FMX_STAGE_{raw}"
    return validate_prefix(raw)


def copy_record(section: str, record: dict[str, Any], *, stress_max_parallel: int, market_attempts: int) -> dict[str, Any]:
    item = dict(record)
    item["section"] = section
    item["environment"] = "staging_two_server"
    item["mutates_production"] = False
    item["requires_real_two_server_staging"] = True
    item["branch_change_areas"] = list(SECTION_BRANCH_AREAS.get(section, []))
    item["branch_change_area"] = item["branch_change_areas"][0] if item["branch_change_areas"] else None
    item["coverage_tags"] = coverage_tags_for(item)
    if section == "production_stress_overlay":
        original_parallel = int(item.get("min_parallel_requests") or 0)
        item["original_min_parallel_requests"] = original_parallel
        item["min_parallel_requests"] = min(max(2, original_parallel), stress_max_parallel)
        item["original_target_rps_floor"] = item.get("target_rps_floor")
        item["target_rps_floor"] = 0
        item["pressure_profile"] = "controlled_no_pressure"
    if section == "market_behavior":
        item["original_min_attempts"] = item.get("min_attempts")
        item["min_attempts"] = max(1, int(market_attempts))
        item["pressure_profile"] = "controlled_no_pressure"
    return item


def coverage_tags_for(item: dict[str, Any]) -> list[str]:
    tags = {str(item.get("kind") or "unknown")}
    for key in (
        "section",
        "quadrant",
        "offer_surface",
        "request_surface",
        "offer_home_server",
        "request_source_server",
        "offer_type",
        "shape",
        "outage_id",
        "actor_pair_id",
        "source_kind",
        "responder_kind",
        "group_relation",
        "family",
        "case_id",
    ):
        value = item.get(key)
        if value is not None and value != "":
            tags.add(f"{key}:{value}")
    if item.get("policy_supported") is False:
        tags.add("policy:unsupported_negative")
    elif item.get("policy_supported") is True:
        tags.add("policy:supported")
    return sorted(tags)


def branch_change_regression_payloads() -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    counter = 1
    for area, requirements in BRANCH_CHANGE_REQUIREMENTS.items():
        for index, requirement in enumerate(requirements, start=1):
            payloads.append(
                {
                    "manifest_id": f"BCR-{counter:03d}",
                    "kind": "branch_change_regression",
                    "section": "branch_change_regression",
                    "environment": "staging_two_server",
                    "branch_change_area": area,
                    "branch_change_areas": [area],
                    "requirement_id": f"{area}:{index:02d}",
                    "required_evidence": requirement,
                    "required": True,
                    "requires_real_two_server_staging": True,
                    "mutates_production": False,
                    "coverage_tags": [
                        "branch_change_regression",
                        f"branch_change_area:{area}",
                        f"requirement:{index:02d}",
                    ],
                    "assertion_refs": [
                        "cleanup_prefix_scope",
                        "cross_server_sync_terminal_state",
                    ],
                }
            )
            counter += 1
    return payloads


def summarize_sections(sections: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    base = production_manifest.summarize_sections(
        {
            key: value
            for key, value in sections.items()
            if key
            in {
                "market_behavior",
                "delivery_contract",
                "targeted_trade_delivery_join",
                "production_base_trade_shape",
                "production_stress_overlay",
                "negative_business_guard",
            }
        }
    )
    branch_counter: Counter[str] = Counter()
    for records in sections.values():
        for item in records:
            for area in item.get("branch_change_areas") or []:
                branch_counter[str(area)] += 1
    base.update(
        {
            "branch_change_regression_scenarios": len(sections["branch_change_regression"]),
            "branch_change_area_counts": dict(sorted(branch_counter.items())),
            "total_manifest_scenarios": sum(len(items) for items in sections.values()),
            "controlled_no_pressure": True,
            "requires_real_two_server_staging": True,
        }
    )
    return base


def build_manifest(
    *,
    prefix: str | None = None,
    stress_max_parallel: int = DEFAULT_STRESS_MAX_PARALLEL,
    market_attempts: int = DEFAULT_MARKET_ATTEMPTS,
) -> dict[str, Any]:
    production = production_manifest.build_manifest(prefix=controlled_prefix(prefix))
    sections: dict[str, list[dict[str, Any]]] = {}
    for section, records in production["sections"].items():
        sections[section] = [
            copy_record(section, record, stress_max_parallel=stress_max_parallel, market_attempts=market_attempts)
            for record in records
        ]
    sections["branch_change_regression"] = branch_change_regression_payloads()
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": utc_now_iso(),
        "prefix": production["prefix"],
        "environment": "staging_two_server",
        "expected_branch": EXPECTED_BRANCH,
        "mutates_production": False,
        "catalog_docs": {
            **production.get("catalog_docs", {}),
            "staging_two_server_design": DESIGN_DOC_PATH,
        },
        "runner_contract": {
            "requires_isolation": True,
            "requires_exact_prefix_cleanup": True,
            "requires_real_two_server_staging": True,
            "requires_separate_staging_databases": True,
            "forbids_single_host_shared_db_final_evidence": True,
            "high_volume_telegram_transport": "aiogram_dispatcher_or_fake_bot_api",
            "manual_real_telegram_slice_required": True,
            "controlled_no_pressure": True,
            "target_users": "role-fixture-minimum",
            "target_rps_floor": 0,
            "stress_max_parallel": stress_max_parallel,
            "market_attempts_per_scenario": market_attempts,
            "agent_log_roots": [
                "tmp/claude/full_matrix_logs/<run-id>/",
                "tmp/chatgpt/full_matrix_logs/<run-id>/",
            ],
        },
        "axes": production["axes"],
        "assertions": production["assertions"],
        "branch_change_requirements": BRANCH_CHANGE_REQUIREMENTS,
        "summary": summarize_sections(sections),
        "sections": sections,
        "release_gate": {
            "status": "manifest_only",
            "reason": "This manifest is side-effect-free. Execution requires real two-server staging preflight.",
        },
    }


def validate_manifest(manifest: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if manifest.get("environment") != "staging_two_server":
        errors.append("environment must be staging_two_server")
    if manifest.get("mutates_production") is not False:
        errors.append("manifest must not mutate production")

    production_like = production_manifest.build_manifest(prefix=manifest.get("prefix"))
    production_errors = production_manifest.validate_manifest(production_like)
    errors.extend(f"production catalog baseline: {error}" for error in production_errors)

    summary = manifest.get("summary") or {}
    for area in BRANCH_CHANGE_REQUIREMENTS:
        if int((summary.get("branch_change_area_counts") or {}).get(area) or 0) <= 0:
            errors.append(f"missing branch-change coverage area: {area}")

    stress_records = manifest["sections"]["production_stress_overlay"]
    too_hot = [item["manifest_id"] for item in stress_records if int(item.get("min_parallel_requests") or 0) > DEFAULT_STRESS_MAX_PARALLEL]
    if too_hot:
        errors.append(f"controlled staging stress contains too-high parallel requests: {too_hot[:5]}")
    rps_records = [item["manifest_id"] for item in stress_records if float(item.get("target_rps_floor") or 0) > 0]
    if rps_records:
        errors.append(f"controlled staging stress contains target RPS floors: {rps_records[:5]}")

    encoded = json.dumps(manifest, ensure_ascii=False)
    forbidden_tokens = ("BOT_TOKEN", "895973", "AAH2", "coin.gold-trade.ir/api", "coin.362514.ir/api")
    for token in forbidden_tokens:
        if token in encoded:
            errors.append(f"manifest appears to contain forbidden token/material: {token}")
    return errors


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prefix", default=None)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--stress-max-parallel", type=int, default=DEFAULT_STRESS_MAX_PARALLEL)
    parser.add_argument("--market-attempts", type=int, default=DEFAULT_MARKET_ATTEMPTS)
    parser.add_argument("--print-full", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    manifest = build_manifest(
        prefix=args.prefix,
        stress_max_parallel=args.stress_max_parallel,
        market_attempts=args.market_attempts,
    )
    errors = validate_manifest(manifest)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    stdout_payload = manifest if args.print_full else {
        "schema_version": manifest["schema_version"],
        "status": "ok" if not errors else "error",
        "prefix": manifest["prefix"],
        "output": str(args.output) if args.output else None,
        "summary": manifest["summary"],
    }
    print(json.dumps(stdout_payload, ensure_ascii=False, sort_keys=True))
    if args.check and errors:
        for error in errors:
            print(f"manifest check failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
