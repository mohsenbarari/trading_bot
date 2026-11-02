"""Public synthetic source/exception fixtures only; never import capture code."""
import ast
from contextlib import ExitStack, redirect_stderr
from hashlib import sha256
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('_capture2_observability', ROOT / 'scripts/prepare_capture2_observability.py')
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)


class Capture2Observability(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        for name in ('subprocess.run', 'subprocess.Popen', 'os.system', 'socket.socket.connect',
                     'socket.create_connection', 'socket.getaddrinfo', 'urllib.request.urlopen', 'sqlite3.connect'):
            self.stack.enter_context(patch(name, side_effect=AssertionError('no host/network/session/DB')))
        self.directory = Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix='.capture2-overlay-', dir=ROOT)))
        self.public = b'def main():\n    try:\n        return run_service()\n' + M.OLD + b'    return 2\n'
        self.stack.enter_context(patch.object(M, 'SOURCE_BYTES', len(self.public)))
        self.stack.enter_context(patch.object(M, 'SOURCE_SHA', sha256(self.public).hexdigest()))

    def exception_report(self, exception, filename='/app/core/market_intelligence/private_capture_service.py'):
        source = M.patch_source(self.public)
        namespace = {'OSError': OSError, 'ValueError': ValueError, 'sqlite3': sqlite3,
                     'safe_json': lambda value: json.dumps(value, sort_keys=True), 'sys': types.SimpleNamespace(stderr=io.StringIO())}
        origin = {'problem': exception}
        exec(compile('def raise_problem():\n    raise problem\n', filename, 'exec'), origin)
        namespace['run_service'] = origin['raise_problem']
        exec(compile(source, '/app/core/market_intelligence/private_pipeline_foundation.py', 'exec'), namespace)
        self.assertEqual(namespace['main'](), 1)
        raw = namespace['sys'].stderr.getvalue()
        self.assertLess(len(raw), 384)
        self.assertEqual(raw.count('\n'), 1)
        self.assertNotIn('PRIVATE_MARKER', raw)
        self.assertNotIn('Traceback', raw)
        return json.loads(raw)

    def test_exact_handler_only_changes_and_other_bytes_remain_identical(self):
        changed = M.patch_source(self.public)
        self.assertEqual(changed.replace(M.NEW, M.OLD, 1), self.public)
        self.assertEqual(changed.count(M.NEW), 1)
        for bad in (self.public + b'changed', self.public.replace(b'except', b'EXCEPT')):
            with self.assertRaisesRegex(RuntimeError, 'actual_base_source_mismatch'):
                M.patch_source(bad)

    def test_error_types_are_useful_bounded_and_messages_never_rendered(self):
        for error in (PermissionError('PRIVATE_MARKER/session'), ValueError('PRIVATE_MARKER'),
                      sqlite3.OperationalError('PRIVATE_MARKER/sqlite')):
            with self.subTest(kind=type(error).__name__):
                report = self.exception_report(error)
                self.assertEqual(report['error_type'], type(error).__name__)
                self.assertEqual(report['reason_code'], 'runtime_dependency_failure')
                self.assertEqual(report['origin'], 'capture_service')
                self.assertEqual(report['origin_line'], 2)

    def test_hostile_message_and_classname_cannot_enter_report(self):
        class Hostile(ValueError):
            def __str__(self): raise AssertionError('exception text forbidden')
        Hostile.__name__ = 'PRIVATE_MARKER' * 1000
        result = self.exception_report(Hostile(), '/private/PRIVATE_MARKER.py')
        self.assertEqual(result['error_type'], 'OtherDependencyError')
        self.assertEqual(result['origin'], 'pipeline_foundation')
        self.assertNotIn('PRIVATE_MARKER', json.dumps(result))

    def test_telethon_sqlite_is_static_origin_not_full_path(self):
        result = self.exception_report(sqlite3.OperationalError('PRIVATE_MARKER'),
                                       '/opt/market-python/telethon/sessions/sqlite.py')
        self.assertEqual(result['origin'], 'telethon_sqlite_session')
        self.assertNotIn('/opt/', json.dumps(result))

    def test_trace_depth_limit_is_bounded_without_traceback_output(self):
        error = ValueError('PRIVATE_MARKER')
        namespace = {'problem': error}
        exec(compile('def recurse(n):\n    if n: return recurse(n-1)\n    raise problem\n', '/private/PRIVATE_MARKER.py', 'exec'), namespace)
        try: namespace['recurse'](80)
        except ValueError as value: error = value
        result = self.exception_report(error)
        self.assertEqual(result['origin'], 'trace_depth_limit')
        self.assertEqual(result['origin_line'], 0)

    def test_no_output_is_read_only_and_explicit_output_is_closed_public_context(self):
        with patch.object(M, 'public_source', return_value=self.public):
            record = M.prepare(ROOT)
            self.assertEqual(list(self.directory.iterdir()), [])
            target = self.directory / 'context'
            previous = os.umask(0o077)
            try: self.assertEqual(M.prepare(ROOT, target), record)
            finally: os.umask(previous)
            self.assertEqual({p.name for p in target.iterdir()}, {'Dockerfile', 'private_pipeline_foundation.py', 'overlay.json'})
            self.assertEqual(target.stat().st_mode & 0o777, 0o700)
            for path in target.iterdir():
                self.assertEqual(path.stat().st_mode & 0o777, 0o644 if path.name == 'private_pipeline_foundation.py' else 0o600)
            self.assertFalse(record['build_performed'])
            self.assertFalse(record['runtime_changed'])
            with self.assertRaisesRegex(RuntimeError, 'existing_output_refused'): M.prepare(ROOT, target)

    def test_recipe_has_only_exact_base_and_one_owned_copy(self):
        text = M.DOCKERFILE.decode()
        self.assertEqual(len(text.splitlines()), 2)
        self.assertEqual(text.splitlines()[0], 'FROM ' + M.BASE_TAG)
        self.assertIn('COPY --chown=10001:10001 ', text)
        self.assertNotIn('--chmod', text)
        for forbidden in ('RUN ', 'USER ', 'ENV ', 'ENTRYPOINT ', 'CMD ', 'ADD ', 'pip ', 'apt '):
            self.assertNotIn(forbidden, text)


if __name__ == '__main__': unittest.main()
