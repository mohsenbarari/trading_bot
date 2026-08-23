import gzip
import hashlib
import io
import json
import os
import re
import signal
from contextlib import ExitStack, redirect_stdout
from datetime import datetime, timedelta, timezone
from io import StringIO
from pathlib import Path
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
import unittest
from unittest.mock import patch

from alembic.config import Config
from alembic.script import ScriptDirectory

from scripts import rehearse_production_migration as rehearsal


def subprocess_output(*args: str) -> str:
    return subprocess.run(
        list(args),
        check=True,
        cwd=rehearsal.REPO_ROOT,
        text=True,
        stdout=subprocess.PIPE,
    ).stdout.strip()


def _write_dump(path: Path, *, owner: str = "app_owner") -> tuple[int, str]:
    sql = (
        "--\n-- PostgreSQL database dump\n--\n"
        "CREATE TABLE public.example (id integer);\n"
        f"ALTER TABLE public.example OWNER TO {owner};\n"
    ).encode("utf-8")
    with gzip.open(path, "wb") as handle:
        handle.write(sql)
    path.chmod(0o600)
    payload = path.read_bytes()
    return len(payload), hashlib.sha256(payload).hexdigest()


def _write_tar_gzip(path: Path, *, marker: str) -> tuple[int, str]:
    data = marker.encode("utf-8")
    with tarfile.open(path, "w:gz") as archive:
        info = tarfile.TarInfo(name="evidence.txt")
        info.size = len(data)
        archive.addfile(info, io.BytesIO(data))
    path.chmod(0o600)
    payload = path.read_bytes()
    return len(payload), hashlib.sha256(payload).hexdigest()


class ReceiptFixture:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.backup_root = root / "host-backups"
        self.pull_root = root / "iran-pull"
        self.receipt_root = root / "backup-evidence"
        for path in (self.backup_root, self.pull_root, self.receipt_root):
            path.mkdir(mode=0o700)
        foreign_run = self.backup_root / "foreign-run"
        foreign_run.mkdir(mode=0o700)
        self.foreign_dump = foreign_run / "foreign-db-test.sql.gz"
        self.iran_dump = self.pull_root / "iran-db-test.sql.gz"
        self.foreign_artifacts = self._artifacts(
            role="foreign",
            db_path=self.foreign_dump,
            local_root=foreign_run,
            remote_root=foreign_run,
        )
        self.iran_artifacts = self._artifacts(
            role="iran",
            db_path=self.iran_dump,
            local_root=self.pull_root,
            remote_root=self.backup_root / "iran-run",
        )
        self.release_sha = "1" * 40
        self.manifest_values = {
            "LOCAL_PROJECT_DIR": str(rehearsal.REPO_ROOT),
            "FOREIGN_PUBLIC_DOMAIN": "coin.362514.ir",
            "IRAN_HOST": "iran.production.invalid",
            "IRAN_SSH_USER": "root",
            "IRAN_SSH_PORT": "22",
            "IRAN_PROJECT_DIR": "/srv/trading-bot/current",
            "IRAN_APP_DOMAIN": "coin.gold-trade.ir",
        }
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        self.payload_created_at = now
        self.payload = {
            "status": "ok",
            "created_at": now,
            "roles": ["foreign", "iran"],
            "results": [
                self._result(
                    role="foreign",
                    artifacts=self.foreign_artifacts,
                    database_identity="2" * 64,
                ),
                self._result(
                    role="iran",
                    artifacts=self.iran_artifacts,
                    database_identity="3" * 64,
                ),
            ],
        }
        self.receipt = self.receipt_root / "production-backup.json"
        self.write_receipt()

    def _artifacts(
        self,
        *,
        role: str,
        db_path: Path,
        local_root: Path,
        remote_root: Path,
    ) -> dict[str, dict[str, object]]:
        artifacts: dict[str, dict[str, object]] = {}
        for kind in rehearsal.EXPECTED_BACKUP_KINDS:
            local_path = db_path if kind == "db" else local_root / f"{role}-{kind}-test.tar.gz"
            remote_path = (
                remote_root / (db_path.name if kind == "db" else local_path.name)
            )
            if kind == "db":
                size, digest = _write_dump(local_path)
            else:
                size, digest = _write_tar_gzip(local_path, marker=f"{role}-{kind}")
            artifacts[kind] = {
                "kind": kind,
                "path": str(remote_path),
                "local_path": str(local_path),
                "bytes": size,
                "sha256": digest,
            }
        return artifacts

    def _result(
        self,
        *,
        role: str,
        artifacts: dict[str, dict[str, object]],
        database_identity: str,
    ) -> dict[str, object]:
        backup_run_dir = Path(str(next(iter(artifacts.values()))["path"])).parent
        volume_names_sha256 = hashlib.sha256(
            f"restore-volume-{role}\n".encode("utf-8")
        ).hexdigest()
        owned_volume_count = 1
        cleanup_proof_sha256 = hashlib.sha256(
            (
                f"{backup_run_dir.name}\0true\0true\0{owned_volume_count}"
                f"\0true\0{volume_names_sha256}"
            ).encode("utf-8")
        ).hexdigest()
        result: dict[str, object] = {
            "status": "ok",
            "created_at": self.payload_created_at,
            "backup_dir": str(backup_run_dir),
            "role": role,
            "command_role": role,
            "schema_head": rehearsal.EXPECTED_PRE_MIGRATION_HEAD,
            "project_label": rehearsal.EXPECTED_PROJECT_LABELS[role],
            "release_sha": self.release_sha,
            "restore_smoke": {
                "status": "passed",
                "table_count": 50,
                "cleanup": {
                    "status": "passed",
                    "container_absent": True,
                    "named_volume_absent": True,
                    "owned_volume_count": owned_volume_count,
                    "owned_volumes_absent": True,
                    "owned_volume_names_sha256": volume_names_sha256,
                    "proof_sha256": cleanup_proof_sha256,
                    "commands_bounded": True,
                    "error": None,
                },
            },
            "target_binding_sha256": rehearsal.backup_target_binding_sha256(
                role, self.manifest_values
            ),
            "database_identity_sha256": database_identity,
            "files": [
                {
                    key: value
                    for key, value in artifacts[kind].items()
                    if key != "local_path"
                }
                for kind in rehearsal.EXPECTED_BACKUP_KINDS
            ],
        }
        if role == "iran":
            result["pulled_files"] = [
                {
                    "remote_path": artifacts[kind]["path"],
                    "local_path": artifacts[kind]["local_path"],
                }
                for kind in rehearsal.EXPECTED_BACKUP_KINDS
            ]
        return result

    def write_receipt(self) -> str:
        self.receipt.write_text(
            json.dumps(self.payload, sort_keys=True) + "\n", encoding="utf-8"
        )
        self.receipt.chmod(0o600)
        return hashlib.sha256(self.receipt.read_bytes()).hexdigest()

    def patches(self) -> ExitStack:
        stack = ExitStack()
        stack.enter_context(
            patch.object(rehearsal, "DEFAULT_BACKUP_DIR", str(self.backup_root))
        )
        stack.enter_context(
            patch.object(rehearsal, "DEFAULT_IRAN_PULL_DIR", self.pull_root)
        )
        stack.enter_context(
            patch.object(rehearsal, "DEFAULT_BACKUP_RECEIPT_DIR", self.receipt_root)
        )
        return stack


class ProductionMigrationRehearsalTests(unittest.TestCase):
    def test_expected_delta_is_exactly_fourteen_tables(self):
        self.assertEqual(len(rehearsal.EXPECTED_NEW_TABLES), 14)
        self.assertEqual(len(set(rehearsal.EXPECTED_NEW_TABLES)), 14)
        self.assertIn("telegram_delivery_jobs", rehearsal.EXPECTED_NEW_TABLES)
        self.assertIn("user_flags", rehearsal.EXPECTED_NEW_TABLES)

    def test_expected_table_delta_matches_current_migration_graph_from_f2(self):
        config = Config(str(rehearsal.REPO_ROOT / "alembic.ini"))
        config.set_main_option(
            "script_location", str(rehearsal.REPO_ROOT / "migrations")
        )
        script = ScriptDirectory.from_config(config)
        head_ancestors = {
            revision.revision: revision
            for revision in script.iterate_revisions("heads", "base")
        }
        f2_ancestors = {
            revision.revision
            for revision in script.iterate_revisions(
                rehearsal.EXPECTED_PRE_MIGRATION_HEAD, "base"
            )
        }
        created: set[str] = set()
        for revision_id in set(head_ancestors) - f2_ancestors:
            text = Path(head_ancestors[revision_id].path).read_text(encoding="utf-8")
            created.update(
                re.findall(r'op\.create_table\(\s*["\']([^"\']+)', text)
            )
        self.assertEqual(created, set(rehearsal.EXPECTED_NEW_TABLES))

    def test_source_checkout_requires_clean_pushed_unique_main(self):
        answers = {
            ("branch", "--show-current"): "main",
            ("rev-parse", "--verify", "HEAD^{commit}"): "a" * 40,
            ("rev-parse", "--verify", "refs/remotes/origin/main^{commit}"): "a" * 40,
            (
                "ls-remote",
                "--exit-code",
                "origin",
                "refs/heads/main",
            ): f"{'a' * 40}\trefs/heads/main",
            ("status", "--porcelain=v1", "--untracked-files=all"): "",
            ("for-each-ref", "--format=%(objectname)", "refs/heads/main"): "a" * 40,
            ("rev-parse", "--verify", "HEAD^{tree}"): "b" * 40,
        }
        with patch.object(rehearsal, "_git", side_effect=lambda *args: answers[args]), patch.object(
            rehearsal, "source_alembic_head", return_value="fd3e4f5a6b7c"
        ):
            binding = rehearsal.verify_source_checkout()
        self.assertEqual(binding.commit, "a" * 40)
        self.assertEqual(binding.tree, "b" * 40)
        self.assertEqual(binding.alembic_head, "fd3e4f5a6b7c")

        answers[("status", "--porcelain=v1", "--untracked-files=all")] = " M main.py"
        with patch.object(rehearsal, "_git", side_effect=lambda *args: answers[args]):
            with self.assertRaisesRegex(rehearsal.RehearsalRefusal, "clean"):
                rehearsal.verify_source_checkout()

        answers[("status", "--porcelain=v1", "--untracked-files=all")] = ""
        answers[("rev-parse", "--verify", "refs/remotes/origin/main^{commit}")] = "c" * 40
        with patch.object(rehearsal, "_git", side_effect=lambda *args: answers[args]):
            with self.assertRaisesRegex(rehearsal.RehearsalRefusal, "pushed"):
                rehearsal.verify_source_checkout()

    def test_verified_receipt_binds_roles_release_schema_target_and_hashes(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = ReceiptFixture(Path(tmp))
            digest = fixture.write_receipt()
            with fixture.patches():
                backup = rehearsal.verify_backup_receipt(
                    receipt_path=fixture.receipt,
                    receipt_sha256=digest,
                    expected_release_sha=fixture.release_sha,
                    manifest_values=fixture.manifest_values,
                )
        self.assertEqual([item.role for item in backup.artifacts], ["foreign", "iran"])
        self.assertEqual(backup.production_release_sha, fixture.release_sha)
        self.assertEqual(
            backup.pre_migration_head, rehearsal.EXPECTED_PRE_MIGRATION_HEAD
        )

    def test_verified_receipt_accepts_exact_source_head_and_refuses_mixed_roles(self):
        source_head = "ff5a6b7c8d9e"
        with tempfile.TemporaryDirectory() as tmp:
            fixture = ReceiptFixture(Path(tmp))
            for result in fixture.payload["results"]:
                result["schema_head"] = source_head
            digest = fixture.write_receipt()
            with fixture.patches():
                backup = rehearsal.verify_backup_receipt(
                    receipt_path=fixture.receipt,
                    receipt_sha256=digest,
                    expected_release_sha=fixture.release_sha,
                    manifest_values=fixture.manifest_values,
                    expected_source_head=source_head,
                )
            self.assertEqual(backup.pre_migration_head, source_head)
            self.assertTrue(
                all(artifact.pre_revision == source_head for artifact in backup.artifacts)
            )

            fixture.payload["results"][1][
                "schema_head"
            ] = rehearsal.EXPECTED_PRE_MIGRATION_HEAD
            digest = fixture.write_receipt()
            with fixture.patches(), self.assertRaisesRegex(
                rehearsal.RehearsalRefusal, "one pre-migration schema"
            ):
                rehearsal.verify_backup_receipt(
                    receipt_path=fixture.receipt,
                    receipt_sha256=digest,
                    expected_release_sha=fixture.release_sha,
                    manifest_values=fixture.manifest_values,
                    expected_source_head=source_head,
                )

    def test_verified_receipt_refuses_schema_outside_two_exact_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = ReceiptFixture(Path(tmp))
            for result in fixture.payload["results"]:
                result["schema_head"] = "abc123def456"
            digest = fixture.write_receipt()
            with fixture.patches(), self.assertRaisesRegex(
                rehearsal.RehearsalRefusal, "pre-migration schema"
            ):
                rehearsal.verify_backup_receipt(
                    receipt_path=fixture.receipt,
                    receipt_sha256=digest,
                    expected_release_sha=fixture.release_sha,
                    manifest_values=fixture.manifest_values,
                    expected_source_head="ff5a6b7c8d9e",
                )

    def test_verified_receipt_requires_exact_restore_cleanup_proof(self):
        mutations = (
            ("missing", lambda cleanup: None),
            ("named-volume", lambda cleanup: cleanup.update(named_volume_absent=False)),
            ("unbounded", lambda cleanup: cleanup.update(commands_bounded=False)),
            ("digest", lambda cleanup: cleanup.update(proof_sha256="0" * 64)),
        )
        for label, mutate in mutations:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as tmp:
                fixture = ReceiptFixture(Path(tmp))
                restore = fixture.payload["results"][0]["restore_smoke"]
                cleanup = restore["cleanup"]
                if label == "missing":
                    restore.pop("cleanup")
                else:
                    mutate(cleanup)
                digest = fixture.write_receipt()
                with fixture.patches(), self.assertRaisesRegex(
                    rehearsal.RehearsalRefusal, "cleanup proof"
                ):
                    rehearsal.verify_backup_receipt(
                        receipt_path=fixture.receipt,
                        receipt_sha256=digest,
                        expected_release_sha=fixture.release_sha,
                        manifest_values=fixture.manifest_values,
                    )

    def test_receipt_refuses_digest_staleness_release_schema_and_tampered_artifact(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = ReceiptFixture(Path(tmp))
            with fixture.patches():
                with self.subTest("digest"):
                    with self.assertRaisesRegex(rehearsal.RehearsalRefusal, "digest"):
                        rehearsal.verify_backup_receipt(
                            receipt_path=fixture.receipt,
                            receipt_sha256="f" * 64,
                            expected_release_sha=fixture.release_sha,
                            manifest_values=fixture.manifest_values,
                        )
                fixture.payload["created_at"] = (
                    datetime.now(timezone.utc) - timedelta(hours=3)
                ).isoformat().replace("+00:00", "Z")
                digest = fixture.write_receipt()
                with self.subTest("stale"):
                    with self.assertRaisesRegex(rehearsal.RehearsalRefusal, "fresh"):
                        rehearsal.verify_backup_receipt(
                            receipt_path=fixture.receipt,
                            receipt_sha256=digest,
                            expected_release_sha=fixture.release_sha,
                            manifest_values=fixture.manifest_values,
                        )
                fixture.payload["created_at"] = datetime.now(timezone.utc).isoformat().replace(
                    "+00:00", "Z"
                )
                fixture.payload["results"][0]["schema_head"] = "bad000000000"
                digest = fixture.write_receipt()
                with self.subTest("schema"):
                    with self.assertRaisesRegex(rehearsal.RehearsalRefusal, "schema"):
                        rehearsal.verify_backup_receipt(
                            receipt_path=fixture.receipt,
                            receipt_sha256=digest,
                            expected_release_sha=fixture.release_sha,
                            manifest_values=fixture.manifest_values,
                        )
                fixture.payload["results"][0]["schema_head"] = rehearsal.EXPECTED_PRE_MIGRATION_HEAD
                fixture.payload["results"][0]["release_sha"] = "9" * 40
                digest = fixture.write_receipt()
                with self.subTest("release"):
                    with self.assertRaisesRegex(rehearsal.RehearsalRefusal, "release"):
                        rehearsal.verify_backup_receipt(
                            receipt_path=fixture.receipt,
                            receipt_sha256=digest,
                            expected_release_sha=fixture.release_sha,
                            manifest_values=fixture.manifest_values,
                        )
                fixture.payload["results"][0]["release_sha"] = fixture.release_sha
                digest = fixture.write_receipt()
                fixture.foreign_dump.write_bytes(fixture.foreign_dump.read_bytes() + b"tamper")
                with self.subTest("artifact"):
                    with self.assertRaisesRegex(rehearsal.RehearsalRefusal, "hash or size"):
                        rehearsal.verify_backup_receipt(
                            receipt_path=fixture.receipt,
                            receipt_sha256=digest,
                            expected_release_sha=fixture.release_sha,
                            manifest_values=fixture.manifest_values,
                        )

    def test_receipt_refuses_role_duplication_and_target_binding_drift(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = ReceiptFixture(Path(tmp))
            fixture.payload["results"][1]["role"] = "foreign"
            digest = fixture.write_receipt()
            with fixture.patches(), self.assertRaisesRegex(
                rehearsal.RehearsalRefusal, "role"
            ):
                rehearsal.verify_backup_receipt(
                    receipt_path=fixture.receipt,
                    receipt_sha256=digest,
                    expected_release_sha=fixture.release_sha,
                    manifest_values=fixture.manifest_values,
                )
            fixture.payload["results"][1]["role"] = "iran"
            fixture.payload["results"][1]["target_binding_sha256"] = "0" * 64
            digest = fixture.write_receipt()
            with fixture.patches(), self.assertRaisesRegex(
                rehearsal.RehearsalRefusal, "target binding"
            ):
                rehearsal.verify_backup_receipt(
                    receipt_path=fixture.receipt,
                    receipt_sha256=digest,
                    expected_release_sha=fixture.release_sha,
                    manifest_values=fixture.manifest_values,
                )

    def test_receipt_requires_all_four_artifacts_and_all_four_iran_pulls(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = ReceiptFixture(Path(tmp))
            fixture.payload["results"][0]["files"] = fixture.payload["results"][0][
                "files"
            ][:-1]
            digest = fixture.write_receipt()
            with fixture.patches(), self.assertRaisesRegex(
                rehearsal.RehearsalRefusal, "files|DB, Redis"
            ):
                rehearsal.verify_backup_receipt(
                    receipt_path=fixture.receipt,
                    receipt_sha256=digest,
                    expected_release_sha=fixture.release_sha,
                    manifest_values=fixture.manifest_values,
                )

    def test_receipt_rejects_unapproved_backup_roots_and_remote_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = ReceiptFixture(Path(tmp))
            fixture.payload["results"][0]["backup_dir"] = "/tmp/backups"
            digest = fixture.write_receipt()
            with fixture.patches(), self.assertRaisesRegex(
                rehearsal.RehearsalRefusal, "approved backup root"
            ):
                rehearsal.verify_backup_receipt(
                    receipt_path=fixture.receipt,
                    receipt_sha256=digest,
                    expected_release_sha=fixture.release_sha,
                    manifest_values=fixture.manifest_values,
                )

            fixture = ReceiptFixture(Path(tmp) / "remote-path")
            fixture.payload["results"][1]["files"][0]["path"] = (
                "/tmp/iran-run/iran-db-test.sql.gz"
            )
            fixture.payload["results"][1]["pulled_files"][0]["remote_path"] = (
                "/tmp/iran-run/iran-db-test.sql.gz"
            )
            digest = fixture.write_receipt()
            with fixture.patches(), self.assertRaisesRegex(
                rehearsal.RehearsalRefusal, "exact run root"
            ):
                rehearsal.verify_backup_receipt(
                    receipt_path=fixture.receipt,
                    receipt_sha256=digest,
                    expected_release_sha=fixture.release_sha,
                    manifest_values=fixture.manifest_values,
                )

            fixture = ReceiptFixture(Path(tmp) / "second")
            fixture.payload["results"][1]["pulled_files"] = fixture.payload[
                "results"
            ][1]["pulled_files"][:-1]
            digest = fixture.write_receipt()
            with fixture.patches(), self.assertRaisesRegex(
                rehearsal.RehearsalRefusal, "four Iran"
            ):
                rehearsal.verify_backup_receipt(
                    receipt_path=fixture.receipt,
                    receipt_sha256=digest,
                    expected_release_sha=fixture.release_sha,
                    manifest_values=fixture.manifest_values,
                )

    def test_runner_prebuild_receipt_binds_commit_tree_image_and_signature(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            receipt = root / "foreign-image-prebuild-receipt.json"
            source = rehearsal.SourceBinding("a" * 40, "b" * 40, "fd3e4f5a6b7c")
            image_id = "sha256:" + "c" * 64
            payload = {
                "schema_version": 1,
                "environment": "production",
                "release_sha": source.commit,
                "release_tree": source.tree,
                "image_id": image_id,
                "input_signature": "d" * 64,
                "secrets_disclosed": False,
            }
            receipt.write_text(json.dumps(payload) + "\n", encoding="utf-8")
            receipt.chmod(0o600)
            digest = hashlib.sha256(receipt.read_bytes()).hexdigest()
            with patch.object(
                rehearsal, "DEFAULT_RUNNER_PREBUILD_RECEIPT", receipt
            ):
                binding = rehearsal.verify_runner_prebuild_receipt(
                    receipt_path=receipt,
                    receipt_sha256=digest,
                    expected_image_id=image_id,
                    source=source,
                )
                self.assertEqual(binding.input_signature, "d" * 64)
                self.assertEqual(binding.receipt_path, receipt)
                payload["unexpected"] = True
                receipt.write_text(json.dumps(payload) + "\n", encoding="utf-8")
                receipt.chmod(0o600)
                unexpected_digest = hashlib.sha256(receipt.read_bytes()).hexdigest()
                with self.assertRaisesRegex(
                    rehearsal.RehearsalRefusal, "source/tree/image"
                ):
                    rehearsal.verify_runner_prebuild_receipt(
                        receipt_path=receipt,
                        receipt_sha256=unexpected_digest,
                        expected_image_id=image_id,
                        source=source,
                    )
                payload.pop("unexpected")
                payload["release_tree"] = "e" * 40
                receipt.write_text(json.dumps(payload) + "\n", encoding="utf-8")
                receipt.chmod(0o600)
                drifted_digest = hashlib.sha256(receipt.read_bytes()).hexdigest()
                with patch.object(
                    rehearsal, "_docker", side_effect=AssertionError("Docker must not run")
                ), self.assertRaisesRegex(rehearsal.RehearsalRefusal, "digest"):
                    rehearsal.verify_runner_image(binding, source=source)
                with self.assertRaisesRegex(
                    rehearsal.RehearsalRefusal, "source/tree/image"
                ):
                    rehearsal.verify_runner_prebuild_receipt(
                        receipt_path=receipt,
                        receipt_sha256=drifted_digest,
                        expected_image_id=image_id,
                        source=source,
                    )
    def test_plain_dump_validation_rejects_custom_or_broken_gzip(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            custom = root / "custom.sql.gz"
            with gzip.open(custom, "wb") as handle:
                handle.write(b"PGDMP custom archive")
            with self.assertRaisesRegex(rehearsal.RehearsalRefusal, "plain"):
                rehearsal._validate_plain_gzip_dump(custom)
            broken = root / "broken.sql.gz"
            broken.write_bytes(b"not-gzip")
            with self.assertRaisesRegex(rehearsal.RehearsalRefusal, "gzip"):
                rehearsal._validate_plain_gzip_dump(broken)

    def test_owner_roles_are_parsed_safely(self):
        with tempfile.TemporaryDirectory() as tmp:
            dump = Path(tmp) / "owners.sql.gz"
            with gzip.open(dump, "wt", encoding="utf-8") as handle:
                handle.write("-- PostgreSQL database dump\n")
                handle.write('ALTER TABLE x OWNER TO app_owner;\n')
                handle.write('ALTER TABLE y OWNER TO "quoted_owner";\n')
                handle.write('ALTER TABLE z OWNER TO postgres;\n')
            self.assertEqual(
                rehearsal.extract_owner_roles(dump), ("app_owner", "quoted_owner")
            )
            with gzip.open(dump, "wt", encoding="utf-8") as handle:
                handle.write("-- PostgreSQL database dump\nALTER TABLE x OWNER TO bad-role;\n")
            with self.assertRaisesRegex(rehearsal.RehearsalRefusal, "unsafe owner"):
                rehearsal.extract_owner_roles(dump)

    def test_generated_resources_reject_production_or_staging_names(self):
        for value in (
            "trading_bot_db",
            "trading_bot_new",
            "staging_db_123",
            "production_db_123",
            "short",
        ):
            with self.subTest(value=value), self.assertRaises(
                rehearsal.RehearsalRefusal
            ):
                rehearsal._deny_runtime_identifier(value)
        resources = rehearsal.allocate_resources("tbmr-run")
        self.assertTrue(resources.network_name.startswith("tbmr_net_"))

    def test_committed_source_export_excludes_worktree_secrets_and_cleans_exact_run(self):
        commit = subprocess_output("git", "rev-parse", "HEAD")
        tree = subprocess_output("git", "rev-parse", "HEAD^{tree}")
        source = rehearsal.SourceBinding(commit, tree, rehearsal.source_alembic_head())
        run_id = "tbmr-" + "a" * 32
        with tempfile.TemporaryDirectory() as tmp:
            work_root = Path(tmp) / "work"
            with patch.object(rehearsal, "DEFAULT_WORK_ROOT", work_root):
                committed = rehearsal.export_committed_migration_source(
                    source, run_id=run_id
                )
                self.assertTrue((committed.path / "alembic.ini").is_file())
                self.assertTrue(
                    (committed.path / "scripts" / "run_guarded_scratch_alembic.py").is_file()
                )
                self.assertFalse((committed.path / ".env").exists())
                self.assertRegex(committed.archive_sha256, r"^[0-9a-f]{64}$")
                self.assertTrue(
                    rehearsal.cleanup_committed_source(committed, run_id=run_id)
                )
                self.assertFalse((work_root / run_id).exists())

    def test_committed_source_export_failure_cleans_its_owned_run_directory(self):
        source = rehearsal.SourceBinding("a" * 40, "b" * 40, "fd3e4f5a6b7c")
        run_id = "tbmr-" + "f" * 32
        with tempfile.TemporaryDirectory() as tmp:
            work_root = Path(tmp) / "work"
            with patch.object(
                rehearsal, "DEFAULT_WORK_ROOT", work_root
            ), patch.object(
                rehearsal,
                "run_command",
                return_value=rehearsal.CommandResult(1, "", "archive failed"),
            ), self.assertRaises(rehearsal.RehearsalCommandError):
                rehearsal.export_committed_migration_source(source, run_id=run_id)
            self.assertFalse((work_root / run_id).exists())

    def test_postgres_is_internal_anonymous_volume_and_has_no_published_port(self):
        resources = rehearsal.DockerResources(
            run_id="tbmr-run",
            network_name="tbmr_net_1234567890ab",
            container_names=[],
            volume_names=[],
        )
        seen: list[tuple[str, ...]] = []

        def fake_docker(*args: str, timeout: int = 0):
            del timeout
            seen.append(args)
            if args[0] == "run":
                return rehearsal.CommandResult(0, "container-id\n", "")
            if args[0] == "inspect":
                name = args[1]
                return rehearsal.CommandResult(
                    0,
                    json.dumps(
                        [
                            {
                                "Config": {"Labels": {rehearsal.RESOURCE_LABEL: "tbmr-run"}},
                                "HostConfig": {"PortBindings": None},
                                "NetworkSettings": {
                                    "Networks": {resources.network_name: {}}
                                },
                                "Mounts": [
                                    {
                                        "Type": "volume",
                                        "Name": "a" * 64,
                                        "Destination": "/var/lib/postgresql/data",
                                    }
                                ],
                                "Name": name,
                            }
                        ]
                    ),
                    "",
                )
            if args[0] == "exec" and "pg_isready" in args:
                return rehearsal.CommandResult(0, "ready", "")
            if args[0] == "exec" and "SHOW server_version_num;" in args:
                return rehearsal.CommandResult(0, "150014\n", "")
            raise AssertionError(args)

        with patch.object(rehearsal, "_docker", side_effect=fake_docker), patch.object(
            rehearsal, "_random_container_name", return_value="tbmr_pg_foreign_1234567890ab"
        ):
            name = rehearsal.start_postgres(
                resources,
                role="foreign",
                username="scratch_user",
                password="secret",
                database="coin_intelligence_prod_rehearsal_abc123",
            )
        self.assertEqual(name, "tbmr_pg_foreign_1234567890ab")
        run_args = next(args for args in seen if args[0] == "run")
        self.assertIn("--mount", run_args)
        self.assertIn("type=volume,destination=/var/lib/postgresql/data", run_args)
        self.assertNotIn("--publish", run_args)
        self.assertNotIn("-p", run_args)
        self.assertEqual(resources.volume_names, ["a" * 64])

    def test_guarded_alembic_uses_read_only_source_and_exact_commands(self):
        resources = rehearsal.DockerResources(
            run_id="tbmr-run",
            network_name="tbmr_net_1234567890ab",
            container_names=[],
            volume_names=[],
        )
        source = rehearsal.SourceBinding("a" * 40, "b" * 40, "fd3e4f5a6b7c")
        image_id = "sha256:" + "c" * 64
        seen: list[tuple[str, ...]] = []

        def fake_docker(*args: str, timeout: int = 0):
            del timeout
            seen.append(args)
            if args[:2] == ("image", "inspect"):
                return rehearsal.CommandResult(
                    0,
                    json.dumps(
                        [
                            {
                                "Id": image_id,
                                "Config": {
                                    "Labels": {
                                        "org.opencontainers.image.revision": source.commit,
                                        "io.gold-trade.release.tree": source.tree,
                                        "io.gold-trade.release.input-signature": "d" * 64,
                                    }
                                },
                            }
                        ]
                    ),
                    "",
                )
            if args[:1] == ("run",):
                return rehearsal.CommandResult(0, "fd3e4f5a6b7c (head)\n", "")
            raise AssertionError(args)

        prebuild = rehearsal.RunnerPrebuildBinding(
            receipt_path=Path("/secure/foreign-image-prebuild-receipt.json"),
            receipt_sha256="e" * 64,
            image_id=image_id,
            release_sha=source.commit,
            release_tree=source.tree,
            input_signature="d" * 64,
        )
        with patch.object(
            rehearsal, "_random_container_name", return_value="tbmr_migrate_1234567890ab"
        ), patch.object(
            rehearsal,
            "_docker",
            side_effect=fake_docker,
        ), patch.object(
            rehearsal,
            "verify_runner_prebuild_receipt",
            return_value=prebuild,
        ):
            output = rehearsal.run_guarded_alembic(
                resources,
                pg_container="tbmr_pg_foreign_1234567890ab",
                username="scratch_user",
                password="secret",
                database="coin_intelligence_prod_rehearsal_abc123",
                source=source,
                source_root=Path("/secure/committed-source"),
                runner_prebuild=prebuild,
                arguments=["current"],
                timeout=60,
            )
        command = next(args for args in seen if args[:1] == ("run",))
        self.assertEqual(output.strip(), "fd3e4f5a6b7c (head)")
        self.assertIn("--read-only", command)
        self.assertIn("--cap-drop", command)
        self.assertIn("ALL", command)
        self.assertIn("--security-opt", command)
        self.assertIn("no-new-privileges", command)
        self.assertIn("/secure/committed-source:/source:ro", command)
        self.assertNotIn(f"{rehearsal.REPO_ROOT}:/source:ro", command)
        self.assertIn("scripts/run_guarded_scratch_alembic.py", command)
        self.assertEqual(command[-1], "current")
        self.assertIn(image_id, command)
        self.assertNotIn("--publish", command)

    def test_runner_image_refuses_tag_id_or_oci_revision_drift(self):
        source = rehearsal.SourceBinding("a" * 40, "b" * 40, "fd3e4f5a6b7c")
        expected = "sha256:" + "c" * 64

        prebuild = rehearsal.RunnerPrebuildBinding(
            receipt_path=Path("/secure/foreign-image-prebuild-receipt.json"),
            receipt_sha256="f" * 64,
            image_id=expected,
            release_sha=source.commit,
            release_tree=source.tree,
            input_signature="1" * 64,
        )

        def response(
            image_id: str,
            revision: str | None = None,
            tree: str | None = None,
            signature: str | None = None,
        ):
            labels = {
                "org.opencontainers.image.revision": revision or "",
                "io.gold-trade.release.tree": tree or "",
                "io.gold-trade.release.input-signature": signature or "",
            }
            return rehearsal.CommandResult(
                0,
                json.dumps([{"Id": image_id, "Config": {"Labels": labels}}]),
                "",
            )

        with patch.object(
            rehearsal, "verify_runner_prebuild_receipt", return_value=prebuild
        ):
            with patch.object(
                rehearsal, "_docker", return_value=response("sha256:" + "d" * 64)
            ), self.assertRaisesRegex(rehearsal.RehearsalRefusal, "immutable ID"):
                rehearsal.verify_runner_image(prebuild, source=source)
            with patch.object(
                rehearsal,
                "_docker",
                return_value=response(
                    expected, "e" * 40, source.tree, prebuild.input_signature
                ),
            ), self.assertRaisesRegex(rehearsal.RehearsalRefusal, "OCI identity"):
                rehearsal.verify_runner_image(prebuild, source=source)
            for labels in (
                ("", source.tree, prebuild.input_signature),
                (source.commit, "", prebuild.input_signature),
                (source.commit, source.tree, ""),
            ):
                with self.subTest(missing_labels=labels), patch.object(
                    rehearsal,
                    "_docker",
                    return_value=response(expected, *labels),
                ), self.assertRaisesRegex(rehearsal.RehearsalRefusal, "OCI identity"):
                    rehearsal.verify_runner_image(prebuild, source=source)
            with patch.object(
                rehearsal,
                "_docker",
                return_value=response(
                    expected, source.commit, source.tree, prebuild.input_signature
                ),
            ):
                binding = rehearsal.verify_runner_image(prebuild, source=source)
        self.assertEqual(binding.image_id, expected)
        self.assertEqual(binding.oci_revision, source.commit)

    def test_rehearsal_runs_current_upgrade_current_upgrade_and_proves_noop(self):
        artifact = rehearsal.DumpArtifact(
            role="foreign",
            path=Path("/secure/foreign.sql.gz"),
            sha256="1" * 64,
            size_bytes=100,
            release_sha="2" * 40,
            database_identity_sha256="3" * 64,
            target_binding_sha256="4" * 64,
        )
        source = rehearsal.SourceBinding("5" * 40, "6" * 40, "fd3e4f5a6b7c")
        resources = rehearsal.DockerResources(
            run_id="tbmr-run",
            network_name="tbmr_net_1234567890ab",
            container_names=[],
            volume_names=[],
        )
        pre_tables = ("users", "commodities", "commodity_aliases", "offers", "trades")
        post_tables = tuple(sorted((*pre_tables, *rehearsal.EXPECTED_NEW_TABLES)))
        seed_counts = {table: 1 for table in rehearsal.CRITICAL_PRESERVED_TABLES}
        new_table_counts = {table: 0 for table in rehearsal.EXPECTED_NEW_TABLES}
        new_table_counts["telegram_delivery_feeder_states"] = 1
        guarded_outputs = [
            rehearsal.EXPECTED_PRE_MIGRATION_HEAD,
            "",
            source.alembic_head,
            "",
        ]
        with ExitStack() as stack:
            stack.enter_context(patch.object(rehearsal, "start_postgres", return_value="pg"))
            stack.enter_context(patch.object(rehearsal, "extract_owner_roles", return_value=()))
            stack.enter_context(patch.object(rehearsal, "create_owner_roles"))
            stack.enter_context(patch.object(rehearsal, "restore_plain_dump"))
            stack.enter_context(
                patch.object(
                    rehearsal,
                    "_query_public_tables",
                    side_effect=[pre_tables, post_tables, post_tables],
                )
            )
            stack.enter_context(
                patch.object(
                    rehearsal,
                    "_current_revision",
                    side_effect=[
                        rehearsal.EXPECTED_PRE_MIGRATION_HEAD,
                        source.alembic_head,
                        source.alembic_head,
                    ],
                )
            )
            stack.enter_context(
                patch.object(rehearsal, "_invalid_index_count", side_effect=[0, 0, 0])
            )
            stack.enter_context(
                patch.object(
                    rehearsal,
                    "_concurrent_index_state",
                    side_effect=["", "valid-ready", "valid-ready"],
                )
            )
            stack.enter_context(
                patch.object(
                    rehearsal,
                    "_query_preexisting_table_counts",
                    side_effect=[seed_counts, seed_counts, seed_counts],
                )
            )
            stack.enter_context(
                patch.object(
                    rehearsal,
                    "_query_new_table_counts",
                    side_effect=[new_table_counts, new_table_counts],
                )
            )
            stack.enter_context(
                patch.object(
                    rehearsal,
                    "schema_only_sha256",
                    side_effect=["7" * 64, "8" * 64, "8" * 64],
                )
            )
            guarded = stack.enter_context(
                patch.object(
                    rehearsal, "run_guarded_alembic", side_effect=guarded_outputs
                )
            )
            result = rehearsal.rehearse_artifact(
                artifact,
                source=source,
                source_root=Path("/secure/committed-source"),
                runner_prebuild=rehearsal.RunnerPrebuildBinding(
                    receipt_path=Path("/secure/foreign-image-prebuild-receipt.json"),
                    receipt_sha256="8" * 64,
                    image_id="sha256:" + "9" * 64,
                    release_sha=source.commit,
                    release_tree=source.tree,
                    input_signature="7" * 64,
                ),
                resources=resources,
                timeout=60,
            )
        self.assertEqual(result["public_table_delta"], 14)
        self.assertEqual(result["migration_mode"], rehearsal.HISTORICAL_UPGRADE_MODE)
        self.assertFalse(result["first_upgrade_noop"])
        self.assertTrue(result["second_upgrade_noop"])
        self.assertEqual(
            [item.kwargs["arguments"] for item in guarded.call_args_list],
            [["current"], ["upgrade", "head"], ["current"], ["upgrade", "head"]],
        )

    def test_rehearsal_accepts_already_at_head_only_when_both_upgrades_are_noops(self):
        source_head = "ff5a6b7c8d9e"
        artifact = rehearsal.DumpArtifact(
            role="foreign",
            path=Path("/secure/foreign.sql.gz"),
            sha256="1" * 64,
            size_bytes=100,
            release_sha="2" * 40,
            database_identity_sha256="3" * 64,
            target_binding_sha256="4" * 64,
            pre_revision=source_head,
        )
        source = rehearsal.SourceBinding("5" * 40, "6" * 40, source_head)
        resources = rehearsal.DockerResources(
            run_id="tbmr-run",
            network_name="tbmr_net_1234567890ab",
            container_names=[],
            volume_names=[],
        )
        tables = tuple(
            sorted(
                set(
                    (
                        *rehearsal.CRITICAL_PRESERVED_TABLES,
                        *rehearsal.EXPECTED_NEW_TABLES,
                    )
                )
            )
        )
        all_counts = {table: index + 1 for index, table in enumerate(tables)}
        target_counts = {
            table: all_counts[table] for table in rehearsal.EXPECTED_NEW_TABLES
        }
        guarded_outputs = [source_head, "", source_head, ""]
        with ExitStack() as stack:
            stack.enter_context(patch.object(rehearsal, "start_postgres", return_value="pg"))
            stack.enter_context(patch.object(rehearsal, "extract_owner_roles", return_value=()))
            stack.enter_context(patch.object(rehearsal, "create_owner_roles"))
            stack.enter_context(patch.object(rehearsal, "restore_plain_dump"))
            stack.enter_context(
                patch.object(
                    rehearsal,
                    "_query_public_tables",
                    side_effect=[tables, tables, tables],
                )
            )
            stack.enter_context(
                patch.object(
                    rehearsal,
                    "_current_revision",
                    side_effect=[source_head, source_head, source_head],
                )
            )
            stack.enter_context(
                patch.object(rehearsal, "_invalid_index_count", side_effect=[0, 0, 0])
            )
            stack.enter_context(
                patch.object(
                    rehearsal,
                    "_concurrent_index_state",
                    side_effect=["valid-ready", "valid-ready", "valid-ready"],
                )
            )
            stack.enter_context(
                patch.object(
                    rehearsal,
                    "_query_preexisting_table_counts",
                    side_effect=[all_counts, all_counts, all_counts],
                )
            )
            target_query = stack.enter_context(
                patch.object(
                    rehearsal,
                    "_query_new_table_counts",
                    side_effect=[target_counts, target_counts, target_counts],
                )
            )
            stack.enter_context(
                patch.object(
                    rehearsal,
                    "schema_only_sha256",
                    side_effect=["7" * 64, "7" * 64, "7" * 64],
                )
            )
            guarded = stack.enter_context(
                patch.object(
                    rehearsal, "run_guarded_alembic", side_effect=guarded_outputs
                )
            )
            result = rehearsal.rehearse_artifact(
                artifact,
                source=source,
                source_root=Path("/secure/committed-source"),
                runner_prebuild=rehearsal.RunnerPrebuildBinding(
                    receipt_path=Path("/secure/foreign-image-prebuild-receipt.json"),
                    receipt_sha256="8" * 64,
                    image_id="sha256:" + "9" * 64,
                    release_sha=source.commit,
                    release_tree=source.tree,
                    input_signature="7" * 64,
                ),
                resources=resources,
                timeout=60,
            )
        self.assertEqual(result["migration_mode"], rehearsal.ALREADY_AT_HEAD_MODE)
        self.assertEqual(result["public_table_delta"], 0)
        self.assertEqual(result["added_tables"], [])
        self.assertTrue(result["first_upgrade_noop"])
        self.assertTrue(result["second_upgrade_noop"])
        self.assertIsNone(result["new_table_seed_contract"])
        self.assertEqual(
            [item.kwargs["arguments"] for item in guarded.call_args_list],
            [["current"], ["upgrade", "head"], ["current"], ["upgrade", "head"]],
        )
        self.assertTrue(
            all(
                call.kwargs["require_initial_seed_contract"] is False
                for call in target_query.call_args_list
            )
        )

    def test_migration_contract_refuses_any_third_schema_path(self):
        contract = rehearsal.migration_contract("ff5a6b7c8d9e", "ff5a6b7c8d9e")
        self.assertEqual(contract.mode, rehearsal.ALREADY_AT_HEAD_MODE)
        self.assertEqual(contract.expected_public_table_delta, 0)
        self.assertTrue(contract.require_first_upgrade_noop)
        with self.assertRaisesRegex(
            rehearsal.RehearsalCommandError, "neither the historical"
        ):
            rehearsal.migration_contract("abc123def456", "ff5a6b7c8d9e")

    def test_migration_contract_accepts_current_production_incremental_path(self):
        contract = rehearsal.migration_contract(
            rehearsal.CURRENT_PRODUCTION_PRE_MIGRATION_HEAD,
            "ff6c7d8e9f01",
        )
        self.assertEqual(contract.mode, rehearsal.INCREMENTAL_UPGRADE_MODE)
        self.assertEqual(contract.expected_public_table_delta, 0)
        self.assertEqual(contract.expected_added_tables, ())
        self.assertFalse(contract.require_initial_seed_contract)
        self.assertFalse(contract.require_first_upgrade_noop)

    def test_schema_digest_ignores_pg_dump_random_restrict_keys(self):
        template = (
            "--\n-- PostgreSQL database dump\n--\n"
            "\\restrict {key}\n\n"
            "-- Dumped from database version {database_version}\n"
            "-- Dumped by pg_dump version {dump_version}\n\n"
            "CREATE TABLE public.example (id integer);\n\n"
            "\\unrestrict {key}\n"
        )
        outputs = [
            template.format(
                key="a" * 64,
                database_version="15.18",
                dump_version="15.18",
            ),
            template.format(
                key="B7c9" * 16,
                database_version="15.19",
                dump_version="15.19",
            ),
        ]

        digests = []
        for output in outputs:
            with patch.object(
                rehearsal,
                "_docker",
                return_value=rehearsal.CommandResult(0, output, ""),
            ):
                digests.append(
                    rehearsal.schema_only_sha256("pg", "scratch_user", "scratch_db")
                )

        self.assertEqual(digests[0], digests[1])

    def test_new_table_seed_contract_is_one_feeder_and_zero_elsewhere(self):
        def good_count(_container, *, username, database, sql, tuples_only=True):
            del username, database, tuples_only
            return "1" if '"telegram_delivery_feeder_states"' in sql else "0"

        with patch.object(
            rehearsal,
            "_query_public_tables",
            return_value=tuple(rehearsal.EXPECTED_NEW_TABLES),
        ), patch.object(rehearsal, "psql", side_effect=good_count):
            counts = rehearsal._query_new_table_counts("pg", "user", "db")
        self.assertEqual(counts["telegram_delivery_feeder_states"], 1)
        self.assertTrue(
            all(
                value == 0
                for table, value in counts.items()
                if table != "telegram_delivery_feeder_states"
            )
        )

        with patch.object(
            rehearsal,
            "_query_public_tables",
            return_value=tuple(rehearsal.EXPECTED_NEW_TABLES),
        ), patch.object(rehearsal, "psql", return_value="0"), self.assertRaisesRegex(
            rehearsal.RehearsalCommandError, "seed contract"
        ):
            rehearsal._query_new_table_counts("pg", "user", "db")

    def test_all_preexisting_table_counts_include_critical_data_tables(self):
        tables = tuple((*rehearsal.CRITICAL_PRESERVED_TABLES, "audit_extra"))

        def count(_container, *, username, database, sql, tuples_only=True):
            del username, database, tuples_only
            if '"users"' in sql or '"commodities"' in sql:
                return "2"
            return "0"

        with patch.object(rehearsal, "psql", side_effect=count):
            counts = rehearsal._query_preexisting_table_counts(
                "pg", "user", "db", tables
            )
        self.assertEqual(set(counts), set(tables))
        self.assertEqual(counts["users"], 2)
        self.assertEqual(counts["commodities"], 2)

    def test_cleanup_deletes_only_label_owned_resources(self):
        resources = rehearsal.DockerResources(
            run_id="tbmr-run",
            network_name="tbmr_net_1234567890ab",
            container_names=["tbmr_pg_foreign_1234567890ab"],
            volume_names=[],
        )
        calls: list[tuple[str, ...]] = []

        def wrong_owner(*args: str, timeout: int = 0):
            del timeout
            calls.append(args)
            if args[:1] == ("inspect",):
                return rehearsal.CommandResult(
                    0, json.dumps([{"Config": {"Labels": {rehearsal.RESOURCE_LABEL: "other"}}}]), ""
                )
            if args[:2] == ("network", "inspect"):
                return rehearsal.CommandResult(
                    1,
                    "",
                    f"Error response from daemon: network {resources.network_name} not found",
                )
            if args[:1] == ("ps",) or args[:2] in {
                ("network", "ls"),
                ("volume", "ls"),
            }:
                return rehearsal.CommandResult(0, "", "")
            raise AssertionError(args)

        with patch.object(rehearsal, "_docker", side_effect=wrong_owner):
            failures = rehearsal.cleanup_owned_resources(resources)
        self.assertIn("container-ownership-unproven", failures)
        self.assertFalse(any(args[:1] == ("rm",) for args in calls))

    def test_cleanup_treats_daemon_errors_as_failures_and_checks_label_residue(self):
        resources = rehearsal.DockerResources(
            run_id="tbmr-run",
            network_name="tbmr_net_1234567890ab",
            container_names=[],
            volume_names=[],
        )

        def daemon_error(*_args: str, timeout: int = 0):
            del timeout
            return rehearsal.CommandResult(
                1, "", "Cannot connect to the Docker daemon"
            )

        with patch.object(rehearsal, "_docker", side_effect=daemon_error):
            failures = rehearsal.cleanup_owned_resources(resources)
        self.assertIn("network-inspection-failed", failures)
        self.assertIn("container-label-enumeration-failed", failures)
        self.assertIn("volume-label-enumeration-failed", failures)

        calls: list[tuple[str, ...]] = []

        def residue(*args: str, timeout: int = 0):
            del timeout
            calls.append(args)
            if args[:2] == ("network", "inspect"):
                return rehearsal.CommandResult(
                    1,
                    "",
                    f"Error response from daemon: network {resources.network_name} not found",
                )
            if args[:1] == ("ps",):
                return rehearsal.CommandResult(0, "leftover-container\n", "")
            return rehearsal.CommandResult(0, "", "")

        with patch.object(rehearsal, "_docker", side_effect=residue):
            failures = rehearsal.cleanup_owned_resources(resources)
        self.assertIn("container-label-residue-detected", failures)

        disguised = rehearsal.CommandResult(
            1,
            "",
            (
                f"Error response from daemon: network {resources.network_name} "
                "not found; Docker daemon unavailable"
            ),
        )
        self.assertFalse(
            rehearsal._docker_not_found(
                disguised, kind="network", name=resources.network_name
            )
        )

    def test_cleanup_never_uses_anonymous_volume_name_as_deletion_authority(self):
        volume = "a" * 64
        resources = rehearsal.DockerResources(
            run_id="tbmr-run",
            network_name="tbmr_net_1234567890ab",
            container_names=[],
            volume_names=[volume],
        )
        calls: list[tuple[str, ...]] = []

        def fake_docker(*args: str, timeout: int = 0):
            del timeout
            calls.append(args)
            if args[:3] == ("volume", "inspect", volume):
                return rehearsal.CommandResult(0, json.dumps([{"Name": volume}]), "")
            if args[:2] == ("network", "inspect"):
                return rehearsal.CommandResult(
                    1,
                    "",
                    f"Error response from daemon: network {resources.network_name} not found",
                )
            if args[:1] == ("ps",) or args[:2] in {
                ("network", "ls"),
                ("volume", "ls"),
            }:
                return rehearsal.CommandResult(0, "", "")
            raise AssertionError(args)

        with patch.object(rehearsal, "_docker", side_effect=fake_docker):
            failures = rehearsal.cleanup_owned_resources(resources)
        self.assertIn("anonymous-volume-residue-detected", failures)
        self.assertNotIn(("volume", "rm", volume), calls)

    def test_receipt_write_is_exclusive_private_and_fixed_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp) / "secure"
            parent.mkdir(mode=0o700)
            root = parent / "evidence"
            receipt = root / "production-migration-rehearsal-test.json"
            with patch.object(rehearsal, "DEFAULT_RECEIPT_ROOT", root):
                rehearsal.write_receipt(receipt, {"status": "passed"})
                self.assertEqual(stat.S_IMODE(receipt.stat().st_mode), 0o600)
                with self.assertRaises(rehearsal.RehearsalRefusal):
                    rehearsal.write_receipt(receipt, {"status": "overwrite"})
                with self.assertRaises(rehearsal.RehearsalRefusal):
                    rehearsal.write_receipt(root / "bad-name.json", {"status": "passed"})

    def test_command_environment_drops_compose_and_database_pollution(self):
        process = unittest.mock.Mock()
        process.pid = 99_999_992
        process.communicate.return_value = ("", "")
        process.returncode = 0
        with patch.dict(
            os.environ,
            {
                "COMPOSE_PROJECT_NAME": "staging",
                "DATABASE_URL": "postgresql://production.invalid/db",
                "SYNC_DATABASE_URL": "postgresql://production.invalid/db",
            },
            clear=False,
        ), patch.object(rehearsal.subprocess, "Popen", return_value=process) as popen:
            rehearsal.run_command(["true"], timeout=1)
        child_env = popen.call_args.kwargs["env"]
        self.assertNotIn("COMPOSE_PROJECT_NAME", child_env)
        self.assertNotIn("DATABASE_URL", child_env)
        self.assertNotIn("SYNC_DATABASE_URL", child_env)

    def test_command_timeout_terminates_descendant_group(self):
        with tempfile.TemporaryDirectory() as tmp:
            pid_file = Path(tmp) / "child.pid"
            code = (
                "import pathlib,subprocess,sys,time;"
                "p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)']);"
                "pathlib.Path(sys.argv[1]).write_text(str(p.pid));time.sleep(60)"
            )
            result = rehearsal.run_command(
                ["python3", "-c", code, str(pid_file)], timeout=0.2
            )
            self.assertEqual(result.returncode, 124)
            child_pid = int(pid_file.read_text(encoding="utf-8"))
            for _ in range(30):
                try:
                    os.kill(child_pid, 0)
                except ProcessLookupError:
                    break
                time.sleep(0.05)
            else:
                self.fail("timed out command left a descendant process running")

    def test_timeout_kills_child_holding_pipes_after_group_leader_exits(self):
        with tempfile.TemporaryDirectory() as tmp:
            pid_file = Path(tmp) / "orphan.pid"
            code = (
                "import pathlib,subprocess,sys;"
                "p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)']);"
                "pathlib.Path(sys.argv[1]).write_text(str(p.pid))"
            )
            result = rehearsal.run_command(
                ["python3", "-c", code, str(pid_file)], timeout=0.2
            )
            self.assertEqual(result.returncode, 124)
            child_pid = int(pid_file.read_text(encoding="utf-8"))
            for _ in range(30):
                try:
                    os.kill(child_pid, 0)
                except ProcessLookupError:
                    break
                time.sleep(0.05)
            else:
                self.fail("leader-exited process group left a pipe-holding child")

    def test_timeout_kills_term_ignoring_descendant_with_closed_pipes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            child_identity = root / "closed-pipe-child.pid"
            child_code = (
                "import os,pathlib,signal,sys,time;"
                "signal.signal(signal.SIGTERM,signal.SIG_IGN);"
                "pathlib.Path(sys.argv[1]).write_text(f'{os.getpid()}:{os.getpgrp()}');"
                "time.sleep(60)"
            )
            leader_code = (
                "import subprocess,sys,time;"
                "subprocess.Popen([sys.executable,'-c',sys.argv[1],sys.argv[2]],"
                "stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL);"
                "time.sleep(60)"
            )
            original_cleanup = rehearsal._stop_process_group

            def fast_cleanup(process, *, process_group_id=None):
                return original_cleanup(
                    process,
                    process_group_id=process_group_id,
                    grace_seconds=0.1,
                    kill_seconds=1.0,
                )

            child_pid = None
            try:
                original_communicate = subprocess.Popen.communicate
                first_communicate = True

                def communicate_after_child_ready(process, *args, **kwargs):
                    nonlocal first_communicate
                    if first_communicate:
                        first_communicate = False
                        ready_deadline = time.monotonic() + 5.0
                        while (
                            not child_identity.exists()
                            and time.monotonic() < ready_deadline
                        ):
                            time.sleep(0.01)
                        self.assertTrue(
                            child_identity.exists(),
                            "term-ignoring descendant did not become ready",
                        )
                        raise subprocess.TimeoutExpired(
                            process.args, kwargs.get("timeout", args[0] if args else 5.0)
                        )
                    return original_communicate(process, *args, **kwargs)

                with patch.object(
                    rehearsal, "_stop_process_group", side_effect=fast_cleanup
                ), patch.object(
                    subprocess.Popen,
                    "communicate",
                    new=communicate_after_child_ready,
                ):
                    result = rehearsal.run_command(
                        [
                            sys.executable,
                            "-c",
                            leader_code,
                            child_code,
                            str(child_identity),
                        ],
                        timeout=5.0,
                    )
                self.assertEqual(result.returncode, 124)
                child_pid, child_group = map(
                    int, child_identity.read_text(encoding="utf-8").split(":")
                )
                self.assertFalse(rehearsal._process_group_has_live_members(child_group))
            finally:
                if child_pid is None and child_identity.exists():
                    child_pid = int(
                        child_identity.read_text(encoding="utf-8").split(":", 1)[0]
                    )
                if child_pid is not None:
                    try:
                        os.kill(child_pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass

    def test_normal_return_fails_closed_on_detached_descendant(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            child_identity = root / "normal-return-child.pid"
            escaped_marker = root / "normal-return-child.escaped"
            child_code = "\n".join(
                (
                    "import os, pathlib, signal, sys, time",
                    "signal.signal(signal.SIGTERM, signal.SIG_IGN)",
                    "pathlib.Path(sys.argv[1]).write_text(f'{os.getpid()}:{os.getpgrp()}')",
                    "time.sleep(0.75)",
                    "pathlib.Path(sys.argv[2]).write_text('escaped')",
                )
            )
            leader_code = "\n".join(
                (
                    "import pathlib, subprocess, sys, time",
                    "subprocess.Popen([sys.executable, '-c', sys.argv[1], sys.argv[2], sys.argv[3]], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)",
                    "ready = pathlib.Path(sys.argv[2])",
                    "deadline = time.monotonic() + 5",
                    "while not ready.exists() and time.monotonic() < deadline: time.sleep(0.01)",
                    "raise SystemExit(0 if ready.exists() else 91)",
                )
            )
            child_pid = None
            try:
                result = rehearsal.run_command(
                    [
                        sys.executable,
                        "-c",
                        leader_code,
                        child_code,
                        str(child_identity),
                        str(escaped_marker),
                    ],
                    timeout=3,
                )
                self.assertEqual(result.returncode, 125)
                child_pid, child_group = map(
                    int, child_identity.read_text(encoding="utf-8").split(":")
                )
                self.assertFalse(rehearsal._process_group_has_live_members(child_group))
                time.sleep(0.85)
                self.assertFalse(escaped_marker.exists())
            finally:
                if child_pid is None and child_identity.exists():
                    child_pid = int(
                        child_identity.read_text(encoding="utf-8").split(":", 1)[0]
                    )
                if child_pid is not None:
                    try:
                        os.kill(child_pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass

    def test_process_group_shutdown_never_uses_unbounded_communicate(self):
        process = unittest.mock.Mock()
        process.pid = 424242
        process.stdout = unittest.mock.Mock()
        process.stderr = unittest.mock.Mock()
        process.communicate.side_effect = [
            subprocess.TimeoutExpired(["test"], 0.01),
            subprocess.TimeoutExpired(["test"], 0.01, output="partial", stderr="err"),
        ]
        with patch.object(rehearsal.os, "killpg") as killpg, self.assertRaisesRegex(
            rehearsal.RehearsalCommandError, "bounded cleanup"
        ):
            rehearsal._stop_process_group(process, grace_seconds=0.01)
        signal_calls = [
            call
            for call in killpg.call_args_list
            if call.args[1] in {signal.SIGTERM, signal.SIGKILL}
        ]
        self.assertEqual(
            signal_calls,
            [
                unittest.mock.call(424242, signal.SIGTERM),
                unittest.mock.call(424242, signal.SIGKILL),
            ],
        )
        self.assertTrue(any(call.args[1] == 0 for call in killpg.call_args_list))
        self.assertEqual(
            [call.kwargs for call in process.communicate.call_args_list],
            [{"timeout": 0.01}, {"timeout": 0.01}],
        )
        process.wait.assert_not_called()

    def test_plan_mode_never_allocates_docker_resources(self):
        source = rehearsal.SourceBinding("a" * 40, "b" * 40, "fd3e4f5a6b7c")
        backup = rehearsal.VerifiedBackup(
            receipt_sha256="c" * 64,
            created_at="2026-08-21T00:00:00Z",
            production_release_sha="d" * 40,
            artifacts=(),
            artifact_set_sha256="f" * 64,
        )
        prebuild = rehearsal.RunnerPrebuildBinding(
            receipt_path=Path("/secure/foreign-image-prebuild-receipt.json"),
            receipt_sha256="1" * 64,
            image_id="sha256:" + "e" * 64,
            release_sha=source.commit,
            release_tree=source.tree,
            input_signature="2" * 64,
        )
        manifest = Path("/secure/online.env")
        with patch.object(rehearsal, "verify_source_checkout", return_value=source), patch.object(
            rehearsal.Path, "is_absolute", return_value=True
        ), patch.object(rehearsal.Path, "is_symlink", return_value=False), patch.object(
            rehearsal.Path, "resolve", return_value=manifest
        ), patch.object(
            rehearsal, "production_backup_manifest_values", return_value={}
        ), patch.object(
            rehearsal, "verify_backup_receipt", return_value=backup
        ), patch.object(
            rehearsal, "verify_runner_prebuild_receipt", return_value=prebuild
        ), patch.object(
            rehearsal, "allocate_resources"
        ) as allocate, redirect_stdout(StringIO()):
            result = rehearsal.main(
                [
                    "--manifest",
                    str(manifest),
                    "--backup-receipt",
                    "/secure/backup.json",
                    "--backup-receipt-sha256",
                    "c" * 64,
                    "--expected-production-release-sha",
                    "d" * 40,
                    "--migration-runner-image-id",
                    "sha256:" + "e" * 64,
                    "--migration-runner-prebuild-receipt",
                    str(rehearsal.DEFAULT_RUNNER_PREBUILD_RECEIPT),
                    "--migration-runner-prebuild-receipt-sha256",
                    "1" * 64,
                ]
            )
        self.assertEqual(result, 0)
        allocate.assert_not_called()


if __name__ == "__main__":
    unittest.main()
