#!/usr/bin/env python3
"""Read-only production data hygiene guard.

The guard intentionally reports suspicious dev/test artifacts without deleting
anything. It is designed to run inside the app container where DATABASE_URL is
already configured.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import text

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

DEFAULT_SCAN_LIMIT = 100_000
DEFAULT_MAX_FINDINGS = 200

SUSPICIOUS_EXACT_MOBILES = {
    "09999999999",  # historical dev-login superadmin mobile
}

TEXT_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "synthetic_exact_name",
        re.compile(r"^(dev|test|stage|staging|fixture|smoke|loadtest|load_test)$", re.IGNORECASE),
    ),
    (
        "synthetic_name_prefix",
        re.compile(r"^(dev|test|stage|staging|fixture|smoke|load|tmp|bench)[_.:-].+", re.IGNORECASE),
    ),
    (
        "synthetic_probe_prefix",
        re.compile(r"^(tg\d+|th\d+|p\d+|pfm|rpl|clm|vf\d+|rpo\d+|l\d+)[_.:-].+", re.IGNORECASE),
    ),
    (
        "synthetic_pw_prefix",
        re.compile(r"^pw[a-z0-9_.:-]{2,}$", re.IGNORECASE),
    ),
    (
        "synthetic_marker",
        re.compile(
            r"(p7_mixed_smoke|tg9_stage|staging_user|load_fixture|production_load|smoke_test|benchmark)",
            re.IGNORECASE,
        ),
    ),
)

TABLE_SCAN_FIELDS: dict[str, tuple[str, ...]] = {
    "users": ("id", "account_name", "mobile_number", "username", "full_name", "role", "is_deleted", "created_at"),
    "invitations": (
        "id",
        "account_name",
        "mobile_number",
        "role",
        "created_by_id",
        "is_used",
        "expires_at",
        "created_at",
    ),
    "accountant_relations": (
        "id",
        "owner_user_id",
        "accountant_user_id",
        "created_by_user_id",
        "global_account_name",
        "relation_display_name",
        "mobile_number",
        "status",
        "deleted_at",
        "created_at",
    ),
    "customer_relations": (
        "id",
        "owner_user_id",
        "customer_user_id",
        "created_by_user_id",
        "management_name",
        "status",
        "deleted_at",
        "created_at",
    ),
}

TEXT_FIELDS_BY_TABLE: dict[str, tuple[str, ...]] = {
    "users": ("account_name", "username", "full_name", "mobile_number"),
    "invitations": ("account_name", "mobile_number"),
    "accountant_relations": ("global_account_name", "relation_display_name", "mobile_number"),
    "customer_relations": ("management_name",),
}

RELATED_USER_COLUMNS: dict[str, tuple[str, ...]] = {
    "user_sessions": ("user_id",),
    "session_login_requests": ("user_id",),
    "single_session_recovery_requests": ("user_id", "decided_by_user_id"),
    "offers": ("user_id", "actor_user_id", "expired_by_user_id", "expired_by_actor_user_id"),
    "trades": ("offer_user_id", "responder_user_id", "actor_user_id"),
    "notifications": ("user_id",),
    "push_subscriptions": ("user_id",),
    "user_blocks": ("blocker_id", "blocked_id"),
    "customer_relations": ("owner_user_id", "customer_user_id", "created_by_user_id"),
    "accountant_relations": ("owner_user_id", "accountant_user_id", "created_by_user_id"),
    "invitations": ("created_by_id",),
}

STATUS_ORDER = {"ok": 0, "warning": 1, "high": 2, "critical": 3}


def normalize_text(value: Any) -> str:
    return str(value or "").strip().lower()


def normalize_mobile(value: Any) -> str:
    raw = str(value or "").strip().lower()
    raw = raw.split("_del_", 1)[0]
    return re.sub(r"\D+", "", raw)


def redacted_mobile(value: Any) -> str | None:
    digits = normalize_mobile(value)
    if not digits:
        return None
    if len(digits) <= 6:
        return "***"
    suffix = "_del" if "_del_" in str(value or "").lower() else ""
    return f"{digits[:3]}*****{digits[-4:]}{suffix}"


def digest_value(value: Any) -> str | None:
    text_value = str(value or "").strip()
    if not text_value:
        return None
    return hashlib.sha256(text_value.encode("utf-8")).hexdigest()[:16]


def reasons_for_field(field: str, value: Any) -> list[str]:
    reasons: list[str] = []
    value_text = normalize_text(value)
    if not value_text:
        return reasons

    if field == "mobile_number":
        mobile = normalize_mobile(value)
        if mobile in SUSPICIOUS_EXACT_MOBILES:
            reasons.append(f"{field}:exact_dev_mobile")
        return reasons

    for name, pattern in TEXT_RULES:
        if pattern.search(value_text):
            reasons.append(f"{field}:{name}")
    return reasons


def reasons_for_record(table: str, record: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    for field in TEXT_FIELDS_BY_TABLE.get(table, ()):
        reasons.extend(reasons_for_field(field, record.get(field)))
    return reasons


def is_past(value: Any, now: datetime | None = None) -> bool:
    if value is None:
        return False
    if not isinstance(value, datetime):
        return False
    now = now or datetime.now(timezone.utc)
    candidate = value
    if candidate.tzinfo is None:
        candidate = candidate.replace(tzinfo=timezone.utc)
    return candidate < now


def severity_for_record(table: str, record: dict[str, Any], reasons: list[str]) -> str:
    if table == "users":
        deleted = bool(record.get("is_deleted"))
        role = str(record.get("role") or "")
        if any("exact_dev_mobile" in reason for reason in reasons) and not deleted and "SUPER_ADMIN" in role:
            return "critical"
        if any("exact_dev_mobile" in reason for reason in reasons) and not deleted:
            return "critical"
        return "warning" if deleted else "high"

    if table == "invitations":
        active_invite = not bool(record.get("is_used")) and not is_past(record.get("expires_at"))
        return "high" if active_invite else "warning"

    if table in {"accountant_relations", "customer_relations"}:
        status = str(record.get("status") or "").lower()
        deleted = record.get("deleted_at") is not None or status in {"deleted", "revoked", "expired"}
        return "warning" if deleted else "high"

    return "warning"


def safe_record_payload(table: str, record: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {"id": record.get("id")}
    if table == "users":
        payload.update(
            {
                "account_name": str(record.get("account_name") or "")[:80],
                "username": str(record.get("username") or "")[:80] or None,
                "full_name": str(record.get("full_name") or "")[:80],
                "mobile": redacted_mobile(record.get("mobile_number")),
                "mobile_digest": digest_value(record.get("mobile_number")),
                "role": str(record.get("role") or ""),
                "is_deleted": bool(record.get("is_deleted")),
            }
        )
    elif table == "invitations":
        payload.update(
            {
                "account_name": str(record.get("account_name") or "")[:80],
                "mobile": redacted_mobile(record.get("mobile_number")),
                "mobile_digest": digest_value(record.get("mobile_number")),
                "role": str(record.get("role") or ""),
                "created_by_id": record.get("created_by_id"),
                "is_used": bool(record.get("is_used")),
                "expired": is_past(record.get("expires_at")),
            }
        )
    elif table == "accountant_relations":
        payload.update(
            {
                "global_account_name": str(record.get("global_account_name") or "")[:80],
                "relation_display_name": str(record.get("relation_display_name") or "")[:80],
                "mobile": redacted_mobile(record.get("mobile_number")),
                "status": str(record.get("status") or ""),
                "owner_user_id": record.get("owner_user_id"),
                "accountant_user_id": record.get("accountant_user_id"),
            }
        )
    elif table == "customer_relations":
        payload.update(
            {
                "management_name": str(record.get("management_name") or "")[:80],
                "status": str(record.get("status") or ""),
                "owner_user_id": record.get("owner_user_id"),
                "customer_user_id": record.get("customer_user_id"),
            }
        )
    return payload


def build_finding(table: str, record: dict[str, Any], reasons: list[str]) -> dict[str, Any]:
    return {
        "severity": severity_for_record(table, record, reasons),
        "category": "suspicious_data_artifact",
        "table": table,
        "record_id": record.get("id"),
        "reasons": sorted(set(reasons)),
        "record": safe_record_payload(table, record),
    }


def overall_status(findings: list[dict[str, Any]]) -> str:
    status = "ok"
    for finding in findings:
        severity = str(finding.get("severity") or "warning")
        if STATUS_ORDER.get(severity, 1) > STATUS_ORDER[status]:
            status = severity
    return status


def should_fail(status: str, fail_on: str) -> bool:
    fail_on = fail_on.lower()
    if fail_on == "never":
        return False
    return STATUS_ORDER.get(status, 0) >= STATUS_ORDER.get(fail_on, STATUS_ORDER["high"])


def quote_identifier(identifier: str) -> str:
    if not re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_]*", identifier):
        raise ValueError(f"Unsafe SQL identifier: {identifier}")
    return f'"{identifier}"'


async def table_columns(session: Any, table: str) -> set[str]:
    result = await session.execute(
        text(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = :table_name
            """
        ),
        {"table_name": table},
    )
    return {str(row[0]) for row in result.fetchall()}


async def scan_table(session: Any, table: str, limit: int) -> tuple[list[dict[str, Any]], int]:
    columns = await table_columns(session, table)
    selected = [column for column in TABLE_SCAN_FIELDS[table] if column in columns]
    if "id" not in selected:
        return [], 0

    q_table = quote_identifier(table)
    q_columns = ", ".join(quote_identifier(column) for column in selected)
    result = await session.execute(text(f"SELECT {q_columns} FROM {q_table} ORDER BY id LIMIT :limit"), {"limit": limit})
    rows = [dict(row._mapping) for row in result.fetchall()]
    findings = []
    for row in rows:
        reasons = reasons_for_record(table, row)
        if reasons:
            findings.append(build_finding(table, row, reasons))
    return findings, len(rows)


async def suspicious_user_ids_from_findings(findings: list[dict[str, Any]]) -> list[int]:
    ids: list[int] = []
    for finding in findings:
        if finding.get("table") != "users":
            continue
        record_id = finding.get("record_id")
        if isinstance(record_id, int):
            ids.append(record_id)
    return sorted(set(ids))


async def count_related_rows(session: Any, suspicious_user_ids: list[int]) -> dict[str, Any]:
    if not suspicious_user_ids:
        return {}

    related: dict[str, Any] = {}
    id_list = ", ".join(str(int(user_id)) for user_id in suspicious_user_ids)
    for table, candidate_columns in RELATED_USER_COLUMNS.items():
        columns = await table_columns(session, table)
        usable_columns = [column for column in candidate_columns if column in columns]
        if not usable_columns:
            continue
        predicates = " OR ".join(f"{quote_identifier(column)} IN ({id_list})" for column in usable_columns)
        result = await session.execute(text(f"SELECT COUNT(*) FROM {quote_identifier(table)} WHERE {predicates}"))
        count = int(result.scalar() or 0)
        if count:
            related[table] = {"count": count, "columns": usable_columns}
    return related


async def collect_database_report(role: str, scan_limit: int, max_findings: int) -> dict[str, Any]:
    from core.db import AsyncSessionLocal

    scanned: dict[str, int] = {}
    findings: list[dict[str, Any]] = []
    async with AsyncSessionLocal() as session:
        for table in TABLE_SCAN_FIELDS:
            table_findings, row_count = await scan_table(session, table, scan_limit)
            scanned[table] = row_count
            findings.extend(table_findings)
        suspicious_ids = await suspicious_user_ids_from_findings(findings)
        related_counts = await count_related_rows(session, suspicious_ids)

    truncated = len(findings) > max_findings
    visible_findings = findings[:max_findings]
    status = overall_status(findings)
    return {
        "status": status,
        "role": role,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "finding_count": len(findings),
        "findings_truncated": truncated,
        "findings": visible_findings,
        "related_counts_for_suspicious_users": related_counts,
        "scanned_rows": scanned,
        "policy": {
            "fail_default": "high",
            "exact_mobile_values_are_redacted": True,
            "mode": "read_only",
        },
    }


def render_human(report: dict[str, Any]) -> str:
    lines = [
        f"Production data hygiene role={report['role']} status={report['status']} findings={report['finding_count']}",
        f"Scanned rows: {json.dumps(report['scanned_rows'], ensure_ascii=False, sort_keys=True)}",
    ]
    for finding in report["findings"]:
        lines.append(
            f"- {finding['severity']} {finding['table']}#{finding['record_id']} "
            f"reasons={','.join(finding['reasons'])} record={json.dumps(finding['record'], ensure_ascii=False, sort_keys=True)}"
        )
    related = report.get("related_counts_for_suspicious_users") or {}
    if related:
        lines.append(f"Related rows: {json.dumps(related, ensure_ascii=False, sort_keys=True)}")
    if report.get("findings_truncated"):
        lines.append("Findings are truncated; rerun with a larger --max-findings for full detail.")
    return "\n".join(lines)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--role", default="local", choices=("local", "foreign", "iran", "staging"))
    parser.add_argument("--scan-limit", type=int, default=DEFAULT_SCAN_LIMIT)
    parser.add_argument("--max-findings", type=int, default=DEFAULT_MAX_FINDINGS)
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of a human-readable report.")
    parser.add_argument(
        "--fail-on",
        choices=("warning", "high", "critical", "never"),
        default="high",
        help="Exit non-zero when overall status is at least this severity.",
    )
    return parser.parse_args(argv)


async def run(argv: list[str]) -> int:
    args = parse_args(argv)
    report = await collect_database_report(args.role, args.scan_limit, args.max_findings)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    else:
        print(render_human(report))
    return 1 if should_fail(str(report["status"]), args.fail_on) else 0


def main() -> int:
    return asyncio.run(run(sys.argv[1:]))


if __name__ == "__main__":
    raise SystemExit(main())
