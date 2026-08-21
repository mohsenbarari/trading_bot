from __future__ import annotations

import argparse
from contextlib import redirect_stdout
import fcntl
from hashlib import sha256
import io
import json
import os
from pathlib import Path
import stat
import tempfile
from types import SimpleNamespace
from unittest import mock

import pytest

from scripts import update_production_coin_inference_source as updater


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "update_production_coin_inference_source.py"
CONFIRMATION = "activate-production-coin-inference-guarded-rollout"
ROLLBACK_CONFIRMATION = "deactivate-production-coin-inference-guarded-rollout"


def _manifest_payload(source: Path, *, relay_enabled: bool = True) -> str:
    relay = (
        "PRODUCTION_COIN_INFERENCE_RELAY_ENABLED=1\n"
        f"PRODUCTION_COIN_INFERENCE_RELAY_CONFIRM={updater.RELAY_CONFIRMATION}\n"
        if relay_enabled
        else "PRODUCTION_COIN_INFERENCE_RELAY_ENABLED=0\nPRODUCTION_COIN_INFERENCE_RELAY_CONFIRM=\n"
    )
    return f"RUNTIME_ENV_SOURCE_PATH={source}\n{relay}"


def _run(*args: str) -> SimpleNamespace:
    manifest = Path(args[args.index("--manifest") + 1]).resolve()
    stdout = io.StringIO()
    with (
        mock.patch.object(updater, "APPROVED_MANIFEST_PATH", manifest),
        redirect_stdout(stdout),
    ):
        returncode = updater.main(list(args))
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
        receipt = root / "receipts" / "activation.json"
        args = argparse.Namespace(
            confirm=CONFIRMATION,
            expected_source_sha256=sha256(original).hexdigest(),
            backup_dir=str(root / "backups"),
            receipt=str(receipt),
        )
        real_exclusive_write = updater._exclusive_write

        def fail_receipt(path: Path, payload: bytes, *, mode: int) -> None:
            if path == receipt:
                raise OSError("synthetic receipt failure")
            real_exclusive_write(path, payload, mode=mode)

        with mock.patch.object(updater, "_exclusive_write", side_effect=fail_receipt):
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
            confirm=CONFIRMATION,
            expected_source_sha256=sha256(original).hexdigest(),
            backup_dir=str(root / "backups"),
            receipt=str(receipt),
        )
        before_manifest = manifest.read_bytes()
        with mock.patch.object(updater, "APPROVED_MANIFEST_PATH", manifest):
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
            confirm=CONFIRMATION,
            expected_source_sha256=sha256(original).hexdigest(),
            backup_dir=str(root / "backups"),
            receipt=str(receipt),
        )
        with mock.patch.object(updater, "APPROVED_MANIFEST_PATH", manifest):
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
