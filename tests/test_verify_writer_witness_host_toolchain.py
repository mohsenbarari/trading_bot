import hashlib
import json
import os
from pathlib import Path
import runpy
import signal
import tempfile
import time
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
                "sshd",
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

    def test_replacement_witness_inventory_has_an_exact_source_cross_reference(self):
        result = MODULE["verify_command_surface"](ROOT)
        self.assertEqual(
            result["replacement_witness_inventory_cross_reference_attested"],
            "yes",
        )
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
            {"ca-certificates", "libfaketime"},
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

    def test_dpkg_diversion_records_are_not_treated_as_package_owners(self):
        output = "\n".join(
            (
                "diversion by postgresql-common from: /usr/bin/pg_config",
                "diversion by postgresql-common to: /usr/bin/pg_config.libpq-dev",
                "postgresql-common: /usr/bin/pg_config",
            )
        )
        self.assertEqual(
            MODULE["_parse_package_owners"](output),
            {"postgresql-common"},
        )

    def test_package_owner_parser_remains_fail_closed_for_real_ambiguity(self):
        self.assertEqual(
            MODULE["_parse_package_owners"](
                "package-one: /usr/bin/tool\npackage-two: /usr/bin/tool\n"
            ),
            {"package-one", "package-two"},
        )

    def test_package_owner_paths_include_only_the_usrmerge_alias(self):
        self.assertEqual(
            MODULE["_package_owner_paths"](Path("/usr/bin/ss")),
            (Path("/usr/bin/ss"), Path("/bin/ss")),
        )
        self.assertEqual(
            MODULE["_package_owner_paths"](Path("/usr/share/tool")),
            (Path("/usr/share/tool"),),
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

    def test_package_lock_actor_execs_as_the_same_pid_and_sigkill_releases_locks(self):
        with tempfile.TemporaryDirectory(prefix="writer-witness-package-actor-") as raw:
            path = Path(raw) / "dpkg.lock"
            path.touch(mode=0o600)
            ready = Path(raw) / "ready"
            actor = os.fork()
            if actor == 0:
                try:
                    LOCK_MODULE["exec_with_package_locks"](
                        [path],
                        [
                            "/bin/sh",
                            "-c",
                            (
                                "test \"$$\" = \"$WRITER_WITNESS_PACKAGE_LOCK_OWNER_PID\" "
                                f"&& : > {ready} && exec /bin/sleep 300"
                            ),
                        ],
                        expected_uid=os.getuid(),
                        expected_gid=os.getgid(),
                    )
                finally:
                    os._exit(71)
            try:
                for _attempt in range(100):
                    if ready.exists():
                        break
                    time.sleep(0.01)
                self.assertTrue(ready.exists(), "exec actor never became ready")
                LOCK_MODULE["assert_package_locks_owned_by"]([path], owner_pid=actor)
                with self.assertRaises(BlockingIOError):
                    LOCK_MODULE["_open_lock"](
                        path, expected_uid=os.getuid(), expected_gid=os.getgid()
                    )
                os.kill(actor, signal.SIGKILL)
                _pid, status = os.waitpid(actor, 0)
                self.assertTrue(os.WIFSIGNALED(status))
                self.assertEqual(os.WTERMSIG(status), signal.SIGKILL)
                contender = LOCK_MODULE["_open_lock"](
                    path, expected_uid=os.getuid(), expected_gid=os.getgid()
                )
                os.close(contender)
            finally:
                try:
                    os.kill(actor, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                try:
                    os.waitpid(actor, 0)
                except ChildProcessError:
                    pass

    def test_provisioner_uses_one_cgroup_main_pid_for_locks_and_mutation(self):
        source = (ROOT / "scripts/provision_writer_witness_host.sh").read_text()
        builder = (ROOT / "scripts/build_writer_witness_release.sh").read_text()
        helper = (ROOT / "scripts/hold_writer_witness_package_locks.py").read_text()
        self.assertNotIn("coproc WRITER_WITNESS_PACKAGE_LOCK_HOLDER", source)
        self.assertIn("--property=KillMode=control-group", source)
        self.assertIn("--assert-parent-locks", source)
        self.assertIn("--exec /bin/bash", source)
        self.assertIn(
            '"$SOURCE_DIR/scripts/provision_writer_witness_host.sh"',
            source,
        )
        self.assertIn(
            '"$ROOT_DIR/scripts/provision_writer_witness_host.sh"',
            builder,
        )
        self.assertIn('SYSTEMD_EXEC_PID:-}" == "$$"', source)
        self.assertIn("os.execve", helper)
        self.assertGreaterEqual(source.count("attest_host_toolchain"), 5)
        for operation in ("record-unit-intent", "commit", "complete"):
            self.assertIn(
                f"installed_activation {operation}",
                source,
            )
        self.assertLess(source.index("assert_package_lock_transaction"), source.index("attest_host_toolchain"))


if __name__ == "__main__":
    unittest.main()
