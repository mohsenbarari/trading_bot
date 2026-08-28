#!/usr/bin/env python3
"""Fail-closed, value-free verifier for production PRIVATE_PRIMARY promotion.

This command is deliberately read-only with respect to product/runtime state.
It consumes root-protected observations prepared on the Bot and Web hosts,
the two blue/green journals, the two release environments, and the two
receiver-acknowledged Web-view snapshots.  Its sole write is one exclusive,
owner-only receipt.  It never invokes Docker, SSH, HTTP, a database, or a
deployment command.

The health artifacts use ``production_private_primary_observation/1.0``.  They
contain only release/container identities, zero-valued safety counters,
per-stream sequence watermarks, and snapshot identities; raw Market facts,
Telegram data, prices, environment values, paths, or credentials are not part
of that contract.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import stat
import sys
from typing import Any, Mapping, Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pydantic import ValidationError

from core.market_intelligence.private_pipeline_contracts import (
    ESTIMATOR_RATE_GRID_V1,
    EstimatorSnapshotV2,
)


CONFIRMATION = "verify-production-private-primary-promotion"
RECEIPT_SCHEMA = "production_private_primary_promotion_verification/1.0"
OBSERVATION_SCHEMA = "production_private_primary_observation/1.0"
JOURNAL_SCHEMA = "market_pipeline_bluegreen_upgrade/1.0"
WEB_VIEW_CONTRACT = "estimator_snapshot_web_view/1.0"
CATCHUP_RECEIPT_SCHEMA = "production_market_catchup_verification/1.2"
MAXIMUM_AGE_SECONDS = 120

HEX40 = re.compile(r"^[0-9a-f]{40}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
IMAGE_ID = re.compile(r"^sha256:[0-9a-f]{64}$")
PROJECT_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{2,62}$")
STREAM_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,160}$")

BOT_SERVICES = frozenset(
    {
        "market-fact-receiver",
        "market-store-adapter",
        "coin-estimator",
        "estimator-snapshot-sender",
    }
)
WEB_SERVICES = frozenset(
    {
        "market-database",
        "market-capture-account1",
        "market-capture-account2",
        "market-capture-external",
        "market-processor",
        "market-fact-sync-worker",
        "estimator-snapshot-receiver",
    }
)
COUNTER_FIELDS = frozenset(
    {
        "duplicate",
        "rejected",
        "dead_letter",
        "open_outbox",
        "receiver_publication_pending",
    }
)
OBSERVATION_FIELDS = frozenset(
    {
        "schema",
        "role",
        "observed_at_utc",
        "release_sha",
        "release_tree",
        "project_name",
        "image_id",
        "owners",
        "legacy_owner_count",
        "unexpected_owner_count",
        "sequences",
        "counts",
        "snapshot",
        "secrets_disclosed",
    }
)
OWNER_FIELDS = frozenset(
    {
        "count",
        "release_sha",
        "release_tree",
        "project_name",
        "image_id",
        "healthy",
    }
)
SNAPSHOT_IDENTITY_FIELDS = frozenset(
    {
        "contract",
        "snapshot_hash",
        "snapshot_version",
        "feed_mode",
        "snapshot_status",
        "estimated_rate_count",
        "file_sha256",
    }
)
ENV_BINDING_FIELDS = frozenset(
    {
        "MARKET_PIPELINE_RELEASE_SHA",
        "MARKET_PIPELINE_IMAGE",
        "MARKET_PIPELINE_PROJECT_NAME",
        "MARKET_PIPELINE_FEED_MODE",
        "MARKET_PIPELINE_ALLOW_PRIVATE_PRIMARY",
        "MARKET_PIPELINE_EXPECTED_SNAPSHOT_LANE",
    }
)
WEB_BACKFILL_ENV_BINDING_FIELDS = frozenset(
    {
        "MARKET_CAPTURE_BACKFILL_NOT_BEFORE_UTC",
        "MARKET_CAPTURE_BACKFILL_SOURCE_CODES",
        "MARKET_CAPTURE_BACKFILL_MAX_MESSAGES",
    }
)
AUTHORIZED_BACKFILL_NOT_BEFORE_UTC = "2026-08-25T09:33:00Z"
AUTHORIZED_BACKFILL_SOURCE_CODES = (
    "MELTED_PRIMARY_FLOW,GROUP_1,GROUP_2"
)
AUTHORIZED_CATCHUP_BACKFILL_SOURCES = (
    "GROUP_1",
    "GROUP_2",
    "MELTED_PRIMARY_FLOW",
)
AUTHORIZED_CATCHUP_SOURCE_INVENTORY = (
    "BINANCE_PAXG_PUBLIC_API",
    "GROUP_1",
    "GROUP_2",
    "MELTED_AGGREGATE",
    "MELTED_FLOW",
    "MELTED_PRIMARY_FLOW",
    "USD_HERAT",
    "WALLEX_PUBLIC_API",
    "XAUUSD",
)
CHECKS = (
    "release_and_image_binding",
    "bluegreen_journals_pass",
    "single_owner_topology",
    "contiguous_sequences_and_ack",
    "idempotent_duplicates_and_zero_rejected_dead_open_outbox",
    "receiver_publication_settled",
    "private_primary_snapshot_contract",
    "fourteen_estimated_rates",
    "effective_underlying_freshness",
    "bot_web_snapshot_identity_and_digest",
    "owner_authorized_backfill_scope_bound",
    "catchup_complete_and_live_tail_verified",
)


class PromotionVerificationError(RuntimeError):
    """A stable, value-free verification refusal."""

    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


def _fail(reason_code: str) -> None:
    raise PromotionVerificationError(reason_code)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _time(value: object, *, reason: str) -> datetime:
    if not isinstance(value, str):
        _fail(reason)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        _fail(reason)
    if parsed.tzinfo is None:
        _fail(reason)
    return parsed.astimezone(timezone.utc)


def _age(value: datetime, *, now: datetime, maximum: int, reason: str) -> float:
    seconds = (now - value).total_seconds()
    if seconds < 0 or seconds > float(maximum):
        _fail(reason)
    return round(seconds, 3)


def _read_secure_bytes(
    path: Path,
    *,
    root_owned: bool,
    maximum_bytes: int,
    required_mode: int | None = None,
    required_mode_reason: str = "artifact_owner_mode_or_type_invalid",
) -> tuple[bytes, str]:
    if not path.is_absolute():
        _fail("artifact_path_not_absolute")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError:
        _fail("artifact_unavailable")
    try:
        before = os.fstat(descriptor)
        try:
            path_info = path.lstat()
        except OSError:
            _fail("artifact_changed_during_read")
        allowed_uids = {os.geteuid()} if root_owned else {0, 10001, os.geteuid()}
        if (
            path.is_symlink()
            or not stat.S_ISREG(before.st_mode)
            or before.st_uid not in allowed_uids
            or stat.S_IMODE(before.st_mode) not in {0o400, 0o600}
            or before.st_nlink != 1
            or not 0 < before.st_size <= maximum_bytes
            or path_info.st_dev != before.st_dev
            or path_info.st_ino != before.st_ino
        ):
            _fail("artifact_owner_mode_or_type_invalid")
        if (
            required_mode is not None
            and stat.S_IMODE(before.st_mode) != required_mode
        ):
            _fail(required_mode_reason)
        chunks: list[bytes] = []
        remaining = maximum_bytes + 1
        while remaining:
            chunk = os.read(descriptor, min(131072, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        after = os.fstat(descriptor)
        try:
            final_path = path.lstat()
        except OSError:
            _fail("artifact_changed_during_read")
        stable = (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        ) == (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        )
        if (
            not stable
            or final_path.st_dev != before.st_dev
            or final_path.st_ino != before.st_ino
            or len(payload) != before.st_size
            or len(payload) > maximum_bytes
        ):
            _fail("artifact_changed_during_read")
        return payload, sha256(payload).hexdigest()
    finally:
        os.close(descriptor)


def _read_json(
    path: Path,
    *,
    root_owned: bool = True,
    maximum_bytes: int = 2_000_000,
) -> tuple[Mapping[str, Any], str]:
    payload, digest = _read_secure_bytes(
        path, root_owned=root_owned, maximum_bytes=maximum_bytes
    )
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        _fail("artifact_json_invalid")
    if not isinstance(value, Mapping):
        _fail("artifact_json_invalid")
    return value, digest


def _validate_catchup_receipt(
    path: Path,
    *,
    expected_sha256: str,
    release_sha: str,
    now: datetime,
    maximum_age_seconds: int,
) -> dict[str, object]:
    if not HEX64.fullmatch(expected_sha256 or ""):
        _fail("catchup_receipt_digest_invalid")
    payload, actual_sha256 = _read_secure_bytes(
        path,
        root_owned=True,
        maximum_bytes=2_000_000,
        required_mode=0o600,
        required_mode_reason="catchup_receipt_owner_mode_invalid",
    )
    if actual_sha256 != expected_sha256:
        _fail("catchup_receipt_cas_mismatch")
    try:
        value = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError):
        _fail("catchup_receipt_json_invalid")
    if not isinstance(value, Mapping):
        _fail("catchup_receipt_json_invalid")
    verified_at = _time(
        value.get("verified_at_utc"), reason="catchup_receipt_time_invalid"
    )
    age_seconds = _age(
        verified_at,
        now=now,
        maximum=maximum_age_seconds,
        reason="catchup_receipt_stale_or_future",
    )
    evidence = value.get("evidence_artifacts")
    if not isinstance(evidence, Mapping) or set(evidence) != {
        "previous_web",
        "previous_bot",
        "web",
        "bot",
    }:
        _fail("catchup_receipt_contract_invalid")
    evidence_times: dict[str, datetime] = {}
    for label, artifact in evidence.items():
        if (
            not isinstance(artifact, Mapping)
            or set(artifact) != {"sha256", "observed_at_utc"}
            or HEX64.fullmatch(str(artifact.get("sha256") or "")) is None
        ):
            _fail("catchup_receipt_contract_invalid")
        observed = _time(
            artifact.get("observed_at_utc"), reason="catchup_receipt_time_invalid"
        )
        evidence_times[str(label)] = observed
        evidence_age = (verified_at - observed).total_seconds()
        if evidence_age < -5 or evidence_age > 300:
            _fail("catchup_receipt_contract_invalid")
    evidence_window = (
        min(evidence_times["web"], evidence_times["bot"])
        - max(evidence_times["previous_web"], evidence_times["previous_bot"])
    ).total_seconds()
    if not 20 <= evidence_window <= 300:
        _fail("catchup_receipt_contract_invalid")
    evidence_binding = sha256(
        (
            json.dumps(
                evidence,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("ascii")
    ).hexdigest()

    def zero_integer(field: str) -> bool:
        item = value.get(field)
        return not isinstance(item, bool) and isinstance(item, int) and item == 0

    if (
        value.get("schema") != CATCHUP_RECEIPT_SCHEMA
        or value.get("status") != "PASS"
        or value.get("release_sha") != release_sha
        or value.get("cutoff_utc") != AUTHORIZED_BACKFILL_NOT_BEFORE_UTC
        or value.get("backfill_sources")
        != list(AUTHORIZED_CATCHUP_BACKFILL_SOURCES)
        or value.get("live_source_inventory")
        != list(AUTHORIZED_CATCHUP_SOURCE_INVENTORY)
        or value.get("live_tail_observed") is not True
        or not zero_integer("internal_sequence_gaps")
        or not zero_integer("unresolved_quarantines")
        or not zero_integer("unresolved_rejections")
        or value.get("upstream_time_gaps_allowed") is not True
        or value.get("secrets_disclosed") is not False
        or value.get("evidence_binding_sha256") != evidence_binding
    ):
        _fail("catchup_receipt_contract_invalid")
    return {
        "receipt_sha256": actual_sha256,
        "age_seconds": age_seconds,
    }


def _read_env_binding(path: Path, *, role: str) -> tuple[dict[str, str], str]:
    payload, digest = _read_secure_bytes(
        path, root_owned=True, maximum_bytes=1_000_000
    )
    selected_fields = ENV_BINDING_FIELDS | (
        WEB_BACKFILL_ENV_BINDING_FIELDS if role == "web" else frozenset()
    )
    binding: dict[str, str] = {}
    seen: set[str] = set()
    try:
        lines = payload.decode("utf-8").splitlines()
    except UnicodeDecodeError:
        _fail("runtime_env_invalid")
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            _fail("runtime_env_invalid")
        key, value = line.split("=", 1)
        if not key or key in seen:
            _fail("runtime_env_invalid")
        seen.add(key)
        if key in selected_fields:
            binding[key] = value
    if set(binding) != set(selected_fields):
        _fail("runtime_env_binding_incomplete")
    return binding, digest


def _validate_expected_identity(
    *,
    release_sha: str,
    release_tree: str,
    bot_image_id: str,
    web_image_id: str,
    maximum_age_seconds: int,
) -> None:
    if not HEX40.fullmatch(release_sha) or not HEX40.fullmatch(release_tree):
        _fail("expected_git_identity_invalid")
    if not IMAGE_ID.fullmatch(bot_image_id) or not IMAGE_ID.fullmatch(web_image_id):
        _fail("expected_image_identity_invalid")
    if maximum_age_seconds != MAXIMUM_AGE_SECONDS:
        _fail("maximum_age_contract_invalid")


def _validate_env(
    path: Path,
    *,
    role: str,
    release_sha: str,
    image_id: str,
) -> tuple[dict[str, str], str]:
    value, digest = _read_env_binding(path, role=role)
    if (
        value["MARKET_PIPELINE_RELEASE_SHA"] != release_sha
        or value["MARKET_PIPELINE_IMAGE"] != image_id
        or not PROJECT_NAME.fullmatch(value["MARKET_PIPELINE_PROJECT_NAME"])
        or value["MARKET_PIPELINE_FEED_MODE"] != "PRIVATE_PRIMARY"
        or value["MARKET_PIPELINE_ALLOW_PRIVATE_PRIMARY"] != "1"
        or value["MARKET_PIPELINE_EXPECTED_SNAPSHOT_LANE"] != "PRIVATE_PRIMARY"
    ):
        _fail("runtime_env_release_or_lane_mismatch")
    if role == "web":
        try:
            maximum = int(value["MARKET_CAPTURE_BACKFILL_MAX_MESSAGES"])
        except (KeyError, ValueError):
            _fail("authorized_backfill_contract_invalid")
        if (
            value.get("MARKET_CAPTURE_BACKFILL_NOT_BEFORE_UTC")
            != AUTHORIZED_BACKFILL_NOT_BEFORE_UTC
            or value.get("MARKET_CAPTURE_BACKFILL_SOURCE_CODES")
            != AUTHORIZED_BACKFILL_SOURCE_CODES
            or not 2_000 <= maximum <= 250_000
        ):
            _fail("authorized_backfill_contract_invalid")
    return value, digest


def _validate_journal(
    path: Path,
    *,
    role: str,
    env_path: Path,
    env_digest: str,
    project_name: str,
    release_sha: str,
    image_id: str,
) -> str:
    value, digest = _read_json(path)
    new_env = Path(str(value.get("new_env") or ""))
    try:
        same_env = new_env.resolve(strict=True) == env_path.resolve(strict=True)
    except (OSError, RuntimeError):
        same_env = False
    if (
        value.get("schema") != JOURNAL_SCHEMA
        or value.get("status") != "PASS"
        or value.get("role") != role
        or value.get("release_sha") != release_sha
        or value.get("new_project") != project_name
        or value.get("new_image_id") != image_id
        or value.get("new_env_sha256") != env_digest
        or not same_env
        or value.get("state_deleted") is not False
        or value.get("product_authority_changed") is not False
        or value.get("secrets_disclosed") is not False
        or value.get("old_project") == project_name
    ):
        _fail("bluegreen_journal_binding_or_state_invalid")
    return digest


def _zero(value: object) -> bool:
    return not isinstance(value, bool) and isinstance(value, int) and value == 0


def _positive(value: object) -> bool:
    return not isinstance(value, bool) and isinstance(value, int) and value >= 1


def _sequence_map(value: object) -> dict[str, int]:
    if not isinstance(value, Mapping) or not value:
        _fail("sequence_evidence_missing")
    result: dict[str, int] = {}
    for key, item in value.items():
        if (
            not isinstance(key, str)
            or not STREAM_ID.fullmatch(key)
            or isinstance(item, bool)
            or not isinstance(item, int)
            or item < 1
        ):
            _fail("sequence_evidence_invalid")
        result[key] = item
    return result


def _validate_snapshot_identity(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping) or set(value) != set(SNAPSHOT_IDENTITY_FIELDS):
        _fail("health_snapshot_identity_invalid")
    if (
        value.get("contract") != WEB_VIEW_CONTRACT
        or not isinstance(value.get("snapshot_hash"), str)
        or not HEX64.fullmatch(str(value.get("snapshot_hash")))
        or not _positive(value.get("snapshot_version"))
        or value.get("feed_mode") != "PRIVATE_PRIMARY"
        or value.get("snapshot_status") != "OK"
        or value.get("estimated_rate_count") != len(ESTIMATOR_RATE_GRID_V1)
        or not isinstance(value.get("file_sha256"), str)
        or not HEX64.fullmatch(str(value.get("file_sha256")))
    ):
        _fail("health_snapshot_identity_invalid")
    return dict(value)


def _validate_observation(
    path: Path,
    *,
    role: str,
    expected_services: frozenset[str],
    sequence_fields: frozenset[str],
    release_sha: str,
    release_tree: str,
    image_id: str,
    project_name: str,
    now: datetime,
) -> tuple[dict[str, dict[str, int]], dict[str, object], str]:
    value, digest = _read_json(path)
    if set(value) != set(OBSERVATION_FIELDS):
        _fail("health_observation_contract_invalid")
    if (
        value.get("schema") != OBSERVATION_SCHEMA
        or value.get("role") != role
        or value.get("release_sha") != release_sha
        or value.get("release_tree") != release_tree
        or value.get("project_name") != project_name
        or value.get("image_id") != image_id
        or value.get("secrets_disclosed") is not False
        or not _zero(value.get("legacy_owner_count"))
        or not _zero(value.get("unexpected_owner_count"))
    ):
        _fail("health_observation_binding_invalid")
    _age(
        _time(value.get("observed_at_utc"), reason="health_observation_time_invalid"),
        now=now,
        maximum=MAXIMUM_AGE_SECONDS,
        reason="health_observation_stale_or_future",
    )

    owners = value.get("owners")
    if not isinstance(owners, Mapping) or set(owners) != set(expected_services):
        _fail("single_owner_inventory_invalid")
    for service, row in owners.items():
        if not isinstance(row, Mapping) or set(row) != set(OWNER_FIELDS):
            _fail("single_owner_inventory_invalid")
        common_owner_invalid = (
            row.get("count") != 1
            or row.get("project_name") != project_name
            or row.get("healthy") is not True
            or not isinstance(row.get("image_id"), str)
            or not IMAGE_ID.fullmatch(str(row.get("image_id")))
        )
        # PostgreSQL is deliberately a separately pinned upstream image and
        # therefore does not inherit the application release labels.  Every
        # executable Market role remains bound to the exact role image/SHA/tree.
        database_binding_invalid = service == "market-database" and (
            row.get("release_sha") is not None
            or row.get("release_tree") is not None
        )
        application_binding_invalid = service != "market-database" and (
            row.get("release_sha") != release_sha
            or row.get("release_tree") != release_tree
            or row.get("image_id") != image_id
        )
        if common_owner_invalid or database_binding_invalid or application_binding_invalid:
            _fail("single_owner_inventory_invalid")

    counts = value.get("counts")
    if (
        not isinstance(counts, Mapping)
        or set(counts) != set(COUNTER_FIELDS)
        or isinstance(counts.get("duplicate"), bool)
        or not isinstance(counts.get("duplicate"), int)
        or int(counts.get("duplicate", -1)) < 0
        or any(
            not _zero(counts[field])
            for field in COUNTER_FIELDS - {"duplicate"}
        )
    ):
        _fail("nonzero_transport_or_publication_counter")

    sequences = value.get("sequences")
    if not isinstance(sequences, Mapping) or set(sequences) != set(sequence_fields):
        _fail("sequence_evidence_invalid")
    sequence_rows = {key: _sequence_map(sequences[key]) for key in sequence_fields}
    snapshot = _validate_snapshot_identity(value.get("snapshot"))
    return sequence_rows, snapshot, digest


def _validate_snapshot(
    path: Path,
    *,
    now: datetime,
    maximum_age_seconds: int,
) -> tuple[dict[str, object], str]:
    value, digest = _read_json(
        path,
        root_owned=False,
        maximum_bytes=16_000_000,
    )
    payload = value.get("snapshot")
    if not isinstance(payload, Mapping):
        _fail("private_primary_snapshot_contract_invalid")
    try:
        snapshot = EstimatorSnapshotV2.model_validate(payload)
    except (TypeError, ValueError, ValidationError):
        _fail("private_primary_snapshot_contract_invalid")
    if (
        value.get("contract") != WEB_VIEW_CONTRACT
        or value.get("snapshot_hash") != snapshot.snapshot_id
        or value.get("snapshot_version") != snapshot.snapshot_version
        or value.get("feed_mode") != "PRIVATE_PRIMARY"
        or snapshot.feed_mode != "PRIVATE_PRIMARY"
        or snapshot.status != "OK"
        or value.get("transport_state") != "FRESH"
    ):
        _fail("private_primary_snapshot_identity_invalid")
    generated_age = _age(
        snapshot.generated_at_utc.astimezone(timezone.utc),
        now=now,
        maximum=maximum_age_seconds,
        reason="private_primary_snapshot_stale_or_future",
    )
    published = _time(
        value.get("published_at_utc"),
        reason="private_primary_publication_time_invalid",
    )
    publish_age = _age(
        published,
        now=now,
        maximum=maximum_age_seconds,
        reason="private_primary_publication_stale_or_future",
    )
    if published < snapshot.generated_at_utc.astimezone(timezone.utc):
        _fail("private_primary_publication_precedes_snapshot")
    stale_after = value.get("stale_after_seconds")
    if (
        isinstance(stale_after, bool)
        or not isinstance(stale_after, int)
        or not 1 <= stale_after <= maximum_age_seconds
    ):
        _fail("private_primary_stale_contract_invalid")

    estimated = [rate for rate in snapshot.rates if rate.status == "ESTIMATED"]
    if len(snapshot.rates) != len(ESTIMATOR_RATE_GRID_V1) or len(estimated) != len(
        ESTIMATOR_RATE_GRID_V1
    ):
        _fail("private_primary_estimated_rate_coverage_invalid")
    maximum_effective_underlying_age = 0.0
    for rate in estimated:
        underlying_age = rate.underlying_age_seconds
        if (
            isinstance(underlying_age, bool)
            or not isinstance(underlying_age, (int, float))
            or float(underlying_age) < 0
            or not rate.underlying_source
        ):
            _fail("private_primary_underlying_freshness_invalid")
        effective = float(underlying_age) + generated_age
        if effective > float(maximum_age_seconds):
            _fail("private_primary_underlying_stale")
        maximum_effective_underlying_age = max(
            maximum_effective_underlying_age,
            effective,
        )
    return (
        {
            "contract": WEB_VIEW_CONTRACT,
            "lane": "PRIVATE_PRIMARY",
            "status": "OK",
            "snapshot_hash": snapshot.snapshot_id,
            "snapshot_version": snapshot.snapshot_version,
            "estimated_rate_count": len(estimated),
            "file_sha256": digest,
            "snapshot_age_seconds": generated_age,
            "publication_age_seconds": publish_age,
            "maximum_effective_underlying_age_seconds": round(
                maximum_effective_underlying_age, 3
            ),
        },
        digest,
    )


def evaluate(
    *,
    release_sha: str,
    release_tree: str,
    bot_image_id: str,
    web_image_id: str,
    bot_env: Path,
    web_env: Path,
    bot_journal: Path,
    web_journal: Path,
    bot_health: Path,
    web_health: Path,
    bot_snapshot: Path,
    web_snapshot: Path,
    catchup_receipt: Path,
    expected_catchup_receipt_sha256: str,
    maximum_age_seconds: int = MAXIMUM_AGE_SECONDS,
    now: datetime | None = None,
) -> dict[str, object]:
    observed_now = (now or _utc_now()).astimezone(timezone.utc)
    _validate_expected_identity(
        release_sha=release_sha,
        release_tree=release_tree,
        bot_image_id=bot_image_id,
        web_image_id=web_image_id,
        maximum_age_seconds=maximum_age_seconds,
    )
    catchup_verification = _validate_catchup_receipt(
        catchup_receipt,
        expected_sha256=expected_catchup_receipt_sha256,
        release_sha=release_sha,
        now=observed_now,
        maximum_age_seconds=maximum_age_seconds,
    )
    bot_env_value, bot_env_digest = _validate_env(
        bot_env, role="bot", release_sha=release_sha, image_id=bot_image_id
    )
    web_env_value, web_env_digest = _validate_env(
        web_env, role="web", release_sha=release_sha, image_id=web_image_id
    )
    if (
        bot_env_value["MARKET_PIPELINE_PROJECT_NAME"]
        != web_env_value["MARKET_PIPELINE_PROJECT_NAME"]
    ):
        _fail("cross_role_project_mismatch")
    project_name = bot_env_value["MARKET_PIPELINE_PROJECT_NAME"]
    bot_journal_digest = _validate_journal(
        bot_journal,
        role="bot",
        env_path=bot_env,
        env_digest=bot_env_digest,
        project_name=project_name,
        release_sha=release_sha,
        image_id=bot_image_id,
    )
    web_journal_digest = _validate_journal(
        web_journal,
        role="web",
        env_path=web_env,
        env_digest=web_env_digest,
        project_name=project_name,
        release_sha=release_sha,
        image_id=web_image_id,
    )
    bot_sequences, bot_health_snapshot, bot_health_digest = _validate_observation(
        bot_health,
        role="bot",
        expected_services=BOT_SERVICES,
        sequence_fields=frozenset({"receiver", "adapter"}),
        release_sha=release_sha,
        release_tree=release_tree,
        image_id=bot_image_id,
        project_name=project_name,
        now=observed_now,
    )
    web_sequences, web_health_snapshot, web_health_digest = _validate_observation(
        web_health,
        role="web",
        expected_services=WEB_SERVICES,
        sequence_fields=frozenset({"producer", "acknowledged"}),
        release_sha=release_sha,
        release_tree=release_tree,
        image_id=web_image_id,
        project_name=project_name,
        now=observed_now,
    )
    sequence_views = (
        web_sequences["producer"],
        web_sequences["acknowledged"],
        bot_sequences["receiver"],
        bot_sequences["adapter"],
    )
    if any(view != sequence_views[0] for view in sequence_views[1:]):
        _fail("sequence_or_ack_gap_detected")

    bot_snapshot_value, bot_snapshot_digest = _validate_snapshot(
        bot_snapshot, now=observed_now, maximum_age_seconds=maximum_age_seconds
    )
    web_snapshot_value, web_snapshot_digest = _validate_snapshot(
        web_snapshot, now=observed_now, maximum_age_seconds=maximum_age_seconds
    )
    if (
        bot_snapshot_value["snapshot_hash"] != web_snapshot_value["snapshot_hash"]
        or bot_snapshot_value["snapshot_version"]
        != web_snapshot_value["snapshot_version"]
        or bot_snapshot_digest != web_snapshot_digest
    ):
        _fail("bot_web_snapshot_identity_or_digest_mismatch")
    for observed in (bot_health_snapshot, web_health_snapshot):
        if (
            observed["snapshot_hash"] != bot_snapshot_value["snapshot_hash"]
            or observed["snapshot_version"]
            != bot_snapshot_value["snapshot_version"]
            or observed["file_sha256"] != bot_snapshot_digest
        ):
            _fail("health_snapshot_identity_mismatch")

    return {
        "project_name": project_name,
        "stream_count": len(sequence_views[0]),
        "highest_sequence": max(sequence_views[0].values()),
        "snapshot": bot_snapshot_value,
        "capture_backfill": {
            "not_before_utc": web_env_value[
                "MARKET_CAPTURE_BACKFILL_NOT_BEFORE_UTC"
            ],
            "source_codes": web_env_value[
                "MARKET_CAPTURE_BACKFILL_SOURCE_CODES"
            ].split(","),
            "max_messages": int(
                web_env_value["MARKET_CAPTURE_BACKFILL_MAX_MESSAGES"]
            ),
        },
        "catchup_verification": catchup_verification,
        "artifacts": {
            "bot_env_sha256": bot_env_digest,
            "web_env_sha256": web_env_digest,
            "bot_journal_sha256": bot_journal_digest,
            "web_journal_sha256": web_journal_digest,
            "bot_health_sha256": bot_health_digest,
            "web_health_sha256": web_health_digest,
            "bot_snapshot_sha256": bot_snapshot_digest,
            "web_snapshot_sha256": web_snapshot_digest,
            "catchup_receipt_sha256": catchup_verification[
                "receipt_sha256"
            ],
        },
    }


def _secure_receipt_parent(path: Path) -> None:
    if not path.is_absolute() or path in {Path("/"), Path("/tmp"), Path("/var/tmp")}:
        _fail("receipt_path_invalid")
    parent = path.parent
    parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = parent.lstat()
    if (
        parent.is_symlink()
        or not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.geteuid()
        or stat.S_IMODE(info.st_mode) != 0o700
    ):
        _fail("receipt_parent_owner_mode_invalid")


def _write_receipt(path: Path, payload: Mapping[str, object]) -> None:
    _secure_receipt_parent(path)
    if path.exists() or path.is_symlink():
        _fail("receipt_exists")
    temporary = path.parent / f".{path.name}.{os.getpid()}.tmp"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(temporary, flags, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, sort_keys=True, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


def _safe_artifact_digests(paths: Mapping[str, Path]) -> dict[str, str | None]:
    result: dict[str, str | None] = {}
    for label, path in paths.items():
        try:
            info = path.lstat()
            valid = (
                not path.is_symlink()
                and stat.S_ISREG(info.st_mode)
                and stat.S_IMODE(info.st_mode) in {0o400, 0o600}
                and info.st_nlink == 1
                and 0 < info.st_size <= 16_000_000
            )
            result[f"{label}_sha256"] = (
                sha256(path.read_bytes()).hexdigest() if valid else None
            )
        except OSError:
            result[f"{label}_sha256"] = None
    return result


def verify_to_receipt(
    *,
    receipt: Path,
    confirmation: str,
    release_sha: str,
    release_tree: str,
    bot_image_id: str,
    web_image_id: str,
    bot_env: Path,
    web_env: Path,
    bot_journal: Path,
    web_journal: Path,
    bot_health: Path,
    web_health: Path,
    bot_snapshot: Path,
    web_snapshot: Path,
    catchup_receipt: Path,
    expected_catchup_receipt_sha256: str,
    maximum_age_seconds: int = MAXIMUM_AGE_SECONDS,
    now: datetime | None = None,
) -> dict[str, object]:
    if confirmation != CONFIRMATION:
        _fail("confirmation_invalid")
    observed_now = (now or _utc_now()).astimezone(timezone.utc)
    input_paths = {
        "bot_env": bot_env,
        "web_env": web_env,
        "bot_journal": bot_journal,
        "web_journal": web_journal,
        "bot_health": bot_health,
        "web_health": web_health,
        "bot_snapshot": bot_snapshot,
        "web_snapshot": web_snapshot,
        "catchup_receipt": catchup_receipt,
    }
    status = "PASS"
    reason_code: str | None = None
    result: dict[str, object] | None = None
    try:
        result = evaluate(
            release_sha=release_sha,
            release_tree=release_tree,
            bot_image_id=bot_image_id,
            web_image_id=web_image_id,
            bot_env=bot_env,
            web_env=web_env,
            bot_journal=bot_journal,
            web_journal=web_journal,
            bot_health=bot_health,
            web_health=web_health,
            bot_snapshot=bot_snapshot,
            web_snapshot=web_snapshot,
            catchup_receipt=catchup_receipt,
            expected_catchup_receipt_sha256=(
                expected_catchup_receipt_sha256
            ),
            maximum_age_seconds=maximum_age_seconds,
            now=observed_now,
        )
    except PromotionVerificationError as exc:
        status = "FAILED"
        reason_code = exc.reason_code
    except Exception:
        status = "FAILED"
        reason_code = "verification_internal_error"

    payload: dict[str, object] = {
        "schema": RECEIPT_SCHEMA,
        "status": status,
        "created_at_utc": _utc_text(observed_now),
        "release_sha": release_sha if HEX40.fullmatch(release_sha) else None,
        "release_tree": release_tree if HEX40.fullmatch(release_tree) else None,
        "image_ids": {
            "bot": bot_image_id if IMAGE_ID.fullmatch(bot_image_id) else None,
            "web": web_image_id if IMAGE_ID.fullmatch(web_image_id) else None,
        },
        "maximum_age_seconds": maximum_age_seconds,
        "reason_code": reason_code,
        "checks": list(CHECKS) if status == "PASS" else [],
        "stream_count": result["stream_count"] if result else 0,
        "highest_sequence": result["highest_sequence"] if result else 0,
        "snapshot": result["snapshot"] if result else None,
        "capture_backfill": result["capture_backfill"] if result else None,
        "catchup_verification": (
            result["catchup_verification"] if result else None
        ),
        "artifacts": (
            result["artifacts"] if result else _safe_artifact_digests(input_paths)
        ),
        "read_only_runtime_verification": True,
        "product_or_runtime_mutated": False,
        "payload_values_included": False,
        "pii_included": False,
        "secrets_disclosed": False,
    }
    _write_receipt(receipt, payload)
    return payload


def _common_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--release-sha", required=True)
    parser.add_argument("--release-tree", required=True)
    parser.add_argument("--bot-image-id", required=True)
    parser.add_argument("--web-image-id", required=True)
    parser.add_argument("--bot-env", type=Path, required=True)
    parser.add_argument("--web-env", type=Path, required=True)
    parser.add_argument("--bot-journal", type=Path, required=True)
    parser.add_argument("--web-journal", type=Path, required=True)
    parser.add_argument("--bot-health", type=Path, required=True)
    parser.add_argument("--web-health", type=Path, required=True)
    parser.add_argument("--bot-snapshot", type=Path, required=True)
    parser.add_argument("--web-snapshot", type=Path, required=True)
    parser.add_argument("--catchup-receipt", type=Path, required=True)
    parser.add_argument(
        "--expected-catchup-receipt-sha256", required=True
    )
    parser.add_argument(
        "--maximum-age-seconds", type=int, default=MAXIMUM_AGE_SECONDS
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    plan = commands.add_parser("plan")
    _common_arguments(plan)
    verify = commands.add_parser("verify")
    _common_arguments(verify)
    verify.add_argument("--receipt", type=Path, required=True)
    verify.add_argument("--confirmation", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    common = {
        "release_sha": args.release_sha,
        "release_tree": args.release_tree,
        "bot_image_id": args.bot_image_id,
        "web_image_id": args.web_image_id,
        "bot_env": args.bot_env,
        "web_env": args.web_env,
        "bot_journal": args.bot_journal,
        "web_journal": args.web_journal,
        "bot_health": args.bot_health,
        "web_health": args.web_health,
        "bot_snapshot": args.bot_snapshot,
        "web_snapshot": args.web_snapshot,
        "catchup_receipt": args.catchup_receipt,
        "expected_catchup_receipt_sha256": (
            args.expected_catchup_receipt_sha256
        ),
        "maximum_age_seconds": args.maximum_age_seconds,
    }
    if args.command == "plan":
        try:
            result = evaluate(**common)
        except PromotionVerificationError as exc:
            reason_code = exc.reason_code
        except Exception:
            reason_code = "verification_internal_error"
        else:
            print(
                json.dumps(
                    {
                        "schema": RECEIPT_SCHEMA,
                        "status": "PLAN_PASS",
                        "stream_count": result["stream_count"],
                        "snapshot": result["snapshot"],
                        "runtime_or_product_mutated": False,
                        "secrets_disclosed": False,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                )
            )
            return 0
        if reason_code:
            print(
                json.dumps(
                    {
                        "schema": RECEIPT_SCHEMA,
                        "status": "FAILED",
                        "reason_code": reason_code,
                        "secrets_disclosed": False,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                )
            )
            return 1
    try:
        receipt = verify_to_receipt(
            receipt=args.receipt,
            confirmation=args.confirmation,
            **common,
        )
    except PromotionVerificationError as exc:
        print(
            json.dumps(
                {
                    "schema": RECEIPT_SCHEMA,
                    "status": "FAILED",
                    "reason_code": exc.reason_code,
                    "secrets_disclosed": False,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return 1
    print(
        json.dumps(
            {
                "schema": RECEIPT_SCHEMA,
                "status": receipt["status"],
                "reason_code": receipt["reason_code"],
                "secrets_disclosed": False,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0 if receipt["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
