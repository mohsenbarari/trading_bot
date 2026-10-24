"""Closed four-file backup retention; synthetic bytes, no host/network commands."""
from contextlib import ExitStack
from hashlib import sha256
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from jinja2 import Environment, StrictUndefined
import yaml

REPO = Path(__file__).resolve().parents[1]
PLAY = yaml.safe_load((REPO / 'deploy/maintenance/web_application_backup_offhost.yml').read_text())[0]
SOURCE = PLAY['vars']['web_app_offhost_validator']


class WebApplicationOffhostTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack(); self.addCleanup(self.stack.close)
        for name in ('subprocess.Popen', 'subprocess.run', 'socket.socket.connect', 'socket.create_connection', 'socket.getaddrinfo'):
            self.stack.enter_context(patch(name, side_effect=AssertionError('host/network forbidden')))
        self.code = {'__name__': '_offhost_fixture'}
        exec(compile(SOURCE, '<standalone-backup-validator>', 'exec'), self.code)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix='.web-app-offhost-fixture-', dir=REPO)))
        self.root.chmod(0o700)
        self.remote = self.root / 'source'; self.remote.mkdir(mode=0o700)
        self.destination = self.root / 'retained'
        self.receipt = self.root / 'new-receipt.json'; self.original = self.root / 'original.json'
        self.pins = []
        for name, _, _ in self.code['PINS']:
            data = ('public synthetic fixture ' + name).encode()
            path = self.remote / name; path.write_bytes(data); path.chmod(0o600)
            self.pins.append((name, len(data), sha256(data).hexdigest()))
        self.code.update(SOURCE=self.remote, DEST=self.destination, RECEIPT=self.receipt, APP=self.original, PINS=tuple(self.pins))
        self.app = {'status': 'ok', 'results': [
            {'role': 'foreign', 'project_label': 'trading_bot', 'status': 'ok', 'files': [],
             'restore_smoke': {'status': 'passed', 'cleanup': {'status': 'passed'}}},
            {'role': 'iran', 'project_label': 'current', 'status': 'ok',
             'restore_smoke': {'status': 'passed', 'cleanup': {'status': 'passed'}},
             'files': [{'path': str(self.remote / name), 'bytes': size, 'sha256': digest} for name, size, digest in self.pins]}]}
        self.write_original()
        self.stack.enter_context(patch('os.statvfs', return_value=type('Capacity', (), {'f_bavail': 20 * 1024**3, 'f_frsize': 1})()))

    def write_original(self):
        raw = json.dumps(self.app).encode(); self.original.write_bytes(raw); self.original.chmod(0o600)
        self.code['APP_SHA'] = sha256(raw).hexdigest()

    def populate(self):
        self.destination.mkdir(mode=0o700)
        for name, _, _ in self.pins:
            target = self.destination / name; target.write_bytes((self.remote / name).read_bytes()); target.chmod(0o600)

    def test_source_is_exactly_four_authentic_pins_and_only_new_evidence_paths(self):
        actual = {'__name__': '_real_pin_source'}
        exec(compile(SOURCE, '<source-pins>', 'exec'), actual)
        self.assertEqual(len(actual['PINS']), 4)
        self.assertEqual(sum(size for _, size, _ in actual['PINS']), 5519376)
        self.assertEqual([name for name, _, _ in actual['PINS']], PLAY['vars']['web_app_offhost_names'])
        self.assertEqual(actual['APP_SHA'], '710e71ff893a319cb4fb10fd44c850c4845bafbdee68889fc128f7947fcf958d')
        self.assertEqual(str(actual['DEST']), '/srv/trading-bot/backups/security-web-app-offhost-20261024-03')
        self.assertEqual(str(actual['RECEIPT']), '/root/trading-bot/trading_bot/.local/security-web-engine-20261024-app-offhost.json')

    def test_native_check_is_read_only_and_unknown_existing_targets_refuse(self):
        before = sorted(self.root.iterdir()); old = self.original.read_bytes()
        self.code['native_check']()
        self.assertEqual(sorted(self.root.iterdir()), before); self.assertEqual(self.original.read_bytes(), old)
        self.destination.mkdir(mode=0o700)
        with self.assertRaises(RuntimeError): self.code['native_check']()
        self.assertFalse(self.receipt.exists())

    def test_actual_four_file_hash_fsync_and_exclusive_receipt_preserve_original(self):
        old = self.original.read_bytes(); self.populate()
        value = self.code['publish']()
        self.assertEqual(value['source_role'], 'iran'); self.assertEqual(value['project_label'], 'current')
        self.assertEqual(len(value['files']), 4); self.assertTrue(value['fsync_completed'])
        self.assertEqual({f['sha256'] for f in value['files']}, {digest for _, _, digest in self.pins})
        self.assertEqual(json.loads(self.receipt.read_bytes()), value)
        self.assertEqual(self.receipt.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.receipt.stat().st_nlink, 1)
        self.assertEqual(self.original.read_bytes(), old)
        new = self.receipt.read_bytes()
        with self.assertRaises(RuntimeError): self.code['publish']()
        self.assertEqual(self.receipt.read_bytes(), new); self.assertEqual(self.original.read_bytes(), old)

    def test_foreign_label_does_not_stand_in_for_actual_web_role_or_missing_file(self):
        self.app['results'][1]['project_label'] = 'trading_bot'; self.write_original()
        with self.assertRaises(RuntimeError): self.code['native_check']()
        self.assertFalse(self.destination.exists())
        self.app['results'][1]['project_label'] = 'current'
        self.app['results'][1]['files'].pop(); self.write_original()
        with self.assertRaises(RuntimeError): self.code['native_check']()
        self.assertFalse(self.destination.exists())

    def test_corrupt_missing_or_extra_file_never_publishes_evidence(self):
        self.populate()
        name = self.pins[0][0]; target = self.destination / name
        old = target.read_bytes(); target.write_bytes(b'corrupt')
        with self.assertRaises(RuntimeError): self.code['publish']()
        self.assertFalse(self.receipt.exists())
        target.write_bytes(old); extra = self.destination / 'unknown'; extra.write_bytes(b'public fixture')
        with self.assertRaises(RuntimeError): self.code['publish']()
        self.assertFalse(self.receipt.exists())

    def test_symlink_hardlink_or_writable_payload_is_refused(self):
        self.populate(); name, size, digest = self.pins[0]
        target = self.destination / name
        target.chmod(0o644)
        with self.assertRaises(RuntimeError): self.code['digest'](target, size, digest)
        target.chmod(0o600); link = self.root / 'hardlink'; os.link(target, link)
        with self.assertRaises(RuntimeError): self.code['digest'](target, size, digest)
        link.unlink(); target.unlink(); target.symlink_to(self.remote / name)
        with self.assertRaises(RuntimeError): self.code['publish']()
        self.assertFalse(self.receipt.exists())

    def test_fetch_default_0644_is_safely_normalized_before_strict_publish_not_waived(self):
        self.populate()
        for name, _, _ in self.pins: (self.destination / name).chmod(0o644)
        with self.assertRaises(RuntimeError): self.code['publish']()
        self.assertFalse(self.receipt.exists())
        tasks = PLAY['tasks']
        inspect = next(task for task in tasks if task.get('register') == 'web_app_fetched_metadata')
        guard = next(task for task in tasks if task['name'].startswith('Refuse unsafe fetched files'))
        normalize = next(task for task in tasks if task['name'].startswith('Make only the four newly fetched'))
        publish = next(task for task in tasks if task['name'].startswith('Verify SHA256 and fsync'))
        self.assertLess(tasks.index(inspect), tasks.index(guard))
        self.assertLess(tasks.index(guard), tasks.index(normalize)); self.assertLess(tasks.index(normalize), tasks.index(publish))
        self.assertFalse(inspect['ansible.builtin.stat']['follow'])
        self.assertFalse(normalize['ansible.builtin.file']['follow'])
        self.assertEqual(normalize['ansible.builtin.file']['mode'], '0600')
        self.assertEqual(normalize['loop'], '{{ web_app_offhost_names }}')
        env = Environment(undefined=StrictUndefined)
        checks = [env.compile_expression(value) for value in guard['ansible.builtin.assert']['that']]
        metadata = {'isreg': True, 'islnk': False, 'uid': 0, 'nlink': 1, 'mode': '0644'}
        self.assertTrue(all(check(item={'stat': metadata}) for check in checks))
        for key, bad in [('islnk', True), ('isreg', False), ('uid', 65534), ('nlink', 2), ('mode', '0664')]:
            self.assertFalse(all(check(item={'stat': {**metadata, key: bad}}) for check in checks))
        for name, _, _ in self.pins: (self.destination / name).chmod(0o600)
        self.code['publish']()
        self.assertTrue(self.receipt.is_file())

    def test_capacity_floor_is_not_weakened_and_no_default_transfer_or_runtime_write(self):
        with patch('os.statvfs', return_value=type('Capacity', (), {'f_bavail': 12 * 1024**3, 'f_frsize': 1})()):
            with self.assertRaises(RuntimeError): self.code['native_check']()
        tasks = PLAY['tasks']; boundary = next(i for i, task in enumerate(tasks) if task.get('ansible.builtin.meta') == 'end_play')
        self.assertEqual(PLAY['vars']['web_app_offhost_action'], 'check')
        self.assertTrue(all('ansible.builtin.fetch' not in task for task in tasks[:boundary]))
        transfer = next(task for task in tasks if 'ansible.builtin.fetch' in task)
        self.assertTrue(transfer['no_log']); self.assertTrue(transfer['ansible.builtin.fetch']['validate_checksum'])
        self.assertGreater(tasks.index(transfer), boundary)
        for task in tasks:
            self.assertFalse(set(task) & {'ansible.builtin.shell', 'ansible.builtin.apt', 'ansible.builtin.systemd_service', 'ansible.builtin.reboot'})


if __name__ == '__main__': unittest.main()
