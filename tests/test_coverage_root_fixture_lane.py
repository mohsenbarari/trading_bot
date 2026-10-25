"""Ordinary suite identities and public coverage buffers; no container execution."""
from contextlib import ExitStack
from copy import deepcopy
from hashlib import sha256
import base64
import importlib.util
import json
import io
import sys
import os
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
TARGETS = ('subprocess.run', 'subprocess.Popen', 'os.system', 'os.kill', 'os.killpg',
           'socket.socket', 'socket.create_connection', 'socket.getaddrinfo',
           'urllib.request.urlopen', 'ctypes.CDLL', 'resource.setrlimit', 'signal.signal', 'signal.alarm')
with ExitStack() as fence:
    for name in TARGETS: fence.enter_context(patch(name, side_effect=AssertionError('ambient_denied')))
    spec = importlib.util.spec_from_file_location('_coverage_fixture_lane', ROOT/'scripts/run_coverage_unittest.py')
    M = importlib.util.module_from_spec(spec); spec.loader.exec_module(M)


class FixtureLaneTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack(); self.addCleanup(self.stack.close)
        for name in TARGETS: self.stack.enter_context(patch(name, side_effect=AssertionError('ambient_denied')))
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix='public-coverage-lane-')))
        old = Path.cwd(); os.chdir(self.root); self.addCleanup(os.chdir, old)
        (self.root/'tmp').mkdir(); self.commit = 'a'*40

    def case(self, name):
        return SimpleNamespace(id=lambda: name)

    def required(self):
        return [self.case(name + '.PublicTests.test_required') for name in M.ROOT_MODULES]

    def test_partition_is_complete_disjoint_and_never_runs_a_root_fixture_in_hosted_lane(self):
        roots = self.required(); ordinary = self.case('tests.test_business.Business.test_behavior')
        hosted, privileged = M.partition([ordinary, *roots])
        self.assertEqual(hosted, [ordinary]); self.assertEqual(privileged, roots)
        self.assertEqual(len(hosted) + len(privileged), 1 + len(M.ROOT_MODULES))
        self.assertFalse({v.id() for v in hosted} & {v.id() for v in privileged})

    def test_duplicate_and_missing_required_modules_refuse_instead_of_skipping(self):
        for values in (self.required()[:-1], [*self.required(), self.required()[0]]):
            with self.assertRaises(RuntimeError): M.partition(values)
        expected = M.expectation(self.commit, self.required())
        for field, value in (('modules', []), ('test_ids', []), ('source_commit', 'b'*40)):
            changed = {**expected, field: value}
            with self.assertRaises(RuntimeError): M.expected_ok(changed, self.commit)

    def test_hosted_duplicate_instances_preserve_existing_discovery_multiplicity(self):
        first = self.case('tests.test_business.ImportedTests.test_behavior')
        second = self.case(first.id())
        hosted, required = M.partition([first, second, *self.required()])
        self.assertEqual(hosted, [first, second]); self.assertEqual(len(required), len(M.ROOT_MODULES))
        self.assertEqual([test.id() for test in hosted], [first.id(), first.id()])

    def test_successful_early_stop_cannot_pass_an_incomplete_hosted_inventory(self):
        class StopsEarly(unittest.TestCase):
            def test_stops(self): self._outcome.result.stop()
            def test_would_run(self): pass
        result = unittest.TestResult()
        unittest.TestSuite([StopsEarly('test_stops'), StopsEarly('test_would_run')]).run(result)
        self.assertTrue(result.wasSuccessful()); self.assertEqual(result.testsRun, 1)
        self.assertFalse(M.complete(result, 2))
        self.assertTrue(M.complete(result, 1))

    def fixture(self):
        expected = M.expectation(self.commit, self.required())
        database = self.root/'ordinary-public-coverage.sqlite'
        with sqlite3.connect(database) as connection:
            connection.execute('CREATE TABLE IF NOT EXISTS public_fixture (name TEXT)')
        data = database.read_bytes()
        report = {'schema': 'coverage_root_fixture_result/1', 'source_commit': self.commit,
            'test_ids': expected['test_ids'], 'modules': list(M.ROOT_MODULES), 'uid': 0,
            'result': {'run': len(expected['test_ids']), 'failures': 0, 'errors': 0, 'skipped': 0,
                       'unexpected_successes': 0, 'expected_failures': 0, 'successful': True},
            'log': 'ordinary root fixture evidence\n', 'coverage_sha256': sha256(data).hexdigest(),
            'coverage_base64': base64.b64encode(data).decode(), 'production_inputs_present': False}
        expected_path = self.root/'expectation'; expected_path.write_bytes(M.encode(expected))
        args = SimpleNamespace(commit=self.commit, expectation=expected_path,
                               envelope=self.root/'envelope', report=self.root/'report')
        return args, report, data

    def test_ingest_binds_ids_commit_uid_and_coverage_bytes_before_merging(self):
        args, report, data = self.fixture(); args.envelope.write_bytes(M.encode(report))
        self.assertEqual(M.ingest(args), 0)
        self.assertEqual((self.root/'.coverage.root-fixtures').read_bytes(), data)
        proof = json.loads(args.report.read_bytes())
        self.assertNotIn('coverage_base64', proof); self.assertNotIn('log', proof)
        self.assertEqual(proof['test_ids'], report['test_ids'])

    def test_wrong_identity_digest_boolean_uid_or_count_cannot_be_a_root_pass(self):
        args, base, _ = self.fixture()
        for mutate in (lambda d: d.update(source_commit='b'*40), lambda d: d.update(uid=True),
                       lambda d: d.update(test_ids=d['test_ids'][:-1]),
                       lambda d: d.update(coverage_sha256='f'*64),
                       lambda d: d['result'].update(run=True), lambda d: d.update(production_inputs_present=True)):
            report = deepcopy(base); mutate(report); args.envelope.write_bytes(M.encode(report))
            with self.assertRaises(RuntimeError): M.ingest(args)
            self.assertFalse((self.root/'.coverage.root-fixtures').exists())

    def test_skip_expected_failure_or_failed_root_test_remains_required_failure(self):
        for field in ('skipped', 'expected_failures', 'failures', 'errors', 'unexpected_successes'):
            args, report, _ = self.fixture(); report['result'][field] = 1
            args.envelope.write_bytes(M.encode(report)); self.assertEqual(M.ingest(args), 1)

    def test_log_is_bounded_before_accumulation(self):
        log = M.BoundedLog(); log.bytes = 8*1024**2 - 1
        with self.assertRaisesRegex(RuntimeError, 'log_bound'): log.write('ordinary')
        self.assertEqual(log.getvalue(), '')

    def root_path_fixture(self, *, dockerenv=True, status='CapEff: 0000000000000000\nNoNewPrivs: 1\n', interfaces=('lo',)):
        class PublicPath:
            def __init__(inner, value): inner.value = value
            def is_file(inner): return dockerenv
            def read_text(inner): return status
            def iterdir(inner): return [SimpleNamespace(name=name) for name in interfaces]
        return patch.object(M, 'Path', side_effect=PublicPath)

    def test_root_entry_requires_uid_readonly_socket_absence_capabilities_nnp_and_loopback(self):
        def check(**changes):
            options = dict(uid=0, gid=0, socket=False, readonly=os.ST_RDONLY,
                           dockerenv=True, status='CapEff: 0000000000000000\nNoNewPrivs: 1\n', interfaces=('lo',))
            options.update(changes)
            with patch.object(M.os, 'geteuid', return_value=options['uid']), \
                 patch.object(M.os, 'getegid', return_value=options['gid']), \
                 patch.object(M.os.path, 'lexists', return_value=options['socket']), \
                 patch.object(M.os, 'statvfs', return_value=SimpleNamespace(f_flag=options['readonly'])), \
                 self.root_path_fixture(dockerenv=options['dockerenv'], status=options['status'], interfaces=options['interfaces']):
                return M.root_guard()
        check()
        for changes in ({'uid': 1}, {'gid': 1}, {'socket': True}, {'readonly': 0}, {'dockerenv': False},
                        {'status': 'CapEff: 1\nNoNewPrivs: 1\n'}, {'status': 'CapEff: 0\nNoNewPrivs: 0\n'},
                        {'interfaces': ('lo', 'eth0')}):
            with self.subTest(changes=changes), self.assertRaises(RuntimeError): check(**changes)

    def root_execution_fixture(self):
        args, _, data = self.fixture()
        reviewed = self.root/'reviewed'; reviewed.mkdir()
        (reviewed/'fixture_case.py').write_text('import unittest\nclass PublicTests(unittest.TestCase):\n def test_owned(self): self.assertTrue(True)\n')
        work = self.root/'work/source'; work.parent.mkdir()
        coverage_path = self.root/'work/root.coverage'
        module = 'fixture_case'
        case = self.case(module + '.PublicTests.test_owned')
        expected = {'schema': 'coverage_root_fixture_expectation/1', 'source_commit': self.commit,
                    'modules': [module], 'test_ids': [case.id()]}
        args.expectation.write_bytes(M.encode(expected))
        collector = SimpleNamespace(config=SimpleNamespace(data_file='/work/.coverage.root-fixtures'),
                                    stop=lambda: None, save=lambda: coverage_path.write_bytes(data))
        paths = {'/reviewed': reviewed, '/work/source': work, '/work/.coverage.root-fixtures': coverage_path}
        self.stack.enter_context(patch.object(M, 'Path', side_effect=lambda path: paths.get(path, Path(path))))
        self.stack.enter_context(patch.object(M, 'root_guard'))
        self.stack.enter_context(patch.object(M, 'ROOT_MODULES', (module,)))
        self.stack.enter_context(patch.object(M.signal, 'signal'))
        self.stack.enter_context(patch.object(M.signal, 'alarm'))
        self.stack.enter_context(patch.dict(sys.modules, {'coverage': SimpleNamespace(Coverage=SimpleNamespace(current=lambda: collector))}))
        saved_path = list(sys.path); self.addCleanup(lambda: sys.path.__setitem__(slice(None), saved_path))
        self.addCleanup(lambda: sys.modules.pop(module, None))
        output = io.TextIOWrapper(io.BytesIO(), encoding='utf-8')
        self.stack.enter_context(patch.object(M.sys, 'stdout', output))
        return args, collector, work, output

    def test_root_lane_uses_process_entry_collector_and_seals_real_owned_fixture_result(self):
        args, collector, work, output = self.root_execution_fixture()
        self.assertEqual(M.root_lane(args), 0)
        value = json.loads(output.buffer.getvalue())
        self.assertEqual(value['result']['run'], 1); self.assertTrue(value['result']['successful'])
        self.assertEqual(value['test_ids'], ['fixture_case.PublicTests.test_owned'])
        self.assertFalse(value['production_inputs_present']); self.assertTrue(work.is_dir())
        self.assertEqual(sha256(base64.b64decode(value['coverage_base64'])).hexdigest(), value['coverage_sha256'])
        M.root_guard.assert_called_once_with()

    def test_root_lane_installs_finite_wall_callback_that_refuses_expiry(self):
        args, collector, work, output = self.root_execution_fixture()
        with patch.object(M.signal, 'signal') as installed, patch.object(M.signal, 'alarm') as alarm:
            self.assertEqual(M.root_lane(args), 0)
        self.assertEqual(installed.call_args.args[0], M.signal.SIGALRM)
        alarm.assert_called_once_with(840)
        with self.assertRaisesRegex(RuntimeError, '^root_fixture_wall_deadline$'):
            installed.call_args.args[1](None, None)

    def test_root_lane_refuses_missing_collector_existing_workspace_and_wrong_test_identity(self):
        args, collector, work, output = self.root_execution_fixture()
        with patch.dict(sys.modules, {'coverage': SimpleNamespace(Coverage=SimpleNamespace(current=lambda: None))}), \
             self.assertRaisesRegex(RuntimeError, 'root_fixture_process_coverage_required'):
            M.root_lane(args)
        work.mkdir()
        with self.assertRaisesRegex(RuntimeError, 'root_fixture_work_preexisting'): M.root_lane(args)
        work.rmdir()
        expected = json.loads(args.expectation.read_bytes()); expected['test_ids'] = ['fixture_case.PublicTests.test_absent']
        args.expectation.write_bytes(M.encode(expected))
        with self.assertRaisesRegex(RuntimeError, 'root_fixture_identity_mismatch'): M.root_lane(args)
        self.assertEqual(output.buffer.getvalue(), b'')

    def test_real_workflow_requires_both_lanes_and_has_no_host_bind_or_socket(self):
        import yaml
        value = yaml.safe_load((ROOT/'.github/workflows/coverage-report.yml').read_bytes())
        steps = value['jobs']['repository-coverage']['steps']
        root = next(step for step in steps if step.get('id') == 'root_fixtures')['run']
        for flag in ('--user 0:0', '--network none', '--read-only', '--cap-drop ALL',
                     '--security-opt no-new-privileges', '--cpus 2', '--memory 4g',
                     '--memory-swap 4g', '--pids-limit 128', 'git -c tar.umask=0022 archive "$commit"',
                     '/work:rw,nosuid,nodev,noexec,size=1g,mode=0700'):
            self.assertIn(flag, root)
        self.assertNotIn('--volume', root); self.assertNotIn('--mount', root); self.assertNotIn('docker.sock', root)
        backend = next(step for step in steps if step.get('id') == 'backend_coverage')['run']
        source_filter = "--include='api/*,bot/*,core/*,models/*,scripts/*,main.py,manage.py,run_bot.py,schemas.py'"
        for lane in (backend, root):
            self.assertIn('coverage json ' + source_filter, lane)
            self.assertIn('coverage html ' + source_filter, lane)
            self.assertNotIn('ignore-errors', lane)
        aliases = (ROOT/'.coveragerc').read_text()
        self.assertIn('    /reviewed\n', aliases); self.assertIn('    /work/source\n', aliases)
        final = steps[-1]['run']; self.assertIn('"$root_fixture_exit_code" == \'0\'', final)
        self.assertIn('"$backend_suite_exit_code" == \'0\'', final)
        recipe = (ROOT/'deploy/maintenance/coverage_root_fixtures/Dockerfile').read_text()
        self.assertIn('COPY source/ /reviewed/', recipe)
        self.assertIn('WORKDIR /reviewed', recipe)
        self.assertIn('Jinja2==3.1.6', recipe)
        self.assertIn('Jinja2==3.1.6', (ROOT/'requirements-test.txt').read_text())
        self.assertIn('"-m","coverage","run","--branch"', recipe)
        self.assertIn('--data-file=/work/.coverage.root-fixtures', recipe)
        self.assertNotIn('COPY . ', recipe)
        self.assertNotIn("str(work/'tests')", (ROOT/'scripts/run_coverage_unittest.py').read_text())


if __name__ == '__main__': unittest.main()
