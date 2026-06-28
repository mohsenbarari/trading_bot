"""Small Prometheus text-format metrics registry.

This avoids adding a runtime dependency while keeping labels bounded and
scrapable by Prometheus.
"""

from __future__ import annotations

import re
import json
import os
import sqlite3
import threading
import time
from collections import defaultdict
from collections.abc import Iterable, Mapping
from typing import Any

from core.log_redaction import redact_string


_LABEL_VALUE_RE = re.compile(r"[^a-zA-Z0-9_.:/{}-]+")
_HTTP_ROUTE_ID_RE = re.compile(r"/(?:\d+|[0-9a-fA-F-]{16,})(?=/|$)")
_FILENAME_LABEL_RE = re.compile(r"(?i)\b[\w.-]+\.(?:jpg|jpeg|png|webp|gif|pdf|xlsx?|docx?|zip|mp4|mov|mp3|wav|ogg)\b")
_HISTOGRAM_BUCKETS = (5, 10, 25, 50, 100, 250, 500, 1000, 2500, 5000, 10000)
_SUPPORTED_BACKENDS = {"memory", "shared_sqlite"}


def _coerce_metrics_backend(value: str | None) -> str:
    candidate = (value or "memory").strip().lower()
    return candidate if candidate in _SUPPORTED_BACKENDS else "memory"


def _sanitize_label_value(value: Any, *, fallback: str = "unknown", max_length: int = 96) -> str:
    raw = str(value if value is not None else fallback).strip() or fallback
    raw = _FILENAME_LABEL_RE.sub("redacted_file", redact_string(raw))
    sanitized = _LABEL_VALUE_RE.sub("_", raw)[:max_length]
    return sanitized or fallback


def normalize_http_route(path: str | None) -> str:
    raw = str(path or "unknown").split("?", 1)[0]
    return _sanitize_label_value(_HTTP_ROUTE_ID_RE.sub("/{id}", raw), max_length=120)


def normalize_status_class(status_code: int | str | None) -> str:
    try:
        value = int(status_code or 0)
    except (TypeError, ValueError):
        return "unknown"
    if value <= 0:
        return "unknown"
    return f"{value // 100}xx"


def normalize_result(result: str | None) -> str:
    candidate = _sanitize_label_value(result, fallback="unknown", max_length=32)
    return candidate if candidate in {"success", "failure", "denied", "noop", "error", "ok"} else candidate


def _label_key(labels: Mapping[str, Any]) -> tuple[tuple[str, str], ...]:
    return tuple(sorted((str(key), _sanitize_label_value(value)) for key, value in labels.items()))


def _format_labels(labels: Iterable[tuple[str, str]]) -> str:
    items = list(labels)
    if not items:
        return ""
    rendered = ",".join(f'{key}="{value.replace(chr(92), chr(92) * 2).replace(chr(34), chr(92) + chr(34))}"' for key, value in items)
    return f"{{{rendered}}}"


class MetricsRegistry:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._backend = _coerce_metrics_backend(os.getenv("TRADING_BOT_METRICS_BACKEND"))
        self._service_name = _sanitize_label_value(os.getenv("TRADING_BOT_SERVICE", "app"), max_length=32)
        self._db_path = os.getenv("TRADING_BOT_METRICS_DB", "/tmp/trading_bot_metrics.sqlite3")
        self._counters: dict[str, dict[tuple[tuple[str, str], ...], float]] = defaultdict(lambda: defaultdict(float))
        self._gauges: dict[str, dict[tuple[tuple[str, str], ...], float]] = defaultdict(dict)
        self._histograms: dict[str, dict[tuple[tuple[str, str], ...], dict[str, Any]]] = defaultdict(dict)
        self._help: dict[str, str] = {}
        self._types: dict[str, str] = {}

    def _shared_enabled(self) -> bool:
        return self._backend == "shared_sqlite" and bool(self._db_path)

    def backend_name(self) -> str:
        return self._backend

    def service_name(self) -> str:
        return self._service_name

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, timeout=1.0)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS metrics (
                name TEXT NOT NULL,
                metric_type TEXT NOT NULL,
                labels TEXT NOT NULL,
                bucket TEXT NOT NULL DEFAULT '',
                value REAL NOT NULL,
                help TEXT NOT NULL,
                PRIMARY KEY (name, labels, bucket)
            )
            """
        )
        return conn

    def _labels_json(self, labels: Mapping[str, Any]) -> str:
        return json.dumps(dict(_label_key(labels)), ensure_ascii=False, separators=(",", ":"), sort_keys=True)

    def _upsert_add(self, name: str, metric_type: str, labels: Mapping[str, Any], bucket: str, amount: float, help_text: str) -> None:
        if not self._shared_enabled():
            return
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO metrics(name, metric_type, labels, bucket, value, help)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(name, labels, bucket) DO UPDATE SET
                        value = value + excluded.value,
                        metric_type = excluded.metric_type,
                        help = excluded.help
                    """,
                    (name, metric_type, self._labels_json(labels), bucket, float(amount), help_text),
                )
        except sqlite3.Error:
            pass

    def _upsert_set(self, name: str, metric_type: str, labels: Mapping[str, Any], bucket: str, value: float, help_text: str) -> None:
        if not self._shared_enabled():
            return
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO metrics(name, metric_type, labels, bucket, value, help)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(name, labels, bucket) DO UPDATE SET
                        value = excluded.value,
                        metric_type = excluded.metric_type,
                        help = excluded.help
                    """,
                    (name, metric_type, self._labels_json(labels), bucket, float(value), help_text),
                )
        except sqlite3.Error:
            pass

    def reset(self) -> None:
        with self._lock:
            self._counters.clear()
            self._gauges.clear()
            self._histograms.clear()
            self._help.clear()
            self._types.clear()
        if self._shared_enabled():
            for path in (self._db_path, f"{self._db_path}-wal", f"{self._db_path}-shm"):
                try:
                    os.remove(path)
                except FileNotFoundError:
                    pass

    def counter(self, name: str, help_text: str, amount: float = 1, **labels: Any) -> None:
        self._upsert_add(name, "counter", labels, "", amount, help_text)
        with self._lock:
            self._help.setdefault(name, help_text)
            self._types.setdefault(name, "counter")
            self._counters[name][_label_key(labels)] += amount

    def gauge(self, name: str, help_text: str, value: float, **labels: Any) -> None:
        self._upsert_set(name, "gauge", labels, "", value, help_text)
        with self._lock:
            self._help.setdefault(name, help_text)
            self._types.setdefault(name, "gauge")
            self._gauges[name][_label_key(labels)] = value

    def observe(self, name: str, help_text: str, value: float, **labels: Any) -> None:
        if self._shared_enabled():
            updates = [("sum", float(value)), ("count", 1.0), ("+Inf", 1.0)]
            updates.extend((str(bucket), 1.0) for bucket in _HISTOGRAM_BUCKETS if value <= bucket)
            try:
                labels_json = self._labels_json(labels)
                with self._connect() as conn:
                    conn.executemany(
                        """
                        INSERT INTO metrics(name, metric_type, labels, bucket, value, help)
                        VALUES (?, ?, ?, ?, ?, ?)
                        ON CONFLICT(name, labels, bucket) DO UPDATE SET
                            value = value + excluded.value,
                            metric_type = excluded.metric_type,
                            help = excluded.help
                        """,
                        [(name, "histogram", labels_json, bucket, amount, help_text) for bucket, amount in updates],
                    )
            except sqlite3.Error:
                pass
        with self._lock:
            self._help.setdefault(name, help_text)
            self._types.setdefault(name, "histogram")
            key = _label_key(labels)
            state = self._histograms[name].setdefault(
                key,
                {"sum": 0.0, "count": 0, "buckets": {bucket: 0 for bucket in _HISTOGRAM_BUCKETS}},
            )
            state["sum"] += float(value)
            state["count"] += 1
            for bucket in _HISTOGRAM_BUCKETS:
                if value <= bucket:
                    state["buckets"][bucket] += 1

    def _render_shared_prometheus(self) -> str | None:
        if not self._shared_enabled() or not os.path.exists(self._db_path):
            return None
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT name, metric_type, labels, bucket, value, help FROM metrics ORDER BY name, labels, bucket"
                ).fetchall()
        except sqlite3.Error:
            return None
        if not rows:
            return None

        grouped: dict[str, dict[str, Any]] = {}
        for name, metric_type, labels_raw, bucket, value, help_text in rows:
            metric = grouped.setdefault(name, {"type": metric_type, "help": help_text, "samples": []})
            try:
                labels = tuple(sorted(json.loads(labels_raw).items()))
            except (TypeError, ValueError, json.JSONDecodeError):
                labels = ()
            metric["samples"].append((labels, bucket, value))

        lines: list[str] = []
        for name in sorted(grouped):
            metric = grouped[name]
            lines.append(f"# HELP {name} {metric['help']}")
            lines.append(f"# TYPE {name} {metric['type']}")
            if metric["type"] in {"counter", "gauge"}:
                for labels, _bucket, value in metric["samples"]:
                    lines.append(f"{name}{_format_labels(labels)} {value:g}")
            elif metric["type"] == "histogram":
                by_labels: dict[tuple[tuple[str, str], ...], dict[str, float]] = defaultdict(dict)
                for labels, bucket, value in metric["samples"]:
                    by_labels[labels][bucket] = value
                for labels, buckets in sorted(by_labels.items()):
                    for bucket in [str(item) for item in _HISTOGRAM_BUCKETS] + ["+Inf"]:
                        bucket_labels = tuple(list(labels) + [("le", bucket)])
                        lines.append(f"{name}_bucket{_format_labels(bucket_labels)} {buckets.get(bucket, 0):g}")
                    lines.append(f"{name}_count{_format_labels(labels)} {buckets.get('count', 0):g}")
                    lines.append(f"{name}_sum{_format_labels(labels)} {buckets.get('sum', 0):g}")
        return "\n".join(lines) + "\n"

    def render_prometheus(self) -> str:
        shared = self._render_shared_prometheus()
        metadata_lines = [
            "# HELP trading_bot_metrics_backend_info Metrics backend metadata for this service process.",
            "# TYPE trading_bot_metrics_backend_info gauge",
            (
                "trading_bot_metrics_backend_info"
                f'{{backend="{self._backend}",service="{self._service_name}",shared="{str(self._shared_enabled()).lower()}"}} 1'
            ),
        ]
        if shared is not None:
            return "\n".join(metadata_lines) + "\n" + shared

        lines: list[str] = list(metadata_lines)
        with self._lock:
            metric_names = sorted(set(self._help) | set(self._types))
            for name in metric_names:
                lines.append(f"# HELP {name} {self._help.get(name, name)}")
                lines.append(f"# TYPE {name} {self._types.get(name, 'gauge')}")
                metric_type = self._types.get(name)
                if metric_type == "counter":
                    for labels, value in sorted(self._counters.get(name, {}).items()):
                        lines.append(f"{name}{_format_labels(labels)} {value:g}")
                elif metric_type == "gauge":
                    for labels, value in sorted(self._gauges.get(name, {}).items()):
                        lines.append(f"{name}{_format_labels(labels)} {value:g}")
                elif metric_type == "histogram":
                    for labels, state in sorted(self._histograms.get(name, {}).items()):
                        for bucket, count in state["buckets"].items():
                            bucket_labels = tuple(list(labels) + [("le", str(bucket))])
                            lines.append(f"{name}_bucket{_format_labels(bucket_labels)} {count:g}")
                        inf_labels = tuple(list(labels) + [("le", "+Inf")])
                        lines.append(f"{name}_bucket{_format_labels(inf_labels)} {state['count']:g}")
                        lines.append(f"{name}_count{_format_labels(labels)} {state['count']:g}")
                        lines.append(f"{name}_sum{_format_labels(labels)} {state['sum']:g}")
        return "\n".join(lines) + "\n"


registry = MetricsRegistry()


def record_http_request(*, method: str, route: str, status_code: int, duration_ms: float) -> None:
    labels = {
        "method": _sanitize_label_value(method, max_length=16),
        "route": normalize_http_route(route),
        "status_class": normalize_status_class(status_code),
    }
    registry.counter("trading_bot_http_requests_total", "HTTP requests by method, route, and status class.", **labels)
    registry.observe("trading_bot_http_request_duration_ms", "HTTP request duration in milliseconds.", duration_ms, **labels)
    if int(status_code or 0) >= 400:
        registry.counter("trading_bot_http_error_responses_total", "HTTP 4xx/5xx responses by route.", **labels)


def set_active_websocket_connections(count: int) -> None:
    registry.gauge(
        "trading_bot_websocket_active_connections",
        "Active websocket connections in this process.",
        max(int(count), 0),
    )


def record_websocket_publish_failure(event_type: str) -> None:
    registry.counter(
        "trading_bot_websocket_publish_failures_total",
        "Realtime websocket/Redis publish failures by event type.",
        event_type=_sanitize_label_value(event_type, max_length=64),
    )


def record_bot_update(*, event_type: str, result: str, duration_ms: float) -> None:
    labels = {"event_type": _sanitize_label_value(event_type, max_length=48), "result": normalize_result(result)}
    registry.counter("trading_bot_bot_updates_total", "Bot updates handled by event type and result.", **labels)
    registry.observe("trading_bot_bot_update_duration_ms", "Bot update handling duration in milliseconds.", duration_ms, **labels)


def record_job_run(*, job_name: str, result: str, duration_ms: float) -> None:
    labels = {"job_name": _sanitize_label_value(job_name, max_length=64), "result": normalize_result(result)}
    registry.counter("trading_bot_job_runs_total", "Background job iterations by job and result.", **labels)
    registry.observe("trading_bot_job_duration_ms", "Background job iteration duration in milliseconds.", duration_ms, **labels)


def record_sync_health(*, server_mode: str, unsynced_count: int, oldest_unsynced_age_seconds: float, outbound_queue: int, retry_queue: int) -> None:
    labels = {"server_mode": _sanitize_label_value(server_mode, max_length=16)}
    registry.gauge(
        "trading_bot_sync_unsynced_change_log_entries",
        "Unsynced change_log entries waiting to be replayed to the peer server.",
        max(int(unsynced_count), 0),
        **labels,
    )
    registry.gauge(
        "trading_bot_sync_oldest_unsynced_age_seconds",
        "Age in seconds of the oldest unsynced change_log entry.",
        max(float(oldest_unsynced_age_seconds), 0.0),
        **labels,
    )
    registry.gauge(
        "trading_bot_sync_redis_queue_length",
        "Redis sync queue length by queue name.",
        max(int(outbound_queue), 0),
        queue="outbound",
        **labels,
    )
    registry.gauge(
        "trading_bot_sync_redis_queue_length",
        "Redis sync queue length by queue name.",
        max(int(retry_queue), 0),
        queue="retry",
        **labels,
    )


def record_offer_publication_health(
    *,
    server_mode: str,
    state_counts: Mapping[str, Mapping[str, int]] | None,
    finding_counts: Mapping[str, int] | None,
) -> None:
    labels = {"server_mode": _sanitize_label_value(server_mode, max_length=16)}
    for surface, statuses in (state_counts or {}).items():
        for status, count in (statuses or {}).items():
            registry.gauge(
                "trading_bot_offer_publication_states",
                "Offer publication states by surface and status.",
                max(int(count or 0), 0),
                surface=_sanitize_label_value(surface, max_length=40),
                status=_sanitize_label_value(status, max_length=40),
                **labels,
            )
    for issue, count in (finding_counts or {}).items():
        registry.gauge(
            "trading_bot_offer_publication_reconciliation_findings",
            "Current offer publication reconciliation findings by issue.",
            max(int(count or 0), 0),
            issue=_sanitize_label_value(issue, max_length=80),
            **labels,
        )


def record_sync_conflict(*, server_mode: str, table: str, reason: str) -> None:
    registry.counter(
        "trading_bot_sync_conflicts_total",
        "Sync conflict or idempotent merge events by table and reason.",
        server_mode=_sanitize_label_value(server_mode, max_length=16),
        table=_sanitize_label_value(table, max_length=64),
        reason=_sanitize_label_value(reason, max_length=80),
    )


def record_sync_source_authority_rejection(*, server_mode: str, table: str, reason: str) -> None:
    registry.counter(
        "trading_bot_sync_source_authority_rejections_total",
        "Receiver-side sync rejections caused by source authority policy.",
        server_mode=_sanitize_label_value(server_mode, max_length=16),
        table=_sanitize_label_value(table, max_length=64),
        reason=_sanitize_label_value(reason, max_length=80),
    )


def record_sync_terminal_policy_rejection(*, server_mode: str, table: str, reason: str) -> None:
    registry.counter(
        "trading_bot_sync_terminal_policy_rejections_total",
        "Worker-dropped terminal sync policy rejections by table and reason.",
        server_mode=_sanitize_label_value(server_mode, max_length=16),
        table=_sanitize_label_value(table, max_length=64),
        reason=_sanitize_label_value(reason, max_length=80),
    )


def record_sync_watermark_decision(*, server_mode: str, table: str, decision: str, reason: str | None = None) -> None:
    registry.counter(
        "trading_bot_sync_watermark_decisions_total",
        "Source-sequence watermark decisions by table, decision, and reason.",
        server_mode=_sanitize_label_value(server_mode, max_length=16),
        table=_sanitize_label_value(table, max_length=64),
        decision=_sanitize_label_value(decision, max_length=32),
        reason=_sanitize_label_value(reason, fallback="none", max_length=80),
    )


def record_sync_parity_summary(
    *,
    server_mode: str,
    status: str,
    fresh: bool,
    business_drift_count: int,
    critical_drift_count: int,
    incomplete_count: int,
) -> None:
    labels = {
        "server_mode": _sanitize_label_value(server_mode, max_length=16),
        "status": _sanitize_label_value(status, max_length=40),
        "fresh": "true" if fresh else "false",
    }
    registry.gauge(
        "trading_bot_sync_parity_business_drift_tables",
        "Latest stored parity comparison business-drift table count.",
        max(int(business_drift_count or 0), 0),
        **labels,
    )
    registry.gauge(
        "trading_bot_sync_parity_critical_drift_tables",
        "Latest stored parity comparison critical-drift table count.",
        max(int(critical_drift_count or 0), 0),
        **labels,
    )
    registry.gauge(
        "trading_bot_sync_parity_incomplete_tables",
        "Latest stored parity comparison incomplete table count.",
        max(int(incomplete_count or 0), 0),
        **labels,
    )


def record_business_action(*, action: str, result: str) -> None:
    registry.counter(
        "trading_bot_business_actions_total",
        "Business and audit actions by action name and result.",
        action=_sanitize_label_value(action, max_length=80),
        result=normalize_result(result),
    )


def metrics_response_body() -> str:
    return registry.render_prometheus()


def uptime_seconds(started_at: float) -> float:
    return max(time.monotonic() - started_at, 0.0)
