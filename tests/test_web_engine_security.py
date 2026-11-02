"""Hermetic public-metadata/preparation contracts; never connect to Docker/SSH."""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'deploy/maintenance/web_engine_preflight.py'
spec = importlib.util.spec_from_file_location('web_engine_preflight_fixture', SOURCE)
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


def rows():
    result = []
    for project, count in guard.PROJECT_COUNTS.items():
        for _ in range(count):
            ident = len(result) + 1
            result.append({'id': f'{ident:064x}', 'name': '/fixture-' + str(ident),
                           'image': 'sha256:' + 'a' * 64, 'project': project,
                           'running': True, 'restart_policy': {'Name': 'always' if project == 'current' else 'unless-stopped', 'MaximumRetryCount': 0},
                           'mounts': [], 'health': None})
    migration = next(r for r in result if r['project'] == 'trading_bot_staging_iran')
    migration.update(name='/trading_bot_staging_iran-migration-1', running=False,
                     restart_policy={'Name': 'no', 'MaximumRetryCount': 0})
    return result


class WebEnginePreparationTests(unittest.TestCase):
    def test_exact_twentyseven_five_project_inventory_and_stopped_migration(self):
        guard.baseline_contract(rows())
        for mutate in [lambda r: r.pop(), lambda r: r[0].update(project='unknown'),
                       lambda r: r[0].update(id=r[1]['id']),
                       lambda r: next(v for v in r if not v['running']).update(running=True),
                       lambda r: next(v for v in r if not v['running']).update(restart_policy={'Name': 'always', 'MaximumRetryCount': 0})]:
            with self.subTest(mutation=mutate):
                fixture = rows(); mutate(fixture)
                with self.assertRaises(guard.Refusal): guard.baseline_contract(fixture)

    def test_actual_store_is_already_containerd_not_overlay2(self):
        actual = {'root': '/var/lib/docker', 'driver': 'overlayfs',
                  'driver_status': [['driver-type', 'io.containerd.snapshotter.v1']], 'live_restore': False}
        guard.store_contract(actual)
        for key, value in [('root', '/different'), ('driver', 'overlay2'), ('driver_status', []), ('live_restore', True)]:
            changed = dict(actual); changed[key] = value
            with self.subTest(key=key), self.assertRaises(guard.Refusal): guard.store_contract(changed)

    def test_mount_order_only_is_ignored_and_duplicate_or_source_drift_is_not(self):
        mount = {'Destination': '/data', 'Mode': 'rw', 'Propagation': 'rprivate', 'RW': True, 'Source': '/source', 'Type': 'bind'}
        other = dict(mount, Destination='/other')
        self.assertEqual(guard.canonical_mounts([mount, other]), guard.canonical_mounts([other, mount]))
        self.assertNotEqual(guard.canonical_mounts([mount]), guard.canonical_mounts([dict(mount, Source='/different')]))
        with self.assertRaises(guard.Refusal): guard.canonical_mounts([mount, mount])

    def test_all_nine_offline_debs_are_exact_public_version_size_digest_pins(self):
        for pins in [guard.OLD_PACKAGES, guard.NEW_PACKAGES]:
            entries = [{'name': name, 'version': version, 'size': size, 'sha256': digest, 'path': '/private/' + name + '.deb'} for name, (version, size, digest) in pins.items()]
            guard.package_contract(entries, pins)
            self.assertTrue(all(len(p['sha256']) == 64 for p in entries))
            for field, replacement in [('version', 'foreign-distro'), ('size', 1), ('sha256', '0' * 64), ('name', 'unknown')]:
                changed = deepcopy(entries); changed[0][field] = replacement
                with self.subTest(field=field), self.assertRaises(guard.Refusal): guard.package_contract(changed, pins)
            with self.assertRaises(guard.Refusal): guard.package_contract(entries[:-1], pins)
            changed = deepcopy(entries); changed[1]['path'] = changed[0]['path']
            with self.assertRaises(guard.Refusal): guard.package_contract(changed, pins)
        self.assertNotIn('docker-ce-rootless-extras', guard.NEW_PACKAGES)
        self.assertEqual(sum(p[1] for p in guard.OLD_PACKAGES.values()), 88152580)
        self.assertTrue(all('ubuntu.26.04~resolute' in p[0] for p in guard.NEW_PACKAGES.values()))

    def test_safe_manifest_and_package_streaming_no_symlink_hardlink_fifo(self):
        with tempfile.TemporaryDirectory(prefix='.web-engine-test-', dir=ROOT) as directory:
            base = Path(directory)
            manifest = base / 'manifest.json'
            raw = b'{"schema":"fixture","value":1}\n'
            manifest.write_bytes(raw); manifest.chmod(0o600)
            self.assertEqual(guard.document(manifest, sha256(raw).hexdigest())['value'], 1)
            self.assertEqual(guard.file_digest(manifest, maximum=1024, private=True), (len(raw), sha256(raw).hexdigest()))
            with self.assertRaises(guard.Refusal): guard.document(manifest, '0' * 64)
            with self.assertRaises(guard.Refusal): guard.file_digest(manifest, maximum=1, private=True)
            link = base / 'symlink'; link.symlink_to(manifest)
            with self.assertRaises((guard.Refusal, OSError)): guard.document(link, sha256(raw).hexdigest())
            hard = base / 'hardlink'; os.link(manifest, hard)
            with self.assertRaises(guard.Refusal): guard.file_digest(manifest, maximum=1024, private=True)
            hard.unlink()
            fifo = base / 'fifo'; os.mkfifo(fifo, mode=0o600)
            with self.assertRaises(guard.Refusal): guard.document(fifo, '0' * 64)
            with self.assertRaises(guard.Refusal): guard.file_digest(fifo, maximum=1024, private=True)
            manifest.write_bytes(b'{"same":1,"same":2}'); manifest.chmod(0o600)
            with self.assertRaises(guard.Refusal): guard.document(manifest, sha256(manifest.read_bytes()).hexdigest())
            manifest.chmod(0o644)
            with self.assertRaises(guard.Refusal): guard.document(manifest, sha256(manifest.read_bytes()).hexdigest())
            self.assertEqual(guard.file_digest(manifest, maximum=1024, private=False)[0], manifest.stat().st_size)

    def test_native_subprocess_bounds_and_static_error_no_environment_dump(self):
        with patch.object(guard.subprocess, 'run', side_effect=subprocess.CalledProcessError(1, ['private-fixture-argument'], stderr='fixture-secret')) as native:
            with self.assertRaisesRegex(guard.Refusal, '^native_metadata_command_failed$'):
                guard.command(['/usr/bin/python3', '-I', '-B', '-c', 'pass'])
            call = native.call_args
            self.assertEqual(call.kwargs['timeout'], 20)
            self.assertEqual(set(call.kwargs['env']), {'PATH', 'LC_ALL', 'DOCKER_HOST'})
            self.assertEqual(call.kwargs['stderr'], subprocess.DEVNULL)
            self.assertIsNot(call.kwargs['stdout'], subprocess.PIPE)
            self.assertTrue(call.kwargs['check'])

    def test_preflight_has_no_mutating_entrypoint_or_claimed_security_closure(self):
        with patch.object(sys, 'argv', [str(SOURCE), '--manifest', '/private/manifest.json', '--manifest-sha256', 'a' * 64]), \
                patch.object(guard, 'document', return_value={'fixture': True}), patch.object(guard, 'validate') as validate, \
                patch('builtins.print') as printed:
            self.assertEqual(guard.main(), 0)
            validate.assert_called_once_with({'fixture': True}, verify_payloads=False)
            result = json.loads(printed.call_args.args[0])
            self.assertFalse(result['runtime_changed'])
            self.assertFalse(result['apply_available'])
            self.assertFalse(result['backup_restore_verified'])
            self.assertFalse(result['remediation_complete'])
        playbook = (ROOT / 'deploy/maintenance/web_engine_preflight.yml').read_text()
        self.assertIn('not (maintenance_apply | default(false) | bool)', playbook)
        for forbidden in ['ansible.builtin.apt:', 'ansible.builtin.copy:', 'ansible.builtin.systemd_service:', 'ansible.builtin.shell:']:
            self.assertNotIn(forbidden, playbook)
        self.assertIn('StrictHostKeyChecking=yes', (ROOT / 'deploy/maintenance/web_engine_inventory.yml').read_text())


if __name__ == '__main__':
    unittest.main()
