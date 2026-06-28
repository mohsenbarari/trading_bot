"""Operator-facing summaries for cross-server sync parity comparisons."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping


DEFAULT_PARITY_STATUS_MAX_AGE_SECONDS = 15 * 60


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_utc_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        text = str(value).strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def infer_parity_comparison_mode(local_snapshot: Mapping[str, Any], peer_snapshot: Mapping[str, Any]) -> str:
    local_mode = str(local_snapshot.get("mode") or "").strip()
    peer_mode = str(peer_snapshot.get("mode") or "").strip()
    if local_mode and local_mode == peer_mode:
        return local_mode
    if local_mode or peer_mode:
        return "mixed"
    return "unknown"


def summarize_parity_comparison(
    comparison: Mapping[str, Any],
    *,
    mode: str | None = None,
    observed_at: str | None = None,
    max_age_seconds: int = DEFAULT_PARITY_STATUS_MAX_AGE_SECONDS,
    now: datetime | None = None,
) -> dict[str, Any]:
    tables = comparison.get("tables") if isinstance(comparison, Mapping) else {}
    tables = tables if isinstance(tables, Mapping) else {}
    severity_counts = comparison.get("severity_counts") if isinstance(comparison, Mapping) else {}
    severity_counts = severity_counts if isinstance(severity_counts, Mapping) else {}
    observed = observed_at or str(comparison.get("compared_at") or comparison.get("observed_at") or utc_now_iso())
    observed_dt = parse_utc_datetime(observed)
    now_dt = now or datetime.now(timezone.utc)
    age_seconds = None
    if observed_dt is not None:
        age_seconds = max((now_dt.astimezone(timezone.utc) - observed_dt).total_seconds(), 0.0)
    fresh = bool(age_seconds is not None and age_seconds <= max(int(max_age_seconds), 0))
    duplicate_identity_count = int(comparison.get("duplicate_identity_count") or 0)
    truncated_table_count = int(comparison.get("truncated_table_count") or 0)
    for report in tables.values():
        if not isinstance(report, Mapping):
            continue
        if report.get("local_truncated") or report.get("peer_truncated"):
            truncated_table_count += 1
        duplicate_identity_count += int(report.get("local_duplicate_identity_count") or 0)
        duplicate_identity_count += int(report.get("peer_duplicate_identity_count") or 0)
    return {
        "status": str(comparison.get("status") or "unknown"),
        "fresh": fresh,
        "age_seconds": round(age_seconds, 3) if age_seconds is not None else None,
        "max_age_seconds": int(max_age_seconds),
        "mode": mode or str(comparison.get("mode") or "unknown"),
        "observed_at": observed,
        "table_count": int(comparison.get("table_count") or len(tables)),
        "truncated_table_count": truncated_table_count,
        "duplicate_identity_count": duplicate_identity_count,
        "business_drift_count": int(severity_counts.get("business_drift") or comparison.get("business_drift_count") or 0),
        "critical_drift_count": int(severity_counts.get("critical_drift") or comparison.get("critical_drift_count") or 0),
        "incomplete_count": int(severity_counts.get("incomplete") or comparison.get("incomplete_count") or 0),
        "non_business_difference_count": int(severity_counts.get("local_only_difference") or 0)
        + int(severity_counts.get("volatile_difference") or 0)
        + int(comparison.get("non_business_difference_count") or 0),
    }


def strict_alert_gate_from_parity_summary(summary: Mapping[str, Any] | None) -> dict[str, Any]:
    if not summary:
        return {
            "status": "blocked",
            "reason": "missing_parity_evidence",
        }
    if not bool(summary.get("fresh")):
        return {
            "status": "blocked",
            "reason": "stale_parity_evidence",
            "latest_parity": dict(summary),
        }
    parity_status = str(summary.get("status") or "unknown")
    if parity_status not in {"ok", "non_business_difference"}:
        return {
            "status": "blocked",
            "reason": f"parity_status_{parity_status}",
            "latest_parity": dict(summary),
        }
    if int(summary.get("incomplete_count") or 0) > 0:
        return {
            "status": "blocked",
            "reason": "parity_incomplete_tables",
            "latest_parity": dict(summary),
        }
    if int(summary.get("truncated_table_count") or 0) > 0:
        return {
            "status": "blocked",
            "reason": "parity_truncated_tables",
            "latest_parity": dict(summary),
        }
    if int(summary.get("duplicate_identity_count") or 0) > 0:
        return {
            "status": "blocked",
            "reason": "parity_duplicate_identities",
            "latest_parity": dict(summary),
        }
    if int(summary.get("critical_drift_count") or 0) > 0 or int(summary.get("business_drift_count") or 0) > 0:
        return {
            "status": "blocked",
            "reason": "parity_business_or_critical_drift",
            "latest_parity": dict(summary),
        }
    return {
        "status": "passed",
        "reason": None,
        "latest_parity": dict(summary),
    }
