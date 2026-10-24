"""Offline safety contracts for Market Pipeline backup and restore evidence."""

from __future__ import annotations

from datetime import timedelta
from copy import deepcopy
import io
import json
from pathlib import Path
import tempfile
import subprocess
import unittest
import os
import errno
from unittest import mock

from scripts import backup_market_pipeline_archive as backup


RELEASE_SHA = "a" * 40
RELEASE_TREE = "b" * 40
IMAGE_ID = "sha256:" + "c" * 64
IMAGE_SIGNATURE = "d" * 64
REAL_SECURE_DIRECTORY = backup._secure_directory


def _invariants(*, fact_count: int, table_count: int = 1) -> dict[str, object]:
    tables = {"market_facts": fact_count}
    for index in range(1, table_count):
        tables[f"table_{index}"] = 0
    return {
        "table_row_counts": tables,
        "sequence_values": {
            "market_facts_id_seq": {"last_value": 101, "is_called": True}
        },
        "schema_catalog_sha256": "1" * 64,
        "schema_objects_sha256": "2" * 64,
    }


def _env_values(root: Path) -> dict[str, str]:
    return {
        "MARKET_PIPELINE_PROJECT_NAME": "market-private-pipeline-production",
        "MARKET_PIPELINE_IMAGE": IMAGE_ID,
        "MARKET_PIPELINE_RELEASE_SHA": RELEASE_SHA,
        "MARKET_PIPELINE_MODE": "live",
        "MARKET_PIPELINE_FEED_MODE": "PRIVATE_SHADOW",
        "MARKET_PIPELINE_ALLOW_PRIVATE_PRIMARY": "0",
        "MARKET_PIPELINE_EXPECTED_SNAPSHOT_LANE": "PRIVATE_SHADOW",
        "MARKET_WEB_DATA_ROOT": str(root / "market-data-production"),
        "MARKET_PRIVATE_BIND_IP": "10.240.1.20",
        "MARKET_WEB_PRIVATE_IP": "10.240.1.20",
        "MARKET_BOT_PRIVATE_IP": "10.240.1.10",
        "MARKET_POSTGRES_PASSWORD_FILE": "/srv/trading-bot/secure/market/postgres-password",
        "MARKET_CAPTURE_ACCOUNT1_CONFIG_FILE": "/srv/trading-bot/secure/market/account1.json",
        "MARKET_CAPTURE_ACCOUNT2_CONFIG_FILE": "/srv/trading-bot/secure/market/account2.json",
        "MARKET_CAPTURE_ACCOUNT2_HMAC_FILE": "/srv/trading-bot/secure/market/account2-hmac",
        "MARKET_RESEARCH_ENCRYPTION_KEY_FILE": "/srv/trading-bot/secure/market/research-archive.key",
        "MARKET_TRANSPORT_CA_FILE": "/srv/trading-bot/secure/market/ca.pem",
        "MARKET_WEB_TRANSPORT_CERT_FILE": "/srv/trading-bot/secure/market/web-cert.pem",
        "MARKET_WEB_TRANSPORT_KEY_FILE": "/srv/trading-bot/secure/market/web-key.pem",
        "MARKET_HMAC_ACTIVE_FILE": "/srv/trading-bot/secure/market/hmac-active",
        "MARKET_HMAC_PREVIOUS_FILE": "/srv/trading-bot/secure/market/hmac-previous",
        "MARKET_POSTGRES_USER": "market_data",
        "MARKET_POSTGRES_DB": "market_archive",
    }


def _write_env(path: Path, values: dict[str, str]) -> None:
    path.write_text(
        "".join(f"{key}={value}\n" for key, value in sorted(values.items())),
        encoding="utf-8",
    )
    path.chmod(0o600)


def _fixture_secure_directory(path: Path, *, create: bool) -> Path:
    if create:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        path.chmod(0o700)
    if not path.is_dir() or path.is_symlink() or path.stat().st_mode & 0o777 != 0o700:
        raise backup.BackupError("backup_directory_owner_mode_invalid")
    return path


class BackupMarketPipelineArchiveTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="market-backup-")
        self.root = Path(self.temporary.name)
        self.root.chmod(0o700)
        self.env_file = self.root / "web.release.env"
        self.values = _env_values(self.root)
        _write_env(self.env_file, self.values)
        self.postgres_root = Path(self.values["MARKET_WEB_DATA_ROOT"]) / "postgres"
        self.postgres_root.mkdir(parents=True, mode=0o700)
        self.backup_dir = self.root / "production-backups"
        self.receipt = self.backup_dir / "market-pipeline-backup-receipt.json"
        self.source_guard = mock.patch.object(backup, "validate_source")
        self.directory_guard = mock.patch.object(
            backup, "_secure_directory", side_effect=_fixture_secure_directory
        )
        self.source_guard.start()
        self.directory_guard.start()
        # Only ambient /tmp lies outside the private fixture boundary. Exercise
        # real owner/mode and all stable-FD checks for the fixture itself.
        trusted_parent = backup._trusted_env_parent
        ambient = Path(tempfile.gettempdir()).stat()
        def fixture_parent(info):
            if (info.st_dev, info.st_ino) == (ambient.st_dev, ambient.st_ino):
                return True
            return trusted_parent(info)
        self.parent_guard = mock.patch.object(backup, "_trusted_env_parent", side_effect=fixture_parent)
        self.parent_guard.start()
        self.command_guards = [
            mock.patch.object(backup.subprocess, name, side_effect=AssertionError("unmocked_host_command"))
            for name in ("run", "Popen")
        ]
        for guard in self.command_guards:
            guard.start()

    def tearDown(self) -> None:
        for guard in reversed(self.command_guards):
            guard.stop()
        self.parent_guard.stop()
        self.directory_guard.stop()
        self.source_guard.stop()
        self.temporary.cleanup()

    def _env_binding(self) -> dict[str, object]:
        with backup.BoundRoleEnv(self.env_file) as bound:
            return dict(bound.binding)

    def _verify(self, **overrides: object) -> dict[str, object]:
        arguments: dict[str, object] = {
            "env_file": self.env_file,
            "receipt": self.receipt,
            "release_sha": RELEASE_SHA,
            "release_tree": RELEASE_TREE,
            "image_id": IMAGE_ID,
            "image_input_signature": IMAGE_SIGNATURE,
            "maximum_age_seconds": 3600,
        }
        arguments.update(overrides)
        return backup.verify_receipt(**arguments)  # type: ignore[arg-type]

    def _write_bound_receipt(self, payload: dict[str, object]) -> None:
        run_id = "deadbeefdeadbeef"
        payload["backup_run_id"] = run_id
        payload.setdefault("source_after", deepcopy(payload["source"]))
        artifact = payload.get("backup")
        artifact_path = (
            Path(artifact["path"])
            if isinstance(artifact, dict)
            else self.backup_dir
            / f"market-archive-before-{RELEASE_SHA[:12]}-20260827T120000Z-{run_id[:8]}.dump"
        )
        journal = {
            "schema": backup.JOURNAL_SCHEMA,
            "status": "COMPLETE",
            "backup_status": payload["status"],
            "run_id": run_id,
            "created_at_utc": payload["created_at_utc"],
            "release_sha": RELEASE_SHA,
            "release_tree": RELEASE_TREE,
            "image_id": IMAGE_ID,
            "image_input_signature": IMAGE_SIGNATURE,
            "role_env_binding": self._env_binding(),
            "receipt_path": str(self.receipt),
            "artifact_path": str(artifact_path),
            "candidate_path": str(self.backup_dir / f".{artifact_path.name}.pending"),
            "source_before": payload["source"],
            "source_after": payload["source_after"],
            "backup": payload["backup"],
            "restore_smoke": payload["restore_smoke"],
            "restore_resources": None,
            "secrets_disclosed": False,
        }
        backup._write_journal(backup._journal_path(self.backup_dir), journal)
        backup._write_receipt(self.receipt, payload)

    def _create_empty(self) -> dict[str, object]:
        with mock.patch.object(backup, "_running_project_services", return_value=[]):
            return backup.create_backup(
                env_file=self.env_file, backup_dir=self.backup_dir,
                receipt=self.receipt, release_sha=RELEASE_SHA,
                release_tree=RELEASE_TREE, image_id=IMAGE_ID,
                image_input_signature=IMAGE_SIGNATURE,
            )

    def _legacy_evidence(self) -> tuple[str, str]:
        self._create_empty()
        journal_path = backup._journal_path(self.backup_dir)
        for path, schema in ((self.receipt, backup.LEGACY_RECEIPT_SCHEMA),
                             (journal_path, backup.LEGACY_JOURNAL_SCHEMA)):
            payload = json.loads(path.read_text())
            payload["schema"] = schema
            del payload["role_env_binding"]
            payload["role_env_sha256"] = "1" * 64  # historical stored value only
            backup._write_receipt(path, payload)
        return backup.file_digest(self.receipt), backup.file_digest(journal_path)

    def test_create_verify_never_hash_or_reopen_env_path(self) -> None:
        digest = backup.file_digest
        open_path = Path.open
        def guarded_digest(path):
            self.assertNotEqual(path, self.env_file)
            return digest(path)
        def guarded_open(path, *args, **kwargs):
            self.assertNotEqual(path, self.env_file)
            return open_path(path, *args, **kwargs)
        with mock.patch.object(backup, "file_digest", side_effect=guarded_digest), \
                mock.patch.object(Path, "open", guarded_open):
            payload = self._create_empty()
            self.assertEqual(self._verify(), payload)
        self.assertEqual(payload["schema"], "market_pipeline_backup_restore/1.3")
        self.assertNotIn("role_env_sha256", payload)
        self.assertEqual(payload["role_env_binding"], self._env_binding())
        self.assertEqual(set(payload["role_env_binding"]), {
            "schema", "path", "device", "inode", "uid", "gid", "mode", "nlink",
            "size_bytes", "mtime_ns", "ctime_ns", "parents",
        })

    def test_file_identity_replacement_and_in_place_rewrite_fail_closed(self) -> None:
        for replacement in (False, True):
            with self.subTest(replacement=replacement):
                self._create_empty()
                original = self.env_file.stat()
                content = self.env_file.read_bytes()
                if replacement:
                    candidate = self.root / "replacement"
                    candidate.write_bytes(content)
                    candidate.chmod(0o600)
                    candidate.replace(self.env_file)
                else:
                    self.env_file.write_bytes(content)
                os.utime(self.env_file, ns=(original.st_atime_ns, original.st_mtime_ns))
                with self.assertRaisesRegex(backup.BackupError, "receipt_identity_mismatch"):
                    self._verify()
                self.receipt.unlink()
                backup._journal_path(self.backup_dir).unlink()

    def test_bound_fd_refuses_path_swaps_symlinks_hardlinks_and_unsafe_modes(self) -> None:
        with self.assertRaisesRegex(backup.BackupError, "role_env_drifted"):
            with backup.BoundRoleEnv(self.env_file) as bound:
                replacement = self.root / "replacement"
                replacement.write_bytes(self.env_file.read_bytes())
                replacement.chmod(0o600)
                replacement.replace(self.env_file)
                bound.check()
        link = self.root / "linked-env"
        os.link(self.env_file, link)
        with self.assertRaisesRegex(backup.BackupError, "owner_mode_invalid"):
            with backup.BoundRoleEnv(self.env_file):
                self.fail("hardlinked Env accepted")
        link.unlink()
        link.symlink_to(self.env_file)
        with self.assertRaisesRegex(backup.BackupError, "path_invalid"):
            with backup.BoundRoleEnv(link):
                self.fail("symlink Env accepted")
        self.env_file.chmod(0o640)
        with self.assertRaisesRegex(backup.BackupError, "owner_mode_invalid"):
            with backup.BoundRoleEnv(self.env_file):
                self.fail("nonprivate Env accepted")

    def _assert_open_failure_closes_descriptors(self, path, reason, *, deny_env_open=False):
        real_open, real_close = os.open, os.close
        opened, closed = [], []
        bound = backup.BoundRoleEnv(path)
        def tracked_open(target, *args, **kwargs):
            if deny_env_open and target == path.name:
                raise PermissionError(errno.EACCES, "private permission detail", str(path))
            descriptor = real_open(target, *args, **kwargs)
            opened.append(descriptor)
            return descriptor
        def tracked_close(descriptor):
            closed.append(descriptor)
            return real_close(descriptor)
        with mock.patch.object(backup.os, "open", side_effect=tracked_open), \
                mock.patch.object(backup.os, "close", side_effect=tracked_close), \
                mock.patch.object(backup.os, "pread", side_effect=AssertionError("unexpected_env_read")):
            with self.assertRaises(backup.BackupError) as failure:
                with bound:
                    self.fail("unavailable Env accepted")
        self.assertEqual(str(failure.exception), reason)
        self.assertTrue(failure.exception.__suppress_context__)
        self.assertNotIn(str(path), str(failure.exception))
        self.assertNotIn("private", str(failure.exception))
        self.assertEqual(closed, list(reversed(opened)))
        self.assertEqual(bound.descriptors, [])
        for descriptor in opened:
            with self.assertRaises(OSError) as bad_fd:
                os.fstat(descriptor)
            self.assertEqual(bad_fd.exception.errno, errno.EBADF)
        return opened

    def test_real_symlink_loop_is_content_free_and_leaks_no_descriptors(self) -> None:
        first, second = self.root / "loop-a", self.root / "loop-b"
        first.symlink_to(second)
        second.symlink_to(first)
        self.assertEqual(self._assert_open_failure_closes_descriptors(
            first, "backup_role_env_path_invalid"
        ), [])

    def test_real_enoent_closes_all_opened_parent_descriptors(self) -> None:
        opened = self._assert_open_failure_closes_descriptors(
            self.root / "missing-env", "backup_role_env_unavailable"
        )
        self.assertGreater(len(opened), 0)

    def test_eacces_at_env_open_closes_real_parent_descriptors(self) -> None:
        # EACCES is injected at the syscall boundary: root can read mode 000.
        opened = self._assert_open_failure_closes_descriptors(
            self.env_file, "backup_role_env_unavailable", deny_env_open=True
        )
        self.assertGreater(len(opened), 0)

    def test_canonicalization_runtimeerror_mapping_does_not_mask_logic_errors(self) -> None:
        with mock.patch.object(backup, "_trusted_env_parent", side_effect=RuntimeError("logic_failure")):
            with self.assertRaisesRegex(RuntimeError, "logic_failure") as failure:
                with backup.BoundRoleEnv(self.env_file):
                    self.fail("logic error swallowed")
        self.assertNotIsInstance(failure.exception, backup.BackupError)

    def test_current_binding_rejects_json_boolean_for_integer_identity(self) -> None:
        self._create_empty()
        payload = json.loads(self.receipt.read_text())
        # nlink=1 must not compare equal to True in a persisted contract.
        payload["role_env_binding"]["nlink"] = True
        backup._write_receipt(self.receipt, payload)
        with self.assertRaisesRegex(backup.BackupError, "receipt_identity_mismatch"):
            self._verify()

    def test_bound_env_refuses_special_files_and_oversized_inputs_without_read(self) -> None:
        special = self.root / "fifo-env"
        os.mkfifo(special, 0o600)
        with mock.patch.object(backup.os, "pread", side_effect=AssertionError("special_read")):
            with self.assertRaisesRegex(backup.BackupError, "owner_mode_invalid"):
                with backup.BoundRoleEnv(special):
                    self.fail("FIFO Env accepted")
        self.env_file.write_bytes(b"X" * (64 * 1024 + 1))
        with mock.patch.object(backup.os, "pread", side_effect=AssertionError("oversized_read")):
            with self.assertRaisesRegex(backup.BackupError, "owner_mode_invalid"):
                with backup.BoundRoleEnv(self.env_file):
                    self.fail("oversized Env accepted")

    def test_trusted_0755_parent_allowed_but_writable_or_replaced_parent_refused(self) -> None:
        parent = self.root / "env-parent"
        parent.mkdir(mode=0o755)
        env_file = parent / "role.env"
        _write_env(env_file, self.values)
        with backup.BoundRoleEnv(env_file) as bound:
            (parent / "ordinary-sibling").touch()
            bound.check()  # parent mtime/ctime are intentionally not a binding
        parent.chmod(0o775)
        with self.assertRaisesRegex(backup.BackupError, "parent_invalid"):
            with backup.BoundRoleEnv(env_file):
                self.fail("writable parent accepted")
        parent.chmod(0o755)
        with self.assertRaisesRegex(backup.BackupError, "parent_drifted"):
            with backup.BoundRoleEnv(env_file) as bound:
                parent.rename(self.root / "old-parent")
                parent.mkdir(mode=0o755)
                bound.check()

    def test_drift_during_external_work_never_publishes_complete_receipt(self) -> None:
        def drift(project):
            self.env_file.write_bytes(self.env_file.read_bytes())
            return []
        with mock.patch.object(backup, "_running_project_services", side_effect=drift):
            with self.assertRaisesRegex(backup.BackupError, "role_env_drifted"):
                backup.create_backup(
                    env_file=self.env_file, backup_dir=self.backup_dir,
                    receipt=self.receipt, release_sha=RELEASE_SHA,
                    release_tree=RELEASE_TREE, image_id=IMAGE_ID,
                    image_input_signature=IMAGE_SIGNATURE,
                )
        self.assertFalse(self.receipt.exists())
        self.assertFalse(backup._journal_path(self.backup_dir).exists())

    def test_legacy_create_verify_refuse_before_any_env_open_or_hash(self) -> None:
        self._legacy_evidence()
        self.env_file.unlink()
        for operation in (self._create_empty, self._verify):
            with self.subTest(operation=operation.__name__), \
                    mock.patch.object(backup, "BoundRoleEnv", side_effect=AssertionError("env_opened")), \
                    mock.patch.object(backup, "file_digest", side_effect=AssertionError("content_hashed")):
                with self.assertRaisesRegex(backup.BackupError, "binding_migration_required"):
                    operation()
        # A legacy journal alone also refuses before Env access, even for hot
        # empty PREPARED transactions; no legacy transaction is auto-adopted.
        self.receipt.unlink()
        journal_path = backup._journal_path(self.backup_dir)
        journal = json.loads(journal_path.read_text())
        journal["status"] = "PREPARED"
        backup._write_journal(journal_path, journal)
        with mock.patch.object(backup, "BoundRoleEnv", side_effect=AssertionError("env_opened")):
            with self.assertRaisesRegex(backup.BackupError, "binding_migration_required"):
                self._create_empty()
        self.assertTrue(journal_path.exists())

    def test_historical_verifier_never_opens_env_and_cannot_prove_current_state(self) -> None:
        receipt_sha, journal_sha = self._legacy_evidence()
        self.env_file.unlink()
        arguments = dict(
            receipt=self.receipt, receipt_sha256=receipt_sha,
            journal_sha256=journal_sha, release_sha=RELEASE_SHA,
            release_tree=RELEASE_TREE, image_id=IMAGE_ID,
            image_input_signature=IMAGE_SIGNATURE,
        )
        with mock.patch.object(backup, "BoundRoleEnv", side_effect=AssertionError("env_opened")):
            result = backup.verify_historical_receipt(**arguments)
        self.assertTrue(result["historical_only"])
        self.assertFalse(result["current_environment_verified"])
        self.assertFalse(result["eligible_for_new_deployment"])
        with self.assertRaisesRegex(backup.BackupError, "historical_evidence_identity_mismatch"):
            backup.verify_historical_receipt(**{**arguments, "journal_sha256": "0" * 64})
        payload = json.loads(self.receipt.read_text())
        payload["role_env_sha256"] = "2" * 64
        backup._write_receipt(self.receipt, payload)
        with self.assertRaisesRegex(backup.BackupError, "receipt_identity_mismatch"):
            backup.verify_historical_receipt(**{**arguments, "receipt_sha256": backup.file_digest(self.receipt)})

    def test_historical_pins_and_parses_one_snapshot_under_real_digest_interleaving(self) -> None:
        receipt_sha, journal_sha = self._legacy_evidence()
        journal_path = backup._journal_path(self.backup_dir)
        originals = {path: path.read_bytes() for path in (self.receipt, journal_path)}
        created_at = json.loads(originals[self.receipt])["created_at_utc"]
        real_digest = backup.file_digest
        observed = []
        def raced_digest(path):
            digest = real_digest(path)  # never return a fabricated digest
            observed.append(path)
            if len(observed) == 2:
                for target, raw in originals.items():
                    payload = json.loads(raw)
                    payload["created_at_utc"] = backup.utc_text(backup.utc_now() - timedelta(seconds=5))
                    target.write_text(json.dumps(payload))
            elif len(observed) == 3:
                for target, raw in originals.items():
                    target.write_bytes(raw)
                digest = real_digest(path)
            return digest
        with mock.patch.object(backup, "file_digest", side_effect=raced_digest), \
                mock.patch.object(backup, "BoundRoleEnv", side_effect=AssertionError("env_opened")):
            result = backup.verify_historical_receipt(
                receipt=self.receipt, receipt_sha256=receipt_sha,
                journal_sha256=journal_sha, release_sha=RELEASE_SHA,
                release_tree=RELEASE_TREE, image_id=IMAGE_ID,
                image_input_signature=IMAGE_SIGNATURE,
            )
        self.assertEqual(result["receipt"]["created_at_utc"], created_at)
        # Control pins use their parsed buffers, never an independent digest
        # open. The prior implementation returned the raced, unpinned payload.
        self.assertEqual(observed, [])
        self.assertEqual(real_digest(self.receipt), receipt_sha)
        self.assertEqual(real_digest(journal_path), journal_sha)

    def test_historical_read_rewrite_and_restore_is_rejected_by_retained_fd(self) -> None:
        receipt_sha, journal_sha = self._legacy_evidence()
        original = self.receipt.read_bytes()
        identity = self.receipt.stat()
        real_pread = os.pread
        mutated = []
        def raced_pread(descriptor, size, offset):
            raw = real_pread(descriptor, size, offset)
            info = os.fstat(descriptor)
            if not mutated and (info.st_dev, info.st_ino) == (identity.st_dev, identity.st_ino):
                payload = json.loads(original)
                payload["created_at_utc"] = backup.utc_text(backup.utc_now() - timedelta(seconds=5))
                self.receipt.write_text(json.dumps(payload))
                self.receipt.write_bytes(original)
                os.utime(self.receipt, ns=(identity.st_atime_ns, identity.st_mtime_ns))
                mutated.append(True)
            return raw
        with mock.patch.object(backup.os, "pread", side_effect=raced_pread), \
                mock.patch.object(backup, "BoundRoleEnv", side_effect=AssertionError("env_opened")):
            with self.assertRaisesRegex(backup.BackupError, "historical_evidence_identity_mismatch"):
                backup.verify_historical_receipt(
                    receipt=self.receipt, receipt_sha256=receipt_sha,
                    journal_sha256=journal_sha, release_sha=RELEASE_SHA,
                    release_tree=RELEASE_TREE, image_id=IMAGE_ID,
                    image_input_signature=IMAGE_SIGNATURE,
                )
        self.assertEqual(mutated, [True])
        self.assertEqual(backup.file_digest(self.receipt), receipt_sha)

    def test_historical_controls_stay_bound_until_validation_returns(self) -> None:
        receipt_sha, journal_sha = self._legacy_evidence()
        journal_path = backup._journal_path(self.backup_dir)
        originals = {path: path.read_bytes() for path in (self.receipt, journal_path)}
        real_evidence = backup._verify_receipt_evidence
        validated = []
        def raced_evidence(**arguments):
            payload = real_evidence(**arguments)
            validated.append(True)
            for target, raw in originals.items():
                changed = json.loads(raw)
                changed["created_at_utc"] = backup.utc_text(backup.utc_now() - timedelta(seconds=5))
                target.write_text(json.dumps(changed))
                target.write_bytes(raw)
            return payload
        with mock.patch.object(backup, "_verify_receipt_evidence", side_effect=raced_evidence), \
                mock.patch.object(backup, "BoundRoleEnv", side_effect=AssertionError("env_opened")):
            with self.assertRaisesRegex(backup.BackupError, "historical_evidence_identity_mismatch"):
                backup.verify_historical_receipt(
                    receipt=self.receipt, receipt_sha256=receipt_sha,
                    journal_sha256=journal_sha, release_sha=RELEASE_SHA,
                    release_tree=RELEASE_TREE, image_id=IMAGE_ID,
                    image_input_signature=IMAGE_SIGNATURE,
                )
        self.assertEqual(validated, [True])
        self.assertEqual(backup.file_digest(self.receipt), receipt_sha)
        self.assertEqual(backup.file_digest(journal_path), journal_sha)

    def test_historical_control_size_is_bounded_before_read_and_fds_close(self) -> None:
        receipt_sha, journal_sha = self._legacy_evidence()
        os.truncate(self.receipt, backup.MAX_HISTORICAL_DOCUMENT_BYTES + 1)
        opened, closed = [], []
        real_open, real_close = os.open, os.close
        def tracked_open(*args, **kwargs):
            descriptor = real_open(*args, **kwargs)
            opened.append(descriptor)
            return descriptor
        def tracked_close(descriptor):
            closed.append(descriptor)
            return real_close(descriptor)
        with mock.patch.object(backup.os, "open", side_effect=tracked_open), \
                mock.patch.object(backup.os, "close", side_effect=tracked_close), \
                mock.patch.object(backup.os, "pread", side_effect=AssertionError("oversized_control_read")):
            with self.assertRaisesRegex(backup.BackupError, "historical_evidence_invalid"):
                backup.verify_historical_receipt(
                    receipt=self.receipt, receipt_sha256=receipt_sha,
                    journal_sha256=journal_sha, release_sha=RELEASE_SHA,
                    release_tree=RELEASE_TREE, image_id=IMAGE_ID,
                    image_input_signature=IMAGE_SIGNATURE,
                )
        self.assertEqual(closed, list(reversed(opened)))
        self.assertGreater(len(opened), 1)
        for descriptor in opened:
            with self.assertRaises(OSError) as bad_fd:
                os.fstat(descriptor)
            self.assertEqual(bad_fd.exception.errno, errno.EBADF)

    def test_historical_cli_requires_no_env_and_reports_no_deployment_eligibility(self) -> None:
        receipt_sha, journal_sha = self._legacy_evidence()
        self.env_file.unlink()
        output = io.StringIO()
        with mock.patch.object(backup.sys, "stdout", output), \
                mock.patch.object(backup, "BoundRoleEnv", side_effect=AssertionError("env_opened")):
            status = backup.main([
                "verify-historical", "--receipt", str(self.receipt),
                "--receipt-sha256", receipt_sha, "--journal-sha256", journal_sha,
                "--release-sha", RELEASE_SHA, "--release-tree", RELEASE_TREE,
                "--image-id", IMAGE_ID, "--image-input-signature", IMAGE_SIGNATURE,
            ])
        self.assertEqual(status, 0)
        result = json.loads(output.getvalue())
        self.assertTrue(result["historical_only"])
        self.assertFalse(result["current_environment_verified"])
        self.assertFalse(result["eligible_for_new_deployment"])

    def test_initial_empty_store_receipt_is_non_mutating_and_fresh(self) -> None:
        with mock.patch.object(backup, "_running_project_services", return_value=[]):
            payload = backup.create_backup(
                env_file=self.env_file,
                backup_dir=self.backup_dir,
                receipt=self.receipt,
                release_sha=RELEASE_SHA,
                release_tree=RELEASE_TREE,
                image_id=IMAGE_ID,
                image_input_signature=IMAGE_SIGNATURE,
            )
        self.assertEqual(payload["status"], "INITIAL_EMPTY")
        self.assertIsNone(payload["backup"])
        self.assertFalse(payload["database_mutated"])
        self.assertFalse(payload["services_started"])
        self.assertFalse(payload["off_host_copy_required"])
        self.assertEqual(self.receipt.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self._verify(), payload)
        with self.assertRaisesRegex(backup.BackupError, "stale"):
            self._verify(now=backup.utc_now() + timedelta(hours=2))
        self.assertEqual(
            self._verify(
                now=backup.utc_now() + timedelta(hours=2),
                maximum_age_seconds=None,
            ),
            payload,
        )
        with self.assertRaisesRegex(backup.BackupError, "stale"):
            self._verify(
                now=backup.utc_now() - timedelta(minutes=2),
                maximum_age_seconds=None,
            )

    def test_initial_empty_receipt_fails_if_store_changes_before_migration(self) -> None:
        with mock.patch.object(backup, "_running_project_services", return_value=[]):
            backup.create_backup(
                env_file=self.env_file,
                backup_dir=self.backup_dir,
                receipt=self.receipt,
                release_sha=RELEASE_SHA,
                release_tree=RELEASE_TREE,
                image_id=IMAGE_ID,
                image_input_signature=IMAGE_SIGNATURE,
            )
        (self.postgres_root / "unexpected").write_text("partial-init\n", encoding="utf-8")
        with self.assertRaisesRegex(backup.BackupError, "initial_store_changed"):
            self._verify()

    def test_restored_artifact_receipt_is_exact_and_tamper_evident(self) -> None:
        self.backup_dir.mkdir(mode=0o700)
        artifact = (
            self.backup_dir
            / f"market-archive-before-{RELEASE_SHA[:12]}-20260827T120000Z-deadbeef.dump"
        )
        artifact.write_bytes(b"synthetic-custom-dump")
        artifact.chmod(0o600)
        payload = {
            "schema": backup.RECEIPT_SCHEMA,
            "status": "PASS",
            "created_at_utc": backup.utc_text(backup.utc_now()),
            "release_sha": RELEASE_SHA,
            "release_tree": RELEASE_TREE,
            "image_id": IMAGE_ID,
            "image_input_signature": IMAGE_SIGNATURE,
            "role_env_binding": self._env_binding(),
            "source": {
                "container_id": "e" * 64,
                "database": "market_archive",
                "database_size_bytes": 12345,
                "database_identity_sha256": "f" * 64,
                "schema_versions": [1, 2],
                "table_count": 26,
                "fact_count": 99,
                **_invariants(fact_count=99, table_count=26),
            },
            "backup": {
                "path": str(artifact),
                "sha256": backup.file_digest(artifact),
                "size_bytes": artifact.stat().st_size,
                "format": "postgres_custom",
            },
            "restore_smoke": {
                "status": "PASS",
                "schema_versions": [1, 2],
                "table_count": 26,
                "fact_count": 99,
                **_invariants(fact_count=99, table_count=26),
                "cleanup_status": "PASS",
            },
            "off_host_copy_required": True,
            "database_mutated": False,
            "services_started": False,
            "secrets_disclosed": False,
        }
        self._write_bound_receipt(payload)
        self.assertEqual(self._verify(), payload)
        artifact.write_bytes(b"tampered")
        with self.assertRaisesRegex(backup.BackupError, "artifact_drifted"):
            self._verify()

    def test_receipt_rejects_extra_fields_and_restore_count_drift(self) -> None:
        self.backup_dir.mkdir(mode=0o700)
        artifact = (
            self.backup_dir
            / f"market-archive-before-{RELEASE_SHA[:12]}-20260827T120000Z-deadbeef.dump"
        )
        artifact.write_bytes(b"dump")
        artifact.chmod(0o600)
        base = {
            "schema": backup.RECEIPT_SCHEMA,
            "status": "PASS",
            "created_at_utc": backup.utc_text(backup.utc_now()),
            "release_sha": RELEASE_SHA,
            "release_tree": RELEASE_TREE,
            "image_id": IMAGE_ID,
            "image_input_signature": IMAGE_SIGNATURE,
            "role_env_binding": self._env_binding(),
            "source": {
                "container_id": "e" * 64,
                "database": "market_archive",
                "database_size_bytes": 1,
                "database_identity_sha256": "f" * 64,
                "schema_versions": [1],
                "table_count": 1,
                "fact_count": 1,
                **_invariants(fact_count=1),
            },
            "backup": {
                "path": str(artifact),
                "sha256": backup.file_digest(artifact),
                "size_bytes": 4,
                "format": "postgres_custom",
            },
            "restore_smoke": {
                "status": "PASS",
                "schema_versions": [1],
                "table_count": 1,
                "fact_count": 2,
                **_invariants(fact_count=2),
                "cleanup_status": "PASS",
            },
            "off_host_copy_required": True,
            "database_mutated": False,
            "services_started": False,
            "secrets_disclosed": False,
        }
        self._write_bound_receipt(base)
        with self.assertRaisesRegex(backup.BackupError, "metadata_invalid"):
            self._verify()
        base["unexpected_raw"] = "must-not-be-accepted"
        backup._write_receipt(self.receipt, base)
        with self.assertRaisesRegex(backup.BackupError, "schema_invalid"):
            self._verify()

    def test_database_inventory_binds_single_healthy_container_and_mount(self) -> None:
        container_id = "e" * 64
        document = {
            "Id": container_id,
            "State": {"Running": True, "Health": {"Status": "healthy"}},
            "Config": {
                "Image": backup.POSTGRES_IMAGE,
                "Labels": {
                    "com.docker.compose.project": "market-private-pipeline-production",
                    "com.docker.compose.service": "market-database",
                },
            },
            "Mounts": [
                {
                    "Source": str(self.postgres_root),
                    "Destination": "/var/lib/postgresql/data",
                }
            ],
        }

        def fake_run(arguments: list[str], *, label: str) -> str:
            joined = " ".join(arguments)
            if label == "backup_project_workload_inventory":
                return "market-database"
            if arguments[:3] == ["docker", "ps", "-q"]:
                return container_id
            if arguments[:2] == ["docker", "inspect"]:
                return json.dumps([document])
            sql = arguments[-1]
            if "to_regclass" in sql:
                return "t"
            if label == "backup_table_names":
                return "market_facts\nmarket_offers"
            if label == "backup_table_row_count":
                return "42" if sql.endswith("market_facts") else "7"
            if label == "backup_sequence_names":
                return "market_facts_id_seq"
            if label == "backup_sequence_value":
                return "99|t"
            if label == "backup_schema_catalogue":
                return '[{"table_name":"market_facts"}]'
            if label == "backup_schema_objects":
                return '[{"kind":"index","identity":"market_facts_pkey"}]'
            if "string_agg" in sql:
                return "1,2"
            if "pg_control_system" in sql:
                return "123456789"
            if "pg_database_size" in sql:
                return "1048576"
            raise AssertionError((label, joined))

        with mock.patch.object(backup, "_run_text", side_effect=fake_run):
            report = backup.inspect_source_database(self.values)
        self.assertEqual(report["schema_versions"], [1, 2])
        self.assertEqual(report["fact_count"], 42)
        self.assertEqual(report["table_count"], 2)
        self.assertEqual(report["table_row_counts"], {"market_facts": 42, "market_offers": 7})
        self.assertEqual(
            report["sequence_values"],
            {"market_facts_id_seq": {"last_value": 99, "is_called": True}},
        )
        self.assertNotIn("system_identifier", report)

    def test_sequence_is_called_accepts_verbose_boolean_text(self) -> None:
        seen = {"names": 0, "values": 0}
        schema_object_sql = ""

        def query(sql: str, *, label: str) -> str:
            nonlocal schema_object_sql
            if label == "backup_table_names":
                return "market_facts"
            if label == "backup_table_row_count":
                return "3"
            if label == "backup_sequence_names":
                seen["names"] += 1
                return "market_facts_id_seq"
            if label == "backup_sequence_value":
                seen["values"] += 1
                return "12|false"
            if label == "backup_schema_catalogue":
                return "[]"
            if label == "backup_schema_objects":
                schema_object_sql = sql
                return "[]"
            if "string_agg" in sql:
                return "1,2,3"
            raise AssertionError(label)

        report = backup._database_invariants(query)
        self.assertEqual(seen["values"], 1)
        self.assertEqual(
            report["sequence_values"],
            {"market_facts_id_seq": {"last_value": 12, "is_called": False}},
        )
        self.assertIn("set_config('search_path','pg_catalog',true)", schema_object_sql)
        self.assertIn("CROSS JOIN canonical_search_path", schema_object_sql)

    def test_database_inventory_rejects_initialized_database_without_archive_schema(self) -> None:
        container_id = "e" * 64
        document = {
            "Id": container_id,
            "State": {"Running": True, "Health": {"Status": "healthy"}},
            "Config": {
                "Image": backup.POSTGRES_IMAGE,
                "Labels": {
                    "com.docker.compose.project": "market-private-pipeline-production",
                    "com.docker.compose.service": "market-database",
                },
            },
            "Mounts": [
                {
                    "Source": str(self.postgres_root),
                    "Destination": "/var/lib/postgresql/data",
                }
            ],
        }

        def fake_run(arguments: list[str], *, label: str) -> str:
            if label == "backup_project_workload_inventory":
                return "market-database"
            if arguments[:3] == ["docker", "ps", "-q"]:
                return container_id
            if arguments[:2] == ["docker", "inspect"]:
                return json.dumps([document])
            if "to_regclass" in arguments[-1]:
                return "f"
            raise AssertionError((label, arguments))

        with mock.patch.object(backup, "_run_text", side_effect=fake_run):
            with self.assertRaisesRegex(backup.BackupError, "schema_unavailable"):
                backup.inspect_source_database(self.values)

    def test_release_env_forbids_primary_authority(self) -> None:
        values = dict(self.values)
        values["MARKET_PIPELINE_FEED_MODE"] = "PRIVATE_PRIMARY"
        _write_env(self.env_file, values)
        with self.assertRaisesRegex(backup.BackupError, "identity_mismatch"):
            backup.validate_release_env(
                self.env_file, release_sha=RELEASE_SHA, image_id=IMAGE_ID
            )

    def test_bluegreen_source_env_keeps_shadow_identity_but_binds_target_receipt(self) -> None:
        source_release = "e" * 40
        source_image = "sha256:" + "f" * 64
        values = dict(self.values)
        values["MARKET_PIPELINE_RELEASE_SHA"] = source_release
        values["MARKET_PIPELINE_IMAGE"] = source_image
        _write_env(self.env_file, values)

        observed = backup.validate_release_env(
            self.env_file,
            release_sha=RELEASE_SHA,
            image_id=IMAGE_ID,
            allow_target_identity_mismatch=True,
        )

        self.assertEqual(observed["MARKET_PIPELINE_RELEASE_SHA"], source_release)
        self.assertEqual(observed["MARKET_PIPELINE_IMAGE"], source_image)
        with self.assertRaisesRegex(backup.BackupError, "identity_mismatch"):
            backup.validate_release_env(
                self.env_file,
                release_sha=RELEASE_SHA,
                image_id=IMAGE_ID,
            )

    def test_bluegreen_source_env_accepts_coherent_primary_runtime(self) -> None:
        source_release = "e" * 40
        source_image = "sha256:" + "f" * 64
        values = dict(self.values)
        values.update(
            {
                "MARKET_PIPELINE_RELEASE_SHA": source_release,
                "MARKET_PIPELINE_IMAGE": source_image,
                "MARKET_PIPELINE_FEED_MODE": "PRIVATE_PRIMARY",
                "MARKET_PIPELINE_ALLOW_PRIVATE_PRIMARY": "1",
                "MARKET_PIPELINE_EXPECTED_SNAPSHOT_LANE": "PRIVATE_PRIMARY",
            }
        )
        _write_env(self.env_file, values)

        observed = backup.validate_release_env(
            self.env_file,
            release_sha=RELEASE_SHA,
            image_id=IMAGE_ID,
            allow_target_identity_mismatch=True,
        )

        self.assertEqual(observed["MARKET_PIPELINE_FEED_MODE"], "PRIVATE_PRIMARY")
        with self.assertRaisesRegex(backup.BackupError, "identity_mismatch"):
            backup.validate_release_env(
                self.env_file,
                release_sha=RELEASE_SHA,
                image_id=IMAGE_ID,
            )

    def test_bluegreen_source_env_rejects_incoherent_primary_lane(self) -> None:
        values = dict(self.values)
        values.update(
            {
                "MARKET_PIPELINE_FEED_MODE": "PRIVATE_PRIMARY",
                "MARKET_PIPELINE_ALLOW_PRIVATE_PRIMARY": "0",
                "MARKET_PIPELINE_EXPECTED_SNAPSHOT_LANE": "PRIVATE_PRIMARY",
            }
        )
        _write_env(self.env_file, values)

        with self.assertRaisesRegex(backup.BackupError, "identity_mismatch"):
            backup.validate_release_env(
                self.env_file,
                release_sha=RELEASE_SHA,
                image_id=IMAGE_ID,
                allow_target_identity_mismatch=True,
            )

    def test_bluegreen_source_identity_refusals_distinguish_release_and_image_without_relaxing_validation(self) -> None:
        cases = [
            ("MARKET_PIPELINE_RELEASE_SHA", "", "backup_source_release_sha_invalid"),
            ("MARKET_PIPELINE_RELEASE_SHA", "not-a-release", "backup_source_release_sha_invalid"),
            ("MARKET_PIPELINE_RELEASE_SHA", "e" * 39, "backup_source_release_sha_invalid"),
            ("MARKET_PIPELINE_IMAGE", "", "backup_source_image_identity_invalid"),
            ("MARKET_PIPELINE_IMAGE", "market_pipeline_release:synthetic-tag", "backup_source_image_identity_invalid"),
            ("MARKET_PIPELINE_IMAGE", "sha256:" + "f" * 63, "backup_source_image_identity_invalid"),
        ]
        for key, invalid, expected in cases:
            with self.subTest(field=key, reason=expected):
                values = dict(self.values)
                values[key] = invalid
                _write_env(self.env_file, values)
                with self.assertRaises(backup.BackupError) as refused:
                    backup.validate_release_env(
                        self.env_file,
                        release_sha=RELEASE_SHA,
                        image_id=IMAGE_ID,
                        allow_target_identity_mismatch=True,
                    )
                self.assertEqual(str(refused.exception), expected)
                self.assertNotIn(invalid or "never-disclose", str(refused.exception))
                self.assertFalse(self.backup_dir.exists())

        values = dict(self.values)
        values["MARKET_PIPELINE_RELEASE_SHA"] = ""
        values["MARKET_PIPELINE_IMAGE"] = ""
        _write_env(self.env_file, values)
        with self.assertRaisesRegex(backup.BackupError, "^backup_source_release_sha_invalid$"):
            backup.validate_release_env(
                self.env_file,
                release_sha=RELEASE_SHA,
                image_id=IMAGE_ID,
                allow_target_identity_mismatch=True,
            )

    def test_receipt_and_backup_must_be_isolated_from_database_root(self) -> None:
        with self.assertRaisesRegex(backup.BackupError, "destination_invalid"):
            backup.create_backup(
                env_file=self.env_file,
                backup_dir=self.backup_dir,
                receipt=self.root / "elsewhere.json",
                release_sha=RELEASE_SHA,
                release_tree=RELEASE_TREE,
                image_id=IMAGE_ID,
                image_input_signature=IMAGE_SIGNATURE,
            )
        with self.assertRaisesRegex(backup.BackupError, "directory_overlap"):
            backup.create_backup(
                env_file=self.env_file,
                backup_dir=self.postgres_root,
                receipt=self.postgres_root / "market-pipeline-backup-receipt.json",
                release_sha=RELEASE_SHA,
                release_tree=RELEASE_TREE,
                image_id=IMAGE_ID,
                image_input_signature=IMAGE_SIGNATURE,
            )

    def test_restore_runtime_is_isolated_pinned_and_label_owned(self) -> None:
        source = Path(backup.__file__).read_text(encoding="utf-8")
        restore = source.split("def restore_smoke(", 1)[1].split("\ndef _write_receipt", 1)[0]
        self.assertIn('"--network", "none"', restore)
        self.assertIn("POSTGRES_IMAGE", restore)
        self.assertIn("io.gold-trade.market-backup-run", restore)
        self.assertNotIn("--publish", restore)
        self.assertIn('"--exit-on-error"', restore)
        self.assertIn("_assert_restore_resource_absent", restore)
        self.assertIn('"-c", "SELECT 1"', restore)
        self.assertIn("consecutive >= 2", restore)

    def test_initial_empty_complete_journal_recovers_missing_receipt(self) -> None:
        with mock.patch.object(backup, "_running_project_services", return_value=[]):
            first = backup.create_backup(
                env_file=self.env_file,
                backup_dir=self.backup_dir,
                receipt=self.receipt,
                release_sha=RELEASE_SHA,
                release_tree=RELEASE_TREE,
                image_id=IMAGE_ID,
                image_input_signature=IMAGE_SIGNATURE,
            )
            self.receipt.unlink()
            recovered = backup.create_backup(
                env_file=self.env_file,
                backup_dir=self.backup_dir,
                receipt=self.receipt,
                release_sha=RELEASE_SHA,
                release_tree=RELEASE_TREE,
                image_id=IMAGE_ID,
                image_input_signature=IMAGE_SIGNATURE,
            )
        self.assertEqual(recovered, first)
        self.assertEqual(self._verify(), first)

    def test_refresh_resumes_after_receipt_was_archived(self) -> None:
        with mock.patch.object(backup, "_running_project_services", return_value=[]):
            first = backup.create_backup(
                env_file=self.env_file,
                backup_dir=self.backup_dir,
                receipt=self.receipt,
                release_sha=RELEASE_SHA,
                release_tree=RELEASE_TREE,
                image_id=IMAGE_ID,
                image_input_signature=IMAGE_SIGNATURE,
            )
            run_id = first["backup_run_id"]
            archived_receipt = (
                self.backup_dir / f"market-pipeline-backup-receipt.{run_id}.json"
            )
            # Reproduce SIGKILL between archiving the receipt and archiving the
            # matching journal.  Resume may only accept this exact payload.
            self.receipt.replace(archived_receipt)
            refreshed = backup.create_backup(
                env_file=self.env_file,
                backup_dir=self.backup_dir,
                receipt=self.receipt,
                release_sha=RELEASE_SHA,
                release_tree=RELEASE_TREE,
                image_id=IMAGE_ID,
                image_input_signature=IMAGE_SIGNATURE,
                refresh_complete=True,
            )
        self.assertNotEqual(refreshed["backup_run_id"], run_id)
        self.assertTrue(
            (self.backup_dir / f"market-pipeline-backup-journal.{run_id}.json").is_file()
        )
        self.assertEqual(self._verify(), refreshed)

    def test_pending_dump_is_resumed_without_second_pg_dump(self) -> None:
        self.backup_dir.mkdir(mode=0o700)
        destination = self.backup_dir / (
            f"market-archive-before-{RELEASE_SHA[:12]}-20260828T120000Z-deadbeef.dump"
        )
        candidate = self.backup_dir / f".{destination.name}.pending"
        candidate.write_bytes(b"completed-custom-dump")
        candidate.chmod(0o600)
        validation = mock.Mock(returncode=0)
        with mock.patch.object(backup.subprocess, "run", return_value=validation) as runner:
            backup._write_dump(
                container_id="e" * 64,
                user="market_data",
                database="market_archive",
                destination=destination,
            )
        self.assertEqual(runner.call_count, 1)
        self.assertIn("pg_restore", runner.call_args.args[0])
        self.assertEqual(destination.read_bytes(), b"completed-custom-dump")
        self.assertFalse(candidate.exists())

    def test_partial_pending_dump_is_not_promoted(self) -> None:
        self.backup_dir.mkdir(mode=0o700)
        destination = self.backup_dir / (
            f"market-archive-before-{RELEASE_SHA[:12]}-20260828T120000Z-deadbeef.dump"
        )
        candidate = self.backup_dir / f".{destination.name}.pending"
        candidate.write_bytes(b"partial")
        candidate.chmod(0o600)
        results = [
            mock.Mock(returncode=1),  # resumed candidate validation
            mock.Mock(returncode=9),  # fresh pg_dump fails in this fixture
        ]
        with mock.patch.object(backup.subprocess, "run", side_effect=results):
            with self.assertRaisesRegex(backup.BackupError, "pg_dump_failed_rc_9"):
                backup._write_dump(
                    container_id="e" * 64,
                    user="market_data",
                    database="market_archive",
                    destination=destination,
                )
        self.assertFalse(destination.exists())
        self.assertFalse(candidate.exists())

    def test_journal_tamper_is_rejected(self) -> None:
        with mock.patch.object(backup, "_running_project_services", return_value=[]):
            backup.create_backup(
                env_file=self.env_file,
                backup_dir=self.backup_dir,
                receipt=self.receipt,
                release_sha=RELEASE_SHA,
                release_tree=RELEASE_TREE,
                image_id=IMAGE_ID,
                image_input_signature=IMAGE_SIGNATURE,
            )
        journal_path = backup._journal_path(self.backup_dir)
        journal = json.loads(journal_path.read_text(encoding="utf-8"))
        journal["unexpected"] = "tamper"
        backup._write_journal(journal_path, journal)
        with self.assertRaisesRegex(backup.BackupError, "journal_schema_invalid"):
            self._verify()

    def test_source_after_mismatch_is_rejected_without_overwrite(self) -> None:
        self.backup_dir.mkdir(mode=0o700)
        artifact = self.backup_dir / (
            f"market-archive-before-{RELEASE_SHA[:12]}-20260828T120000Z-deadbeef.dump"
        )
        artifact.write_bytes(b"dump")
        artifact.chmod(0o600)
        source = {
            "container_id": "e" * 64,
            "database": "market_archive",
            "database_size_bytes": 100,
            "database_identity_sha256": "f" * 64,
            "schema_versions": [1],
            "table_count": 1,
            "fact_count": 7,
            **_invariants(fact_count=7),
        }
        source_after = deepcopy(source)
        source_after["fact_count"] = 8
        source_after["table_row_counts"]["market_facts"] = 8
        payload: dict[str, object] = {
            "schema": backup.RECEIPT_SCHEMA,
            "status": "PASS",
            "created_at_utc": backup.utc_text(backup.utc_now()),
            "release_sha": RELEASE_SHA,
            "release_tree": RELEASE_TREE,
            "image_id": IMAGE_ID,
            "image_input_signature": IMAGE_SIGNATURE,
            "role_env_binding": self._env_binding(),
            "source": source,
            "source_after": source_after,
            "backup": {
                "path": str(artifact), "sha256": backup.file_digest(artifact),
                "size_bytes": 4, "format": "postgres_custom",
            },
            "restore_smoke": {
                "status": "PASS", "schema_versions": [1], "table_count": 1,
                "fact_count": 7, **_invariants(fact_count=7),
                "cleanup_status": "PASS",
            },
            "off_host_copy_required": True,
            "database_mutated": False,
            "services_started": False,
            "secrets_disclosed": False,
        }
        self._write_bound_receipt(payload)
        with self.assertRaisesRegex(backup.BackupError, "metadata_invalid"):
            self._verify()

    def test_writer_workload_must_be_quiesced(self) -> None:
        with mock.patch.object(
            backup,
            "_running_project_services",
            return_value=["market-capture-account1", "market-database"],
        ):
            with self.assertRaisesRegex(backup.BackupError, "writer_workloads_not_quiesced"):
                backup._assert_writer_workloads_quiesced(
                    self.values["MARKET_PIPELINE_PROJECT_NAME"]
                )
            with self.assertRaisesRegex(backup.BackupError, "writer_workloads_not_quiesced"):
                backup.inspect_source_database(self.values)

    def test_hot_backup_allows_running_writers_at_inspect_gate(self) -> None:
        with mock.patch.object(
            backup,
            "_running_project_services",
            return_value=["market-capture-account1", "market-database"],
        ):
            with mock.patch.object(
                backup,
                "_container_ids",
                side_effect=backup.BackupError("stopped_at_container_lookup"),
            ):
                with self.assertRaisesRegex(backup.BackupError, "stopped_at_container_lookup"):
                    backup.inspect_source_database(self.values, require_quiesce=False)
        self.assertNotEqual(backup.CREATE_CONFIRMATION, backup.HOT_CREATE_CONFIRMATION)
        self.assertIn("hot-backup", backup.HOT_CREATE_CONFIRMATION)

    def test_hot_window_allows_row_drift_but_not_schema_or_identity_drift(self) -> None:
        before = {
            "database_identity_sha256": "a" * 64,
            "schema_versions": [1, 2],
            "schema_catalog_sha256": "1" * 64,
            "schema_objects_sha256": "2" * 64,
            "table_count": 2,
            "fact_count": 10,
            "table_row_counts": {"market_facts": 10, "market_offers": 1},
            "sequence_values": {"market_facts_id_seq": {"last_value": 10, "is_called": True}},
        }
        after = dict(before)
        after["fact_count"] = 12
        after["table_row_counts"] = {"market_facts": 12, "market_offers": 1}
        after["sequence_values"] = {
            "market_facts_id_seq": {"last_value": 12, "is_called": True}
        }
        self.assertTrue(
            backup._source_window_compatible(
                before, after, allow_running_writers=True
            )
        )
        self.assertFalse(
            backup._source_window_compatible(
                before, after, allow_running_writers=False
            )
        )
        drifted_schema = dict(after)
        drifted_schema["schema_catalog_sha256"] = "3" * 64
        self.assertFalse(
            backup._source_window_compatible(
                before, drifted_schema, allow_running_writers=True
            )
        )
        drifted_identity = dict(after)
        drifted_identity["database_identity_sha256"] = "b" * 64
        self.assertFalse(
            backup._source_window_compatible(
                before, drifted_identity, allow_running_writers=True
            )
        )
        restore = {
            "schema_versions": [1, 2],
            "schema_catalog_sha256": "9" * 64,
            "schema_objects_sha256": "8" * 64,
            "table_count": 2,
            "fact_count": 11,
            "table_row_counts": {"market_facts": 11, "market_offers": 1},
            "sequence_values": {"market_facts_id_seq": {"last_value": 11, "is_called": True}},
        }
        self.assertTrue(
            backup._restore_window_compatible(
                before, after, restore, allow_running_writers=True
            )
        )
        self.assertFalse(
            backup._restore_window_compatible(
                before, after, restore, allow_running_writers=False
            )
        )
        matching_rows = dict(restore)
        matching_rows["fact_count"] = before["fact_count"]
        matching_rows["table_row_counts"] = before["table_row_counts"]
        matching_rows["sequence_values"] = before["sequence_values"]
        self.assertTrue(
            backup._restore_window_compatible(
                before, before, matching_rows, allow_running_writers=False
            )
        )

    def test_hot_retry_abandons_empty_prepared_journal(self) -> None:
        journal = {
            "status": "PREPARED",
            "backup": None,
            "artifact_path": str(self.backup_dir / "market-archive-before-aaaaaaaaaaaa-20260101T000000Z-01234567.dump"),
            "candidate_path": str(self.backup_dir / ".market-archive-before-aaaaaaaaaaaa-20260101T000000Z-01234567.dump.pending"),
        }
        self.backup_dir.mkdir(mode=0o700)
        path = self.backup_dir / "market-pipeline-backup-journal.json"
        path.write_text("{}\n", encoding="utf-8")
        self.assertTrue(backup._empty_prepared_journal(journal, root=self.backup_dir))
        backup._abandon_empty_prepared_journal(path, journal, root=self.backup_dir)
        self.assertFalse(path.exists())
        with self.assertRaisesRegex(backup.BackupError, "not_abandonable"):
            backup._abandon_empty_prepared_journal(
                path,
                {**journal, "status": "DUMP_READY"},
                root=self.backup_dir,
            )

    def test_orphan_cleanup_refuses_wrong_owner_label(self) -> None:
        inspected = subprocess.CompletedProcess(
            [], 0, stdout="different-run\n", stderr=""
        )
        with (
            mock.patch.object(backup.subprocess, "run", return_value=inspected),
            mock.patch.object(backup, "_run_text") as destructive,
        ):
            with self.assertRaisesRegex(backup.BackupError, "owner_mismatch"):
                backup._cleanup_owned_restore_resource(
                    "container", "market_pipeline_restore_0123456789abcdef",
                    "market-pipeline-backup-0123456789abcdef",
                )
        destructive.assert_not_called()

    def test_production_backup_directory_rejects_tmp(self) -> None:
        with self.assertRaisesRegex(backup.BackupError, "tmp_forbidden"):
            REAL_SECURE_DIRECTORY(self.backup_dir, create=False)

    def test_real_create_cli_reports_source_refusal_without_traceback_or_private_text(self) -> None:
        args = [
            "create", "--env-file", str(self.env_file), "--receipt", str(self.receipt),
            "--release-sha", RELEASE_SHA, "--release-tree", RELEASE_TREE,
            "--image-id", IMAGE_ID, "--image-input-signature", IMAGE_SIGNATURE,
            "--bluegreen-source-env", "--backup-dir", str(self.backup_dir),
            "--allow-running-writers", "--confirm", backup.HOT_CREATE_CONFIRMATION,
        ]
        errors = [
            (backup.ReleaseContractError("source_env_required_keys_missing"), "source_env_required_keys_missing"),
            (backup.ReleaseContractError("private value /sensitive/path"), "backup_cli_input_or_io_failure"),
            (OSError("private value /sensitive/path"), "backup_cli_input_or_io_failure"),
            (ValueError("private value /sensitive/path"), "backup_cli_input_or_io_failure"),
            (backup.BackupError("backup_database_runtime_identity_mismatch"), "backup_database_runtime_identity_mismatch"),
            (backup.BackupError("backup_source_release_sha_invalid"), "backup_source_release_sha_invalid"),
            (backup.BackupError("backup_source_image_identity_invalid"), "backup_source_image_identity_invalid"),
        ]
        for error, expected in errors:
            with self.subTest(error=type(error).__name__, expected=expected):
                output, diagnostics = io.StringIO(), io.StringIO()
                with mock.patch.object(backup, "create_backup", side_effect=error) as create, \
                        mock.patch.object(backup.sys, "stdout", output), \
                        mock.patch.object(backup.sys, "stderr", diagnostics), \
                        mock.patch.object(backup.subprocess, "run", side_effect=AssertionError("no host commands")):
                    self.assertEqual(backup.main(args), 1)
                self.assertEqual(output.getvalue(), "")
                self.assertEqual(json.loads(diagnostics.getvalue()), {
                    "status": "fail", "reason_code": expected, "secrets_disclosed": False,
                })
                self.assertTrue(create.call_args.kwargs["allow_running_writers"])
                self.assertTrue(create.call_args.kwargs["allow_target_identity_mismatch"])
                self.assertFalse(self.backup_dir.exists())


if __name__ == "__main__":
    unittest.main()
