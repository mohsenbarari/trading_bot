from __future__ import annotations

import argparse
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
import fcntl
from hashlib import sha256
import io
import json
import os
from pathlib import Path
import signal
import stat
import subprocess
import sys
import tempfile
from types import SimpleNamespace
from unittest import mock

import pytest

from scripts import update_production_coin_inference_source as updater
from scripts import cutover_telegram_delivery_queue_production as queue_cutover


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "update_production_coin_inference_source.py"
CONFIRMATION = "activate-production-coin-inference-guarded-rollout"
ROLLBACK_CONFIRMATION = "deactivate-production-coin-inference-guarded-rollout"
PRIVATE_PRIMARY_CONFIRMATION = "activate-production-private-primary-snapshots"
PRIVATE_PRIMARY_ROLLBACK_CONFIRMATION = "restore-production-legacy-snapshots"


def _manifest_payload(source: Path, *, relay_enabled: bool = True) -> str:
    relay = (
        "PRODUCTION_COIN_INFERENCE_RELAY_ENABLED=1\n"
        f"PRODUCTION_COIN_INFERENCE_RELAY_CONFIRM={updater.RELAY_CONFIRMATION}\n"
        if relay_enabled
        else "PRODUCTION_COIN_INFERENCE_RELAY_ENABLED=0\n"
        "PRODUCTION_COIN_INFERENCE_RELAY_CONFIRM=\n"
        "PRODUCTION_COIN_INFERENCE_RELAY_DISABLE_CONFIRM=disable-production-coin-inference-snapshot\n"
    )
    return f"RUNTIME_ENV_SOURCE_PATH={source}\n{relay}"


def _promotion_receipt(
    root: Path, *, created_at: datetime | None = None
) -> tuple[Path, str, str, str]:
    release_sha = "1" * 40
    release_tree = "2" * 40
    path = root / "production-evidence" / "promotion.json"
    path.parent.mkdir(mode=0o700)
    path.write_text(
        json.dumps(
            {
                "schema": updater.PROMOTION_RECEIPT_SCHEMA,
                "status": "PASS",
                "created_at_utc": (created_at or datetime.now(timezone.utc))
                .isoformat()
                .replace("+00:00", "Z"),
                "release_sha": release_sha,
                "release_tree": release_tree,
                "maximum_age_seconds": 120,
                "checks": list(updater.PROMOTION_REQUIRED_CHECKS),
                "catchup_verification": {
                    "receipt_sha256": "c" * 64,
                    "age_seconds": 1,
                },
                "capture_backfill": {
                    "not_before_utc": updater.AUTHORIZED_BACKFILL_NOT_BEFORE_UTC,
                    "source_codes": list(
                        updater.AUTHORIZED_BACKFILL_SOURCE_CODES
                    ),
                    "max_messages": 100_000,
                },
                "snapshot": {
                    "contract": updater.PROMOTION_SNAPSHOT_CONTRACT,
                    "lane": "PRIVATE_PRIMARY",
                    "status": "OK",
                    "snapshot_hash": "a" * 64,
                    "snapshot_version": 1,
                    "estimated_rate_count": 14,
                    "file_sha256": "b" * 64,
                    "snapshot_age_seconds": 1,
                    "publication_age_seconds": 1,
                    "maximum_effective_underlying_age_seconds": 1,
                },
                "read_only_runtime_verification": True,
                "product_or_runtime_mutated": False,
                "payload_values_included": False,
                "pii_included": False,
                "secrets_disclosed": False,
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    path.chmod(0o600)
    return path, sha256(path.read_bytes()).hexdigest(), release_sha, release_tree


def _run(*args: str) -> SimpleNamespace:
    manifest = Path(args[args.index("--manifest") + 1]).resolve()
    arguments = list(args)
    if arguments[0] in {
        "apply",
        "rollback",
        "activate-private-primary",
        "rollback-private-primary",
    } and "--expected-manifest-sha256" not in arguments:
        arguments.extend(
            [
                "--expected-manifest-sha256",
                sha256(manifest.read_bytes()).hexdigest(),
            ]
        )
    stdout = io.StringIO()
    with (
        mock.patch.object(updater, "APPROVED_MANIFEST_PATH", manifest),
        mock.patch.object(
            updater, "APPROVED_MANIFEST_ROOTS", (manifest.parent,)
        ),
        redirect_stdout(stdout),
    ):
        returncode = updater.main(arguments)
    return SimpleNamespace(returncode=returncode, stdout=stdout.getvalue(), stderr="")


def test_plan_and_apply_are_cas_bound_atomic_private_and_value_free() -> None:
    with tempfile.TemporaryDirectory(prefix="production-inference-source-") as temporary:
        root = Path(temporary)
        source = root / "immutable.env"
        source.write_text(
            "PRIVATE_TOKEN=do-not-disclose\n"
            "PRODUCTION_COIN_INFERENCE_PREVIEW_ENABLED=false\n"
            "PRODUCTION_COIN_INFERENCE_MAXIMUM_AGE_SECONDS=120\n",
            encoding="utf-8",
        )
        source.chmod(0o600)
        manifest = root / "online.env"
        manifest.write_text(_manifest_payload(source), encoding="utf-8")
        manifest.chmod(0o600)
        backup_dir = root / "backups"
        receipt_dir = root / "receipts"
        receipt = receipt_dir / "activation.json"
        before = sha256(source.read_bytes()).hexdigest()

        plan = _run("plan", "--manifest", str(manifest))
        assert plan.returncode == 0, plan.stderr + plan.stdout
        plan_payload = json.loads(plan.stdout)
        assert plan_payload["status"] == "PLAN"
        assert "PRIVATE_TOKEN" not in plan.stdout
        assert "do-not-disclose" not in plan.stdout

        rejected = _run(
            "apply", "--manifest", str(manifest),
            "--expected-source-sha256", "0" * 64,
            "--confirm", CONFIRMATION,
            "--backup-dir", str(backup_dir),
            "--receipt", str(receipt),
        )
        assert rejected.returncode == 2
        assert json.loads(rejected.stdout)["reason"] == "immutable_source_cas_mismatch"
        assert sha256(source.read_bytes()).hexdigest() == before

        applied = _run(
            "apply", "--manifest", str(manifest),
            "--expected-source-sha256", before,
            "--confirm", CONFIRMATION,
            "--backup-dir", str(backup_dir),
            "--receipt", str(receipt),
        )
        assert applied.returncode == 0, applied.stderr + applied.stdout
        payload = json.loads(applied.stdout)
        assert payload["status"] == "APPLIED"
        assert set(payload["changed_keys"]) == {
            "PRODUCTION_COIN_INFERENCE_PREVIEW_ENABLED",
            "PRODUCTION_COIN_INFERENCE_SELECTION_ENABLED",
            "PRODUCTION_COIN_INFERENCE_AUTO_SELECTION_ENABLED",
            "PRODUCTION_OFFER_MODEL_PRICE_GUARD_ENABLED",
        }
        updated = source.read_text(encoding="utf-8")
        assert "PRIVATE_TOKEN=do-not-disclose" in updated
        assert "PRODUCTION_COIN_INFERENCE_PREVIEW_ENABLED=true" in updated
        assert "PRODUCTION_COIN_INFERENCE_SELECTION_ENABLED=true" in updated
        assert "PRODUCTION_COIN_INFERENCE_AUTO_SELECTION_ENABLED=false" in updated
        assert "PRODUCTION_OFFER_MODEL_PRICE_GUARD_ENABLED=true" in updated
        assert "PRODUCTION_COIN_INFERENCE_MAXIMUM_AGE_SECONDS=120" in updated
        backup = next(backup_dir.iterdir())
        assert sha256(backup.read_bytes()).hexdigest() == before
        assert stat.S_IMODE(source.stat().st_mode) == 0o600
        assert stat.S_IMODE(backup.stat().st_mode) == 0o600
        assert stat.S_IMODE(receipt.stat().st_mode) == 0o600
        assert "do-not-disclose" not in applied.stdout
        assert "do-not-disclose" not in receipt.read_text(encoding="utf-8")

        enabled_digest = sha256(source.read_bytes()).hexdigest()
        rollback_receipt = receipt_dir / "rollback.json"
        rolled_back = _run(
            "rollback", "--manifest", str(manifest),
            "--expected-source-sha256", enabled_digest,
            "--confirm", ROLLBACK_CONFIRMATION,
            "--backup-dir", str(backup_dir),
            "--receipt", str(rollback_receipt),
        )
        assert rolled_back.returncode == 0, rolled_back.stderr + rolled_back.stdout
        rollback_payload = json.loads(rolled_back.stdout)
        assert rollback_payload["action"] == "DISABLE_GUARDED_INFERENCE"
        disabled = source.read_text(encoding="utf-8")
        assert "PRODUCTION_COIN_INFERENCE_PREVIEW_ENABLED=false" in disabled
        assert "PRODUCTION_COIN_INFERENCE_SELECTION_ENABLED=false" in disabled
        assert "PRODUCTION_COIN_INFERENCE_AUTO_SELECTION_ENABLED=false" in disabled
        assert "PRODUCTION_OFFER_MODEL_PRICE_GUARD_ENABLED=false" in disabled
        assert "PRODUCTION_COIN_INFERENCE_MAXIMUM_AGE_SECONDS=120" in disabled
        assert "PRIVATE_TOKEN=do-not-disclose" in disabled


def test_apply_rejects_missing_confirmation_insecure_or_aliased_source() -> None:
    with tempfile.TemporaryDirectory(prefix="production-inference-source-negative-") as temporary:
        root = Path(temporary)
        source = root / "immutable.env"
        source.write_text("PRIVATE_TOKEN=do-not-disclose\n", encoding="utf-8")
        source.chmod(0o600)
        manifest = root / "online.env"
        manifest.write_text(_manifest_payload(source), encoding="utf-8")
        manifest.chmod(0o600)
        digest = sha256(source.read_bytes()).hexdigest()
        missing = _run(
            "apply", "--manifest", str(manifest),
            "--expected-source-sha256", digest,
            "--confirm", "wrong",
            "--backup-dir", str(root / "backups"),
            "--receipt", str(root / "receipts" / "activation.json"),
        )
        assert json.loads(missing.stdout)["reason"] == "apply_confirmation_required"
        source.chmod(0o644)
        insecure = _run("plan", "--manifest", str(manifest))
        assert json.loads(insecure.stdout)["reason"] == "immutable_source_invalid"
        source.chmod(0o600)
        alias = root / "source-link.env"
        alias.symlink_to(source)
        manifest.write_text(_manifest_payload(alias), encoding="utf-8")
        aliased = _run("plan", "--manifest", str(manifest))
        assert json.loads(aliased.stdout)["reason"] == "immutable_source_invalid"


def test_private_primary_activation_and_rollback_are_cas_bound_and_value_free() -> None:
    with tempfile.TemporaryDirectory(prefix="production-private-primary-") as temporary:
        root = Path(temporary)
        source = root / "immutable.env"
        original_source = (
            "PRIVATE_TOKEN=do-not-disclose\n"
            "PRODUCTION_MARKET_PIPELINE_SHADOW_ROLLOUT_ENABLED=1\n"
            "PRODUCTION_MARKET_PIPELINE_CAPTURE_CUTOVER_ENABLED=1\n"
        )
        source.write_text(original_source, encoding="utf-8")
        source.chmod(0o600)
        manifest = root / "online.env"
        manifest.write_text(
            _manifest_payload(source, relay_enabled=False), encoding="utf-8"
        )
        manifest.chmod(0o600)
        backup_dir = root / "backups"
        receipt_dir = root / "receipts"
        promotion, promotion_digest, release_sha, release_tree = _promotion_receipt(root)
        activation_receipt = receipt_dir / "activate.json"

        with mock.patch.object(updater, "APPROVED_MANIFEST_PATH", manifest):
            plan = _run("plan-private-primary", "--manifest", str(manifest))
            assert plan.returncode == 0
            assert set(json.loads(plan.stdout)["changed_keys"]) == set(
                updater.PRIVATE_PRIMARY_UPDATES
            )
            assert updater.PRIVATE_PRIMARY_UPDATES[
                "PRODUCTION_PRODUCT_ESTIMATOR_APP_SNAPSHOT_HOST_DIR"
            ] == "/srv/trading-bot/production-data/market-pipeline/snapshots"
            assert updater.PRIVATE_PRIMARY_UPDATES[
                "PRODUCTION_PRODUCT_ESTIMATOR_BOT_SNAPSHOT_HOST_DIR"
            ] == "/srv/trading-bot/production-data/market-pipeline/snapshots"
            assert updater.PRIVATE_PRIMARY_UPDATES[
                "PRODUCTION_PRODUCT_ESTIMATOR_IRAN_APP_SNAPSHOT_HOST_DIR"
            ] == "/srv/trading-bot/market-data-production/snapshots"

            before = sha256(source.read_bytes()).hexdigest()
            activated = _run(
                "activate-private-primary",
                "--manifest",
                str(manifest),
                "--expected-source-sha256",
                before,
                "--confirm",
                PRIVATE_PRIMARY_CONFIRMATION,
                "--backup-dir",
                str(backup_dir),
                "--receipt",
                str(activation_receipt),
                "--promotion-receipt",
                str(promotion),
                "--expected-promotion-receipt-sha256",
                promotion_digest,
                "--expected-release-sha",
                release_sha,
                "--expected-release-tree",
                release_tree,
            )
            assert activated.returncode == 0
            activated_payload = json.loads(activated.stdout)
            assert activated_payload["action"] == "ACTIVATE_PRIVATE_PRIMARY_PRODUCT_SNAPSHOTS"
            assert activated_payload["promotion_receipt_sha256"] == promotion_digest
            assert activated_payload["release_sha"] == release_sha
            assert activated_payload["release_tree"] == release_tree
            assert "do-not-disclose" not in activated.stdout
            installed = source.read_text(encoding="utf-8")
            assert "PRODUCTION_PRODUCT_ESTIMATOR_SNAPSHOT_MODE=PRIVATE_PRIMARY" in installed
            for key in (
                "PRODUCTION_PRODUCT_ESTIMATOR_APP_PRIVATE_PRIMARY_SNAPSHOT_PATH",
                "PRODUCTION_PRODUCT_ESTIMATOR_BOT_PRIVATE_PRIMARY_SNAPSHOT_PATH",
                "PRODUCTION_PRODUCT_ESTIMATOR_IRAN_APP_PRIVATE_PRIMARY_SNAPSHOT_PATH",
            ):
                assert (
                    f"{key}=/app/runtime/product-estimator/"
                    "latest-private-primary.json"
                ) in installed
            assert "PRIVATE_TOKEN=do-not-disclose" in installed
            assert "PRODUCTION_MARKET_PIPELINE_SHADOW_ROLLOUT_ENABLED=1" in installed
            assert "PRODUCTION_MARKET_PIPELINE_CAPTURE_CUTOVER_ENABLED=1" in installed

            enabled_digest = sha256(source.read_bytes()).hexdigest()
            rolled_back = _run(
                "rollback-private-primary",
                "--manifest",
                str(manifest),
                "--expected-source-sha256",
                enabled_digest,
                "--confirm",
                PRIVATE_PRIMARY_ROLLBACK_CONFIRMATION,
                "--backup-dir",
                str(backup_dir),
                "--receipt",
                str(receipt_dir / "rollback.json"),
                "--activation-receipt",
                str(activation_receipt),
                "--expected-activation-receipt-sha256",
                sha256(activation_receipt.read_bytes()).hexdigest(),
            )
            assert rolled_back.returncode == 0
            assert source.read_text(encoding="utf-8") == original_source
            rollback = json.loads(rolled_back.stdout)
            assert rollback["action"] == "RESTORE_EXACT_PRE_ACTIVATION_SOURCE"


def test_private_primary_activation_rejects_stale_or_future_promotion_receipt() -> None:
    for label, created_at in (
        ("stale", datetime.now(timezone.utc) - timedelta(seconds=121)),
        ("future", datetime.now(timezone.utc) + timedelta(seconds=60)),
    ):
        with tempfile.TemporaryDirectory(
            prefix=f"production-private-primary-{label}-"
        ) as temporary:
            root = Path(temporary)
            source = root / "immutable.env"
            source.write_text(
                "PRIVATE_TOKEN=do-not-disclose\n", encoding="utf-8"
            )
            source.chmod(0o600)
            manifest = root / "online.env"
            manifest.write_text(
                _manifest_payload(source, relay_enabled=False), encoding="utf-8"
            )
            manifest.chmod(0o600)
            promotion, digest, release_sha, release_tree = _promotion_receipt(
                root, created_at=created_at
            )
            before = source.read_bytes()
            result = _run(
                "activate-private-primary",
                "--manifest",
                str(manifest),
                "--expected-source-sha256",
                sha256(before).hexdigest(),
                "--confirm",
                PRIVATE_PRIMARY_CONFIRMATION,
                "--backup-dir",
                str(root / "backups"),
                "--receipt",
                str(root / "receipts" / "activation.json"),
                "--promotion-receipt",
                str(promotion),
                "--expected-promotion-receipt-sha256",
                digest,
                "--expected-release-sha",
                release_sha,
                "--expected-release-tree",
                release_tree,
            )
            assert result.returncode == 2
            assert (
                json.loads(result.stdout)["reason"]
                == "promotion_receipt_stale_or_future"
            )
            assert source.read_bytes() == before
            assert not (root / "backups").exists()


def test_private_primary_activation_rejects_non_web_view_or_non_primary_receipt() -> None:
    for label, key, value in (
        ("contract", "contract", "estimator_snapshot/2.0"),
        ("lane", "lane", "PRIVATE_SHADOW"),
        ("identity", "snapshot_hash", "invalid"),
    ):
        with tempfile.TemporaryDirectory(
            prefix=f"production-private-primary-{label}-"
        ) as temporary:
            root = Path(temporary)
            source = root / "immutable.env"
            source.write_text("PRIVATE_TOKEN=stable\n", encoding="utf-8")
            source.chmod(0o600)
            manifest = root / "online.env"
            manifest.write_text(
                _manifest_payload(source, relay_enabled=False), encoding="utf-8"
            )
            manifest.chmod(0o600)
            promotion, _digest_before, release_sha, release_tree = (
                _promotion_receipt(root)
            )
            payload = json.loads(promotion.read_text(encoding="utf-8"))
            payload["snapshot"][key] = value
            promotion.write_text(
                json.dumps(payload, sort_keys=True), encoding="utf-8"
            )
            promotion.chmod(0o600)
            receipt_digest = sha256(promotion.read_bytes()).hexdigest()
            before = source.read_bytes()
            result = _run(
                "activate-private-primary",
                "--manifest",
                str(manifest),
                "--expected-source-sha256",
                sha256(before).hexdigest(),
                "--confirm",
                PRIVATE_PRIMARY_CONFIRMATION,
                "--backup-dir",
                str(root / "backups"),
                "--receipt",
                str(root / "receipts" / "activation.json"),
                "--promotion-receipt",
                str(promotion),
                "--expected-promotion-receipt-sha256",
                receipt_digest,
                "--expected-release-sha",
                release_sha,
                "--expected-release-tree",
                release_tree,
            )
            assert result.returncode == 2
            assert (
                json.loads(result.stdout)["reason"]
                == "promotion_receipt_contract_invalid"
            )
            assert source.read_bytes() == before


def test_private_primary_activation_rejects_unbound_capture_backfill_scope() -> None:
    for label, key, value in (
        ("cutoff", "not_before_utc", "2026-08-25T09:34:00Z"),
        ("sources", "source_codes", ["GROUP_1", "GROUP_2"]),
        ("limit", "max_messages", 1_999),
    ):
        with tempfile.TemporaryDirectory(
            prefix=f"production-private-primary-backfill-{label}-"
        ) as temporary:
            root = Path(temporary)
            source = root / "immutable.env"
            source.write_text("PRIVATE_TOKEN=stable\n", encoding="utf-8")
            source.chmod(0o600)
            manifest = root / "online.env"
            manifest.write_text(
                _manifest_payload(source, relay_enabled=False), encoding="utf-8"
            )
            manifest.chmod(0o600)
            promotion, _digest_before, release_sha, release_tree = (
                _promotion_receipt(root)
            )
            payload = json.loads(promotion.read_text(encoding="utf-8"))
            payload["capture_backfill"][key] = value
            promotion.write_text(
                json.dumps(payload, sort_keys=True), encoding="utf-8"
            )
            promotion.chmod(0o600)
            before = source.read_bytes()
            result = _run(
                "activate-private-primary",
                "--manifest",
                str(manifest),
                "--expected-source-sha256",
                sha256(before).hexdigest(),
                "--confirm",
                PRIVATE_PRIMARY_CONFIRMATION,
                "--backup-dir",
                str(root / "backups"),
                "--receipt",
                str(root / "receipts" / "activation.json"),
                "--promotion-receipt",
                str(promotion),
                "--expected-promotion-receipt-sha256",
                sha256(promotion.read_bytes()).hexdigest(),
                "--expected-release-sha",
                release_sha,
                "--expected-release-tree",
                release_tree,
            )
            assert result.returncode == 2
            assert (
                json.loads(result.stdout)["reason"]
                == "promotion_receipt_contract_invalid"
            )
            assert source.read_bytes() == before


def test_private_primary_activation_requires_exact_manifest_digest_and_mode_transition() -> None:
    with tempfile.TemporaryDirectory(
        prefix="production-private-primary-manifest-cas-"
    ) as temporary:
        root = Path(temporary)
        source = root / "immutable.env"
        source.write_text(
            "PRODUCTION_PRODUCT_ESTIMATOR_SNAPSHOT_MODE=PRIVATE_SHADOW\n",
            encoding="utf-8",
        )
        source.chmod(0o600)
        manifest = root / "online.env"
        manifest.write_text(_manifest_payload(source), encoding="utf-8")
        manifest.chmod(0o600)
        source_digest = sha256(source.read_bytes()).hexdigest()
        promotion, promotion_digest, release_sha, release_tree = _promotion_receipt(root)
        promotion_args = (
            "--promotion-receipt",
            str(promotion),
            "--expected-promotion-receipt-sha256",
            promotion_digest,
            "--expected-release-sha",
            release_sha,
            "--expected-release-tree",
            release_tree,
        )

        stale_manifest = _run(
            "activate-private-primary",
            "--manifest",
            str(manifest),
            "--expected-manifest-sha256",
            "0" * 64,
            "--expected-source-sha256",
            source_digest,
            "--confirm",
            PRIVATE_PRIMARY_CONFIRMATION,
            "--backup-dir",
            str(root / "production-backups"),
            "--receipt",
            str(root / "production-receipts" / "stale.json"),
            *promotion_args,
        )
        assert stale_manifest.returncode == 2
        assert json.loads(stale_manifest.stdout)["reason"] == "manifest_cas_mismatch"

        invalid_transition = _run(
            "activate-private-primary",
            "--manifest",
            str(manifest),
            "--expected-source-sha256",
            source_digest,
            "--confirm",
            PRIVATE_PRIMARY_CONFIRMATION,
            "--backup-dir",
            str(root / "production-backups"),
            "--receipt",
            str(root / "production-receipts" / "transition.json"),
            *promotion_args,
        )
        assert invalid_transition.returncode == 2
        assert (
            json.loads(invalid_transition.stdout)["reason"]
            == "private_primary_source_transition_invalid"
        )
        assert sha256(source.read_bytes()).hexdigest() == source_digest


def test_private_primary_cas_accepts_only_the_caller_owned_canonical_source_lock() -> None:
    with tempfile.TemporaryDirectory(
        prefix="production-private-primary-held-lock-"
    ) as temporary:
        root = Path(temporary)
        source = root / "immutable.env"
        original = b"PRIVATE_TOKEN=stable\n"
        source.write_bytes(original)
        source.chmod(0o600)
        manifest = root / "online.env"
        manifest.write_text(
            _manifest_payload(source, relay_enabled=False), encoding="utf-8"
        )
        manifest.chmod(0o600)
        promotion, promotion_digest, release_sha, release_tree = (
            _promotion_receipt(root)
        )
        receipt = root / "receipts" / "activation.json"
        args = argparse.Namespace(
            manifest=str(manifest),
            confirm=PRIVATE_PRIMARY_CONFIRMATION,
            expected_source_sha256=sha256(original).hexdigest(),
            expected_manifest_sha256=sha256(manifest.read_bytes()).hexdigest(),
            backup_dir=str(root / "backups"),
            receipt=str(receipt),
            promotion_receipt=str(promotion),
            expected_promotion_receipt_sha256=promotion_digest,
            expected_release_sha=release_sha,
            expected_release_tree=release_tree,
        )
        lock_path = root / updater.LOCK_NAME
        owner = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        os.fchmod(owner, 0o600)
        fcntl.flock(owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
        non_owner = os.open(lock_path, os.O_RDWR)
        try:
            with (
                mock.patch.object(updater, "APPROVED_MANIFEST_PATH", manifest),
                mock.patch.object(
                    updater, "APPROVED_MANIFEST_ROOTS", (root,)
                ),
            ):
                with pytest.raises(
                    updater.SourceUpdateError,
                    match="inherited_source_lock_not_owned",
                ):
                    updater.activate_private_primary_with_held_source_lock(
                        args,
                        source,
                        source_lock_descriptor=non_owner,
                    )
                assert source.read_bytes() == original
                assert not receipt.exists()
                assert (
                    updater.activate_private_primary_with_held_source_lock(
                        args,
                        source,
                        source_lock_descriptor=owner,
                    )
                    == 0
                )
            assert receipt.is_file()
            assert (
                "PRODUCTION_PRODUCT_ESTIMATOR_SNAPSHOT_MODE=PRIVATE_PRIMARY"
                in source.read_text(encoding="utf-8")
            )
        finally:
            os.close(non_owner)
            fcntl.flock(owner, fcntl.LOCK_UN)
            os.close(owner)


def test_inherited_source_lock_rejects_an_unlocked_canonical_descriptor() -> None:
    with tempfile.TemporaryDirectory(
        prefix="production-private-primary-unlocked-lock-"
    ) as temporary:
        root = Path(temporary)
        source = root / "immutable.env"
        source.write_text("SAFE=1\n", encoding="utf-8")
        source.chmod(0o600)
        descriptor = os.open(root / updater.LOCK_NAME, os.O_RDWR | os.O_CREAT, 0o600)
        os.fchmod(descriptor, 0o600)
        try:
            with pytest.raises(
                updater.SourceUpdateError,
                match="inherited_source_lock_not_held",
            ):
                updater._verify_inherited_source_lock(source, descriptor)
        finally:
            os.close(descriptor)


def test_queue_immutable_source_lock_is_accepted_by_product_cas_contract() -> None:
    with tempfile.TemporaryDirectory(
        prefix="production-private-primary-queue-lock-"
    ) as temporary:
        root = Path(temporary)
        source = root / "immutable.env"
        source.write_text("SAFE=1\n", encoding="utf-8")
        source.chmod(0o600)
        source_lock = queue_cutover.ImmutableSourceLock(source)
        source_lock.acquire()
        try:
            assert source_lock.descriptor is not None
            verified = updater._verify_inherited_source_lock(
                source, source_lock.descriptor
            )
            lock_metadata = os.fstat(source_lock.descriptor)
            assert verified.st_dev == lock_metadata.st_dev
            assert verified.st_ino == lock_metadata.st_ino
        finally:
            source_lock.release()


def test_activation_requires_enabled_exactly_confirmed_production_relay() -> None:
    with tempfile.TemporaryDirectory(prefix="production-inference-relay-contract-") as temporary:
        root = Path(temporary)
        source = root / "immutable.env"
        source.write_text("PRIVATE_TOKEN=stable\n", encoding="utf-8")
        source.chmod(0o600)
        manifest = root / "online.env"
        manifest.write_text(_manifest_payload(source, relay_enabled=False), encoding="utf-8")
        manifest.chmod(0o600)
        before = sha256(source.read_bytes()).hexdigest()

        result = _run(
            "apply",
            "--manifest",
            str(manifest),
            "--expected-source-sha256",
            before,
            "--confirm",
            CONFIRMATION,
            "--backup-dir",
            str(root / "backups"),
            "--receipt",
            str(root / "receipts" / "activation.json"),
        )

        assert result.returncode == 2
        assert json.loads(result.stdout)["reason"] == "production_snapshot_relay_activation_required"
        assert sha256(source.read_bytes()).hexdigest() == before


def test_apply_uses_shared_nonblocking_lock_and_post_lock_cas() -> None:
    with tempfile.TemporaryDirectory(prefix="production-inference-source-lock-") as temporary:
        root = Path(temporary)
        source = root / "immutable.env"
        source.write_text("PRIVATE_TOKEN=stable\n", encoding="utf-8")
        source.chmod(0o600)
        manifest = root / "online.env"
        manifest.write_text(_manifest_payload(source), encoding="utf-8")
        manifest.chmod(0o600)
        digest = sha256(source.read_bytes()).hexdigest()
        lock = root / updater.LOCK_NAME
        descriptor = os.open(lock, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            blocked = _run(
                "apply", "--manifest", str(manifest),
                "--expected-source-sha256", digest,
                "--confirm", CONFIRMATION,
                "--backup-dir", str(root / "backups"),
                "--receipt", str(root / "receipts" / "activation.json"),
            )
        finally:
            os.close(descriptor)
        assert blocked.returncode == 2
        assert json.loads(blocked.stdout)["reason"] == "immutable_source_update_locked"
        assert sha256(source.read_bytes()).hexdigest() == digest


def test_receipt_failure_rolls_back_exact_installed_source_and_clears_pending_marker() -> None:
    with tempfile.TemporaryDirectory(prefix="production-inference-source-receipt-") as temporary:
        root = Path(temporary)
        source = root / "immutable.env"
        original = b"PRIVATE_TOKEN=stable\n"
        source.write_bytes(original)
        source.chmod(0o600)
        manifest = root / "online.env"
        manifest.write_text(_manifest_payload(source), encoding="utf-8")
        manifest.chmod(0o600)
        receipt = root / "receipts" / "activation.json"
        args = argparse.Namespace(
            manifest=str(manifest),
            confirm=CONFIRMATION,
            expected_source_sha256=sha256(original).hexdigest(),
            expected_manifest_sha256=sha256(manifest.read_bytes()).hexdigest(),
            backup_dir=str(root / "backups"),
            receipt=str(receipt),
        )
        real_exclusive_write = updater._exclusive_write

        def fail_receipt(path: Path, payload: bytes, *, mode: int) -> None:
            if path == receipt:
                raise OSError("synthetic receipt failure")
            real_exclusive_write(path, payload, mode=mode)

        with (
            mock.patch.object(updater, "APPROVED_MANIFEST_PATH", manifest),
            mock.patch.object(updater, "APPROVED_MANIFEST_ROOTS", (root,)),
            mock.patch.object(updater, "_exclusive_write", side_effect=fail_receipt),
        ):
            with pytest.raises(OSError, match="synthetic receipt failure"):
                updater._apply(
                    args,
                    source,
                    updates=updater.APPROVED_UPDATES,
                    confirmation=CONFIRMATION,
                    action="ENABLE_GUARDED_INFERENCE",
                )
        assert source.read_bytes() == original
        assert not (root / updater.PENDING_NAME).exists()
        assert not receipt.exists()


def test_manifest_identity_is_fixed_and_manifest_must_be_private() -> None:
    with tempfile.TemporaryDirectory(prefix="production-inference-manifest-") as temporary:
        root = Path(temporary)
        source = root / "immutable.env"
        source.write_text("PRIVATE_TOKEN=stable\n", encoding="utf-8")
        source.chmod(0o600)
        approved = root / "approved-online.env"
        approved.write_text(_manifest_payload(source), encoding="utf-8")
        approved.chmod(0o600)
        arbitrary = root / "other-online.env"
        arbitrary.write_text(_manifest_payload(source), encoding="utf-8")
        arbitrary.chmod(0o600)

        with mock.patch.object(updater, "APPROVED_MANIFEST_PATH", approved):
            with pytest.raises(updater.SourceUpdateError, match="manifest_identity_invalid"):
                updater._manifest_source(arbitrary)
            alias = root / "approved-manifest-link.env"
            alias.symlink_to(approved)
            with pytest.raises(updater.SourceUpdateError, match="manifest_invalid"):
                updater._manifest_source(alias)
            approved.chmod(0o644)
            with pytest.raises(updater.SourceUpdateError, match="manifest_invalid"):
                updater._manifest_source(approved)


@pytest.mark.parametrize("alias_name", ["source", "manifest", "lock", "pending"])
def test_receipt_cannot_alias_any_protected_transaction_path(alias_name: str) -> None:
    with tempfile.TemporaryDirectory(prefix="production-inference-alias-") as temporary:
        root = Path(temporary)
        source = root / "immutable.env"
        original = b"PRIVATE_TOKEN=stable\n"
        source.write_bytes(original)
        source.chmod(0o600)
        manifest = root / "online.env"
        manifest.write_text(_manifest_payload(source), encoding="utf-8")
        manifest.chmod(0o600)
        targets = {
            "source": source,
            "manifest": manifest,
            "lock": root / updater.LOCK_NAME,
            "pending": root / updater.PENDING_NAME,
        }
        receipt = targets[alias_name]
        args = argparse.Namespace(
            manifest=str(manifest),
            confirm=CONFIRMATION,
            expected_source_sha256=sha256(original).hexdigest(),
            expected_manifest_sha256=sha256(manifest.read_bytes()).hexdigest(),
            backup_dir=str(root / "backups"),
            receipt=str(receipt),
        )
        before_manifest = manifest.read_bytes()
        with (
            mock.patch.object(updater, "APPROVED_MANIFEST_PATH", manifest),
            mock.patch.object(updater, "APPROVED_MANIFEST_ROOTS", (root,)),
        ):
            with pytest.raises(updater.SourceUpdateError):
                updater._apply(
                    args,
                    source,
                    updates=updater.APPROVED_UPDATES,
                    confirmation=CONFIRMATION,
                    action="ENABLE_GUARDED_INFERENCE",
                )
        assert source.read_bytes() == original
        assert manifest.read_bytes() == before_manifest


def test_receipt_is_fresh_exclusive_and_never_overwrites_existing_file() -> None:
    with tempfile.TemporaryDirectory(prefix="production-inference-receipt-exclusive-") as temporary:
        root = Path(temporary)
        source = root / "immutable.env"
        original = b"PRIVATE_TOKEN=stable\n"
        source.write_bytes(original)
        source.chmod(0o600)
        manifest = root / "online.env"
        manifest.write_text(_manifest_payload(source), encoding="utf-8")
        manifest.chmod(0o600)
        receipt = root / "receipts" / "activation.json"
        receipt.parent.mkdir(mode=0o700)
        receipt.write_text("operator-owned\n", encoding="utf-8")
        receipt.chmod(0o600)
        before_receipt = receipt.read_bytes()
        args = argparse.Namespace(
            manifest=str(manifest),
            confirm=CONFIRMATION,
            expected_source_sha256=sha256(original).hexdigest(),
            expected_manifest_sha256=sha256(manifest.read_bytes()).hexdigest(),
            backup_dir=str(root / "backups"),
            receipt=str(receipt),
        )
        with (
            mock.patch.object(updater, "APPROVED_MANIFEST_PATH", manifest),
            mock.patch.object(updater, "APPROVED_MANIFEST_ROOTS", (root,)),
        ):
            with pytest.raises(updater.SourceUpdateError, match="receipt_path_invalid"):
                updater._apply(
                    args,
                    source,
                    updates=updater.APPROVED_UPDATES,
                    confirmation=CONFIRMATION,
                    action="ENABLE_GUARDED_INFERENCE",
                )
        assert receipt.read_bytes() == before_receipt
        assert source.read_bytes() == original


class _SyntheticSigkill(BaseException):
    pass


def _private_primary_recovery_fixture(root: Path):
    source = root / "production-runtime.env"
    source.write_text(
        "PRODUCTION_PRODUCT_ESTIMATOR_SNAPSHOT_MODE=LEGACY\n",
        encoding="utf-8",
    )
    source.chmod(0o600)
    manifest = root / "production-control" / "online.env"
    manifest.parent.mkdir(mode=0o700)
    manifest.write_text(_manifest_payload(source), encoding="utf-8")
    manifest.chmod(0o600)
    promotion, promotion_digest, release_sha, release_tree = _promotion_receipt(root)
    receipt = root / "production-receipts" / "activation.json"
    args = argparse.Namespace(
        manifest=str(manifest),
        confirm=PRIVATE_PRIMARY_CONFIRMATION,
        expected_source_sha256=sha256(source.read_bytes()).hexdigest(),
        expected_manifest_sha256=sha256(manifest.read_bytes()).hexdigest(),
        backup_dir=str(root / "production-backups"),
        receipt=str(receipt),
        promotion_receipt=str(promotion),
        expected_promotion_receipt_sha256=promotion_digest,
        expected_release_sha=release_sha,
        expected_release_tree=release_tree,
    )
    return source, manifest, receipt, args


@pytest.mark.parametrize(
    "boundary",
    ["before_source", "before_receipt", "after_receipt", "after_receipt_rollback"],
)
def test_private_primary_sigkill_boundaries_are_explicitly_recoverable(
    boundary: str,
) -> None:
    with tempfile.TemporaryDirectory(prefix="production-source-sigkill-") as temporary:
        root = Path(temporary)
        source, manifest, receipt, args = _private_primary_recovery_fixture(root)
        original = source.read_bytes()
        real_atomic = updater._atomic_write
        real_exclusive = updater._exclusive_write
        real_remove = updater._remove_pending

        def atomic(path: Path, payload: bytes, *, mode: int) -> None:
            if boundary == "before_source" and path == source:
                raise _SyntheticSigkill()
            real_atomic(path, payload, mode=mode)

        def exclusive(path: Path, payload: bytes, *, mode: int) -> None:
            if boundary == "before_receipt" and path == receipt:
                raise _SyntheticSigkill()
            real_exclusive(path, payload, mode=mode)

        def remove(path: Path) -> None:
            if boundary.startswith("after_receipt"):
                raise _SyntheticSigkill()
            real_remove(path)

        with (
            mock.patch.object(updater, "APPROVED_MANIFEST_PATH", manifest),
            mock.patch.object(updater, "APPROVED_MANIFEST_ROOTS", (manifest.parent,)),
            mock.patch.object(updater, "_atomic_write", side_effect=atomic),
            mock.patch.object(updater, "_exclusive_write", side_effect=exclusive),
            mock.patch.object(updater, "_remove_pending", side_effect=remove),
        ):
            binding = updater._promotion_binding(args)
            with pytest.raises(_SyntheticSigkill):
                updater._apply(
                    args,
                    source,
                    updates=updater.PRIVATE_PRIMARY_UPDATES,
                    confirmation=PRIVATE_PRIMARY_CONFIRMATION,
                    action="ACTIVATE_PRIVATE_PRIMARY_PRODUCT_SNAPSHOTS",
                    evidence_binding=binding,
                )

        pending = root / updater.PENDING_NAME
        assert pending.is_file()
        recovery = argparse.Namespace(
            manifest=str(manifest),
            backup_dir=args.backup_dir,
            receipt=str(receipt),
            expected_pending_sha256=sha256(pending.read_bytes()).hexdigest(),
            recovery_action="resume" if boundary == "after_receipt" else "rollback",
            recovery_confirm=updater.PRIVATE_PRIMARY_RECOVERY_CONFIRMATION,
            promotion_receipt=args.promotion_receipt,
            expected_promotion_receipt_sha256=args.expected_promotion_receipt_sha256,
            expected_release_sha=args.expected_release_sha,
            expected_release_tree=args.expected_release_tree,
        )
        with (
            mock.patch.object(updater, "APPROVED_MANIFEST_PATH", manifest),
            mock.patch.object(updater, "APPROVED_MANIFEST_ROOTS", (manifest.parent,)),
            updater._source_lock(source) as descriptor,
        ):
            result = updater.recover_private_primary_with_held_source_lock(
                recovery, source, source_lock_descriptor=descriptor
            )
        assert not pending.exists()
        if boundary == "after_receipt":
            assert result["status"] == "APPLIED"
            assert receipt.is_file()
            assert b"PRIVATE_PRIMARY" in source.read_bytes()
        elif boundary == "after_receipt_rollback":
            assert result["status"] == "RECOVERED_ROLLED_BACK"
            assert receipt.is_file()
            assert source.read_bytes() == original
        else:
            assert result["status"] == "RECOVERED_ROLLED_BACK"
            assert not receipt.exists()
            assert source.read_bytes() == original


@pytest.mark.parametrize(
    ("boundary", "recovery_action", "expected_status"),
    (
        ("AFTER_SOURCE_WRITE", "rollback", "RECOVERED_ROLLED_BACK"),
        ("AFTER_RECEIPT_WRITE", "resume", "APPLIED"),
    ),
)
def test_private_primary_real_sigkill_recovers_source_wal_exactly(
    boundary: str,
    recovery_action: str,
    expected_status: str,
) -> None:
    """A real process death cannot make source mutation ambiguous."""

    with tempfile.TemporaryDirectory(
        prefix="production-source-real-sigkill-"
    ) as temporary:
        root = Path(temporary)
        source, manifest, receipt, args = _private_primary_recovery_fixture(root)
        original = source.read_bytes()
        command = (
            "activate-private-primary",
            "--manifest",
            str(manifest),
            "--expected-source-sha256",
            args.expected_source_sha256,
            "--expected-manifest-sha256",
            args.expected_manifest_sha256,
            "--confirm",
            PRIVATE_PRIMARY_CONFIRMATION,
            "--backup-dir",
            args.backup_dir,
            "--receipt",
            str(receipt),
            "--promotion-receipt",
            args.promotion_receipt,
            "--expected-promotion-receipt-sha256",
            args.expected_promotion_receipt_sha256,
            "--expected-release-sha",
            args.expected_release_sha,
            "--expected-release-tree",
            args.expected_release_tree,
        )
        program = r"""
import os
import signal
import sys
from pathlib import Path
from scripts import update_production_coin_inference_source as updater

boundary, manifest_value, source_value, receipt_value, *arguments = sys.argv[1:]
manifest = Path(manifest_value)
source = Path(source_value)
receipt = Path(receipt_value)
updater.APPROVED_MANIFEST_PATH = manifest
updater.APPROVED_MANIFEST_ROOTS = (manifest.parent,)
if boundary == "AFTER_SOURCE_WRITE":
    original = updater._atomic_write
    def kill_after_source(path, payload, *, mode):
        result = original(path, payload, mode=mode)
        if Path(path) == source:
            os.kill(os.getpid(), signal.SIGKILL)
        return result
    updater._atomic_write = kill_after_source
elif boundary == "AFTER_RECEIPT_WRITE":
    original = updater._exclusive_write
    def kill_after_receipt(path, payload, *, mode):
        result = original(path, payload, mode=mode)
        if Path(path) == receipt:
            os.kill(os.getpid(), signal.SIGKILL)
        return result
    updater._exclusive_write = kill_after_receipt
else:
    raise AssertionError(boundary)
raise SystemExit(updater.main(arguments))
"""
        killed = subprocess.run(
            [
                sys.executable,
                "-c",
                program,
                boundary,
                str(manifest),
                str(source),
                str(receipt),
                *command,
            ],
            cwd=REPO_ROOT,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        assert killed.returncode == -signal.SIGKILL
        pending = root / updater.PENDING_NAME
        assert pending.is_file()
        assert b"PRIVATE_PRIMARY" in source.read_bytes()
        assert receipt.exists() is (boundary == "AFTER_RECEIPT_WRITE")

        recovered = _run(
            "recover-private-primary",
            "--manifest",
            str(manifest),
            "--backup-dir",
            args.backup_dir,
            "--receipt",
            str(receipt),
            "--expected-pending-sha256",
            sha256(pending.read_bytes()).hexdigest(),
            "--recovery-action",
            recovery_action,
            "--recovery-confirm",
            updater.PRIVATE_PRIMARY_RECOVERY_CONFIRMATION,
            "--promotion-receipt",
            args.promotion_receipt,
            "--expected-promotion-receipt-sha256",
            args.expected_promotion_receipt_sha256,
            "--expected-release-sha",
            args.expected_release_sha,
            "--expected-release-tree",
            args.expected_release_tree,
        )
        assert recovered.returncode == 0, recovered.stdout
        result = json.loads(recovered.stdout)
        assert result["status"] == expected_status
        assert not pending.exists()
        if recovery_action == "resume":
            assert receipt.is_file()
            assert b"PRIVATE_PRIMARY" in source.read_bytes()
        else:
            assert not receipt.exists()
            assert source.read_bytes() == original


def test_stale_evidence_allows_safe_rollback_but_not_new_promotion_resume() -> None:
    with tempfile.TemporaryDirectory(prefix="production-source-stale-recovery-") as temporary:
        root = Path(temporary)
        source, manifest, receipt, args = _private_primary_recovery_fixture(root)
        real_atomic = updater._atomic_write

        def kill_before_source(path: Path, payload: bytes, *, mode: int) -> None:
            if path == source:
                raise _SyntheticSigkill()
            real_atomic(path, payload, mode=mode)

        with (
            mock.patch.object(updater, "APPROVED_MANIFEST_PATH", manifest),
            mock.patch.object(updater, "APPROVED_MANIFEST_ROOTS", (manifest.parent,)),
            mock.patch.object(updater, "_atomic_write", side_effect=kill_before_source),
        ):
            with pytest.raises(_SyntheticSigkill):
                updater._apply(
                    args,
                    source,
                    updates=updater.PRIVATE_PRIMARY_UPDATES,
                    confirmation=PRIVATE_PRIMARY_CONFIRMATION,
                    action="ACTIVATE_PRIVATE_PRIMARY_PRODUCT_SNAPSHOTS",
                    evidence_binding=updater._promotion_binding(args),
                )
        pending = root / updater.PENDING_NAME
        stale = datetime.now(timezone.utc) - timedelta(seconds=121)
        _promotion_receipt_path = Path(args.promotion_receipt)
        document = json.loads(_promotion_receipt_path.read_text(encoding="utf-8"))
        document["created_at_utc"] = stale.isoformat().replace("+00:00", "Z")
        _promotion_receipt_path.write_text(json.dumps(document, sort_keys=True), encoding="utf-8")
        _promotion_receipt_path.chmod(0o600)
        recovery = argparse.Namespace(
            manifest=str(manifest), backup_dir=args.backup_dir, receipt=str(receipt),
            expected_pending_sha256=sha256(pending.read_bytes()).hexdigest(),
            recovery_action="resume", recovery_confirm=updater.PRIVATE_PRIMARY_RECOVERY_CONFIRMATION,
            promotion_receipt=str(_promotion_receipt_path),
            expected_promotion_receipt_sha256=sha256(_promotion_receipt_path.read_bytes()).hexdigest(),
            expected_release_sha=args.expected_release_sha, expected_release_tree=args.expected_release_tree,
        )
        with (
            mock.patch.object(updater, "APPROVED_MANIFEST_PATH", manifest),
            mock.patch.object(updater, "APPROVED_MANIFEST_ROOTS", (manifest.parent,)),
            updater._source_lock(source) as descriptor,
        ):
            with pytest.raises(updater.SourceUpdateError, match="promotion_receipt_stale_or_future"):
                updater.recover_private_primary_with_held_source_lock(
                    recovery, source, source_lock_descriptor=descriptor
                )
        assert pending.exists()
        recovery.recovery_action = "rollback"
        with (
            mock.patch.object(updater, "APPROVED_MANIFEST_PATH", manifest),
            mock.patch.object(updater, "APPROVED_MANIFEST_ROOTS", (manifest.parent,)),
            updater._source_lock(source) as descriptor,
        ):
            result = updater.recover_private_primary_with_held_source_lock(
                recovery, source, source_lock_descriptor=descriptor
            )
        assert result["status"] == "RECOVERED_ROLLED_BACK"
        assert not pending.exists()
