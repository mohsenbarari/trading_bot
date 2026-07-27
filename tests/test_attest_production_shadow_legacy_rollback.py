from __future__ import annotations

from contextlib import redirect_stdout
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from scripts import attest_production_shadow_legacy_rollback as MODULE


OPERATION_ID = "123e4567-e89b-42d3-a456-426614174000"
RELEASE_SHA = "a" * 40
LEGACY_RELEASE_SHA = "b" * 40
STAMP = "20260727T171618Z"


def canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


class RollbackFixture:
    def __init__(self, root: Path, role: str = "bot_fi") -> None:
        self.root = root
        self.role = role
        self.rollback_root = root / "rollbacks"
        self.backup_root = root / "backups"
        self.directory = (
            self.rollback_root
            / LEGACY_RELEASE_SHA
            / role.replace("_", "-")
        )
        self.directory.mkdir(parents=True, mode=0o700)
        self.backup_root.mkdir(mode=0o700)
        self.prefix = MODULE.ROLE_PREFIXES[role]
        self.manifest_name = f"{self.prefix}-backup-{STAMP}.json"
        self.backup_rows: list[dict] = []
        for index, kind in enumerate(MODULE.BACKUP_KINDS, 1):
            name = (
                f"{self.prefix}-{kind}-{STAMP}"
                f"{MODULE.BACKUP_SUFFIXES[kind]}"
            )
            payload = (f"{kind}-{index}" * 3).encode("ascii")
            path = self.backup_root / name
            path.write_bytes(payload)
            path.chmod(0o644)
            self.backup_rows.append(
                {
                    "bytes": len(payload),
                    "kind": kind,
                    "path": str(path),
                    "sha256": hashlib.sha256(payload).hexdigest(),
                }
            )
        self.manifest = {
            "backup_dir": str(self.backup_root),
            "compose_file": MODULE.ROLE_COMPOSE[role],
            "created_at": "2026-07-27T17:17:02.414470Z",
            "files": self.backup_rows,
            "hostname": "host",
            "notes": ["sealed rollback"],
            "restore_smoke": {
                "error": None,
                "status": "passed",
                "table_count": 46,
            },
            "role": self.prefix,
            "stamp": STAMP,
            "status": "ok",
        }
        files = {
            name: name.encode("ascii")
            for name in MODULE.ROLE_SEALED_FILES[role]
        }
        files["release-sha.txt"] = f"{LEGACY_RELEASE_SHA}\n".encode()
        files["image-id.txt"] = f"sha256:{'c' * 64}\n".encode()
        files[self.manifest_name] = canonical_bytes(self.manifest)
        self.digests: dict[str, str] = {}
        for name, payload in files.items():
            path = self.directory / name
            path.write_bytes(payload)
            path.chmod(0o600)
            self.digests[name] = hashlib.sha256(payload).hexdigest()
        sums = "".join(
            f"{self.digests[name]}  {name}\n"
            for name in sorted(self.digests)
        ).encode("ascii")
        (self.directory / "SHA256SUMS").write_bytes(sums)
        (self.directory / "SHA256SUMS").chmod(0o600)

    def patches(self):
        return (
            mock.patch.dict(
                MODULE.ROLLBACK_ROOTS,
                {self.role: self.rollback_root},
            ),
            mock.patch.object(MODULE, "BACKUP_ROOT", self.backup_root),
        )


class LegacyRollbackAttestationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.root.chmod(0o700)
        self.fixture = RollbackFixture(self.root)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def inspect(self, fixture: RollbackFixture | None = None) -> dict:
        selected = fixture or self.fixture
        first, second = selected.patches()
        with first, second:
            return MODULE.inspect_rollback(
                operation_id=OPERATION_ID,
                release_sha=RELEASE_SHA,
                legacy_release_sha=LEGACY_RELEASE_SHA,
                role=selected.role,
            )

    def run_main(
        self,
        arguments: list[str],
        fixture: RollbackFixture | None = None,
    ) -> tuple[int, dict]:
        selected = fixture or self.fixture
        output = io.StringIO()
        first, second = selected.patches()
        with first, second, redirect_stdout(output):
            status = MODULE.main(arguments)
        return status, json.loads(output.getvalue())

    def arguments(self, *, role: str = "bot_fi") -> list[str]:
        return [
            "--operation-id",
            OPERATION_ID,
            "--release-sha",
            RELEASE_SHA,
            "--legacy-release-sha",
            LEGACY_RELEASE_SHA,
            "--role",
            role,
            "--output-directory",
            str(self.root),
        ]

    def test_inspection_binds_complete_sealed_and_backup_closure(self):
        result = self.inspect()
        self.assertEqual(result["status"], "verified")
        self.assertEqual(result["role"], "bot_fi")
        self.assertEqual(
            result["legacy_redis_rollback_sha256"],
            next(
                row["sha256"]
                for row in self.fixture.backup_rows
                if row["kind"] == "redis"
            ),
        )
        self.assertEqual(
            result["sealed_file_count"],
            len(MODULE.ROLE_SEALED_FILES["bot_fi"]) + 1,
        )
        self.assertEqual(result["backup_artifact_count"], 4)
        self.assertTrue(result["database_restore_smoke_passed"])
        self.assertFalse(result["source_mutated"])

    def test_webapp_role_has_distinct_exact_closure(self):
        other_root = self.root / "webapp"
        other_root.mkdir(mode=0o700)
        fixture = RollbackFixture(other_root, role="webapp_fi")
        result = self.inspect(fixture)
        self.assertEqual(result["role"], "webapp_fi")
        self.assertEqual(
            result["sealed_file_count"],
            len(MODULE.ROLE_SEALED_FILES["webapp_fi"]) + 1,
        )
        self.assertNotEqual(
            result["rollback_closure_sha256"],
            self.inspect()["rollback_closure_sha256"],
        )

    def test_default_plan_and_create_only_exact_retry(self):
        status, plan = self.run_main(self.arguments())
        self.assertEqual(status, 0)
        self.assertEqual(plan["status"], "planned")
        self.assertFalse(plan["output_mutated"])
        self.assertFalse(plan["network_io"])

        apply = self.arguments() + [
            "--apply",
            "--confirm",
            plan["required_confirmation"],
        ]
        status, result = self.run_main(apply)
        self.assertEqual(status, 0)
        self.assertEqual(result["publication"], "created")
        output = Path(result["output"])
        self.assertEqual(output.stat().st_mode & 0o777, 0o600)
        published = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(published["status"], "verified")

        status, retry = self.run_main(apply)
        self.assertEqual(status, 0)
        self.assertEqual(retry["publication"], "reused")

    def test_tampered_sealed_file_and_unlisted_file_fail_closed(self):
        target = self.fixture.directory / "source.tar.gz"
        target.write_bytes(b"tampered")
        target.chmod(0o600)
        with self.assertRaisesRegex(
            MODULE.LegacyRollbackAttestationError,
            "digest differs",
        ):
            self.inspect()

        self.fixture = RollbackFixture(self.root / "fresh")
        extra = self.fixture.directory / "unexpected"
        extra.write_bytes(b"x")
        extra.chmod(0o600)
        with self.assertRaisesRegex(
            MODULE.LegacyRollbackAttestationError,
            "closure is not exact",
        ):
            self.inspect()

    def test_backup_artifact_tamper_or_restore_failure_blocks(self):
        redis = next(
            row for row in self.fixture.backup_rows if row["kind"] == "redis"
        )
        path = Path(redis["path"])
        path.write_bytes(b"changed")
        path.chmod(0o644)
        with self.assertRaisesRegex(
            MODULE.LegacyRollbackAttestationError,
            "digest differs|size differs",
        ):
            self.inspect()

        self.fixture = RollbackFixture(self.root / "fresh")
        self.fixture.manifest["restore_smoke"]["status"] = "failed"
        raw = canonical_bytes(self.fixture.manifest)
        manifest = self.fixture.directory / self.fixture.manifest_name
        manifest.write_bytes(raw)
        manifest.chmod(0o600)
        digest = hashlib.sha256(raw).hexdigest()
        lines = (self.fixture.directory / "SHA256SUMS").read_text().splitlines()
        rewritten = [
            f"{digest}  {self.fixture.manifest_name}"
            if line.endswith(f"  {self.fixture.manifest_name}")
            else line
            for line in lines
        ]
        (self.fixture.directory / "SHA256SUMS").write_text(
            "\n".join(rewritten) + "\n"
        )
        (self.fixture.directory / "SHA256SUMS").chmod(0o600)
        with self.assertRaisesRegex(
            MODULE.LegacyRollbackAttestationError,
            "restore smoke",
        ):
            self.inspect()

    def test_symlink_hardlink_mode_and_bad_sums_are_rejected(self):
        source = self.fixture.directory / "source.tar.gz"
        original = source.read_bytes()
        source.unlink()
        outside = self.root / "outside"
        outside.write_bytes(original)
        outside.chmod(0o600)
        source.symlink_to(outside)
        with self.assertRaisesRegex(
            MODULE.LegacyRollbackAttestationError,
            "unavailable",
        ):
            self.inspect()

        self.fixture = RollbackFixture(self.root / "hardlink")
        source = self.fixture.directory / "source.tar.gz"
        os.link(source, self.root / "second-link")
        with self.assertRaisesRegex(
            MODULE.LegacyRollbackAttestationError,
            "unsafe",
        ):
            self.inspect()

        self.fixture = RollbackFixture(self.root / "mode")
        source = self.fixture.directory / "source.tar.gz"
        source.chmod(0o644)
        with self.assertRaisesRegex(
            MODULE.LegacyRollbackAttestationError,
            "unsafe",
        ):
            self.inspect()

        self.fixture = RollbackFixture(self.root / "sums")
        sums = self.fixture.directory / "SHA256SUMS"
        sums.write_bytes(sums.read_bytes() + b"invalid\n")
        sums.chmod(0o600)
        with self.assertRaisesRegex(
            MODULE.LegacyRollbackAttestationError,
            "record is invalid",
        ):
            self.inspect()

    def test_wrong_confirmation_and_conflicting_output_fail_closed(self):
        status, result = self.run_main(
            self.arguments() + ["--apply", "--confirm", "wrong"]
        )
        self.assertEqual(status, 1)
        self.assertIn("apply requires", result["error"])
        status, result = self.run_main(
            self.arguments() + ["--confirm", "wrong"]
        )
        self.assertEqual(status, 1)
        self.assertIn("valid only", result["error"])

        output = self.root / "legacy-rollback-bot-fi.json"
        output.write_bytes(b"{}")
        output.chmod(0o600)
        status, plan = self.run_main(self.arguments())
        self.assertEqual(status, 0)
        status, result = self.run_main(
            self.arguments()
            + ["--apply", "--confirm", plan["required_confirmation"]]
        )
        self.assertEqual(status, 1)
        self.assertIn("overwrite", result["error"])
        self.assertEqual(output.read_bytes(), b"{}")

    def test_operation_release_path_and_output_mode_are_exact(self):
        with self.assertRaisesRegex(
            MODULE.LegacyRollbackAttestationError,
            "UUIDv4",
        ):
            MODULE.inspect_rollback(
                operation_id="not-a-uuid",
                release_sha=RELEASE_SHA,
                legacy_release_sha=LEGACY_RELEASE_SHA,
                role="bot_fi",
            )
        self.root.chmod(0o755)
        status, result = self.run_main(self.arguments())
        self.assertEqual(status, 1)
        self.assertIn("mode", result["error"])

    def test_unexpected_failure_is_redacted(self):
        with mock.patch.object(
            MODULE,
            "inspect_rollback",
            side_effect=RuntimeError("secret"),
        ):
            status, result = self.run_main(self.arguments())
        self.assertEqual(status, 1)
        self.assertNotIn("secret", json.dumps(result))

    def test_release_cli_entrypoints_import_from_outside_repository(self):
        for name in (
            "attest_production_shadow_legacy_rollback.py",
            "build_production_shadow_source_snapshot_binding.py",
        ):
            result = subprocess.run(
                [
                    sys.executable,
                    str(MODULE.REPO_ROOT / "scripts" / name),
                    "--help",
                ],
                cwd="/",
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=10,
                check=False,
            )
            self.assertEqual(
                result.returncode,
                0,
                result.stderr.decode("utf-8", errors="replace"),
            )


if __name__ == "__main__":
    unittest.main()
