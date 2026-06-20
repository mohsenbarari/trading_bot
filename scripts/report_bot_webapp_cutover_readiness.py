#!/usr/bin/env python3
"""Evaluate Bot/WebApp cutover readiness from staging/synthetic snapshots.

This Step 12 gate is deliberately offline. It does not connect to production,
does not deploy, and does not mutate data. Operators provide a staging or
synthetic JSON snapshot, and the report decides whether the snapshot is safe
enough for owner review before any future production decision.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping


REPO_ROOT = Path(__file__).resolve().parents[1]
MATRIX_VERSION = "step-12-2026-06-20"
REQUIRED_ROLES = ("iran", "foreign")
NON_PRODUCTION_ENVIRONMENTS = {"staging", "synthetic", "dry_run", "local"}
NO_PRODUCTION_ACTION_NOTICE = (
    "This report must be generated from staging/synthetic evidence only. "
    "It performs no production deploy, production peer access, or production data action."
)


@dataclass(frozen=True)
class GateResult:
    gate_id: str
    title: str
    status: str
    detail: str
    requirement: str
    role: str | None = None


def _as_mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _to_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _is_true(value: Any) -> bool:
    return value is True


def _is_false(value: Any) -> bool:
    return value is False


def _gate(
    gate_id: str,
    title: str,
    passed: bool,
    detail: str,
    requirement: str,
    *,
    role: str | None = None,
    warning: bool = False,
) -> GateResult:
    if passed:
        status = "warning" if warning else "passed"
    else:
        status = "failed"
    return GateResult(
        gate_id=gate_id,
        title=title,
        status=status,
        detail=detail,
        requirement=requirement,
        role=role,
    )


def _required_counts(section: Mapping[str, Any], keys: tuple[str, ...]) -> tuple[dict[str, int], list[str]]:
    counts: dict[str, int] = {}
    missing: list[str] = []
    for key in keys:
        value = _to_int(section.get(key))
        if value is None:
            missing.append(key)
            continue
        counts[key] = value
    return counts, missing


def _zero_counts_gate(
    *,
    gate_id: str,
    title: str,
    role: str,
    section: Mapping[str, Any],
    keys: tuple[str, ...],
    requirement: str,
) -> GateResult:
    counts, missing = _required_counts(section, keys)
    non_zero = {key: value for key, value in counts.items() if value != 0}
    passed = not missing and not non_zero
    detail_parts: list[str] = []
    if missing:
        detail_parts.append(f"missing={missing}")
    if non_zero:
        detail_parts.append(f"non_zero={non_zero}")
    if not detail_parts:
        detail_parts.append("all required counts are zero")
    return _gate(gate_id, title, passed, "; ".join(detail_parts), requirement, role=role)


def template_snapshot() -> dict[str, Any]:
    backfill_report = {
        "total_offers": 0,
        "offers_with_public_id_before": 0,
        "offers_with_public_id_after": 0,
        "offers_missing_public_id": 0,
        "trades_linked_to_offers": 0,
        "old_channel_message_bindings": 0,
        "legacy_unknown_expiry_source_rows": 0,
    }
    sync_health = {
        "unsynced_change_log_count": 0,
        "redis_outbound_queue": 0,
        "redis_retry_queue": 0,
        "partial_failure_count": 0,
        "unsafe_backlog_count": 0,
    }
    publication_state = {
        "unresolved_partial_count": 0,
        "pending_count": 0,
        "retryable_failed_count": 0,
        "lagged_count": 0,
        "failed_terminal_count": 0,
    }
    offer_request_ledger = {
        "migration_non_destructive": True,
        "preserves_trade_history": True,
        "historical_request_backfill_attempted": False,
        "historical_request_backfill_explicitly_approved": False,
        "invented_missing_request_attempts": 0,
        "unknown_legacy_rows": 0,
    }
    return {
        "metadata": {
            "environment": "staging",
            "generated_at": "replace-with-staging-timestamp",
            "branch": "candidate/bot-webapp-integration",
            "commit_sha": "replace-with-staging-sha",
            "production_data_used": False,
            "production_peer_used": False,
        },
        "roles": {
            "iran": {
                "server_mode": "iran",
                "active_offer_count": 0,
                "backfill_report": dict(backfill_report),
                "sync_health": dict(sync_health),
                "publication_state": dict(publication_state),
                "offer_request_ledger": dict(offer_request_ledger),
                "runtime_guards": {
                    "telegram_blocked": True,
                    "webapp_user_surface_blocked": False,
                    "chat_user_surface_blocked": False,
                },
            },
            "foreign": {
                "server_mode": "foreign",
                "active_offer_count": 0,
                "backfill_report": dict(backfill_report),
                "sync_health": dict(sync_health),
                "publication_state": dict(publication_state),
                "offer_request_ledger": dict(offer_request_ledger),
                "runtime_guards": {
                    "telegram_blocked": False,
                    "webapp_user_surface_blocked": True,
                    "chat_user_surface_blocked": True,
                },
            },
        },
        "global": {
            "registry_coverage_complete": True,
            "sensitive_field_policy_complete": True,
            "rollback": {
                "disable_new_behavior_without_delete": True,
                "destructive_cleanup_required": False,
                "synced_data_preserved": True,
                "migrated_data_preserved": True,
                "audit_evidence_preserved": True,
            },
            "observability": {
                "ready": True,
                "logs_reviewed": True,
                "sync_health_reviewed": True,
                "alerts_configured": True,
            },
            "backups": {
                "snapshots_ready": True,
                "backup_manifest_present": True,
                "restore_smoke_passed": True,
            },
            "staging_validation": {
                "step11_matrix_passed": True,
                "manual_scenarios_passed": True,
                "owner_signoff": True,
            },
        },
    }


def evaluate_environment(payload: Mapping[str, Any]) -> list[GateResult]:
    metadata = _as_mapping(payload.get("metadata"))
    environment = str(metadata.get("environment") or "").strip().lower()
    production_data_used = metadata.get("production_data_used")
    production_peer_used = metadata.get("production_peer_used")
    gates = [
        _gate(
            "C12-ENV-01",
            "Snapshot environment is non-production",
            environment in NON_PRODUCTION_ENVIRONMENTS,
            f"environment={environment or '<missing>'}",
            "Step 12 must not use production data or production peers.",
        ),
        _gate(
            "C12-ENV-02",
            "Snapshot declares production data and peers unused",
            _is_false(production_data_used) and _is_false(production_peer_used),
            f"production_data_used={production_data_used}, production_peer_used={production_peer_used}",
            "No production deploy, production peer access, or production data action occurs from Step 12.",
        ),
    ]
    return gates


def evaluate_role(role: str, role_payload: Mapping[str, Any]) -> list[GateResult]:
    gates: list[GateResult] = []
    expected_mode = role
    server_mode = str(role_payload.get("server_mode") or "").strip().lower()
    gates.append(
        _gate(
            f"C12-{role.upper()}-MODE",
            "Role server_mode matches the expected server",
            server_mode == expected_mode,
            f"server_mode={server_mode or '<missing>'}, expected={expected_mode}",
            "Cutover evidence must prove Iran and foreign snapshots are not swapped.",
            role=role,
        )
    )

    active_offer_count = _to_int(role_payload.get("active_offer_count"))
    gates.append(
        _gate(
            f"C12-{role.upper()}-ACTIVE",
            "Active offers are zero",
            active_offer_count == 0,
            f"active_offer_count={active_offer_count if active_offer_count is not None else '<missing>'}",
            "Active offers count on both servers is zero before cutover.",
            role=role,
        )
    )

    backfill = _as_mapping(role_payload.get("backfill_report"))
    backfill_counts, backfill_missing = _required_counts(
        backfill,
        (
            "total_offers",
            "offers_with_public_id_before",
            "offers_with_public_id_after",
            "offers_missing_public_id",
            "trades_linked_to_offers",
            "old_channel_message_bindings",
            "legacy_unknown_expiry_source_rows",
        ),
    )
    public_id_passed = False
    public_id_detail = f"missing={backfill_missing}"
    if not backfill_missing:
        total = backfill_counts["total_offers"]
        before = backfill_counts["offers_with_public_id_before"]
        after = backfill_counts["offers_with_public_id_after"]
        missing_public = backfill_counts["offers_missing_public_id"]
        public_id_passed = missing_public == 0 and after == total and after >= before
        public_id_detail = (
            f"total={total}, before={before}, after={after}, "
            f"missing_public_id={missing_public}"
        )
    gates.append(
        _gate(
            f"C12-{role.upper()}-PUBLIC-ID",
            "Offer public identity backfill is complete",
            public_id_passed,
            public_id_detail,
            "Existing offers must be backfilled or verified to have public identifiers before link/callback flows are enabled.",
            role=role,
        )
    )

    gates.append(
        _gate(
            f"C12-{role.upper()}-BACKFILL-REPORT",
            "Backfill report includes the required migration counters",
            not backfill_missing,
            "required counters present" if not backfill_missing else f"missing={backfill_missing}",
            "Backfill report must include offer totals, public identifiers before/after, missing public IDs, linked trades, old channel bindings, and legacy_unknown rows.",
            role=role,
        )
    )

    if not backfill_missing:
        legacy_unknown = backfill_counts["legacy_unknown_expiry_source_rows"]
        old_bindings = backfill_counts["old_channel_message_bindings"]
        if legacy_unknown > 0:
            gates.append(
                _gate(
                    f"C12-{role.upper()}-LEGACY-UNKNOWN",
                    "Legacy unknown metadata is reported, not fabricated",
                    True,
                    f"legacy_unknown_expiry_source_rows={legacy_unknown}",
                    "Rows with unknown legacy expiry/source metadata may be reported but must not be invented as precise history.",
                    role=role,
                    warning=True,
                )
            )
        if old_bindings > 0:
            gates.append(
                _gate(
                    f"C12-{role.upper()}-OLD-CHANNEL-BINDINGS",
                    "Old Telegram channel bindings are reported for review",
                    True,
                    f"old_channel_message_bindings={old_bindings}",
                    "Existing channel_message_id bindings must be visible in the backfill report.",
                    role=role,
                    warning=True,
                )
            )

    ledger = _as_mapping(role_payload.get("offer_request_ledger"))
    historical_attempted = ledger.get("historical_request_backfill_attempted")
    historical_approved = ledger.get("historical_request_backfill_explicitly_approved")
    invented_attempts = _to_int(ledger.get("invented_missing_request_attempts"))
    ledger_passed = (
        _is_true(ledger.get("migration_non_destructive"))
        and _is_true(ledger.get("preserves_trade_history"))
        and (_is_false(historical_attempted) or _is_true(historical_approved))
        and invented_attempts == 0
    )
    gates.append(
        _gate(
            f"C12-{role.upper()}-LEDGER",
            "Offer request ledger migration is conservative",
            ledger_passed,
            (
                f"migration_non_destructive={ledger.get('migration_non_destructive')}, "
                f"preserves_trade_history={ledger.get('preserves_trade_history')}, "
                f"historical_backfill_attempted={historical_attempted}, "
                f"historical_backfill_approved={historical_approved}, "
                f"invented_missing_request_attempts={invented_attempts if invented_attempts is not None else '<missing>'}"
            ),
            "Ledger migration must be non-destructive and must not fabricate historical request attempts.",
            role=role,
        )
    )
    if historical_attempted and historical_approved:
        gates.append(
            _gate(
                f"C12-{role.upper()}-LEDGER-APPROVED-BACKFILL",
                "Approved historical request backfill is visible as accepted risk",
                True,
                "historical request backfill was attempted with explicit approval",
                "Any historical request ledger backfill must be explicitly approved and visible in the report.",
                role=role,
                warning=True,
            )
        )
    unknown_legacy_rows = _to_int(ledger.get("unknown_legacy_rows"))
    if unknown_legacy_rows and unknown_legacy_rows > 0:
        gates.append(
            _gate(
                f"C12-{role.upper()}-LEDGER-UNKNOWN-LEGACY",
                "Unknown legacy request rows remain explicitly unknown",
                True,
                f"unknown_legacy_rows={unknown_legacy_rows}",
                "Missing request attempts must remain unknown_legacy rather than being invented.",
                role=role,
                warning=True,
            )
        )

    gates.append(
        _zero_counts_gate(
            gate_id=f"C12-{role.upper()}-SYNC",
            title="Sync backlog is safe",
            role=role,
            section=_as_mapping(role_payload.get("sync_health")),
            keys=(
                "unsynced_change_log_count",
                "redis_outbound_queue",
                "redis_retry_queue",
                "partial_failure_count",
                "unsafe_backlog_count",
            ),
            requirement="Backlog and partial sync state must be reconciled to a known safe state.",
        )
    )
    gates.append(
        _zero_counts_gate(
            gate_id=f"C12-{role.upper()}-PUBLICATION",
            title="No unresolved partial publication state blocks cutover",
            role=role,
            section=_as_mapping(role_payload.get("publication_state")),
            keys=(
                "unresolved_partial_count",
                "pending_count",
                "retryable_failed_count",
                "lagged_count",
                "failed_terminal_count",
            ),
            requirement="No unresolved partial publication state may block cutover.",
        )
    )

    guards = _as_mapping(role_payload.get("runtime_guards"))
    if role == "iran":
        gates.append(
            _gate(
                "C12-IRAN-TELEGRAM-GUARD",
                "Iran runtime guard blocks Telegram",
                _is_true(guards.get("telegram_blocked")),
                f"telegram_blocked={guards.get('telegram_blocked')}",
                "Runtime guards must prove Iran cannot call Telegram.",
                role=role,
            )
        )
    if role == "foreign":
        gates.append(
            _gate(
                "C12-FOREIGN-WEBAPP-GUARD",
                "Foreign runtime guard blocks WebApp and chat user surfaces",
                _is_true(guards.get("webapp_user_surface_blocked"))
                and _is_true(guards.get("chat_user_surface_blocked")),
                (
                    f"webapp_user_surface_blocked={guards.get('webapp_user_surface_blocked')}, "
                    f"chat_user_surface_blocked={guards.get('chat_user_surface_blocked')}"
                ),
                "Runtime guards must prove foreign cannot serve WebApp/chat user surfaces.",
                role=role,
            )
        )
    return gates


def evaluate_global(payload: Mapping[str, Any]) -> list[GateResult]:
    global_section = _as_mapping(payload.get("global"))
    rollback = _as_mapping(global_section.get("rollback"))
    observability = _as_mapping(global_section.get("observability"))
    backups = _as_mapping(global_section.get("backups"))
    staging = _as_mapping(global_section.get("staging_validation"))
    gates = [
        _gate(
            "C12-GLOBAL-REGISTRY",
            "Sync registry coverage is complete",
            _is_true(global_section.get("registry_coverage_complete")),
            f"registry_coverage_complete={global_section.get('registry_coverage_complete')}",
            "Registry coverage must be complete before cutover.",
        ),
        _gate(
            "C12-GLOBAL-SENSITIVE-POLICY",
            "Sensitive-field policy is complete",
            _is_true(global_section.get("sensitive_field_policy_complete")),
            f"sensitive_field_policy_complete={global_section.get('sensitive_field_policy_complete')}",
            "Sensitive-field policy must be complete before cutover.",
        ),
        _gate(
            "C12-GLOBAL-ROLLBACK",
            "Rollback disables new behavior without deleting migrated data",
            _is_true(rollback.get("disable_new_behavior_without_delete"))
            and _is_false(rollback.get("destructive_cleanup_required"))
            and _is_true(rollback.get("synced_data_preserved"))
            and _is_true(rollback.get("migrated_data_preserved"))
            and _is_true(rollback.get("audit_evidence_preserved")),
            (
                f"disable_new_behavior_without_delete={rollback.get('disable_new_behavior_without_delete')}, "
                f"destructive_cleanup_required={rollback.get('destructive_cleanup_required')}, "
                f"synced_data_preserved={rollback.get('synced_data_preserved')}, "
                f"migrated_data_preserved={rollback.get('migrated_data_preserved')}, "
                f"audit_evidence_preserved={rollback.get('audit_evidence_preserved')}"
            ),
            "Rollback must fail closed or disable new behavior without deleting synced or migrated data.",
        ),
        _gate(
            "C12-GLOBAL-OBSERVABILITY",
            "Observability review is ready",
            _is_true(observability.get("ready"))
            and _is_true(observability.get("logs_reviewed"))
            and _is_true(observability.get("sync_health_reviewed"))
            and _is_true(observability.get("alerts_configured")),
            (
                f"ready={observability.get('ready')}, logs_reviewed={observability.get('logs_reviewed')}, "
                f"sync_health_reviewed={observability.get('sync_health_reviewed')}, "
                f"alerts_configured={observability.get('alerts_configured')}"
            ),
            "Observability must be ready before owner production review.",
        ),
        _gate(
            "C12-GLOBAL-BACKUPS",
            "Backups and snapshots are ready",
            _is_true(backups.get("snapshots_ready"))
            and _is_true(backups.get("backup_manifest_present"))
            and _is_true(backups.get("restore_smoke_passed")),
            (
                f"snapshots_ready={backups.get('snapshots_ready')}, "
                f"backup_manifest_present={backups.get('backup_manifest_present')}, "
                f"restore_smoke_passed={backups.get('restore_smoke_passed')}"
            ),
            "Backups/snapshots and restore confidence must be ready before owner production review.",
        ),
        _gate(
            "C12-GLOBAL-STAGING-SIGNOFF",
            "Owner staging validation is complete before production consideration",
            _is_true(staging.get("step11_matrix_passed"))
            and _is_true(staging.get("manual_scenarios_passed"))
            and _is_true(staging.get("owner_signoff")),
            (
                f"step11_matrix_passed={staging.get('step11_matrix_passed')}, "
                f"manual_scenarios_passed={staging.get('manual_scenarios_passed')}, "
                f"owner_signoff={staging.get('owner_signoff')}"
            ),
            "The owner must test staging and sign off before any production decision.",
        ),
    ]
    return gates


def evaluate_snapshot(payload: Mapping[str, Any]) -> dict[str, Any]:
    gates: list[GateResult] = []
    gates.extend(evaluate_environment(payload))

    roles = _as_mapping(payload.get("roles"))
    for role in REQUIRED_ROLES:
        role_payload = _as_mapping(roles.get(role))
        if not role_payload:
            gates.append(
                _gate(
                    f"C12-{role.upper()}-PRESENT",
                    "Required server snapshot is present",
                    False,
                    f"missing role snapshot: {role}",
                    "Both Iran and foreign snapshots are required.",
                    role=role,
                )
            )
            continue
        gates.append(
            _gate(
                f"C12-{role.upper()}-PRESENT",
                "Required server snapshot is present",
                True,
                f"{role} snapshot present",
                "Both Iran and foreign snapshots are required.",
                role=role,
            )
        )
        gates.extend(evaluate_role(role, role_payload))

    gates.extend(evaluate_global(payload))

    failures = [gate for gate in gates if gate.status == "failed"]
    warnings = [gate for gate in gates if gate.status == "warning"]
    role_summaries: dict[str, Any] = {}
    for role in REQUIRED_ROLES:
        role_payload = _as_mapping(roles.get(role))
        backfill = _as_mapping(role_payload.get("backfill_report"))
        sync_health = _as_mapping(role_payload.get("sync_health"))
        publication = _as_mapping(role_payload.get("publication_state"))
        role_summaries[role] = {
            "active_offer_count": _to_int(role_payload.get("active_offer_count")),
            "total_offers": _to_int(backfill.get("total_offers")),
            "offers_missing_public_id": _to_int(backfill.get("offers_missing_public_id")),
            "unsynced_change_log_count": _to_int(sync_health.get("unsynced_change_log_count")),
            "unsafe_backlog_count": _to_int(sync_health.get("unsafe_backlog_count")),
            "unresolved_publication_count": sum(
                value or 0
                for value in (
                    _to_int(publication.get("unresolved_partial_count")),
                    _to_int(publication.get("pending_count")),
                    _to_int(publication.get("retryable_failed_count")),
                    _to_int(publication.get("lagged_count")),
                    _to_int(publication.get("failed_terminal_count")),
                )
            ),
        }

    return {
        "version": MATRIX_VERSION,
        "notice": NO_PRODUCTION_ACTION_NOTICE,
        "status": "passed" if not failures else "failed",
        "ready_for_owner_production_review": not failures,
        "failure_count": len(failures),
        "warning_count": len(warnings),
        "role_summaries": role_summaries,
        "gates": [asdict(gate) for gate in gates],
        "failures": [asdict(gate) for gate in failures],
        "warnings": [asdict(gate) for gate in warnings],
    }


def load_snapshot(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("snapshot JSON must be an object")
    return payload


def build_markdown_report(result: Mapping[str, Any]) -> str:
    lines = [
        "# Bot/WebApp Cutover Readiness Report",
        "",
        f"- Version: `{result.get('version')}`",
        f"- Status: `{result.get('status')}`",
        f"- Ready for owner production review: `{result.get('ready_for_owner_production_review')}`",
        f"- Failure count: `{result.get('failure_count')}`",
        f"- Warning count: `{result.get('warning_count')}`",
        f"- No Production Action Notice: {result.get('notice')}",
        "",
        "## Role Summary",
        "",
        "| Role | Active Offers | Total Offers | Missing Public IDs | Unsynced Change Log | Unsafe Backlog | Unresolved Publication |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for role, summary in sorted(_as_mapping(result.get("role_summaries")).items()):
        lines.append(
            f"| `{role}` | `{summary.get('active_offer_count')}` | `{summary.get('total_offers')}` | "
            f"`{summary.get('offers_missing_public_id')}` | `{summary.get('unsynced_change_log_count')}` | "
            f"`{summary.get('unsafe_backlog_count')}` | `{summary.get('unresolved_publication_count')}` |"
        )

    lines.extend(["", "## Gates", ""])
    for gate in result.get("gates") or []:
        role = f" `{gate['role']}`" if gate.get("role") else ""
        lines.append(f"- `{gate['status']}` `{gate['gate_id']}`{role}: {gate['title']} - {gate['detail']}")
    return "\n".join(lines) + "\n"


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate Bot/WebApp cutover readiness from a staging/synthetic snapshot.")
    parser.add_argument("--input", type=Path, help="Path to a staging/synthetic readiness snapshot JSON.")
    parser.add_argument("--template", action="store_true", help="Print a passing snapshot template.")
    parser.add_argument("--json", action="store_true", help="Emit JSON.")
    parser.add_argument("--markdown", action="store_true", help="Emit markdown.")
    parser.add_argument("--check", action="store_true", help="Exit non-zero when readiness fails.")
    parser.add_argument("--report-out", type=Path, help="Optional markdown report output path.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])

    if args.template:
        print(json.dumps(template_snapshot(), ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    if args.input is None:
        print("--input is required unless --template is used", file=sys.stderr)
        return 2

    try:
        payload = load_snapshot(args.input)
    except Exception as exc:
        print(f"failed to read snapshot: {exc}", file=sys.stderr)
        return 2

    result = evaluate_snapshot(payload)
    if args.report_out:
        args.report_out.write_text(build_markdown_report(result), encoding="utf-8")

    if args.markdown:
        print(build_markdown_report(result), end="")
    elif args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print("Bot/WebApp Cutover Readiness")
        print(f"- Status: {result['status']}")
        print(f"- Ready for owner production review: {result['ready_for_owner_production_review']}")
        print(f"- Failures: {result['failure_count']}")
        print(f"- Warnings: {result['warning_count']}")
        print(f"- Notice: {result['notice']}")

    return 1 if args.check and result["status"] != "passed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
