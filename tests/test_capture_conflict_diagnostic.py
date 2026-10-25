"""Genuine-root public fixtures; no Docker, SSH, runtime config or session reads."""
from contextlib import ExitStack, redirect_stdout
from copy import deepcopy
from hashlib import sha256
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
DENIED = ('subprocess.run', 'subprocess.Popen', 'subprocess.call', 'subprocess.check_call',
          'subprocess.check_output', 'os.system', 'os.popen', 'os.fork', 'os.kill', 'os.killpg',
          'socket.socket', 'socket.create_connection', 'socket.getaddrinfo', 'urllib.request.urlopen',
          'resource.setrlimit', 'signal.signal', 'signal.alarm')


def fences(stack):
    for name in DENIED:
        stack.enter_context(patch(name, side_effect=AssertionError('ambient operation denied')))


with ExitStack() as importing:
    fences(importing)
    spec = importlib.util.spec_from_file_location('_public_capture_conflict', ROOT/'deploy/maintenance/capture_conflict_diagnostic.py')
    H = importlib.util.module_from_spec(spec); spec.loader.exec_module(H)


class CaptureConflictTests(unittest.TestCase):
    def setUp(self):
        self.assertEqual((os.geteuid(), os.getegid()), (0, 0), 'required genuine root public fixture lane')
        self.stack = ExitStack(); self.addCleanup(self.stack.close); fences(self.stack)
        # The coordinator refuses writable ancestors. The root coverage lane
        # supplies its own /work/source; /tmp is deliberately not an exception.
        import tempfile
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix='.public-capture-conflict-', dir=Path.cwd())))
        self.root.chmod(0o700); self.nonce = 'a'*32; self.pin = 'b'*64
        self.oldpath = '/root/.local/public-capture-source/private_capture.py'
        self.newpath = str(H.ROOTS['web']/self.nonce/'private_capture.py')
        self.files = ['/root/.local/public-capture/base.yml', '/root/.local/public-capture/overrides.yml']
        self.env_reference = '/srv/trading-bot/market-pipeline-releases/'+'c'*40+'/web.release.env'

    def private(self, name, raw=b'EXPLICIT PUBLIC FIXTURE', mode=0o600):
        path = self.root/name; path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        path.write_bytes(raw); path.chmod(mode); return path

    def control(self):
        now = int(time.time()); metadata = [1, 2, 0o100600, 0, 0, 1, 0, 3, 4]
        return {'schema': 'capture_conflict_control/1', 'operation': H.OP, 'nonce': self.nonce,
                'source_commit': 'c'*40, 'worker_sha256': self.pin, 'issued_at': now, 'deadline': now+900,
                'native_machine_id': 'd'*32, 'web_machine_id': 'e'*32, 'public_profile_sha256': 'f'*64,
                'fences': {'native': {'marker': list(metadata), 'source': list(metadata)}, 'web': {'marker': list(metadata)}},
                'compose': {'directory': '/root/.local/security-capture2-compose-parity-20261024-04',
                            'files': [{'path': path, 'sha256': str(index+1)*64} for index, path in enumerate(self.files)],
                            'env_reference': self.env_reference},
                'old_source': {'path': self.oldpath, 'sha256': H.OLD_SHA, 'bytes': 111954},
                'new_source': {'path': self.newpath, 'sha256': H.NEW_SHA, 'bytes': H.NEW_BYTES},
                'foundation_source': {'path': '/root/.local/public-capture-source/private_pipeline_foundation.py',
                                      'sha256': H.FOUNDATION_SHA, 'bytes': 37187}}

    def fixture(self):
        service = {'image': H.IMAGE, 'user': '10001:10001', 'read_only': True, 'init': True,
                   'restart': 'unless-stopped', 'cap_drop': ['ALL'], 'security_opt': ['no-new-privileges'],
                   'pids_limit': 64, 'mem_limit': '1073741824', 'cpus': '1.0', 'tmpfs': ['/tmp:rw,noexec,size=16m'],
                   'logging': {'driver': 'json-file', 'options': {'max-size': '1m', 'max-file': '2'}},
                   'environment': {'SYNTHETIC_PRIVATE_TOKEN': 'PUBLIC_VALUE_NEVER_SERIALIZE'},
                   'command': ['python3', 'capture_fixture.py'], 'entrypoint': None,
                   'healthcheck': {'test': ['CMD', 'true'], 'interval': '30s', 'timeout': '5s', 'start_period': '10s', 'retries': 3},
                   'volumes': [{'type': 'bind', 'source': self.oldpath, 'target': H.DEST, 'read_only': True},
                               {'type': 'bind', 'source': '/root/.local/public-capture-source/private_pipeline_foundation.py', 'target': H.FOUNDATION_DEST, 'read_only': True},
                               {'type': 'bind', 'source': H.SESSION, 'target': '/sessions', 'read_only': True}],
                   'networks': {'market-backend': {}}}
        image = {'Id': H.IMAGE, 'Config': {'Env': ['PATH=/usr/bin'], 'Cmd': ['python3'], 'Entrypoint': None,
                                        'User': '10001:10001', 'WorkingDir': '/app', 'Volumes': None, 'Labels': {'public.fixture': 'true'}}}
        rendered = {'services': {H.SERVICE: service, 'other-fixture-service': {'image': 'public-inert'}},
                    'networks': {'market-backend': {'name': 'public-market-backend'}}}
        cfg = {'AttachStdin': False, 'AttachStdout': True, 'AttachStderr': True, 'OpenStdin': False,
               'StdinOnce': False, 'Tty': False, 'Domainname': '', 'Hostname': H.OLD_CID[:12],
               'Cmd': service['command'], 'Entrypoint': None, 'Env': ['PATH=/usr/bin', 'SYNTHETIC_PRIVATE_TOKEN=PUBLIC_VALUE_NEVER_SERIALIZE'],
               'Healthcheck': {'Test': ['CMD', 'true'], 'Interval': 30000000000, 'Timeout': 5000000000,
                               'StartPeriod': 10000000000, 'Retries': 3},
               'Image': H.IMAGE, 'Labels': {'public.fixture': 'true', 'com.docker.compose.project': H.PROJECT,
                   'com.docker.compose.service': H.SERVICE, 'com.docker.compose.version': '2.40.3', 'com.docker.compose.config-hash': 'a'*64,
                   'com.docker.compose.project.working_dir': '/root/.local/security-capture2-compose-parity-20261024-04',
                   'com.docker.compose.project.config_files': ','.join(self.files),
                   'com.docker.compose.project.environment_file': self.env_reference},
               'User': '10001:10001', 'Volumes': None, 'WorkingDir': '/app'}
        host = {name: None for name in ('BlkioDeviceReadBps', 'BlkioDeviceReadIOps', 'BlkioDeviceWriteBps', 'BlkioDeviceWriteIOps',
                'BlkioWeightDevice', 'CapAdd', 'DeviceCgroupRules', 'DeviceRequests', 'Devices', 'Dns', 'DnsOptions', 'DnsSearch',
                'ExtraHosts', 'GroupAdd', 'Links', 'PortBindings', 'Ulimits', 'VolumesFrom')}
        host.update({name: 0 for name in ('BlkioWeight', 'CpuCount', 'CpuPercent', 'CpuPeriod', 'CpuQuota', 'CpuRealtimePeriod',
                    'CpuRealtimeRuntime', 'CpuShares', 'IOMaximumBandwidth', 'IOMaximumIOps', 'MemoryReservation', 'OomScoreAdj')})
        host.update({name: '' for name in ('Cgroup', 'CgroupParent', 'ContainerIDFile', 'CpusetCpus', 'CpusetMems', 'Isolation',
                    'PidMode', 'UTSMode', 'UsernsMode', 'VolumeDriver')})
        host.update({'ReadonlyRootfs': True, 'Init': True, 'CapDrop': ['ALL'], 'SecurityOpt': ['no-new-privileges'],
                     'PidsLimit': 64, 'Memory': 1073741824, 'NanoCpus': 1000000000,
                     'RestartPolicy': {'Name': 'unless-stopped', 'MaximumRetryCount': 0},
                     'LogConfig': service['logging'] | {}, 'Tmpfs': {'/tmp': 'rw,noexec,size=16m'},
                     'Binds': [row['source']+':'+row['target']+':ro' for row in service['volumes']], 'Mounts': [],
                     'AutoRemove': False, 'CgroupnsMode': 'private', 'ConsoleSize': [0, 0], 'IpcMode': 'private',
                     'MemorySwappiness': None, 'Privileged': False, 'PublishAllPorts': False, 'Runtime': 'runc',
                     'ShmSize': 67108864, 'MemorySwap': 2147483648, 'NetworkMode': 'public-market-backend', 'OomKillDisable': False,
                     'MaskedPaths': ['/proc/acpi', '/proc/asound', '/proc/interrupts', '/proc/kcore', '/proc/keys', '/proc/latency_stats',
                         '/proc/sched_debug', '/proc/scsi', '/proc/timer_list', '/proc/timer_stats', '/sys/devices/virtual/powercap', '/sys/firmware'],
                     'ReadonlyPaths': ['/proc/bus', '/proc/fs', '/proc/irq', '/proc/sys', '/proc/sysrq-trigger']})
        host['LogConfig'] = {'Type': 'json-file', 'Config': {'max-size': '1m', 'max-file': '2'}}
        row = {'id': H.OLD_CID, 'image': H.IMAGE, 'name': '/'+H.PROJECT+'-'+H.SERVICE+'-1', 'config': cfg, 'host': host,
               'mounts': [{'Type': 'bind', 'Source': r['source'], 'Destination': r['target'], 'RW': False, 'Propagation': 'rprivate'} for r in service['volumes']],
               'networks': {'public-market-backend': {'NetworkID': '1'*64, 'IPAMConfig': None, 'DriverOpts': None, 'Links': None,
                   'Aliases': [H.PROJECT+'-'+H.SERVICE+'-1', H.SERVICE], 'EndpointID': '2'*64,
                   'IPAddress': '192.0.2.7', 'MacAddress': '02:00:00:00:00:07'}},
               'running': True, 'restarting': False, 'pid': 12345}
        return rendered, row, image

    def baseline(self):
        _, target, _ = self.fixture(); rows = {target['id']: target}
        for index in range(26):
            row = deepcopy(target); row['id'] = f'{index+1:064x}'; row['name'] = '/public-other-'+str(index)
            row['config']['Labels']['com.docker.compose.service'] = 'public-other-'+str(index)
            row['config']['Hostname'] = row['id'][:12]
            row['mounts'] = [{'Type': 'bind', 'Source': '/public/other-'+str(index), 'Destination': '/data', 'RW': True, 'Propagation': 'rprivate'}]
            row['host']['Binds'] = ['/public/other-'+str(index)+':/data:rw']; row['pid'] += index+1
            rows[row['id']] = row
        return rows

    def changed(self, row, cid='c'*64, running=False, *, restore=False, override='/public/diagnostic-source.yml'):
        value = deepcopy(row) if restore else H.replace_mount_source(row, self.oldpath, self.newpath)
        value.update(id=cid, pid=54321 if running else 0, running=running, restarting=False)
        value['config']['Hostname'] = cid[:12]
        value['config']['Labels']['com.docker.compose.config-hash'] = 'b'*64
        value['config']['Labels']['com.docker.compose.project.config_files'] = ','.join(self.files+([] if restore else [override]))
        if not running: value['networks']['public-market-backend']['NetworkID'] = ''
        return value

    def diagnostic(self):
        return {'schema': 'capture_identity_conflict_diagnostic/1', 'account': 'account2', 'source_code': 'GROUP_1',
                'event_schema': 'coin_group_event', 'schema_version': '2.1', 'event_type': 'message_created',
                'incoming_origin': 'live', 'previous_origin': 'explicit_backfill', 'branch': 'seen', 'rollback_confirmed': True,
                'comparison_status': 'compared', 'changed_fields': ['message_text'], 'security_accepted': False}

    def test_control_is_fixed_fresh_typed_and_requires_actual_generated_source(self):
        value = self.control(); now = value['issued_at']; self.assertIs(H.validate_control(value, now), value)
        for mutate in (lambda v: v.update(extra='PUBLIC'), lambda v: v.update(deadline=True),
                       lambda v: v.update(deadline=now+901), lambda v: v.update(issued_at=now+1),
                       lambda v: v['fences']['native']['source'].__setitem__(6, 1),
                       lambda v: v['fences']['web']['marker'].__setitem__(0, True),
                       lambda v: v['new_source'].update(sha256='0'*64),
                       lambda v: v['new_source'].update(path=self.oldpath),
                       lambda v: v['foundation_source'].update(bytes=True)):
            changed = deepcopy(value); mutate(changed)
            with self.assertRaises((RuntimeError, ValueError)): H.validate_control(changed, now)

    def test_recursive_parity_never_aliases_booleans_integers_and_json_duplicate_keys(self):
        self.assertFalse(H.same({'x': True}, {'x': 1})); self.assertFalse(H.same([1], [1.0]))
        self.assertTrue(H.same({'a': [1, None]}, {'a': [1, None]}))
        for raw in (b'{"a":1,"a":2}', b'{"a":NaN}', b'{"a":Infinity}'):
            with self.assertRaises(RuntimeError): H.document(raw)

    def test_metadata_fences_use_real_root_metadata_and_never_read_or_hash_permanent_payloads(self):
        marker = self.private('marker', b'PUBLIC_PERMANENT_PAYLOAD_MUST_NEVER_BE_READ'); source = self.private('source', b'')
        expected = {path: H.ident(path.lstat()) for path in (marker, source)}
        with ExitStack() as held, patch.object(H.os, 'read', side_effect=AssertionError('permanent read')), \
             patch.object(H.os, 'pread', side_effect=AssertionError('permanent pread')), \
             patch.object(H, 'sha256', side_effect=AssertionError('permanent hash')):
            for path in (marker, source): H.MetadataFence(path, expected[path], held).check()
        self.assertEqual({path: H.ident(path.lstat()) for path in (marker, source)}, expected)
        with ExitStack() as held:
            H.MetadataFence(marker, expected[marker], held)
            with ExitStack() as blocked, self.assertRaisesRegex(RuntimeError, '^fence_busy$'): H.MetadataFence(marker, expected[marker], blocked)

    def test_owned_guard_parent_drift_and_existing_foreign_paths_refuse_and_unknown_retains_guard(self):
        parent = self.root/'guard-parent'; parent.mkdir(mode=0o700); path = parent/'guard'
        with ExitStack() as held:
            guard = H.OwnedGuard(path, self.nonce, self.pin, held)
            with ExitStack() as blocked, self.assertRaisesRegex(RuntimeError, '^owner_exists$'): H.OwnedGuard(path, self.nonce, self.pin, blocked)
            parent.chmod(0o770)
            try:
                with self.assertRaisesRegex(RuntimeError, '^owner_changed$'): guard.check()
            finally: parent.chmod(0o700)
        self.assertTrue(path.exists())  # Closing a process FD is not success/closure authority.
        path.unlink()
        with ExitStack() as held:
            guard = H.OwnedGuard(path, self.nonce, self.pin, held); descriptor = guard.fd
            guard.close_known(); self.assertTrue(guard.closed); self.assertEqual(os.fstat(descriptor).st_nlink, 0)
            self.assertFalse(path.exists())

    def test_source_and_render_changes_allow_exactly_one_public_source_bind(self):
        rendered, row, _ = self.fixture(); original = deepcopy(row)
        new = self.changed(row); H.expected_source_change(row, new, self.oldpath, self.newpath, stopped=True)
        changed = deepcopy(rendered); changed['services'][H.SERVICE]['volumes'][0]['source'] = self.newpath
        H.rendered_delta(rendered, changed, self.oldpath, self.newpath); self.assertEqual(row, original)
        for alter in (lambda v: v['config']['Env'].append('PRIVATE_DIFFERENT=1'),
                      lambda v: v['host'].update(Memory=123), lambda v: v['config'].update(User='0:0'),
                      lambda v: v['mounts'][1].update(Source='/public/WRONG'),
                      lambda v: v['networks']['public-market-backend'].update(IPAMConfig={'IPv4Address': '192.0.2.99'})):
            bad = deepcopy(new); alter(bad)
            with self.assertRaises(RuntimeError): H.expected_source_change(row, bad, self.oldpath, self.newpath, stopped=True)
        bad = deepcopy(changed); bad['services']['other-fixture-service']['image'] = 'WRONG'
        with self.assertRaisesRegex(RuntimeError, '^source_delta$'): H.rendered_delta(rendered, bad, self.oldpath, self.newpath)

    def test_full_normal_compose_private_config_and_host_parity_rejects_every_unapproved_dimension(self):
        rendered, row, image = self.fixture(); self.assertTrue(H.full_parity(rendered, row, image))
        for field, change in (('Env', ['PATH=/usr/bin', 'SYNTHETIC_PRIVATE_TOKEN=WRONG']), ('Image', 'public-wrong-image'),
                              ('User', '0'), ('WorkingDir', '/wrong'), ('AttachStdin', 0)):
            bad = deepcopy(row); bad['config'][field] = change
            with self.subTest(field=field), self.assertRaises(RuntimeError): H.full_parity(rendered, bad, image)
        for field, change in (('Privileged', True), ('CapAdd', ['SYS_ADMIN']), ('Memory', True), ('NetworkMode', 'host')):
            bad = deepcopy(row); bad['host'][field] = change
            with self.subTest(field=field), self.assertRaises(RuntimeError): H.full_parity(rendered, bad, image)
        bad = deepcopy(row); bad['config']['Env'].append('PATH=/different')
        with self.assertRaisesRegex(RuntimeError, '^environment$'): H.full_parity(rendered, bad, image)

    def test_all_other_twenty_six_static_contracts_and_session_owners_stay_protected(self):
        before = self.baseline(); target = before[H.OLD_CID]; after = deepcopy(before)
        new = self.changed(target); del after[H.OLD_CID]; after[new['id']] = new
        H.other_static(before, after, H.OLD_CID, new['id'])
        public_before = H.public_profile(after)
        other = next(key for key in after if key != new['id'])
        after[other]['config']['Env'].append('PRIVATE_DIFFERENT=1')
        with self.assertRaisesRegex(RuntimeError, '^profile$'): H.other_static(before, after, H.OLD_CID, new['id'])
        self.assertEqual(H.public_profile(after), public_before)
        H.session_owners(before, H.OLD_CID)
        for source in (H.SESSION, str(Path(H.SESSION).parent), H.SESSION+'/nested'):
            bad = deepcopy(before); key = next(key for key in bad if key != H.OLD_CID)
            bad[key]['mounts'][0]['Source'] = source
            with self.assertRaisesRegex(RuntimeError, '^session_owner$'): H.session_owners(bad, H.OLD_CID)

    def test_diagnostic_and_journal_accept_only_closed_value_free_records(self):
        value = self.diagnostic(); self.assertEqual(H.diagnostic(value), value)
        for changes in ({'raw': 'PUBLIC_PRIVATE_MARKER'}, {'source_code': 'PUBLIC_PRIVATE_MARKER'}, {'rollback_confirmed': 1},
                        {'changed_fields': ['PUBLIC_PRIVATE_MARKER']}, {'changed_fields': ['message_text', 'message_text']},
                        {'comparison_status': 'timeout'}): self.assertIsNone(H.diagnostic({**value, **changes}))
        journal = H.Journal(self.root, self.nonce, self.pin)
        for arguments in (('PUBLIC_PRIVATE_MARKER', {}), ('prepared', {'Env': ['PUBLIC_PRIVATE_MARKER']}),
                          ('prepared', {'diagnostic': {'raw': 'PUBLIC_PRIVATE_MARKER'}})):
            with self.assertRaisesRegex(RuntimeError, '^journal$'): journal.add(arguments[0], **arguments[1])
        journal.add('prepared', full_config_parity=True); journal.add('outcome_unknown')
        self.assertTrue(all(b'PUBLIC_PRIVATE_MARKER' not in path.read_bytes() for path in self.root.glob('*.json')))

    def backend(self, snapshots):
        case = self; rendered, _, image = self.fixture()
        class Backend:
            def __init__(self):
                self.op = case.control(); self.events = []; self.requested = False; self.snapshots = list(snapshots)
                self.override = case.root/'diagnostic-source.yml'; self.checks = []; self.clock = H.Clock(self.op)
                self.journal = H.Journal(case.root, case.nonce, case.pin); self.never_started = set(); self.started = set()
            def target(self, rows):
                values = [row for row in rows.values() if row['name'] == '/'+H.PROJECT+'-'+H.SERVICE+'-1']
                case.assertEqual(len(values), 1); return values[0]
            def snapshot(self, absent=False): self.events.append(('snapshot', absent)); return deepcopy(self.snapshots.pop(0))
            def bind_cgroup(self, row):
                case.assertTrue(row['running']); case.assertGreater(row['pid'], 0); self.events.append(('bind', row['id']))
                return lambda: self.events.append(('empty', row['id']))
            def stop(self, new=False): self.requested = True; self.events.append(('stop', new))
            def legacy_owners(self): self.events.append(('legacy_owners',))
            def create(self, new=False): self.requested = True; self.events.append(('create', new))
            def start(self, cid): self.requested = True; self.started.add(cid); self.events.append(('start', cid))
            def full_parity(self, value, row): self.events.append(('parity', row['id']))
            def check(self):
                self.events.append(('check',))
                for check in self.checks: check()
            def render(self, new=False, public_override=None):
                value = deepcopy(rendered)
                if new: value['services'][H.SERVICE]['volumes'][0]['source'] = case.newpath
                return value
            def command(self, argv, **kwargs):
                return b'2.40.3\n' if argv[-3:] == ['compose', 'version', '--short'] else H.encode(image)
            def observe(self, cid): return case.diagnostic()
        return Backend()

    def states(self):
        baseline = self.baseline(); stopped = deepcopy(baseline); stopped[H.OLD_CID].update(pid=0, running=False)
        candidate = self.changed(baseline[H.OLD_CID], override=str(self.root/'diagnostic-source.yml'))
        created = {key: value for key, value in deepcopy(baseline).items() if key != H.OLD_CID}; created[candidate['id']] = candidate
        started = deepcopy(created); started[candidate['id']].update(pid=54321, running=True)
        started[candidate['id']]['networks']['public-market-backend']['NetworkID'] = '1'*64
        return baseline, stopped, created, started

    def test_replacement_stops_and_proves_old_owner_empty_before_create_parity_and_one_start(self):
        baseline, stopped, created, started = self.states(); backend = self.backend([stopped, created, started])
        old_render = backend.render(); new_render = backend.render(new=True)
        result = H.replacement(backend, baseline, old_render, new_render)
        self.assertEqual(result['id'], 'c'*64)
        self.assertLess(backend.events.index(('stop', False)), backend.events.index(('empty', H.OLD_CID)))
        self.assertLess(backend.events.index(('empty', H.OLD_CID)), backend.events.index(('create', True)))
        self.assertLess(backend.events.index(('parity', 'c'*64)), backend.events.index(('start', 'c'*64)))
        self.assertEqual([event for event in backend.events if event[0] == 'start'], [('start', 'c'*64)])
        self.assertLess(backend.events.index(('legacy_owners',)), backend.events.index(('stop', False)))
        self.assertEqual(backend.events.count(('legacy_owners',)), 2)

    def test_busy_legacy_owner_refuses_before_stopping_the_running_capture(self):
        baseline, stopped, created, started = self.states(); backend = self.backend([stopped, created, started])
        with patch.object(backend, 'legacy_owners', side_effect=RuntimeError('session_owner')):
            with self.assertRaisesRegex(RuntimeError, '^session_owner$'):
                H.replacement(backend, baseline, backend.render(), backend.render(new=True))
        self.assertFalse(backend.requested)
        self.assertEqual([event for event in backend.events if event[0] in {'bind', 'stop', 'create', 'start'}], [])

    def test_legacy_owner_appearing_after_create_refuses_candidate_start(self):
        baseline, stopped, created, started = self.states(); backend = self.backend([stopped, created, started])
        with patch.object(backend, 'legacy_owners', side_effect=[None, RuntimeError('session_owner')]) as legacy:
            with self.assertRaisesRegex(RuntimeError, '^session_owner$'):
                H.replacement(backend, baseline, backend.render(), backend.render(new=True))
        self.assertEqual(legacy.call_count, 2)
        self.assertEqual([event for event in backend.events if event[0] in {'stop', 'create', 'start'}],
                         [('stop', False), ('create', True)])
        self.assertEqual(backend.created_stopped_id, 'c'*64)
        self.assertFalse(hasattr(backend, 'start_requested_id'))

    def test_known_never_started_candidate_rolls_back_through_normal_stopped_first_path(self):
        baseline, stopped, created, _ = self.states()
        restored = {key: value for key, value in deepcopy(baseline).items() if key != H.OLD_CID}
        restored['d'*64] = self.changed(baseline[H.OLD_CID], cid='d'*64, restore=True)
        running = deepcopy(restored); running['d'*64].update(pid=55555, running=True)
        running['d'*64]['networks']['public-market-backend']['NetworkID'] = '1'*64
        backend = self.backend([stopped, created])
        with patch.object(backend, 'full_parity', side_effect=RuntimeError('config')):
            with self.assertRaisesRegex(RuntimeError, '^config$'):
                H.replacement(backend, baseline, backend.render(), backend.render(new=True))
        self.assertEqual(backend.created_stopped_id, 'c'*64)
        self.assertNotIn(('start', 'c'*64), backend.events)
        backend.snapshots.extend([created, created, restored, running])
        value = H.rollback(backend, baseline, backend.render())
        self.assertEqual(value['id'], 'd'*64)
        self.assertEqual([event for event in backend.events if event[0] == 'bind'], [('bind', H.OLD_CID)])
        self.assertLess(backend.events.index(('stop', True)), backend.events.index(('create', False)))
        self.assertLess(backend.events.index(('parity', 'd'*64)), backend.events.index(('start', 'd'*64)))

    def test_previously_started_or_unproved_exited_candidate_never_gets_automatic_rollback(self):
        baseline, _, created, _ = self.states()
        for started in (False, True):
            backend = self.backend([created]); backend.created_stopped_id = 'c'*64
            if started: backend.start_requested_id = 'c'*64
            backend.old_empty_proof = (lambda: None) if started else None
            with self.subTest(started=started), self.assertRaises((H.Unknown, RuntimeError)):
                H.rollback(backend, baseline, backend.render())
            self.assertEqual([event for event in backend.events if event[0] in {'stop', 'create', 'start'}], [])

    def test_web_default_check_uses_only_ram_override_and_creates_no_guard_or_journal(self):
        baseline = self.baseline(); backend = self.backend([baseline]); control = backend.op
        control['public_profile_sha256'] = H.public_profile(baseline)
        with ExitStack() as held, patch.object(H, 'WEB_OWNER', self.root/'web.guard'), \
             patch.object(H, 'Web', return_value=backend):
            value = H.web_run(control, self.pin, self.root, held, [], False)
        self.assertEqual(value['status'], 'preflight_passed_not_applied')
        self.assertFalse((self.root/'web.guard').exists()); self.assertFalse(backend.override.exists())
        self.assertEqual(list(self.root.glob('*.json')), [])

    def test_web_unknown_after_request_retains_owned_guard_and_never_calls_rollback(self):
        baseline = self.baseline(); backend = self.backend([baseline]); control = backend.op
        control['public_profile_sha256'] = H.public_profile(baseline)
        def uncertain(*_): backend.requested = True; raise H.Unknown()
        with ExitStack() as held, patch.object(H, 'WEB_OWNER', self.root/'web.guard'), \
             patch.object(H, 'Web', return_value=backend), patch.object(H, 'replacement', side_effect=uncertain) as replace, \
             patch.object(H, 'rollback', side_effect=AssertionError('unknown outcome cannot retry')) as rollback:
            with self.assertRaises(H.Unknown): H.web_run(control, self.pin, self.root, held, [], True)
        self.assertEqual(replace.call_count, 1); rollback.assert_not_called()
        self.assertTrue((self.root/'web.guard').exists()); self.assertFalse((self.root/'terminal.json').exists())
        records = [json.loads(path.read_bytes()) for path in sorted(self.root.glob('[0-9]*.json'))]
        self.assertEqual(records[-1]['phase'], 'outcome_unknown')

    def test_web_source_delta_failure_refuses_before_any_guard_or_stop_request(self):
        baseline = self.baseline(); backend = self.backend([baseline]); control = backend.op
        control['public_profile_sha256'] = H.public_profile(baseline); render = backend.render
        def wrong(*args, **kwargs):
            value = render(*args, **kwargs)
            if kwargs.get('new'): value['services']['other-fixture-service']['image'] = 'WRONG'
            return value
        with ExitStack() as held, patch.object(H, 'WEB_OWNER', self.root/'web.guard'), \
             patch.object(H, 'Web', return_value=backend), patch.object(backend, 'render', side_effect=wrong):
            with self.assertRaisesRegex(RuntimeError, '^source_delta$'): H.web_run(control, self.pin, self.root, held, [], True)
        self.assertFalse((self.root/'web.guard').exists()); self.assertFalse(backend.requested)

    def test_observer_reads_combined_stream_and_retains_only_closed_diagnostic_records(self):
        control = self.control()
        web = H.Web(control, self.pin, self.root, self.stack, [])
        raw = b'PRIVATE_SYNTHETIC_UNSTRUCTURED_LOG\n'+H.encode(self.diagnostic())
        with patch.object(H, 'bounded', return_value=raw) as command:
            value = web.observe('c'*64)
        self.assertEqual(value, self.diagnostic()); self.assertTrue(command.call_args.kwargs['combine_stderr'])
        self.assertEqual(command.call_args.args[0], [*H.DOCKER, 'logs', '--tail', '64', 'c'*64])
        self.assertNotIn('PRIVATE_SYNTHETIC', json.dumps(value))

    def test_observer_failure_keeps_known_configuration_active_incident_open_without_rollback(self):
        baseline, _, _, started = self.states(); backend = self.backend([baseline]); control = backend.op
        control['public_profile_sha256'] = H.public_profile(baseline)
        def accepted(*_): backend.requested = True; return started['c'*64]
        with ExitStack() as held, patch.object(H, 'WEB_OWNER', self.root/'web.guard'), \
             patch.object(H, 'Web', return_value=backend), patch.object(H, 'replacement', side_effect=accepted), \
             patch.object(backend, 'observe', side_effect=RuntimeError('PUBLIC_PRIVATE_LOG_FAILURE')), \
             patch.object(H, 'rollback', side_effect=AssertionError('log visibility is not a config failure')) as rollback:
            value = H.web_run(control, self.pin, self.root, held, [], True)
        rollback.assert_not_called(); self.assertFalse((self.root/'web.guard').exists())
        self.assertEqual(value['status'], 'diagnostic_active_incident_open'); self.assertTrue(value['capture_issue_open'])
        self.assertFalse(value['functional_fix_claimed']); self.assertFalse(value['security_accepted'])
        self.assertNotIn('PUBLIC_PRIVATE_LOG_FAILURE', json.dumps(value))
        H.peer_result(value, control, self.pin)

    def test_wrong_compose_version_refuses_before_guard_or_mutation(self):
        for version in (b'2.40.4\n', b'PUBLIC_PRIVATE_VERSION\n'):
            baseline = self.baseline(); backend = self.backend([baseline]); control = backend.op
            control['public_profile_sha256'] = H.public_profile(baseline)
            with ExitStack() as held, patch.object(H, 'WEB_OWNER', self.root/'web.guard'), \
                 patch.object(H, 'Web', return_value=backend), patch.object(backend, 'command', return_value=version):
                with self.assertRaisesRegex(RuntimeError, '^compose$'): H.web_run(control, self.pin, self.root, held, [], True)
            self.assertFalse(backend.requested); self.assertFalse((self.root/'web.guard').exists())

    def test_command_mutation_failure_becomes_closed_unknown_without_leaking_private_output(self):
        clock = H.Clock(self.control())
        class Child:
            pid = 123456789
            def poll(self): return 7
            def wait(self, timeout=None): return 7
        def process(argv, **kwargs):
            self.assertEqual(argv, ['public-inert-fixture'])
            os.write(kwargs['stdout'], b'PRIVATE_SYNTHETIC_COMMAND_OUTPUT')
            return Child()
        with patch.object(H.subprocess, 'Popen', side_effect=process), patch.object(H.os, 'killpg', side_effect=ProcessLookupError):
            for mutating, error, code in ((False, RuntimeError, 'command'), (True, H.Unknown, 'unknown')):
                with self.subTest(mutating=mutating), self.assertRaises(error) as caught:
                    H.bounded(['public-inert-fixture'], clock, mutating=mutating)
                self.assertEqual(caught.exception.args, (code,))

    def test_native_unknown_peer_retains_owned_guard_and_closed_journal_without_retry(self):
        control = self.control(); args = SimpleNamespace(nonce=self.nonce, control_sha256=self.pin, apply=H.CONFIRM)
        reservation = self.root/'reservation'
        with ExitStack() as held, patch.object(H, 'RESERVATION', reservation), \
             patch.object(H, 'bounded', side_effect=H.Unknown()) as command:
            with self.assertRaises(H.Unknown): H.native_run(args, self.root, control, [], held)
        self.assertEqual(command.call_count, 1); self.assertTrue(reservation.exists())
        records = [json.loads(path.read_bytes()) for path in self.root.glob('[0-9]*.json')]
        self.assertEqual(records[-1]['phase'], 'outcome_unknown')
        self.assertFalse((self.root/'terminal.json').exists())

    def test_native_requires_closed_web_ack_durable_terminal_before_own_guard_closure(self):
        control = self.control(); args = SimpleNamespace(nonce=self.nonce, control_sha256=self.pin, apply=H.CONFIRM)
        ack = H.result(control, self.pin, 'diagnostic_active_incident_open', cid='c'*64, record=self.diagnostic())
        ack.update(terminal_sha256=sha256(H.encode(ack)).hexdigest(), web_guard_closed=True)
        reservation = self.root/'reservation'; original = H.OwnedGuard.close_known; order = []
        def close(guard):
            self.assertEqual(json.loads((self.root/'terminal.json').read_bytes()), ack); order.append('terminal-before-close'); original(guard)
        with ExitStack() as held, patch.object(H, 'RESERVATION', reservation), patch.object(H.OwnedGuard, 'close_known', close), \
             patch.object(H, 'bounded', return_value=H.encode(ack)) as command:
            self.assertEqual(H.native_run(args, self.root, control, [], held), ack)
        self.assertFalse(reservation.exists()); self.assertEqual(order, ['terminal-before-close'])
        self.assertEqual(command.call_args.kwargs['allowed_codes'], (0, 3)); self.assertEqual(command.call_args.kwargs['seconds'], 900)
        text = command.call_args.args[0][-1]
        self.assertIn('RuntimeMaxSec=900', text); self.assertIn('-oStrictHostKeyChecking=yes', ' '.join(command.call_args.args[0]))

    def test_peer_ack_requires_exact_status_fields_true_false_types_and_actual_public_terminal_hash(self):
        control = self.control(); base = H.result(control, self.pin, 'diagnostic_active_incident_open', cid='c'*64, record=self.diagnostic())
        valid = {**base, 'terminal_sha256': sha256(H.encode(base)).hexdigest(), 'web_guard_closed': True}
        H.peer_result(valid, control, self.pin)
        for mutate in (lambda v: v.update(nonce='d'*32), lambda v: v.update(security_accepted=0),
                       lambda v: v.update(capture_issue_open=1), lambda v: v.update(web_guard_closed=1),
                       lambda v: v.pop('container_id'), lambda v: v.update(status='PUBLIC_PRIVATE_STATUS'),
                       lambda v: v.update(Env=['PUBLIC_PRIVATE_VALUE']), lambda v: v.update(terminal_sha256='0'*64)):
            changed = deepcopy(valid); mutate(changed)
            if changed['terminal_sha256'] != '0'*64:
                terminal = {k: value for k, value in changed.items() if k not in {'terminal_sha256', 'web_guard_closed'}}
                changed['terminal_sha256'] = sha256(H.encode(terminal)).hexdigest()
            with self.assertRaisesRegex(RuntimeError, '^peer$'): H.peer_result(changed, control, self.pin)

    def test_cli_check_is_default_and_intentionally_open_diagnostic_outcome_has_exit_three(self):
        control = self.control()
        for apply in (False, True):
            value = H.result(control, self.pin, 'diagnostic_active_incident_open' if apply else 'preflight_passed_not_applied')
            argv = ['--site', 'native', '--nonce', self.nonce, '--control-sha256', self.pin]
            if apply: argv += ['--apply', H.CONFIRM]
            with patch.object(H, 'load', return_value=(self.root, control, [])), \
                 patch.object(H, 'native_run', return_value=value) as native, redirect_stdout(io.StringIO()) as output:
                self.assertEqual(H.main(argv), 3 if apply else 0)
            self.assertEqual(native.call_args.args[0].apply, H.CONFIRM if apply else None)
            self.assertTrue(json.loads(output.getvalue())['capture_issue_open']); self.assertFalse(json.loads(output.getvalue())['security_accepted'])


if __name__ == '__main__': unittest.main()
