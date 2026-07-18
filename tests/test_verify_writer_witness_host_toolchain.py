import hashlib
import json
import os
from pathlib import Path
import runpy
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
MODULE = runpy.run_path(str(ROOT / "scripts/verify_writer_witness_host_toolchain.py"))
LOCK_MODULE = runpy.run_path(str(ROOT / "scripts/hold_writer_witness_package_locks.py"))


class WriterWitnessHostToolchainTests(unittest.TestCase):
    def test_inventory_canonicalization_is_stable_and_newline_terminated(self):
        inventory = {
            "tools": [{"name": "bash", "sha256": "a" * 64}],
            "schema_version": "writer_witness_host_toolchain_v1",
            "packages": [{"package": "bash", "version": "1"}],
        }
        payload = MODULE["canonical_bytes"](inventory)
        self.assertTrue(payload.endswith(b"\n"))
        self.assertEqual(json.loads(payload), inventory)
        self.assertEqual(
            hashlib.sha256(payload).hexdigest(),
            hashlib.sha256(MODULE["canonical_bytes"](inventory)).hexdigest(),
        )

    def test_closed_tool_set_covers_privileged_shell_and_recovery_surface(self):
        tools = set(MODULE["TOOL_NAMES"])
        self.assertTrue(
            {
                "bash",
                "cat",
                "dd",
                "df",
                "python3.12",
                "systemctl",
                "journalctl",
                "ldd",
                "mount",
                "mountpoint",
                "umount",
                "dpkg-query",
                "curl",
                "openssl",
                "psql",
                "pg_dump",
                "pg_config",
                "pg_ctl",
                "initdb",
                "postgres",
                "pgrep",
                "python3",
                "nginx",
                "nft",
                "ufw",
                "age",
                "ssh",
                "sshd",
                "scp",
                "flock",
                "find",
                "grep",
                "sed",
                "awk",
                "basename",
                "dirname",
                "ln",
                "sync",
                "timedatectl",
                "timeout",
                "wc",
            }.issubset(tools)
        )

    def test_complete_command_surface_is_mechanically_equal_to_inventory(self):
        result = MODULE["verify_command_surface"](ROOT)
        self.assertEqual(result["command_surface_attested"], "yes")
        self.assertEqual(
            {entry["command"] for entry in result["entries"]},
            set(MODULE["TOOL_NAMES"]),
        )
        self.assertEqual(result["entry_count"], len(MODULE["TOOL_NAMES"]))

    def test_command_surface_rejects_an_unbound_inventory_entry(self):
        function_globals = MODULE["verify_command_surface"].__globals__
        original = function_globals["TOOL_NAMES"]
        function_globals["TOOL_NAMES"] = (*original, "unreviewed-tool")
        try:
            with self.assertRaisesRegex(
                MODULE["ToolchainError"], "missing_source_binding=\\['unreviewed-tool'\\]"
            ):
                MODULE["verify_command_surface"](ROOT)
        finally:
            function_globals["TOOL_NAMES"] = original

    def test_extra_non_executable_bootstrap_packages_remain_bound(self):
        self.assertEqual(
            set(MODULE["EXTRA_PACKAGES"]),
            {"ca-certificates", "libfaketime", "python3-venv"},
        )

    def test_postgresql_wrappers_and_selected_server_binaries_are_both_closed(self):
        self.assertEqual(
            set(MODULE["POSTGRESQL_WRAPPED_BINARIES"]),
            {"createdb", "dropdb", "pg_dump", "pg_restore", "psql"},
        )
        self.assertEqual(
            set(MODULE["POSTGRESQL_SERVER_BINARIES"]),
            {"initdb", "pg_ctl", "postgres"},
        )

    def test_native_package_lock_excludes_a_second_process(self):
        with tempfile.TemporaryDirectory(prefix="writer-witness-package-lock-") as raw:
            path = Path(raw) / "dpkg.lock"
            path.touch(mode=0o600)
            descriptor = LOCK_MODULE["_open_lock"](
                path, expected_uid=os.getuid(), expected_gid=os.getgid()
            )
            read_fd, write_fd = os.pipe()
            child = os.fork()
            if child == 0:
                os.close(read_fd)
                try:
                    contender = LOCK_MODULE["_open_lock"](
                        path, expected_uid=os.getuid(), expected_gid=os.getgid()
                    )
                except BlockingIOError:
                    os.write(write_fd, b"blocked")
                else:
                    os.close(contender)
                    os.write(write_fd, b"acquired")
                finally:
                    os.close(write_fd)
                os._exit(0)
            os.close(write_fd)
            try:
                self.assertEqual(os.read(read_fd, 64), b"blocked")
                _pid, status = os.waitpid(child, 0)
                self.assertEqual(status, 0)
            finally:
                os.close(read_fd)
                os.close(descriptor)

    def test_provisioner_holds_package_locks_and_reattests_each_boundary(self):
        source = (ROOT / "scripts/provision_writer_witness_host.sh").read_text()
        self.assertIn("coproc WRITER_WITNESS_PACKAGE_LOCK_HOLDER", source)
        self.assertIn("package_manager_locks_held=yes", source)
        self.assertIn("release_package_manager_locks", source)
        self.assertGreaterEqual(source.count("attest_host_toolchain"), 5)
        for operation in ("record-unit-intent", "commit", "complete"):
            self.assertIn(
                f"installed_activation {operation}",
                source,
            )
        self.assertGreater(
            source.rindex("release_package_manager_locks"),
            source.index("installed_activation complete"),
        )


if __name__ == "__main__":
    unittest.main()
