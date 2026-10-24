"""Synthetic, bounded transaction/backup tests: no live Docker, SSH or apt."""
from copy import deepcopy
import argparse
import ast
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

import yaml

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
    def setUp(self):
        # Every production metadata/command seam is denied unless a specific
        # synthetic fixture below overrides it. Never fall through to systemctl.
        blocked = patch.object(guard.B, 'command', side_effect=AssertionError('unmocked native command'))
        blocked.start(); self.addCleanup(blocked.stop)
        for target in ('socket.socket.connect', 'socket.create_connection', 'socket.getaddrinfo'):
            blocked = patch(target, side_effect=AssertionError('unmocked network'))
            blocked.start(); self.addCleanup(blocked.stop)
        original = subprocess.Popen
        def owned_child_only(argv, *args, **kwargs):
            if argv != [sys.executable, '-I', '-B', '-c', 'import time;time.sleep(30)']:
                raise AssertionError('unmocked host process')
            return original(argv, *args, **kwargs)
        blocked = patch.object(subprocess, 'Popen', side_effect=owned_child_only)
        blocked.start(); self.addCleanup(blocked.stop)

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
                if failure == 'unproven': raise guard.StageLivenessError('synthetic_populated_scope')
                if failure: raise RuntimeError('fixture child crash')
                tag = argv[-1]
                self.assertEqual(argv[-2], '--tags')
                if tag == 'pre_install':
                    self.assertEqual(deadline, '2098-12-31T23:25:00Z')
                    phases = guard.PHASES[1:guard.PHASES.index('installing')]
                else:
                    self.assertEqual(tag, 'install_restore')
                    self.assertEqual(deadline, plan['deadline_utc'])
                    phases = guard.PHASES[guard.PHASES.index('installing'):]
                for phase in phases: guard.advance(plan, 'b' * 64, phase)
                return 0
            def stage_child(plan, digest, argv, env, deadline, descriptors, stage):
                return child([*argv, '--tags', stage], env, deadline, lock_fds=descriptors)
            with patch.object(guard, 'ROOT', root), patch.object(guard.P, 'MARKER', marker), patch.object(guard.P, 'RESERVATION', reservation), \
                    patch.object(guard, 'invariant_contract'), patch.object(guard, 'runtime_contract'), patch.object(guard, 'verify_private_inputs'), \
                    patch.object(guard, 'package_versions', return_value={n: p[0] for n, p in guard.P.OLD_PACKAGES.items()}), \
                    patch.object(guard.B, 'command', return_value=''), patch.object(guard, 'apt_scope'), patch.object(guard, 'offhost'), \
                    patch.object(guard.os, 'statvfs', return_value=type('Capacity', (), {'f_bavail': 100 * 1024**3, 'f_frsize': 1})()), \
                    patch.object(guard, 'execute_stage', side_effect=stage_child), patch.object(guard.B, 'publish_noreplace', side_effect=publish), \
                    patch.object(guard, 'abort_restore', wraps=guard.abort_restore) as abort, patch('builtins.print'):
                if failure or race:
                    with self.assertRaises((RuntimeError, guard.B.Refusal)): guard.run(plan, root / 'manifest', 'b' * 64, True)
                    self.assertTrue(reservation.exists())
                    if race: self.assertEqual(reservation.read_bytes(), b'foreign owner\n'); self.assertEqual(called, [])
                    else:
                        self.assertEqual(guard.journal(plan, 'b' * 64)['phase'], 'reserved')
                        self.assertEqual(guard.B.document(reservation)['nonce'], plan['nonce'])
                        if failure == 'unproven': abort.assert_not_called()
                else:
                    guard.run(plan, root / 'manifest', 'b' * 64, True)
                    self.assertFalse(reservation.exists()); self.assertFalse(guard.paths(plan)[1].exists())
                    self.assertEqual(guard.journal(plan, 'b' * 64)['phase'], 'complete')
            self.assertEqual(marker.read_bytes(), b'unchanged historical marker\n')

    def test_real_owned_flocks_inherited_as_three_descriptors_and_released_only_after_complete(self):
        self.owned_transaction()

    def test_actual_child_crash_retains_reservation_journal_and_historical_marker(self):
        self.owned_transaction(failure=True)

    def test_unproven_scope_liveness_never_attempts_automatic_runtime_restore(self):
        self.owned_transaction(failure='unproven')

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

    def test_start_requires_sixtyfive_minutes_before_any_reservation(self):
        plan = {'new_packages': [], 'backup_root': '/private/backup',
                'deadline_utc': (datetime.now(timezone.utc) + timedelta(minutes=64)).isoformat()}
        with patch.object(guard, 'invariant_contract'), patch.object(guard, 'runtime_contract'), \
                patch.object(guard, 'package_versions', return_value={n: p[0] for n, p in guard.P.OLD_PACKAGES.items()}), \
                patch.object(guard.B, 'command', return_value=''), patch.object(guard, 'apt_scope'), \
                patch.object(guard.os.path, 'lexists', return_value=False), patch.object(guard.os, 'open') as opened, \
                patch.object(guard.os, 'statvfs', return_value=type('Capacity', (), {'f_bavail': 100 * 1024**3, 'f_frsize': 1})()), \
                patch.object(guard, 'verify_private_inputs'), patch.object(guard, 'offhost'), \
                patch.object(guard.B, 'execute_ansible') as child:
            with self.assertRaisesRegex(guard.P.Refusal, 'insufficient_restore_window'):
                guard.run(plan, Path('/private/manifest'), 'a' * 64, True)
            opened.assert_not_called(); child.assert_not_called()

    def test_last_thirty_minute_check_is_before_package_phase_or_any_write(self):
        plan = {'backup_root': '/private/fixture',
                'deadline_utc': (datetime.now(timezone.utc) + timedelta(minutes=29)).isoformat()}
        value = {'phase': 'backup_ready', 'encryption': {'ciphertext_size_bytes': 1, 'ciphertext_sha256': 'a' * 64}}
        with patch.object(guard, 'invariant_contract'), patch.object(guard, 'engine_stopped'), \
                patch.object(guard, 'journal', return_value=value), \
                patch.object(guard.P, 'file_digest', return_value=(1, 'a' * 64)), \
                patch.object(guard, 'offhost'), patch.object(guard, 'apt_argv', return_value=['/fixture/apt']), \
                patch.object(guard.B, 'command', return_value=''), patch.object(guard, 'apt_scope'), \
                patch.object(guard.os.path, 'lexists', return_value=False), patch.object(guard, 'vendor_configs', return_value={}), \
                patch.object(guard, 'advance') as advance, patch.object(guard.os, 'open') as opened:
            with self.assertRaisesRegex(guard.P.Refusal, 'insufficient_restore_window'):
                guard.install(plan, 'b' * 64)
            advance.assert_not_called(); opened.assert_not_called()

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

    def test_market_backup_cli_matches_real_parser_and_required_receipt_basename(self):
        backup_tree = ast.parse((REPO / 'scripts/backup_market_pipeline_archive.py').read_text())
        parser_function = next(node for node in backup_tree.body if isinstance(node, ast.FunctionDef) and node.name == 'build_parser')
        namespace = {'argparse': argparse, 'Path': Path, '__doc__': 'synthetic contract'}
        exec(compile(ast.Module(body=[parser_function], type_ignores=[]), '<actual-backup-parser>', 'exec'), namespace)
        create = next(node for node in backup_tree.body if isinstance(node, ast.FunctionDef) and node.name == 'create_backup')
        receipt_condition = next(node.test for node in create.body if isinstance(node, ast.If)
                                 and any(isinstance(part, ast.Attribute) and isinstance(part.value, ast.Name)
                                         and part.value.id == 'receipt' and part.attr == 'name' for part in ast.walk(node.test)))
        controller_tree = ast.parse((HERE / 'web_engine_controller.py').read_text())
        market = next(node for node in controller_tree.body if isinstance(node, ast.FunctionDef) and node.name == 'market_backup')
        argument = next(node.value for node in market.body if isinstance(node, ast.Assign)
                        and any(isinstance(target, ast.Name) and target.id == 'args' for target in node.targets))
        root = Path('/fixture/owned-backup')
        options = {'release_sha': 'a' * 40, 'release_tree': 'b' * 40,
                   'image_id': 'sha256:' + 'c' * 64, 'image_input_signature': 'd' * 64}
        argv = eval(compile(ast.Expression(argument), '<actual-controller-argv>', 'eval'),
                    {'root': root, 'env_path': Path('/fixture/private-role'), 'options': options})
        parser = namespace['build_parser']()
        parsed = parser.parse_args(['create', *argv, '--backup-dir', str(root / 'market'),
                                    '--confirm', 'create-production-market-pipeline-archive-backup'])
        check = compile(ast.Expression(receipt_condition), '<actual-receipt-condition>', 'eval')
        self.assertFalse(eval(check, {'receipt': parsed.receipt, 'root': parsed.backup_dir}))
        self.assertTrue(eval(check, {'receipt': parsed.backup_dir / 'receipt.json', 'root': parsed.backup_dir}))
        self.assertEqual(parsed.receipt.name, 'market-pipeline-backup-receipt.json')
        self.assertTrue(parsed.bluegreen_source_env)
        parser.parse_args(['verify', *argv, '--maximum-age-seconds', '3600'])
        with patch.object(guard, 'journal', return_value={'market_backup': {'receipt_sha256': 'e' * 64, 'artifact_identity': []}}), \
                patch.object(guard.P, 'document', return_value={}) as read, patch.object(guard, 'market_contract'):
            guard.market_document({'backup_root': str(root)}, 'f' * 64)
        self.assertEqual(read.call_args.args[0], parsed.receipt)

    def test_market_provenance_anchor_must_be_actual_active_market_image_and_exact_oci_tuple(self):
        baseline = rows(); selected = next(row for row in baseline if row['project'] == 'market-private-pipeline-primary' and row['running'])
        options = {'release_sha': 'a' * 40, 'release_tree': 'b' * 40,
                   'image_id': selected['image'], 'image_input_signature': 'c' * 64}
        plan = {'containers': baseline, 'market_backup': options}
        labels = {'revision': options['release_sha'], 'tree': options['release_tree'], 'input_signature': options['image_input_signature']}
        with patch.object(guard.B, 'command', return_value=json.dumps(labels)) as metadata:
            verified = guard.market_anchor_contract(plan)
        self.assertEqual(metadata.call_args.args[0][:3], ['/usr/bin/docker', 'image', 'inspect'])
        self.assertNotIn('.Config.Env', metadata.call_args.args[0][-2])
        for change in ({'input_signature': ''}, {'revision': 'f' * 40}, {'tree': None}):
            with patch.object(guard.B, 'command', return_value=json.dumps({**labels, **change})), self.assertRaisesRegex(guard.P.Refusal, 'provenance_changed'):
                guard.market_anchor_contract(plan)
        with patch.object(guard.B, 'command') as metadata, self.assertRaisesRegex(guard.P.Refusal, 'not_active_baseline_image'):
            guard.market_anchor_contract({'containers': baseline, 'market_backup': {**options, 'image_id': 'sha256:' + 'f' * 64}})
        metadata.assert_not_called()
        with patch.object(guard.B, 'command') as metadata:
            self.assertEqual(guard.market_anchor_contract(plan, verified=verified), verified)
        metadata.assert_not_called()
        with patch.object(guard.B, 'command') as metadata, self.assertRaisesRegex(guard.P.Refusal, 'recorded_provenance_changed'):
            guard.market_anchor_contract(plan, verified={**verified, 'input_signature': ''})
        metadata.assert_not_called()

    def test_only_owned_stopped_engine_phases_accept_prior_quiescent_anchor_proof(self):
        proof = {'image_id': 'sha256:' + 'a' * 64, 'revision': 'b' * 40, 'tree': 'c' * 40, 'input_signature': 'd' * 64}
        for phase in ('quiesced', 'backing_up', 'backup_ready', 'installing', 'installed'):
            with patch.object(guard, 'journal', return_value={'phase': phase, 'market_anchor_provenance': proof}), \
                    patch.object(guard, 'market_anchor_contract') as verify:
                guard.market_anchor_invariant({}, 'e' * 64, owned=True)
            verify.assert_called_once_with({}, verified=proof)
            with patch.object(guard, 'journal', return_value={'phase': phase}), patch.object(guard, 'market_anchor_contract') as verify:
                with self.assertRaisesRegex(guard.P.Refusal, 'quiescent_proof_missing'):
                    guard.market_anchor_invariant({}, 'e' * 64, owned=True)
                verify.assert_not_called()
        for phase in ('reserved', 'market_backed_up', 'engines_verified', 'datastores', 'healthy', 'complete'):
            with patch.object(guard, 'journal', return_value={'phase': phase, 'market_anchor_provenance': proof}), \
                    patch.object(guard, 'market_anchor_contract') as verify:
                guard.market_anchor_invariant({}, 'e' * 64, owned=True)
            verify.assert_called_once_with({}, verified=None)
        with patch.object(guard, 'journal') as journal, patch.object(guard, 'market_anchor_contract') as verify:
            guard.market_anchor_invariant({}, 'e' * 64, owned=False)
        journal.assert_not_called(); verify.assert_called_once_with({}, verified=None)

    def test_quiesced_checkpoint_records_actual_anchor_before_engines_are_stopped(self):
        proof = {'image_id': 'sha256:' + 'a' * 64, 'revision': 'b' * 40, 'tree': 'c' * 40, 'input_signature': 'd' * 64}
        with patch.object(guard, 'invariant_contract'), patch.object(guard, 'runtime_contract') as runtime, \
                patch.object(guard, 'journal', return_value={'phase': 'market_backed_up'}), \
                patch.object(guard, 'market_anchor_contract', return_value=proof) as anchor, patch.object(guard, 'advance') as advance:
            guard.checkpoint({}, 'e' * 64, 'quiesced')
        runtime.assert_called_once_with({}, 'quiesced'); anchor.assert_called_once_with({})
        advance.assert_called_once_with({}, 'e' * 64, 'quiesced', market_anchor_provenance=proof)

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
        tasks = yaml.safe_load(source)[0]['tasks']
        boundary = next(i for i, task in enumerate(tasks) if task['name'].startswith('Reverify offhost evidence and install'))
        self.assertTrue(all(task['tags'] == ['always'] for task in tasks[:3]))
        self.assertTrue(all(task['tags'] == ['pre_install'] for task in tasks[3:boundary]))
        self.assertTrue(all(task['tags'] == ['install_restore'] for task in tasks[boundary:]))


class PreinstallAbortTests(unittest.TestCase):
    setUp = FiniteWebEngineTests.setUp
    def fixture(self, *, phase='backup_ready', altered=False, smoke_failure=False):
        baseline = rows()
        current = deepcopy(baseline)
        for row in current:
            row['running'] = False; row['restart_policy'] = {'Name': 'no', 'MaximumRetryCount': 0}
        if altered: current[0]['image'] = 'sha256:' + 'f' * 64
        expected = {row['id']: row for row in baseline}
        observed = {row['id']: row for row in current}
        plan = {'operation_id': 'fixture', 'nonce': 'a' * 32, 'deadline_utc': '2099-01-01T00:00:00Z',
            'containers': baseline, 'datastore_ids': [r['id'] for r in baseline if r['running']][:8],
            'engine_intent': [{'name': unit, 'active': True, 'enabled': 'enabled'} for unit in [*guard.ENGINES, 'nginx.service']],
            'units': [{'name': name, 'kind': 'timer' if name.endswith('.timer') else 'daemon', 'active': True, 'enabled': 'enabled'}
                      for name in sorted(guard.UNITS)],
            'health_probes': [{'id': baseline[0]['id'], 'url': 'http://127.0.0.1:8000/api/config'}]}
        plan['market_backup'] = {'image_id': baseline[0]['image'], 'release_sha': 'a' * 40,
                                 'release_tree': 'b' * 40, 'image_input_signature': 'c' * 64}
        plan['datastore_probes'] = [{'id': ident, 'kind': 'redis'} for ident in plan['datastore_ids']]
        calls, active = [], set()
        def command(argv):
            calls.append(argv)
            if argv[0] == '/usr/bin/systemctl':
                if argv[1] == 'start': active.add(argv[-1]); return ''
                return 'enabled' if 'UnitFileState' in argv else 'active'
            if argv[1:3] == ['image', 'inspect']:
                return json.dumps({'revision': 'a' * 40, 'tree': 'b' * 40, 'input_signature': 'c' * 64})
            if argv[1] == 'ps': return '\n'.join(observed)
            if argv[1] == 'inspect': return json.dumps(observed[argv[-1]])
            if argv[1] == 'info':
                if '.CgroupDriver' in argv[-1]: return '{"driver":"systemd","version":"2"}'
                return '{"root":"/var/lib/docker","driver":"overlayfs","driver_status":[["driver-type","io.containerd.snapshotter.v1"]],"live_restore":false}'
            if argv[1] == 'start': observed[argv[2]]['running'] = True; return ''
            if argv[1] == 'update':
                observed[argv[-1]]['restart_policy'] = deepcopy(expected[argv[-1]]['restart_policy']); return ''
            if argv[1] == 'exec':
                if 'redis-cli' in argv: return 'PONG'
                if smoke_failure: raise guard.B.Refusal('synthetic_old_api_failure')
                return '200'
            raise AssertionError('unknown fixture command')
        return plan, observed, calls, command

    def test_preinstall_restore_preserves_all_twentyseven_ids_and_orders_data_api_policies_timers(self):
        plan, current, calls, command = self.fixture()
        with patch.object(guard, 'invariant_contract') as invariant, \
                patch.object(guard, 'journal', return_value={'phase': 'backup_ready'}) as journal, \
                patch.object(guard, 'package_versions', return_value={n: p[0] for n, p in guard.P.OLD_PACKAGES.items()}), \
                patch.object(guard.B, 'command', side_effect=command), patch.object(guard, 'containerd_contract'), \
                patch.object(guard, 'write_json') as write:
            guard.abort_restore(plan, 'b' * 64)
        self.assertEqual(set(current), {row['id'] for row in plan['containers']})
        self.assertEqual(sum(row['running'] for row in current.values()), 26)
        self.assertFalse(next(row for row in current.values() if row['name'].endswith('migration-1'))['running'])
        starts = [call for call in calls if call[:2] == ['/usr/bin/docker', 'start']]
        self.assertEqual([call[2] for call in starts[:8]], plan['datastore_ids'])
        first_app = next(i for i, call in enumerate(calls) if call[:2] == ['/usr/bin/docker', 'start'] and call[2] not in plan['datastore_ids'])
        self.assertTrue(any('redis-cli' in call for call in calls[:first_app]))
        first_policy = next(i for i, call in enumerate(calls) if call[:2] == ['/usr/bin/docker', 'update'])
        self.assertTrue(any(guard.B.CONTAINER_HTTP_PROBE in call for call in calls[:first_policy]))
        timer = next(i for i, call in enumerate(calls) if call[:2] == ['/usr/bin/systemctl', 'start'] and call[2].endswith('.timer'))
        self.assertGreater(timer, max(i for i, call in enumerate(calls) if call[:2] == ['/usr/bin/docker', 'update']))
        recorded = write.call_args.args[1]
        self.assertEqual(recorded['phase'], 'aborted_restored')
        self.assertFalse(recorded['security_transition_completed'])
        self.assertEqual(recorded['aborted_from'], 'backup_ready')
        self.assertEqual(invariant.call_count, 2)

    def test_post_package_boundary_or_old_package_mismatch_never_attempts_restore(self):
        plan, current, calls, command = self.fixture()
        for phase, versions in [('installing', {n: p[0] for n, p in guard.P.OLD_PACKAGES.items()}), ('backup_ready', {})]:
            with patch.object(guard, 'invariant_contract'), patch.object(guard, 'journal', return_value={'phase': phase}), \
                    patch.object(guard, 'package_versions', return_value=versions), \
                    patch.object(guard.B, 'command', side_effect=command), patch.object(guard, 'write_json') as write:
                with self.assertRaises(guard.P.Refusal): guard.abort_restore(plan, 'b' * 64)
                self.assertEqual(calls, []); write.assert_not_called()

    def test_changed_authority_or_unexpected_native_writer_refuses_before_restore_writes(self):
        plan, current, calls, command = self.fixture()
        with patch.object(guard, 'invariant_contract', side_effect=guard.P.Refusal('synthetic_marker_drift')), \
                patch.object(guard.B, 'command', side_effect=command), patch.object(guard, 'write_json') as write:
            with self.assertRaisesRegex(guard.P.Refusal, 'marker_drift'): guard.abort_restore(plan, 'b' * 64)
            self.assertEqual(calls, []); write.assert_not_called()
        plan['units'][0]['active'] = False
        with patch.object(guard, 'invariant_contract'), patch.object(guard, 'journal', return_value={'phase': 'backup_ready'}), \
                patch.object(guard, 'package_versions', return_value={n: p[0] for n, p in guard.P.OLD_PACKAGES.items()}), \
                patch.object(guard.B, 'command', side_effect=command), patch.object(guard, 'write_json') as write:
            with self.assertRaisesRegex(guard.P.Refusal, 'abort_unexpected_native_writer'):
                guard.abort_restore(plan, 'b' * 64)
            self.assertFalse(any(call[1] == 'start' for call in calls)); write.assert_not_called()

    def test_changed_identity_refuses_before_any_container_writer_or_policy_restore(self):
        plan, current, calls, command = self.fixture(altered=True)
        with patch.object(guard, 'invariant_contract'), patch.object(guard, 'journal', return_value={'phase': 'backup_ready'}), \
                patch.object(guard, 'package_versions', return_value={n: p[0] for n, p in guard.P.OLD_PACKAGES.items()}), \
                patch.object(guard.B, 'command', side_effect=command), patch.object(guard, 'write_json') as write:
            with self.assertRaisesRegex(guard.P.Refusal, 'abort_container_identity'):
                guard.abort_restore(plan, 'b' * 64)
            self.assertFalse(any(call[:2] in [['/usr/bin/docker', 'start'], ['/usr/bin/docker', 'update']] for call in calls))
            write.assert_not_called()

    def test_failed_smoke_does_not_restore_policies_timers_or_publish_success(self):
        plan, current, calls, command = self.fixture(smoke_failure=True)
        with patch.object(guard, 'invariant_contract'), patch.object(guard, 'journal', return_value={'phase': 'backup_ready'}), \
                patch.object(guard, 'package_versions', return_value={n: p[0] for n, p in guard.P.OLD_PACKAGES.items()}), \
                patch.object(guard.B, 'command', side_effect=command), patch.object(guard, 'containerd_contract'), \
                patch.object(guard.time, 'sleep'), patch.object(guard, 'write_json') as write:
            with self.assertRaisesRegex(guard.B.Refusal, 'synthetic_old_api_failure'):
                guard.abort_restore(plan, 'b' * 64)
            self.assertFalse(any(call[:2] == ['/usr/bin/docker', 'update'] for call in calls))
            self.assertFalse(any(call[:2] == ['/usr/bin/systemctl', 'start'] and call[2].endswith('.timer') for call in calls))
            write.assert_not_called()


class StageScopeTests(unittest.TestCase):
    setUp = FiniteWebEngineTests.setUp

    def exercise(self, *, timeout=False, populated=False, foreign=False, missing=False):
        plan = {'operation_id': 'fixture', 'nonce': 'a' * 32}
        digest = 'b' * 64; stage = 'pre_install'
        unit = 'security-web-engine-' + plan['nonce'] + '-' + stage + '.scope'
        description = ':'.join(('web-engine', 'fixture', plan['nonce'], digest, stage))
        states = iter(['not-found', 'not-found' if missing else 'loaded'])
        commands = []
        def command(argv):
            commands.append(argv)
            if 'LoadState' in argv: return next(states)
            if argv[1] == 'stop': return ''
            return '\n'.join(('Description=' + ('foreign' if foreign else description),
                'ControlGroup=/system.slice/' + unit, 'Transient=yes', 'ActiveState=active',
                'KillMode=control-group', 'Slice=system.slice'))
        failure = subprocess.TimeoutExpired(['/fixture/ansible'], 1) if timeout else None
        with patch.object(guard.B, 'command', side_effect=command), patch.object(guard.os.path, 'lexists', return_value=False), \
                patch.object(guard, 'scope_empty', return_value=not populated) as empty, \
                patch.object(guard.B, 'execute_ansible', side_effect=failure, return_value=0) as execute:
            if foreign or populated:
                with self.assertRaises(guard.StageLivenessError):
                    guard.execute_stage(plan, digest, ['/fixture/ansible'], {'SAFE': 'fixture'}, '2099-01-01T00:00:00Z', [7, 8, 9], stage)
            elif timeout:
                with self.assertRaises(subprocess.TimeoutExpired):
                    guard.execute_stage(plan, digest, ['/fixture/ansible'], {'SAFE': 'fixture'}, '2099-01-01T00:00:00Z', [7, 8, 9], stage)
            else:
                self.assertEqual(guard.execute_stage(plan, digest, ['/fixture/ansible'], {'SAFE': 'fixture'}, '2099-01-01T00:00:00Z', [7, 8, 9], stage), 0)
        argv = execute.call_args.args[0]
        for required in ('--scope', '--system', '--expand-environment=no', '--slice=system.slice',
                         '--property=KillMode=control-group', '--property=SendSIGKILL=yes', '--property=TimeoutStopSec=10s'):
            self.assertIn(required, argv)
        self.assertEqual(argv[-4:], ['--', '/fixture/ansible', '--tags', stage])
        self.assertNotIn('--wait', argv)
        self.assertEqual(execute.call_args.kwargs['lock_fds'], [7, 8, 9])
        self.assertTrue(any(value.startswith('--property=RuntimeMaxSec=') for value in argv))
        stopped = ['/usr/bin/systemctl', 'stop', unit] in commands
        self.assertEqual(stopped, not foreign and not missing)
        if foreign: empty.assert_not_called()

    def test_scope_preserves_lock_descriptors_and_proves_empty_on_normal_completion(self):
        self.exercise()

    def test_scope_timeout_reaps_whole_owned_cgroup_before_rescue_can_be_considered(self):
        self.exercise(timeout=True)

    def test_populated_or_foreign_scope_fails_closed_without_unsafe_restore_or_foreign_stop(self):
        self.exercise(populated=True)
        self.exercise(foreign=True)
        self.exercise(missing=True, populated=True)

    def test_prior_scope_or_cgroup_is_never_adopted_or_executed(self):
        with patch.object(guard.B, 'command', return_value='loaded'), patch.object(guard.B, 'execute_ansible') as execute:
            with self.assertRaisesRegex(guard.StageLivenessError, 'prior_stage_scope_present'):
                guard.execute_stage({'operation_id': 'fixture', 'nonce': 'a' * 32}, 'b' * 64, [], {}, '2099-01-01T00:00:00Z', [], 'pre_install')
        execute.assert_not_called()

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
