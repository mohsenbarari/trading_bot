"""Web prep only: no network, host processes, env/key reads or app imports."""
from contextlib import ExitStack
import ast
from datetime import datetime, timezone
from hashlib import sha256
import importlib.util
import io
import json
import os
from pathlib import Path
import socket
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from jinja2 import Environment, StrictUndefined
import yaml

REPO = Path(__file__).resolve().parents[1]
HERE = REPO / 'deploy/maintenance'
spec = importlib.util.spec_from_file_location('_web_engine_preparation_test', HERE / 'web_engine_prepare.py')
R = importlib.util.module_from_spec(spec); spec.loader.exec_module(R)
spec = importlib.util.spec_from_file_location('_web_engine_preparation_pins', HERE / 'web_engine_preflight.py')
P = importlib.util.module_from_spec(spec); spec.loader.exec_module(P)


class Clock(datetime):
    @classmethod
    def now(cls, tz=None): return datetime(2026, 10, 24, 3, 11, tzinfo=timezone.utc)


class WebEnginePreparationTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack(); self.addCleanup(self.stack.close)
        self.temp = Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix='.web-prep-fixture-', dir=REPO)))
        self.packages = self.temp / 'packages'; self.packages.mkdir(mode=0o700)
        self.stack.enter_context(patch.object(R, 'PACKAGES', self.packages))
        self.stack.enter_context(patch.object(R, 'ROOT', self.temp))
        self.stack.enter_context(patch.object(R, 'PLAN', self.temp / 'plan.json'))
        self.stack.enter_context(patch.object(R, 'INPUTS', self.temp / 'inputs.json'))
        self.stack.enter_context(patch.object(R, 'datetime', Clock))
        self.stack.enter_context(patch.object(R, 'parents', side_effect=lambda p:
            R.require(p.is_relative_to(self.temp) and p.resolve(strict=False) == p, 'outside_fixture')))
        for target in ('socket.socket.connect', 'socket.create_connection', 'socket.getaddrinfo', 'subprocess.Popen', 'subprocess.run'):
            self.stack.enter_context(patch(target, side_effect=AssertionError('no real network or host command')))
        self.open_network = self.stack.enter_context(patch.object(R.urllib.request, 'build_opener', side_effect=AssertionError('no network opener')))
        self.inputs = {'schema': 'web_engine_prepare_inputs/1', 'operation_id': 'security-web-engine-20261023-01',
            'nonce': 'a' * 32, 'deadline_utc': '2026-10-24T04:41:00Z',
            'source_sha256': {name: 'b' * 64 for name in R.PUBLIC_SOURCES},
            'app_backup_receipt': {'source_path': R.RECEIPTS['app_backup_receipt'][0], 'path': str(R.RECEIPTS['app_backup_receipt'][1]), 'sha256': 'c' * 64},
            'app_offhost_receipt': {'source_path': R.RECEIPTS['app_offhost_receipt'][0], 'path': str(R.RECEIPTS['app_offhost_receipt'][1]), 'sha256': 'd' * 64},
            'market_backup': {'env_file': '/private/metadata-only.env', 'release_sha': 'e' * 40, 'release_tree': 'f' * 40,
                'image_id': 'sha256:' + '1' * 64, 'image_input_signature': '2' * 64},
            'backup_key_file': '/private/metadata-only.key',
            'offhost': {'identity_file': '/root/.ssh/security-controller-20261023-host-patch-03/id_ed25519',
                'known_hosts_file': '/root/.ssh/security-controller-20261023-host-patch-03/known_hosts', 'known_hosts_sha256': '3' * 64},
            'datastore_probes': [{'name': '/fixture-db' + str(n), 'kind': 'postgres', 'user': 'fixture', 'database': 'fixture'} for n in range(8)]}
        self.inputs['source_sha256']['deploy/maintenance/web_engine_controller.py'] = R.CONTROLLER_SHA

    def tiny_controller(self, payload=b'public tiny deb fixture'):
        pins = {'docker.io': ('29.1.3-0ubuntu4.1', len(payload), sha256(payload).hexdigest())}
        def write_all(fd, data):
            while data:
                count = os.write(fd, data); self.assertGreater(count, 0); data = data[count:]
        def digest(path, **kwargs):
            raw = R.read(Path(path), maximum=kwargs['maximum'], private=kwargs['private'])
            return len(raw), sha256(raw).hexdigest()
        return SimpleNamespace(P=SimpleNamespace(OLD_PACKAGES=pins, NEW_PACKAGES={}, file_digest=digest), B=SimpleNamespace(write_all=write_all))

    def fake_download(self, body, *, length=None, status=200):
        class Response(io.BytesIO):
            pass
        response = Response(body); response.status = status
        response.headers = {} if length is None else {'Content-Length': str(length)}
        opener = SimpleNamespace(open=Mock(return_value=response))
        self.open_network.side_effect = None; self.open_network.return_value = opener
        return opener

    def test_shape_is_exact_thirteen_sources_and_fresh_lane_without_secrets(self):
        R.validate_inputs(self.inputs)
        self.assertEqual(len(R.PUBLIC_SOURCES), 13)
        self.assertEqual(len(set(R.PUBLIC_SOURCES)), 13)
        for mutation in (lambda x: x.update(password='forbidden'),
                         lambda x: x['source_sha256'].pop('deploy/maintenance/ansible.cfg'),
                         lambda x: x['offhost'].update(identity_file='/root/.ssh/security-controller-20261023-host-patch-02/id_ed25519'),
                         lambda x: x.update(deadline_utc='2026-10-24T04:41:01Z'),
                         lambda x: x.update(deadline_utc='2026-10-24T01:30:00Z')):
            value = json.loads(json.dumps(self.inputs)); mutation(value)
            with self.subTest(mutation=mutation), self.assertRaises(RuntimeError): R.validate_inputs(value)

    def test_wrong_release_control_receipt_prefix_refuses_before_any_transfer_or_read(self):
        self.assertEqual(R.RECEIPTS['app_backup_receipt'][0], '/root/secure-envs/trading-bot/production-backups/evidence/security-host-maintenance-20261023.json')
        value = json.loads(json.dumps(self.inputs))
        value['app_backup_receipt']['source_path'] = '/root/secure-envs/trading-bot/release-control/production-backups/evidence/security-host-maintenance-20261023.json'
        with patch.object(R, 'read') as read, self.assertRaisesRegex(RuntimeError, 'receipt_input'):
            R.validate_inputs(value)
        read.assert_not_called(); self.open_network.assert_not_called()

    def test_native_validation_returns_verified_transfer_pins_without_private_payloads(self):
        payload = json.dumps(self.inputs).encode()
        receipt_hashes = {self.inputs[key]['source_path']: self.inputs[key]['sha256']
                          for key in ('app_backup_receipt', 'app_offhost_receipt')}
        controller = SimpleNamespace(backup_contract=Mock())
        output = io.StringIO()
        with patch.object(R.sys, 'argv', ['web_engine_prepare.py', 'validate-inputs']), \
                patch.object(R, 'read', return_value=payload) as reader, \
                patch.object(R, 'controller', return_value=controller), \
                patch.object(R, 'binding', side_effect=lambda path, **kwargs:
                    {'path': path, 'sha256': receipt_hashes[path], 'size': 123, 'private': True}), \
                patch.object(R.sys, 'stdout', output):
            self.assertEqual(R.main(), 0)
        result = json.loads(output.getvalue())
        reader.assert_called_once_with(R.NATIVE_INPUTS, maximum=65536)
        controller.backup_contract.assert_called_once()
        self.assertEqual(result['source_sha256'], self.inputs['source_sha256'])
        self.assertEqual(result['inputs_sha256'], sha256(payload).hexdigest())
        self.assertEqual(result['receipt_sha256'], {'app-backup-receipt.json': 'c' * 64,
                                                   'app-offhost-receipt.json': 'd' * 64})
        self.assertEqual(set(result), {'status', 'runtime_changed', 'source_sha256',
                                       'inputs_sha256', 'receipt_sha256'})
        self.open_network.assert_not_called()

    def test_nine_package_entries_use_exact_reviewed_pins_and_https_origins(self):
        entries = R.package_entries(SimpleNamespace(P=P))
        self.assertEqual(len(entries), 9)
        self.assertEqual(sum(e['old'] for e in entries), 4)
        for entry in entries:
            pins = P.OLD_PACKAGES if entry['old'] else P.NEW_PACKAGES
            self.assertEqual((entry['version'], entry['size'], entry['sha256']), pins[entry['name']])
            self.assertEqual(Path(entry['path']).parent, self.packages)
            self.assertIn(R.urllib.parse.urlsplit(entry['url']).hostname, {'archive.ubuntu.com', 'security.ubuntu.com', 'download.docker.com'})
            self.assertTrue(entry['url'].startswith('https://'))

    def test_streamed_public_download_is_atomic_exact_and_idempotent(self):
        body = b'public tiny deb fixture'; C = self.tiny_controller(body); entry = R.package_entries(C)[0]
        opener = self.fake_download(body, length=len(body)); R.download(C, entry)
        path = Path(entry['path']); self.assertEqual(path.read_bytes(), body)
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        R.download(C, entry); self.assertEqual(opener.open.call_count, 1)
        self.assertFalse(list(self.packages.glob('.download-*')))

    def test_oversize_truncation_wrong_digest_and_http_status_never_publish(self):
        body = b'expected'; C = self.tiny_controller(body); entry = R.package_entries(C)[0]
        for value, length, status in ((body + b'overflow', None, 200), (body[:-1], None, 200),
                                      (b'wrong!!!', None, 200), (body, 999999, 200), (body, None, 500)):
            with self.subTest(value=value, length=length, status=status):
                self.fake_download(value, length=length, status=status)
                with self.assertRaises(RuntimeError): R.download(C, entry)
                self.assertFalse(Path(entry['path']).exists()); self.assertFalse(list(self.packages.glob('.download-*')))

    def test_unknown_url_pin_or_output_path_refuses_before_network(self):
        C = self.tiny_controller(); entry = R.package_entries(C)[0]
        for alter in ({'url': 'https://attacker.invalid/package.deb'}, {'sha256': '0' * 64}, {'path': str(self.temp / 'outside.deb')}):
            with self.subTest(alter=alter), self.assertRaises(RuntimeError): R.download(C, dict(entry, **alter))
        self.open_network.assert_not_called()

    def test_redirects_cannot_downgrade_or_change_origin_or_add_credentials(self):
        policy = R.SameOrigin(); request = R.urllib.request.Request('https://download.docker.com/pinned.deb')
        for url in ('http://download.docker.com/a', 'https://other.invalid/a',
                    'https://user:password@download.docker.com/a', 'https://download.docker.com/a?query=bad'):
            with self.subTest(url=url), self.assertRaises(RuntimeError): policy.redirect_request(request, None, 302, 'redirect', {}, url)

    def test_metadata_only_private_inputs_never_open_env_or_key(self):
        path = self.temp / 'not-opened.env'; path.write_bytes(b'never read this private fixture'); path.chmod(0o600)
        with patch.object(R.os, 'open', side_effect=AssertionError('must not open private input')):
            self.assertEqual(len(R.private_metadata(path, 1024)), 9)
        path.chmod(0o644)
        with self.assertRaises(RuntimeError): R.private_metadata(path, 1024)

    def test_no_apply_default_or_wrong_baseline_can_download_or_create_plan(self):
        C = self.tiny_controller()
        with patch.object(R, 'snapshot', return_value={}) as snapshot, patch.object(R, 'download') as download:
            self.assertEqual(R.prepare(C, self.inputs, apply=False)['status'], 'metadata_checked')
            snapshot.assert_called_once(); download.assert_not_called()
        self.assertFalse(R.PLAN.exists())
        with patch.object(R, 'snapshot', side_effect=RuntimeError('wrong host')), patch.object(R, 'download') as download:
            with self.assertRaises(RuntimeError): R.prepare(C, self.inputs, apply=True)
            download.assert_not_called()

    def test_capacity_failure_is_before_download_and_plan_publish(self):
        C = self.tiny_controller()
        with patch.object(R, 'snapshot', return_value={}), patch.object(R.os, 'statvfs', return_value=SimpleNamespace(f_bavail=1, f_frsize=1)), patch.object(R, 'download') as download:
            with self.assertRaisesRegex(RuntimeError, 'preparation_capacity'): R.prepare(C, self.inputs, apply=True)
            download.assert_not_called()
        self.assertFalse(R.PLAN.exists())

    def test_preparation_refreezes_actual_facts_after_downloads_before_immutable_publish(self):
        C = self.tiny_controller(); C.invariant_contract = Mock()
        calls = []
        def snapshot(*args): calls.append('snapshot'); return {'actual': True}
        def download(*args): calls.append('download')
        with patch.object(R, 'snapshot', side_effect=snapshot), patch.object(R, 'download', side_effect=download), \
                patch.object(R.os, 'statvfs', return_value=SimpleNamespace(f_bavail=100 * 1024**3, f_frsize=1)):
            result = R.prepare(C, self.inputs, apply=True)
        self.assertEqual(calls, ['snapshot', 'download', 'snapshot'])
        self.assertEqual(result['status'], 'prepared_not_applied')
        self.assertEqual(json.loads(R.PLAN.read_bytes()), {'actual': True})
        C.invariant_contract.assert_called_once_with({'actual': True}, '0' * 64, owned=False)

    def test_actual_snapshot_shape_has_twentyseven_ten_four_eight_and_metadata_only_keys(self):
        source = self.temp / 'source'; source.mkdir(mode=0o700)
        marker = self.temp / 'marker'; marker.write_bytes(b'public authority marker'); marker.chmod(0o600)
        containers = []
        for project, count in P.PROJECT_COUNTS.items():
            for _ in range(count):
                ident = len(containers) + 1
                containers.append({'id': f'{ident:064x}', 'name': '/fixture-' + str(ident),
                    'image': 'sha256:' + '5' * 64, 'project': project, 'running': True,
                    'restart_policy': {'Name': 'unless-stopped', 'MaximumRetryCount': 0}, 'mounts': [], 'health': None,
                    'network_mode': 'fixture', 'port_bindings': {},
                    'network_names': {'fixture-network': {'IPAddress': 'never-persist-this-field'}}})
        aliases = {0: '/fixture-market-database-1', 7: '/trading_bot_app', 8: '/trading_bot_db', 9: '/trading_bot_redis',
            11: '/trading_bot_staging_iran-migration-1', 12: '/iran-db-1', 13: '/iran-redis-1',
            17: '/older-db-1', 18: '/older-alt-db-1', 19: '/older-redis-1'}
        for index, name in aliases.items(): containers[index]['name'] = name
        containers[11]['running'] = False; containers[11]['restart_policy']['Name'] = 'no'
        stores = [r for r in containers if r['name'] in {'/trading_bot_db', '/trading_bot_redis'} or
            r['name'].endswith(('-db-1', '-redis-1', '-market-database-1'))]
        spec = json.loads(json.dumps(self.inputs))
        spec['datastore_probes'] = [{'name': row['name'], 'kind': 'redis'} if 'redis' in row['name'] else
            {'name': row['name'], 'kind': 'postgres', 'user': 'fixture', 'database': 'fixture'} for row in stores]
        policy = ast.parse((HERE / 'web_engine_controller.py').read_text())
        values = {node.targets[0].id: ast.literal_eval(node.value) for node in policy.body if isinstance(node, ast.Assign)
            and isinstance(node.targets[0], ast.Name) and node.targets[0].id in {'UNITS', 'ENGINES'}}
        def command(argv):
            if argv[0] == '/usr/sbin/ip': return json.dumps([{'addr_info': [{'local': '65.109.220.59'}]}])
            if argv[0] == '/usr/bin/findmnt': return 'fixture-uuid'
            if argv[0] == '/usr/bin/docker' and argv[1] == 'ps': return '\n'.join(r['id'] for r in containers)
            if argv[0] == '/usr/bin/docker' and argv[1] == 'inspect': return json.dumps(next(r for r in containers if r['id'] == argv[-1]))
            if argv[0] == '/usr/bin/systemctl':
                name = argv[2]
                if 'FragmentPath' in argv[-1]:
                    return '\n'.join(['FragmentPath=/public/' + name, 'DropInPaths=',
                        'UnitFileState=' + ('enabled' if name.endswith('.timer') else 'static'),
                        'ActiveState=' + ('active' if name.endswith('.timer') else 'inactive'),
                        'Type=' + ('simple' if name.endswith('.timer') else 'oneshot')])
                return 'UnitFileState=enabled\nActiveState=active'
            if argv[-1] == 'check': return 'No broken requirements found.'
            if 'importlib.metadata' in argv[-1]: return '2.21.5'
            raise AssertionError('unexpected command')
        receipt_hashes = {spec[key]['path']: spec[key]['sha256'] for key in ('app_backup_receipt', 'app_offhost_receipt')}
        receipt_hashes[spec['offhost']['known_hosts_file']] = spec['offhost']['known_hosts_sha256']
        def bind(path, *, private):
            return {'path': str(path), 'size': 1, 'sha256': receipt_hashes.get(str(path), '6' * 64), 'private': private}
        C = SimpleNamespace(P=SimpleNamespace(OLD_PACKAGES=P.OLD_PACKAGES, NEW_PACKAGES=P.NEW_PACKAGES,
            MARKER=marker, RESERVATION=self.temp / 'reservation', PENDING=self.temp / 'pending',
            baseline_contract=P.baseline_contract, canonical_mounts=P.canonical_mounts),
            B=SimpleNamespace(command=Mock(side_effect=command)), UNITS=values['UNITS'], ENGINES=values['ENGINES'],
            INSPECT='only-public-fields', native_contract=Mock(), control_parent_contract=Mock(), store_root_contract=Mock(),
            paths=lambda _: (self.temp / 'journal', self.temp / 'source.lock'), backup_contract=Mock(), runtime_contract=Mock(),
            package_versions=Mock(return_value={n: pin[0] for n, pin in P.OLD_PACKAGES.items()}))
        original_lstat, original_text, original_lexists = Path.lstat, Path.read_text, os.path.lexists
        def lstat(path, *args, **kwargs):
            if str(path) in {'/var/lib/docker', '/var/lib/containerd'}: return SimpleNamespace(st_dev=123, st_ino=456)
            return original_lstat(path, *args, **kwargs)
        def text(path, *args, **kwargs):
            return 'fixture-host-id' if str(path) == '/etc/machine-id' else original_text(path, *args, **kwargs)
        def lexists(path):
            if str(path) in {'/root/secure-envs/trading-bot/.production-runtime-source.lock',
                    '/root/secure-envs/trading-bot/market-pipeline-cutover/bot-legacy-handoff-4ef8e6dc.json'}: return False
            return original_lexists(path)
        with patch.object(R, '__file__', str(source / 'deploy/maintenance/web_engine_prepare.py')), patch.object(R, 'SOURCE', source), \
                patch.object(R, 'binding', side_effect=bind), patch.object(R, 'private_metadata', return_value=list(range(9))) as metadata, \
                patch.object(Path, 'lstat', new=lstat), patch.object(Path, 'read_text', new=text), patch.object(R.os.path, 'lexists', side_effect=lexists):
            plan = R.snapshot(C, spec)
        self.assertEqual((len(plan['containers']), len(plan['units']), len(plan['engine_intent']), len(plan['datastore_probes'])), (27, 10, 4, 8))
        self.assertEqual(sum(row['running'] for row in plan['containers']), 26)
        self.assertTrue(all(row['network_names'] == ['fixture-network'] for row in plan['containers']))
        self.assertNotIn('never-persist-this-field', json.dumps(plan))
        self.assertIn(spec['backup_key_file'], [str(call.args[0]) for call in metadata.call_args_list])
        self.assertIn(spec['offhost']['identity_file'], [str(call.args[0]) for call in metadata.call_args_list])
        C.runtime_contract.assert_called_once_with(plan, 'baseline')

    def test_source_cannot_import_application_or_read_private_input_contents(self):
        raw = (HERE / 'web_engine_prepare.py').read_text()
        self.assertNotIn('verify_private_inputs(', raw)
        self.assertNotIn('parse_env(', raw)
        self.assertNotIn('_master_key(', raw)
        self.assertNotIn('docker start', raw)
        self.assertNotIn('apt-get', raw)
        self.assertIn('private_metadata(spec[\'backup_key_file\']', raw)

    def test_ansible_default_boundary_fixed_copylist_and_no_runtime_actions(self):
        play = yaml.safe_load((HERE / 'web_engine_prepare.yml').read_text())[0]
        self.assertEqual(tuple(play['vars']['web_engine_source_files']), R.PUBLIC_SOURCES)
        self.assertEqual(play['vars']['web_engine_prepare_action'], 'check')
        tasks = play['tasks']; boundary = next(i for i, t in enumerate(tasks) if t.get('ansible.builtin.meta') == 'end_play')
        native = next(i for i, t in enumerate(tasks) if t.get('delegate_to') == 'localhost' and 'ansible.builtin.command' in t)
        first_copy = next(i for i, t in enumerate(tasks) if 'ansible.builtin.copy' in t)
        self.assertLess(boundary, native); self.assertLess(native, first_copy)
        self.assertIn('plan_exists', tasks[boundary]['when'])
        for task in tasks:
            self.assertNotIn('ansible.builtin.apt', task)
            self.assertNotIn('ansible.builtin.systemd_service', task)
            self.assertNotIn('ansible.builtin.reboot', task)
        copies = [t['ansible.builtin.copy'] for t in tasks if 'ansible.builtin.copy' in t]
        self.assertFalse(any('lookup(' in json.dumps(copy) for copy in copies))
        self.assertTrue(all(copy['force'] is False for copy in copies))
        self.assertEqual(copies[1]['content'], '')

    def test_ansible_native_pinned_code_input_and_receipt_gates_precede_remote_execution(self):
        tasks = yaml.safe_load((HERE / 'web_engine_prepare.yml').read_text())[0]['tasks']
        remote = next(i for i, task in enumerate(tasks) if
                      task.get('ansible.builtin.command', {}).get('argv', [None])[-1] == 'prepare')
        env = Environment(undefined=StrictUndefined)
        env.filters['from_json'] = json.loads
        pins = {'source_sha256': self.inputs['source_sha256'], 'inputs_sha256': 'e' * 64,
                'receipt_sha256': {'app-backup-receipt.json': 'c' * 64,
                                  'app-offhost-receipt.json': 'd' * 64}}
        context = {'web_engine_native_validation': {'stdout': json.dumps(pins)}}
        base = {'isreg': True, 'islnk': False, 'uid': 0, 'nlink': 1, 'mode': '0600', 'size': 128}
        groups = (
            ('Require native-verified source bytes and exact empty namespace before remote execution',
             [('item', {'item': name, 'stat': {**base, 'checksum': digest}})
              for name, digest in {**pins['source_sha256'], 'scripts/__init__.py': sha256(b'').hexdigest()}.items()]),
            ('Require the exact private native-validated metadata input',
             [('web_engine_staged_inputs', {'stat': {**base, 'checksum': pins['inputs_sha256']}})]),
            ('Require immutable native-verified private metadata receipt bytes',
             [('item', {'item': name, 'stat': {**base, 'checksum': digest}})
              for name, digest in pins['receipt_sha256'].items()]),
        )
        for name, fixtures in groups:
            index = next(i for i, task in enumerate(tasks) if task.get('name') == name)
            self.assertLess(index, remote)
            conditions = [env.compile_expression(value) for value in tasks[index]['ansible.builtin.assert']['that']]
            for variable, fixture in fixtures:
                with self.subTest(gate=name, path=fixture.get('item')):
                    self.assertTrue(all(check(**{**context, variable: fixture}) for check in conditions))
                    for key, value in (('checksum', 'f' * 64), ('nlink', 2), ('mode', '0644'),
                                       ('islnk', True), ('uid', 65534)):
                        bad = {**fixture, 'stat': {**fixture['stat'], key: value}}
                        self.assertFalse(all(check(**{**context, variable: bad}) for check in conditions))
        code_hash = next(task for task in tasks if task.get('register') == 'web_engine_staged_code')
        receipt_hash = next(task for task in tasks if task.get('register') == 'web_engine_staged_receipts')
        self.assertEqual(receipt_hash['loop'], ['app-backup-receipt.json', 'app-offhost-receipt.json'])
        for task in (code_hash, receipt_hash,
                     next(task for task in tasks if task.get('register') == 'web_engine_staged_inputs')):
            self.assertFalse(task['ansible.builtin.stat']['follow'])
            self.assertEqual(task['ansible.builtin.stat']['checksum_algorithm'], 'sha256')
            self.assertLess(tasks.index(task), remote)


if __name__ == '__main__': unittest.main()
