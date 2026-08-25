#!/usr/bin/env python3
"""Produce a payload-free Stage 0 inventory for the coin market pipeline.

This program is deliberately read-only.  Its output contains aggregate counts,
time bounds, process states, and storage sizes only.  Telegram identifiers,
message bodies, entities, database payload columns, environment values, and
session material are never emitted.
"""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import glob
import json
import os
import platform
import re
import shutil
import sqlite3
import subprocess
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


SCHEMA_VERSION = 1
UTC = dt.timezone.utc

ROLE_CONFIG: dict[str, dict[str, Any]] = {
    "bot": {
        "services": (
            "coin-public-market-telegram.service",
            "coin-rate-estimator-dashboard.service",
            "coin-group-event-telegram.service",
            "coin-intelligence-staging-market-input-bridge.service",
        ),
        "timers": (
            "coin-intelligence-production-snapshot-relay.timer",
            "coin-intelligence-staging-market-input-bridge.timer",
            "coin-intelligence-staging-snapshot-publish.timer",
            "coin-intelligence-staging-snapshot-relay.timer",
            "coin-intelligence-sync-health-sampler.timer",
            "coin-group-event-telegram.timer",
        ),
        "containers": (
            "trading_bot_app",
            "trading_bot_bot",
            "trading_bot_db",
            "trading_bot_redis",
            "trading_bot_sync_worker",
            "trading_bot_staging_bot",
            "trading_bot_staging_bot_executor",
            "trading_bot_staging_foreign_app",
            "trading_bot_staging_db",
            "trading_bot_staging_redis",
            "trading_bot_staging_foreign_sync_worker",
        ),
        "storage_roots": (
            ("coin_intelligence", "/srv/trading-bot/production-data/coin-intelligence"),
        ),
        "jsonl_streams": (),
        "heartbeats": (),
        "model_states": (
            (
                "coin_estimator",
                "/srv/trading-bot/production-data/coin-intelligence/"
                "estimator-live/runtime/state.json",
            ),
        ),
        "databases": (
            (
                "public_market",
                "/srv/trading-bot/production-data/coin-intelligence/"
                "estimator-live/public-market/market_prices.sqlite3",
                (
                    ("raw_posts", "published_at_utc"),
                    ("price_events", "event_time_utc"),
                    ("external_market_observations", "observed_at_utc"),
                ),
            ),
            (
                "coin_conversation",
                "/srv/trading-bot/production-data/coin-intelligence/"
                "estimator-live/conversation/conversation_events.sqlite3",
                (
                    ("messages", "event_time_utc"),
                    ("offers", None),
                    ("trade_requests", None),
                    ("confirmed_trades", "event_time_utc"),
                    ("review_queue", None),
                ),
            ),
            (
                "canonical_market",
                "/srv/trading-bot/production-data/coin-intelligence/"
                "private-gold-live/market/market.sqlite3",
                (
                    ("market_observations", "event_time_utc"),
                    ("market_observations_archive", "event_time_utc"),
                    ("market_source_checkpoints", "last_event_time_utc"),
                ),
            ),
            (
                "coin_capture_staging",
                "/srv/trading-bot/production-data/coin-intelligence/"
                "private-gold-live/staging/coin-groups.sqlite3",
                (
                    ("coin_group_staged_messages", "event_time_utc"),
                ),
            ),
            (
                "private_gold_capture_staging",
                "/srv/trading-bot/production-data/coin-intelligence/"
                "private-gold-live/staging/private-gold.sqlite3",
                (
                    ("private_gold_staged_offers", "event_time_utc"),
                ),
            ),
        ),
    },
    "web": {
        "services": (
            "coin-capture.service",
            "market-channel-capture.service",
        ),
        "timers": (),
        "containers": (),
        "storage_roots": (
            ("coin_capture", "/srv/coin_group_capture"),
            ("market_capture", "/srv/market_channel_capture"),
            ("shadow_pipeline", "/srv/coin-intelligence-shadow"),
        ),
        "jsonl_streams": (
            ("coin_groups", "/srv/coin_group_capture/runtime/spool/coin/events-*.jsonl"),
            (
                "market_channels",
                "/srv/market_channel_capture/runtime/spool/"
                "market-channel/events-*.jsonl",
            ),
        ),
        "heartbeats": (
            ("market_channels", "/srv/market_channel_capture/runtime/heartbeat.json"),
        ),
        "model_states": (),
        "databases": (
            (
                "shadow_canonical_market",
                "/srv/coin-intelligence-shadow/runtime/live/market/market.sqlite3",
                (
                    ("market_observations", "event_time_utc"),
                    ("market_observations_archive", "event_time_utc"),
                    ("market_source_checkpoints", "last_event_time_utc"),
                ),
            ),
            (
                "shadow_capture_staging",
                "/srv/coin-intelligence-shadow/runtime/live/staging/capture.sqlite3",
                (
                    ("coin_group_staged_messages", "event_time_utc"),
                    ("capture_market_messages", "event_time_utc"),
                    ("capture_market_message_revisions", "event_time_utc"),
                    ("capture_tombstones", "available_at_utc"),
                    ("capture_seen_events", "available_at_utc"),
                ),
            ),
            (
                "coin_capture_state",
                "/srv/coin_group_capture/runtime/state.db",
                (
                    ("seen", "ts"),
                    ("deleted", "ts"),
                    ("outbox", "ts"),
                    ("message_origin", "ts"),
                ),
            ),
            (
                "market_capture_state",
                "/srv/market_channel_capture/runtime/state.db",
                (
                    ("seen", "ts"),
                    ("messages", "updated_at"),
                    ("deleted_messages", "updated_at"),
                    ("outbox", "ts"),
                ),
            ),
        ),
    },
}


FORBIDDEN_OUTPUT_KEYS = {
    "action_type",
    "content_digest",
    "entities",
    "entities_json",
    "event_id",
    "message_id",
    "message_text",
    "payload",
    "peer_id",
    "raw_text",
    "reply",
    "reply_to_message_id",
    "revision_sha256",
    "sender_digest",
    "sender_hash",
    "source_id",
    "text",
    "text_sha256",
}


def utc_now() -> str:
    return dt.datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _run(command: Sequence[str]) -> tuple[int, str]:
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return 127, ""
    return completed.returncode, completed.stdout.strip()


def summarize_git(repo: Path) -> dict[str, Any]:
    if not (repo / ".git").exists():
        return {"available": False}
    branch_rc, branch = _run(("git", "-C", str(repo), "branch", "--show-current"))
    sha_rc, sha = _run(("git", "-C", str(repo), "rev-parse", "HEAD"))
    dirty_rc, dirty = _run(("git", "-C", str(repo), "status", "--porcelain"))
    return {
        "available": branch_rc == sha_rc == dirty_rc == 0,
        "branch": branch if branch_rc == 0 else None,
        "sha": sha if sha_rc == 0 else None,
        "dirty_entry_count": len(dirty.splitlines()) if dirty_rc == 0 and dirty else 0,
    }


def summarize_units(names: Iterable[str]) -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    for name in names:
        rc, output = _run(
            (
                "systemctl",
                "show",
                name,
                "--property=LoadState,ActiveState,SubState,UnitFileState",
            )
        )
        fields = dict(
            line.split("=", 1) for line in output.splitlines() if "=" in line
        )
        result[name] = {
            "load": fields.get("LoadState", "unavailable" if rc else "unknown"),
            "active": fields.get("ActiveState", "unavailable"),
            "sub": fields.get("SubState", "unavailable"),
            "enabled": fields.get("UnitFileState", "unavailable"),
        }
    return result


def summarize_containers(names: Iterable[str]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for name in names:
        rc, output = _run(
            (
                "docker",
                "inspect",
                "--format",
                "{{.Config.Image}}|{{.State.Status}}|{{.State.Running}}|{{.RestartCount}}",
                name,
            )
        )
        if rc != 0 or not output:
            result[name] = {"available": False}
            continue
        image, state, running, restart_count = (output.split("|", 3) + ["", "", "", ""])[:4]
        result[name] = {
            "available": True,
            "image": image,
            "state": state,
            "running": running.lower() == "true",
            "restart_count": int(restart_count) if restart_count.isdigit() else None,
        }
    return result


def directory_size(path: Path) -> int:
    total = 0
    for root, _dirs, files in os.walk(path, onerror=lambda _error: None):
        for filename in files:
            try:
                total += (Path(root) / filename).stat().st_size
            except OSError:
                continue
    return total


def summarize_storage_roots(specs: Iterable[tuple[str, str]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for label, raw_path in specs:
        path = Path(raw_path)
        result[label] = (
            {"available": True, "bytes": directory_size(path)}
            if path.is_dir()
            else {"available": False}
        )
    return result


def summarize_disks(role: str) -> dict[str, Any]:
    paths = [Path("/")]
    if role == "bot":
        paths.append(Path("/srv/trading-bot/production-data"))
    result: dict[str, Any] = {}
    for index, path in enumerate(paths, start=1):
        label = "root" if index == 1 else "persistent_data"
        if not path.exists():
            result[label] = {"available": False}
            continue
        usage = shutil.disk_usage(path)
        result[label] = {
            "available": True,
            "total_bytes": usage.total,
            "used_bytes": usage.used,
            "free_bytes": usage.free,
            "used_percent": round((usage.used / usage.total) * 100, 1),
        }
    return result


def _update_bounds(bounds: list[str | None], value: Any) -> None:
    if not isinstance(value, str) or not value:
        return
    if bounds[0] is None or value < bounds[0]:
        bounds[0] = value
    if bounds[1] is None or value > bounds[1]:
        bounds[1] = value


def summarize_jsonl_files(paths: Iterable[Path]) -> dict[str, Any]:
    files = sorted(path for path in paths if path.is_file())
    event_types: collections.Counter[str] = collections.Counter()
    schema_versions: collections.Counter[str] = collections.Counter()
    source_keys: set[str] = set()
    events_per_source: collections.Counter[str] = collections.Counter()
    occurred_bounds: list[str | None] = [None, None]
    available_bounds: list[str | None] = [None, None]
    records = invalid_records = backfill_records = 0
    daily_files: list[dict[str, Any]] = []

    for path in files:
        file_records = file_invalid_records = 0
        try:
            stream = path.open("r", encoding="utf-8")
        except OSError:
            invalid_records += 1
            file_invalid_records += 1
            continue
        with stream:
            for line in stream:
                if not line.strip():
                    continue
                try:
                    event = json.loads(line)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    invalid_records += 1
                    file_invalid_records += 1
                    continue
                if not isinstance(event, dict):
                    invalid_records += 1
                    file_invalid_records += 1
                    continue
                records += 1
                file_records += 1
                event_type = event.get("event_type")
                if isinstance(event_type, str) and event_type:
                    event_types[event_type] += 1
                schema_version = event.get("schema_version")
                if isinstance(schema_version, (str, int)):
                    schema_versions[str(schema_version)] += 1
                source = event.get("source")
                if isinstance(source, dict):
                    # Values are used only to calculate cardinality and anonymous
                    # distribution; neither source IDs nor market names are emitted.
                    source_key = json.dumps(
                        [source.get("source_id"), source.get("market")],
                        ensure_ascii=False,
                        sort_keys=True,
                    )
                    source_keys.add(source_key)
                    events_per_source[source_key] += 1
                producer = event.get("producer")
                if isinstance(producer, dict):
                    if producer.get("is_backfill") is True:
                        backfill_records += 1
                    _update_bounds(available_bounds, producer.get("available_at_utc"))
                _update_bounds(occurred_bounds, event.get("occurred_at_utc"))
        day_match = re.search(r"\d{4}-\d{2}-\d{2}", path.name)
        daily_files.append(
            {
                "day": day_match.group(0) if day_match else None,
                "bytes": path.stat().st_size,
                "records": file_records,
                "invalid_records": file_invalid_records,
            }
        )

    mtimes = [path.stat().st_mtime for path in files]
    return {
        "available": bool(files),
        "file_count": len(files),
        "bytes": sum(path.stat().st_size for path in files),
        "daily_files": daily_files,
        "records": records,
        "invalid_records": invalid_records,
        "backfill_records": backfill_records,
        "source_count": len(source_keys),
        "records_per_source_sorted": sorted(events_per_source.values()),
        "event_type_counts": dict(sorted(event_types.items())),
        "schema_version_counts": dict(sorted(schema_versions.items())),
        "occurred_at_utc": {"minimum": occurred_bounds[0], "maximum": occurred_bounds[1]},
        "available_at_utc": {"minimum": available_bounds[0], "maximum": available_bounds[1]},
        "newest_file_mtime_utc": (
            dt.datetime.fromtimestamp(max(mtimes), UTC).isoformat().replace("+00:00", "Z")
            if mtimes
            else None
        ),
        "quality_metric_availability": {
            "duplicate_rate": False,
            "gap_count": False,
            "reason": "not encoded in durable event spool",
        },
    }


def summarize_jsonl_streams(specs: Iterable[tuple[str, str]]) -> dict[str, Any]:
    return {
        label: summarize_jsonl_files(Path(path) for path in glob.glob(pattern))
        for label, pattern in specs
    }


HEARTBEAT_ROOT_FIELDS = (
    "capture_sequence",
    "connected",
    "last_durable_append_utc",
    "observed_at_utc",
    "outbox_count",
    "reconcile_complete",
    "schema",
    "version",
)
HEARTBEAT_SOURCE_FIELDS = (
    "created",
    "deleted",
    "duplicate",
    "edited",
    "last_available_at_utc",
    "last_event_at_utc",
    "last_live_lag_seconds",
    "recovered",
    "snapshots",
)


def summarize_heartbeat(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"available": False}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        return {
            "available": True,
            "readable": False,
            "error_type": type(error).__name__,
        }
    if not isinstance(payload, dict):
        return {"available": True, "readable": False, "error_type": "InvalidShape"}
    result: dict[str, Any] = {
        "available": True,
        "readable": True,
        "mtime_utc": dt.datetime.fromtimestamp(path.stat().st_mtime, UTC)
        .isoformat()
        .replace("+00:00", "Z"),
    }
    for field in HEARTBEAT_ROOT_FIELDS:
        value = payload.get(field)
        if isinstance(value, (str, int, float, bool)) or value is None:
            result[field] = value
    sources = payload.get("sources")
    if isinstance(sources, dict):
        source_summaries: dict[str, dict[str, Any]] = {}
        for source_code, source in sorted(sources.items()):
            if not isinstance(source_code, str) or not re.fullmatch(
                r"[A-Z0-9_]{1,64}", source_code
            ):
                continue
            if not isinstance(source, dict):
                continue
            source_summaries[source_code] = {
                field: source.get(field)
                for field in HEARTBEAT_SOURCE_FIELDS
                if isinstance(source.get(field), (str, int, float, bool))
                or source.get(field) is None
            }
        result["sources"] = source_summaries
    return result


def summarize_heartbeats(specs: Iterable[tuple[str, str]]) -> dict[str, Any]:
    return {label: summarize_heartbeat(Path(path)) for label, path in specs}


MODEL_ROOT_FIELDS = (
    "generated_at_utc",
    "is_llm",
    "model_kind",
    "price_unit",
    "schema_version",
    "service_status",
    "window_end_utc",
    "window_start_utc",
)
COLLECTOR_HEALTH_FIELDS = (
    "heartbeat_age_seconds",
    "heartbeat_at_utc",
    "last_success_at_utc",
    "max_age_seconds",
    "probe_status",
    "reason_code",
    "status",
)
MODEL_INPUT_HEALTH_FIELDS = (
    "historical_commodity_count",
    "importance",
    "latest_observation_age_seconds",
    "latest_observation_utc",
    "live_commodity_count",
    "settlements",
    "status",
)
SHADOW_FIELDS = (
    "authoritative_override",
    "comparison_vs_live",
    "enabled",
    "label",
    "light_mode",
    "shadow_model_kind",
    "shared_live_window_end_utc",
    "status",
)


def _copy_scalar_fields(payload: Mapping[str, Any], fields: Iterable[str]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for field in fields:
        value = payload.get(field)
        if isinstance(value, (str, int, float, bool)) or value is None:
            result[field] = value
        elif isinstance(value, list) and all(
            isinstance(item, (str, int, float, bool)) or item is None for item in value
        ):
            result[field] = value
        elif field == "comparison_vs_live" and isinstance(value, dict):
            result[field] = {
                key: child
                for key, child in value.items()
                if key
                in {
                    "mean_abs_pct",
                    "mean_signed_pct",
                    "median_abs_pct",
                    "paired_estimated_count",
                }
                and (isinstance(child, (int, float)) or child is None)
            }
    return result


def summarize_model_state(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"available": False}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        return {
            "available": True,
            "readable": False,
            "error_type": type(error).__name__,
        }
    if not isinstance(payload, dict):
        return {"available": True, "readable": False, "error_type": "InvalidShape"}
    result = {
        "available": True,
        "readable": True,
        "mtime_utc": dt.datetime.fromtimestamp(path.stat().st_mtime, UTC)
        .isoformat()
        .replace("+00:00", "Z"),
        **_copy_scalar_fields(payload, MODEL_ROOT_FIELDS),
    }
    health = payload.get("input_health")
    if isinstance(health, dict):
        health_result = _copy_scalar_fields(
            health,
            ("schema_version", "status", "evaluated_at_utc"),
        )
        for section, allowed_fields in (
            ("collectors", COLLECTOR_HEALTH_FIELDS),
            ("model_inputs", MODEL_INPUT_HEALTH_FIELDS),
        ):
            entries = health.get(section)
            if isinstance(entries, dict):
                health_result[section] = {
                    code: _copy_scalar_fields(entry, allowed_fields)
                    for code, entry in sorted(entries.items())
                    if isinstance(code, str)
                    and re.fullmatch(r"[a-z0-9_]{1,64}", code)
                    and isinstance(entry, dict)
                }
        result["input_health"] = health_result
    for section in ("shadow_parallel", "research_shadow_parallel", "ml_shadow_parallel"):
        value = payload.get(section)
        if isinstance(value, dict):
            result[section] = _copy_scalar_fields(value, SHADOW_FIELDS)
    for section, fields in (
        (
            "live_group_event_control",
            ("enabled", "status", "disabled_since_utc", "historical_group_data_enabled"),
        ),
        (
            "live_group_input_control",
            ("enabled", "disabled_since_utc", "changed_at_utc", "last_applied_at_utc"),
        ),
        (
            "market_regime_input",
            ("method", "canonical_store_available", "canonical_store_required"),
        ),
    ):
        value = payload.get(section)
        if isinstance(value, dict):
            result[section] = _copy_scalar_fields(value, fields)
    return result


def summarize_model_states(specs: Iterable[tuple[str, str]]) -> dict[str, Any]:
    return {label: summarize_model_state(Path(path)) for label, path in specs}


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def summarize_sqlite(
    path: Path,
    tables: Iterable[tuple[str, str | None]],
) -> dict[str, Any]:
    if not path.is_file():
        return {"available": False}
    result: dict[str, Any] = {
        "available": True,
        "bytes": path.stat().st_size,
        "tables": {},
    }
    try:
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
    except sqlite3.Error as error:
        result.update({"readable": False, "error_type": type(error).__name__})
        return result

    result["readable"] = True
    try:
        existing = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        for table, time_column in tables:
            if table not in existing:
                result["tables"][table] = {"available": False}
                continue
            quoted_table = _quote_identifier(table)
            try:
                row_count = connection.execute(
                    f"SELECT COUNT(*) FROM {quoted_table}"
                ).fetchone()[0]
                table_result: dict[str, Any] = {
                    "available": True,
                    "rows": int(row_count),
                }
                if time_column:
                    columns = {
                        row[1]
                        for row in connection.execute(f"PRAGMA table_info({quoted_table})")
                    }
                    if time_column in columns:
                        quoted_time = _quote_identifier(time_column)
                        minimum, maximum = connection.execute(
                            f"SELECT MIN({quoted_time}), MAX({quoted_time}) FROM {quoted_table}"
                        ).fetchone()
                        table_result["time_bounds"] = {
                            "column": time_column,
                            "minimum": minimum,
                            "maximum": maximum,
                        }
                result["tables"][table] = table_result
            except sqlite3.Error as error:
                result["tables"][table] = {
                    "available": True,
                    "readable": False,
                    "error_type": type(error).__name__,
                }
    finally:
        connection.close()
    return result


def summarize_databases(specs: Iterable[tuple[str, str, Any]]) -> dict[str, Any]:
    return {
        label: summarize_sqlite(Path(path), tables)
        for label, path, tables in specs
    }


def assert_sanitized(value: Any, path: tuple[str, ...] = ()) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized = str(key).lower()
            if normalized in FORBIDDEN_OUTPUT_KEYS:
                raise ValueError(f"forbidden output field: {'.'.join(path + (normalized,))}")
            assert_sanitized(child, path + (normalized,))
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            assert_sanitized(child, path + (str(index),))


def build_inventory(role: str, repo: Path) -> dict[str, Any]:
    config = ROLE_CONFIG[role]
    inventory = {
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": utc_now(),
        "role": role,
        "host": {
            "hostname": platform.node(),
            "kernel": platform.release(),
            "machine": platform.machine(),
        },
        "git": summarize_git(repo),
        "disks": summarize_disks(role),
        "services": summarize_units(config["services"]),
        "timers": summarize_units(config["timers"]),
        "containers": summarize_containers(config["containers"]),
        "storage_roots": summarize_storage_roots(config["storage_roots"]),
        "jsonl_streams": summarize_jsonl_streams(config["jsonl_streams"]),
        "heartbeats": summarize_heartbeats(config["heartbeats"]),
        "model_states": summarize_model_states(config["model_states"]),
        "databases": summarize_databases(config["databases"]),
    }
    assert_sanitized(inventory)
    return inventory


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--role", choices=sorted(ROLE_CONFIG), required=True)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--pretty", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    inventory = build_inventory(args.role, args.repo.resolve())
    print(
        json.dumps(
            inventory,
            ensure_ascii=False,
            indent=2 if args.pretty else None,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
