"""Synthetic-only contracts for the finite native Bot boot-mount package."""

import ast
import configparser
import json
import os
from pathlib import Path
import pathlib
import re
import resource
import shlex
import stat
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import MagicMock, patch

import yaml


ROOT = Path(__file__).resolve().parents[1]
PLAY = yaml.safe_load((ROOT / 'deploy/maintenance/boot_mount_gates.yml').read_text())[0]
INVENTORY = yaml.safe_load((ROOT / 'deploy/maintenance/boot_mount_inventory.yml').read_text())
SOURCE = PLAY['vars']['boot_mount_validator']
TASKS = PLAY['tasks']
PRIMARY = '/srv/trading-bot/production-data'
SECURE = '/root/secure-envs/trading-bot'
STAGING = '/srv/trading-bot/staging-data'


def functions():
    # Execute constants and pure helpers only. Native probes/main are NEVER run.
    constants = {'PRIMARY', 'SECURE', 'STAGING', 'UUID', 'NAME', 'SYSTEMD_ROOT', 'EXPECTED'}
    nodes = [node for node in ast.parse(SOURCE).body if isinstance(node, ast.FunctionDef) or (
        isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name) and node.targets[0].id in constants)]
    namespace = dict(configparser=configparser, json=json, os=os, pathlib=pathlib, re=re, resource=resource,
                     shlex=shlex, stat=stat, subprocess=subprocess, tempfile=tempfile)
    exec(compile(ast.Module(body=nodes, type_ignores=[]), '<boot-gate-fixture-helpers>', 'exec'), namespace)
    return namespace


class BootMountPlaybookContracts(unittest.TestCase):
    def test_frozen_public_inventory_matches_validator_exact_eleven_consumers(self):
        expected = functions()['EXPECTED']
        self.assertEqual(INVENTORY['all']['vars']['boot_mount_service_paths'], expected)
        self.assertEqual(len(expected), 11)
        self.assertEqual(expected['docker.service'], [PRIMARY, SECURE, STAGING])
        self.assertEqual([key for key, paths in expected.items() if STAGING in paths], [
            'docker.service', 'coin-group-event-telegram.service', 'coin-private-shadow-legacy-estimator-bridge.service',
            'coin-intelligence-staging-snapshot-relay.service', 'market-pipeline-watchdog.service'])
        self.assertEqual(expected['coin-intelligence-staging-snapshot-relay.service'], [PRIMARY, STAGING])
        self.assertEqual(expected['market-pipeline-watchdog.service'], [STAGING, SECURE])
        self.assertFalse(any('backups' in path for paths in expected.values() for path in paths))
        self.assertFalse(any(key.endswith('.timer') or key.startswith(('golden', 'ssh', 'codex')) for key in expected))

    def test_default_readonly_explicit_action_and_single_native_host(self):
        self.assertEqual(PLAY['hosts'], 'boot_mount_bot')
        self.assertFalse(PLAY['gather_facts'])
        self.assertEqual(PLAY['vars']['boot_mount_action'], 'check')
        hosts = INVENTORY['all']['hosts']
        self.assertEqual(list(hosts), ['boot_mount_bot'])
        self.assertEqual(hosts['boot_mount_bot']['ansible_connection'], 'local')
        self.assertEqual(hosts['boot_mount_bot']['ansible_host'], '65.109.216.187')
        guard = TASKS[0]['ansible.builtin.assert']['that']
        self.assertIn("groups['all'] == ['boot_mount_bot']", guard)
        self.assertIn("boot_mount_action == 'check' or boot_mount_confirmation == 'BOT-MOUNT-GATES-20261023'", guard)
        end = next(value for value in TASKS if 'ansible.builtin.meta' in value)
        self.assertEqual(end['when'], "boot_mount_action == 'check' or ansible_check_mode")
        self.assertEqual(TASKS.index(end), 2)

    def test_no_restart_reboot_enable_disable_or_existing_config_edit(self):
        daemon = [value['ansible.builtin.systemd_service'] for value in TASKS if 'ansible.builtin.systemd_service' in value]
        self.assertEqual(daemon, [{'daemon_reload': True}])
        self.assertFalse(any('ansible.builtin.shell' in value for value in TASKS))
        commands = [value['ansible.builtin.command']['argv'] for value in TASKS if 'ansible.builtin.command' in value]
        for argv in commands:
            self.assertEqual(argv[:4], ['/usr/bin/python3', '-I', '-B', '-c'])
        copy = next(value['ansible.builtin.copy'] for value in TASKS if 'ansible.builtin.copy' in value)
        self.assertFalse(copy['force'])
        self.assertFalse(copy['follow'])
        self.assertFalse(copy['unsafe_writes'])
        dirs = next(value for value in TASKS if 'ansible.builtin.file' in value and value['ansible.builtin.file']['state'] == 'directory')
        self.assertIn('item.directory_missing', dirs['when'])
        cleanup = next(value for value in TASKS if 'ansible.builtin.file' in value and value['ansible.builtin.file']['state'] == 'absent')
        self.assertEqual(cleanup['ansible.builtin.file']['path'], '{{ item.destination }}')
        self.assertIn('item.exists', cleanup['when'])

    def test_post_reload_reads_effective_mount_dependencies_and_assertions(self):
        self.assertIn("state['NeedDaemonReload'] == 'no'", SOURCE)
        self.assertIn("str(destination) in shlex.split(state['DropInPaths'])", SOURCE)
        self.assertIn("set(paths) <= set(shlex.split(state['RequiresMountsFor']))", SOURCE)
        self.assertIn("['AssertPathIsMountPoint', False, False, path]", SOURCE)
        self.assertIn("org.freedesktop.systemd1.Unit', 'Asserts'", SOURCE)
        self.assertIn('verify_cleanup', SOURCE)
        self.assertLess(next(i for i, value in enumerate(TASKS) if 'ansible.builtin.systemd_service' in value), len(TASKS) - 1)

    def test_native_volume_and_bounded_nofollow_public_reads_only(self):
        for text in ("os.geteuid() == 0", "'systemd'", "'/proc/self/ns/'", "'/.dockerenv'", "'65.109.216.187'", "mapping == EXPECTED", "UUID=' + UUID", "value['uuid'] == UUID", "--mountpoint", "os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK", "value.st_nlink == 1"):
            self.assertIn(text, SOURCE)
        self.assertNotIn('402653', SOURCE)
        tree = ast.parse(SOURCE)
        forbidden = {'write', 'mkdir', 'unlink', 'remove', 'chmod', 'chown', 'rename', 'replace', 'rmdir'}
        self.assertFalse(any(isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in forbidden for node in ast.walk(tree)))
        self.assertNotIn('Environment=', SOURCE)
        self.assertIn("if line.startswith('ExecStart=')", SOURCE)
        self.assertNotIn('--property=ExecStart', SOURCE)
        compile(SOURCE, '<boot-gate-validator>', 'exec')


class BootMountSyntheticFixtures(unittest.TestCase):
    def setUp(self):
        self.ns = functions()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.ns['SYSTEMD_ROOT'] = str(self.root)
        self.fragment = self.root / 'fixture.service'
        self.fragment.write_text('[Unit]\nDescription=public synthetic fixture\n')
        self.fragment.chmod(0o644)
        original = os.lstat

        def owned_stat(path):
            info = original(path)
            return types.SimpleNamespace(**{name: (0 if name in ('st_uid', 'st_gid') else getattr(info, name)) for name in ('st_uid', 'st_gid', 'st_mode', 'st_nlink', 'st_size', 'st_dev', 'st_ino', 'st_mtime_ns', 'st_ctime_ns')})

        self.owner = patch('os.lstat', side_effect=owned_stat)
        self.owner.start()
        self.addCleanup(self.owner.stop)
        self.service = 'coin-public-market-telegram.service'
        self.state = {'LoadState': 'loaded', 'FragmentPath': str(self.fragment), 'DropInPaths': '', 'RequiresMountsFor': '', 'NeedDaemonReload': 'no', 'ReadWritePaths': PRIMARY + '/synthetic', 'ReadOnlyPaths': '', 'WorkingDirectory': str(self.root)}
        self.ns['properties'] = lambda service: self.state

    def destination(self):
        return self.root / (self.service + '.d') / self.ns['NAME']

    def install_fixture(self, text=None):
        destination = self.destination()
        destination.parent.mkdir(mode=0o755)
        destination.write_text(self.ns['content']([PRIMARY]) if text is None else text)
        destination.chmod(0o644)
        return destination

    def test_missing_destination_default_check_does_not_create_anything(self):
        before = sorted(self.root.iterdir())
        entry = self.ns['inspect_consumer'](self.service, [PRIMARY], 'check')
        self.assertTrue(entry['directory_missing'])
        self.assertFalse(entry['exists'])
        self.assertEqual(sorted(self.root.iterdir()), before)

    def test_repuposed_consumer_without_declared_mount_path_is_refused(self):
        self.state['ReadWritePaths'] = '/unrelated/path'
        with self.assertRaisesRegex(SystemExit, 'consumer path scope drift'):
            self.ns['inspect_consumer'](self.service, [PRIMARY], 'check')

    def test_execstart_path_only_evidence_closes_relay_and_watchdog_scope(self):
        self.state['ReadWritePaths'] = '/unrelated/operations'
        self.fragment.write_text('[Service]\nExecStart=/usr/bin/python3 /public/fixture.py --input ' + STAGING + '/synthetic --lock ' + SECURE + '/synthetic.lock\nEnvironment=NEVER_RETURN_THIS_VALUE=synthetic-private-value\n')
        result = self.ns['inspect_consumer']('market-pipeline-watchdog.service', [STAGING, SECURE], 'check')
        self.assertEqual(result['service'], 'market-pipeline-watchdog.service')
        self.assertNotIn('synthetic-private-value', json.dumps(result))
        self.assertNotIn('/public/fixture.py', json.dumps(result))

    def test_exact_owned_dropin_is_idempotent_but_foreign_bytes_refused(self):
        destination = self.install_fixture()
        before = destination.stat()
        self.assertTrue(self.ns['inspect_consumer'](self.service, [PRIMARY], 'check')['exists'])
        self.assertEqual(before.st_ino, destination.stat().st_ino)
        destination.write_text('[Unit]\nDescription=foreign\n')
        with self.assertRaisesRegex(SystemExit, 'foreign/changed'):
            self.ns['inspect_consumer'](self.service, [PRIMARY], 'check')

    def test_parent_symlink_and_writable_parent_refused(self):
        parent = self.destination().parent
        target = self.root / 'foreign-directory'
        target.mkdir()
        parent.symlink_to(target, target_is_directory=True)
        with self.assertRaises(SystemExit):
            self.ns['inspect_consumer'](self.service, [PRIMARY], 'check')
        parent.unlink()
        parent.mkdir(mode=0o777)
        parent.chmod(0o777)
        with self.assertRaises(SystemExit):
            self.ns['inspect_consumer'](self.service, [PRIMARY], 'check')

    def test_destination_symlink_hardlink_fifo_and_mode_refused(self):
        destination = self.destination()
        destination.parent.mkdir()
        target = self.root / 'foreign-file'
        target.write_text(self.ns['content']([PRIMARY]))
        target.chmod(0o644)
        destination.symlink_to(target)
        with self.assertRaises(SystemExit):
            self.ns['inspect_consumer'](self.service, [PRIMARY], 'check')
        destination.unlink()
        os.link(target, destination)
        with self.assertRaises(SystemExit):
            self.ns['inspect_consumer'](self.service, [PRIMARY], 'check')
        destination.unlink()
        os.mkfifo(destination, 0o600)
        with self.assertRaises(SystemExit):
            self.ns['inspect_consumer'](self.service, [PRIMARY], 'check')
        destination.unlink()
        destination.write_text(self.ns['content']([PRIMARY]))
        destination.chmod(0o666)
        with self.assertRaises(SystemExit):
            self.ns['inspect_consumer'](self.service, [PRIMARY], 'check')

    def test_post_reload_requires_loaded_exact_file_dependency_and_assertion(self):
        destination = self.install_fixture()
        self.state['DropInPaths'] = str(destination)
        self.state['RequiresMountsFor'] = PRIMARY
        assertions = [['AssertPathIsMountPoint', False, False, PRIMARY, 0]]
        self.ns['run'] = lambda argv: json.dumps({'data': ['/unit/synthetic'] if 'GetUnit' in argv else assertions})
        self.ns['inspect_consumer'](self.service, [PRIMARY], 'verify_setup')
        for bad in ([], [['AssertPathIsMountPoint', False, True, PRIMARY, 0]], [['ConditionPathIsMountPoint', False, False, PRIMARY, 0]]):
            assertions[:] = bad
            with self.assertRaisesRegex(SystemExit, 'mount assertion absent'):
                self.ns['inspect_consumer'](self.service, [PRIMARY], 'verify_setup')
        assertions[:] = [['AssertPathIsMountPoint', False, False, PRIMARY, 0]]
        self.state['NeedDaemonReload'] = 'yes'
        with self.assertRaises(SystemExit):
            self.ns['inspect_consumer'](self.service, [PRIMARY], 'verify_setup')

    def test_cleanup_verification_requires_owned_file_and_loaded_reference_absent(self):
        self.ns['inspect_consumer'](self.service, [PRIMARY], 'verify_cleanup')
        destination = self.install_fixture()
        with self.assertRaises(SystemExit):
            self.ns['inspect_consumer'](self.service, [PRIMARY], 'verify_cleanup')
        destination.unlink()
        self.state['DropInPaths'] = str(destination)
        with self.assertRaises(SystemExit):
            self.ns['inspect_consumer'](self.service, [PRIMARY], 'verify_cleanup')

    def test_public_reads_bound_size_before_open(self):
        path = self.root / 'oversized-public.conf'
        path.write_bytes(b'x' * 65537)
        path.chmod(0o644)
        with patch('os.open', side_effect=AssertionError('must reject before opening')), self.assertRaises(SystemExit):
            self.ns['public_read'](path)

    def test_public_read_rejects_path_replacement_after_open(self):
        path = self.root / 'public.conf'
        path.write_text('original public fixture')
        path.chmod(0o644)
        original_read = os.read

        def changed_read(fd, size):
            raw = original_read(fd, size)
            alternate = self.root / 'alternate.conf'
            alternate.write_text('changed public fixture')
            alternate.replace(path)
            return raw

        with patch('os.read', side_effect=changed_read), self.assertRaisesRegex(SystemExit, 'changed during read'):
            self.ns['public_read'](path)

    def test_metadata_subprocess_output_is_capped_before_memory_read_and_errors_are_static(self):
        self.assertEqual(self.ns['run']([sys.executable, '-I', '-c', 'print("synthetic")']), 'synthetic\n')
        for code in ('import os; os.write(1, b"x" * (3 * 1024 * 1024))', 'raise SystemExit(7)'):
            with self.assertRaisesRegex(SystemExit, 'PUBLIC_METADATA_(COMMAND_FAILED|OUTPUT_LIMIT)') as caught:
                self.ns['run']([sys.executable, '-I', '-c', code])
            self.assertNotIn(code, str(caught.exception))
        with self.assertRaisesRegex(SystemExit, 'PUBLIC_METADATA_EXEC_FAILED'):
            self.ns['run'](['/synthetic-missing-executable'])

    def test_metadata_timeout_kills_and_reaps_child_without_waiting_fifteen_seconds(self):
        child = MagicMock()
        child.__enter__.return_value = child
        child.wait.side_effect = [subprocess.TimeoutExpired('synthetic', 15), 0]
        with patch('subprocess.Popen', return_value=child), self.assertRaisesRegex(SystemExit, 'PUBLIC_METADATA_TIMEOUT'):
            self.ns['run'](['/synthetic-command'])
        child.kill.assert_called_once_with()
        self.assertEqual(child.wait.call_count, 2)

    def test_mount_uuid_source_persistence_and_hidden_override_validation(self):
        root = '/host-retained/rootfs-20260906/staging-data'
        value = {'target': STAGING, 'uuid': self.ns['UUID'], 'fstype': 'ext4', 'options': 'rw,noatime', 'source': '/dev/fixture[' + root + ']'}
        state = {'LoadState': 'loaded', 'UnitFileState': 'enabled', 'RequiresMountsFor': PRIMARY, 'FragmentPath': 'synthetic.mount', 'DropInPaths': '', 'NeedDaemonReload': 'no'}
        unit_text = '[Mount]\nWhat=' + PRIMARY + root + '\nWhere=' + STAGING + '\nType=none\nOptions=bind\n'
        self.ns['run'] = lambda argv: json.dumps({'filesystems': [value]}) if '--mountpoint' in argv else 'synthetic.mount\n'
        self.ns['properties'] = lambda unit: state
        self.ns['public_read'] = lambda path: unit_text
        self.assertEqual(self.ns['verify_mount'](STAGING, root), '/dev/fixture')
        for key, bad in [('uuid', 'other-uuid'), ('source', '/dev/fixture[/wrong]'), ('target', PRIMARY), ('options', 'ro,noatime')]:
            with self.subTest(key=key):
                original = value[key]
                value[key] = bad
                with self.assertRaises(SystemExit): self.ns['verify_mount'](STAGING, root)
                value[key] = original
        for key, bad in [('UnitFileState', 'disabled'), ('DropInPaths', '/foreign-override.conf'), ('NeedDaemonReload', 'yes')]:
            original = state[key]
            state[key] = bad
            with self.assertRaises(SystemExit): self.ns['verify_mount'](STAGING, root)
            state[key] = original
        unit_text = unit_text.replace(PRIMARY + root, PRIMARY + '/wrong-source')
        with self.assertRaises(SystemExit): self.ns['verify_mount'](STAGING, root)

    def test_primary_uuid_fstab_entry_must_be_exact_and_unique(self):
        value = {'target': PRIMARY, 'uuid': self.ns['UUID'], 'fstype': 'ext4', 'options': 'rw,noatime', 'source': '/dev/fixture'}
        state = {'LoadState': 'loaded', 'UnitFileState': 'generated', 'DropInPaths': '', 'NeedDaemonReload': 'no'}
        line = 'UUID=' + self.ns['UUID'] + ' ' + PRIMARY + ' ext4 defaults,nofail 0 2\n'
        self.ns['run'] = lambda argv: json.dumps({'filesystems': [value]}) if '--mountpoint' in argv else 'synthetic.mount\n'
        self.ns['properties'] = lambda unit: state
        self.ns['public_read'] = lambda path: line
        self.assertEqual(self.ns['verify_mount'](PRIMARY, ''), '/dev/fixture')
        for text in (line + line, line.replace(self.ns['UUID'], 'wrong-uuid')):
            self.ns['public_read'] = lambda path, text=text: text
            with self.assertRaises(SystemExit): self.ns['verify_mount'](PRIMARY, '')

    def test_all_container_source_union_is_public_paths_only_with_named_volume_fold(self):
        identifiers = ['a' * 64, 'b' * 64]
        sources = {identifiers[0]: [PRIMARY + '/docker-data/volumes/example/_data', '/srv/trading-bot/secure/market-data/example.env'], identifiers[1]: [STAGING + '/market-store', SECURE + '/control/example']}
        unknown = False
        seen = []

        def run(argv):
            seen.append(argv)
            if 'info' in argv: return PRIMARY + '/docker-data\n'
            if 'ps' in argv: return '\n'.join(identifiers)
            if 'inspect' in argv:
                self.assertEqual(argv[argv.index('--format') + 1], '{{range .Mounts}}{{println .Source}}{{end}}')
                return '\n'.join(sources[argv[-1]])
            source = argv[argv.index('--target') + 1]
            if unknown:
                value = {'target': '/unknown-mounted-root', 'uuid': 'other-uuid'}
            elif source.startswith(PRIMARY + '/'):
                value = {'target': source, 'uuid': self.ns['UUID']}
            elif source.startswith(STAGING + '/'):
                value = {'target': STAGING, 'uuid': self.ns['UUID']}
            elif source.startswith(SECURE + '/'):
                value = {'target': SECURE, 'uuid': self.ns['UUID']}
            else:
                value = {'target': '/', 'uuid': '64f6fb02-4fed-4836-a33b-86e8993afdfa'}
            return json.dumps({'filesystems': [value]})

        self.ns['run'] = run
        with patch('os.open', side_effect=AssertionError('never open a container source file')):
            result = self.ns['docker_source_roots']()
        self.assertEqual(result, {'containers_checked': 2, 'nonroot_source_mounts': sorted([PRIMARY, STAGING, SECURE])})
        self.assertTrue(all('--host=unix:///var/run/docker.sock' in argv for argv in seen if argv[0] == '/usr/bin/docker'))
        unknown = True
        with self.assertRaises(SystemExit): self.ns['docker_source_roots']()
        unknown = False
        original_run = self.ns['run']

        def same_volume_arbitrary_mount(argv):
            if '--target' in argv and argv[argv.index('--target') + 1].startswith(PRIMARY + '/'):
                return json.dumps({'filesystems': [{'target': PRIMARY + '/docker-data/custom-input', 'uuid': self.ns['UUID']}]})
            return original_run(argv)

        self.ns['run'] = same_volume_arbitrary_mount
        with self.assertRaisesRegex(SystemExit, 'unknown nested Docker-data mount'):
            self.ns['docker_source_roots']()


if __name__ == '__main__':
    unittest.main()
