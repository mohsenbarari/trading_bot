#!/usr/bin/env python3
"""Collect independent per-hop synchronization timing from three staging hosts.

The workload doer supplies a correlation manifest.  This observer never writes
business data: it measures each host clock, runs the least-privilege snapshot
container, correlates all three journals, and refuses to emit evidence until
every required event is applied and acknowledged.
"""

from __future__ import annotations

import argparse
import base64
import copy
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import time
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.secure_file_io import read_secure_text, write_secure_atomic_bytes
from core.three_site_full_matrix_campaign import secure_json
from core.three_site_sync_timing import SyncTimingEvidenceError, sync_timing_policy
from scripts.build_three_site_sync_timing_evidence import (
    MANIFEST_SCHEMA,
    TimingBuildError,
    build,
)
from scripts.measure_three_site_host_clock import SCHEMA as CLOCK_SCHEMA
from scripts.verify_three_site_staging_inventory import verify_inventory


CONFIG_SCHEMA = "three-site-staging-sync-observer-config-v1"
SITES = ("bot_fi", "webapp_fi", "webapp_ir")
SITE_PROFILE = {"bot_fi": "bot-fi", "webapp_fi": "webapp-fi", "webapp_ir": "webapp-ir"}
SITE_SERVICE = {
    "bot_fi": "bot_fi_sync_observer",
    "webapp_fi": "webapp_fi_sync_observer",
    "webapp_ir": "webapp_ir_sync_observer",
}
SAFE_NAME = re.compile(r"^[a-z_][a-z0-9_-]{0,62}$")
SAFE_PATH = re.compile(r"^/[A-Za-z0-9_./-]{1,511}$")
SAFE_CORRELATION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{15,127}$")
SHA40 = re.compile(r"^[0-9a-f]{40}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
SAFE_ENV = {
    "PATH": "/usr/bin:/bin",
    "HOME": "/nonexistent",
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
}


class SyncObserverError(RuntimeError):
    pass


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise SyncObserverError(f"duplicate observer config field: {key}")
        result[key] = value
    return result


def _secure_regular_file(path: Path, *, private: bool, label: str) -> bytes:
    if not path.is_absolute() or path.is_symlink():
        raise SyncObserverError(f"{label} path is unsafe")
    try:
        metadata = path.stat()
        descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0))
    except OSError as exc:
        raise SyncObserverError(f"{label} cannot be opened") from exc
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_uid != os.geteuid()
            or stat.S_IMODE(opened.st_mode) & (0o077 if private else 0o022)
            or opened.st_dev != metadata.st_dev
            or opened.st_ino != metadata.st_ino
            or opened.st_size != metadata.st_size
            or opened.st_nlink != 1
            or opened.st_size <= 0
            or opened.st_size > 4 * 1024 * 1024
        ):
            raise SyncObserverError(f"{label} ownership/mode is unsafe")
        value = os.pread(descriptor, opened.st_size + 1, 0)
    finally:
        os.close(descriptor)
    if len(value) != metadata.st_size:
        raise SyncObserverError(f"{label} changed while being read")
    return value


def load_config(path: Path, *, campaign_id: str, release_sha: str, output: Path) -> dict[str, Any]:
    try:
        config = json.loads(
            read_secure_text(path, label="sync observer config", max_size=1024 * 1024),
            object_pairs_hook=_strict_object,
        )
    except Exception as exc:
        raise SyncObserverError("sync observer config is invalid") from exc
    fields = {
        "schema", "campaign_id", "release_sha", "production_forbidden",
        "artifact_root", "inventory", "ssh", "sites", "limits",
    }
    if (
        not isinstance(config, dict)
        or set(config) != fields
        or config.get("schema") != CONFIG_SCHEMA
        or config.get("campaign_id") != campaign_id
        or config.get("release_sha") != release_sha
        or config.get("production_forbidden") is not True
    ):
        raise SyncObserverError("sync observer identity/scope is invalid")
    artifact_root = Path(str(config["artifact_root"]))
    if (
        not artifact_root.is_absolute()
        or artifact_root.is_symlink()
        or output.resolve().parent != artifact_root.resolve()
    ):
        raise SyncObserverError("sync observer output is outside its approved artifact root")
    try:
        artifact_metadata = artifact_root.stat()
    except OSError as exc:
        raise SyncObserverError("sync observer artifact root is unavailable") from exc
    if (
        not stat.S_ISDIR(artifact_metadata.st_mode)
        or artifact_metadata.st_uid != os.geteuid()
        or stat.S_IMODE(artifact_metadata.st_mode) & 0o077
    ):
        raise SyncObserverError("sync observer artifact root must be owner-only")
    inventory_binding = config["inventory"]
    if not isinstance(inventory_binding, dict) or set(inventory_binding) != {"path", "sha256"}:
        raise SyncObserverError("sync observer inventory binding is invalid")
    inventory_path = Path(str(inventory_binding["path"]))
    inventory_bytes = _secure_regular_file(
        inventory_path, private=True, label="provisioned staging inventory"
    )
    if (
        SHA256.fullmatch(str(inventory_binding["sha256"])) is None
        or hashlib.sha256(inventory_bytes).hexdigest() != inventory_binding["sha256"]
    ):
        raise SyncObserverError("sync observer inventory hash differs")
    try:
        inventory = json.loads(
            inventory_bytes.decode("utf-8"), object_pairs_hook=_strict_object
        )
        verified_inventory = verify_inventory(inventory, host_destructive=None)
    except Exception as exc:
        raise SyncObserverError("sync observer inventory is invalid") from exc
    if (
        inventory.get("inventory_stage") != "provisioned"
        or inventory.get("campaign_id") != campaign_id
        or verified_inventory.get("release_sha") != release_sha
    ):
        raise SyncObserverError("sync observer inventory identity differs")
    ssh = config["ssh"]
    if not isinstance(ssh, dict) or set(ssh) != {
        "binary", "identity_file", "known_hosts_file", "connect_timeout_seconds"
    }:
        raise SyncObserverError("sync observer SSH config is invalid")
    if ssh["binary"] != "/usr/bin/ssh":
        raise SyncObserverError("sync observer requires the fixed OpenSSH client")
    _secure_regular_file(Path(ssh["binary"]), private=False, label="OpenSSH client")
    _secure_regular_file(Path(ssh["identity_file"]), private=True, label="SSH identity")
    _secure_regular_file(Path(ssh["known_hosts_file"]), private=False, label="known_hosts")
    if type(ssh["connect_timeout_seconds"]) is not int or not 1 <= ssh["connect_timeout_seconds"] <= 30:
        raise SyncObserverError("sync observer SSH timeout is invalid")
    limits = config["limits"]
    if not isinstance(limits, dict) or set(limits) != {
        "observation_timeout_seconds", "poll_interval_seconds", "command_timeout_seconds"
    }:
        raise SyncObserverError("sync observer limits are invalid")
    if (
        type(limits["observation_timeout_seconds"]) is not int
        or not 10 <= limits["observation_timeout_seconds"] <= 7200
        or not isinstance(limits["poll_interval_seconds"], (int, float))
        or isinstance(limits["poll_interval_seconds"], bool)
        or not 0.25 <= float(limits["poll_interval_seconds"]) <= 30.0
        or type(limits["command_timeout_seconds"]) is not int
        or not 10 <= limits["command_timeout_seconds"] <= 600
    ):
        raise SyncObserverError("sync observer limit value is invalid")
    sites = config["sites"]
    if not isinstance(sites, dict) or set(sites) != set(SITES):
        raise SyncObserverError("sync observer requires exactly three sites")
    addresses: set[str] = set()
    inventory_hosts = {
        str(item["role"]): str(item["host_ip"])
        for item in inventory["roles"]
        if item["role"] in SITES
    }
    for site, item in sites.items():
        if not isinstance(item, dict) or set(item) != {
            "host", "port", "user", "repo_root", "compose_file", "env_file"
        }:
            raise SyncObserverError("sync observer site config is invalid")
        try:
            address = str(ipaddress.ip_address(str(item["host"])))
        except ValueError as exc:
            raise SyncObserverError("sync observer host must be a literal inventory IP") from exc
        if address in addresses or ipaddress.ip_address(address).is_loopback:
            raise SyncObserverError("sync observer host is duplicate or loopback")
        if inventory_hosts.get(site) != address:
            raise SyncObserverError("sync observer host differs from signed staging inventory")
        addresses.add(address)
        if type(item["port"]) is not int or not 1 <= item["port"] <= 65535:
            raise SyncObserverError("sync observer SSH port is invalid")
        if SAFE_NAME.fullmatch(str(item["user"])) is None:
            raise SyncObserverError("sync observer SSH user is invalid")
        for key in ("repo_root", "compose_file", "env_file"):
            if SAFE_PATH.fullmatch(str(item[key])) is None or ".." in Path(str(item[key])).parts:
                raise SyncObserverError("sync observer remote path is unsafe")
        repo_root = str(item["repo_root"]).rstrip("/")
        if str(item["compose_file"]) != f"{repo_root}/deploy/staging/docker-compose.three-site.yml":
            raise SyncObserverError("sync observer Compose path is not canonical")
    return config


def _validate_manifest_scope(
    manifest: dict[str, Any],
    *,
    config: dict[str, Any],
    scenario_id: str,
    policy: dict[str, Any],
) -> str:
    fields = {
        "schema", "scenario_id", "release_sha", "state", "correlation_prefix",
        "captured_at", "load", "samples", "backlog",
    }
    prefix = str(manifest.get("correlation_prefix") or "")
    if (
        not isinstance(manifest, dict)
        or set(manifest) != fields
        or manifest.get("schema") != MANIFEST_SCHEMA
        or manifest.get("scenario_id") != scenario_id
        or manifest.get("release_sha") != config.get("release_sha")
        or manifest.get("state") != policy["state"]
        or SAFE_CORRELATION.fullmatch(prefix) is None
        or not isinstance(manifest.get("samples"), list)
        or not manifest["samples"]
    ):
        raise SyncObserverError("sync probe manifest identity/scope is invalid")
    for sample in manifest["samples"]:
        if (
            not isinstance(sample, dict)
            or not isinstance(sample.get("correlation_id"), str)
            or not sample["correlation_id"].startswith(prefix)
            or SAFE_CORRELATION.fullmatch(sample["correlation_id"]) is None
        ):
            raise SyncObserverError("sync probe correlation is invalid")
    return prefix


def _ssh_base(config: dict[str, Any], site: str) -> list[str]:
    ssh = config["ssh"]
    target = config["sites"][site]
    return [
        ssh["binary"], "-F", "/dev/null", "-i", ssh["identity_file"],
        "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes",
        "-o", "StrictHostKeyChecking=yes",
        "-o", f"UserKnownHostsFile={ssh['known_hosts_file']}",
        "-o", f"ConnectTimeout={ssh['connect_timeout_seconds']}",
        "-p", str(target["port"]), "--", f"{target['user']}@{target['host']}",
    ]


def _run_json(arguments: list[str], *, timeout: int, label: str) -> dict[str, Any]:
    try:
        result = subprocess.run(
            arguments,
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            env=SAFE_ENV,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SyncObserverError(f"{label} did not complete") from exc
    if result.returncode != 0 or len(result.stdout.encode("utf-8")) > 32 * 1024 * 1024:
        raise SyncObserverError(f"{label} failed closed")
    try:
        value = json.loads(result.stdout, object_pairs_hook=_strict_object)
    except (json.JSONDecodeError, SyncObserverError) as exc:
        raise SyncObserverError(f"{label} returned invalid JSON") from exc
    if not isinstance(value, dict):
        raise SyncObserverError(f"{label} did not return an object")
    return value


def _clock(config: dict[str, Any], site: str) -> dict[str, Any]:
    remote = config["sites"][site]
    value = _run_json(
        [
            *_ssh_base(config, site),
            "/usr/bin/python3",
            f"{str(remote['repo_root']).rstrip('/')}/scripts/measure_three_site_host_clock.py",
            "--site", site,
            "--release-sha", config["release_sha"],
        ],
        timeout=config["limits"]["command_timeout_seconds"],
        label=f"{site} host clock measurement",
    )
    if value.get("schema") != CLOCK_SCHEMA or value.get("site") != site:
        raise SyncObserverError(f"{site} host clock identity differs")
    return value


def _snapshot(
    config: dict[str, Any],
    site: str,
    *,
    correlation_prefix: str,
    clock: dict[str, Any],
) -> dict[str, Any]:
    remote = config["sites"][site]
    clock_bytes = json.dumps(
        clock, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    encoded_clock = base64.urlsafe_b64encode(clock_bytes).decode("ascii").rstrip("=")
    return _run_json(
        [
            *_ssh_base(config, site),
            "/usr/bin/docker", "compose",
            "--project-directory", remote["repo_root"],
            "-f", remote["compose_file"],
            "--env-file", remote["env_file"],
            "--profile", SITE_PROFILE[site],
            "run", "--rm", "--no-deps", "-T", SITE_SERVICE[site],
            "python", "scripts/collect_three_site_sync_timing_snapshot.py",
            "--correlation-prefix", correlation_prefix,
            "--clock-evidence-base64", encoded_clock,
        ],
        timeout=config["limits"]["command_timeout_seconds"],
        label=f"{site} least-privilege timing snapshot",
    )


def observe(
    *,
    config: dict[str, Any],
    manifest: dict[str, Any],
    scenario_id: str,
) -> dict[str, Any]:
    policy = sync_timing_policy(scenario_id)
    if policy is None:
        raise SyncObserverError("scenario has no synchronization timing policy")
    prefix = _validate_manifest_scope(
        manifest,
        config=config,
        scenario_id=scenario_id,
        policy=policy,
    )
    deadline = time.monotonic() + config["limits"]["observation_timeout_seconds"]
    last_error = "no observation attempted"
    backlog_samples: list[dict[str, Any]] = []
    first_positive_at: float | None = None
    initial_oldest_age = 0.0
    peak_pending = 0
    first_event_ids: set[str] | None = None
    previous_at: float | None = None
    previous_event_count = 0
    previous_applied_count = 0
    while True:
        with ThreadPoolExecutor(max_workers=3) as pool:
            clocks = dict(zip(SITES, pool.map(lambda site: _clock(config, site), SITES)))
        with ThreadPoolExecutor(max_workers=3) as pool:
            snapshots = dict(
                zip(
                    SITES,
                    pool.map(
                        lambda site: _snapshot(
                            config,
                            site,
                            correlation_prefix=prefix,
                            clock=clocks[site],
                        ),
                        SITES,
                    ),
                )
            )
        observed_monotonic = time.monotonic()
        observed_at = datetime.now(timezone.utc)
        event_ids = {
            str(event["event_id"])
            for snapshot in snapshots.values()
            for event in snapshot.get("events", [])
        }
        applied_receipts = {
            (str(receipt["event_id"]), str(receipt["destination_site"]))
            for snapshot in snapshots.values()
            for receipt in snapshot.get("receipts", [])
            if receipt.get("status") == "applied"
        }
        pending = sum(
            int(snapshot.get("backlog", {}).get("pending_events") or 0)
            for snapshot in snapshots.values()
        )
        oldest_values = [
            datetime.fromisoformat(
                str(snapshot["backlog"]["oldest_pending_at"]).replace("Z", "+00:00")
            ).astimezone(timezone.utc)
            for snapshot in snapshots.values()
            if snapshot.get("backlog", {}).get("oldest_pending_at")
        ]
        oldest_age = max(
            0.0,
            (observed_at - min(oldest_values)).total_seconds() if oldest_values else 0.0,
        )
        elapsed = (
            max(0.000001, observed_monotonic - previous_at)
            if previous_at is not None
            else 0.0
        )
        ingress_rate = (
            max(0, len(event_ids) - previous_event_count) / elapsed if elapsed else 0.0
        )
        apply_rate = (
            max(0, len(applied_receipts) - previous_applied_count) / elapsed
            if elapsed else 0.0
        )
        sample = {
            "observed_at": observed_at.isoformat(),
            "pending_events": pending,
            "oldest_age_seconds": round(oldest_age, 6),
            "ingress_events_per_second": round(ingress_rate, 6),
            "apply_events_per_second": round(apply_rate, 6),
        }
        required_backlog = bool(policy["backlog_required"])
        if required_backlog and first_positive_at is None:
            if pending > 0:
                first_positive_at = observed_monotonic
                initial_oldest_age = oldest_age
                first_event_ids = set(event_ids)
                # Rates computed against a pre-backlog poll do not prove live
                # traffic during recovery.  Start the retained series here.
                sample["ingress_events_per_second"] = 0.0
                sample["apply_events_per_second"] = 0.0
                backlog_samples = [sample]
                peak_pending = pending
        elif required_backlog:
            backlog_samples.append(sample)
            peak_pending = max(peak_pending, pending)
        previous_at = observed_monotonic
        previous_event_count = len(event_ids)
        previous_applied_count = len(applied_receipts)
        observed_manifest = copy.deepcopy(manifest)
        observed_manifest["captured_at"] = observed_at.isoformat()
        if required_backlog:
            drain_duration = (
                observed_monotonic - first_positive_at
                if first_positive_at is not None and pending == 0
                else 0.0
            )
            observed_manifest["backlog"] = {
                "required": True,
                "peak_pending_events": peak_pending,
                "initial_oldest_age_seconds": round(initial_oldest_age, 6),
                "drained_to_zero": first_positive_at is not None and pending == 0,
                "drain_duration_seconds": round(drain_duration, 6),
                "live_ingress_events": len(event_ids - (first_event_ids or set())),
                "applied_events": len(applied_receipts),
                "samples": list(backlog_samples),
            }
        else:
            observed_manifest["backlog"] = {
                "required": False,
                "peak_pending_events": 0,
                "initial_oldest_age_seconds": 0.0,
                "drained_to_zero": True,
                "drain_duration_seconds": 0.0,
                "live_ingress_events": 0,
                "applied_events": len(applied_receipts),
                "samples": [],
            }
        try:
            return build(
                manifest=observed_manifest,
                snapshots=snapshots,
                scenario_id=scenario_id,
            )
        except (TimingBuildError, SyncTimingEvidenceError) as exc:
            last_error = str(exc)
        if time.monotonic() >= deadline:
            raise SyncObserverError(
                f"synchronization observation timed out before convergence: {last_error}"
            )
        time.sleep(float(config["limits"]["poll_interval_seconds"]))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign-id", required=True)
    parser.add_argument("--release-sha", required=True)
    parser.add_argument("--scenario-id", required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--probe-manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if SHA40.fullmatch(args.release_sha) is None:
        parser.error("--release-sha must be lowercase 40-hex")
    try:
        config = load_config(
            args.config,
            campaign_id=args.campaign_id,
            release_sha=args.release_sha,
            output=args.output,
        )
        manifest = secure_json(
            args.probe_manifest,
            label="sync probe manifest",
            max_size=16 * 1024 * 1024,
        )
        artifact = observe(
            config=config,
            manifest=manifest,
            scenario_id=args.scenario_id,
        )
        write_secure_atomic_bytes(
            args.output,
            (json.dumps(artifact, sort_keys=True, ensure_ascii=False) + "\n").encode(),
            label="three-site timing observer output",
            mode=0o600,
            max_size=32 * 1024 * 1024,
        )
        print(json.dumps({
            "status": "passed",
            "scenario_id": args.scenario_id,
            "sample_count": len(artifact["samples"]),
            "output_sha256": hashlib.sha256(args.output.read_bytes()).hexdigest(),
            "captured_at": datetime.now(timezone.utc).isoformat(),
        }, sort_keys=True))
        return 0
    except Exception as exc:
        print(json.dumps({
            "status": "blocked", "error": str(exc), "error_class": type(exc).__name__
        }, sort_keys=True), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
