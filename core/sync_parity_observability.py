"""Operator-facing summaries for cross-server sync parity comparisons."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping


DEFAULT_PARITY_STATUS_MAX_AGE_SECONDS = 15 * 60
ARTIFACT_METADATA_REQUIRED_FIELDS = (
    "local_server_mode",
    "peer_server_mode",
    "local_release_sha",
    "peer_release_sha",
    "snapshot_mode",
    "local_table_count",
    "peer_table_count",
    "local_snapshot_at",
    "peer_snapshot_at",
    "comparison_artifact_hash",
    "artifact_reference",
)


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


def _string_value(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _int_value(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _metadata_value(source: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        value = source.get(key)
        if value not in (None, ""):
            return value
    return None


def normalize_parity_artifact_metadata(comparison: Mapping[str, Any]) -> dict[str, Any]:
    source = comparison.get("artifact_metadata")
    source = source if isinstance(source, Mapping) else {}

    metadata: dict[str, Any] = {}
    string_fields = {
        "local_server_mode": ("local_server_mode", "local_mode", "source_server_mode"),
        "peer_server_mode": ("peer_server_mode", "peer_mode", "target_server_mode"),
        "local_release_sha": ("local_release_sha", "local_commit_sha", "source_release_sha"),
        "peer_release_sha": ("peer_release_sha", "peer_commit_sha", "target_release_sha"),
        "snapshot_mode": ("snapshot_mode", "mode"),
        "local_snapshot_at": ("local_snapshot_at", "source_snapshot_at"),
        "peer_snapshot_at": ("peer_snapshot_at", "target_snapshot_at"),
        "comparison_artifact_hash": ("comparison_artifact_hash", "artifact_hash", "sha256"),
        "artifact_reference": ("artifact_reference", "artifact_path", "storage_reference", "comparison_artifact_path"),
    }
    for normalized_key, aliases in string_fields.items():
        value = _string_value(_metadata_value(source, *aliases))
        if value is None and normalized_key == "snapshot_mode":
            value = _string_value(comparison.get("mode"))
        if value is not None:
            metadata[normalized_key] = value

    int_fields = {
        "local_table_count": ("local_table_count", "source_table_count"),
        "peer_table_count": ("peer_table_count", "target_table_count"),
    }
    for normalized_key, aliases in int_fields.items():
        value = _int_value(_metadata_value(source, *aliases))
        if value is not None:
            metadata[normalized_key] = value

    missing = [field for field in ARTIFACT_METADATA_REQUIRED_FIELDS if field not in metadata]
    return {
        "artifact_metadata": metadata,
        "artifact_metadata_complete": not missing,
        "artifact_metadata_missing_fields": missing,
    }


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
    artifact_metadata = normalize_parity_artifact_metadata(comparison)
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
        **artifact_metadata,
    }


def strict_alert_gate_from_parity_summary(
    summary: Mapping[str, Any] | None,
    *,
    require_artifact_metadata: bool = False,
) -> dict[str, Any]:
    if not summary:
        return {
            "status": "blocked",
            "reason": "missing_parity_evidence",
        }
    latest_summary = dict(summary)
    if "artifact_metadata_complete" not in latest_summary:
        latest_summary.update(normalize_parity_artifact_metadata(latest_summary))

    if not bool(latest_summary.get("fresh")):
        return {
            "status": "blocked",
            "reason": "stale_parity_evidence",
            "latest_parity": latest_summary,
        }
    if require_artifact_metadata and not bool(latest_summary.get("artifact_metadata_complete")):
        return {
            "status": "blocked",
            "reason": "missing_artifact_metadata",
            "missing_fields": list(latest_summary.get("artifact_metadata_missing_fields") or ARTIFACT_METADATA_REQUIRED_FIELDS),
            "latest_parity": latest_summary,
        }
    parity_status = str(latest_summary.get("status") or "unknown")
    if parity_status not in {"ok", "non_business_difference"}:
        return {
            "status": "blocked",
            "reason": f"parity_status_{parity_status}",
            "latest_parity": latest_summary,
        }
    if int(latest_summary.get("incomplete_count") or 0) > 0:
        return {
            "status": "blocked",
            "reason": "parity_incomplete_tables",
            "latest_parity": latest_summary,
        }
    if int(latest_summary.get("truncated_table_count") or 0) > 0:
        return {
            "status": "blocked",
            "reason": "parity_truncated_tables",
            "latest_parity": latest_summary,
        }
    if int(latest_summary.get("duplicate_identity_count") or 0) > 0:
        return {
            "status": "blocked",
            "reason": "parity_duplicate_identities",
            "latest_parity": latest_summary,
        }
    if int(latest_summary.get("critical_drift_count") or 0) > 0 or int(latest_summary.get("business_drift_count") or 0) > 0:
        return {
            "status": "blocked",
            "reason": "parity_business_or_critical_drift",
            "latest_parity": latest_summary,
        }
    return {
        "status": "passed",
        "reason": None,
        "latest_parity": latest_summary,
    }
