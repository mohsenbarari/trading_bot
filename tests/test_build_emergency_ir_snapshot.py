import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "build_emergency_ir_snapshot.py"
SPEC = importlib.util.spec_from_file_location("build_emergency_ir_snapshot", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
SNAPSHOT = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = SNAPSHOT
SPEC.loader.exec_module(SNAPSHOT)


def executable(path: Path) -> None:
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(0o700)


class BuildEmergencyIrSnapshotTests(unittest.TestCase):
    def test_captures_validated_custom_dump_without_source_mutation_command(self) -> None:
        with tempfile.TemporaryDirectory(prefix="emergency-snapshot-") as raw:
            root = Path(raw)
            root.chmod(0o700)
            docker = root / "docker"
            executable(docker)
            output = root / "snapshot.dump"
            calls: list[list[str]] = []

            def runner(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
                calls.append(list(command))
                if command[1:3] == ["inspect", "-f"]:
                    return subprocess.CompletedProcess(command, 0, stdout="postgres:15-alpine\n", stderr="")
                if SNAPSHOT.PG_DUMP_COMMAND in command:
                    target = kwargs["stdout"]
                    assert hasattr(target, "write")
                    target.write(b"PGDMPfake-custom-dump")
                    return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")
                if SNAPSHOT.PG_RESTORE_LIST_COMMAND in command:
                    source = kwargs["stdin"]
                    assert hasattr(source, "read")
                    self.assertEqual(source.read(5), b"PGDMP")
                    return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")
                self.fail(f"unexpected command: {command!r}")

            with patch.object(SNAPSHOT, "DOCKER_BINARY", docker):
                result = SNAPSHOT.capture_snapshot(output=output, runner=runner)

            self.assertEqual(result.output, output)
            self.assertEqual(result.bytes, len(b"PGDMPfake-custom-dump"))
            self.assertTrue(result.sha256)
            self.assertTrue(output.is_file())
            self.assertEqual(output.read_bytes(), b"PGDMPfake-custom-dump")
            self.assertTrue(any(SNAPSHOT.PG_DUMP_COMMAND in call for call in calls))
            self.assertTrue(any(SNAPSHOT.PG_RESTORE_LIST_COMMAND in call for call in calls))
            self.assertFalse(any("rm" in call or "restore" in call for call in calls))

    def test_rejects_wrong_source_image_before_pg_dump(self) -> None:
        with tempfile.TemporaryDirectory(prefix="emergency-snapshot-") as raw:
            root = Path(raw)
            root.chmod(0o700)
            docker = root / "docker"
            executable(docker)
            calls: list[list[str]] = []

            def runner(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
                calls.append(list(command))
                return subprocess.CompletedProcess(command, 0, stdout="postgres:16-alpine\n", stderr="")

            with patch.object(SNAPSHOT, "DOCKER_BINARY", docker), self.assertRaisesRegex(
                SNAPSHOT.EmergencySnapshotError, "source does not match"
            ):
                SNAPSHOT.capture_snapshot(output=root / "snapshot.dump", runner=runner)
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0][1], "inspect")

    def test_failed_dump_preserves_partial_and_never_overwrites_final(self) -> None:
        with tempfile.TemporaryDirectory(prefix="emergency-snapshot-") as raw:
            root = Path(raw)
            root.chmod(0o700)
            docker = root / "docker"
            executable(docker)
            output = root / "snapshot.dump"

            def runner(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes | str]:
                if command[1] == "inspect":
                    return subprocess.CompletedProcess(command, 0, stdout="postgres:15-alpine\n", stderr="")
                if SNAPSHOT.PG_DUMP_COMMAND in command:
                    target = kwargs["stdout"]
                    assert hasattr(target, "write")
                    target.write(b"partial")
                    return subprocess.CompletedProcess(command, 1, stdout=b"", stderr=b"failed")
                self.fail("pg_restore must not run after a failed dump")

            with patch.object(SNAPSHOT, "DOCKER_BINARY", docker), self.assertRaisesRegex(
                SNAPSHOT.EmergencySnapshotError, "pg_dump failed"
            ):
                SNAPSHOT.capture_snapshot(output=output, runner=runner)
            self.assertFalse(output.exists())
            partials = list(root.glob(".snapshot.dump.*.part"))
            self.assertEqual(len(partials), 1)
            self.assertEqual(partials[0].read_bytes(), b"partial")

    def test_cli_requires_isolated_interpreter(self) -> None:
        completed = subprocess.run(
            [sys.executable, str(MODULE_PATH), "--help"],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 2)
        self.assertIn("python3 -I -B", completed.stdout)


if __name__ == "__main__":
    unittest.main()
