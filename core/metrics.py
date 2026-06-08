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


_LABEL_VALUE_RE = re.compile(r"[^a-zA-Z0-9_.:/{}-]+")
_HTTP_ROUTE_ID_RE = re.compile(r"/(?:\d+|[0-9a-fA-F-]{16,})(?=/|$)")
_HISTOGRAM_BUCKETS = (5, 10, 25, 50, 100, 250, 500, 1000, 2500, 5000, 10000)


def _sanitize_label_value(value: Any, *, fallback: str = "unknown", max_length: int = 96) -> str:
    raw = str(value if value is not None else fallback).strip() or fallback
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
        self._db_path = os.getenv("TRADING_BOT_METRICS_DB", "/tmp/trading_bot_metrics.sqlite3")
        self._counters: dict[str, dict[tuple[tuple[str, str], ...], float]] = defaultdict(lambda: defaultdict(float))
        self._gauges: dict[str, dict[tuple[tuple[str, str], ...], float]] = defaultdict(dict)
        self._histograms: dict[str, dict[tuple[tuple[str, str], ...], dict[str, Any]]] = defaultdict(dict)
        self._help: dict[str, str] = {}
        self._types: dict[str, str] = {}

    def _shared_enabled(self) -> bool:
        return bool(self._db_path)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, timeout=0.2)
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
            try:
                os.remove(self._db_path)
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
        if shared is not None:
            return shared

        lines: list[str] = []
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
