"""Pinned public source and owned synthetic spools; no runtime or image actions."""
from contextlib import ExitStack, redirect_stdout
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import ast
import base64
import errno
import fcntl
import importlib.util
import io
import json
import os
from pathlib import Path
import resource
import signal
import sqlite3
import sys
import tempfile
import time
from types import MethodType, ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch
import zlib

ROOT = Path(__file__).resolve().parents[1]
PUBLIC_SOURCE_ZLIB = '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'
REAL_FORK, REAL_WAITPID, REAL_KILL = os.fork, os.waitpid, os.kill
REAL_LIMIT, REAL_CLOSERANGE, REAL_DUP2 = resource.setrlimit, os.closerange, os.dup2
DENIED = ('subprocess.run', 'subprocess.Popen', 'subprocess.call', 'subprocess.check_call',
          'subprocess.check_output', 'os.system', 'os.popen', 'os.fork', 'os.forkpty',
          'os.posix_spawn', 'os.posix_spawnp', 'os.kill', 'os.killpg', 'os.waitpid',
          'socket.socket', 'socket.create_connection', 'socket.getaddrinfo',
          'urllib.request.urlopen', 'resource.setrlimit', 'os.closerange', 'os.dup2')


def fenced(stack):
    for name in DENIED:
        stack.enter_context(patch(name, side_effect=AssertionError('ambient operation denied')))


def public_source():
    raw = zlib.decompress(base64.b64decode(PUBLIC_SOURCE_ZLIB))
    if len(raw) != 111954 or sha256(raw).hexdigest() != '1b9c611692978edec4c37de217e77abf00f5a0e456006060c936d40c53978929':
        raise AssertionError('public historical source fixture pin')
    return raw


with ExitStack() as importing:
    fenced(importing)
    spec = importlib.util.spec_from_file_location('_public_f79_preparer', ROOT/'scripts/prepare_f79_conflict_diagnostic.py')
    H = importlib.util.module_from_spec(spec); spec.loader.exec_module(H)


class F79DiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack(); self.addCleanup(self.stack.close); fenced(self.stack)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix='public-f79-diagnostic-')))
        self.root.chmod(0o700)
        self.children = []; self.parent = os.getpid(); self.addCleanup(self.reap_owned)
        self.stderr_packets = []
        self.module = self.load_overlay()

    def load_overlay(self):
        # The only adapter dependency is a closed fixture stub. No application
        # package, config, session or environment file is imported by this test.
        raw = H.make_overlay(public_source())
        package = ModuleType('_public_f79_fixture'); package.__path__ = []
        adapter = ModuleType('_public_f79_fixture.capture_event_adapter')
        adapter.CaptureEvent = type('CaptureEvent', (), {})
        adapter.CaptureEventContractError = type('CaptureEventContractError', (RuntimeError,), {})
        def decode(*_args, **_kwargs):
            raise AssertionError('ingress decoder outside diagnostic test scope')
        adapter.decode_capture_event = decode
        module = ModuleType('_public_f79_fixture.private_capture'); module.__package__ = '_public_f79_fixture'
        self.stack.enter_context(patch.dict(sys.modules, {package.__name__: package, adapter.__name__: adapter, module.__name__: module}))
        exec(compile(raw, '<pinned-public-f79-overlay>', 'exec'), module.__dict__)
        return module

    def document(self):
        return {'schema': 'market_channel_event', 'schema_version': '1.0',
                'event_type': 'message_created', 'event_id': 'public-fixture-event',
                'occurred_at_utc': '2026-10-25T00:00:00Z',
                'source': {'code': 'MELTED_PRIMARY_FLOW'},
                'producer': {'name': 'public-fixture', 'version': '1', 'capture_sequence': 7, 'origin': 'live'},
                'message': {'text': 'PRIVATE_PUBLIC_FIXTURE_OLD', 'sender': {'telegram_id': 123, 'kind': 'user'}}}

    def context(self, old=None, new=None):
        old = old or self.document(); new = new or deepcopy(old)
        if new == old:
            new['message']['text'] = 'PRIVATE_PUBLIC_FIXTURE_NEW'
        return ('MELTED_PRIMARY_FLOW', 7, self.module.semantic_payload_hash(old), old['event_id'], new)

    def spool(self, old=None, *, day=0, body=None):
        directory = self.root/'spool'
        directory.mkdir(mode=0o700, exist_ok=True)
        date = self.module.utc_now().date() - timedelta(days=day)
        path = directory/('events-'+date.strftime('%Y-%m-%d')+'.jsonl')
        path.write_bytes(body if body is not None else self.module.canonical_json(old or self.document())+b'\n')
        path.chmod(0o600)
        return directory, path

    def real_children(self, stderr_fd=None):
        stack = ExitStack(); self.addCleanup(stack.close)
        if stderr_fd is None:
            read, stderr_fd = os.pipe2(os.O_CLOEXEC)
            os.set_blocking(read, False)
            stack.callback(os.close, read); stack.callback(os.close, stderr_fd)
            def drain():
                packet = bytearray()
                while True:
                    try: block = os.read(read, 4097)
                    except BlockingIOError: break
                    if not block: break
                    packet.extend(block)
                    self.assertLessEqual(len(packet), 4096)
                if packet: self.stderr_packets.append(bytes(packet))
            stack.callback(drain)
        def fork():
            self.assertEqual(os.getpid(), self.parent)
            pid = REAL_FORK()
            if pid: self.children.append(pid)
            else: REAL_DUP2(stderr_fd, 2)
            return pid
        def wait(pid, options):
            self.assertIn(pid, self.children)
            return REAL_WAITPID(pid, options)
        def kill(pid, number):
            self.assertIn(pid, self.children); self.assertEqual(number, signal.SIGKILL)
            return REAL_KILL(pid, number)
        def child_only(function):
            def call(*args):
                if os.getpid() == self.parent: raise AssertionError('parent process mutation denied')
                return function(*args)
            return call
        stack.enter_context(patch('os.fork', side_effect=fork))
        stack.enter_context(patch('os.waitpid', side_effect=wait))
        stack.enter_context(patch('os.kill', side_effect=kill))
        stack.enter_context(patch('resource.setrlimit', side_effect=child_only(REAL_LIMIT)))
        stack.enter_context(patch('os.closerange', side_effect=child_only(REAL_CLOSERANGE)))
        stack.enter_context(patch('os.dup2', side_effect=child_only(REAL_DUP2)))
        return stack

    def reap_owned(self):
        for pid in self.children:
            try:
                observed, _ = REAL_WAITPID(pid, os.WNOHANG)
            except ChildProcessError:
                continue
            if not observed:
                try: REAL_KILL(pid, signal.SIGKILL)
                except ProcessLookupError: pass
                try: REAL_WAITPID(pid, 0)
                except ChildProcessError: pass

    def assert_reaped(self):
        for pid in self.children:
            with self.assertRaises(ChildProcessError): REAL_WAITPID(pid, os.WNOHANG)

    def test_cold_source_pin_precedes_any_parse_or_compile_and_reverse_patch_is_exact(self):
        raw = public_source()
        with patch.object(H.ast, 'parse', side_effect=AssertionError('parse before pin')):
            for bad in (raw[:-1], raw[:-1]+b'X', bytearray(raw), b'raise RuntimeError("private")'):
                with self.assertRaisesRegex(RuntimeError, '^historical_source_pin$'): H.make_overlay(bad)
        overlay = H.make_overlay(raw)
        self.assertEqual(overlay[:-len(H.DIAGNOSTIC.encode())].replace(H.NEW_RAISE, H.RAISE).replace(H.NEW_ACCEPT, H.ACCEPT), raw)
        original = ast.parse(raw); changed = ast.parse(overlay)
        old_functions = {node.name: ast.dump(node) for node in original.body if isinstance(node, (ast.FunctionDef, ast.ClassDef))}
        new_functions = {node.name: ast.dump(node) for node in changed.body if isinstance(node, (ast.FunctionDef, ast.ClassDef))}
        for name in old_functions.keys()-{'CaptureState', 'CaptureEngine'}:
            self.assertEqual(old_functions[name], new_functions[name], name)

    def test_prepare_writes_only_exact_public_overlay_and_truthful_unbuilt_manifest(self):
        source = self.root/'historical.py'; source.write_bytes(public_source()); source.chmod(0o600)
        output = self.root/'overlay'; manifest = H.prepare(source, output)
        self.assertEqual({p.name for p in output.iterdir()}, {'private_capture.py', 'Dockerfile', 'manifest.json'})
        self.assertEqual((output/'private_capture.py').read_bytes(), H.make_overlay(public_source()))
        self.assertEqual(manifest['source_sha256'], H.SOURCE_SHA)
        self.assertFalse(manifest['image_built']); self.assertFalse(manifest['runtime_changed'])
        self.assertFalse(manifest['functional_fix_claimed']); self.assertFalse(manifest['security_accepted'])
        self.assertEqual(manifest['limits'], {'wall_seconds': 1, 'cleanup_seconds': .25, 'read_bytes': 32*1024**2,
                         'files': 8, 'output_bytes': 4096, 'additional_address_space_bytes': 32*1024**2, 'record_bytes': 256*1024,
                         'child_fds': ['own_spool_directory', 'bounded_reply_pipe', 'duplicate_existing_stderr_writer'],
                         'parent_emits_diagnostic': False,
                         'process_ram_rate_limit': {'attempts': 1, 'seconds': 60, 'event_identifiers_stored': False}})
        self.assertIn(b'COPY --chown=10001:10001 --chmod=0644', (output/'Dockerfile').read_bytes())
        with self.assertRaises(FileExistsError): H.prepare(source, output)

    def test_cli_prepares_actual_pinned_public_overlay_and_refuses_replacing_existing_output(self):
        source = self.root/'public-historical.py'; source.write_bytes(public_source()); source.chmod(0o600)
        output = self.root/'cli-overlay'; captured = io.StringIO()
        with redirect_stdout(captured):
            self.assertEqual(H.main(['--source',str(source),'--output',str(output)]),0)
        value = json.loads(captured.getvalue())
        self.assertEqual(value,{'status':'public_source_overlay_prepared',
            'overlay_sha256':sha256((output/'private_capture.py').read_bytes()).hexdigest(),
            'image_built':False,'security_accepted':False})
        before = {p.name:p.read_bytes() for p in output.iterdir()}; captured = io.StringIO()
        with redirect_stdout(captured):
            self.assertEqual(H.main(['--source',str(source),'--output',str(output)]),1)
        self.assertEqual(json.loads(captured.getvalue()),{'status':'refused','code':'diagnostic_preparation_refused','security_accepted':False})
        self.assertEqual({p.name:p.read_bytes() for p in output.iterdir()},before)

    def test_cli_bad_arguments_and_untrusted_source_return_only_closed_failure(self):
        marker = 'PUBLIC_SYNTHETIC_ARGUMENT_NEVER_EMIT'; source = self.root/(marker+'.py')
        source.write_bytes(b'PUBLIC WRONG SOURCE'); source.chmod(0o600)
        for arguments in ([],['--unknown-'+marker],['--source',str(source),'--output',str(self.root/'never-created')]):
            captured = io.StringIO()
            with self.subTest(arguments=arguments),redirect_stdout(captured):
                self.assertEqual(H.main(arguments),1)
            self.assertEqual(captured.getvalue(),'{"status":"refused","code":"diagnostic_preparation_refused","security_accepted":false}\n')
            self.assertNotIn(marker,captured.getvalue()); self.assertFalse((self.root/'never-created').exists())

    def test_error_keeps_original_args_and_context_is_private_typed_and_value_free_on_output(self):
        old = self.document(); context = self.context(old)
        error = self.module._f79_conflict_error({'capture_sequence': 7, 'payload_sha256': context[2]},
                                             SimpleNamespace(event_id=context[3]), context[4], context[0])
        self.assertIs(type(error), self.module.CaptureRuntimeError)
        self.assertEqual(error.args, ('capture_event_identity_conflict',))
        self.assertEqual(error._f79_conflict_context, context)
        value = self.module._f79_diag_meta('account1', context)
        self.assertNotIn('PRIVATE_PUBLIC_FIXTURE', json.dumps(value))
        self.assertNotIn(context[2], json.dumps(value)); self.assertNotIn(context[3], json.dumps(value))
        for bad in (None, list(context), (context[0], True, *context[2:]), ('PRIVATE', *context[1:]),
                    (context[0], context[1], 'PRIVATE', *context[3:])):
            self.assertEqual(self.module._f79_diag_collect(self.root, 'account1', bad)['comparison_status'], 'context_unavailable')

    def test_comparison_uses_only_fixed_labels_and_preserves_transport_and_identity_normalization(self):
        old = self.document(); new = deepcopy(old)
        new['producer'].update(capture_sequence=99, origin='explicit_backfill', available_at_utc='PRIVATE', received_at_utc='PRIVATE', is_backfill=True)
        new['message']['is_backfill'] = True; new['event_type'] = 'message_snapshot'
        self.assertEqual(self.module.semantic_payload_hash(old), self.module.semantic_payload_hash(new))
        self.assertEqual(self.module._f79_diag_compare(old, new), [])
        new['message']['text'] = 'PRIVATE_PUBLIC_NEW'; new['message']['sender']['telegram_id'] = True
        new['PRIVATE_UNCLASSIFIED_KEY'] = 'PRIVATE_UNCLASSIFIED_VALUE'
        labels = self.module._f79_diag_compare(old, new)
        self.assertEqual(labels, ['message_text', 'other_semantic_fields', 'sender_telegram_id'])
        self.assertTrue(set(labels) <= self.module._F79_DIAG_LABELS)

    def test_closed_emitter_rejects_unclassified_values_extra_fields_boolean_spoofing_and_duplicates(self):
        value = self.module._f79_diag_meta('account1', self.context())
        value.update(comparison_status='compared', changed_fields=['message_text'])
        with patch.object(self.module.os, 'write') as write:
            self.module._f79_emit_diagnostic(value)
            self.assertEqual(write.call_count, 1)
            descriptor, raw = write.call_args.args
            self.assertEqual(descriptor, 5); self.assertLessEqual(len(raw), 4096)
            self.assertEqual(json.loads(raw), value); self.assertNotIn(b'PRIVATE', raw)
            for change in ({'extra': 'PRIVATE'}, {'source_code': 'PRIVATE'}, {'account': 'PRIVATE'},
                           {'rollback_confirmed': 1}, {'security_accepted': 0},
                           {'changed_fields': ['PRIVATE']}, {'changed_fields': ['message_text', 'message_text']},
                           {'comparison_status': 'PRIVATE'}, {'event_type': 'PRIVATE'}):
                write.reset_mock(); self.module._f79_emit_diagnostic({**value, **change}); write.assert_not_called()

    def test_real_owned_child_compares_fixed_spool_and_reaps_without_fd_leaks(self):
        directory, _ = self.spool(); before = len(os.listdir('/proc/self/fd'))
        with self.real_children():
            value = self.module._f79_diag_collect(directory, 'account1', self.context())
        self.assertEqual(value['comparison_status'], 'compared')
        self.assertEqual(value['changed_fields'], ['message_text'])
        self.assertEqual(value['previous_origin'], 'live'); self.assertFalse(value['security_accepted'])
        self.assertNotIn('PRIVATE', json.dumps(value)); self.assert_reaped()
        self.assertEqual(json.loads(self.stderr_packets[-1]), value)
        self.assertEqual(len(os.listdir('/proc/self/fd')), before)

    def test_fixed_eight_days_read_record_bounds_and_old_identity_are_closed(self):
        for case, expected in (('ninth', 'not_found'), ('bound', 'read_bound'), ('large', 'invalid_record'), ('boundary', 'compared'),
                               ('partial', 'invalid_record'), ('duplicate', 'invalid_record'), ('mismatch', 'old_identity_mismatch')):
            with self.subTest(case=case), tempfile.TemporaryDirectory(dir=self.root) as temporary:
                directory = Path(temporary); directory.chmod(0o700)
                day = 8 if case == 'ninth' else 0
                date = self.module.utc_now().date()-timedelta(days=day)
                path = directory/('events-'+date.strftime('%Y-%m-%d')+'.jsonl')
                old = self.document()
                if case == 'mismatch': old['message']['text'] = 'PRIVATE_ALTERED_OLD'
                if case == 'boundary':
                    old['message']['text'] = ''
                    old['message']['text'] = 'x'*(256*1024-len(self.module.canonical_json(old)))
                    self.assertEqual(len(self.module.canonical_json(old)), 256*1024)
                body = self.module.canonical_json(old)+b'\n'
                if case == 'large': body = b'x'*(256*1024+1)+b'\n'
                if case == 'partial': body = body[:-1]
                if case == 'duplicate': body = b'{"event_id":"a","event_id":"b"}\n'
                path.write_bytes(body); path.chmod(0o600)
                with self.real_children(), patch.object(self.module, '_F79_DIAG_READ_BYTES', 32 if case == 'bound' else 32*1024**2):
                    result = self.module._f79_diag_collect(directory, 'account1', self.context(old) if case == 'boundary' else self.context())
                self.assertEqual(result['comparison_status'], expected)
                self.assertEqual(result['changed_fields'], ['message_text'] if case == 'boundary' else [])
                self.assertNotIn('PRIVATE', json.dumps(result)); self.assert_reaped()

    def test_spool_member_mode_hardlinks_and_symlinks_are_refused_without_reading_the_target(self):
        directory, path = self.spool()
        for kind in ('mode', 'hardlink', 'symlink'):
            with self.subTest(kind=kind):
                if kind == 'mode': path.chmod(0o640)
                if kind == 'hardlink': os.link(path, directory/'public-retained-link')
                if kind == 'symlink':
                    path.rename(directory/'public-retained-source'); path.symlink_to(directory/'public-retained-source')
                with self.real_children(): result = self.module._f79_diag_collect(directory, 'account1', self.context())
                self.assertEqual(result['comparison_status'], 'unavailable'); self.assert_reaped()
                if kind == 'mode': path.chmod(0o600)
                if kind == 'hardlink': (directory/'public-retained-link').unlink()
                if kind == 'symlink':
                    path.unlink(); (directory/'public-retained-source').rename(path)

    def test_child_output_limit_is_checked_before_any_emission_or_reply(self):
        directory, _ = self.spool(); packets = len(self.stderr_packets)
        with self.real_children(), patch.object(self.module, '_F79_DIAG_OUTPUT_BYTES', 32):
            value = self.module._f79_diag_collect(directory, 'account1', self.context())
        self.assertEqual(value['comparison_status'], 'unavailable'); self.assert_reaped()
        self.assertEqual(len(self.stderr_packets), packets)

    def test_child_has_hard_limits_and_closes_all_inherited_stdio_and_private_fds(self):
        directory, _ = self.spool(); private = os.open(self.root/'public-inherited.db', os.O_RDWR|os.O_CREAT|os.O_EXCL, 0o600)
        inherited = fcntl.fcntl(private, fcntl.F_DUPFD_CLOEXEC, 40); self.addCleanup(os.close, private); self.addCleanup(os.close, inherited)
        expected = self.module._f79_diag_meta('account1', self.context())
        def lookup(*_):
            for descriptor in (0, 1, 2, inherited):
                try: os.fstat(descriptor)
                except OSError as error:
                    if error.errno != errno.EBADF: return expected
                else: return expected
            for kind, limit in ((resource.RLIMIT_CORE, 0), (resource.RLIMIT_FSIZE, 0), (resource.RLIMIT_CPU, 1), (resource.RLIMIT_NPROC, 0)):
                if resource.getrlimit(kind) != (limit, limit): return expected
            lower, upper = resource.getrlimit(resource.RLIMIT_AS)
            if lower != upper or lower <= 0: return expected
            return {**expected, 'comparison_status': 'compared'}
        before = {kind: resource.getrlimit(kind) for kind in (resource.RLIMIT_CORE, resource.RLIMIT_FSIZE, resource.RLIMIT_CPU, resource.RLIMIT_NPROC, resource.RLIMIT_AS)}
        with self.real_children(), patch.object(self.module, '_f79_diag_lookup', side_effect=lookup):
            result = self.module._f79_diag_collect(directory, 'account1', self.context())
        self.assertEqual(result['comparison_status'], 'compared'); self.assert_reaped()
        self.assertEqual({kind: resource.getrlimit(kind) for kind in before}, before)
        self.assertEqual(os.fstat(inherited).st_ino, os.fstat(private).st_ino)

    def test_real_child_wall_timeout_kills_reaps_and_keeps_fixed_timeout_status(self):
        directory, _ = self.spool()
        def delay(*_): time.sleep(.3)
        before = len(os.listdir('/proc/self/fd'))
        with self.real_children(), patch.object(self.module, '_F79_DIAG_SECONDS', .03), patch.object(self.module, '_f79_diag_lookup', side_effect=delay):
            result = self.module._f79_diag_collect(directory, 'account1', self.context())
        self.assertEqual(result['comparison_status'], 'timeout'); self.assert_reaped()
        self.assertEqual(len(os.listdir('/proc/self/fd')), before)

    def test_full_owned_stderr_pipe_cannot_block_parent_or_change_its_flags(self):
        directory, _ = self.spool(); before = len(os.listdir('/proc/self/fd'))
        read, write = os.pipe2(os.O_CLOEXEC|os.O_NONBLOCK)
        try:
            capacity = fcntl.fcntl(write, fcntl.F_GETPIPE_SZ); filled = 0
            while True:
                try: filled += os.write(write, b'x'*4096)
                except BlockingIOError: break
            self.assertEqual(filled, capacity)
            flags = fcntl.fcntl(write, fcntl.F_GETFL) & ~os.O_NONBLOCK
            fcntl.fcntl(write, fcntl.F_SETFL, flags)
            parent_flags = fcntl.fcntl(2, fcntl.F_GETFL)
            started = time.monotonic()
            with self.real_children(stderr_fd=write), patch.object(self.module, '_F79_DIAG_SECONDS', .03):
                result = self.module._f79_diag_collect(directory, 'account1', self.context())
            self.assertLess(time.monotonic()-started, .75)
            self.assertEqual(result['comparison_status'], 'timeout'); self.assert_reaped()
            self.assertEqual(fcntl.fcntl(write, fcntl.F_GETFL), flags)
            self.assertEqual(fcntl.fcntl(2, fcntl.F_GETFL), parent_flags)
            self.assertEqual(os.read(read, capacity+1), b'x'*capacity)
        finally: os.close(read); os.close(write)
        self.assertEqual(len(os.listdir('/proc/self/fd')), before)

    def test_spool_path_metadata_stall_is_inside_child_wall_deadline(self):
        directory, _ = self.spool(); resolve = Path.resolve
        def stalled(path, *args, **kwargs):
            if path == directory:
                self.assertNotEqual(os.getpid(), self.parent, 'spool metadata must be child-only')
                time.sleep(.3)
            return resolve(path, *args, **kwargs)
        started = time.monotonic(); before = len(os.listdir('/proc/self/fd'))
        with self.real_children(), patch.object(Path, 'resolve', autospec=True, side_effect=stalled), \
             patch.object(self.module, '_F79_DIAG_SECONDS', .03):
            result = self.module._f79_diag_collect(directory, 'account1', self.context())
        self.assertLess(time.monotonic()-started, .75)
        self.assertEqual(result['comparison_status'], 'timeout'); self.assert_reaped()
        self.assertEqual(len(os.listdir('/proc/self/fd')), before)

    def test_actual_stage_rolls_back_before_diagnostic_and_original_exception_survives_all_faults(self):
        old = self.document(); context = self.context(old)
        for fault in ('collect', 'no_parent_emit'):
            module = self.load_overlay()  # Independent process-local rate state per fault case.
            connection = sqlite3.connect(':memory:'); connection.row_factory = sqlite3.Row; self.addCleanup(connection.close)
            connection.execute('CREATE TABLE capture_seen(event_id TEXT,capture_sequence INTEGER,payload_sha256 TEXT)')
            connection.execute('INSERT INTO capture_seen VALUES(?,?,?)', (context[3], context[1], context[2])); connection.commit()
            state = SimpleNamespace(connection=connection, account='account1')
            state.stage = MethodType(module.CaptureState.stage, state)
            engine = module.CaptureEngine.__new__(module.CaptureEngine); engine.state = state; engine.spool = SimpleNamespace(directory=self.root)
            errors = []; real_error = module._f79_conflict_error
            def capture(*args):
                result = real_error(*args); errors.append(result); return result
            def collect(*args):
                self.assertIs(connection.in_transaction, False)
                if fault == 'collect': raise RuntimeError('PRIVATE_LOOKUP_FAILURE')
                return module._f79_diag_meta('account1', context)
            event = SimpleNamespace(event_id=context[3])
            with self.subTest(fault=fault), patch.object(module, 'validate_ingress', return_value=(context[0], event)), \
                 patch.object(module, '_f79_conflict_error', side_effect=capture), patch.object(module, '_f79_diag_collect', side_effect=collect), \
                 patch.object(module, '_f79_emit_diagnostic', side_effect=AssertionError('parent report forbidden')) as emit:
                with self.assertRaises(module.CaptureRuntimeError) as caught: engine.accept(context[4])
            emit.assert_not_called()
            self.assertIs(caught.exception, errors[0]); self.assertEqual(caught.exception.args, ('capture_event_identity_conflict',))
            self.assertFalse(connection.in_transaction)
            self.assertEqual(tuple(connection.execute('SELECT * FROM capture_seen').fetchone()), (context[3], context[1], context[2]))

    def test_real_diagnostic_lookup_report_kill_and_wait_faults_preserve_the_original_conflict(self):
        directory, _ = self.spool()
        for fault in ('lookup', 'report', 'kill', 'wait'):
            module = self.load_overlay()  # Independent process-local rate state per fault case.
            context = self.context(); connection = sqlite3.connect(':memory:')
            connection.row_factory = sqlite3.Row; self.addCleanup(connection.close)
            connection.execute('CREATE TABLE capture_seen(event_id TEXT,capture_sequence INTEGER,payload_sha256 TEXT)')
            connection.execute('INSERT INTO capture_seen VALUES(?,?,?)', (context[3], context[1], context[2])); connection.commit()
            state = SimpleNamespace(connection=connection, account='account1')
            state.stage = MethodType(module.CaptureState.stage, state)
            engine = module.CaptureEngine.__new__(module.CaptureEngine); engine.state = state; engine.spool = SimpleNamespace(directory=directory)
            errors = []; real_error = module._f79_conflict_error
            def capture(*args):
                value = real_error(*args); errors.append(value); return value
            def lookup(*_args):
                if fault == 'lookup': raise RuntimeError('PRIVATE_LOOKUP_FAULT')
                time.sleep(.3)
            def kill(pid, number):
                self.assertIn(pid, self.children); REAL_KILL(pid, number)
                raise OSError(errno.EIO, 'PRIVATE_KILL_FAULT')
            def wait(pid, options):
                self.assertIn(pid, self.children)
                value = REAL_WAITPID(pid, options)
                if value[0]: raise OSError(errno.EIO, 'PRIVATE_WAIT_FAULT')
                return value
            with self.subTest(fault=fault), self.real_children(), ExitStack() as changes:
                changes.enter_context(patch.object(module, 'validate_ingress', return_value=(context[0], SimpleNamespace(event_id=context[3]))))
                changes.enter_context(patch.object(module, '_f79_conflict_error', side_effect=capture))
                changes.enter_context(patch.object(module, '_F79_DIAG_SECONDS', .03))
                if fault == 'report': changes.enter_context(patch.object(module, '_f79_emit_diagnostic', side_effect=RuntimeError('PRIVATE_REPORT_FAULT')))
                else: changes.enter_context(patch.object(module, '_f79_diag_lookup', side_effect=lookup))
                if fault == 'kill': changes.enter_context(patch.object(module.os, 'kill', side_effect=kill))
                if fault == 'wait': changes.enter_context(patch.object(module.os, 'waitpid', side_effect=wait))
                with self.assertRaises(module.CaptureRuntimeError) as caught: engine.accept(context[4])
            self.assertIs(caught.exception, errors[0]); self.assertEqual(caught.exception.args, ('capture_event_identity_conflict',))
            self.assertFalse(connection.in_transaction); self.assert_reaped()
            self.assertTrue(all(b'PRIVATE' not in value for value in self.stderr_packets))

    def test_unfinished_actual_transaction_never_runs_diagnostic_or_changes_original_error(self):
        connection = sqlite3.connect(':memory:'); self.addCleanup(connection.close); connection.execute('BEGIN')
        error = self.module.CaptureRuntimeError('capture_event_identity_conflict')
        def stage(*_args, **_kwargs): raise error
        engine = self.module.CaptureEngine.__new__(self.module.CaptureEngine)
        engine.state = SimpleNamespace(connection=connection, account='account1', stage=stage)
        engine.spool = SimpleNamespace(directory=self.root)
        with patch.object(self.module, '_f79_diag_collect', side_effect=AssertionError('unrolled transaction')) as collect:
            with self.assertRaises(self.module.CaptureRuntimeError) as caught: engine.accept(self.document())
        self.assertIs(caught.exception, error); self.assertTrue(connection.in_transaction); collect.assert_not_called()
        connection.rollback()

    def test_rate_admission_is_one_ram_float_with_exact_sixty_second_boundary_and_no_identity_keys(self):
        function = next(node for node in ast.parse(H.DIAGNOSTIC).body
                        if isinstance(node, ast.FunctionDef) and node.name == '_f79_diag_admit')
        self.assertEqual(function.args.args, [])
        self.assertEqual([name for node in ast.walk(function) if isinstance(node, ast.Global) for name in node.names],
                         ['_F79_DIAG_NEXT_ALLOWED'])
        self.assertFalse(any(isinstance(node, (ast.Dict, ast.List, ast.Set, ast.DictComp, ast.ListComp, ast.SetComp))
                             for node in ast.walk(function)))
        self.assertIs(type(self.module._F79_DIAG_NEXT_ALLOWED), float)
        with patch.object(self.module._f79_time, 'monotonic', side_effect=[100.0, 159.999, 160.0]):
            self.assertTrue(self.module._f79_diag_admit())
            self.assertEqual(self.module._F79_DIAG_NEXT_ALLOWED, 160.0)
            self.assertFalse(self.module._f79_diag_admit())
            self.assertEqual(self.module._F79_DIAG_NEXT_ALLOWED, 160.0)
            self.assertTrue(self.module._f79_diag_admit())
            self.assertEqual(self.module._F79_DIAG_NEXT_ALLOWED, 220.0)

    def test_two_actual_sqlite_conflicts_within_one_minute_only_fork_once_and_keep_both_errors(self):
        module = self.module; context = self.context(); directory, _ = self.spool()
        connection = sqlite3.connect(':memory:'); connection.row_factory = sqlite3.Row; self.addCleanup(connection.close)
        connection.execute('CREATE TABLE capture_seen(event_id TEXT,capture_sequence INTEGER,payload_sha256 TEXT)')
        connection.execute('INSERT INTO capture_seen VALUES(?,?,?)', (context[3], context[1], context[2])); connection.commit()
        state = SimpleNamespace(connection=connection, account='account1')
        state.stage = MethodType(module.CaptureState.stage, state)
        engine = module.CaptureEngine.__new__(module.CaptureEngine); engine.state = state; engine.spool = SimpleNamespace(directory=directory)
        errors = []; real_error = module._f79_conflict_error
        def capture(*args):
            value = real_error(*args); errors.append(value); return value
        with self.real_children(), patch.object(module, 'validate_ingress', return_value=(context[0], SimpleNamespace(event_id=context[3]))), \
             patch.object(module, '_f79_conflict_error', side_effect=capture), \
             patch.object(module, '_f79_diag_collect', wraps=module._f79_diag_collect) as collect:
            for index in range(2):
                with self.assertRaises(module.CaptureRuntimeError) as caught: engine.accept(context[4])
                self.assertIs(caught.exception, errors[index])
                self.assertEqual(caught.exception.args, ('capture_event_identity_conflict',))
                self.assertFalse(connection.in_transaction)
                self.assertEqual(len(self.children), 1)
            self.assertEqual(collect.call_count, 1)
        self.assertEqual(len(self.stderr_packets), 1)
        self.assertEqual(json.loads(self.stderr_packets[0])['comparison_status'], 'compared')
        self.assertIs(type(module._F79_DIAG_NEXT_ALLOWED), float)
        self.assertEqual(tuple(connection.execute('SELECT * FROM capture_seen').fetchone()), (context[3], context[1], context[2]))
        self.assert_reaped()


if __name__ == '__main__': unittest.main()
