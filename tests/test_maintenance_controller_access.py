"""Hermetic source contracts; never run Ansible, SSH or native-host probes."""

import ast
import base64
import datetime
import json
import os
from pathlib import Path
import re
import stat
import struct
import subprocess
import tempfile
import types
import unittest
from unittest.mock import patch

import yaml


ROOT = Path(__file__).resolve().parents[1]
PLAY = yaml.safe_load((ROOT / "deploy/maintenance/controller_access.yml").read_text())[0]
INVENTORY = yaml.safe_load((ROOT / "deploy/maintenance/controller_inventory.example.yml").read_text())
SOURCE = PLAY["vars"]["controller_validator"]
TASKS = PLAY["tasks"]
COMMENT = "security-controller-20261023-fixture"
KEY = "ssh-ed25519 " + base64.b64encode(struct.pack(">I", 11) + b"ssh-ed25519" + struct.pack(">I", 32) + bytes(range(32))).decode()


def task(name):
    return next(value for value in TASKS if value["name"] == name)


class ControllerPlaybookContracts(unittest.TestCase):
    def test_only_two_exact_hosts_native_bot_controls_delegation(self):
        hosts = INVENTORY["all"]["hosts"]
        self.assertEqual(set(hosts), {"controller_bot", "controller_web"})
        self.assertEqual(PLAY["hosts"], "controller_bot")
        self.assertFalse(PLAY["gather_facts"])
        self.assertEqual((hosts["controller_bot"]["ansible_connection"], hosts["controller_bot"]["ansible_host"], hosts["controller_bot"]["ansible_port"]), ("local", "65.109.216.187", 22))
        self.assertEqual((hosts["controller_web"]["ansible_connection"], hosts["controller_web"]["ansible_host"], hosts["controller_web"]["ansible_port"]), ("ssh", "65.109.220.59", 37067))
        guards = TASKS[0]["ansible.builtin.assert"]["that"]
        self.assertIn("groups['all'] | sort == ['controller_bot', 'controller_web']", guards)
        self.assertIn("ansible_play_hosts_all == ['controller_bot']", guards)
        for host in hosts:
            for key in ("ansible_connection", "ansible_host", "ansible_port", "ansible_user"):
                self.assertTrue(any(f"hostvars.{host}.{key}" in value for value in guards))

    def test_default_and_check_mode_stop_before_any_managed_write(self):
        self.assertEqual(PLAY["vars"]["controller_access_action"], "check")
        end = task("Default and Ansible check modes make no managed changes")
        self.assertEqual(end["when"], "controller_access_action == 'check' or ansible_check_mode")
        self.assertEqual(end["ansible.builtin.meta"], "end_play")
        for value in TASKS[:TASKS.index(end)]:
            self.assertTrue(set(value) & {"ansible.builtin.command", "ansible.builtin.assert", "ansible.builtin.stat"})
            if "ansible.builtin.command" in value:
                self.assertEqual(value["ansible.builtin.command"]["argv"][:4], ["/usr/bin/python3", "-I", "-B", "-c"])
                self.assertIs(value["changed_when"], False)

    def test_persistent_native_identity_is_explicit_and_metadata_only(self):
        hosts = INVENTORY["all"]["hosts"]
        identity = "/root/.ssh/id_ed25519_iran"
        self.assertEqual(hosts["controller_web"]["ansible_ssh_private_key_file"], identity)
        guards = TASKS[0]["ansible.builtin.assert"]["that"]
        self.assertIn(f"hostvars.controller_web.ansible_ssh_private_key_file == '{identity}'", guards)
        inspection = task("Inspect persistent native to Web identity metadata without opening its bytes")
        self.assertEqual(inspection["ansible.builtin.stat"], {
            "path": identity, "follow": False, "get_checksum": False, "get_mime": False,
        })
        checks = task("Require persistent owned identity for operation and postboot cleanup")["ansible.builtin.assert"]["that"]
        for condition in ("controller_native_identity.stat.isreg", "not controller_native_identity.stat.islnk",
                          "controller_native_identity.stat.uid == 0", "controller_native_identity.stat.nlink == 1",
                          "controller_native_identity.stat.mode == '0600'"):
            self.assertIn(condition, checks)
        self.assertLess(TASKS.index(inspection), TASKS.index(task("Read only Web native safety and owned workspace metadata")))

    def test_explicit_fixed_window_and_native_host_guards(self):
        guards = TASKS[0]["ansible.builtin.assert"]["that"]
        self.assertIn("controller_access_action == 'check' or controller_access_confirmation == 'TEMPORARY-ROOT-SSH-20261024T013000Z'", guards)
        for value in ("os.geteuid() == 0", "'/proc/1/exe'", "'systemd'", "'/proc/self/ns/'", "'/.dockerenv'", "'/usr/sbin/ip'", "'65.109.216.187' if role == 'bot' else '65.109.220.59'", "datetime.datetime(2026, 10, 24, 1, 30"):
            self.assertIn(value, SOURCE)
        self.assertNotIn("402653", SOURCE)  # Namespace inode magic is not host proof.
        compile(SOURCE, "<read-only-controller-validator>", "exec")

    def test_validator_has_no_write_or_private_read_operations(self):
        tree = ast.parse(SOURCE)
        forbidden = {"write", "write_text", "write_bytes", "mkdir", "unlink", "remove", "chmod", "chown", "rename", "replace", "rmdir", "slurp"}
        calls = [value for value in ast.walk(tree) if isinstance(value, ast.Call)]
        self.assertFalse(any(isinstance(value.func, ast.Attribute) and value.func.attr in forbidden for value in calls))
        reads = [value for value in calls if isinstance(value.func, ast.Name) and value.func.id == "public"]
        self.assertEqual(len(reads), 4)
        self.assertFalse(any(ast.unparse(value.args[0]).endswith("'id_ed25519'") for value in reads))
        self.assertIn("os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK", SOURCE)

    def test_private_key_generation_and_metadata_never_leave_web(self):
        generation = task("Generate private key only on Web without returning its bytes")
        self.assertEqual(generation["delegate_to"], "controller_web")
        self.assertTrue(generation["no_log"])
        self.assertEqual(generation["ansible.builtin.command"]["argv"][:6], ["/usr/bin/ssh-keygen", "-q", "-t", "ed25519", "-N", ""])
        inspection = task("Inspect private key metadata without checksums MIME detection or following links")
        self.assertEqual(inspection["delegate_to"], "controller_web")
        self.assertEqual(inspection["ansible.builtin.stat"], {"path": "{{ controller_key }}", "follow": False, "get_checksum": False, "get_mime": False})
        self.assertFalse(any("ansible.builtin.slurp" in value or "ansible.builtin.fetch" in value for value in TASKS))

    def test_only_exact_key_line_can_be_appended_or_revoked(self):
        line = task("Append or remove only the exact owned timed public key preserving other lines")["ansible.builtin.lineinfile"]
        self.assertEqual(line["path"], "/root/.ssh/authorized_keys")
        self.assertEqual(line["regexp"], "^{{ controller_managed_line | regex_escape }}$")
        for flag in ("create", "backrefs", "unsafe_writes"):
            self.assertIs(line[flag], False)
        for metadata in ("owner", "group", "mode"):
            self.assertIn("controller_authorized_metadata.stat.", line[metadata])
        managed = task("Derive exactly one options-prefixed managed public key")["ansible.builtin.set_fact"]["controller_managed_line"]
        self.assertIn('restrict,from="65.109.220.59",expiry-time="20261024013000Z" ', managed)
        self.assertNotIn("command=", managed)  # Restrict is not a command sandbox.
        index = TASKS.index(task("Append or remove only the exact owned timed public key preserving other lines"))
        self.assertIn("Refuse unsafe authorized keys metadata", TASKS[index - 1]["name"])

    def test_public_host_pin_comes_from_trusted_native_file_not_tofu(self):
        self.assertIn("public('/etc/ssh/ssh_host_ed25519_key.pub')", SOURCE)
        self.assertNotIn("ssh-keyscan", yaml.safe_dump(PLAY))
        pin = task("Install only trusted public Bot host pin in scoped Web file")["ansible.builtin.copy"]
        self.assertFalse(pin["force"])
        self.assertEqual(pin["mode"], "0600")
        probe = task("Prove source restriction and strict public host pin via Web to Bot round trip")
        args = probe["ansible.builtin.command"]["argv"]
        for flag in ("/dev/null", "BatchMode=yes", "IdentitiesOnly=yes", "ForwardAgent=no", "RequestTTY=no", "StrictHostKeyChecking=yes", "GlobalKnownHostsFile=/dev/null", "HostKeyAlgorithms=ssh-ed25519", "root@65.109.216.187"):
            self.assertIn(flag, args)
        self.assertEqual(args[-1], "/usr/bin/true")
        self.assertEqual(probe["timeout"], 20)

    def test_cleanup_revokes_first_keeps_public_ownership_until_last_and_never_recurses(self):
        revocation = task("Append or remove only the exact owned timed public key preserving other lines")
        files = task("Remove only individually validated owned workspace files after key revocation")
        directory = task("Remove only the empty owned workspace never recursively")
        self.assertLess(TASKS.index(revocation), TASKS.index(files))
        self.assertLess(TASKS.index(files), TASKS.index(directory))
        self.assertEqual(files["loop"], ["id_ed25519", "known_hosts", "id_ed25519.pub"])
        self.assertIn("item in", files["when"])
        self.assertEqual(directory["ansible.builtin.command"]["argv"], ["/usr/bin/rmdir", "{{ controller_directory }}"])
        self.assertFalse(any("ansible.builtin.shell" in value for value in TASKS))


class PublicValidatorFixtures(unittest.TestCase):
    def setUp(self):
        # Execute ONLY standalone read-only functions on synthetic temp files,
        # never the script's native-host preamble or real /root paths.
        functions = [node for node in ast.parse(SOURCE).body if isinstance(node, ast.FunctionDef)]
        namespace = dict(base64=base64, os=os, stat=stat, struct=struct)
        exec(compile(ast.Module(body=functions, type_ignores=[]), "<validator-fixture-functions>", "exec"), namespace)
        self.functions = namespace
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        original = os.lstat

        def owned_stat(path):
            info = original(path)
            # Simulate production root ownership even on nonroot GitHub runners.
            return types.SimpleNamespace(**{name: (0 if name == "st_uid" else getattr(info, name)) for name in ("st_uid", "st_mode", "st_nlink", "st_size", "st_dev", "st_ino")})

        self.owner = patch("os.lstat", side_effect=owned_stat)
        self.owner.start()
        self.addCleanup(self.owner.stop)

    def test_valid_ed25519_public_key_and_comment(self):
        self.assertEqual(self.functions["ed25519"](KEY + " " + COMMENT + "\n", COMMENT), KEY)

    def test_public_key_rejects_options_extra_lines_wrong_comment_and_wire_lengths(self):
        for value in ("restrict " + KEY + " " + COMMENT, KEY + " wrong", KEY + " " + COMMENT + "\n" + KEY, "ssh-ed25519 " + base64.b64encode(bytes(32)).decode() + " " + COMMENT):
            with self.subTest(value=value), self.assertRaises((SystemExit, ValueError)):
                self.functions["ed25519"](value, COMMENT)

    def test_regular_public_file_is_bounded_and_read_only(self):
        path = self.root / "fixture.pub"
        path.write_text(KEY + " " + COMMENT + "\n")
        path.chmod(0o600)
        before = path.stat()
        self.assertEqual(self.functions["public"](path), path.read_text())
        self.assertEqual(path.stat().st_ino, before.st_ino)
        with self.assertRaises(SystemExit):
            self.functions["public"](path, 8)

    def test_symlink_hardlink_fifo_and_writable_file_fail_before_read(self):
        original = self.root / "fixture.pub"
        original.write_text(KEY)
        original.chmod(0o600)
        link = self.root / "symlink"
        link.symlink_to(original)
        with self.assertRaises(SystemExit):
            self.functions["public"](link)
        hard = self.root / "hardlink"
        os.link(original, hard)
        with self.assertRaises(SystemExit):
            self.functions["public"](hard)
        fifo = self.root / "fifo"
        os.mkfifo(fifo, 0o600)
        with self.assertRaises(SystemExit):
            self.functions["public"](fifo)
        hard.unlink()
        original.chmod(0o622)
        with self.assertRaises(SystemExit):
            self.functions["public"](original)

    def test_nonroot_fixture_metadata_does_not_require_sudo(self):
        path = self.root / "nonroot.pub"
        path.write_text(KEY)
        path.chmod(0o600)
        self.assertEqual(self.functions["public"](path), KEY)

    def test_root_home_0755_and_private_ssh_directory_are_allowed(self):
        home = self.root / "home"
        home.mkdir(mode=0o755)
        home.chmod(0o755)
        ssh = home / ".ssh"
        ssh.mkdir(mode=0o700)
        self.functions["ssh_parent"](home)
        self.functions["ssh_parent"](ssh, private=True)
        self.assertIn("ssh_parent('/root')", SOURCE)
        self.assertIn("ssh_parent('/root/.ssh', private=True)", SOURCE)

    def test_ssh_directory_0755_and_writable_home_are_refused(self):
        directory = self.root / "parent"
        directory.mkdir(mode=0o755)
        directory.chmod(0o755)
        with self.assertRaisesRegex(SystemExit, "private SSH parent"):
            self.functions["ssh_parent"](directory, private=True)
        directory.chmod(0o775)
        with self.assertRaisesRegex(SystemExit, "unsafe writable permissions"):
            self.functions["ssh_parent"](directory)

    def test_parent_symlink_and_nonroot_owner_remain_refused(self):
        directory = self.root / "parent"
        directory.mkdir(mode=0o700)
        link = self.root / "linked-parent"
        link.symlink_to(directory)
        for private in (False, True):
            with self.subTest(private=private), self.assertRaisesRegex(SystemExit, "unsafe owner or symlink"):
                self.functions["ssh_parent"](link, private=private)
        unsafe = types.SimpleNamespace(st_uid=1001, st_mode=stat.S_IFDIR | 0o700)
        with patch("os.lstat", return_value=unsafe):
            for private in (False, True):
                with self.subTest(private=private), self.assertRaisesRegex(SystemExit, "unsafe owner or symlink"):
                    self.functions["ssh_parent"](directory, private=private)


if __name__ == "__main__":
    unittest.main()
