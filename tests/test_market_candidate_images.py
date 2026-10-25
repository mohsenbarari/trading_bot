"""Owned public image-store invariants; no Docker, SSH or live operations.

Synthetic bytes below establish unit-test facts only. They never claim a real
Market image admission. Root fixtures run with genuine UID0, without UID masks.
"""
from contextlib import ExitStack, redirect_stdout, redirect_stderr
from copy import deepcopy
from hashlib import sha256
import ast
import errno
import gzip
import importlib.util
import io
import json
import os
import struct
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import warnings
import zipfile


ROOT = Path(__file__).resolve().parents[1]
AMBIENT = ('subprocess.Popen', 'subprocess.run', 'subprocess.call', 'subprocess.check_call',
           'subprocess.check_output', 'os.system', 'os.popen', 'os.kill', 'os.killpg',
           'socket.socket', 'socket.create_connection', 'socket.getaddrinfo',
           'urllib.request.urlopen', 'ctypes.CDLL', 'ctypes.PyDLL',
           'signal.signal', 'signal.alarm', 'resource.setrlimit')


def fence(stack):
    for name in AMBIENT:
        stack.enter_context(patch(name, side_effect=AssertionError('ambient operation denied')))


def module(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    value = importlib.util.module_from_spec(spec)
    sys.modules[name] = value
    spec.loader.exec_module(value)
    return value


with ExitStack() as importing:
    fence(importing)
    M = module('_owned_market_candidate_images', 'deploy/maintenance/market_candidate_images.py')
    D = module('_owned_market_candidate_contract', 'deploy/maintenance/datastore_candidate_contract.py')


class MarketCandidateImageTests(unittest.TestCase):
    def setUp(self):
        self.assertEqual(os.geteuid(), 0, 'required genuine-root fixture CI lane')
        self.assertEqual(os.getegid(), 0, 'required genuine-root fixture CI lane')
        self.stack = ExitStack(); self.addCleanup(self.stack.close); fence(self.stack)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix='owned-market-image-tests-')))
        self.root.chmod(0o700)
        old = os.umask(0o077); self.addCleanup(os.umask, old)

    def private(self, name, body):
        path = self.root / name
        path.write_bytes(body); path.chmod(0o600)
        return path

    def basic(self):
        ctx = M.Context.__new__(M.Context)
        ctx.stack = self.stack; ctx.checks = []; ctx.end = M.time.monotonic() + 60
        ctx.work = self.root / 'attempt'; ctx.D = D; ctx.pin = 'a' * 64
        ctx.spec = {'profile': 'explicit-public-fixture'}; ctx.op = {'phase': 'load-native'}
        ctx.budget = 0; ctx.requested = False
        return ctx

    def mapped_temporary_storage(self):
        temporary = self.root / 'temporary'; temporary.mkdir(mode=0o700); temporary.chmod(0o1777)
        real_path = Path
        self.stack.enter_context(patch.object(M, 'Path', side_effect=lambda value:
            self.root if value == '/' else temporary if value == '/tmp' else real_path(value)))
        return temporary

    def test_public_json_is_closed_typed_bounded_and_rejects_nested_duplicates(self):
        body = M.encode({'closed': [None, False, 0, 'ordinary']})
        self.assertEqual(M.document(body), {'closed': [None, False, 0, 'ordinary']})
        self.assertTrue(body.endswith(b'\n')); M.need(True, 'unused')
        for raw, reason in [(b'', 'document_bound'), (bytearray(b'{}'), 'document_bound'),
                            (b'x' * (M.LIMIT + 1), 'document_bound'),
                            (b'{"x":{"same":1,"same":2}}', 'duplicate_key'),
                            (b'{"value":NaN}', 'nonfinite_json')]:
            with self.subTest(reason=reason), self.assertRaisesRegex(RuntimeError, '^' + reason + '$'):
                M.document(raw)
        with self.assertRaises(ValueError): M.encode({'value': float('inf')})
        M.public({'receipt': [{'accepted': False}]})
        for key in ('Config', 'Env', 'environment', 'runtime_config', 'access_token'):
            with self.subTest(key=key), self.assertRaisesRegex(RuntimeError, '^private_output$'):
                M.public({'nested': [{key: 'EXPLICIT_PUBLIC_REJECTION_FIXTURE'}]})

    def test_real_source_descriptor_is_retained_until_scope_exit_and_rejects_replacement(self):
        body = b'OWNED_PUBLIC_SOURCE = True\n'; source = self.private('source.py', body)
        owned = ExitStack(); self.addCleanup(owned.close)
        raw, check = M.seal(owned, source, sha256(body).hexdigest())
        self.assertEqual(raw, body); check()
        source.rename(self.root / 'retained-old-source.py')
        self.private('source.py', body)
        with self.assertRaisesRegex(RuntimeError, '^source_changed$'): check()
        owned.close()
        with self.assertRaises(OSError): check()

    def test_source_parent_mode_inode_and_canonical_path_are_revalidated(self):
        folder = self.root / 'source-parent'; folder.mkdir(mode=0o700)
        source = folder / 'public.py'; body = b'EXPLICIT_OWNED_PUBLIC_SOURCE = True\n'
        source.write_bytes(body); source.chmod(0o600); pin = sha256(body).hexdigest()
        _, check = M.seal(self.stack, source, pin); check()
        folder.chmod(0o770)
        with self.assertRaisesRegex(RuntimeError, '^protected_parent_changed$'): check()
        with self.assertRaisesRegex(RuntimeError, '^protected_parent$'): M.seal(self.stack, source, pin)
        folder.chmod(0o700); check()
        link = self.root / 'linked-parent'; link.symlink_to(folder, target_is_directory=True)
        with self.assertRaisesRegex(RuntimeError, '^source_path$'): M.seal(self.stack, link / source.name, pin)
        folder.rename(self.root / 'retained-source-parent'); folder.mkdir(mode=0o700)
        source.write_bytes(body); source.chmod(0o600)
        with self.assertRaisesRegex(RuntimeError, '^protected_parent_changed$'): check()

    def test_source_pin_and_metadata_refuse_symlinks_hardlinks_empty_or_excess_bytes(self):
        body = b'public'; source = self.private('input.py', body)
        for pin, maximum, reason in [('0' * 64, len(body), 'source_sha256'),
                                      (sha256(body).hexdigest(), len(body) - 1, 'source_metadata')]:
            with self.subTest(reason=reason), ExitStack() as held, self.assertRaisesRegex(RuntimeError, '^' + reason + '$'):
                M.seal(held, source, pin, maximum)
        link = self.root / 'alias.py'; link.symlink_to(source)
        with ExitStack() as held, self.assertRaisesRegex(RuntimeError, '^source_path$'):
            M.seal(held, link, sha256(body).hexdigest())
        hard = self.root / 'hard.py'; os.link(source, hard)
        with ExitStack() as held, self.assertRaisesRegex(RuntimeError, '^source_metadata$'):
            M.seal(held, source, sha256(body).hexdigest())
        hard.unlink(); source.chmod(0o640)
        with ExitStack() as held, self.assertRaisesRegex(RuntimeError, '^source_metadata$'):
            M.seal(held, source, sha256(body).hexdigest())
        empty = self.private('empty.py', b'')
        with ExitStack() as held, self.assertRaisesRegex(RuntimeError, '^source_metadata$'):
            M.seal(held, empty, sha256(b'').hexdigest())

    def test_source_same_inode_content_change_is_detected_before_reuse(self):
        source = self.private('mutable.py', b'public-one')
        with ExitStack() as held:
            _, check = M.seal(held, source, sha256(b'public-one').hexdigest())
            with source.open('r+b') as stream: stream.write(b'public-two')
            with self.assertRaisesRegex(RuntimeError, '^source_changed$'): check()

    def test_child_limits_bound_files_and_cores_without_address_space_cap(self):
        with patch.object(M.resource, 'setrlimit') as limit: M.bounds()
        self.assertEqual(limit.call_args_list[0].args, (M.resource.RLIMIT_CORE, (0, 0)))
        self.assertEqual(limit.call_args_list[1].args, (M.resource.RLIMIT_FSIZE, (M.LIMIT + 1, M.LIMIT + 1)))
        self.assertEqual(limit.call_count, 2)

    def test_checkpoint_runs_all_real_source_checks_and_refuses_expired_window(self):
        ctx = self.basic(); calls = []
        source = self.private('retained.py', b'PUBLIC = 1\n')
        _, check = M.seal(self.stack, source, sha256(source.read_bytes()).hexdigest())
        ctx.checks = [check, lambda: calls.append('checked')]
        ctx.check(); self.assertEqual(calls, ['checked'])
        source.chmod(0o640)
        with self.assertRaisesRegex(RuntimeError, '^source_changed$'): ctx.check()
        ctx.end = 0
        with self.assertRaisesRegex(RuntimeError, '^deadline$'): ctx.check()

    def test_published_intent_is_real_owned_file_and_prior_attempt_is_never_overwritten(self):
        ctx = self.basic(); ctx.begin()
        target = ctx.work / 'intent.json'; old = target.read_bytes()
        value = M.document(old)
        self.assertEqual(value['profile'], 'explicit-public-fixture')
        self.assertEqual(value['operation_sha256'], ctx.pin)
        self.assertIs(value['retry_allowed'], False); self.assertIs(value['security_accepted'], False)
        self.assertEqual(target.stat().st_mode & 0o777, 0o600)
        with self.assertRaisesRegex(RuntimeError, '^previous_attempt_requires_reconciliation$'): ctx.begin()
        self.assertEqual(target.read_bytes(), old)
        for name, value, reason in [('not/a.json', {}, 'output_bound'),
                                     ('large.json', {'ordinary': 'x' * M.LIMIT}, 'output_bound'),
                                     ('private.json', {'nested': {'Env': []}}, 'private_output')]:
            with self.subTest(name=name), self.assertRaisesRegex(RuntimeError, '^' + reason + '$'):
                ctx.publish(name, value)
        pin = ctx.publish('public.json', {'accepted': False})
        self.assertEqual(pin, sha256((ctx.work / 'public.json').read_bytes()).hexdigest())

    def test_root_temporary_metadata_has_retained_identity_and_mode_drift_is_observed(self):
        temporary = self.mapped_temporary_storage()
        before = M.temp_metadata()
        self.assertEqual(len(before), 2)
        self.assertEqual(before[0]['identity'][0], before[1]['identity'][0])
        self.assertEqual(before[0]['identity'][3:5], [0, 0])
        temporary.chmod(0o755)
        with self.assertRaisesRegex(RuntimeError, '^temporary_storage_mode$'): M.temp_metadata()

    def test_load_budget_is_reserved_above_each_floor_before_any_rpc_and_at_every_pulse(self):
        temporary = self.mapped_temporary_storage(); ctx = self.basic()
        data = self.root / 'data'; data.mkdir(mode=0o700)
        ctx.baseline = {'store': {'root': str(data)}, 'temporary_storage': M.temp_metadata()}
        ctx.budget = 20 * 1024**2; additional = ctx.budget + 5
        root_available = 2 * 1024**3 + M.MARGIN + M.LIMIT + additional
        data_available = 8 * 1024**3 + M.MARGIN + M.LIMIT + additional
        available = {'/': root_available, '/tmp': root_available, str(data): data_available}
        def observe(path): return SimpleNamespace(f_bavail=available[str(path)], f_frsize=1)
        with patch.object(M.os, 'statvfs', side_effect=observe):
            ctx.capacity(additional)
            for path in ('/', '/tmp', str(data)):
                available[path] -= 1
                with self.subTest(path=path), self.assertRaisesRegex(RuntimeError, '^capacity_floor$'):
                    ctx.capacity(additional)
                available[path] += 1
            # This is above the nominal 2GiB floor yet cannot fund active LOAD.
            available['/'] = 2 * 1024**3 + 1
            with patch.object(M.subprocess, 'Popen') as launch, self.assertRaisesRegex(RuntimeError, '^capacity_floor$'):
                M.command(ctx, ['EXPLICIT_INERT_CLIENT'])
            launch.assert_not_called(); available['/'] = root_available
            temporary.rename(self.root / 'prior-temporary')
            temporary.mkdir(mode=0o700); temporary.chmod(0o1777)
            with self.assertRaisesRegex(RuntimeError, '^temporary_storage_changed$'): ctx.capacity()

    def memory_fixture(self, raw):
        info = self.private('owned-public-meminfo', raw); path_factory = M.Path
        self.stack.enter_context(patch.object(M, 'Path', side_effect=lambda value:
            info if value == '/proc/meminfo' else path_factory(value)))
        return info

    def test_memory_available_uses_one_bounded_owned_input_and_requires_exact_linux_metadata(self):
        info = self.memory_fixture(b'MemTotal:       999999 kB\nMemAvailable:\t12345 kB\n')
        actual_open = Path.open; reads = []
        class ObservedFile:
            def __enter__(inner): inner.stream = actual_open(info, 'rb'); return inner
            def __exit__(inner, *args): inner.stream.close()
            def read(inner, count): reads.append(count); return inner.stream.read(count)
        with patch.object(Path, 'open', side_effect=lambda *args, **kwargs: ObservedFile()):
            self.assertEqual(M.memory_available(), 12345 * 1024)
        self.assertEqual(reads, [65537])
        for raw in (b'', b'X' * 65537, b'MemTotal: 123 kB\n',
                    b'MemAvailable: 12 kB\nMemAvailable: 13 kB\n',
                    b'MemAvailable: -1 kB\n', b'MemAvailable: 1.5 kB\n',
                    b'MemAvailable: 1024 bytes\n', b'MemAvailable: 1 KB\n',
                    b'MemAvailable: 1 kB trailing\n'):
            with self.subTest(raw=raw[:50]):
                info.write_bytes(raw)
                with self.assertRaisesRegex(RuntimeError, '^temporary_memory_metadata$'): M.memory_available()

    def test_distinct_temporary_device_keeps_exact_metadata_and_reserves_memory_budget_every_pulse(self):
        temporary = self.mapped_temporary_storage(); ctx = self.basic()
        data = self.root / 'distinct-device-data'; data.mkdir(mode=0o700)
        real_identity = M.identity; temporary_inode = temporary.lstat().st_ino; device_delta = [1]
        # Explicit inert filesystem-observation seam: genuine owned UID/mode/
        # inode metadata, with only st_dev varied to represent a separate tmpfs.
        def filesystem_identity(metadata):
            observed = real_identity(metadata)
            if metadata.st_ino == temporary_inode: observed[0] += device_delta[0]
            return observed
        self.stack.enter_context(patch.object(M, 'identity', side_effect=filesystem_identity))
        observed = M.temp_metadata()
        self.assertNotEqual(observed[0]['identity'][0], observed[1]['identity'][0])
        self.assertEqual(observed[1]['identity'], filesystem_identity(temporary.lstat())[:5])
        ctx.baseline = {'store': {'root': str(data)}, 'temporary_storage': deepcopy(observed)}
        ctx.budget = 1024**2; additional = 2 * 1024**2
        floor = 2 * 1024**3 + M.MARGIN + M.LIMIT + additional
        info = self.memory_fixture(b'MemAvailable: ' + str(floor // 1024).encode() + b' kB\n')
        with patch.object(M.os, 'statvfs', return_value=SimpleNamespace(f_bavail=16 * 1024**3, f_frsize=1)):
            ctx.capacity(additional)
            ctx.budget = additional
            info.write_bytes(b'MemAvailable: ' + str(floor // 1024 - 1).encode() + b' kB\n')
            with patch.object(M.subprocess, 'Popen') as launch, self.assertRaisesRegex(RuntimeError, '^temporary_memory_floor$'):
                M.command(ctx, ['EXPLICIT_INERT_CLIENT'])
            launch.assert_not_called()
            info.write_bytes(b'MemAvailable: ' + str(floor // 1024).encode() + b' kB\n')
            state, launch = self.child(pending=4)
            def memory_drops_after_client_launch(argv, **kwargs):
                child = launch(argv, **kwargs)
                info.write_bytes(b'MemAvailable: ' + str(floor // 1024 - 1).encode() + b' kB\n')
                return child
            with patch.object(M.subprocess, 'Popen', side_effect=memory_drops_after_client_launch), \
                 patch.object(M.time, 'sleep'), self.assertRaisesRegex(RuntimeError, '^temporary_memory_floor$'):
                M.command(ctx, ['EXPLICIT_INERT_CLIENT'])
            self.assertTrue(state.killed)
            with self.assertRaises(OSError): os.fstat(state.fd)
            info.write_bytes(b'MemAvailable: ' + str(floor // 1024).encode() + b' kB\n')
            ctx.capacity(additional); device_delta[0] = 2
            with self.assertRaisesRegex(RuntimeError, '^temporary_storage_changed$'): ctx.capacity(additional)

    def test_capacity_rechecks_retained_store_inode_and_public_daemon_unit_metadata(self):
        self.mapped_temporary_storage(); ctx = self.basic()
        data = self.root / 'data'; data.mkdir(mode=0o700)
        unit = self.private('owned-daemon.service', b'[Service]\nPrivateTmp=false\n')
        ctx.baseline = {'store': {'root': str(data), 'identity': M.identity(data.lstat())[:5]},
            'temporary_storage': M.temp_metadata(),
            'daemon': {'files': [{'path': str(unit), 'identity': M.identity(unit.lstat())}]}}
        with patch.object(M.os, 'statvfs', return_value=SimpleNamespace(f_bavail=16 * 1024**3, f_frsize=1)):
            ctx.capacity(); unit.chmod(0o640)
            with self.assertRaisesRegex(RuntimeError, '^daemon_unit_metadata_changed$'): ctx.capacity()
            unit.chmod(0o600); ctx.baseline['daemon']['files'][0]['identity'] = M.identity(unit.lstat())
            data.rename(self.root / 'retained-old-data'); data.mkdir(mode=0o700)
            with self.assertRaisesRegex(RuntimeError, '^store_root_changed$'): ctx.capacity()

    def child(self, body=b'closed public response', *, code=0, pending=1):
        state = SimpleNamespace(fd=None, killed=False, polls=0, waits=[])
        class Child:
            def poll(inner):
                state.polls += 1
                return None if state.polls <= pending and not state.killed else code
            def kill(inner): state.killed = True
            def wait(inner, timeout): state.waits.append(timeout); return code
        def launch(argv, **kwargs):
            self.assertEqual(argv, ['EXPLICIT_INERT_CLIENT'])
            self.assertTrue(callable(kwargs['preexec_fn']))
            with patch.object(M, 'bounds') as limits:
                kwargs['preexec_fn'](); limits.assert_called_once()
            state.fd = kwargs['stdout']; os.write(state.fd, body)
            return Child()
        return state, launch

    def test_bounded_command_owns_real_memfd_and_rechecks_capacity_while_child_pending(self):
        ctx = SimpleNamespace(capacity=Mock()); state, launch = self.child()
        with patch.object(M.subprocess, 'Popen', side_effect=launch), patch.object(M.time, 'sleep'):
            self.assertEqual(M.command(ctx, ['EXPLICIT_INERT_CLIENT']), b'closed public response')
        self.assertGreaterEqual(ctx.capacity.call_count, 2)
        with self.assertRaises(OSError): os.fstat(state.fd)
        self.assertIn(10, state.waits)

    def test_output_overflow_unknown_exit_and_capacity_loss_cleanup_child_and_owned_fd(self):
        for body, code, maximum, failure in [(b'12345', 0, 4, 'command_output_bound'),
                                               (b'ordinary', 2, 64, 'command_outcome_unknown')]:
            ctx = SimpleNamespace(capacity=Mock()); state, launch = self.child(body, code=code)
            with self.subTest(failure=failure), patch.object(M.subprocess, 'Popen', side_effect=launch), \
                 patch.object(M.time, 'sleep'), self.assertRaisesRegex(RuntimeError, '^' + failure + '$'):
                M.command(ctx, ['EXPLICIT_INERT_CLIENT'], maximum=maximum)
            with self.assertRaises(OSError): os.fstat(state.fd)
        ctx = SimpleNamespace(capacity=Mock(side_effect=[None, RuntimeError('capacity_floor')]))
        state, launch = self.child(pending=5)
        with patch.object(M.subprocess, 'Popen', side_effect=launch), self.assertRaisesRegex(RuntimeError, '^capacity_floor$'):
            M.command(ctx, ['EXPLICIT_INERT_CLIENT'])
        self.assertTrue(state.killed)
        with self.assertRaises(OSError): os.fstat(state.fd)

    def test_stop_reaps_an_already_exited_child_and_kills_only_pending_owned_child(self):
        for running in (True, False):
            child = Mock(); child.poll.return_value = None if running else 0
            M.stop(child); child.wait.assert_called_once_with(timeout=10)
            self.assertEqual(child.kill.call_count, int(running))

    def snapshot_fixture(self, site):
        ids = [format(i + 1, '064x') for i in range(27 if site == 'web' else 15)]
        if site == 'web': ids[0] = M.CAPTURE
        rows = [{'id': value, 'running': True, 'restarting': False, 'health': 'healthy', 'pid': 100 + i,
                 'image': 'sha256:' + 'b' * 64, 'host': {'fixture': 'public'}, 'mounts': [],
                 'networks': {}, 'execution': {'entry': [], 'cmd': ['ordinary']}}
                for i, value in enumerate(ids)]
        image_map = [{'id': 'sha256:' + 'a' * 64, 'repository': 'ordinary-fixture', 'tag': 'public'}]
        calls = []
        def command(ctx, argv, **kwargs):
            calls.append(argv)
            if argv[len(M.DOCKER):] == ['ps', '-aq', '--no-trunc']:
                self.assertEqual(M.SUBPHASE, 'snapshot_container_inventory'); return ('\n'.join(ids) + '\n').encode()
            if argv[len(M.DOCKER)] == 'inspect':
                self.assertEqual(M.SUBPHASE, 'snapshot_container_contracts')
                return b'\n'.join(M.encode(row).rstrip() for row in rows) + b'\n'
            self.assertEqual(M.SUBPHASE, 'snapshot_image_inventory')
            self.assertEqual(argv[len(M.DOCKER):len(M.DOCKER)+2], ['image', 'ls'])
            return b'\n'.join(M.encode(row).rstrip() for row in image_map) + b'\n'
        return ids, rows, image_map, calls, command

    def test_snapshot_binds_complete_inventory_and_exact_web_capture_singleton(self):
        for site in ('bot', 'web'):
            ids, rows, images, calls, command = self.snapshot_fixture(site)
            ctx = SimpleNamespace(site=site, D=D)
            with self.subTest(site=site), patch.object(M, 'command', side_effect=command):
                observed = M.snapshot(ctx)
                self.assertEqual(observed['containers'], sorted(rows, key=lambda row: row['id']))
                self.assertEqual(observed['image_map'], images); self.assertEqual(len(calls), 3)
                ctx.baseline = deepcopy(observed); self.assertEqual(M.stable(ctx), observed)
                rows[-1]['host']['drift'] = True
                with self.assertRaisesRegex(RuntimeError, '^runtime_changed$'): M.stable(ctx)
                ids.append(ids[0])
                with self.assertRaisesRegex(RuntimeError, '^container_inventory$'): M.snapshot(ctx)
        ids, rows, _, _, command = self.snapshot_fixture('web'); ctx = SimpleNamespace(site='web')
        with patch.object(M, 'command', side_effect=command):
            for field, value in [('running', False), ('restarting', True), ('health', None), ('pid', True)]:
                original = rows[0][field]; rows[0][field] = value
                with self.subTest(field=field), self.assertRaisesRegex(RuntimeError, '^capture_singleton$'): M.snapshot(ctx)
                rows[0][field] = original

    def test_optional_inspect_maps_use_safe_formatter_and_only_non_capture_health_may_be_null(self):
        ids, rows, _, _, command = self.snapshot_fixture('web'); ctx = SimpleNamespace(site='web')
        for row in rows:
            row['health'] = 'healthy' if row['id'] == M.CAPTURE else None
            row['execution']['healthcheck'] = None
        def observe(current, argv, **kwargs):
            if argv[len(M.DOCKER)] == 'inspect':
                fmt = argv[argv.index('--format') + 1]
                self.assertIn('with index .State "Health"', fmt)
                self.assertIn('with index .Config "Healthcheck"', fmt)
                self.assertNotIn('.State.Health', fmt); self.assertNotIn('.Config.Healthcheck', fmt)
                self.assertIn('{{else}}null{{end}}', fmt)
            return command(current, argv, **kwargs)
        # The inert formatter output represents optional fields absent in the
        # source maps, not an assertion that a native Go formatter ran here.
        with patch.object(M, 'command', side_effect=observe): value = M.snapshot(ctx)
        M.public(value); by_id = {row['id']: row for row in value['containers']}
        self.assertEqual(by_id[M.CAPTURE]['health'], 'healthy')
        self.assertTrue(all(row['health'] is None for cid, row in by_id.items() if cid != M.CAPTURE))
        self.assertTrue(all(row['execution']['healthcheck'] is None for row in by_id.values()))
        rows[0]['health'] = None
        with patch.object(M, 'command', side_effect=observe), self.assertRaisesRegex(RuntimeError, '^capture_singleton$'):
            M.snapshot(ctx)

    def test_snapshot_contract_command_failure_reports_fixed_phase_and_never_echoes_error_arguments(self):
        worker = self.private('snapshot-worker.py', (ROOT / 'deploy/maintenance/market_candidate_images.py').read_bytes())
        machine = self.private('snapshot-machine-id', b'a' * 32 + b'\n')
        store = self.root / 'snapshot-store'; store.mkdir(mode=0o700); real_path = Path
        ids, _, _, _, _ = self.snapshot_fixture('bot')
        for error, expected in [(RuntimeError('command_outcome_unknown'), 'command_outcome_unknown'),
                                (ValueError('EXPLICIT_PRIVATE_LOOKING_FORMATTER_DIAGNOSTIC'), 'refused_or_unknown')]:
            calls = []
            def command(ctx, argv, **kwargs):
                ctx.capacity(); calls.append(argv)
                if argv[len(M.DOCKER):len(M.DOCKER)+1] == ['info']:
                    self.assertEqual(M.SUBPHASE, 'snapshot_store_info')
                    return M.encode({'root': str(store), 'driver': 'public', 'version': 'public'})
                if argv[0] == '/usr/bin/findmnt':
                    self.assertEqual(M.SUBPHASE, 'snapshot_store_mount'); return M.encode({'filesystems': []})
                if argv[len(M.DOCKER):] == ['ps', '-aq', '--no-trunc']:
                    self.assertEqual(M.SUBPHASE, 'snapshot_container_inventory'); return ('\n'.join(ids) + '\n').encode()
                self.assertEqual(argv[len(M.DOCKER)], 'inspect'); self.assertEqual(M.SUBPHASE, 'snapshot_container_contracts')
                raise error
            out = io.StringIO()
            with self.subTest(error=type(error).__name__), patch.object(M, '__file__', str(worker)), \
                 patch.object(M, 'Path', side_effect=lambda p: machine if p == '/etc/machine-id' else real_path(p)), \
                 patch.object(M, 'command', side_effect=command), patch.object(M.signal, 'alarm'), redirect_stdout(out):
                self.assertEqual(M.main(['--site', 'bot', '--phase', 'snapshot',
                    '--source-sha256', sha256(worker.read_bytes()).hexdigest()]), 1)
            result = json.loads(out.getvalue()); self.assertEqual(result['subphase'], 'snapshot_container_contracts')
            self.assertEqual(result['code'], expected); self.assertEqual(len(calls), 4)
            self.assertIs(result['retry_allowed'], False); self.assertIs(result['security_accepted'], False)
            self.assertNotIn('EXPLICIT_PRIVATE', out.getvalue())

    def test_remote_argv_is_fixed_finite_and_does_not_add_a_serving_command(self):
        ctx = SimpleNamespace(op={'nonce': 'a' * 32}, pin='b' * 64)
        argv = M.remote(ctx, 'load-web')
        self.assertEqual(argv[:len(M.SSH)], M.SSH)
        for fixed in ('--property=RuntimeMaxSec=900', '--property=MemoryMax=2G', '--property=TasksMax=64',
                      '--phase', 'load-web', '--apply', M.CONFIRM): self.assertIn(fixed, argv)
        self.assertEqual(argv[argv.index('-i') + 1], '/root/.ssh/id_ed25519_iran')
        self.assertEqual(argv[argv.index('-S') + 1], 'none')
        self.assertIn('-oControlMaster=no', argv); self.assertIn('-oUpdateHostKeys=no', argv)
        self.assertNotIn('docker', argv); self.assertNotIn('run', argv)

    def test_snapshot_projects_only_logconfig_map_and_keeps_nested_environment_prohibition(self):
        for options in ({}, {'max-size': '10m', 'max-file': '3'}):
            with self.subTest(options=options):
                _, rows, _, _, command = self.snapshot_fixture('bot'); ctx = SimpleNamespace(site='bot')
                rows[0]['host'] = {'LogConfig': {'Type': 'json-file', 'Config': options}, 'Memory': 123}
                with patch.object(M, 'command', side_effect=command): value = M.snapshot(ctx)
                M.public(value)
                self.assertEqual(value['containers'][0]['host'],
                    {'LogConfig': {'Type': 'json-file', 'options': options}, 'Memory': 123})
                rows[0]['host']['Env'] = ['EXPLICIT_PUBLIC_REJECTION_FIXTURE=1']
                with patch.object(M, 'command', side_effect=command), self.assertRaisesRegex(RuntimeError, '^private_output$'):
                    M.public(M.snapshot(ctx))
                rows[0]['host'].pop('Env'); rows[0]['host']['LogConfig']['Config'] = {'Env': []}
                with patch.object(M, 'command', side_effect=command), self.assertRaisesRegex(RuntimeError, '^private_output$'):
                    M.public(M.snapshot(ctx))

    def test_finite_cgroup_scope_requires_exact_owned_name_and_resource_caps(self):
        nonce = 'a' * 32; name = 'market-images-bot-load-native-' + nonce + '.scope'
        cgroup = self.private('public-cgroup', ('0::/ordinary/' + name + '\n').encode())
        tree = self.root / 'public-sysfs'; folder = tree / 'ordinary' / name
        folder.mkdir(parents=True, mode=0o700)
        values = {'memory.max': str(2 * 1024**3), 'memory.swap.max': '0', 'pids.max': '64', 'cpu.max': '100000 100000'}
        for key, value in values.items(): (folder / key).write_text(value)
        real_path = Path
        with patch.object(M, 'Path', side_effect=lambda value:
                          cgroup if value == '/proc/self/cgroup' else tree if value == '/sys/fs/cgroup' else real_path(value)):
            M.scope('bot', 'load-native', nonce)
            for key, value in [('memory.max', 'max'), ('memory.swap.max', '1'), ('pids.max', '65'), ('cpu.max', 'max 100000'), ('cpu.max', '100001 100000')]:
                (folder / key).write_text(value)
                with self.subTest(key=key, value=value), self.assertRaisesRegex(RuntimeError, '^scope_limits$'):
                    M.scope('bot', 'load-native', nonce)
                (folder / key).write_text(values[key])
            cgroup.write_bytes(b'0::/unowned.scope\n')
            with self.assertRaisesRegex(RuntimeError, '^finite_scope_required$'): M.scope('bot', 'load-native', nonce)

    def owned_guard(self, ctx):
        marker = self.private('permanent-marker', b'EXPLICIT_PUBLIC_PERMANENT_MARKER')
        source = self.private('permanent-empty-source', b'')
        reservation = self.root / 'owned-reservation'
        self.stack.enter_context(patch.object(M, 'MARKER', marker))
        self.stack.enter_context(patch.object(M, 'SOURCE', source))
        self.stack.enter_context(patch.object(M, 'RESERVATION', reservation))
        self.stack.enter_context(patch.object(M, 'PENDING', self.root / 'owned-absent-release-state.json'))
        ctx.root = self.root; ctx.site = 'bot'; ctx.op['nonce'] = 'a' * 32
        ctx.op['fences'] = {'bot': {'marker': M.identity(marker.lstat()), 'source': M.identity(source.lstat())}}
        # Guard-only fixtures observe retained files; disk-budget integration
        # fixtures above exercise the real Context.capacity implementation.
        if 'capacity' not in ctx.__dict__: ctx.capacity = lambda *args: ctx.check()
        return marker, source, reservation

    def lock_fixture(self):
        path = self.private('owned-contended-lock', b'EXPLICIT_PUBLIC_LOCK')
        owner = os.open(path, os.O_RDWR); contender = os.open(path, os.O_RDWR)
        self.stack.callback(os.close, owner); self.stack.callback(os.close, contender)
        original = M.identity(path.lstat()); checked = []
        def check():
            checked.append(True)
            M.need(M.identity(os.fstat(contender)) == original == M.identity(path.lstat()), 'permanent_fence_changed')
        ctx = self.basic(); ctx.capacity = lambda *args: ctx.check()
        return ctx, path, owner, contender, check, checked

    def test_genuine_owned_flock_contention_rechecks_metadata_and_acquires_only_after_release(self):
        ctx, path, owner, contender, check, checked = self.lock_fixture()
        actual_flock = M.fcntl.flock; actual_flock(owner, M.fcntl.LOCK_EX | M.fcntl.LOCK_NB)
        blocked = []; attempts = []
        def lock(fd, flags):
            self.assertEqual(fd, contender); self.assertEqual(flags, M.fcntl.LOCK_EX | M.fcntl.LOCK_NB)
            attempts.append(fd)
            try: return actual_flock(fd, flags)
            except OSError as error: blocked.append(error.errno); raise
        def capacity(*args):
            ctx.check()
            if blocked: actual_flock(owner, M.fcntl.LOCK_UN)
        ctx.capacity = capacity
        with patch.object(M.fcntl, 'flock', side_effect=lock), patch.object(M.time, 'sleep'):
            M.acquire_lock(ctx, contender, check)
        self.assertTrue(blocked); self.assertGreaterEqual(len(attempts), 2); self.assertGreaterEqual(len(checked), 3)
        with self.assertRaises(BlockingIOError): actual_flock(owner, M.fcntl.LOCK_EX | M.fcntl.LOCK_NB)
        self.assertEqual(M.identity(path.lstat()), M.identity(os.fstat(contender)))

    def test_lock_wait_refuses_actual_path_replacement_before_a_second_attempt(self):
        ctx, path, owner, contender, check, _ = self.lock_fixture()
        actual_flock = M.fcntl.flock; actual_flock(owner, M.fcntl.LOCK_EX | M.fcntl.LOCK_NB)
        original = path.read_bytes(); attempts = []
        def lock(fd, flags):
            attempts.append(fd); return actual_flock(fd, flags)
        def replace_on_wait(delay):
            path.rename(self.root / 'retained-contended-lock'); self.private(path.name, original)
        with patch.object(M.fcntl, 'flock', side_effect=lock), patch.object(M.time, 'sleep', side_effect=replace_on_wait), \
             self.assertRaisesRegex(RuntimeError, '^permanent_fence_changed$'):
            M.acquire_lock(ctx, contender, check)
        self.assertEqual(attempts, [contender]); self.assertEqual(os.pread(contender, len(original), 0), original)

    def test_lock_noncontention_errors_propagate_once_without_sleep_or_retry(self):
        ctx, _, _, contender, check, _ = self.lock_fixture()
        for number in (errno.EACCES, errno.EINTR, errno.EBADF):
            error = OSError(number, 'EXPLICIT_PRIVATE_LOOKING_MESSAGE', '/EXPLICIT_PRIVATE_LOOKING_PATH')
            with self.subTest(errno=number), patch.object(M.fcntl, 'flock', side_effect=error) as lock, \
                 patch.object(M.time, 'sleep') as sleep, self.assertRaises(OSError) as caught:
                M.acquire_lock(ctx, contender, check)
            self.assertIs(caught.exception, error); lock.assert_called_once(); sleep.assert_not_called()

    def test_lock_wait_is_bounded_by_30_seconds_and_rechecks_deadline_after_capacity(self):
        ctx, _, _, contender, check, _ = self.lock_fixture(); ctx.end = 1000
        clock = [100.0]; sleeps = []
        def wait(delay): self.assertLessEqual(delay, .1); sleeps.append(delay); clock[0] += 31
        with patch.object(M.time, 'monotonic', side_effect=lambda: clock[0]), \
             patch.object(M.fcntl, 'flock', side_effect=BlockingIOError(errno.EAGAIN, 'owned contention')) as lock, \
             patch.object(M.time, 'sleep', side_effect=wait), self.assertRaisesRegex(RuntimeError, '^fence_lock_timeout$'):
            M.acquire_lock(ctx, contender, check)
        lock.assert_called_once(); self.assertEqual(sleeps, [.1])
        clock[0] = 100
        def expires_inside_flock(*args):
            clock[0] += 31; raise BlockingIOError(errno.EAGAIN, 'owned contention')
        with patch.object(M.time, 'monotonic', side_effect=lambda: clock[0]), \
             patch.object(M.fcntl, 'flock', side_effect=expires_inside_flock), patch.object(M.time, 'sleep') as sleep, \
             self.assertRaisesRegex(RuntimeError, '^fence_lock_timeout$'):
            M.acquire_lock(ctx, contender, check)
        sleep.assert_not_called()
        clock[0] = 100; ctx.end = 100.05
        def consumes_deadline(*args): clock[0] = ctx.end
        ctx.capacity = consumes_deadline
        with patch.object(M.time, 'monotonic', side_effect=lambda: clock[0]), patch.object(M.fcntl, 'flock') as lock, \
             self.assertRaisesRegex(RuntimeError, '^fence_lock_timeout$'):
            M.acquire_lock(ctx, contender, check)
        lock.assert_not_called()

    def test_owned_guard_preserves_permanent_inodes_and_only_releases_exact_reservation(self):
        ctx = self.basic(); marker, source, reservation = self.owned_guard(ctx)
        before = {path: M.identity(path.lstat()) for path in (marker, source)}
        guard = M.Guard(ctx); self.assertTrue(reservation.is_file()); guard.check()
        owned_fd = guard.reservation.fd; guard.finish()
        self.assertFalse(reservation.exists()); self.assertEqual(os.fstat(owned_fd).st_nlink, 0)
        self.assertEqual({path: M.identity(path.lstat()) for path in (marker, source)}, before)
        self.assertEqual(source.stat().st_size, 0)
        self.assertEqual(marker.read_bytes(), b'EXPLICIT_PUBLIC_PERMANENT_MARKER')

    def test_closed_exception_metadata_never_contains_args_paths_or_custom_class_names(self):
        custom = type('EXPLICIT_PRIVATE_LOOKING_CLASS_NAME', (OSError,), {})
        for error, kind, number in [(PermissionError(errno.EACCES, 'EXPLICIT_PRIVATE_TEXT', '/EXPLICIT_PRIVATE_PATH'), 'PermissionError', errno.EACCES),
                                    (FileExistsError(errno.EEXIST, 'EXPLICIT_PRIVATE_TEXT'), 'FileExistsError', errno.EEXIST),
                                    (OSError(), 'OSError', None),
                                    (RuntimeError(['EXPLICIT_PRIVATE_TEXT']), 'RuntimeError', None),
                                    (custom(errno.EACCES, 'EXPLICIT_PRIVATE_TEXT'), 'other', None),
                                    (Exception('EXPLICIT_PRIVATE_TEXT'), 'other', None)]:
            with self.subTest(kind=kind):
                value = M.exception_metadata(error)
                self.assertEqual(value, {'exception_class': kind, 'os_errno': number})
                self.assertNotIn('EXPLICIT_PRIVATE', json.dumps(value)); M.public(value)
        for number in (True, 0, -1, 4096, '13'):
            error = OSError(); error.errno = number
            self.assertEqual(M.exception_metadata(error), {'exception_class': 'OSError', 'os_errno': None})

    def test_guard_source_flock_failure_reports_exact_stage_without_reservation_attempt_or_rpc(self):
        ctx, marker, source, reservation = self.run_fixture()
        before = {path: M.identity(path.lstat()) for path in (marker, source)}
        actual_flock = M.fcntl.flock; phases = []
        failure = PermissionError(errno.EACCES, 'EXPLICIT_PRIVATE_TEXT', '/EXPLICIT_PRIVATE_PATH')
        def flock(fd, flags):
            phases.append(M.SUBPHASE)
            if os.fstat(fd).st_ino == source.lstat().st_ino: raise failure
            return actual_flock(fd, flags)
        def context(args, stack): ctx.stack = stack; return ctx
        out = io.StringIO()
        with patch.object(M, 'Context', side_effect=context), patch.object(M.fcntl, 'flock', side_effect=flock), \
             patch.object(M.signal, 'alarm'), patch.object(M, 'load_native') as phase, \
             patch.object(M, 'coordinate_web') as remote, redirect_stdout(out):
            self.assertEqual(M.main(['--site', 'bot', '--phase', 'load-native', '--apply', M.CONFIRM]), 1)
        self.assertEqual(phases, ['guard_marker_flock', 'guard_source_flock'])
        value = json.loads(out.getvalue()); self.assertEqual(value['subphase'], 'guard_source_flock')
        self.assertEqual(value['exception_class'], 'PermissionError'); self.assertEqual(value['os_errno'], errno.EACCES)
        self.assertNotIn('EXPLICIT_PRIVATE', out.getvalue()); self.assertFalse(reservation.exists()); self.assertFalse(ctx.work.exists())
        phase.assert_not_called(); remote.assert_not_called()
        self.assertEqual({path: M.identity(path.lstat()) for path in (marker, source)}, before)

    def test_guard_parent_policy_drift_refuses_cleanup_and_preserves_owned_fences(self):
        ctx = self.basic(); marker, source, reservation = self.owned_guard(ctx)
        before = {path: M.identity(path.lstat()) for path in (marker, source)}
        guard = M.Guard(ctx); self.root.chmod(0o770)
        with self.assertRaisesRegex(RuntimeError, '^protected_parent_changed$'): guard.check()
        with self.assertRaisesRegex(RuntimeError, '^protected_parent_changed$'): guard.finish()
        self.assertTrue(reservation.exists()); self.root.chmod(0o700); guard.check(); guard.finish()
        self.assertEqual({path: M.identity(path.lstat()) for path in (marker, source)}, before)

    def test_fence_replacement_or_corruption_never_unlinks_foreign_or_ambiguous_owner(self):
        ctx = self.basic(); marker, source, reservation = self.owned_guard(ctx)
        guard = M.Guard(ctx); body = reservation.read_bytes()
        reservation.rename(self.root / 'prior-reservation'); self.private('owned-reservation', body)
        with self.assertRaisesRegex(RuntimeError, '^output_changed$'): guard.finish()
        self.assertTrue(reservation.exists())
        marker.rename(self.root / 'prior-marker'); self.private('permanent-marker', b'foreign')
        with self.assertRaisesRegex(RuntimeError, '^permanent_fence_changed$'): guard.check()
        self.assertEqual(source.stat().st_size, 0)

    def test_permanent_source_must_be_empty_and_owned_guard_will_not_replace_existing_record(self):
        ctx = self.basic(); marker, source, reservation = self.owned_guard(ctx)
        first_scope = ExitStack(); self.addCleanup(first_scope.close); ctx.stack = first_scope
        source.write_bytes(b'explicit public nonempty rejection fixture')
        ctx.op['fences']['bot']['source'] = M.identity(source.lstat())
        with self.assertRaisesRegex(RuntimeError, '^permanent_fence_identity$'): M.Guard(ctx)
        self.assertFalse(reservation.exists())
        first_scope.close(); ctx.stack = ExitStack(); self.addCleanup(ctx.stack.close)
        source.write_bytes(b''); ctx.op['fences']['bot']['source'] = M.identity(source.lstat())
        self.private('owned-reservation', b'EXPLICIT_FOREIGN_OWNER')
        with self.assertRaises(FileExistsError): M.Guard(ctx)
        self.assertEqual(reservation.read_bytes(), b'EXPLICIT_FOREIGN_OWNER')

    def zipper(self, members):
        buffer = io.BytesIO()
        with warnings.catch_warnings():
            warnings.simplefilter('ignore', UserWarning)
            with zipfile.ZipFile(buffer, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
                for name, body in members: archive.writestr(name, body)
        return buffer.getvalue()

    def evidence_fixture(self, *, export_change=None, report_change=None, extra=()):
        ctx = self.basic(); ctx.root = self.root; evidence = self.root / 'evidence'; evidence.mkdir(mode=0o700)
        ctx.spec.update(candidate_config_id='sha256:' + 'c' * 64, candidate_rootfs=['sha256:' + 'd' * 64], role='synthetic_fixture')
        ctx.spec['archive'] = {'name': 'candidate-image.tar.gz', 'format': 'docker-archive+gzip', 'size_bytes': 20, 'uncompressed_bytes': 40, 'sha256': 'a' * 64}
        exported = {'image_id': ctx.spec['candidate_config_id'], 'archive': deepcopy(ctx.spec['archive']), 'security_accepted': False, 'deployment_eligible': False}
        report = {'Metadata': {'ImageID': ctx.spec['candidate_config_id'], 'ImageConfig': {'rootfs': {'diff_ids': ctx.spec['candidate_rootfs']}, 'config': {'Cmd': ['ordinary'], 'Env': ['PUBLIC_FIXTURE=1']}}}}
        if export_change: export_change(exported)
        if report_change: report_change(report)
        exported_raw, report_raw = M.encode(exported), M.encode(report)
        raw = self.zipper([('image-export.json', exported_raw), ('reports/vulnerabilities.json', report_raw), *extra])
        ctx.spec.update(public_export_bytes=len(exported_raw), public_export_sha256=sha256(exported_raw).hexdigest(),
                        public_raw_report_sha256=sha256(report_raw).hexdigest(),
                        evidence_artifact={'digest': 'sha256:' + sha256(raw).hexdigest(), 'size_in_bytes': len(raw)})
        path = evidence / (ctx.spec['profile'] + '.zip'); path.write_bytes(raw); path.chmod(0o600)
        return ctx, exported, report

    def test_public_evidence_zip_binds_the_selected_image_layers_and_exact_report_bytes(self):
        ctx, _, report = self.evidence_fixture()
        value = M.selected_evidence(ctx)
        self.assertEqual(value['image']['Config'], report['Metadata']['ImageConfig']['config'])
        self.assertEqual(value['spec']['config_id'], ctx.spec['candidate_config_id'])
        self.assertEqual(value['image']['Comment'], '')
        self.assertTrue(ctx.checks); ctx.check()

    def test_sealed_public_metadata_projects_only_top_comment_or_last_history_comment(self):
        # Reduced public F79 metadata only; no Config/Env values. Actual report
        # SHA256 6080fe89e7d97766b67615e004f4895b9a22ddfd1c0c8c7988a6cfa98e08f855
        # from f79cdda21c19.zip, source ZIP SHA256
        # faa1ccede53c7c2243aeba4c4ab6f9fa170ee0b8d05441be2c6027cba7d08b42.
        reduced = {'comment': '', 'history': [{'comment': 'debuerreotype 0.17'}] +
                   [{'comment': 'buildkit.dockerfile.v0'} for _ in range(40)]}
        cases = [(reduced, 'buildkit.dockerfile.v0'),
                 ({'history': [{'comment': 'earlier'}, {'comment': 'last'}]}, 'last'),
                 ({'comment': 'top', 'history': [{'comment': 'last'}]}, 'top'),
                 ({'history': [{'comment': 'earlier'}, {}]}, '')]
        for metadata, expected in cases:
            with self.subTest(metadata=metadata):
                ctx, _, _ = self.evidence_fixture(report_change=lambda report:
                    report['Metadata']['ImageConfig'].update(deepcopy(metadata)))
                self.assertEqual(M.selected_evidence(ctx)['image']['Comment'], expected); ctx.check()
                (self.root / 'evidence' / (ctx.spec['profile'] + '.zip')).unlink()
                (self.root / 'evidence').rmdir()

    def test_evidence_identity_and_closed_zip_inventory_refuse_even_when_container_is_valid(self):
        ctx, _, _ = self.evidence_fixture(export_change=lambda d: d.update(security_accepted=True))
        with self.assertRaisesRegex(RuntimeError, '^export_identity$'): M.selected_evidence(ctx)
        (self.root / 'evidence' / (ctx.spec['profile'] + '.zip')).unlink(); (self.root / 'evidence').rmdir()
        ctx, _, _ = self.evidence_fixture(report_change=lambda d: d['Metadata'].update(ImageID='sha256:' + 'e' * 64))
        with self.assertRaisesRegex(RuntimeError, '^evidence_config_identity$'): M.selected_evidence(ctx)
        (self.root / 'evidence' / (ctx.spec['profile'] + '.zip')).unlink(); (self.root / 'evidence').rmdir()
        ctx, _, _ = self.evidence_fixture(extra=[('image-export.json', b'{}')])
        with self.assertRaisesRegex(RuntimeError, '^evidence_inventory$'): M.selected_evidence(ctx)

    def image_zip_fixture(self, *, members_change=None, spec_change=None):
        ctx = self.basic(); ctx.work.mkdir(mode=0o700); ctx.capacity = Mock()
        tar = b'EXPLICIT_PUBLIC_ARCHIVE_BYTES_ONLY_NOT_A_DOCKER_ADMISSION\n' * 4
        compressed = gzip.compress(tar, mtime=0); exported = M.encode({'synthetic_fixture': True})
        members = [('image-export.json', exported), ('candidate-image.tar.gz', compressed)]
        if members_change: members = members_change(members)
        raw = self.zipper(members)
        ctx.spec.update(public_export_bytes=len(exported), public_export_sha256=sha256(exported).hexdigest(),
            image_artifact={'size_in_bytes': len(raw), 'digest': 'sha256:' + sha256(raw).hexdigest()},
            archive={'size_bytes': len(compressed), 'sha256': sha256(compressed).hexdigest(), 'uncompressed_bytes': len(tar)})
        if spec_change: spec_change(ctx.spec)
        path = self.private('public-image.zip', raw); fd, _, check = self.stack.enter_context(D.held(path, len(raw)))
        ctx.checks.append(check)
        return ctx, fd, raw, tar

    def test_zip_and_gzip_decode_retains_real_owned_fds_without_extracting_arbitrary_paths(self):
        ctx, fd, raw, body = self.image_zip_fixture()
        output = M.validate_zip(ctx, fd, len(raw), ctx.work / 'source.tar')
        self.assertEqual(os.pread(output.fd, len(body) + 1, 0), body)
        self.assertEqual(output.before[6], len(body)); output.check(); ctx.check()
        self.assertGreater(ctx.capacity.call_count, 1)

    def test_unknown_zip_entries_and_wrong_gzip_or_tar_pins_cannot_be_decoded(self):
        ctx, fd, raw, _ = self.image_zip_fixture(members_change=lambda rows: [*rows, ('../unowned', b'ordinary')])
        with self.assertRaisesRegex(RuntimeError, '^image_zip_inventory$'): M.validate_zip(ctx, fd, len(raw), ctx.work / 'source.tar')
        ctx.spec['image_artifact']['digest'] = 'sha256:' + '0' * 64
        with self.assertRaisesRegex(RuntimeError, '^image_zip_identity$'): M.validate_zip(ctx, fd, len(raw), ctx.work / 'source.tar')

    def admission_fixture(self, ctx, body=b'EXPLICIT_PUBLIC_HELD_TAR\n'):
        ctx.root = self.root; ctx.op['admission'] = ctx.spec['profile'] + '-admission.json'
        controls = self.root / 'controls'; controls.mkdir(exist_ok=True, mode=0o700)
        folder = self.root / 'attempts' / (ctx.spec['profile'] + '-receive-web'); folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = folder / 'source.tar'; path.write_bytes(body); path.chmod(0o600)
        ctx.spec.setdefault('candidate_config_id', 'sha256:' + 'c' * 64)
        ctx.spec.setdefault('candidate_rootfs', ['sha256:' + 'd' * 64])
        ctx.spec['archive'] = {'uncompressed_bytes': len(body)}
        ctx.T = SimpleNamespace(EXPANDED_BYTES=512 * 1024**2)
        value = {'schema': 'market_candidate_archive/1', 'profile': ctx.spec['profile'], 'candidate_config_id': ctx.spec['candidate_config_id'],
            'security_accepted': False, 'archive_bytes': len(body), 'archive_sha256': sha256(body).hexdigest(),
            'tar_identity': M.identity(path.lstat()), 'expanded_layer_bytes': 17,
            'rootfs': ctx.spec['candidate_rootfs'], 'prospective_import_bytes': 2 * len(body) + 34,
            'source_identities': {ctx.spec['candidate_config_id']: {'kind': 'Docker_config_identity'}}}
        raw = M.encode(value); control = controls / ctx.op['admission']; control.write_bytes(raw); control.chmod(0o600)
        ctx.op['admission_sha256'] = sha256(raw).hexdigest(); ctx.capacity = lambda *args: ctx.check()
        return path, value, body

    def test_admitted_source_real_fd_stays_open_through_final_check_and_closes_with_operation(self):
        ctx = self.basic(); path, value, body = self.admission_fixture(ctx)
        fd, admitted = M.admitted_tar(ctx)
        self.assertEqual(admitted, value); self.assertEqual(os.pread(fd, len(body), 0), body)
        self.assertEqual(ctx.budget, value['prospective_import_bytes']); ctx.check()
        self.stack.close()
        with self.assertRaises(OSError): os.fstat(fd)

    def test_admitted_source_aba_is_refused_while_real_original_fd_remains_held(self):
        ctx = self.basic(); path, _, body = self.admission_fixture(ctx)
        fd, _ = M.admitted_tar(ctx); path.rename(path.with_name('retained-original.tar'))
        path.write_bytes(body); path.chmod(0o600)
        self.assertEqual(os.pread(fd, len(body), 0), body)
        with self.assertRaisesRegex(RuntimeError, '^input_changed$'): ctx.check()
        with self.assertRaisesRegex(RuntimeError, '^input_changed$'): self.stack.close()
        with self.assertRaises(OSError): os.fstat(fd)

    def test_actual_pipe_framing_roundtrip_and_truncation_are_bounded(self):
        ctx = SimpleNamespace(capacity=Mock()); read_fd, write_fd = os.pipe()
        self.stack.callback(os.close, read_fd); self.stack.callback(os.close, write_fd)
        expected = {'schema': 'ordinary_public_frame/1', 'accepted': False}
        M.frame(ctx, write_fd, expected); self.assertEqual(M.read_frame(ctx, read_fd), expected)
        with self.assertRaisesRegex(RuntimeError, '^read_bound$'): M.read_part(ctx, read_fd, 0)
        with self.assertRaisesRegex(RuntimeError, '^frame_bound$'): M.frame(ctx, write_fd, {'ordinary': 'x' * 65536})
        read2, write2 = os.pipe(); os.write(write2, struct.pack('!I', 10) + b'{}'); os.close(write2)
        with self.assertRaisesRegex(RuntimeError, '^frame_short$'): M.read_frame(ctx, read2)
        os.close(read2)

    def test_nonblocking_write_retries_only_owned_pipe_and_preserves_exact_bytes(self):
        ctx = SimpleNamespace(capacity=Mock()); read_fd, write_fd = os.pipe()
        self.stack.callback(os.close, read_fd); self.stack.callback(os.close, write_fd)
        actual_write = os.write; attempts = []
        def interrupted(fd, value):
            self.assertEqual(fd, write_fd); attempts.append(bytes(value))
            if len(attempts) == 1: raise BlockingIOError('explicit owned-pipe readiness race')
            return actual_write(fd, value[:3])
        with patch.object(M.os, 'write', side_effect=interrupted): M.write_all(ctx, write_fd, b'public-body')
        self.assertEqual(os.read(read_fd, 64), b'public-body')
        self.assertGreater(len(attempts), 2); self.assertGreaterEqual(ctx.capacity.call_count, len(attempts))

    def test_real_empty_or_full_pipe_rechecks_deadline_after_readiness_timeout(self):
        reader, writer = os.pipe(); self.stack.callback(os.close, reader); self.stack.callback(os.close, writer)
        reads = Mock(side_effect=[None, RuntimeError('deadline')])
        with self.assertRaisesRegex(RuntimeError, '^deadline$'):
            M.read_part(SimpleNamespace(capacity=reads), reader, 1)
        self.assertEqual(reads.call_count, 2)
        os.set_blocking(writer, False); filled = 0
        while True:
            try:
                filled += os.write(writer, b'X' * 4096)
                self.assertLessEqual(filled, 1024**2, 'bounded owned pipe fixture')
            except BlockingIOError: break
        self.assertGreater(filled, 0)
        writes = Mock(side_effect=[None, RuntimeError('deadline')])
        with self.assertRaisesRegex(RuntimeError, '^deadline$'):
            M.write_all(SimpleNamespace(capacity=writes), writer, b'public-unwritten-body')
        self.assertEqual(writes.call_count, 2); self.assertEqual(os.read(reader, filled), b'X' * filled)

    def image_fixture(self):
        ctx = self.basic(); ctx.site = 'web'; ctx.op['admission_sha256'] = 'f' * 64
        ctx.spec.update(site='web', role='synthetic_fixture', candidate_config_id='sha256:' + 'c' * 64,
                        candidate_rootfs=['sha256:' + 'd' * 64], ci_run_id=1,
                        image_artifact={'digest': 'sha256:' + 'f' * 64})
        ctx.baseline = {'machine_id': 'a' * 32}
        expected = {'Cmd': ['ordinary'], 'Env': ['PUBLIC_FIXTURE=1'], 'Labels': {'ordinary': 'public'}}
        manifest = 'sha256:' + 'e' * 64
        admission = {'archive_sha256': 'a' * 64, 'source_identities': {
            ctx.spec['candidate_config_id']: {'kind': 'Docker_config_identity'},
            manifest: {'kind': 'OCI_manifest_to_config', 'media_type': 'application/vnd.oci.image.manifest.v1+json', 'size': 123}}}
        actual = {'id': ctx.spec['candidate_config_id'], 'os': 'linux', 'architecture': 'amd64',
                  'rootfs': {'Type': 'layers', 'Layers': ctx.spec['candidate_rootfs']}, 'config': deepcopy(expected)}
        descriptor = {'digest': manifest, 'mediaType': 'application/vnd.oci.image.manifest.v1+json', 'size': 123}
        def command(ctx, argv, **kwargs):
            self.assertEqual(argv[:len(M.DOCKER)], M.DOCKER)
            return M.encode(descriptor if 'Descriptor' in argv[-2] else actual)
        self.stack.enter_context(patch.object(M, 'selected_evidence', return_value={'image': {'Config': expected}}))
        return ctx, admission, actual, descriptor, command

    def test_loaded_config_and_descriptor_are_typed_complete_and_never_become_acceptance(self):
        ctx, admission, actual, descriptor, command = self.image_fixture()
        with patch.object(M, 'command', side_effect=command):
            receipt = M.image(ctx, ctx.spec['candidate_config_id'], admission)
            M.public(receipt); self.assertIs(receipt['security_accepted'], False)
            self.assertEqual(receipt['serving_roles_activated'], [])
            actual['id'] = descriptor['digest']
            self.assertEqual(M.image(ctx, actual['id'], admission)['candidate_local_image_id'], descriptor['digest'])
            descriptor['size'] = True
            with self.assertRaisesRegex(RuntimeError, '^daemon_descriptor$'): M.image(ctx, actual['id'], admission)
            actual['id'] = ctx.spec['candidate_config_id']; actual['config']['Tty'] = 1
            with self.assertRaisesRegex(RuntimeError, '^config_type$'): M.image(ctx, actual['id'], admission)
            actual['config']['Tty'] = True
            with self.assertRaisesRegex(RuntimeError, '^daemon_config_or_rootfs$'): M.image(ctx, actual['id'], admission)
            with self.assertRaisesRegex(RuntimeError, '^unknown_daemon_identity$'): M.image(ctx, 'sha256:' + '0' * 64, admission)

    def test_import_delta_refuses_extra_unknown_image_and_old_tag_drift(self):
        ctx, admission, _, _, _ = self.image_fixture()
        old = {'id': 'sha256:' + '1' * 64, 'repository': 'ordinary', 'tag': 'retained'}
        new = {'id': ctx.spec['candidate_config_id'], 'repository': '<none>', 'tag': '<none>'}
        ctx.baseline = {'image_map': [old]}
        observed = {'image_map': [old, new]}
        with patch.object(M, 'stable', return_value=observed), patch.object(M, 'image', return_value={'measured': True}) as inspect:
            self.assertEqual(M.measured(ctx, admission), {'measured': True})
            inspect.assert_called_once_with(ctx, new['id'], admission)
            observed['image_map'].append({'id': 'sha256:' + '2' * 64})
            with self.assertRaisesRegex(RuntimeError, '^image_map_delta$'): M.measured(ctx, admission)
            observed['image_map'] = [{**old, 'tag': 'foreign-drift'}, new]
            with self.assertRaisesRegex(RuntimeError, '^image_map_delta$'): M.measured(ctx, admission)

    def public_pipe(self, body):
        reader, writer = os.pipe(); os.write(writer, body); os.close(writer)
        self.stack.callback(os.close, reader)
        return reader

    def test_receive_phase_decodes_owned_zip_and_binds_helper_proof_without_loading(self):
        ctx, _, raw, body = self.image_zip_fixture(); ctx.root = self.root; ctx.site = 'web'
        ctx.spec.update(candidate_config_id='sha256:' + 'c' * 64, candidate_rootfs=['sha256:' + 'd' * 64])
        proof = {'physical_graph_closed': True, 'archive_bytes': len(body), 'archive_sha256': sha256(body).hexdigest(),
                 'expanded_layer_bytes': 17, 'rootfs_layers': ctx.spec['candidate_rootfs'],
                 'source_identities': {ctx.spec['candidate_config_id']: {'kind': 'Docker_config_identity'}}}
        ctx.spec['tar_sha256'] = proof['archive_sha256']; ctx.baseline = {'image_map': []}
        ctx.T = object(); ctx.L = object()
        def admission(stream, size, end, data, transport, moby):
            self.assertEqual(stream.read(), body); self.assertEqual(size, len(body)); self.assertIs(transport, ctx.T)
            return proof
        ctx.D = SimpleNamespace(**{name: getattr(D, name) for name in ('Output', 'CompressedReader', 'digest_fd')},
                                validate_archive=admission)
        incoming = self.public_pipe(raw); real_read = M.read_part
        with patch.object(M, 'read_part', side_effect=lambda c, fd, count: real_read(c, incoming if fd == 0 else fd, count)), \
             patch.object(M, 'selected_evidence', return_value={'explicit_inert_archive_seam': True}), \
             patch.object(M, 'stable', return_value={'image_map': []}), patch.object(M, 'command') as rpc:
            result = M.receive_web(ctx)
        self.assertEqual(result['status'], 'archive_admitted_not_loaded'); rpc.assert_not_called()
        record = M.document((ctx.work / 'admission.json').read_bytes())
        self.assertEqual(record['archive_sha256'], sha256(body).hexdigest())
        self.assertEqual(ctx.budget, 2 * len(body) + 34)
        self.assertEqual(record['tar_identity'], M.identity((ctx.work / 'source.tar').lstat()))

    def test_receive_rejects_truncated_or_extra_input_without_guessing_source_outcome(self):
        ctx = self.basic(); ctx.site = 'web'; ctx.work.mkdir(mode=0o700); ctx.capacity = Mock()
        ctx.spec.update(image_artifact={'size_in_bytes': 10}, archive={'size_bytes': 10, 'uncompressed_bytes': 10})
        incoming = self.public_pipe(b'ordinary'); real_read = M.read_part
        with patch.object(M, 'read_part', side_effect=lambda c, fd, count: real_read(c, incoming, count)), \
             self.assertRaisesRegex(RuntimeError, '^source_short$'):
            M.receive_web(ctx)

    def test_load_web_measures_exact_new_image_only_and_unknown_rpc_is_never_retried(self):
        ctx = self.basic(); ctx.site = 'web'; ctx.spec['site'] = 'web'; ctx.work.mkdir(mode=0o700)
        path, admission, body = self.admission_fixture(ctx); ctx.baseline = {'image_map': []}
        calls = []
        def command(c, argv, **kwargs):
            calls.append(argv); self.assertEqual(argv, [*M.DOCKER, 'image', 'load', '--quiet'])
            self.assertEqual(os.pread(kwargs['stdin'], len(body), 0), body)
            return b'public inert Docker completion'
        with patch.object(M, 'stable', return_value={'image_map': []}), patch.object(M, 'command', side_effect=command), \
             patch.object(M, 'measured', return_value={'security_accepted': False}):
            result = M.load_web(ctx)
        self.assertEqual(len(calls), 1); self.assertTrue(ctx.requested)
        self.assertEqual(result['status'], 'loaded_measured_not_accepted'); ctx.check()

    def test_stream_web_sends_bound_header_actual_tar_and_completion_after_fragmented_start(self):
        ctx = self.basic(); ctx.site = 'web'; ctx.spec['site'] = 'native'; ctx.baseline = {'image_map': []}
        _, admission, body = self.admission_fixture(ctx)
        incoming = self.public_pipe(b'START\n'); outgoing = os.memfd_create('public-market-stream')
        self.stack.callback(os.close, outgoing); real_read, real_write = M.read_part, M.write_all
        def read(c, fd, count): return real_read(c, incoming if fd == 0 else fd, min(count, 2))
        with patch.object(M, 'stable', return_value={'image_map': []}), patch.object(M, 'read_part', side_effect=read), \
             patch.object(M, 'write_all', side_effect=lambda c, fd, raw: real_write(c, outgoing if fd == 1 else fd, raw)):
            self.assertIsNone(M.stream_web(ctx))
        raw = os.pread(outgoing, os.fstat(outgoing).st_size, 0); length = struct.unpack('!I', raw[:4])[0]
        first = M.document(raw[4:4 + length]); self.assertEqual(raw[4 + length:4 + length + len(body)], body)
        offset = 4 + length + len(body); last_size = struct.unpack('!I', raw[offset:offset + 4])[0]
        last = M.document(raw[offset + 4:offset + 4 + last_size]); M.header(ctx, first, admission); M.header(ctx, last, admission, complete=True)
        last['source_fd_held_and_verified'] = 1
        with self.assertRaisesRegex(RuntimeError, '^stream_binding$'): M.header(ctx, last, admission, complete=True)

    def test_client_cleanup_records_unproven_reap_or_close_and_has_no_cancellation_claim(self):
        ctx = SimpleNamespace(publish=Mock())
        bad = Mock(); bad.close.side_effect = OSError('explicit inert close failure')
        with patch.object(M, 'stop', side_effect=RuntimeError('explicit inert owned-client failure')):
            self.assertFalse(M.clients_close(ctx, [object()], [bad]))
        value = ctx.publish.call_args.args[1]
        self.assertEqual(value['causes'], ['client_descriptor_close_unproven', 'client_reap_unproven'])
        self.assertIs(value['daemon_cancellation_proven'], False)
        fd = os.memfd_create('public-cleanup-success')
        with patch.object(M, 'stop'): self.assertTrue(M.clients_close(ctx, [object()], [fd, io.BytesIO()]))
        with self.assertRaises(OSError): os.fstat(fd)

    def test_native_load_uses_real_pipes_and_only_accepts_both_bound_terminal_frames(self):
        self.native_load_case()

    def native_load_case(self, *, fail_cleanup=False, fail_measurement=False, fail_cleanup_receipt=False):
        ctx = self.basic(); ctx.site = 'bot'; ctx.spec['site'] = 'native'; ctx.op['nonce'] = 'a' * 32
        ctx.work.mkdir(mode=0o700); _, admission, body = self.admission_fixture(ctx); ctx.baseline = {'image_map': []}
        head = {'schema': 'market_image_stream/1', 'profile': ctx.spec['profile'], 'operation_sha256': ctx.pin,
                'admission_sha256': ctx.op['admission_sha256'], 'archive_bytes': len(body),
                'archive_sha256': sha256(body).hexdigest(), 'tar_identity': admission['tar_identity']}
        start = M.encode(head); finish = M.encode({**head, 'schema': 'market_image_stream_complete/1', 'source_fd_held_and_verified': True})
        source_read = self.public_pipe(struct.pack('!I', len(start)) + start + body + struct.pack('!I', len(finish)) + finish)
        ack_read, ack_write = os.pipe(); load_read, load_write = os.pipe()
        self.stack.callback(os.close, ack_read); self.stack.callback(os.close, load_read)
        source_in = os.fdopen(ack_write, 'wb'); source_out = os.fdopen(os.dup(source_read), 'rb')
        loader_in = os.fdopen(load_write, 'wb'); children = []
        class Child:
            def __init__(inner, **values): inner.__dict__.update(values); inner.polls = 0
            def poll(inner): inner.polls += 1; return None if inner.polls == 1 else 0
            def wait(inner, timeout): return 0
            def kill(inner): pass
        def launch(argv, **kwargs):
            self.assertTrue(callable(kwargs['preexec_fn']))
            if argv[:len(M.SSH)] == M.SSH: child = Child(stdin=source_in, stdout=source_out)
            else:
                self.assertEqual(argv, [*M.DOCKER, 'image', 'load', '--quiet']); os.write(kwargs['stdout'], b'ordinary')
                child = Child(stdin=loader_in)
            children.append(child); return child
        actual_close, actual_stop, actual_publish = M.clients_close, M.stop, ctx.publish
        stopped = []
        def stop(child):
            stopped.append(child)
            if fail_cleanup_receipt and len(stopped) == 1:
                raise OSError('explicit_inert_client_reap_failure')
            return actual_stop(child)
        def publish(name, value):
            if fail_cleanup_receipt and name == 'client-cleanup-unproven.json':
                raise OSError('explicit_owned_receipt_filesystem_failure')
            return actual_publish(name, value)
        ctx.publish = publish
        def cleanup(*args):
            closed = actual_close(*args); self.assertEqual(closed, not fail_cleanup_receipt)
            return closed and not fail_cleanup
        def measure(*args):
            if fail_measurement: raise RuntimeError('explicit_inert_measurement_refusal')
            return {'security_accepted': False}
        with patch.object(M.subprocess, 'Popen', side_effect=launch), patch.object(M, 'stable', return_value={'image_map': []}), \
             patch.object(M, 'measured', side_effect=measure), patch.object(M.time, 'sleep'), \
             patch.object(M, 'clients_close', side_effect=cleanup), patch.object(M, 'stop', side_effect=stop):
            if fail_cleanup or fail_measurement:
                reason = 'explicit_inert_measurement_refusal' if fail_measurement else 'client_cleanup_unproven'
                with self.assertRaisesRegex(RuntimeError, '^' + reason + '$'): M.load_native(ctx)
            else:
                self.assertEqual(M.load_native(ctx)['status'], 'loaded_measured_not_accepted')
        self.assertEqual(len(children), 2)
        self.assertEqual(stopped, list(reversed(children)))
        self.assertEqual(os.read(ack_read, 64), b'START\n'); self.assertEqual(os.read(load_read, len(body) + 1), body)
        self.assertTrue(ctx.requested); self.assertTrue(source_in.closed and source_out.closed and loader_in.closed)

    def test_native_completed_rpc_with_unproven_client_cleanup_is_not_success(self):
        self.native_load_case(fail_cleanup=True)

    def test_native_measurement_failure_preserves_primary_refusal_and_still_closes_both_clients(self):
        self.native_load_case(fail_cleanup=True, fail_measurement=True)

    def test_primary_failure_survives_cleanup_receipt_filesystem_failure_and_all_clients_are_attempted(self):
        self.native_load_case(fail_measurement=True, fail_cleanup_receipt=True)

    def test_real_sealed_archive_helper_accepts_market_user_and_repeated_ordered_logical_layers(self):
        # Reuse the established tiny TAR producer, never an actual image/runtime source.
        from tests import test_datastore_candidate_stage as fixtures
        blob_name = lambda body: 'blobs/sha256/' + sha256(body).hexdigest()
        for layer_count in (18, 19):
            with self.subTest(layer_count=layer_count):
                bodies = [b'EXPLICIT_PUBLIC_EMPTY_LOGICAL_LAYER', b'EXPLICIT_PUBLIC_APPLICATION_LAYER']
                ordered = [bodies[0]] * (layer_count - 1) + [bodies[1]]
                layers = ['sha256:' + sha256(body).hexdigest() for body in ordered]
                config = {'architecture': 'amd64', 'created': '2026-10-25T00:00:00Z', 'history': [],
                    'os': 'linux', 'rootfs': {'type': 'layers', 'diff_ids': layers},
                    'config': {'User': '10001', 'WorkingDir': '/app', 'Cmd': ['python', '-m', 'ordinary'],
                               'Env': ['EXPLICIT_PUBLIC_FIXTURE=1'], 'Labels': {'fixture': 'owned-public'}}}
                config['history'] = [{'comment': 'earlier comment never selected'}, {'comment': 'last comment'}]
                if layer_count == 19: config['comment'] = 'nonempty top comment wins'
                config_raw = D.encode(config); config_id = 'sha256:' + sha256(config_raw).hexdigest()
                descriptors = [{'mediaType': 'application/vnd.oci.image.layer.v1.tar', 'size': len(body),
                    'digest': 'sha256:' + sha256(body).hexdigest()} for body in ordered]
                manifest_raw = D.encode({'schemaVersion': 2, 'mediaType': D.OCI_MANIFEST,
                    'config': {'mediaType': 'application/vnd.oci.image.config.v1+json', 'digest': config_id,
                               'size': len(config_raw)}, 'layers': descriptors})
                manifest_id = 'sha256:' + sha256(manifest_raw).hexdigest()
                index = {'schemaVersion': 2, 'mediaType': D.OCI_INDEX, 'manifests': [
                    {'mediaType': D.OCI_MANIFEST, 'digest': manifest_id, 'size': len(manifest_raw)}]}
                rows = [(blob_name(config_raw), config_raw), (blob_name(manifest_raw), manifest_raw),
                    ('index.json', D.encode(index)), ('oci-layout', D.encode({'imageLayoutVersion': '1.0.0'})),
                    ('manifest.json', D.encode([{'Config': blob_name(config_raw),
                        'Layers': [blob_name(body) for body in ordered], 'RepoTags': None}]))]
                rows.extend((blob_name(body), body) for body in bodies)
                rows.extend(D.legacy_configs(config, fixtures.M).items())
                raw = fixtures.DatastoreCandidateTests.tar(None, rows)
                ctx = self.basic(); ctx.root = self.root / ('helper-' + str(layer_count)); ctx.root.mkdir(mode=0o700)
                evidence = ctx.root / 'evidence'; evidence.mkdir(mode=0o700)
                ctx.spec.update(candidate_config_id=config_id, candidate_rootfs=layers,
                    archive={'uncompressed_bytes': len(raw)})
                exported = {'image_id': config_id, 'archive': ctx.spec['archive'],
                    'security_accepted': False, 'deployment_eligible': False}
                report = {'Metadata': {'ImageID': config_id, 'ImageConfig': config}}
                export_raw, report_raw = M.encode(exported), M.encode(report)
                zip_raw = self.zipper([('image-export.json', export_raw), ('reports/vulnerabilities.json', report_raw)])
                target = evidence / (ctx.spec['profile'] + '.zip'); target.write_bytes(zip_raw); target.chmod(0o600)
                ctx.spec.update(public_export_bytes=len(export_raw), public_export_sha256=sha256(export_raw).hexdigest(),
                    public_raw_report_sha256=sha256(report_raw).hexdigest(),
                    evidence_artifact={'size_in_bytes': len(zip_raw), 'digest': 'sha256:' + sha256(zip_raw).hexdigest()})
                data = M.selected_evidence(ctx)
                archive = self.private('market-helper-' + str(layer_count) + '.tar', raw)
                fd, size, check = self.stack.enter_context(D.held(archive, len(raw), sha256(raw).hexdigest(), len(raw)))
                stream = os.fdopen(os.dup(fd), 'rb'); self.stack.callback(stream.close)
                proof = D.validate_archive(stream, size, ctx.end, data, fixtures.T, fixtures.M); check(); ctx.check()
                self.assertIs(proof['physical_graph_closed'], True); self.assertIs(proof['security_accepted'], False)
                self.assertEqual(proof['rootfs_layers'], layers); self.assertEqual(proof['legacy_compatibility_configs'], layer_count)
                self.assertEqual(proof['expanded_layer_bytes'], sum(map(len, ordered)))
                self.assertEqual(proof['archive_sha256'], sha256(raw).hexdigest())
                data['image']['Config']['User'] = '0'; stream.seek(0)
                with self.assertRaisesRegex(D.Refusal, '^archive_config$'):
                    D.validate_archive(stream, size, ctx.end, data, fixtures.T, fixtures.M)

    def coordinator_case(self, phase, *, response=None, fetch_code=0, cleanup_failure=False, drift=False):
        ctx = self.basic(); ctx.site = 'bot'; ctx.op.update(phase=phase, nonce='a' * 32)
        ctx.spec['image_artifact'] = {'id': 123}; ctx.baseline = {'image_map': []}
        ctx.work = self.root / ('coordinator-' + phase); ctx.work.mkdir(mode=0o700)
        ctx.capacity = lambda *args: ctx.check(); artifact = b'OWNED_PUBLIC_ZIP_TRANSPORT_FIXTURE'
        expected = 'archive_admitted_not_loaded' if phase == 'receive-web' else 'loaded_measured_not_accepted'
        result = response if response is not None else {'status': expected, 'receipt_sha256': 'b' * 64, 'security_accepted': False}
        children = []; calls = []
        class Fetch:
            def __init__(inner):
                inner.stdout = os.fdopen(os.dup(self.public_pipe(artifact)), 'rb'); inner.polls = 0
            def poll(inner): inner.polls += 1; return None if inner.polls == 1 else fetch_code
            def wait(inner, timeout): return fetch_code
            def kill(inner): pass
        def launch(argv, **kwargs):
            self.assertEqual(argv, ['/usr/bin/gh', 'api', '--method', 'GET',
                'repos/mohsenbarari/trading_bot/actions/artifacts/123/zip'])
            self.assertEqual(kwargs['stdin'], M.subprocess.DEVNULL)
            child = Fetch(); children.append(child); return child
        def command(current, argv, **kwargs):
            calls.append(argv); self.assertTrue(current.requested)
            self.assertTrue((current.work / 'web-request.json').is_file())
            self.assertEqual(argv, M.remote(ctx, phase))
            if phase == 'receive-web': self.assertEqual(kwargs['stdin'].read(), artifact)
            else: self.assertEqual(kwargs['stdin'], M.subprocess.DEVNULL)
            return M.encode(result)
        close = M.clients_close
        def cleanup(*args):
            clean = close(*args); self.assertTrue(clean)
            return clean and not cleanup_failure
        with patch.object(M.subprocess, 'Popen', side_effect=launch), patch.object(M, 'command', side_effect=command), \
             patch.object(M, 'stable', return_value={'image_map': [{'foreign': True}] if drift else []}), \
             patch.object(M, 'clients_close', side_effect=cleanup), patch.object(M.time, 'sleep'):
            try: value = M.coordinate_web(ctx)
            finally:
                self.assertTrue(ctx.requested); self.assertEqual(len(calls), 1)
                self.assertTrue(all(child.stdout.closed for child in children))
        self.assertEqual(value, result); return ctx

    def test_coordinator_keeps_local_image_store_unchanged_and_transports_only_explicit_artifact(self):
        for phase in ('receive-web', 'load-web'):
            with self.subTest(phase=phase): self.coordinator_case(phase)

    def test_coordinator_unknown_fetch_or_remote_result_is_never_retried_or_accepted(self):
        with self.assertRaisesRegex(RuntimeError, '^artifact_client_unknown$'):
            self.coordinator_case('receive-web', fetch_code=3)
        with self.assertRaisesRegex(RuntimeError, '^remote_result$'):
            self.coordinator_case('load-web', response={'status': 'loaded_measured_not_accepted',
                'receipt_sha256': 'b' * 64, 'security_accepted': True}, cleanup_failure=True)

    def test_coordinator_rejects_native_drift_and_unproven_cleanup_after_successful_remote_receipt(self):
        with self.assertRaisesRegex(RuntimeError, '^native_runtime_changed$'):
            self.coordinator_case('receive-web', drift=True)
        with self.assertRaisesRegex(RuntimeError, '^client_cleanup_unproven$'):
            self.coordinator_case('load-web', cleanup_failure=True)

    def context_fixture(self, *, site='bot', phase='load-native', change=None):
        folder = Path(tempfile.mkdtemp(prefix='public-context-', dir=self.root)); folder.chmod(0o700)
        for name in ('source', 'controls', 'attempts', 'data'): (folder / name).mkdir(mode=0o700)
        worker = folder / 'market_candidate_images.py'
        body = (ROOT / 'deploy/maintenance/market_candidate_images.py').read_bytes()
        worker.write_bytes(body); worker.chmod(0o600)
        helpers = {}
        for name in ('datastore_candidate_contract.py', 'runtime_image_archive_transport.py',
                     'admit_security_component_image_reuse.py', 'compose_market_security_os_candidate.py'):
            # Explicit inert ancillary module, never represented as actual helper bytes.
            raw = ('SYNTHETIC_PUBLIC_HELPER = ' + repr(name) + '\n').encode()
            p = folder / 'source' / name; p.write_bytes(raw); p.chmod(0o600)
            helpers['public-fixture/' + name] = {'sha256': sha256(raw).hexdigest()}
        catalog = {'helpers': helpers, 'profiles': [{'profile': 'owned-fixture', 'site': 'native', 'role': 'synthetic'}]}
        raw = M.encode(catalog); path = folder / 'market_candidate_images.json'; path.write_bytes(raw); path.chmod(0o600)
        now = int(M.time.time()); nonce = 'a' * 32
        op = {'schema': 'market_image_store_control/1', 'operation': M.OPERATION, 'phase': 'load-native',
            'profile': 'owned-fixture', 'nonce': nonce, 'not_before': now - 1, 'deadline': now + 60,
            'worker_sha256': sha256(body).hexdigest(), 'baselines': {key: {'observed_at': now,
                'store': {'root': str(folder / 'data')}, 'temporary_storage': []} for key in ('bot', 'web')},
            'fences': {'bot': {}, 'web': {}}, 'admission': 'owned-fixture-admission.json', 'admission_sha256': 'b' * 64}
        if change: change(op, catalog)
        raw = M.encode(catalog); path.write_bytes(raw)
        control = folder / 'controls' / (nonce + '.json'); control.write_bytes(M.encode(op)); control.chmod(0o600)
        args = SimpleNamespace(site=site, phase=phase, control=control, control_sha256=sha256(control.read_bytes()).hexdigest())
        self.stack.enter_context(patch.object(M, 'TOOLS', {'bot': folder, 'web': folder}))
        self.stack.enter_context(patch.object(M, 'CATALOG_SHA', sha256(raw).hexdigest()))
        self.stack.enter_context(patch.object(M, '__file__', str(worker)))
        scoped = self.stack.enter_context(patch.object(M, 'scope'))
        command = self.stack.enter_context(patch.object(M, 'command', return_value=
            b'RuntimeMaxUSec=15min\nTimeoutStopUSec=5s\nKillMode=control-group\nSendSIGKILL=yes\n'))
        return args, scoped, command, folder

    def test_context_keeps_actual_control_catalog_and_source_fds_and_finite_unit_binding(self):
        args, scoped, command, folder = self.context_fixture()
        ctx = M.Context(args, self.stack)
        scoped.assert_called_once_with('bot', 'load-native', 'a' * 32)
        self.assertIn('--property=RuntimeMaxUSec,TimeoutStopUSec,KillMode,SendSIGKILL', command.call_args.args[1])
        self.assertEqual(ctx.work, folder / 'attempts/owned-fixture-load-native')
        ctx.check(); (folder / 'source/datastore_candidate_contract.py').chmod(0o640)
        with self.assertRaisesRegex(RuntimeError, '^source_changed$'): ctx.check()

    def test_context_rejects_boolean_deadlines_unbound_profile_phase_and_stale_baseline(self):
        cases = [(lambda op, cat: op.update(deadline=True), 'deadline'),
                 (lambda op, cat: op.update(profile='unknown'), 'profile_scope'),
                 (lambda op, cat: op.update(extra='unbound'), 'control_fields'),
                 (lambda op, cat: op['baselines']['bot'].update(observed_at=0), 'baseline_freshness')]
        for change, reason in cases:
            args, _, _, _ = self.context_fixture(change=change)
            with self.subTest(reason=reason), self.assertRaisesRegex(RuntimeError, '^' + reason + '$'):
                M.Context(args, self.stack)
        args, _, _, _ = self.context_fixture(phase='load-web')
        with self.assertRaisesRegex(RuntimeError, '^phase_scope$'): M.Context(args, self.stack)

    def daemon_fixture(self):
        fragment = self.private('public-docker.service', b'[Service]\nPrivateTmp=false\n')
        dropin = self.private('public-docker.conf', b'[Service]\nTasksMax=ordinary\n')
        fields = {'FragmentPath': str(fragment), 'DropInPaths': str(dropin), 'NeedDaemonReload': 'no',
                  'PrivateTmp': 'no', 'RootDirectory': '', 'RootImage': '', 'TemporaryFileSystem': ''}
        raw = lambda: ''.join(k + '=' + v + '\n' for k, v in fields.items()).encode()
        return fragment, dropin, fields, raw

    def test_daemon_policy_retains_only_public_unit_metadata_and_refuses_namespace_overrides(self):
        fragment, dropin, fields, raw = self.daemon_fixture(); ctx = self.basic()
        with patch.object(M, 'command', side_effect=lambda *args, **kwargs: raw()):
            value = M.daemon_policy(ctx)
            self.assertEqual([row['path'] for row in value['files']], [str(fragment), str(dropin)])
            self.assertIs(value['runtime_override_absence_proven'], False)
            fields['RootDirectory'] = '/explicit-public-invalid-root'
            with self.assertRaisesRegex(RuntimeError, '^daemon_temporary_storage_contract$'): M.daemon_policy(ctx)
            fields['RootDirectory'] = ''; dropin.chmod(0o666)
            with self.assertRaisesRegex(RuntimeError, '^daemon_public_file_metadata$'): M.daemon_policy(ctx)

    def test_collect_uses_real_owned_source_and_only_observes_a_stable_public_snapshot(self):
        worker = self.private('public-worker.py', (ROOT / 'deploy/maintenance/market_candidate_images.py').read_bytes())
        store = self.root / 'store'; store.mkdir(mode=0o700)
        machine = self.private('public-machine-id', b'a' * 32 + b'\n'); real_path = Path
        observation = {'containers': [{'id': 'public-inert-cid'}], 'image_map': []}
        with patch.object(M, '__file__', str(worker)), patch.object(M, 'Path', side_effect=lambda p:
                          machine if p == '/etc/machine-id' else real_path(p)), \
             patch.object(M, 'command', side_effect=lambda ctx, *args, **kwargs:
                          (ctx.capacity(), M.encode({'root': str(store), 'driver': 'public', 'version': 'public'}))[1]), \
             patch.object(M, 'snapshot', return_value=observation), patch.object(M, 'daemon_policy', return_value={'files': []}), \
             patch.object(M, 'temp_metadata', return_value=[{'public': 'metadata'}]):
            value = M.collect('bot', sha256(worker.read_bytes()).hexdigest())
        self.assertEqual(value['machine_id'], 'a' * 32); self.assertEqual(value['containers'], observation['containers'])
        self.assertIs(value['security_accepted'], False); self.assertEqual(value['store']['identity'], M.identity(store.lstat())[:5])

    def run_fixture(self, phase='load-native', site='bot'):
        ctx = self.basic(); ctx.args = SimpleNamespace(phase=phase); ctx.site = site; ctx.op['phase'] = phase
        marker, source, reservation = self.owned_guard(ctx); ctx.site = site
        if site == 'web': ctx.op['fences']['web'] = {'marker': M.identity(marker.lstat())}
        machine = self.private('run-machine-id', b'a' * 32 + b'\n'); store = self.root / 'store'; store.mkdir(mode=0o700)
        policy = {'properties': {'public': 'unchanged'}, 'files': []}
        ctx.baseline = {'machine_id': 'a' * 32, 'daemon': policy, 'image_map': [],
            'store': {'root': str(store), 'driver': 'public', 'version': 'public', 'identity': M.identity(store.lstat())[:5],
                      'mount': {'filesystems': [{'source': 'owned-public-fixture', 'uuid': 'public', 'fstype': 'fixture', 'maj:min': '0:1'}]}}}
        real_path = Path
        self.stack.enter_context(patch.object(M, 'Path', side_effect=lambda p: machine if p == '/etc/machine-id' else real_path(p)))
        self.stack.enter_context(patch.object(M, 'daemon_policy', return_value=policy))
        self.stack.enter_context(patch.object(M, 'command', side_effect=lambda c, argv, **kwargs:
            M.encode(ctx.baseline['store']['mount'] if argv[0] == '/usr/bin/findmnt' else
                     {k: v for k, v in ctx.baseline['store'].items() if k not in {'identity', 'mount'}})))
        self.stack.enter_context(patch.object(M, 'stable', return_value={'image_map': []}))
        ctx.capacity = lambda *args: ctx.check()
        return ctx, marker, source, reservation

    def test_run_real_guard_and_final_checkpoint_keep_tar_fd_until_operation_exit(self):
        ctx, marker, source, reservation = self.run_fixture(); path, _, body = self.admission_fixture(ctx)
        observed = {}
        def handler(current):
            fd, _ = M.admitted_tar(current); observed['fd'] = fd
            self.assertEqual(os.pread(fd, len(body), 0), body)
            return {'status': 'explicit_inert_phase', 'security_accepted': False}
        with patch.object(M, 'load_native', side_effect=handler):
            result = M.run(ctx, True)
        self.assertEqual(result['status'], 'explicit_inert_phase'); self.assertFalse(reservation.exists())
        self.assertEqual(os.pread(observed['fd'], len(body), 0), body); self.assertEqual(source.stat().st_size, 0)
        self.stack.close()
        with self.assertRaises(OSError): os.fstat(observed['fd'])

    def test_unknown_phase_outcome_keeps_owned_reservation_and_does_not_retry_or_relax_source(self):
        ctx, marker, source, reservation = self.run_fixture()
        original = M.identity(source.lstat())
        def unknown(current): current.requested = True; raise RuntimeError('explicit_unknown_rpc_outcome')
        with patch.object(M, 'load_native', side_effect=unknown) as phase, \
             self.assertRaisesRegex(RuntimeError, '^explicit_unknown_rpc_outcome$'):
            M.run(ctx, True)
        phase.assert_called_once(); self.assertTrue(reservation.is_file()); self.assertTrue(ctx.requested)
        self.assertEqual(M.identity(source.lstat()), original)
        self.assertEqual(M.document(reservation.read_bytes())['operation_sha256'], ctx.pin)

    def test_readonly_run_and_both_site_dispatch_paths_have_no_unrequested_activation(self):
        ctx, _, _, reservation = self.run_fixture(phase='receive-web')
        self.assertEqual(M.run(ctx, False)['status'], 'public_preflight_passed_not_requested')
        self.assertFalse(reservation.exists())
        with patch.object(M, 'coordinate_web', return_value={'status': 'explicit_inert_remote', 'security_accepted': False}) as phase:
            self.assertEqual(M.run(ctx, True)['status'], 'explicit_inert_remote'); phase.assert_called_once_with(ctx)

    def test_main_serializes_only_closed_outcome_and_stream_failure_does_not_pollute_protocol(self):
        args = ['--site', 'bot', '--phase', 'load-native', '--control', '/explicit-public-control', '--control-sha256', 'a' * 64]
        public_ctx = SimpleNamespace(end=M.time.monotonic() + 60)
        for extra, result, expected in [([], {'status': 'public_preflight', 'security_accepted': False}, 0),
                                         (['--apply', 'wrong'], None, 1)]:
            out = io.StringIO()
            with patch.object(M, 'Context', return_value=public_ctx), patch.object(M, 'run', return_value=result), \
                 patch.object(M.signal, 'alarm'), redirect_stdout(out):
                self.assertEqual(M.main(args + extra), expected)
            self.assertIs(json.loads(out.getvalue())['security_accepted'], False)
        out = io.StringIO()
        with patch.object(M, 'Context', side_effect=RuntimeError('explicit_private_looking_failure_never_echoed')), \
             patch.object(M.signal, 'alarm'), redirect_stdout(out):
            self.assertEqual(M.main(['--site', 'web', '--phase', 'stream-web']), 1)
        self.assertEqual(out.getvalue(), '')
        out = io.StringIO()
        with patch.object(M, 'collect', return_value={'status': 'public_snapshot', 'security_accepted': False}) as collect, \
             patch.object(M.signal, 'alarm'), redirect_stdout(out):
            self.assertEqual(M.main(['--site', 'bot', '--phase', 'snapshot', '--source-sha256', 'a' * 64]), 0)
        collect.assert_called_once_with('bot', 'a' * 64)

    def test_literal_refusal_catalog_is_complete_and_unknown_error_arguments_cannot_leak(self):
        tree = ast.parse((ROOT / 'deploy/maintenance/market_candidate_images.py').read_text())
        literal_codes = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name): continue
            position = 1 if node.func.id == 'need' else 0 if node.func.id == 'RuntimeError' else None
            if position is not None and len(node.args) > position and isinstance(node.args[position], ast.Constant):
                code = node.args[position].value
                if isinstance(code, str): literal_codes.add(code)
        self.assertEqual(M.PUBLIC_CODES, literal_codes)
        args = ['--site', 'bot', '--phase', 'load-native', '--control', '/explicit-public-control',
                '--control-sha256', 'a' * 64]
        for failure in (RuntimeError(['EXPLICIT_PRIVATE_LOOKING_UNHASHABLE']),
                        RuntimeError({'EXPLICIT_PRIVATE_LOOKING_KEY': 'never-echo'}),
                        RuntimeError('EXPLICIT_PRIVATE_LOOKING_VALUE'), ValueError('never-echo')):
            with self.subTest(kind=type(failure.args[0]).__name__):
                out = io.StringIO(); ctx = SimpleNamespace(end=M.time.monotonic() + 60, D=D)
                with patch.object(M, 'Context', return_value=ctx), patch.object(M, 'run', side_effect=failure), \
                     patch.object(M.signal, 'alarm'), redirect_stdout(out):
                    self.assertEqual(M.main(args), 1)
                record = json.loads(out.getvalue()); self.assertEqual(record['code'], 'unexpected_failure')
                self.assertIs(record['retry_allowed'], False); self.assertIs(record['security_accepted'], False)
                self.assertNotIn('EXPLICIT_PRIVATE', out.getvalue()); self.assertNotIn('never-echo', out.getvalue())
        out = io.StringIO(); ctx = SimpleNamespace(end=M.time.monotonic() + 60, D=D)
        with patch.object(M, 'Context', return_value=ctx), \
             patch.object(M, 'run', side_effect=RuntimeError('client_cleanup_unproven')), \
             patch.object(M.signal, 'alarm'), redirect_stdout(out):
            self.assertEqual(M.main(args), 1)
        self.assertEqual(json.loads(out.getvalue())['code'], 'client_cleanup_unproven')
