#!/usr/bin/env python3
"""Transactionally promote the production Product to PRIVATE_PRIMARY.

The command is intentionally narrower than the ordinary production release
script.  It binds an already verified Market-pipeline release, a derived
PRIVATE_PRIMARY deploy manifest, its preparation receipt, and the immutable
Product runtime source.  It then:

1. activates the Product source through the existing CAS updater;
2. runs the ordinary production release with the exact private manifest;
3. on any post-activation failure, restores the exact pre-activation source
   bytes through the updater and redeploys a transaction-local, normalized
   legacy Product manifest whose Market-pipeline ownership gates are disabled.

No environment values or child-process output are written to stdout or to the
result receipt.  The only successful terminal states are ``PASS``,
``ROLLED_BACK``, and ``BLOCKED_MANUAL``.
"""

from __future__ import annotations

import argparse
from contextlib import redirect_stdout
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import io
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
from typing import Callable, Mapping, Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts import cutover_telegram_delivery_queue_production as queue_cutover
from scripts import update_production_coin_inference_source as source_updater


MANIFEST_PREPARER_SCRIPT = (
    REPO_ROOT / "scripts/prepare_production_private_primary_manifest.py"
)
MANIFEST_SCHEMA_SOURCE = REPO_ROOT / "deploy/production/online.env.example"
APPROVED_SECURE_ROOT = Path("/root/secure-envs/trading-bot")

CONFIRMATION = "promote-production-private-primary-product"
RECOVERY_CONFIRMATION = "recover-production-private-primary-product"
ACTIVATION_CONFIRMATION = "activate-production-private-primary-snapshots"
ROLLBACK_CONFIRMATION = "restore-production-legacy-snapshots"
RECEIPT_SCHEMA = "production_private_primary_product_promotion/1.0"
POSTDEPLOY_RECEIPT_SCHEMA = (
    "production_private_primary_product_postdeploy_verification/1.0"
)
PREPARATION_RECEIPT_SCHEMA = "production_private_primary_deploy_manifest/1.0"
PROMOTION_RECEIPT_SCHEMA = "production_private_primary_promotion_verification/1.0"
PROMOTION_SNAPSHOT_CONTRACT = "estimator_snapshot_web_view/1.0"
CATCHUP_RECEIPT_SCHEMA = "production_market_catchup_verification/1.2"
PROMOTION_MAXIMUM_AGE_SECONDS = 120
PROMOTION_REQUIRED_CHECKS = (
    "release_and_image_binding",
    "bluegreen_journals_pass",
    "single_owner_topology",
    "contiguous_sequences_and_ack",
    "idempotent_duplicates_and_zero_rejected_dead_open_outbox",
    "receiver_publication_settled",
    "private_primary_snapshot_contract",
    "complete_rate_grid_with_safe_one_gram_no_data",
    "effective_underlying_freshness",
    "bot_web_snapshot_identity_and_digest",
    "owner_authorized_backfill_scope_bound",
    "catchup_complete_and_live_tail_verified",
)
AUTHORIZED_BACKFILL_NOT_BEFORE_UTC = "2026-08-25T09:33:00Z"
AUTHORIZED_BACKFILL_SOURCE_CODES = (
    "MELTED_PRIMARY_FLOW",
    "GROUP_1",
    "GROUP_2",
)
AUTHORIZED_BACKFILL_MIN_MESSAGES = 2_000
AUTHORIZED_BACKFILL_MAX_MESSAGES = 250_000
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

HEX40 = re.compile(r"^[0-9a-f]{40}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
IMAGE_ID = re.compile(r"^sha256:[0-9a-f]{64}$")
TRANSACTION_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{7,95}$")
ENV_KEY = re.compile(r"^[A-Z][A-Z0-9_]*$")
MAXIMUM_FILE_BYTES = 2_000_000

PRIVATE_MANIFEST_UPDATES: Mapping[str, str] = {
    "PRODUCTION_MARKET_PIPELINE_RELEASE_EVIDENCE_ENABLED": "0",
    "PRODUCTION_MARKET_PIPELINE_RELEASE_EVIDENCE_CONFIRM": "",
    "PRODUCTION_MARKET_PIPELINE_HOST_PREFLIGHT_ENABLED": "0",
    "PRODUCTION_MARKET_PIPELINE_HOST_PREFLIGHT_CONFIRM": "",
    "PRODUCTION_MARKET_PIPELINE_MIGRATION_ENABLED": "0",
    "PRODUCTION_MARKET_PIPELINE_MIGRATION_CONFIRM": "",
    "PRODUCTION_MARKET_PIPELINE_SHADOW_ROLLOUT_ENABLED": "0",
    "PRODUCTION_MARKET_PIPELINE_SHADOW_ROLLOUT_CONFIRM": "",
    "PRODUCTION_MARKET_PIPELINE_CAPTURE_CUTOVER_ENABLED": "0",
    "PRODUCTION_COIN_INFERENCE_RELAY_ENABLED": "0",
    "PRODUCTION_COIN_INFERENCE_RELAY_CONFIRM": "",
    "PRODUCTION_COIN_INFERENCE_RELAY_DISABLE_CONFIRM": (
        "disable-production-coin-inference-snapshot"
    ),
}

# Recovery is deliberately a Product-only deployment.  The legacy collectors
# are not a data oracle for this cutover and must never be restarted beside the
# committed PRIVATE_PRIMARY Telegram capture owners.  A failed Product
# promotion therefore restores the exact source bytes, but deploys a bounded
# inference-disabled legacy runtime until an operator can resolve the failure;
# it cannot replay pipeline gates or reclaim Telegram sessions.
LEGACY_PRODUCT_MANIFEST_UPDATES: Mapping[str, str] = {
    "PRODUCTION_MARKET_PIPELINE_RELEASE_EVIDENCE_ENABLED": "0",
    "PRODUCTION_MARKET_PIPELINE_RELEASE_EVIDENCE_CONFIRM": "",
    "PRODUCTION_MARKET_PIPELINE_HOST_PREFLIGHT_ENABLED": "0",
    "PRODUCTION_MARKET_PIPELINE_HOST_PREFLIGHT_CONFIRM": "",
    "PRODUCTION_MARKET_PIPELINE_MIGRATION_ENABLED": "0",
    "PRODUCTION_MARKET_PIPELINE_MIGRATION_CONFIRM": "",
    "PRODUCTION_MARKET_PIPELINE_SHADOW_ROLLOUT_ENABLED": "0",
    "PRODUCTION_MARKET_PIPELINE_SHADOW_ROLLOUT_CONFIRM": "",
    "PRODUCTION_MARKET_PIPELINE_CAPTURE_CUTOVER_ENABLED": "0",
    "PRODUCTION_COIN_INFERENCE_PREVIEW_ENABLED": "false",
    "PRODUCTION_COIN_INFERENCE_SELECTION_ENABLED": "false",
    "PRODUCTION_OFFER_MODEL_PRICE_GUARD_ENABLED": "false",
    "PRODUCTION_COIN_INFERENCE_AUTO_SELECTION_ENABLED": "false",
    "PRODUCTION_COIN_INFERENCE_RELAY_ENABLED": "0",
    "PRODUCTION_COIN_INFERENCE_RELAY_CONFIRM": "",
    "PRODUCTION_COIN_INFERENCE_RELAY_DISABLE_CONFIRM": (
        "disable-production-coin-inference-snapshot"
    ),
}


class PromotionError(RuntimeError):
    """A stable, value-free promotion refusal."""

    def __init__(self, reason_code: str, *, stage: str = "preflight") -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.stage = stage


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: bytes = b""
    stderr: bytes = b""


@dataclass(frozen=True)
class Binding:
    release_sha: str
    release_tree: str
    release_checkout_path_sha256: str
    source_manifest_sha256: str
    legacy_product_manifest_sha256: str
    private_manifest_sha256: str
    private_manifest_receipt_sha256: str
    promotion_receipt_sha256: str
    catchup_receipt_sha256: str
    maintenance_journal_sha256: str
    maintenance_journal_path_sha256: str
    web_maintenance_journal_sha256: str
    web_maintenance_journal_path_sha256: str
    source_sha256_before: str
    runtime_source_path_sha256: str


def _binding_payload(binding: Binding) -> dict[str, str]:
    return {
        name: str(getattr(binding, name))
        for name in Binding.__dataclass_fields__
    }


def _binding_from_payload(value: object) -> Binding:
    if not isinstance(value, dict) or set(value) != set(Binding.__dataclass_fields__):
        _fail("recovery_binding_invalid", stage="recovery")
    normalized = {name: str(value.get(name) or "") for name in Binding.__dataclass_fields__}
    if (
        not HEX40.fullmatch(normalized["release_sha"])
        or not HEX40.fullmatch(normalized["release_tree"])
        or any(
            not HEX64.fullmatch(normalized[name])
            for name in normalized
            if name not in {"release_sha", "release_tree"}
        )
    ):
        _fail("recovery_binding_invalid", stage="recovery")
    return Binding(**normalized)


def _digest(payload: bytes) -> str:
    return sha256(payload).hexdigest()


def _utc_text() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _fail(reason_code: str, *, stage: str = "preflight") -> None:
    raise PromotionError(reason_code, stage=stage)


def _secure_root() -> Path:
    if not APPROVED_SECURE_ROOT.is_absolute():
        _fail("secure_root_invalid")
    try:
        root = APPROVED_SECURE_ROOT.resolve(strict=True)
        info = root.lstat()
    except OSError:
        _fail("secure_root_invalid")
    if (
        root != APPROVED_SECURE_ROOT
        or root.is_symlink()
        or not stat.S_ISDIR(info.st_mode)
        or info.st_uid not in {0, os.geteuid()}
        or stat.S_IMODE(info.st_mode) & 0o077
    ):
        _fail("secure_root_invalid")
    return root


def _under_secure_root(path: Path, *, label: str) -> Path:
    if not path.is_absolute():
        _fail(f"{label}_path_invalid")
    root = _secure_root()
    try:
        resolved = path.resolve(strict=False)
    except OSError:
        _fail(f"{label}_path_invalid")
    if resolved != path or not (
        path == root or path.parent == root or root in path.parents
    ):
        _fail(f"{label}_scope_invalid")
    return path


def _secure_directory(path: Path, *, label: str, create: bool = False) -> Path:
    path = _under_secure_root(path, label=label)
    if create and not path.exists():
        try:
            path.mkdir(mode=0o700, parents=True)
        except OSError:
            _fail(f"{label}_directory_invalid")
    try:
        info = path.lstat()
        resolved = path.resolve(strict=True)
    except OSError:
        _fail(f"{label}_directory_invalid")
    if (
        path.is_symlink()
        or resolved != path
        or not stat.S_ISDIR(info.st_mode)
        or info.st_uid not in {0, os.geteuid()}
        or stat.S_IMODE(info.st_mode) != 0o700
    ):
        _fail(f"{label}_directory_invalid")
    return path


def _secure_file(path: Path, *, label: str) -> tuple[Path, bytes]:
    path = _under_secure_root(path, label=label)
    _secure_directory(path.parent, label=f"{label}_parent")
    flags = os.O_RDONLY
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    if hasattr(os, "O_NONBLOCK"):
        flags |= os.O_NONBLOCK
    try:
        descriptor = os.open(path, flags)
    except OSError:
        _fail(f"{label}_unavailable")
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid not in {0, os.geteuid()}
            or stat.S_IMODE(before.st_mode) != 0o600
            or before.st_nlink != 1
            or not 0 < before.st_size <= MAXIMUM_FILE_BYTES
        ):
            _fail(f"{label}_security_invalid")
        chunks: list[bytes] = []
        remaining = before.st_size + 1
        while remaining:
            chunk = os.read(descriptor, remaining)
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        after = os.fstat(descriptor)
        if len(payload) != before.st_size or (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ):
            _fail(f"{label}_changed_during_read")
        return path, payload
    except OSError:
        _fail(f"{label}_read_failed")
    finally:
        os.close(descriptor)
    raise AssertionError("unreachable")


def _require_manifest_scope(path: Path, *, label: str) -> None:
    manifest_root = _secure_directory(
        _secure_root() / "release-control", label="manifest_root"
    )
    if path.parent != manifest_root and manifest_root not in path.parents:
        _fail(f"{label}_scope_invalid")


def _require_production_scope(path: Path, *, label: str) -> None:
    lowered = tuple(part.lower() for part in path.parts)
    if (
        path == REPO_ROOT
        or REPO_ROOT in path.parents
        or any("staging" in part for part in lowered)
        or not any("production" in part for part in lowered)
    ):
        _fail(f"{label}_production_scope_invalid")


def _read_json(path: Path, *, label: str) -> tuple[Mapping[str, object], bytes]:
    _path, payload = _secure_file(path, label=label)
    try:
        document = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError):
        _fail(f"{label}_json_invalid")
    if not isinstance(document, Mapping):
        _fail(f"{label}_json_invalid")
    return document, payload


def _read_env(payload: bytes, *, label: str) -> dict[str, str]:
    try:
        lines = payload.decode("utf-8").splitlines()
    except UnicodeDecodeError:
        _fail(f"{label}_encoding_invalid")
    values: dict[str, str] = {}
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            _fail(f"{label}_syntax_invalid")
        key, value = line.split("=", 1)
        if not ENV_KEY.fullmatch(key) or key in values:
            _fail(f"{label}_syntax_invalid")
        values[key] = value
    return values


def _expected_digest(value: str, *, label: str) -> str:
    if not HEX64.fullmatch(value or ""):
        _fail(f"expected_{label}_sha256_invalid")
    return value


def _check_digest(payload: bytes, expected: str, *, label: str) -> str:
    actual = _digest(payload)
    if actual != _expected_digest(expected, label=label):
        _fail(f"{label}_cas_mismatch")
    return actual


def _assert_file_digest(path: Path, expected: str, *, label: str) -> None:
    _path, payload = _secure_file(path, label=label)
    if _digest(payload) != expected:
        _fail(f"{label}_cas_mismatch", stage="artifact_cas")


def _command(
    argv: Sequence[str],
    *,
    capture: bool = False,
    env: Mapping[str, str] | None = None,
    cwd: Path = REPO_ROOT,
) -> CommandResult:
    try:
        completed = subprocess.run(
            list(argv),
            cwd=cwd,
            env=dict(env) if env is not None else None,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
            stderr=subprocess.PIPE if capture else subprocess.DEVNULL,
            check=False,
            shell=False,
        )
    except OSError:
        _fail("child_process_unavailable", stage="child_process")
    return CommandResult(
        completed.returncode,
        completed.stdout if capture else b"",
        completed.stderr if capture else b"",
    )


def _git_identity(
    release_checkout: Path, expected_sha: str, expected_tree: str
) -> dict[str, str]:
    try:
        resolved = release_checkout.resolve(strict=True)
        metadata = release_checkout.lstat()
    except OSError:
        _fail("release_git_identity_unavailable")
    if (
        not release_checkout.is_absolute()
        or release_checkout.is_symlink()
        or resolved != release_checkout
        or not stat.S_ISDIR(metadata.st_mode)
    ):
        _fail("release_git_identity_unavailable")
    checks = (
        (["git", "rev-parse", "HEAD"], expected_sha),
        (["git", "rev-parse", "HEAD^{tree}"], expected_tree),
        (["git", "rev-parse", "origin/main"], expected_sha),
        (["git", "rev-parse", "--abbrev-ref", "HEAD"], "main"),
    )
    for argv, expected in checks:
        result = _command(argv, capture=True, cwd=release_checkout)
        if result.returncode != 0:
            _fail("release_git_identity_unavailable")
        try:
            actual = result.stdout.decode("ascii").strip()
        except UnicodeDecodeError:
            _fail("release_git_identity_unavailable")
        if actual != expected:
            _fail("release_git_identity_mismatch")
    clean = _command(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"],
        capture=True,
        cwd=release_checkout,
    )
    if clean.returncode != 0 or clean.stdout:
        _fail("release_worktree_not_clean")
    return {
        "branch": "main",
        "head": expected_sha,
        "tree": expected_tree,
        "origin_main": expected_sha,
        "worktree": "clean",
    }


def _validate_preparation_receipt(
    *,
    source_manifest: Path,
    source_manifest_digest: str,
    private_manifest: Path,
    private_manifest_digest: str,
    receipt_path: Path,
    receipt: Mapping[str, object],
    expected_changed_keys: Sequence[str],
) -> None:
    expected_schema_digest = _digest(MANIFEST_SCHEMA_SOURCE.read_bytes())
    expected_tool_digest = _digest(MANIFEST_PREPARER_SCRIPT.read_bytes())
    normalized = sorted(PRIVATE_MANIFEST_UPDATES)
    changed = receipt.get("changed_keys")
    if (
        receipt.get("schema") != PREPARATION_RECEIPT_SCHEMA
        or receipt.get("status") != "PASS"
        or receipt.get("action") != "PREPARE_PRIVATE_PRIMARY_DEPLOY_MANIFEST"
        or receipt.get("source_sha256") != source_manifest_digest
        or receipt.get("output_sha256") != private_manifest_digest
        or receipt.get("source_path_sha256")
        != _digest(str(source_manifest).encode("utf-8"))
        or receipt.get("output_path_sha256")
        != _digest(str(private_manifest).encode("utf-8"))
        or receipt.get("receipt_path_sha256")
        != _digest(str(receipt_path).encode("utf-8"))
        or receipt.get("manifest_schema_sha256") != expected_schema_digest
        or receipt.get("tool_sha256") != expected_tool_digest
        or receipt.get("normalized_keys") != normalized
        or changed != sorted(expected_changed_keys)
        or receipt.get("source_preserved_by_tool") is not True
        or receipt.get("secrets_disclosed") is not False
    ):
        _fail("private_manifest_receipt_contract_invalid")


def _validate_catchup_receipt(
    receipt: Mapping[str, object], *, release_sha: str
) -> float:
    verified_at = receipt.get("verified_at_utc")
    if not isinstance(verified_at, str) or not verified_at.endswith("Z"):
        _fail("catchup_receipt_time_invalid")
    try:
        verified = datetime.fromisoformat(
            verified_at.replace("Z", "+00:00")
        )
    except ValueError:
        _fail("catchup_receipt_time_invalid")
    if verified.tzinfo is None:
        _fail("catchup_receipt_time_invalid")
    age_seconds = (
        datetime.now(timezone.utc) - verified.astimezone(timezone.utc)
    ).total_seconds()
    if age_seconds < 0 or age_seconds > PROMOTION_MAXIMUM_AGE_SECONDS:
        _fail("catchup_receipt_stale_or_future")
    evidence = receipt.get("evidence_artifacts")
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
        try:
            observed = datetime.fromisoformat(
                str(artifact.get("observed_at_utc") or "").replace("Z", "+00:00")
            )
        except ValueError:
            _fail("catchup_receipt_time_invalid")
        if observed.tzinfo is None:
            _fail("catchup_receipt_time_invalid")
        evidence_times[str(label)] = observed.astimezone(timezone.utc)
        evidence_age = (
            verified - observed.astimezone(timezone.utc)
        ).total_seconds()
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
        value = receipt.get(field)
        return (
            not isinstance(value, bool)
            and isinstance(value, int)
            and value == 0
        )

    if (
        receipt.get("schema") != CATCHUP_RECEIPT_SCHEMA
        or receipt.get("status") != "PASS"
        or receipt.get("release_sha") != release_sha
        or receipt.get("cutoff_utc") != AUTHORIZED_BACKFILL_NOT_BEFORE_UTC
        or receipt.get("backfill_sources")
        != list(AUTHORIZED_CATCHUP_BACKFILL_SOURCES)
        or receipt.get("live_source_inventory")
        != list(AUTHORIZED_CATCHUP_SOURCE_INVENTORY)
        or receipt.get("live_tail_observed") is not True
        or not zero_integer("internal_sequence_gaps")
        or not zero_integer("unresolved_quarantines")
        or not zero_integer("unresolved_rejections")
        or receipt.get("upstream_time_gaps_allowed") is not True
        or receipt.get("secrets_disclosed") is not False
        or receipt.get("evidence_binding_sha256") != evidence_binding
    ):
        _fail("catchup_receipt_contract_invalid")
    return age_seconds


def _validate_promotion_receipt(
    receipt: Mapping[str, object],
    *,
    release_sha: str,
    release_tree: str,
    catchup_receipt_sha256: str,
    catchup_age_seconds: float,
) -> None:
    snapshot = receipt.get("snapshot")
    capture_backfill = receipt.get("capture_backfill")
    catchup_verification = receipt.get("catchup_verification")
    checks = receipt.get("checks")
    image_ids = receipt.get("image_ids")
    artifacts = receipt.get("artifacts")
    created_at = receipt.get("created_at_utc")
    if not isinstance(created_at, str) or not created_at.endswith("Z"):
        _fail("promotion_receipt_time_invalid")
    try:
        created = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    except ValueError:
        _fail("promotion_receipt_time_invalid")
    if created.tzinfo is None:
        _fail("promotion_receipt_time_invalid")
    receipt_age = (datetime.now(timezone.utc) - created.astimezone(timezone.utc)).total_seconds()
    if receipt_age < 0 or receipt_age > PROMOTION_MAXIMUM_AGE_SECONDS:
        _fail("promotion_receipt_stale_or_future")

    def fresh_number(value: object) -> bool:
        return (
            not isinstance(value, bool)
            and isinstance(value, (int, float))
            and 0 <= float(value)
            and float(value) + receipt_age <= PROMOTION_MAXIMUM_AGE_SECONDS
        )

    if (
        receipt.get("schema") != PROMOTION_RECEIPT_SCHEMA
        or receipt.get("status") != "PASS"
        or receipt.get("release_sha") != release_sha
        or receipt.get("release_tree") != release_tree
        or receipt.get("maximum_age_seconds") != PROMOTION_MAXIMUM_AGE_SECONDS
        or checks != list(PROMOTION_REQUIRED_CHECKS)
        or not isinstance(image_ids, Mapping)
        or set(image_ids) != {"bot", "web"}
        or any(
            IMAGE_ID.fullmatch(str(image_ids.get(role) or "")) is None
            for role in ("bot", "web")
        )
        or not isinstance(catchup_verification, Mapping)
        or set(catchup_verification) != {"receipt_sha256", "age_seconds"}
        or catchup_verification.get("receipt_sha256")
        != catchup_receipt_sha256
        or isinstance(catchup_verification.get("age_seconds"), bool)
        or not isinstance(catchup_verification.get("age_seconds"), (int, float))
        or not 0 <= float(catchup_verification["age_seconds"])
        <= PROMOTION_MAXIMUM_AGE_SECONDS
        or float(catchup_verification["age_seconds"])
        > catchup_age_seconds + 0.01
        or not isinstance(capture_backfill, Mapping)
        or set(capture_backfill)
        != {"not_before_utc", "source_codes", "max_messages"}
        or capture_backfill.get("not_before_utc")
        != AUTHORIZED_BACKFILL_NOT_BEFORE_UTC
        or capture_backfill.get("source_codes")
        != list(AUTHORIZED_BACKFILL_SOURCE_CODES)
        or isinstance(capture_backfill.get("max_messages"), bool)
        or not isinstance(capture_backfill.get("max_messages"), int)
        or not AUTHORIZED_BACKFILL_MIN_MESSAGES
        <= int(capture_backfill["max_messages"])
        <= AUTHORIZED_BACKFILL_MAX_MESSAGES
        or not isinstance(snapshot, Mapping)
        or snapshot.get("contract") != PROMOTION_SNAPSHOT_CONTRACT
        or snapshot.get("lane") != "PRIVATE_PRIMARY"
        or snapshot.get("status") != "OK"
        or not source_updater.promotion_snapshot_coverage_valid(snapshot)
        or not HEX64.fullmatch(str(snapshot.get("snapshot_hash") or ""))
        or isinstance(snapshot.get("snapshot_version"), bool)
        or not isinstance(snapshot.get("snapshot_version"), int)
        or int(snapshot["snapshot_version"]) < 1
        or not HEX64.fullmatch(str(snapshot.get("file_sha256") or ""))
        or not fresh_number(snapshot.get("snapshot_age_seconds"))
        or not fresh_number(snapshot.get("publication_age_seconds"))
        or not fresh_number(
            snapshot.get("maximum_effective_underlying_age_seconds")
        )
        or not isinstance(artifacts, Mapping)
        or artifacts.get("bot_snapshot_sha256")
        != snapshot.get("file_sha256")
        or artifacts.get("web_snapshot_sha256")
        != snapshot.get("file_sha256")
        or receipt.get("read_only_runtime_verification") is not True
        or receipt.get("product_or_runtime_mutated") is not False
        or receipt.get("payload_values_included") is not False
        or receipt.get("pii_included") is not False
        or receipt.get("secrets_disclosed") is not False
    ):
        _fail("promotion_receipt_contract_invalid")


def _validate_web_maintenance_receipt(
    receipt: Mapping[str, object],
    *,
    release_sha: str,
    primary_verification_sha256: str,
) -> None:
    """Validate the copied wa-fi handoff without pretending it is local live state."""

    verified_at = receipt.get("verified_at_utc")
    authority = receipt.get("authority_transfer")
    maintenance = receipt.get("maintenance_lock")
    try:
        verified = datetime.fromisoformat(
            str(verified_at or "").replace("Z", "+00:00")
        ).astimezone(timezone.utc)
    except (TypeError, ValueError):
        _fail("web_maintenance_journal_time_invalid")
    age = (datetime.now(timezone.utc) - verified).total_seconds()
    if (
        receipt.get("schema")
        != "production_legacy_market_collector_handoff/1.1"
        or receipt.get("status") != "PRIMARY_COMMITTED"
        or receipt.get("host_role") != "web"
        or receipt.get("release_sha") != release_sha
        or receipt.get("primary_verification_sha256")
        != primary_verification_sha256
        or receipt.get("primary_rollback_sha256") is not None
        or receipt.get("state_deleted") is not False
        or receipt.get("secrets_disclosed") is not False
        or not isinstance(maintenance, Mapping)
        or not isinstance(authority, Mapping)
        or set(authority)
        != {
            "bluegreen_journal_path_sha256",
            "prepared_bluegreen_journal_sha256",
            "authorization_bluegreen_journal_sha256",
            "marker_authority_sha256",
        }
        or any(
            not HEX64.fullmatch(str(value or ""))
            for value in authority.values()
        )
        or age < 0
        or age > PROMOTION_MAXIMUM_AGE_SECONDS
    ):
        _fail("web_maintenance_journal_contract_invalid")


def _derive_manifest(
    source: bytes, updates: Mapping[str, str]
) -> tuple[bytes, list[str]]:
    """Apply a byte-preserving, explicitly bounded manifest rewrite."""

    try:
        text = source.decode("utf-8")
    except UnicodeDecodeError:
        _fail("source_manifest_encoding_invalid")
    lines = text.splitlines(keepends=True)
    seen: set[str] = set()
    changed: list[str] = []
    for index, raw in enumerate(lines):
        line = raw.rstrip("\r\n")
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _value = line.split("=", 1)
        if key not in updates:
            continue
        seen.add(key)
        newline = "\r\n" if raw.endswith("\r\n") else "\n" if raw.endswith("\n") else ""
        replacement = f"{key}={updates[key]}{newline}"
        if replacement != raw:
            lines[index] = replacement
            changed.append(key)
    missing = [key for key in updates if key not in seen]
    if missing:
        if lines and not lines[-1].endswith("\n"):
            lines[-1] += "\n"
        for key in missing:
            lines.append(f"{key}={updates[key]}\n")
            changed.append(key)
    return "".join(lines).encode("utf-8"), sorted(changed)


def _derive_private_manifest(source: bytes) -> tuple[bytes, list[str]]:
    """Reproduce the preparation tool's byte-preserving target rewrite."""

    return _derive_manifest(source, PRIVATE_MANIFEST_UPDATES)


def _derive_legacy_product_manifest(source: bytes) -> tuple[bytes, list[str]]:
    """Disable only pipeline ownership gates for exact legacy redeployment."""

    return _derive_manifest(source, LEGACY_PRODUCT_MANIFEST_UPDATES)


def _preflight(
    args: argparse.Namespace, *, legacy_product_manifest: Path
) -> tuple[Binding, Path]:
    if args.confirm != CONFIRMATION:
        _fail("confirmation_invalid")
    release_sha = args.expected_release_sha or ""
    release_tree = args.expected_release_tree or ""
    if not HEX40.fullmatch(release_sha) or not HEX40.fullmatch(release_tree):
        _fail("expected_release_identity_invalid")
    release_checkout = Path(args.release_checkout)
    _git_identity(release_checkout, release_sha, release_tree)

    maintenance_journal = Path(args.maintenance_journal)
    _require_production_scope(maintenance_journal, label="maintenance_journal")
    maintenance_journal, maintenance_journal_payload = _secure_file(
        maintenance_journal, label="maintenance_journal"
    )
    maintenance_journal_digest = _check_digest(
        maintenance_journal_payload,
        args.expected_maintenance_journal_sha256,
        label="maintenance_journal",
    )
    web_maintenance_journal = Path(args.web_maintenance_journal)
    _require_production_scope(
        web_maintenance_journal, label="web_maintenance_journal"
    )
    web_maintenance, web_maintenance_payload = _read_json(
        web_maintenance_journal, label="web_maintenance_journal"
    )
    web_maintenance_digest = _check_digest(
        web_maintenance_payload,
        args.expected_web_maintenance_journal_sha256,
        label="web_maintenance_journal",
    )

    source_manifest, source_manifest_payload = _secure_file(
        Path(args.source_manifest), label="source_manifest"
    )
    private_manifest, private_manifest_payload = _secure_file(
        Path(args.private_manifest), label="private_manifest"
    )
    _require_manifest_scope(source_manifest, label="source_manifest")
    _require_manifest_scope(private_manifest, label="private_manifest")
    if source_manifest == private_manifest:
        _fail("source_and_private_manifest_alias")
    source_manifest_digest = _check_digest(
        source_manifest_payload,
        args.expected_source_manifest_sha256,
        label="source_manifest",
    )
    private_manifest_digest = _check_digest(
        private_manifest_payload,
        args.expected_private_manifest_sha256,
        label="private_manifest",
    )
    derived_private_manifest, expected_changed_keys = _derive_private_manifest(
        source_manifest_payload
    )
    if derived_private_manifest != private_manifest_payload:
        _fail("private_manifest_derivation_mismatch")

    preparation_path = Path(args.private_manifest_receipt)
    _require_manifest_scope(preparation_path, label="private_manifest_receipt")
    preparation, preparation_payload = _read_json(
        preparation_path, label="private_manifest_receipt"
    )
    preparation_digest = _check_digest(
        preparation_payload,
        args.expected_private_manifest_receipt_sha256,
        label="private_manifest_receipt",
    )
    _validate_preparation_receipt(
        source_manifest=source_manifest,
        source_manifest_digest=source_manifest_digest,
        private_manifest=private_manifest,
        private_manifest_digest=private_manifest_digest,
        receipt_path=preparation_path,
        receipt=preparation,
        expected_changed_keys=expected_changed_keys,
    )

    catchup_path = Path(args.catchup_receipt)
    _require_production_scope(catchup_path, label="catchup_receipt")
    catchup, catchup_payload = _read_json(
        catchup_path, label="catchup_receipt"
    )
    catchup_digest = _check_digest(
        catchup_payload,
        args.expected_catchup_receipt_sha256,
        label="catchup_receipt",
    )
    catchup_age_seconds = _validate_catchup_receipt(
        catchup, release_sha=release_sha
    )

    promotion_path = Path(args.promotion_receipt)
    _require_production_scope(promotion_path, label="promotion_receipt")
    promotion, promotion_payload = _read_json(
        promotion_path, label="promotion_receipt"
    )
    promotion_digest = _check_digest(
        promotion_payload,
        args.expected_promotion_receipt_sha256,
        label="promotion_receipt",
    )
    _validate_promotion_receipt(
        promotion,
        release_sha=release_sha,
        release_tree=release_tree,
        catchup_receipt_sha256=catchup_digest,
        catchup_age_seconds=catchup_age_seconds,
    )
    _validate_web_maintenance_receipt(
        web_maintenance,
        release_sha=release_sha,
        primary_verification_sha256=promotion_digest,
    )

    source_values = _read_env(source_manifest_payload, label="source_manifest")
    private_values = _read_env(private_manifest_payload, label="private_manifest")
    source_path_text = source_values.get("RUNTIME_ENV_SOURCE_PATH", "")
    if not source_path_text or source_path_text != private_values.get(
        "RUNTIME_ENV_SOURCE_PATH", ""
    ):
        _fail("runtime_source_identity_mismatch")
    runtime_source, runtime_payload = _secure_file(
        Path(source_path_text), label="runtime_source"
    )
    _require_production_scope(runtime_source, label="runtime_source")
    source_digest = _check_digest(
        runtime_payload,
        args.expected_source_sha256,
        label="source",
    )
    runtime_values = _read_env(runtime_payload, label="runtime_source")
    if runtime_values.get(
        "PRODUCTION_PRODUCT_ESTIMATOR_SNAPSHOT_MODE", "LEGACY"
    ).strip().upper() != "LEGACY":
        _fail("runtime_source_not_legacy")
    for key, expected in PRIVATE_MANIFEST_UPDATES.items():
        if private_values.get(key) != expected:
            _fail("private_manifest_contract_invalid")

    legacy_payload, _legacy_changed = _derive_legacy_product_manifest(
        source_manifest_payload
    )
    legacy_values = _read_env(
        legacy_payload, label="legacy_product_manifest"
    )
    for key, expected in LEGACY_PRODUCT_MANIFEST_UPDATES.items():
        if legacy_values.get(key) != expected:
            _fail("legacy_product_manifest_contract_invalid")
    for key in ("RUNTIME_ENV_SOURCE_PATH",):
        if legacy_values.get(key) != source_values.get(key):
            _fail("legacy_product_manifest_preservation_failed")
    _exclusive_write(legacy_product_manifest, legacy_payload)
    _legacy_path, installed_legacy = _secure_file(
        legacy_product_manifest, label="legacy_product_manifest"
    )
    if installed_legacy != legacy_payload:
        _fail("legacy_product_manifest_install_failed")
    legacy_product_manifest_digest = _digest(legacy_payload)

    # Re-read every immutable evidence artifact immediately before returning.
    for path, expected, label in (
        (source_manifest, source_manifest_digest, "source_manifest"),
        (private_manifest, private_manifest_digest, "private_manifest"),
        (preparation_path, preparation_digest, "private_manifest_receipt"),
        (Path(args.promotion_receipt), promotion_digest, "promotion_receipt"),
        (catchup_path, catchup_digest, "catchup_receipt"),
        (
            maintenance_journal,
            maintenance_journal_digest,
            "maintenance_journal",
        ),
        (
            web_maintenance_journal,
            web_maintenance_digest,
            "web_maintenance_journal",
        ),
        (runtime_source, source_digest, "source"),
        (
            legacy_product_manifest,
            legacy_product_manifest_digest,
            "legacy_product_manifest",
        ),
    ):
        _path, payload = _secure_file(path, label=label)
        if _digest(payload) != expected:
            _fail(f"{label}_cas_mismatch")

    return (
        Binding(
            release_sha=release_sha,
            release_tree=release_tree,
            release_checkout_path_sha256=_digest(
                str(release_checkout).encode("utf-8")
            ),
            source_manifest_sha256=source_manifest_digest,
            legacy_product_manifest_sha256=legacy_product_manifest_digest,
            private_manifest_sha256=private_manifest_digest,
            private_manifest_receipt_sha256=preparation_digest,
            promotion_receipt_sha256=promotion_digest,
            catchup_receipt_sha256=catchup_digest,
            maintenance_journal_sha256=maintenance_journal_digest,
            maintenance_journal_path_sha256=_digest(
                str(maintenance_journal).encode("utf-8")
            ),
            web_maintenance_journal_sha256=web_maintenance_digest,
            web_maintenance_journal_path_sha256=_digest(
                str(web_maintenance_journal).encode("utf-8")
            ),
            source_sha256_before=source_digest,
            runtime_source_path_sha256=_digest(
                str(runtime_source).encode("utf-8")
            ),
        ),
        runtime_source,
    )


def _revalidate_committed_handoff(args: argparse.Namespace) -> None:
    try:
        queue_cutover.market_handoff.validate_committed_handoff(
            journal=Path(args.maintenance_journal),
            expected_journal_sha256=args.expected_maintenance_journal_sha256,
            release_sha=args.expected_release_sha,
            expected_primary_verification_sha256=(
                args.expected_promotion_receipt_sha256
            ),
            host_role="bot",
        )
    except queue_cutover.market_handoff.CollectorHandoffError:
        _fail("market_maintenance_revalidation_failed", stage="maintenance")
    web_receipt, web_payload = _read_json(
        Path(args.web_maintenance_journal), label="web_maintenance_journal"
    )
    if _digest(web_payload) != args.expected_web_maintenance_journal_sha256:
        _fail("web_maintenance_journal_cas_mismatch", stage="maintenance")
    try:
        _validate_web_maintenance_receipt(
            web_receipt,
            release_sha=args.expected_release_sha,
            primary_verification_sha256=args.expected_promotion_receipt_sha256,
        )
    except PromotionError as exc:
        _fail(exc.reason_code, stage="maintenance")


def _revalidate_fresh_promotion_evidence(
    args: argparse.Namespace,
    *,
    binding: Binding,
) -> Mapping[str, object]:
    """Re-read and time-check the exact immutable evidence at terminal time."""

    catchup, catchup_payload = _read_json(
        Path(args.catchup_receipt), label="catchup_receipt"
    )
    if _digest(catchup_payload) != binding.catchup_receipt_sha256:
        _fail("catchup_receipt_cas_mismatch", stage="postdeploy")
    try:
        catchup_age = _validate_catchup_receipt(
            catchup, release_sha=binding.release_sha
        )
    except PromotionError as exc:
        raise PromotionError(exc.reason_code, stage="postdeploy") from exc
    promotion, promotion_payload = _read_json(
        Path(args.promotion_receipt), label="promotion_receipt"
    )
    if _digest(promotion_payload) != binding.promotion_receipt_sha256:
        _fail("promotion_receipt_cas_mismatch", stage="postdeploy")
    try:
        _validate_promotion_receipt(
            promotion,
            release_sha=binding.release_sha,
            release_tree=binding.release_tree,
            catchup_receipt_sha256=binding.catchup_receipt_sha256,
            catchup_age_seconds=catchup_age,
        )
    except PromotionError as exc:
        raise PromotionError(exc.reason_code, stage="postdeploy") from exc
    return promotion


def _validated_product_readiness(
    deployment: Mapping[str, object],
    *,
    promotion: Mapping[str, object],
) -> dict[str, object]:
    report = deployment.get("report")
    readiness = report.get("product_readiness") if isinstance(report, Mapping) else None
    snapshot = promotion.get("snapshot")
    if not isinstance(readiness, Mapping) or not isinstance(snapshot, Mapping):
        _fail("postdeploy_product_readiness_missing", stage="postdeploy")
    age = readiness.get("maximum_snapshot_age_seconds")
    version = readiness.get("snapshot_version")
    if (
        readiness.get("consumer_count") != 3
        or readiness.get("required_source_input_trace_count") != 9
        or isinstance(age, bool)
        or not isinstance(age, (int, float))
        or not 0 <= float(age) <= PROMOTION_MAXIMUM_AGE_SECONDS
        or isinstance(version, bool)
        or not isinstance(version, int)
        or version != snapshot.get("snapshot_version")
        or readiness.get("snapshot_digest") != snapshot.get("file_sha256")
        or readiness.get("snapshot_hash") != snapshot.get("snapshot_hash")
        or HEX64.fullmatch(
            str(readiness.get("source_input_trace_sha256") or "")
        )
        is None
    ):
        _fail("postdeploy_product_readiness_invalid", stage="postdeploy")
    return {
        "consumer_count": 3,
        "snapshot_digest": str(readiness["snapshot_digest"]),
        "snapshot_hash": str(readiness["snapshot_hash"]),
        "snapshot_version": int(version),
        "maximum_snapshot_age_seconds": round(float(age), 3),
        "required_source_input_trace_count": 9,
        "source_input_trace_sha256": str(
            readiness["source_input_trace_sha256"]
        ),
    }


def _postdeploy_receipt_payload(
    *,
    binding: Binding,
    source_sha256: str,
    promotion: Mapping[str, object],
    deployment: Mapping[str, object],
    legacy_state: Mapping[str, object],
    snapshot_state: Mapping[str, object],
    outbox_state: Mapping[str, object],
) -> dict[str, object]:
    readiness = _validated_product_readiness(deployment, promotion=promotion)
    if legacy_state != {
        "status": "verified",
        "legacy_input_units_active": 0,
        "legacy_input_timers_enabled": 0,
        "unit_count": 6,
    }:
        _fail("postdeploy_legacy_runtime_active", stage="postdeploy")
    if snapshot_state != {
        "status": "verified",
        "snapshot_digest": readiness["snapshot_digest"],
        "consumer_artifact_count": 3,
    }:
        _fail("postdeploy_snapshot_identity_invalid", stage="postdeploy")
    if outbox_state != {"status": "verified", "open_outbox": 0}:
        _fail("postdeploy_outbox_not_drained", stage="postdeploy")
    image_ids = promotion.get("image_ids")
    if not isinstance(image_ids, Mapping):
        _fail("promotion_receipt_contract_invalid", stage="postdeploy")
    return {
        "schema": POSTDEPLOY_RECEIPT_SCHEMA,
        "status": "PASS",
        "verified_at_utc": _utc_text(),
        "maximum_age_seconds": PROMOTION_MAXIMUM_AGE_SECONDS,
        "release_sha": binding.release_sha,
        "release_tree": binding.release_tree,
        "image_ids": {"bot": image_ids["bot"], "web": image_ids["web"]},
        "source_sha256": source_sha256,
        "private_manifest_sha256": binding.private_manifest_sha256,
        "private_manifest_receipt_sha256": (
            binding.private_manifest_receipt_sha256
        ),
        "promotion_receipt_sha256": binding.promotion_receipt_sha256,
        "catchup_receipt_sha256": binding.catchup_receipt_sha256,
        "maintenance_journal_sha256": binding.maintenance_journal_sha256,
        "web_maintenance_journal_sha256": (
            binding.web_maintenance_journal_sha256
        ),
        "product_readiness": readiness,
        "queue_owner": "queue-v1",
        "legacy_collectors_disabled": True,
        "outbox_zero_verified": True,
        "bot_web_same_snapshot_digest": True,
        "payload_values_included": False,
        "pii_included": False,
        "secrets_disclosed": False,
    }


def _write_postdeploy_receipt(
    path: Path,
    *,
    binding: Binding,
    source_sha256: str,
    promotion: Mapping[str, object],
    deployment: Mapping[str, object],
    operations: queue_cutover.ProductionOperations,
) -> str:
    try:
        legacy_state = operations.private_primary_legacy_inputs_off()
    except Exception:
        _fail("postdeploy_legacy_runtime_recheck_failed", stage="postdeploy")
    snapshot = promotion.get("snapshot")
    if not isinstance(snapshot, Mapping):
        _fail("promotion_receipt_contract_invalid", stage="postdeploy")
    try:
        snapshot_state = operations.private_primary_snapshot_identity(
            expected_digest=str(snapshot.get("file_sha256") or "")
        )
    except Exception:
        _fail("postdeploy_snapshot_runtime_recheck_failed", stage="postdeploy")
    try:
        outbox_state = operations.private_primary_publication_outbox_zero()
    except Exception:
        _fail("postdeploy_outbox_runtime_recheck_failed", stage="postdeploy")
    payload = _postdeploy_receipt_payload(
        binding=binding,
        source_sha256=source_sha256,
        promotion=promotion,
        deployment=deployment,
        legacy_state=legacy_state,
        snapshot_state=snapshot_state,
        outbox_state=outbox_state,
    )
    _write_final_receipt(path, payload)
    return _final_receipt_sha256(payload)


def _validate_postdeploy_receipt(
    path: Path,
    *,
    expected_sha256: str,
    binding: Binding,
    source_sha256: str,
    promotion: Mapping[str, object],
) -> None:
    receipt, payload = _read_json(path, label="postdeploy_receipt")
    if _digest(payload) != expected_sha256:
        _fail("postdeploy_receipt_cas_mismatch", stage="recovery")
    try:
        verified = datetime.fromisoformat(
            str(receipt.get("verified_at_utc") or "").replace("Z", "+00:00")
        )
    except ValueError:
        _fail("postdeploy_receipt_time_invalid", stage="recovery")
    if verified.tzinfo is None:
        _fail("postdeploy_receipt_time_invalid", stage="recovery")
    age = (
        datetime.now(timezone.utc) - verified.astimezone(timezone.utc)
    ).total_seconds()
    readiness = receipt.get("product_readiness")
    readiness_age = (
        readiness.get("maximum_snapshot_age_seconds")
        if isinstance(readiness, Mapping)
        else None
    )
    image_ids = promotion.get("image_ids")
    if (
        age < 0
        or age > PROMOTION_MAXIMUM_AGE_SECONDS
        or isinstance(readiness_age, bool)
        or not isinstance(readiness_age, (int, float))
        or float(readiness_age) + age > PROMOTION_MAXIMUM_AGE_SECONDS
        or receipt.get("schema") != POSTDEPLOY_RECEIPT_SCHEMA
        or receipt.get("status") != "PASS"
        or receipt.get("maximum_age_seconds") != PROMOTION_MAXIMUM_AGE_SECONDS
        or receipt.get("release_sha") != binding.release_sha
        or receipt.get("release_tree") != binding.release_tree
        or receipt.get("image_ids") != image_ids
        or receipt.get("source_sha256") != source_sha256
        or receipt.get("private_manifest_sha256")
        != binding.private_manifest_sha256
        or receipt.get("private_manifest_receipt_sha256")
        != binding.private_manifest_receipt_sha256
        or receipt.get("promotion_receipt_sha256")
        != binding.promotion_receipt_sha256
        or receipt.get("catchup_receipt_sha256")
        != binding.catchup_receipt_sha256
        or receipt.get("maintenance_journal_sha256")
        != binding.maintenance_journal_sha256
        or receipt.get("web_maintenance_journal_sha256")
        != binding.web_maintenance_journal_sha256
        or not isinstance(readiness, Mapping)
        or readiness.get("consumer_count") != 3
        or readiness.get("required_source_input_trace_count") != 9
        or HEX64.fullmatch(
            str(readiness.get("source_input_trace_sha256") or "")
        )
        is None
        or readiness.get("snapshot_digest")
        != promotion.get("snapshot", {}).get("file_sha256")
        or readiness.get("snapshot_hash")
        != promotion.get("snapshot", {}).get("snapshot_hash")
        or receipt.get("queue_owner") != "queue-v1"
        or receipt.get("legacy_collectors_disabled") is not True
        or receipt.get("outbox_zero_verified") is not True
        or receipt.get("bot_web_same_snapshot_digest") is not True
        or receipt.get("payload_values_included") is not False
        or receipt.get("pii_included") is not False
        or receipt.get("secrets_disclosed") is not False
    ):
        _fail("postdeploy_receipt_contract_invalid", stage="recovery")


def _exclusive_write(path: Path, payload: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb", closefd=False) as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        os.close(descriptor)
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def _receipt_payload(
    *,
    status_value: str,
    binding: Binding | None,
    reason_code: str | None,
    recovery_reason_code: str | None,
    failed_stage: str | None,
    source_sha256_after: str | None,
    activation_receipt_sha256: str | None,
    rollback_receipt_sha256: str | None,
    private_deploy_completed: bool,
    legacy_redeploy_completed: bool,
    postdeploy_receipt_sha256: str | None = None,
) -> dict[str, object]:
    return {
        "schema": RECEIPT_SCHEMA,
        "status": status_value,
        "created_at_utc": _utc_text(),
        "action": "PROMOTE_PRIVATE_PRIMARY_PRODUCT",
        "release_sha": binding.release_sha if binding else None,
        "release_tree": binding.release_tree if binding else None,
        "source_manifest_sha256": (
            binding.source_manifest_sha256 if binding else None
        ),
        "legacy_product_manifest_sha256": (
            binding.legacy_product_manifest_sha256 if binding else None
        ),
        "private_manifest_sha256": (
            binding.private_manifest_sha256 if binding else None
        ),
        "private_manifest_receipt_sha256": (
            binding.private_manifest_receipt_sha256 if binding else None
        ),
        "promotion_receipt_sha256": (
            binding.promotion_receipt_sha256 if binding else None
        ),
        "catchup_receipt_sha256": (
            binding.catchup_receipt_sha256 if binding else None
        ),
        "maintenance_journal_sha256": (
            binding.maintenance_journal_sha256 if binding else None
        ),
        "maintenance_journal_path_sha256": (
            binding.maintenance_journal_path_sha256 if binding else None
        ),
        "web_maintenance_journal_sha256": (
            binding.web_maintenance_journal_sha256 if binding else None
        ),
        "web_maintenance_journal_path_sha256": (
            binding.web_maintenance_journal_path_sha256 if binding else None
        ),
        "runtime_source_path_sha256": (
            binding.runtime_source_path_sha256 if binding else None
        ),
        "source_sha256_before": (
            binding.source_sha256_before if binding else None
        ),
        "source_sha256_after": source_sha256_after,
        "activation_receipt_sha256": activation_receipt_sha256,
        "rollback_receipt_sha256": rollback_receipt_sha256,
        "postdeploy_receipt_sha256": postdeploy_receipt_sha256,
        "private_deploy_completed": private_deploy_completed,
        "legacy_redeploy_completed": legacy_redeploy_completed,
        "reason_code": reason_code,
        "recovery_reason_code": recovery_reason_code,
        "failed_stage": failed_stage,
        "payload_values_included": False,
        "pii_included": False,
        "secrets_disclosed": False,
    }


def _write_final_receipt(path: Path, payload: Mapping[str, object]) -> None:
    try:
        _exclusive_write(
            path,
            (
                json.dumps(payload, sort_keys=True, separators=(",", ":"))
                + "\n"
            ).encode("utf-8"),
        )
    except OSError:
        _fail("receipt_write_failed", stage="receipt")


def _final_receipt_sha256(payload: Mapping[str, object]) -> str:
    """Return the digest of the exact bytes written by `_write_final_receipt`."""

    return _digest(
        (
            json.dumps(payload, sort_keys=True, separators=(",", ":"))
            + "\n"
        ).encode("utf-8")
    )


def _transaction_binding_payload(
    *,
    args: argparse.Namespace,
    binding: Binding,
    transaction_dir: Path,
    backup_dir: Path,
    activation_path: Path,
    rollback_path: Path,
    legacy_product_manifest: Path,
    receipt_path: Path,
    phase_journal: Path,
) -> dict[str, object]:
    value: dict[str, object] = {
        "transaction_id": args.transaction_id,
        "binding": _binding_payload(binding),
        "transaction_dir_path_sha256": _digest(str(transaction_dir).encode()),
        "backup_dir_path_sha256": _digest(str(backup_dir).encode()),
        "activation_path_sha256": _digest(str(activation_path).encode()),
        "rollback_path_sha256": _digest(str(rollback_path).encode()),
        "legacy_product_manifest_path_sha256": _digest(
            str(legacy_product_manifest).encode()
        ),
        "receipt_path_sha256": _digest(str(receipt_path).encode()),
        "phase_journal_path_sha256": _digest(str(phase_journal).encode()),
    }
    value["transaction_binding_sha256"] = _digest(
        (
            json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode()
    )
    return value


def _validate_transaction_binding(
    value: object,
    *,
    args: argparse.Namespace,
    transaction_dir: Path,
    backup_dir: Path,
    activation_path: Path,
    rollback_path: Path,
    legacy_product_manifest: Path,
    receipt_path: Path,
    phase_journal: Path,
) -> Binding:
    if not isinstance(value, dict):
        _fail("recovery_transaction_binding_invalid", stage="recovery")
    binding = _binding_from_payload(value.get("binding"))
    expected = _transaction_binding_payload(
        args=args,
        binding=binding,
        transaction_dir=transaction_dir,
        backup_dir=backup_dir,
        activation_path=activation_path,
        rollback_path=rollback_path,
        legacy_product_manifest=legacy_product_manifest,
        receipt_path=receipt_path,
        phase_journal=phase_journal,
    )
    if value != expected:
        _fail("recovery_transaction_binding_invalid", stage="recovery")
    return binding


def _terminal_receipt_binding(
    payload: Mapping[str, object],
    *,
    transaction_id: str,
    journal: queue_cutover.PhaseJournal,
) -> dict[str, object]:
    result = dict(payload)
    result.update(
        {
            "transaction_id": transaction_id,
            "phase_journal_path_sha256": _digest(str(journal.path).encode()),
            "recovery_phase_journal_input_sha256": journal.payload.get(
                "recovery_phase_journal_input_sha256"
            ),
        }
    )
    return result


def _persist_terminal_result(
    *,
    journal: queue_cutover.PhaseJournal,
    journal_status: str,
    receipt_path: Path,
    payload: Mapping[str, object],
    **journal_facts: object,
) -> None:
    """Commit the authoritative journal state before exposing its receipt.

    The two files cannot be replaced atomically as a pair.  The phase journal
    is therefore authoritative: it first records the digest of the exact
    receipt bytes and only then is the exclusive receipt created.  A crash or
    write failure can leave a terminal journal without its convenience
    receipt, but can never leave a trusted PASS receipt followed by an
    automatic rollback.
    """

    journal.update(
        journal_status,
        receipt_sha256=_final_receipt_sha256(payload),
        receipt_path_sha256=_digest(str(receipt_path).encode()),
        terminal_receipt_payload=dict(payload),
        receipt_written=False,
        **journal_facts,
    )
    _write_final_receipt(receipt_path, payload)
    journal.update(
        journal_status,
        receipt_sha256=_final_receipt_sha256(payload),
        receipt_path_sha256=_digest(str(receipt_path).encode()),
        terminal_receipt_payload=dict(payload),
        receipt_written=True,
        **journal_facts,
    )


def _persisted_journal_status(
    journal: queue_cutover.PhaseJournal | None,
) -> str | None:
    if journal is None:
        return None
    try:
        value, _payload = _read_json(journal.path, label="queue_phase_journal")
    except PromotionError:
        return None
    status = value.get("status")
    return str(status) if isinstance(status, str) else None


def _complete_terminal_receipt_from_journal(
    *,
    journal: queue_cutover.PhaseJournal,
    receipt_path: Path,
    transaction_id: str,
) -> tuple[dict[str, object], int] | None:
    payload = journal.payload
    status = str(payload.get("status") or "")
    terminal = payload.get("terminal_receipt_payload")
    if status not in {
        "applied",
        "rolled_back",
        "failed_recovered",
        "recovery_failed",
    }:
        return None
    if (
        not isinstance(terminal, dict)
        or terminal.get("transaction_id") != transaction_id
        or terminal.get("phase_journal_path_sha256")
        != _digest(str(journal.path).encode())
        or payload.get("receipt_path_sha256")
        != _digest(str(receipt_path).encode())
        or payload.get("receipt_sha256") != _final_receipt_sha256(terminal)
    ):
        _fail("terminal_receipt_journal_invalid", stage="recovery")
    if receipt_path.exists() or receipt_path.is_symlink():
        _path, existing = _secure_file(receipt_path, label="receipt")
        if _digest(existing) != payload["receipt_sha256"]:
            _fail("terminal_receipt_conflict", stage="recovery")
    else:
        _write_final_receipt(receipt_path, terminal)
    journal.update(
        status,
        receipt_sha256=payload["receipt_sha256"],
        receipt_path_sha256=payload["receipt_path_sha256"],
        terminal_receipt_payload=terminal,
        receipt_written=True,
    )
    code = 0 if terminal.get("status") == "PASS" else (
        3 if terminal.get("status") == "ROLLED_BACK" else 4
    )
    return dict(terminal), code


def _runtime_source_for_lock(args: argparse.Namespace) -> Path:
    source_manifest, payload = _secure_file(
        Path(args.source_manifest), label="source_manifest"
    )
    _require_manifest_scope(source_manifest, label="source_manifest")
    values = _read_env(payload, label="source_manifest")
    source_text = values.get("RUNTIME_ENV_SOURCE_PATH", "")
    if not source_text:
        _fail("runtime_source_identity_mismatch")
    source, _payload = _secure_file(Path(source_text), label="runtime_source")
    _require_production_scope(source, label="runtime_source")
    return source


def _validate_recovery_artifacts(
    *,
    args: argparse.Namespace,
    binding: Binding,
    source: Path,
    legacy_product_manifest: Path,
) -> None:
    """Validate exact immutable identities without treating stale PASS as new authority."""

    if (
        args.expected_release_sha != binding.release_sha
        or args.expected_release_tree != binding.release_tree
        or args.expected_source_manifest_sha256 != binding.source_manifest_sha256
        or args.expected_private_manifest_sha256 != binding.private_manifest_sha256
        or args.expected_private_manifest_receipt_sha256
        != binding.private_manifest_receipt_sha256
        or args.expected_promotion_receipt_sha256
        != binding.promotion_receipt_sha256
        or args.expected_catchup_receipt_sha256 != binding.catchup_receipt_sha256
        or args.expected_maintenance_journal_sha256
        != binding.maintenance_journal_sha256
        or args.expected_web_maintenance_journal_sha256
        != binding.web_maintenance_journal_sha256
    ):
        _fail("recovery_argument_binding_mismatch", stage="recovery")
    checks = (
        (Path(args.source_manifest), binding.source_manifest_sha256, "source_manifest"),
        (Path(args.private_manifest), binding.private_manifest_sha256, "private_manifest"),
        (
            Path(args.private_manifest_receipt),
            binding.private_manifest_receipt_sha256,
            "private_manifest_receipt",
        ),
        (Path(args.promotion_receipt), binding.promotion_receipt_sha256, "promotion_receipt"),
        (Path(args.catchup_receipt), binding.catchup_receipt_sha256, "catchup_receipt"),
        (
            Path(args.maintenance_journal),
            binding.maintenance_journal_sha256,
            "maintenance_journal",
        ),
        (
            Path(args.web_maintenance_journal),
            binding.web_maintenance_journal_sha256,
            "web_maintenance_journal",
        ),
        (
            legacy_product_manifest,
            binding.legacy_product_manifest_sha256,
            "legacy_product_manifest",
        ),
    )
    for path, expected, label in checks:
        _path, payload = _secure_file(path, label=label)
        if _digest(payload) != expected:
            _fail(f"{label}_cas_mismatch", stage="recovery")
    if _digest(str(Path(args.maintenance_journal)).encode()) != binding.maintenance_journal_path_sha256:
        _fail("maintenance_journal_identity_mismatch", stage="recovery")
    if (
        _digest(str(Path(args.web_maintenance_journal)).encode())
        != binding.web_maintenance_journal_path_sha256
    ):
        _fail("web_maintenance_journal_identity_mismatch", stage="recovery")
    if _digest(str(source).encode()) != binding.runtime_source_path_sha256:
        _fail("runtime_source_identity_mismatch", stage="recovery")
    release_checkout = Path(args.release_checkout)
    if (
        _digest(str(release_checkout).encode("utf-8"))
        != binding.release_checkout_path_sha256
    ):
        _fail("release_checkout_identity_mismatch", stage="recovery")
    _git_identity(release_checkout, binding.release_sha, binding.release_tree)


def _recover_source_updater_pending(
    *,
    args: argparse.Namespace,
    binding: Binding,
    source: Path,
    private_manifest: Path,
    backup_dir: Path,
    activation_path: Path,
    rollback_path: Path,
    source_lock: queue_cutover.ImmutableSourceLock,
    recovery_action: str,
) -> dict[str, object] | None:
    pending = source.parent / source_updater.PENDING_NAME
    if not pending.exists() and not pending.is_symlink():
        return None
    _pending, pending_payload = _secure_file(pending, label="source_update_pending")
    pending_digest = _digest(pending_payload)
    try:
        document = json.loads(pending_payload)
    except (UnicodeDecodeError, json.JSONDecodeError):
        _fail("source_update_pending_invalid", stage="recovery")
    action = document.get("action") if isinstance(document, dict) else None
    receipt = (
        rollback_path
        if action == "RESTORE_EXACT_PRE_ACTIVATION_SOURCE"
        else activation_path
    )
    updater_args = argparse.Namespace(
        manifest=str(private_manifest),
        backup_dir=str(backup_dir),
        receipt=str(receipt),
        expected_pending_sha256=pending_digest,
        recovery_action=(
            "rollback"
            if action == "RESTORE_EXACT_PRE_ACTIVATION_SOURCE"
            else recovery_action
        ),
        recovery_confirm=source_updater.PRIVATE_PRIMARY_RECOVERY_CONFIRMATION,
        promotion_receipt=str(args.promotion_receipt),
        expected_promotion_receipt_sha256=binding.promotion_receipt_sha256,
        expected_release_sha=binding.release_sha,
        expected_release_tree=binding.release_tree,
    )
    if source_lock.descriptor is None:
        _fail("queue_source_lock_not_held", stage="recovery")
    try:
        return source_updater.recover_private_primary_with_held_source_lock(
            updater_args,
            source,
            source_lock_descriptor=source_lock.descriptor,
        )
    except source_updater.SourceUpdateError as exc:
        _fail(str(exc), stage="source_recovery")


def _queue_git_binding(
    binding: Binding, release_checkout: Path
) -> dict[str, str]:
    observed = _git_identity(
        release_checkout, binding.release_sha, binding.release_tree
    )
    if (
        observed.get("branch") != "main"
        or observed.get("worktree") != "clean"
        or observed.get("head") != binding.release_sha
        or observed.get("origin_main") != binding.release_sha
    ):
        _fail("queue_git_binding_invalid", stage="queue_recheck")
    return dict(observed)


def _queue_recheck(
    *,
    binding: Binding,
    source: Path,
    expected_source_sha256: str,
    manifest: Path,
    expected_manifest_sha256: str,
    run_lock: queue_cutover.ExclusiveRunLock,
    source_lock: queue_cutover.ImmutableSourceLock,
    operations: queue_cutover.ProductionOperations,
) -> dict[str, object]:
    try:
        run_lock.binding()
        if source_lock.descriptor is None:
            _fail("queue_source_lock_not_held", stage="queue_recheck")
        source_updater._verify_inherited_source_lock(
            source, source_lock.descriptor
        )
        _assert_file_digest(
            source, expected_source_sha256, label="runtime_source"
        )
        _assert_file_digest(
            manifest, expected_manifest_sha256, label="deploy_manifest"
        )
        git = _queue_git_binding(binding, operations.release_root)
        source_values = queue_cutover.parse_env_file(source)
        if queue_cutover.source_profile(source_values) != "queue-v1":
            _fail("queue_owner_profile_invalid", stage="queue_recheck")
        inventory = operations.executor_inventory()
        queue_cutover._assert_inventory(
            inventory, count=1, owner="queue-v1"
        )
        runtime = operations.runtime_contract(
            source_values, expected_owner="queue-v1"
        )
    except PromotionError:
        raise
    except Exception:
        _fail("queue_runtime_recheck_failed", stage="queue_recheck")
    return {"git": git, "inventory": inventory, "runtime": runtime}


def _revalidate_terminal_pass(
    *,
    args: argparse.Namespace,
    binding: Binding,
    source: Path,
    source_sha256: str,
    manifest: Path,
    run_lock: queue_cutover.ExclusiveRunLock,
    source_lock: queue_cutover.ImmutableSourceLock,
    operations: queue_cutover.ProductionOperations,
    terminal_payload: Mapping[str, object],
    postdeploy_path: Path,
) -> None:
    _queue_recheck(
        binding=binding,
        source=source,
        expected_source_sha256=source_sha256,
        manifest=manifest,
        expected_manifest_sha256=binding.private_manifest_sha256,
        run_lock=run_lock,
        source_lock=source_lock,
        operations=operations,
    )
    _revalidate_committed_handoff(args)
    promotion = _revalidate_fresh_promotion_evidence(args, binding=binding)
    try:
        legacy_state = operations.private_primary_legacy_inputs_off()
    except Exception:
        _fail("postdeploy_legacy_runtime_recheck_failed", stage="recovery")
    snapshot = promotion.get("snapshot")
    if not isinstance(snapshot, Mapping):
        _fail("promotion_receipt_contract_invalid", stage="recovery")
    try:
        snapshot_state = operations.private_primary_snapshot_identity(
            expected_digest=str(snapshot.get("file_sha256") or "")
        )
    except Exception:
        _fail("postdeploy_snapshot_runtime_recheck_failed", stage="recovery")
    try:
        outbox_state = operations.private_primary_publication_outbox_zero()
    except Exception:
        _fail("postdeploy_outbox_runtime_recheck_failed", stage="recovery")
    if legacy_state != {
        "status": "verified",
        "legacy_input_units_active": 0,
        "legacy_input_timers_enabled": 0,
        "unit_count": 6,
    }:
        _fail("postdeploy_legacy_runtime_active", stage="recovery")
    if snapshot_state != {
        "status": "verified",
        "snapshot_digest": str(snapshot.get("file_sha256") or ""),
        "consumer_artifact_count": 3,
    }:
        _fail("postdeploy_snapshot_identity_invalid", stage="recovery")
    if outbox_state != {"status": "verified", "open_outbox": 0}:
        _fail("postdeploy_outbox_not_drained", stage="recovery")
    digest = str(terminal_payload.get("postdeploy_receipt_sha256") or "")
    if HEX64.fullmatch(digest) is None:
        _fail("postdeploy_receipt_missing", stage="recovery")
    _validate_postdeploy_receipt(
        postdeploy_path,
        expected_sha256=digest,
        binding=binding,
        source_sha256=source_sha256,
        promotion=promotion,
    )


def _with_updater_manifest(
    manifest: Path,
    callback: Callable[[], int],
) -> int:
    previous_manifest = source_updater.APPROVED_MANIFEST_PATH
    previous_roots = source_updater.APPROVED_MANIFEST_ROOTS
    source_updater.APPROVED_MANIFEST_PATH = manifest
    if not any(
        manifest.parent == root or root in manifest.parents
        for root in previous_roots
    ):
        source_updater.APPROVED_MANIFEST_ROOTS = (
            *previous_roots,
            manifest.parent,
        )
    try:
        with redirect_stdout(io.StringIO()):
            return int(callback())
    finally:
        source_updater.APPROVED_MANIFEST_PATH = previous_manifest
        source_updater.APPROVED_MANIFEST_ROOTS = previous_roots


def _activate_in_process(
    *,
    args: argparse.Namespace,
    binding: Binding,
    source: Path,
    private_manifest: Path,
    backup_dir: Path,
    activation_path: Path,
    source_lock: queue_cutover.ImmutableSourceLock,
) -> None:
    if source_lock.descriptor is None:
        _fail("queue_source_lock_not_held", stage="source_activation")
    updater_args = argparse.Namespace(
        manifest=str(private_manifest),
        expected_source_sha256=binding.source_sha256_before,
        expected_manifest_sha256=binding.private_manifest_sha256,
        confirm=ACTIVATION_CONFIRMATION,
        backup_dir=str(backup_dir),
        receipt=str(activation_path),
        promotion_receipt=str(Path(args.promotion_receipt)),
        expected_promotion_receipt_sha256=binding.promotion_receipt_sha256,
        expected_release_sha=binding.release_sha,
        expected_release_tree=binding.release_tree,
    )
    try:
        result = _with_updater_manifest(
            private_manifest,
            lambda: source_updater.activate_private_primary_with_held_source_lock(
                updater_args,
                source,
                source_lock_descriptor=source_lock.descriptor,
            ),
        )
    except Exception:
        _fail("source_updater_failed", stage="source_activation")
    if result != 0:
        _fail("source_updater_failed", stage="source_activation")


def _rollback_in_process(
    *,
    binding: Binding,
    source: Path,
    private_manifest: Path,
    backup_dir: Path,
    activation_path: Path,
    activation_digest: str,
    active_source_digest: str,
    rollback_path: Path,
    source_lock: queue_cutover.ImmutableSourceLock,
) -> None:
    if source_lock.descriptor is None:
        _fail("queue_source_lock_not_held", stage="source_rollback")
    updater_args = argparse.Namespace(
        manifest=str(private_manifest),
        expected_source_sha256=active_source_digest,
        expected_manifest_sha256=binding.private_manifest_sha256,
        confirm=ROLLBACK_CONFIRMATION,
        backup_dir=str(backup_dir),
        receipt=str(rollback_path),
        activation_receipt=str(activation_path),
        expected_activation_receipt_sha256=activation_digest,
    )
    try:
        result = _with_updater_manifest(
            private_manifest,
            lambda: source_updater.rollback_private_primary_with_held_source_lock(
                updater_args,
                source,
                source_lock_descriptor=source_lock.descriptor,
            ),
        )
    except Exception:
        _fail("source_updater_failed", stage="source_rollback")
    if result != 0:
        _fail("source_updater_failed", stage="source_rollback")


def _deploy_with_fresh_authority(
    *,
    binding: Binding,
    source: Path,
    expected_source_sha256: str,
    manifest: Path,
    expected_manifest_sha256: str,
    artifact_dir: Path,
    run_lock: queue_cutover.ExclusiveRunLock,
    source_lock: queue_cutover.ImmutableSourceLock,
    journal: queue_cutover.PhaseJournal,
    operations: queue_cutover.ProductionOperations,
    journal_status: str,
    private_attestation: (
        queue_cutover.PrivatePrimaryDeployAttestation | None
    ) = None,
) -> dict[str, object]:
    _queue_recheck(
        binding=binding,
        source=source,
        expected_source_sha256=expected_source_sha256,
        manifest=manifest,
        expected_manifest_sha256=expected_manifest_sha256,
        run_lock=run_lock,
        source_lock=source_lock,
        operations=operations,
    )
    journal.update(
        journal_status,
        source_sha256=expected_source_sha256,
        deploy_manifest_sha256=expected_manifest_sha256,
        private_primary_attestation=(
            queue_cutover._private_primary_attestation_binding(
                manifest, private_attestation
            )
        ),
    )
    reconciled = queue_cutover.reconcile_deploy_child_fence(
        artifact_dir=artifact_dir,
        journal_path=journal.path,
        expected_source_sha256=expected_source_sha256,
        expected_manifest_sha256=expected_manifest_sha256,
    )
    if reconciled is not None and reconciled.get("status") == "completed":
        return {"authority_path": None, "report": reconciled}
    try:
        authority_path, authority_digest = queue_cutover.create_deploy_authority(
            artifact_dir,
            source,
            _queue_git_binding(binding, operations.release_root),
            run_lock=run_lock,
            journal=journal,
            deploy_manifest=manifest,
            private_primary_attestation=private_attestation,
        )
        report = operations.deploy_official(
            authority_path,
            authority_digest,
            inherited_lock_descriptors=(
                int(run_lock.descriptor),
                int(source_lock.descriptor),
            ),
            **(
                {"private_primary_attestation": private_attestation}
                if private_attestation is not None
                else {}
            ),
        )
    except PromotionError:
        raise
    except Exception:
        _fail("production_deploy_failed", stage="official_deploy")
    return {"authority_path": authority_path, "report": report}


def _activation_receipt(
    path: Path,
    *,
    binding: Binding,
    backup_dir: Path,
    source: Path,
    require_active_source: bool = True,
) -> tuple[Mapping[str, object], str, str]:
    document, payload = _read_json(path, label="activation_receipt")
    after = str(document.get("source_sha256_after") or "")
    backup_name = document.get("backup_file")
    changed_keys = document.get("changed_keys")
    if (
        document.get("schema_version") != 1
        or document.get("action")
        != "ACTIVATE_PRIVATE_PRIMARY_PRODUCT_SNAPSHOTS"
        or document.get("status") != "APPLIED"
        or document.get("source_sha256_before") != binding.source_sha256_before
        or not HEX64.fullmatch(after)
        or document.get("backup_sha256") != binding.source_sha256_before
        or document.get("manifest_sha256") != binding.private_manifest_sha256
        or document.get("promotion_receipt_sha256")
        != binding.promotion_receipt_sha256
        or document.get("release_sha") != binding.release_sha
        or document.get("release_tree") != binding.release_tree
        or not isinstance(backup_name, str)
        or not re.fullmatch(
            r"production-runtime-source\.[A-Za-z0-9.:-]+\.env", backup_name
        )
        or document.get("secrets_disclosed") is not False
        or not isinstance(changed_keys, list)
        or "PRODUCTION_PRODUCT_ESTIMATOR_SNAPSHOT_MODE"
        not in changed_keys
    ):
        _fail("activation_receipt_contract_invalid", stage="source_activation")
    _backup, backup_payload = _secure_file(
        backup_dir / backup_name, label="activation_backup"
    )
    if _digest(backup_payload) != binding.source_sha256_before:
        _fail("activation_backup_digest_mismatch", stage="source_activation")
    if require_active_source:
        _source, source_payload = _secure_file(source, label="runtime_source")
        if _digest(source_payload) != after:
            _fail("source_activation_postcondition_failed", stage="source_activation")
    return document, _digest(payload), after


def _rollback_receipt(
    path: Path,
    *,
    binding: Binding,
    activation_digest: str,
    active_source_digest: str,
    source: Path,
) -> str:
    document, payload = _read_json(path, label="rollback_receipt")
    if (
        document.get("schema_version") != 1
        or document.get("action") != "RESTORE_EXACT_PRE_ACTIVATION_SOURCE"
        or document.get("status") != "APPLIED"
        or document.get("source_sha256_before") != active_source_digest
        or document.get("source_sha256_after") != binding.source_sha256_before
        or document.get("activation_receipt_sha256") != activation_digest
        or document.get("manifest_sha256") != binding.private_manifest_sha256
        or document.get("backup_sha256") != binding.source_sha256_before
        or document.get("secrets_disclosed") is not False
    ):
        _fail("rollback_receipt_contract_invalid", stage="source_rollback")
    _source, source_payload = _secure_file(source, label="runtime_source")
    if _digest(source_payload) != binding.source_sha256_before:
        _fail("source_rollback_postcondition_failed", stage="source_rollback")
    return _digest(payload)


def _recover(
    *,
    binding: Binding,
    source: Path,
    private_manifest: Path,
    legacy_product_manifest: Path,
    backup_dir: Path,
    activation_path: Path,
    rollback_path: Path,
    artifact_dir: Path,
    run_lock: queue_cutover.ExclusiveRunLock,
    source_lock: queue_cutover.ImmutableSourceLock,
    journal: queue_cutover.PhaseJournal,
    release_checkout: Path,
    source_pending_pre_restored: bool = False,
) -> tuple[str, str | None, str | None, bool, str | None]:
    activation_digest: str | None = None
    rollback_digest: str | None = None
    try:
        _source, current_payload = _secure_file(source, label="runtime_source")
        current_digest = _digest(current_payload)
        if activation_path.exists():
            _activation, activation_digest, active_digest = _activation_receipt(
                activation_path,
                binding=binding,
                backup_dir=backup_dir,
                source=source,
                require_active_source=(
                    not rollback_path.exists()
                    and not source_pending_pre_restored
                ),
            )
            if (
                source_pending_pre_restored
                and current_digest == binding.source_sha256_before
                and not rollback_path.exists()
            ):
                journal.update(
                    "product_source_restored",
                    source_after_sha256=binding.source_sha256_before,
                    rollback_receipt_sha256=None,
                    source_updater_pending_recovery=True,
                    legacy_product_manifest_sha256=(
                        binding.legacy_product_manifest_sha256
                    ),
                    private_primary_attestation=None,
                )
            elif not rollback_path.exists():
                if current_digest != active_digest:
                    _fail("source_changed_before_rollback", stage="source_rollback")
                journal.update(
                    "product_source_rollback_pending",
                    activation_receipt_sha256=activation_digest,
                    source_before_sha256=active_digest,
                    source_after_sha256=binding.source_sha256_before,
                )
                _rollback_in_process(
                    binding=binding,
                    source=source,
                    private_manifest=private_manifest,
                    backup_dir=backup_dir,
                    activation_path=activation_path,
                    activation_digest=activation_digest,
                    active_source_digest=active_digest,
                    rollback_path=rollback_path,
                    source_lock=source_lock,
                )
            if rollback_path.exists():
                rollback_digest = _rollback_receipt(
                    rollback_path,
                    binding=binding,
                    activation_digest=activation_digest,
                    active_source_digest=active_digest,
                    source=source,
                )
                journal.update(
                    "product_source_restored",
                    source_after_sha256=binding.source_sha256_before,
                    rollback_receipt_sha256=rollback_digest,
                    legacy_product_manifest_sha256=(
                        binding.legacy_product_manifest_sha256
                    ),
                    private_primary_attestation=None,
                )
        elif current_digest != binding.source_sha256_before:
            _fail("activation_receipt_missing_for_changed_source", stage="source_rollback")

        legacy_operations = queue_cutover.ProductionOperations(
            legacy_product_manifest,
            release_root=release_checkout,
        )
        _deploy_with_fresh_authority(
            binding=binding,
            source=source,
            expected_source_sha256=binding.source_sha256_before,
            manifest=legacy_product_manifest,
            expected_manifest_sha256=binding.legacy_product_manifest_sha256,
            artifact_dir=artifact_dir,
            run_lock=run_lock,
            source_lock=source_lock,
            journal=journal,
            operations=legacy_operations,
            journal_status="legacy_product_redeploy_authorizing",
        )
        _queue_recheck(
            binding=binding,
            source=source,
            expected_source_sha256=binding.source_sha256_before,
            manifest=legacy_product_manifest,
            expected_manifest_sha256=binding.legacy_product_manifest_sha256,
            run_lock=run_lock,
            source_lock=source_lock,
            operations=legacy_operations,
        )
        journal.update(
            "legacy_product_redeployed_and_verified",
            source_after_sha256=binding.source_sha256_before,
            legacy_product_manifest_sha256=(
                binding.legacy_product_manifest_sha256
            ),
        )
        return "ROLLED_BACK", activation_digest, rollback_digest, True, None
    except Exception as unexpected:
        recovery_reason = (
            unexpected.reason_code
            if isinstance(unexpected, PromotionError)
            else "recovery_internal_error"
        )
        return (
            "BLOCKED_MANUAL",
            activation_digest,
            rollback_digest,
            False,
            recovery_reason,
        )


def _validate_recovery_freshness(
    args: argparse.Namespace, *, binding: Binding
) -> None:
    catchup, catchup_payload = _read_json(
        Path(args.catchup_receipt), label="catchup_receipt"
    )
    if _digest(catchup_payload) != binding.catchup_receipt_sha256:
        _fail("catchup_receipt_cas_mismatch", stage="recovery")
    catchup_age = _validate_catchup_receipt(catchup, release_sha=binding.release_sha)
    promotion, promotion_payload = _read_json(
        Path(args.promotion_receipt), label="promotion_receipt"
    )
    if _digest(promotion_payload) != binding.promotion_receipt_sha256:
        _fail("promotion_receipt_cas_mismatch", stage="recovery")
    _validate_promotion_receipt(
        promotion,
        release_sha=binding.release_sha,
        release_tree=binding.release_tree,
        catchup_receipt_sha256=binding.catchup_receipt_sha256,
        catchup_age_seconds=catchup_age,
    )


def _phase_journal_for_recovery(
    path: Path,
    *,
    run_lock: queue_cutover.ExclusiveRunLock,
    original: Mapping[str, object],
    expected_input_sha256: str,
) -> queue_cutover.PhaseJournal:
    if str(original.get("status") or "") in queue_cutover.PHASE_TERMINAL_STATES:
        journal = queue_cutover.PhaseJournal.__new__(queue_cutover.PhaseJournal)
        journal.path = path
        journal.payload = dict(original)
        journal.payload["recovery_phase_journal_input_sha256"] = (
            expected_input_sha256
        )
        return journal
    previous_status = str(original.get("status") or "")
    journal = queue_cutover.PhaseJournal.adopt(path, run_lock=run_lock)
    journal.update(
        "product_recovery_acquired",
        interrupted_status=previous_status,
        recovery_phase_journal_input_sha256=expected_input_sha256,
    )
    return journal


def recover_execute(args: argparse.Namespace) -> tuple[dict[str, object], int]:
    if args.recovery_confirm != RECOVERY_CONFIRMATION:
        _fail("recovery_confirmation_invalid", stage="recovery")
    recovery_action = str(args.recovery_action or "").strip().lower()
    if recovery_action not in {"resume", "rollback"}:
        _fail("recovery_action_invalid", stage="recovery")
    if not TRANSACTION_ID.fullmatch(args.transaction_id or ""):
        _fail("transaction_id_invalid", stage="recovery")

    receipt_path = _under_secure_root(Path(args.receipt), label="receipt")
    transaction_root = _secure_directory(
        Path(args.transaction_root), label="transaction"
    )
    if receipt_path.parent != transaction_root:
        _fail("receipt_transaction_scope_invalid", stage="recovery")
    transaction_dir = _secure_directory(
        transaction_root / args.transaction_id, label="transaction"
    )
    backup_dir = _secure_directory(
        transaction_dir / "production-backups", label="backup_directory"
    )
    activation_path = transaction_dir / "activation.json"
    rollback_path = transaction_dir / "rollback.json"
    postdeploy_path = transaction_dir / "post-deploy-verification.json"
    legacy_product_manifest = transaction_dir / "legacy-product-only.env"
    artifact_dir = _secure_directory(
        Path(args.queue_artifact_dir), label="queue_artifact"
    )
    phase_path = _under_secure_root(
        Path(args.recovery_phase_journal), label="recovery_phase_journal"
    )
    if phase_path.parent != artifact_dir:
        _fail("recovery_phase_journal_scope_invalid", stage="recovery")
    if not HEX64.fullmatch(args.expected_phase_journal_sha256 or ""):
        _fail("recovery_phase_journal_digest_invalid", stage="recovery")
    phase_path, phase_bytes = _secure_file(
        phase_path, label="recovery_phase_journal"
    )
    if _digest(phase_bytes) != args.expected_phase_journal_sha256:
        _fail("recovery_phase_journal_cas_mismatch", stage="recovery")
    try:
        phase_payload = json.loads(phase_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError):
        _fail("recovery_phase_journal_invalid", stage="recovery")
    if (
        not isinstance(phase_payload, dict)
        or phase_payload.get("command") != "product-private-primary-promotion"
        or phase_payload.get("secrets_disclosed") is not False
    ):
        _fail("recovery_phase_journal_invalid", stage="recovery")
    if (
        (receipt_path.exists() or receipt_path.is_symlink())
        and str(phase_payload.get("status") or "")
        not in {
            "applied",
            "rolled_back",
            "failed_recovered",
            "recovery_failed",
        }
    ):
        _fail("terminal_receipt_conflict", stage="recovery")

    source = _runtime_source_for_lock(args)
    source_lock = queue_cutover.ImmutableSourceLock(source)
    run_lock: queue_cutover.ExclusiveRunLock | None = None
    promotion_terminal_pass = False
    source_lock.acquire()
    try:
        run_lock = queue_cutover.ExclusiveRunLock(artifact_dir)
        run_lock.adopt_market_pipeline_maintenance(
            journal=Path(args.maintenance_journal),
            expected_journal_sha256=args.expected_maintenance_journal_sha256,
            expected_primary_verification_sha256=(
                args.expected_promotion_receipt_sha256
            ),
            release_sha=args.expected_release_sha,
            allow_recovery_journal=phase_path,
            allow_interrupted_journal=phase_path,
        )
        journal = _phase_journal_for_recovery(
            phase_path,
            run_lock=run_lock,
            original=phase_payload,
            expected_input_sha256=args.expected_phase_journal_sha256,
        )
        binding = _validate_transaction_binding(
            phase_payload.get("transaction_binding"),
            args=args,
            transaction_dir=transaction_dir,
            backup_dir=backup_dir,
            activation_path=activation_path,
            rollback_path=rollback_path,
            legacy_product_manifest=legacy_product_manifest,
            receipt_path=receipt_path,
            phase_journal=phase_path,
        )
        _validate_recovery_artifacts(
            args=args,
            binding=binding,
            source=source,
            legacy_product_manifest=legacy_product_manifest,
        )
        if str(phase_payload.get("status") or "") in {
            "applied",
            "rolled_back",
            "failed_recovered",
            "recovery_failed",
        }:
            terminal_payload = phase_payload.get("terminal_receipt_payload")
            if not isinstance(terminal_payload, dict) or not HEX64.fullmatch(
                str(terminal_payload.get("source_sha256_after") or "")
            ):
                _fail("terminal_receipt_journal_invalid", stage="recovery")
            expected_source = str(terminal_payload["source_sha256_after"])
            is_private = (
                terminal_payload.get("status") == "PASS"
                or expected_source != binding.source_sha256_before
            )
            terminal_manifest = (
                Path(args.private_manifest)
                if is_private
                else legacy_product_manifest
            )
            terminal_manifest_digest = (
                binding.private_manifest_sha256
                if is_private
                else binding.legacy_product_manifest_sha256
            )
            terminal_operations = queue_cutover.ProductionOperations(
                terminal_manifest,
                release_root=Path(args.release_checkout),
            )
            if is_private:
                queue_cutover.bind_private_primary_deploy_attestation(
                    terminal_manifest,
                    manifest_sha256=binding.private_manifest_sha256,
                    receipt_path=Path(args.private_manifest_receipt),
                    receipt_sha256=binding.private_manifest_receipt_sha256,
                )
            if terminal_payload.get("status") == "PASS":
                _revalidate_terminal_pass(
                    args=args,
                    binding=binding,
                    source=source,
                    source_sha256=expected_source,
                    manifest=terminal_manifest,
                    run_lock=run_lock,
                    source_lock=source_lock,
                    operations=terminal_operations,
                    terminal_payload=terminal_payload,
                    postdeploy_path=postdeploy_path,
                )
            else:
                _queue_recheck(
                    binding=binding,
                    source=source,
                    expected_source_sha256=expected_source,
                    manifest=terminal_manifest,
                    expected_manifest_sha256=terminal_manifest_digest,
                    run_lock=run_lock,
                    source_lock=source_lock,
                    operations=terminal_operations,
                )
        terminal = _complete_terminal_receipt_from_journal(
            journal=journal,
            receipt_path=receipt_path,
            transaction_id=args.transaction_id,
        )
        if terminal is not None:
            promotion_terminal_pass = terminal[0].get("status") == "PASS"
            return terminal

        private_manifest = Path(args.private_manifest)
        if recovery_action == "resume":
            # Freshness is checked only for continuation toward a new private
            # deployment.  It is deliberately not required for safe rollback.
            _validate_recovery_freshness(args, binding=binding)
        source_pending_result = _recover_source_updater_pending(
            args=args,
            binding=binding,
            source=source,
            private_manifest=private_manifest,
            backup_dir=backup_dir,
            activation_path=activation_path,
            rollback_path=rollback_path,
            source_lock=source_lock,
            recovery_action=recovery_action,
        )
        source_pending_pre_restored = bool(
            isinstance(source_pending_result, dict)
            and source_pending_result.get("status") == "RECOVERED_ROLLED_BACK"
        )
        if source_pending_result is not None:
            journal.update(
                "source_updater_pending_recovered",
                source_updater_pending_status=source_pending_result.get("status"),
                source_updater_transaction_sha256=source_pending_result.get(
                    "transaction_sha256"
                ),
            )

        if recovery_action == "resume":
            if not activation_path.exists():
                _source, current = _secure_file(source, label="runtime_source")
                if _digest(current) != binding.source_sha256_before:
                    _fail("recovery_source_state_ambiguous", stage="recovery")
                journal.update(
                    "product_source_activation_pending",
                    transaction_binding=phase_payload["transaction_binding"],
                    activation_path_sha256=_digest(str(activation_path).encode()),
                )
                _activate_in_process(
                    args=args,
                    binding=binding,
                    source=source,
                    private_manifest=private_manifest,
                    backup_dir=backup_dir,
                    activation_path=activation_path,
                    source_lock=source_lock,
                )
            _activation, activation_digest, source_after = _activation_receipt(
                activation_path,
                binding=binding,
                backup_dir=backup_dir,
                source=source,
            )
            private_attestation = queue_cutover.bind_private_primary_deploy_attestation(
                private_manifest,
                manifest_sha256=binding.private_manifest_sha256,
                receipt_path=Path(args.private_manifest_receipt),
                receipt_sha256=binding.private_manifest_receipt_sha256,
            )
            operations = queue_cutover.ProductionOperations(
                private_manifest,
                release_root=Path(args.release_checkout),
            )
            _revalidate_committed_handoff(args)
            deployment = _deploy_with_fresh_authority(
                binding=binding,
                source=source,
                expected_source_sha256=source_after,
                manifest=private_manifest,
                expected_manifest_sha256=binding.private_manifest_sha256,
                artifact_dir=artifact_dir,
                run_lock=run_lock,
                source_lock=source_lock,
                journal=journal,
                operations=operations,
                journal_status="private_product_recovery_deploy_authorizing",
                private_attestation=private_attestation,
            )
            journal.update(
                "private_product_deploy_returned",
                source_after_sha256=source_after,
                activation_receipt_sha256=activation_digest,
                deploy_manifest_sha256=binding.private_manifest_sha256,
            )
            _queue_recheck(
                binding=binding,
                source=source,
                expected_source_sha256=source_after,
                manifest=private_manifest,
                expected_manifest_sha256=binding.private_manifest_sha256,
                run_lock=run_lock,
                source_lock=source_lock,
                operations=operations,
            )
            _revalidate_committed_handoff(args)
            promotion = _revalidate_fresh_promotion_evidence(
                args, binding=binding
            )
            postdeploy_digest = _write_postdeploy_receipt(
                postdeploy_path,
                binding=binding,
                source_sha256=source_after,
                promotion=promotion,
                deployment=deployment,
                operations=operations,
            )
            payload = _terminal_receipt_binding(
                _receipt_payload(
                    status_value="PASS",
                    binding=binding,
                    reason_code=None,
                    recovery_reason_code="interrupted_transaction_resumed",
                    failed_stage=None,
                    source_sha256_after=source_after,
                    activation_receipt_sha256=activation_digest,
                    rollback_receipt_sha256=None,
                    postdeploy_receipt_sha256=postdeploy_digest,
                    private_deploy_completed=True,
                    legacy_redeploy_completed=False,
                ),
                transaction_id=args.transaction_id,
                journal=journal,
            )
            _persist_terminal_result(
                journal=journal,
                journal_status="applied",
                receipt_path=receipt_path,
                payload=payload,
                source_after_sha256=source_after,
            )
            promotion_terminal_pass = True
            return payload, 0

        recovered, activation_digest, rollback_digest, legacy_done, recovery_reason = _recover(
            binding=binding,
            source=source,
            private_manifest=private_manifest,
            legacy_product_manifest=legacy_product_manifest,
            backup_dir=backup_dir,
            activation_path=activation_path,
            rollback_path=rollback_path,
            artifact_dir=artifact_dir,
            run_lock=run_lock,
            source_lock=source_lock,
            journal=journal,
            release_checkout=Path(args.release_checkout),
            source_pending_pre_restored=source_pending_pre_restored,
        )
        _source, current = _secure_file(source, label="runtime_source")
        payload = _terminal_receipt_binding(
            _receipt_payload(
                status_value=recovered,
                binding=binding,
                reason_code="interrupted_transaction_recovery",
                recovery_reason_code=recovery_reason,
                failed_stage="recovery",
                source_sha256_after=_digest(current),
                activation_receipt_sha256=activation_digest,
                rollback_receipt_sha256=rollback_digest,
                private_deploy_completed=False,
                legacy_redeploy_completed=legacy_done,
            ),
            transaction_id=args.transaction_id,
            journal=journal,
        )
        _persist_terminal_result(
            journal=journal,
            journal_status=("rolled_back" if recovered == "ROLLED_BACK" else "recovery_failed"),
            receipt_path=receipt_path,
            payload=payload,
            recovery_reason_code=recovery_reason,
        )
        return payload, 3 if recovered == "ROLLED_BACK" else 4
    finally:
        if run_lock is not None and run_lock.held:
            if promotion_terminal_pass:
                run_lock.release()
            else:
                run_lock.restore_adopted_market_pipeline_maintenance()
        source_lock.release()


def execute(args: argparse.Namespace) -> tuple[dict[str, object], int]:
    if getattr(args, "recovery_phase_journal", None):
        return recover_execute(args)
    binding: Binding | None = None
    receipt_path = _under_secure_root(Path(args.receipt), label="receipt")
    transaction_root = _secure_directory(
        Path(args.transaction_root), label="transaction", create=True
    )
    _require_production_scope(transaction_root, label="transaction")
    if receipt_path.parent != transaction_root:
        _fail("receipt_transaction_scope_invalid")
    if receipt_path.exists() or receipt_path.is_symlink():
        _fail("receipt_exists")
    if not TRANSACTION_ID.fullmatch(args.transaction_id or ""):
        _fail("transaction_id_invalid")
    transaction_dir = transaction_root / args.transaction_id
    if transaction_dir.exists() or transaction_dir.is_symlink():
        _fail("transaction_exists")
    transaction_dir.mkdir(mode=0o700)
    backup_dir = transaction_dir / "production-backups"
    backup_dir.mkdir(mode=0o700)
    activation_path = transaction_dir / "activation.json"
    rollback_path = transaction_dir / "rollback.json"
    postdeploy_path = transaction_dir / "post-deploy-verification.json"
    legacy_product_manifest = transaction_dir / "legacy-product-only.env"
    artifact_dir = _secure_directory(
        Path(args.queue_artifact_dir), label="queue_artifact", create=True
    )

    private_deploy_completed = False
    legacy_redeploy_completed = False
    activation_digest: str | None = None
    rollback_digest: str | None = None
    source_after: str | None = None
    promotion_terminal_pass = False
    run_lock: queue_cutover.ExclusiveRunLock | None = None
    source_lock: queue_cutover.ImmutableSourceLock | None = None
    journal: queue_cutover.PhaseJournal | None = None
    try:
        source = _runtime_source_for_lock(args)
        source_lock = queue_cutover.ImmutableSourceLock(source)
        source_lock.acquire()
        run_lock = queue_cutover.ExclusiveRunLock(artifact_dir)
        maintenance_journal = Path(args.maintenance_journal)
        _require_production_scope(
            maintenance_journal, label="maintenance_journal"
        )
        run_lock.adopt_market_pipeline_maintenance(
            journal=maintenance_journal,
            expected_journal_sha256=args.expected_maintenance_journal_sha256,
            expected_primary_verification_sha256=(
                args.expected_promotion_receipt_sha256
            ),
            release_sha=args.expected_release_sha,
        )
    except Exception as unexpected:
        try:
            if run_lock is not None and run_lock.held:
                run_lock.restore_adopted_market_pipeline_maintenance()
        finally:
            if source_lock is not None:
                source_lock.release()
        original = (
            unexpected
            if isinstance(unexpected, PromotionError)
            else PromotionError("queue_transaction_lock_failed", stage="lock")
        )
        payload = _receipt_payload(
            status_value="BLOCKED_MANUAL",
            binding=None,
            reason_code=original.reason_code,
            recovery_reason_code=None,
            failed_stage=original.stage,
            source_sha256_after=None,
            activation_receipt_sha256=None,
            rollback_receipt_sha256=None,
            private_deploy_completed=False,
            legacy_redeploy_completed=False,
        )
        _write_final_receipt(receipt_path, payload)
        return payload, 4
    try:
        try:
            binding, locked_source = _preflight(
                args, legacy_product_manifest=legacy_product_manifest
            )
            if locked_source != source:
                _fail("runtime_source_identity_mismatch")
            private_manifest = Path(args.private_manifest)
            private_attestation = (
                queue_cutover.bind_private_primary_deploy_attestation(
                    private_manifest,
                    manifest_sha256=binding.private_manifest_sha256,
                    receipt_path=Path(args.private_manifest_receipt),
                    receipt_sha256=(
                        binding.private_manifest_receipt_sha256
                    ),
                )
            )
            private_operations = queue_cutover.ProductionOperations(
                private_manifest,
                release_root=Path(args.release_checkout),
            )
            _queue_recheck(
                binding=binding,
                source=source,
                expected_source_sha256=binding.source_sha256_before,
                manifest=private_manifest,
                expected_manifest_sha256=binding.private_manifest_sha256,
                run_lock=run_lock,
                source_lock=source_lock,
                operations=private_operations,
            )
            journal = queue_cutover.PhaseJournal(
                artifact_dir,
                command="product-private-primary-promotion",
                source_sha256=binding.source_sha256_before,
                git_head=binding.release_sha,
                run_lock=run_lock,
            )
            transaction_binding = _transaction_binding_payload(
                args=args,
                binding=binding,
                transaction_dir=transaction_dir,
                backup_dir=backup_dir,
                activation_path=activation_path,
                rollback_path=rollback_path,
                legacy_product_manifest=legacy_product_manifest,
                receipt_path=receipt_path,
                phase_journal=journal.path,
            )
            journal.update(
                "product_preflight_verified",
                transaction_binding=transaction_binding,
                private_primary_attestation=(
                    queue_cutover._private_primary_attestation_binding(
                        private_manifest, private_attestation
                    )
                ),
            )
            _revalidate_committed_handoff(args)
            journal.update(
                "product_source_activation_pending",
                transaction_binding=transaction_binding,
                activation_path_sha256=_digest(str(activation_path).encode()),
            )
            _activate_in_process(
                args=args,
                binding=binding,
                source=source,
                private_manifest=private_manifest,
                backup_dir=backup_dir,
                activation_path=activation_path,
                source_lock=source_lock,
            )
            _activation, activation_digest, source_after = _activation_receipt(
                activation_path,
                binding=binding,
                backup_dir=backup_dir,
                source=source,
            )
            journal.update(
                "product_source_activated",
                source_after_sha256=source_after,
                activation_receipt_sha256=activation_digest,
                private_primary_attestation=(
                    queue_cutover._private_primary_attestation_binding(
                        private_manifest, private_attestation
                    )
                ),
            )
            _revalidate_committed_handoff(args)
            deployment = _deploy_with_fresh_authority(
                binding=binding,
                source=source,
                expected_source_sha256=source_after,
                manifest=private_manifest,
                expected_manifest_sha256=binding.private_manifest_sha256,
                artifact_dir=artifact_dir,
                run_lock=run_lock,
                source_lock=source_lock,
                journal=journal,
                operations=private_operations,
                journal_status="private_product_deploy_authorizing",
                private_attestation=private_attestation,
            )
            private_deploy_completed = True
            journal.update(
                "private_product_deploy_returned",
                source_after_sha256=source_after,
                activation_receipt_sha256=activation_digest,
                deploy_manifest_sha256=binding.private_manifest_sha256,
            )
            _queue_recheck(
                binding=binding,
                source=source,
                expected_source_sha256=source_after,
                manifest=private_manifest,
                expected_manifest_sha256=binding.private_manifest_sha256,
                run_lock=run_lock,
                source_lock=source_lock,
                operations=private_operations,
            )
            _revalidate_committed_handoff(args)
            promotion = _revalidate_fresh_promotion_evidence(
                args, binding=binding
            )
            postdeploy_digest = _write_postdeploy_receipt(
                postdeploy_path,
                binding=binding,
                source_sha256=source_after,
                promotion=promotion,
                deployment=deployment,
                operations=private_operations,
            )
            journal.update(
                "private_product_deployed_and_verified",
                source_after_sha256=source_after,
                activation_receipt_sha256=activation_digest,
            )
            payload = _terminal_receipt_binding(_receipt_payload(
                status_value="PASS",
                binding=binding,
                reason_code=None,
                recovery_reason_code=None,
                failed_stage=None,
                source_sha256_after=source_after,
                activation_receipt_sha256=activation_digest,
                rollback_receipt_sha256=None,
                postdeploy_receipt_sha256=postdeploy_digest,
                private_deploy_completed=True,
                legacy_redeploy_completed=False,
            ), transaction_id=args.transaction_id, journal=journal)
            _persist_terminal_result(
                journal=journal,
                journal_status="applied",
                receipt_path=receipt_path,
                payload=payload,
                source_after_sha256=source_after,
            )
            promotion_terminal_pass = True
            return payload, 0
        except Exception as unexpected:
            original = (
                unexpected
                if isinstance(unexpected, PromotionError)
                else PromotionError("promotion_internal_error", stage="transaction")
            )
            # Once the on-disk journal says `applied`, the private runtime is
            # authoritative.  A missing convenience receipt must block for
            # manual evidence repair, never trigger a rollback which could
            # contradict a PASS receipt that was already exposed.
            if _persisted_journal_status(journal) == "applied":
                terminal_payload = journal.payload.get(
                    "terminal_receipt_payload"
                )
                if not isinstance(terminal_payload, Mapping):
                    _fail(
                        "terminal_receipt_journal_invalid",
                        stage="recovery",
                    )
                _revalidate_terminal_pass(
                    args=args,
                    binding=binding,
                    source=source,
                    source_sha256=str(
                        terminal_payload.get("source_sha256_after") or ""
                    ),
                    manifest=Path(args.private_manifest),
                    run_lock=run_lock,
                    source_lock=source_lock,
                    operations=private_operations,
                    terminal_payload=terminal_payload,
                    postdeploy_path=postdeploy_path,
                )
                try:
                    terminal = _complete_terminal_receipt_from_journal(
                        journal=journal,
                        receipt_path=receipt_path,
                        transaction_id=args.transaction_id,
                    )
                except Exception:
                    terminal_payload = journal.payload.get(
                        "terminal_receipt_payload"
                    )
                    expected = journal.payload.get("receipt_sha256")
                    if (
                        not isinstance(terminal_payload, dict)
                        or not receipt_path.is_file()
                        or _digest(receipt_path.read_bytes()) != expected
                    ):
                        return dict(terminal_payload or {}), 4
                    terminal = (dict(terminal_payload), 0)
                if terminal is not None:
                    promotion_terminal_pass = terminal[0].get("status") == "PASS"
                    return terminal
                return {}, 4
            if binding is None:
                payload = _receipt_payload(
                    status_value="BLOCKED_MANUAL",
                    binding=None,
                    reason_code=original.reason_code,
                    recovery_reason_code=None,
                    failed_stage=original.stage,
                    source_sha256_after=None,
                    activation_receipt_sha256=None,
                    rollback_receipt_sha256=None,
                    private_deploy_completed=False,
                    legacy_redeploy_completed=False,
                )
                if journal is not None:
                    _persist_terminal_result(
                        journal=journal,
                        journal_status="failed_recovered",
                        receipt_path=receipt_path,
                        payload=payload,
                        error_code=original.reason_code,
                    )
                else:
                    _write_final_receipt(receipt_path, payload)
                return payload, 4
            if journal is None:
                journal = queue_cutover.PhaseJournal(
                    artifact_dir,
                    command="product-private-primary-promotion",
                    source_sha256=binding.source_sha256_before,
                    git_head=binding.release_sha,
                    run_lock=run_lock,
                )
            (
                recovered_status,
                activation_digest,
                rollback_digest,
                legacy_redeploy_completed,
                recovery_reason_code,
            ) = _recover(
                binding=binding,
                source=source,
                private_manifest=Path(args.private_manifest),
                legacy_product_manifest=legacy_product_manifest,
                backup_dir=backup_dir,
                activation_path=activation_path,
                rollback_path=rollback_path,
                artifact_dir=artifact_dir,
                run_lock=run_lock,
                source_lock=source_lock,
                journal=journal,
                release_checkout=Path(args.release_checkout),
            )
            _source, current = _secure_file(source, label="runtime_source")
            source_after = _digest(current)
            payload = _terminal_receipt_binding(_receipt_payload(
                status_value=recovered_status,
                binding=binding,
                reason_code=original.reason_code,
                recovery_reason_code=recovery_reason_code,
                failed_stage=original.stage,
                source_sha256_after=source_after,
                activation_receipt_sha256=activation_digest,
                rollback_receipt_sha256=rollback_digest,
                private_deploy_completed=private_deploy_completed,
                legacy_redeploy_completed=legacy_redeploy_completed,
            ), transaction_id=args.transaction_id, journal=journal)
            _persist_terminal_result(
                journal=journal,
                journal_status=(
                    "rolled_back"
                    if recovered_status == "ROLLED_BACK"
                    else "recovery_failed"
                ),
                receipt_path=receipt_path,
                payload=payload,
                error_code=original.reason_code,
                recovery_reason_code=recovery_reason_code,
            )
            return payload, 3 if recovered_status == "ROLLED_BACK" else 4
    finally:
        if run_lock is not None and run_lock.held:
            if promotion_terminal_pass:
                run_lock.release()
            else:
                run_lock.restore_adopted_market_pipeline_maintenance()
        if source_lock is not None:
            source_lock.release()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-manifest", required=True)
    parser.add_argument("--expected-source-manifest-sha256", required=True)
    parser.add_argument("--private-manifest", required=True)
    parser.add_argument("--expected-private-manifest-sha256", required=True)
    parser.add_argument("--private-manifest-receipt", required=True)
    parser.add_argument("--expected-private-manifest-receipt-sha256", required=True)
    parser.add_argument("--promotion-receipt", required=True)
    parser.add_argument("--expected-promotion-receipt-sha256", required=True)
    parser.add_argument("--catchup-receipt", required=True)
    parser.add_argument("--expected-catchup-receipt-sha256", required=True)
    parser.add_argument("--expected-source-sha256", required=True)
    parser.add_argument("--expected-release-sha", required=True)
    parser.add_argument("--expected-release-tree", required=True)
    parser.add_argument("--release-checkout", required=True)
    parser.add_argument("--maintenance-journal", required=True)
    parser.add_argument("--expected-maintenance-journal-sha256", required=True)
    parser.add_argument("--web-maintenance-journal", required=True)
    parser.add_argument(
        "--expected-web-maintenance-journal-sha256", required=True
    )
    parser.add_argument("--transaction-root", required=True)
    parser.add_argument(
        "--queue-artifact-dir",
        default=str(queue_cutover.DEFAULT_ARTIFACT_DIR),
    )
    parser.add_argument("--transaction-id", required=True)
    parser.add_argument("--receipt", required=True)
    parser.add_argument("--confirm", required=True)
    parser.add_argument("--recovery-phase-journal")
    parser.add_argument("--expected-phase-journal-sha256")
    parser.add_argument("--recovery-action", choices=("resume", "rollback"))
    parser.add_argument("--recovery-confirm")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        payload, code = execute(args)
    except (OSError, PromotionError) as exc:
        reason = exc.reason_code if isinstance(exc, PromotionError) else "os_error"
        payload = {
            "schema": RECEIPT_SCHEMA,
            "status": "BLOCKED_MANUAL",
            "reason_code": reason,
            "payload_values_included": False,
            "pii_included": False,
            "secrets_disclosed": False,
        }
        code = 4
    print(json.dumps(payload, sort_keys=True, separators=(",", ":")), flush=True)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
