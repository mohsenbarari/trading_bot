import hashlib
import json
import os
from pathlib import Path
import runpy
import subprocess
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
MODULE = runpy.run_path(
    str(ROOT / "scripts/verify_writer_witness_controller_toolchain.py")
)
RUNTIME = runpy.run_path(str(ROOT / "scripts/writer_witness_controller_runtime.py"))


class WriterWitnessControllerToolchainTests(unittest.TestCase):
    def test_controller_inventory_is_canonical_and_separate(self):
        inventory = {
            "native_objects": [],
            "packages": [],
            "schema_version": "writer_witness_matrix_controller_toolchain_v1",
            "tools": [{"name": "git", "sha256": "a" * 64}],
        }
        payload = MODULE["canonical_bytes"](inventory)
        self.assertTrue(payload.endswith(b"\n"))
        self.assertEqual(json.loads(payload), inventory)
        self.assertEqual(
            hashlib.sha256(payload).hexdigest(),
            hashlib.sha256(MODULE["canonical_bytes"](inventory)).hexdigest(),
        )

    def test_controller_closes_every_review_critical_local_executable(self):
        tools = MODULE["CONTROLLER_TOOL_PATHS"]
        self.assertTrue(
            {
                "bash",
                "docker",
                "findmnt",
                "git",
                "python3.12",
                "scp",
                "ssh",
                "ssh-keygen",
                "systemctl",
                "systemd-run",
            }.issubset(tools)
        )
        self.assertTrue(all(path.is_absolute() for path in tools.values()))

    def test_hostile_path_cannot_change_controller_command_resolution(self):
        with mock.patch.dict(os.environ, {"PATH": "/tmp/hostile"}, clear=False):
            self.assertEqual(RUNTIME["executable"]("git"), "/usr/bin/git")
            self.assertEqual(RUNTIME["executable"]("ssh-keygen"), "/usr/bin/ssh-keygen")
            self.assertEqual(
                RUNTIME["clean_environment"]()["PATH"],
                "/usr/sbin:/usr/bin:/sbin:/bin",
            )

    def test_dynamic_controller_command_rejects_ambient_or_unknown_executable(self):
        for arguments in (["ssh", "host", "true"], ["/tmp/ssh", "host", "true"]):
            with self.subTest(arguments=arguments), self.assertRaisesRegex(
                RUNTIME["ControllerRuntimeError"], "absolute inventoried"
            ):
                RUNTIME["assert_command"](arguments)
        RUNTIME["assert_command"](["/usr/bin/ssh", "host", "true"])

    def test_runtime_refuses_before_toolchain_work_without_transaction_identity(self):
        with mock.patch.dict(
            os.environ,
            {
                "HOME": "/root",
                "LANG": "C.UTF-8",
                "LC_ALL": "C.UTF-8",
                "LOGNAME": "root",
                "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
                "USER": "root",
            },
            clear=True,
        ), self.assertRaisesRegex(
            RUNTIME["ControllerRuntimeError"], "transaction identity"
        ):
            with mock.patch.object(
                RUNTIME["sys"],
                "executable",
                "/usr/bin/python3.12",
            ), mock.patch.object(
                RUNTIME["sys"],
                "flags",
                mock.Mock(isolated=1, no_site=1, dont_write_bytecode=1, utf8_mode=1),
            ):
                RUNTIME["assert_runtime"]("a" * 64)

    def test_runtime_accepts_real_systemctl_property_order_independently(self):
        inventory = {
            "native_objects": [],
            "packages": [],
            "schema_version": "writer_witness_matrix_controller_toolchain_v1",
            "tools": [],
        }
        digest = hashlib.sha256(MODULE["canonical_bytes"](inventory)).hexdigest()
        environment = {
            "HOME": "/root",
            "INVOCATION_ID": "test-invocation",
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "LOGNAME": "root",
            "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
            "SYSTEMD_EXEC_PID": "4242",
            "USER": "root",
            "WRITER_WITNESS_CONTROLLER_TRANSACTION_UNIT": (
                "writer-witness-matrix-controller-0123456789abcdefabcd.service"
            ),
            "WRITER_WITNESS_PACKAGE_LOCK_OWNER_PID": "4242",
        }
        completed = subprocess.CompletedProcess(
            args=["/usr/bin/systemctl"],
            returncode=0,
            stdout="Type=exec\nMainPID=4242\nKillMode=control-group\n",
            stderr="",
        )
        with mock.patch.dict(os.environ, environment, clear=True), mock.patch.object(
            RUNTIME["os"], "geteuid", return_value=0
        ), mock.patch.object(
            RUNTIME["os"], "getegid", return_value=0
        ), mock.patch.object(
            RUNTIME["os"], "getpid", return_value=4242
        ), mock.patch.object(
            RUNTIME["sys"], "executable", "/usr/bin/python3.12"
        ), mock.patch.object(
            RUNTIME["sys"],
            "flags",
            mock.Mock(isolated=1, no_site=1, dont_write_bytecode=1, utf8_mode=1),
        ), mock.patch.object(
            RUNTIME["package_locks"], "assert_package_locks_owned_by"
        ), mock.patch.object(
            RUNTIME["subprocess"], "run", return_value=completed
        ), mock.patch.object(
            RUNTIME["controller_toolchain"], "build_inventory", return_value=inventory
        ):
            self.assertEqual(RUNTIME["assert_runtime"](digest), digest)


if __name__ == "__main__":
    unittest.main()
