#!/usr/bin/env python3
"""Build raw three-site timing evidence from independent role snapshots."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.secure_file_io import write_secure_atomic_bytes
from core.three_site_full_matrix_campaign import secure_json
from core.three_site_sync_timing import (
    ROUTE_HOPS,
    SAFE_ID,
    SHA256,
    SYNC_TIMING_SCHEMA,
    summarize_sync_timing_samples,
    sync_timing_policy,
    verify_sync_timing_evidence,
)
from scripts.collect_three_site_sync_timing_snapshot import SCHEMA as SITE_SCHEMA


MANIFEST_SCHEMA = "three-site-staging-sync-probe-manifest-v1"


class TimingBuildError(RuntimeError):
    pass


def _mapping(values: list[str]) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for value in values:
        site, separator, raw = value.partition("=")
        if not separator or site in result or site not in {"bot_fi", "webapp_fi", "webapp_ir"}:
            raise TimingBuildError("site snapshot mapping is invalid")
        result[site] = Path(raw)
    if set(result) != {"bot_fi", "webapp_fi", "webapp_ir"}:
        raise TimingBuildError("all three site snapshots are required")
    return result


def _one(items: list[dict[str, Any]], *, label: str) -> dict[str, Any]:
    if len(items) != 1:
        raise TimingBuildError(f"{label} requires exactly one observed row")
    return items[0]


def build(
    *,
    manifest: dict[str, Any],
    snapshots: dict[str, dict[str, Any]],
    scenario_id: str,
) -> dict[str, Any]:
    policy = sync_timing_policy(scenario_id)
    fields = {
        "schema", "scenario_id", "release_sha", "state", "correlation_prefix",
        "captured_at", "load", "samples", "backlog",
    }
    if (
        policy is None
        or not isinstance(manifest, dict)
        or set(manifest) != fields
        or manifest.get("schema") != MANIFEST_SCHEMA
        or manifest.get("scenario_id") != scenario_id
        or manifest.get("state") != policy["state"]
        or not isinstance(manifest.get("samples"), list)
        or SAFE_ID.fullmatch(str(manifest.get("correlation_prefix") or "")) is None
    ):
        raise TimingBuildError("sync probe manifest is invalid")
    prefix = str(manifest["correlation_prefix"])
    release_sha = str(manifest["release_sha"])
    if len(release_sha) != 40 or any(character not in "0123456789abcdef" for character in release_sha):
        raise TimingBuildError("sync probe release identity is invalid")
    if set(snapshots) != {"bot_fi", "webapp_fi", "webapp_ir"}:
        raise TimingBuildError("all three exact site snapshots are required")
    for site, snapshot in snapshots.items():
        if (
            snapshot.get("schema") != SITE_SCHEMA
            or snapshot.get("site") != site
            or snapshot.get("release_sha") != release_sha
            or snapshot.get("correlation_prefix") != prefix
        ):
            raise TimingBuildError("site timing snapshot identity differs")

    route_durations: dict[str, list[float]] = {route: [] for route in policy["routes"]}
    output_samples = []
    seen_correlations: set[str] = set()
    for sample in manifest["samples"]:
        if not isinstance(sample, dict) or set(sample) != {
            "sample_id", "correlation_id", "route",
            "controller_observed_duration_seconds",
        }:
            raise TimingBuildError("probe sample manifest is invalid")
        correlation = str(sample["correlation_id"])
        route = str(sample["route"])
        if (
            correlation in seen_correlations
            or not correlation.startswith(prefix)
            or SAFE_ID.fullmatch(correlation) is None
            or route not in route_durations
        ):
            raise TimingBuildError("probe sample correlation/route is invalid")
        seen_correlations.add(correlation)
        expected_hops = ROUTE_HOPS[route]
        expected_origin = expected_hops[0][0]
        first_origin_created: str | None = None
        event_id: str | None = None
        envelope_hash: str | None = None
        hops: list[dict[str, Any]] = []
        for source, destination in expected_hops:
            source_snapshot = snapshots[source]
            destination_snapshot = snapshots[destination]
            event = _one(
                [
                    item for item in source_snapshot["events"]
                    if item.get("correlation_id") == correlation
                ],
                label=f"{correlation} source event at {source}",
            )
            if event_id is None:
                event_id = str(event["event_id"])
                envelope_hash = str(event["envelope_hash"])
                first_origin_created = str(event["created_at"])
            elif (
                event.get("event_id") != event_id
                or event.get("envelope_hash") != envelope_hash
            ):
                raise TimingBuildError("relay changed event identity or envelope")
            delivery = _one(
                [
                    item for item in source_snapshot["deliveries"]
                    if item.get("event_id") == event_id
                    and item.get("destination_site") == destination
                ],
                label=f"{correlation} delivery {source}->{destination}",
            )
            receipt = _one(
                [
                    item for item in destination_snapshot["receipts"]
                    if item.get("event_id") == event_id
                    and item.get("destination_site") == destination
                    and item.get("received_from_site") == source
                ],
                label=f"{correlation} receipt at {destination}",
            )
            if (
                event.get("origin_physical_site") != expected_origin
                or delivery.get("relay_site") != source
                or SHA256.fullmatch(str(delivery.get("acknowledgement_hash") or "")) is None
                or receipt.get("origin_physical_site") != expected_origin
                or receipt.get("envelope_hash") != envelope_hash
                or receipt.get("relay_site")
                != (source if source != expected_origin else None)
            ):
                raise TimingBuildError("probe route identity/hash evidence differs")
            if (
                delivery.get("status") != "acknowledged"
                or receipt.get("status") != "applied"
                or not delivery.get("first_attempt_at")
                or not delivery.get("acknowledged_at")
                or not receipt.get("received_at")
                or not receipt.get("applied_at")
                or not first_origin_created
            ):
                raise TimingBuildError("probe did not reach applied-and-acknowledged state")
            hops.append(
                {
                    "source_site": source,
                    "destination_site": destination,
                    "source_event_id": event_id,
                    "source_envelope_hash": envelope_hash,
                    "destination_receipt_hash": receipt["receipt_hash"],
                    "origin_event_created_at": first_origin_created,
                    "delivery_enqueued_at": delivery["enqueued_at"],
                    "first_attempt_at": delivery["first_attempt_at"],
                    "destination_received_at": receipt["received_at"],
                    "destination_applied_at": receipt["applied_at"],
                    "source_acknowledged_at": delivery["acknowledged_at"],
                    "attempt_count": delivery["attempt_count"],
                    "payload_bytes": event["payload_bytes"],
                }
            )
        duration = float(sample["controller_observed_duration_seconds"])
        route_durations[route].append(duration)
        output_samples.append(
            {
                "sample_id": sample["sample_id"],
                "correlation_id": correlation,
                "route": route,
                "state": policy["state"],
                "hops": hops,
                "controller_observed_duration_seconds": duration,
                "status": "applied_and_acknowledged",
            }
        )

    captured_at = str(manifest["captured_at"])
    clocks = {site: snapshots[site]["clock"] for site in snapshots}
    maximum_offset = max(abs(float(value["offset_ms"])) for value in clocks.values())
    artifact = {
        "schema": SYNC_TIMING_SCHEMA,
        "scenario_id": scenario_id,
        "state": policy["state"],
        "captured_at": captured_at,
        "clock": {
            "basis": "utc-ntp-plus-controller-monotonic",
            "maximum_absolute_offset_ms": maximum_offset,
            "sites": clocks,
        },
        "load": manifest["load"],
        "samples": output_samples,
        "backlog": manifest["backlog"],
        "summary": summarize_sync_timing_samples(output_samples),
    }
    verify_sync_timing_evidence(artifact, scenario_id=scenario_id)
    return artifact


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario-id", required=True)
    parser.add_argument("--probe-manifest", type=Path, required=True)
    parser.add_argument("--site-snapshot", action="append", default=[])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    snapshots = {
        site: secure_json(path, label=f"{site} timing snapshot", max_size=32 * 1024 * 1024)
        for site, path in _mapping(args.site_snapshot).items()
    }
    artifact = build(
        manifest=secure_json(
            args.probe_manifest,
            label="sync probe manifest",
            max_size=16 * 1024 * 1024,
        ),
        snapshots=snapshots,
        scenario_id=args.scenario_id,
    )
    write_secure_atomic_bytes(
        args.output,
        (json.dumps(artifact, ensure_ascii=False, sort_keys=True) + "\n").encode(),
        label="three-site synchronization timing evidence",
        mode=0o600,
        max_size=32 * 1024 * 1024,
    )
    print(json.dumps({"status": "passed", "sample_count": len(artifact["samples"])}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
