import hashlib
import json
from pathlib import Path
import runpy
import unittest


ROOT = Path(__file__).resolve().parents[1]
MODULE = runpy.run_path(str(ROOT / "scripts/verify_writer_witness_host_toolchain.py"))


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
                "perl",
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
            }.issubset(tools)
        )

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


if __name__ == "__main__":
    unittest.main()
