import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
STATE_HELPER = (
    ROOT
    / "deploy/writer-witness/writer-witness-matrix-host-fault-state.py"
)
HOST_FAULT_HELPER = (
    ROOT / "deploy/writer-witness/writer-witness-matrix-host-faults.sh"
)
TAG = "wwm_0123456789ab"
FORBIDDEN_HELPER_ENVIRONMENT = (
    "PYTHONPATH",
    "PYTHONHOME",
    "PYTHONSTARTUP",
    "PYTHONINSPECT",
    "PYTHONUSERBASE",
    "LD_PRELOAD",
    "LD_LIBRARY_PATH",
)


class WriterWitnessHostFaultRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="wwm-host-fault-")
        self.base = Path(self.temporary.name)
        self.state_root = self.base / "state"
        self.runtime_root = self.base / "run"
        self.runtime_root.mkdir(mode=0o700)
        self.processes: list[subprocess.Popen] = []

    def tearDown(self):
        for process in self.processes:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)
        self.temporary.cleanup()

    def run_state(self, command: str, *arguments: str, check: bool = True):
        helper_environment = os.environ.copy()
        for name in FORBIDDEN_HELPER_ENVIRONMENT:
            helper_environment.pop(name, None)
        completed = subprocess.run(
            [
                sys.executable,
                "-I",
                "-S",
                "-B",
                "-X",
                "utf8",
                "-X",
                "pycache_prefix=/dev/null",
                str(STATE_HELPER),
                command,
                "--tag",
                TAG,
                *arguments,
                "--test-mode",
                "--state-root",
                str(self.state_root),
                "--runtime-root",
                str(self.runtime_root),
                "--workload-uid",
                str(os.geteuid()),
            ],
            capture_output=True,
            text=True,
            env=helper_environment,
        )
        if check and completed.returncode != 0:
            self.fail(completed.stderr)
        return completed

    def sleeping_process(self, *, cwd: Path | None = None) -> subprocess.Popen:
        process = subprocess.Popen(["sleep", "300"], cwd=cwd)
        self.processes.append(process)
        return process

    def hard_kill(self, process: subprocess.Popen) -> None:
        os.kill(process.pid, signal.SIGKILL)
        process.wait(timeout=5)

    def test_recover_after_helper_is_killed_immediately_after_durable_claim(self):
        helper = self.sleeping_process()
        self.run_state(
            "claim",
            "--kind",
            "disk",
            "--helper-pid",
            str(helper.pid),
            "--no-mount",
        )
        self.hard_kill(helper)

        self.run_state("recover")

        self.assertFalse((self.state_root / f"{TAG}-disk").exists())
        self.assertFalse((self.runtime_root / f"{TAG}-disk").exists())

    def test_recover_claim_crash_before_and_after_staged_metadata_publish(self):
        for failpoint, expected_status in (
            ("after_staging_mkdir", 97),
            ("after_staging_metadata", 98),
        ):
            with self.subTest(failpoint=failpoint):
                helper = self.sleeping_process()
                completed = self.run_state(
                    "claim",
                    "--kind",
                    "disk",
                    "--helper-pid",
                    str(helper.pid),
                    "--no-mount",
                    "--test-failpoint",
                    failpoint,
                    check=False,
                )
                self.assertEqual(completed.returncode, expected_status, completed.stderr)
                self.hard_kill(helper)
                self.assertFalse((self.state_root / f"{TAG}-disk").exists())
                self.assertEqual(
                    len(list(self.state_root.glob(f".{TAG}-disk.claim.*.tmp"))),
                    1,
                )

                self.run_state("recover")

                self.assertFalse((self.state_root / f"{TAG}-disk").exists())
                self.assertEqual(
                    list(self.state_root.glob(f".{TAG}-disk.claim.*.tmp")), []
                )

    def test_recover_discovers_and_kills_unrecorded_process_holding_tagged_root(self):
        helper = self.sleeping_process()
        self.run_state(
            "claim",
            "--kind",
            "disk",
            "--helper-pid",
            str(helper.pid),
            "--no-mount",
        )
        tagged_root = self.runtime_root / f"{TAG}-disk"
        tagged_root.mkdir(mode=0o700)
        os.chmod(tagged_root, 0o700)
        self.run_state(
            "update",
            "--kind",
            "disk",
            "--helper-pid",
            str(helper.pid),
            "--phase",
            "mounted",
        )
        leftover = self.sleeping_process(cwd=tagged_root)
        self.hard_kill(helper)

        self.run_state("recover")

        leftover.wait(timeout=5)
        self.assertIsNotNone(leftover.returncode)
        self.assertFalse(tagged_root.exists())
        self.assertFalse((self.state_root / f"{TAG}-disk").exists())

    def test_recover_validates_recorded_pid_and_start_time_before_termination(self):
        helper = self.sleeping_process()
        self.run_state(
            "claim",
            "--kind",
            "disk",
            "--helper-pid",
            str(helper.pid),
            "--no-mount",
        )
        tagged_root = self.runtime_root / f"{TAG}-disk"
        tagged_root.mkdir(mode=0o700)
        os.chmod(tagged_root, 0o700)
        postgres = self.sleeping_process(cwd=tagged_root)
        self.run_state(
            "update",
            "--kind",
            "disk",
            "--helper-pid",
            str(helper.pid),
            "--phase",
            "postgres_started",
            "--postgres-pid",
            str(postgres.pid),
        )
        metadata = json.loads(
            (self.state_root / f"{TAG}-disk" / "metadata.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(metadata["postgres_pid"], postgres.pid)
        self.assertGreater(metadata["postgres_start_ticks"], 0)
        self.hard_kill(helper)

        self.run_state("recover")

        postgres.wait(timeout=5)
        self.assertFalse(tagged_root.exists())

    def test_recovery_refuses_tampered_ownership_metadata(self):
        helper = self.sleeping_process()
        self.run_state(
            "claim",
            "--kind",
            "disk",
            "--helper-pid",
            str(helper.pid),
            "--no-mount",
        )
        metadata_path = self.state_root / f"{TAG}-disk" / "metadata.json"
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        metadata["root"] = str(self.runtime_root / "foreign-root")
        metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
        os.chmod(metadata_path, 0o600)
        self.hard_kill(helper)

        completed = self.run_state("recover", check=False)

        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("ownership identity mismatch", completed.stderr)
        self.assertTrue(metadata_path.exists())

    def test_shell_and_controller_delegate_cleanup_to_durable_recovery(self):
        helper = HOST_FAULT_HELPER.read_text(encoding="utf-8")
        controller = (
            ROOT / "scripts/run_writer_witness_real_host_matrix.py"
        ).read_text(encoding="utf-8")
        preflight = (
            ROOT / "scripts/plan_writer_witness_real_host_matrix.py"
        ).read_text(encoding="utf-8")
        self.assertIn("writer-witness-matrix-host-fault-state", helper)
        self.assertIn("state_helper claim", helper)
        self.assertIn("state_helper update", helper)
        self.assertIn("state_helper recover", helper)
        self.assertIn("writer-witness-matrix-host-faults recover --tag", controller)
        self.assertNotIn('set +e; for root in /run/{self.tag}-disk', controller)
        self.assertIn("matrix-host-faults", preflight)
        self.assertIn("(^|:)(55439|55440)$", preflight)

    def test_clock_fault_uses_real_database_clock_path_inside_isolated_postgres(self):
        helper = HOST_FAULT_HELPER.read_text(encoding="utf-8")
        self.assertIn("run_writer_witness_clock_jump_probe.py", helper)
        self.assertIn("--phase phase-one", helper)
        self.assertIn("--phase phase-two", helper)
        self.assertIn('listen_addresses=\'\'', helper)
        self.assertIn("libfaketime_loaded", helper)
        self.assertIn("production_processes_never_loaded_libfaketime", helper)
        self.assertNotIn("127.0.0.1:$port/postgres", helper)


if __name__ == "__main__":
    unittest.main()
