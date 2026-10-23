"""Synthetic, bounded transaction/backup tests: no live Docker, SSH or apt."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import fcntl
from hashlib import sha256
import importlib.util
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
HERE = REPO / 'deploy/maintenance'


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


guard = module('web_engine_transaction_fixture', HERE / 'web_engine_controller.py')
receiver = module('web_engine_offhost_fixture', HERE / 'web_engine_offhost.py')


def rows():
    result = []
    for project, count in guard.P.PROJECT_COUNTS.items():
        for _ in range(count):
            ident = len(result) + 1
            result.append({'id': f'{ident:012x}' + 'a' * 52, 'name': '/fixture-' + str(ident),
                           'image': 'sha256:' + 'a' * 64, 'project': project, 'running': True,
                           'restart_policy': {'Name': 'unless-stopped', 'MaximumRetryCount': 0},
                           'mounts': [], 'health': None, 'network_mode': 'bridge', 'port_bindings': {}, 'network_names': []})
    migration = next(r for r in result if r['project'] == 'trading_bot_staging_iran')
    migration.update(name='/trading_bot_staging_iran-migration-1', running=False,
                     restart_policy={'Name': 'no', 'MaximumRetryCount': 0})
    return result


class FiniteWebEngineTests(unittest.TestCase):
    def owned_transaction(self, failure=False, race=False):
        with tempfile.TemporaryDirectory(prefix='.web-owned-transaction-', dir=REPO) as directory:
            root = Path(directory)
            marker, reservation = root / 'marker', root / 'reservation'
            marker.write_bytes(b'unchanged historical marker\n'); marker.chmod(0o600)
            plan = {'operation_id': 'fixture', 'nonce': 'a' * 32, 'new_packages': [], 'backup_root': str(root / 'backup'),
                    'ansible_binary': '/fixture/ansible-playbook', 'deadline_utc': '2099-01-01T00:00:00Z'}
            called = []
            original_publish = guard.B.publish_noreplace
            def publish(temporary, target):
                if race and target == reservation:
                    target.write_bytes(b'foreign owner\n'); target.chmod(0o600)
                return original_publish(temporary, target)
            def child(argv, env, deadline, *, lock_fds):
                called.append(True)
                self.assertEqual(len(lock_fds), 3)
                guard.check_owners(plan, 'b' * 64)
                for path in (marker, guard.paths(plan)[1], reservation):
                    fd = os.open(path, os.O_RDWR)
                    try:
                        with self.assertRaises(BlockingIOError): fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    finally: os.close(fd)
                if failure: raise RuntimeError('fixture child crash')
                for phase in guard.PHASES[1:]: guard.advance(plan, 'b' * 64, phase)
                return 0
            with patch.object(guard, 'ROOT', root), patch.object(guard.P, 'MARKER', marker), patch.object(guard.P, 'RESERVATION', reservation), \
                    patch.object(guard, 'invariant_contract'), patch.object(guard, 'runtime_contract'), patch.object(guard, 'verify_private_inputs'), \
                    patch.object(guard, 'package_versions', return_value={n: p[0] for n, p in guard.P.OLD_PACKAGES.items()}), \
                    patch.object(guard.B, 'command', return_value=''), patch.object(guard, 'apt_scope'), patch.object(guard, 'offhost'), \
                    patch.object(guard.os, 'statvfs', return_value=type('Capacity', (), {'f_bavail': 100 * 1024**3, 'f_frsize': 1})()), \
                    patch.object(guard.B, 'execute_ansible', side_effect=child), patch.object(guard.B, 'publish_noreplace', side_effect=publish), patch('builtins.print'):
                if failure or race:
                    with self.assertRaises((RuntimeError, guard.B.Refusal)): guard.run(plan, root / 'manifest', 'b' * 64, True)
                    self.assertTrue(reservation.exists())
                    if race: self.assertEqual(reservation.read_bytes(), b'foreign owner\n'); self.assertEqual(called, [])
                    else:
                        self.assertEqual(guard.journal(plan, 'b' * 64)['phase'], 'reserved')
                        self.assertEqual(guard.B.document(reservation)['nonce'], plan['nonce'])
                else:
                    guard.run(plan, root / 'manifest', 'b' * 64, True)
                    self.assertFalse(reservation.exists()); self.assertFalse(guard.paths(plan)[1].exists())
                    self.assertEqual(guard.journal(plan, 'b' * 64)['phase'], 'complete')
            self.assertEqual(marker.read_bytes(), b'unchanged historical marker\n')

    def test_real_owned_flocks_inherited_as_three_descriptors_and_released_only_after_complete(self):
        self.owned_transaction()

    def test_actual_child_crash_retains_reservation_journal_and_historical_marker(self):
        self.owned_transaction(failure=True)

    def test_reservation_publication_race_never_overwrites_foreign_owner_or_runs_ansible(self):
        self.owned_transaction(race=True)

    def test_offline_apt_scope_is_exact_vendor_replacement_not_autoremove(self):
        result = '\n'.join(['Inst ' + p + ' (version repo)' for p in guard.P.NEW_PACKAGES]
                           + ['Remv ' + p + ' [old]' for p in guard.P.OLD_PACKAGES])
        guard.apt_scope(result)
        for extra in ['\nRemv openssh-server [old]', '\nInst libc6 (new repo)']:
            with self.assertRaises(guard.P.Refusal): guard.apt_scope(result + extra)
        with self.assertRaises(guard.P.Refusal): guard.apt_scope(result.replace('Remv runc [old]', ''))
        plan = {'new_packages': [{'path': '/private/' + p + '.deb'} for p in guard.P.NEW_PACKAGES]}
        argv = guard.apt_argv(plan, simulation=False)
        self.assertIn('--no-download', argv)
        self.assertIn('--no-install-recommends', argv)
        self.assertNotIn('autoremove', argv)
        self.assertNotIn('purge', argv)
        self.assertTrue(set(p + '-' for p in guard.P.OLD_PACKAGES) <= set(argv))

    def test_phase_journal_is_linear_and_published_durably(self):
        with patch.object(guard, 'journal', return_value={'phase': 'quiesced'}), patch.object(guard, 'write_json') as write:
            value = guard.advance({'operation_id': 'fixture'}, 'a' * 64, 'backing_up', fixture=True)
            self.assertEqual(value['phase'], 'backing_up')
            write.assert_called_once()
            self.assertFalse(write.call_args.kwargs['exclusive'])
        for phase in ['quiesced', 'installed', 'complete']:
            with patch.object(guard, 'journal', return_value={'phase': 'quiesced'}), patch.object(guard, 'write_json') as write:
                with self.assertRaises(guard.P.Refusal): guard.advance({'operation_id': 'fixture'}, 'a' * 64, phase)
                write.assert_not_called()

    def test_checkpoint_ack_is_idempotent_but_revalidates_real_conditions(self):
        with patch.object(guard, 'invariant_contract') as invariants, patch.object(guard, 'runtime_contract') as runtime, \
                patch.object(guard, 'journal', return_value={'phase': 'datastores'}), patch.object(guard, 'advance') as advance:
            guard.checkpoint({}, 'a' * 64, 'datastores')
            invariants.assert_called_once_with({}, 'a' * 64, owned=True)
            runtime.assert_called_once_with({}, 'datastores')
            advance.assert_not_called()

    def test_default_run_never_creates_reservations_or_invokes_ansible(self):
        with patch.object(guard, 'invariant_contract'), patch.object(guard, 'runtime_contract'), \
                patch.object(guard, 'package_versions', return_value={n: p[0] for n, p in guard.P.OLD_PACKAGES.items()}), \
                patch.object(guard.B, 'command', return_value=''), patch.object(guard, 'apt_scope'), \
                patch.object(guard.os.path, 'lexists', return_value=False), patch.object(guard.os, 'open') as opened, \
                patch.object(guard.os, 'statvfs', return_value=type('Capacity', (), {'f_bavail': 100 * 1024**3, 'f_frsize': 1})()), patch.object(guard, 'verify_private_inputs'), \
                patch.object(guard, 'offhost') as remote, patch.object(guard.B, 'execute_ansible') as ansible, patch('builtins.print'):
            guard.run({'new_packages': [], 'backup_root': '/private/backup'}, Path('/private/manifest'), 'a' * 64, False)
            opened.assert_not_called(); ansible.assert_not_called()
            self.assertIsNone(remote.call_args.kwargs['verify'])

    def test_old_package_mismatch_fails_before_any_operational_write(self):
        with patch.object(guard, 'invariant_contract'), patch.object(guard, 'runtime_contract'), \
                patch.object(guard, 'package_versions', return_value={}), patch.object(guard.os, 'open') as opened:
            with self.assertRaises(guard.P.Refusal): guard.run({}, Path('/private/manifest'), 'a' * 64, True)
            opened.assert_not_called()

    def runtime(self, phase, change=None):
        baseline = rows()
        plan = {'containers': baseline, 'units': [], 'datastore_ids': [baseline[0]['id']], 'datastore_probes': [],
                'engine_intent': [{'name': n, 'enabled': 'enabled', 'active': True} for n in [*guard.ENGINES, 'nginx.service']]}
        actual = deepcopy(baseline)
        for row in actual:
            row['running'] = row['running'] if phase in {'baseline', 'healthy', 'complete'} else row['id'] in plan['datastore_ids'] if phase == 'datastores' else False
            if phase not in {'baseline', 'complete'}: row['restart_policy'] = {'Name': 'no', 'MaximumRetryCount': 0}
        if change: change(actual)
        by_id = {r['id']: r for r in actual}
        def native(argv):
            if argv[1] == 'ps': return '\n'.join(by_id)
            if argv[1] == 'info':
                if '.CgroupDriver' in argv[-1]: return json.dumps({'driver': 'systemd', 'version': '2'})
                return json.dumps({'root': '/var/lib/docker', 'driver': 'overlayfs', 'driver_status': [['driver-type', 'io.containerd.snapshotter.v1']], 'live_restore': False})
            if argv[1] == 'inspect': return json.dumps(by_id[argv[-1]])
            if argv[0] == '/usr/bin/systemctl': return 'enabled' if argv[4] == 'UnitFileState' else 'active'
            raise AssertionError('unexpected fixture command')
        with patch.object(guard.B, 'command', side_effect=native), patch.object(guard, 'patched_runtime'), patch.object(guard, 'containerd_contract'):
            guard.runtime_contract(plan, phase)

    def test_every_phase_preserves_same_twentyseven_and_migration_stays_stopped(self):
        for phase in ['baseline', 'quiesced', 'datastores', 'healthy', 'complete']:
            with self.subTest(phase=phase): self.runtime(phase)
        for change in [lambda r: r[0].update(image='sha256:' + 'b' * 64),
                       lambda r: r[0].update(project='foreign'),
                       lambda r: next(v for v in r if v['name'].endswith('migration-1')).update(running=True),
                       lambda r: r.pop()]:
            with self.assertRaises(guard.P.Refusal): self.runtime('healthy', change)

    def test_engine_backup_precedes_package_install_and_package_stage_needs_real_offhost(self):
        with patch.object(guard, 'invariant_contract'), patch.object(guard, 'engine_stopped'), \
                patch.object(guard, 'journal', return_value={'phase': 'quiesced'}), patch.object(guard, 'advance') as advance:
            with self.assertRaises(guard.P.Refusal): guard.install({}, 'a' * 64)
            advance.assert_not_called()

    def test_backup_capacity_refusal_is_before_tar_encrypt_or_ssh(self):
        capacity = type('Capacity', (), {'f_bavail': 1, 'f_frsize': 4096})()
        with patch.object(guard, 'invariant_contract'), patch.object(guard, 'engine_stopped'), \
                patch.object(guard, 'journal', return_value={'phase': 'quiesced'}), patch.object(guard, 'advance'), \
                patch.object(guard.os, 'statvfs', return_value=capacity), patch.object(guard, 'bounded') as launch:
            with self.assertRaisesRegex(guard.P.Refusal, 'capacity'):
                guard.engine_backup({'backup_root': '/private/fixture'}, 'a' * 64)
            launch.assert_not_called()

    def test_real_owned_child_timeout_is_bounded_and_reaped(self):
        with self.assertRaisesRegex(guard.P.Refusal, 'timeout'):
            guard.bounded([sys.executable, '-I', '-B', '-c', 'import time;time.sleep(30)'], timeout=0.05)

    def test_non_docker_containerd_tasks_are_never_implicitly_stopped_or_accepted(self):
        plan = {'containers': rows(), 'datastore_ids': []}
        tasks = {r['id'] for r in plan['containers'] if r['running']}
        def native(argv):
            if 'namespaces' in argv: return 'moby\nmoby_history'
            if 'tasks' in argv: return '\n'.join(sorted(tasks)) if argv[4] == 'moby' else ''
            return 'TYPE ID PLATFORMS STATUS\nio.containerd.snapshotter.v1 overlayfs linux/amd64 ok'
        with patch.object(guard.B, 'command', side_effect=native): guard.containerd_contract(plan, 'baseline')
        def unknown(argv):
            if 'tasks' in argv and argv[4] == 'moby_history': return 'foreign-task'
            return native(argv)
        with patch.object(guard.B, 'command', side_effect=unknown), self.assertRaisesRegex(guard.P.Refusal, 'unexpected_containerd'):
            guard.containerd_contract(plan, 'baseline')
        with patch.object(guard.B, 'command', return_value='moby\nmoby_history\nforeign'), self.assertRaisesRegex(guard.P.Refusal, 'namespace'):
            guard.containerd_contract(plan, 'baseline')

    def test_vendor_config_is_derived_from_pinned_payload_without_extracting_files(self):
        value = b'version = 2\ndisabled_plugins = ["cri"]\n'
        def archive_data(*, symlink=False, duplicate=False):
            output = io.BytesIO()
            with tarfile.open(fileobj=output, mode='w') as archive:
                info = tarfile.TarInfo('./etc/containerd/config.toml')
                info.mode = 0o644; info.uid = 0
                if symlink: info.type = tarfile.SYMTYPE; info.linkname = '/outside'
                else: info.size = len(value)
                archive.addfile(info, None if symlink else io.BytesIO(value))
                if duplicate: archive.addfile(info, io.BytesIO(value))
            return output.getvalue()
        def bounded(argv, **kwargs): os.write(kwargs['stdout'], archive_data())
        plan = {'new_packages': [{'name': 'containerd.io', 'path': '/private/containerd.deb'}]}
        with patch.object(guard, 'bounded', side_effect=bounded):
            self.assertEqual(guard.vendor_configs(plan), {'/etc/containerd/config.toml': {'size': len(value), 'sha256': sha256(value).hexdigest()}})
        for bad in [archive_data(symlink=True), archive_data(duplicate=True)]:
            with patch.object(guard, 'bounded', side_effect=lambda argv, **kw: os.write(kw['stdout'], bad)), self.assertRaises(guard.P.Refusal):
                guard.vendor_configs(plan)

    def test_strict_market_restore_receipt_supports_real_short_id_but_not_forged_or_nonparity(self):
        with tempfile.TemporaryDirectory(prefix='.market-proof-', dir=REPO) as directory:
            artifact = Path(directory) / 'market.dump'; artifact.write_bytes(b'fixture dump'); artifact.chmod(0o600)
            containers = rows(); containers[0]['name'] = '/market-private-pipeline-primary-market-database-1'
            parity = {'schema_versions': [1], 'table_count': 1, 'fact_count': 4,
                      'table_row_counts': {'market_facts': 4}, 'sequence_values': {}}
            receipt = {'schema': 'market_pipeline_backup_restore/1.2', 'status': 'PASS',
                       'created_at_utc': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
                       'database_mutated': False, 'services_started': False,
                       'source': {'container_id': containers[0]['id'][:12], **parity},
                       'restore_smoke': {'status': 'PASS', 'cleanup_status': 'PASS', **parity},
                       'backup': {'path': str(artifact), 'size_bytes': artifact.stat().st_size, 'sha256': sha256(artifact.read_bytes()).hexdigest()}}
            identity = list(guard.B.identity(artifact.lstat()))
            guard.market_contract({'containers': containers}, receipt, identity)
            for alter in [lambda r: r['source'].update(container_id='f' * 12),
                          lambda r: r['restore_smoke'].update(cleanup_status='FAIL'),
                          lambda r: r['restore_smoke'].update(fact_count=3),
                          lambda r: r.update(created_at_utc=(datetime.now(timezone.utc)-timedelta(hours=2)).isoformat())]:
                bad = deepcopy(receipt); alter(bad)
                with self.assertRaises(guard.P.Refusal): guard.market_contract({'containers': containers}, bad, identity)

    def test_market_dump_is_generated_only_after_non_database_writer_quiescence(self):
        with patch.object(guard, 'invariant_contract'), patch.object(guard, 'journal', return_value={'phase': 'quiescing'}), patch.object(guard, 'bounded') as launch:
            with self.assertRaisesRegex(guard.P.Refusal, 'market_backup_phase'):
                guard.market_backup({}, 'a' * 64)
            launch.assert_not_called()

    def test_store_root_symlink_to_same_device_inode_never_satisfies_binding(self):
        with tempfile.TemporaryDirectory(prefix='.store-root-fixture-', dir=REPO) as directory:
            root = Path(directory) / 'store'; root.mkdir(mode=0o700)
            value = root.stat()
            entry = {'path': str(root), 'device': value.st_dev, 'inode': value.st_ino, 'uuid': 'fixture-uuid'}
            with patch.object(guard.B, 'command', return_value='fixture-uuid'):
                guard.store_root_contract(entry)
            alias = Path(directory) / 'alias'; alias.symlink_to(root, target_is_directory=True)
            with patch.object(guard.B, 'command') as native, self.assertRaises((guard.P.Refusal, guard.B.Refusal)):
                guard.store_root_contract(dict(entry, path=str(alias)))
            native.assert_not_called()
            root.chmod(0o777)
            with patch.object(guard.B, 'command') as native, self.assertRaises((guard.P.Refusal, guard.B.Refusal)):
                guard.store_root_contract(entry)
            native.assert_not_called()

    def test_unsafe_control_parent_is_refused_before_reservation_open_or_publish(self):
        with tempfile.TemporaryDirectory(prefix='.control-parent-fixture-', dir=REPO) as directory:
            real = Path(directory) / 'real'; real.mkdir(mode=0o700)
            alias = Path(directory) / 'alias'; alias.symlink_to(real, target_is_directory=True)
            plan = {'schema': 'web_engine_transaction_plan/1', 'operation_id': 'fixture', 'nonce': 'a' * 32}
            for target in (alias, real):
                if target == real: real.chmod(0o777)
                with patch.object(guard, 'ROOT', target), patch.object(guard, 'native_contract'), \
                        patch.object(guard.os, 'open') as opened, patch.object(guard.B, 'publish_noreplace') as publish:
                    with self.assertRaises((guard.P.Refusal, guard.B.Refusal)):
                        guard.invariant_contract(plan, 'b' * 64, owned=False)
                    opened.assert_not_called(); publish.assert_not_called()

    def test_ansible_choreography_never_recreates_or_reboots_and_uses_only_exact_ids(self):
        source = (HERE / 'web_engine_transaction.yml').read_text()
        self.assertLess(source.index('phase, quiesced'), source.index('guard }}", backup'))
        self.assertLess(source.index('guard }}", backup'), source.index('guard }}", install'))
        self.assertLess(source.index('phase, engines_verified'), source.index('Start only the prior-running exact datastores'))
        self.assertIn("selectattr('running', 'equalto', true)", source)
        self.assertIn("rejectattr('id', 'in', runtime.datastore_ids)", source)
        for prohibited in ['compose up', 'docker run', 'docker rm', 'prune', 'reboot:', 'ansible.builtin.shell:']:
            self.assertNotIn(prohibited, source)


class EncryptedOffhostReceiverTests(unittest.TestCase):
    def exercise(self, *, data=b'fixture ciphertext only', supplied=None, size=None, digest=None, verify_after=True):
        root = Path(self.directory.name)
        payload = data if supplied is None else supplied
        size = len(data) if size is None else size
        digest = sha256(data).hexdigest() if digest is None else digest
        value = receiver.transfer('fixture', 'a' * 32, size, digest, 'b' * 64, io.BytesIO(payload), root=root)
        if verify_after:
            receiver.transfer('fixture', 'a' * 32, size, digest, 'b' * 64, io.BytesIO(), verify=True, root=root)
        return value

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='.offhost-fixture-', dir=REPO)
        self.addCleanup(self.directory.cleanup)
        capacity = type('Capacity', (), {'f_bavail': 8 * 1024**3, 'f_frsize': 1})()
        volume = patch.object(receiver.os, 'statvfs', return_value=capacity)
        volume.start(); self.addCleanup(volume.stop)

    def test_actual_transfer_fsync_verify_and_no_plaintext_or_overwrite(self):
        value = self.exercise()
        self.assertTrue(value['fsync_completed'])
        self.assertFalse(value['plaintext_materialized_offhost'])
        with self.assertRaises(FileExistsError): self.exercise()
        directory = Path(self.directory.name) / ('security-web-engine-fixture-' + 'a' * 32)
        self.assertEqual((directory / 'engine.enc').stat().st_mode & 0o777, 0o600)
        self.assertEqual(directory.stat().st_mode & 0o777, 0o700)
        (directory / 'engine.enc').write_bytes(b'changed')
        with self.assertRaises(RuntimeError):
            receiver.transfer('fixture', 'a' * 32, value['ciphertext_size_bytes'], value['ciphertext_sha256'], 'b' * 64, io.BytesIO(), verify=True, root=Path(self.directory.name))

    def test_truncated_oversize_or_wrong_digest_refuses_and_retains_interrupted_directory(self):
        for payload in [b'short', b'fixture ciphertext only plus unapproved bytes']:
            with tempfile.TemporaryDirectory(prefix='.offhost-fixture-', dir=REPO) as directory:
                with self.assertRaises(RuntimeError):
                    receiver.transfer('fixture', 'a' * 32, len(b'fixture ciphertext only'), sha256(b'fixture ciphertext only').hexdigest(), 'b' * 64, io.BytesIO(payload), root=Path(directory))
                owned = Path(directory) / ('security-web-engine-fixture-' + 'a' * 32)
                self.assertTrue(owned.is_dir()); self.assertFalse((owned / 'received.json').exists())
        with self.assertRaises(RuntimeError): self.exercise(digest='0' * 64)

    def test_remote_space_floor_no_directory_created_on_refusal(self):
        capacity = type('Capacity', (), {'f_bavail': 1, 'f_frsize': 4096})()
        with patch.object(receiver.os, 'statvfs', return_value=capacity), self.assertRaises(RuntimeError):
            self.exercise()
        self.assertEqual(list(Path(self.directory.name).iterdir()), [])

    def test_invalid_identity_or_size_cannot_escape_backup_root(self):
        for op, nonce, size in [('../escape', 'a' * 32, 5), ('fixture', '../escape', 5), ('fixture', 'a' * 32, receiver.MAXIMUM + 1)]:
            with self.assertRaises(RuntimeError):
                receiver.transfer(op, nonce, size, 'a' * 64, 'b' * 64, io.BytesIO(b'bytes'), root=Path(self.directory.name))
        self.assertEqual(list(Path(self.directory.name).iterdir()), [])


if __name__ == '__main__': unittest.main()
