"""Real test-module imports restore ambient APIs; no host/network call is made."""
from contextlib import ExitStack
import ctypes
import importlib
import importlib.util
from pathlib import Path
import resource
import signal
import socket
import subprocess
import unittest
import urllib.request
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]
COMMON = ('subprocess.Popen', 'subprocess.run', 'os.system', 'os.kill', 'os.killpg',
          'socket.socket', 'socket.create_connection', 'socket.getaddrinfo',
          'urllib.request.urlopen')
CASES = (
    ('test_compose_alpine_datastore_security.py', 'DatastoreTests',
     'scripts/compose_alpine_datastore_security.py',
     (*COMMON, 'urllib.request.OpenerDirector.open', 'ctypes.CDLL')),
    ('test_compose_market_security_os_candidate.py', 'MarketCompositionTests',
     'scripts/compose_market_security_os_candidate.py',
     (*COMMON, 'urllib.request.OpenerDirector.open', 'ctypes.CDLL', 'ctypes.PyDLL',
      'signal.signal', 'signal.alarm')),
    ('test_compose_market_security_profile.py', 'ProfileCompositionTests',
     'scripts/compose_market_security_profile.py',
     (*COMMON, 'urllib.request.OpenerDirector.open', 'ctypes.CDLL', 'ctypes.PyDLL',
      'signal.signal', 'signal.alarm', 'resource.setrlimit')),
    ('test_prepare_market_security_profile.py', 'ProfilePreparationTests',
     'scripts/prepare_market_security_profile.py',
     (*COMMON, 'urllib.request.OpenerDirector.open', 'ctypes.CDLL', 'ctypes.PyDLL',
      'signal.signal', 'signal.alarm', 'resource.setrlimit')),
    ('test_native_dashboard_tls.py', 'DashboardTests',
     'deploy/maintenance/native_dashboard_tls.py',
     (*COMMON, 'ctypes.CDLL', 'signal.signal', 'signal.setitimer')),
)
TARGETS = tuple(sorted({target for _, _, _, targets in CASES for target in targets}))
SPEC = importlib.util.spec_from_file_location


def reference(target):
    parts = target.split('.')
    value = importlib.import_module(parts[0])
    for part in parts[1:]:
        value = getattr(value, part)
    return value


def load(filename, suffix):
    spec = SPEC('_independent_test_isolation_' + suffix, ROOT / 'tests' / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class MaintenanceTestIsolationTests(unittest.TestCase):
    def snapshot(self):
        return {target: reference(target) for target in TARGETS}

    def assert_restored(self, before):
        for target, value in before.items():
            self.assertIs(reference(target), value, target)

    def assert_fenced(self, targets, before):
        for target in targets:
            value = reference(target)
            self.assertIsNot(value, before[target], target)
            self.assertIsInstance(value, Mock, target)
            # Only the verified mock is called. No real syscall or process API
            # is invoked, including when the independent outer runner is fenced.
            with self.assertRaisesRegex(AssertionError, 'ambient_host_network_forbidden'):
                value()

    def case(self, module, class_name):
        cls = getattr(module, class_name)
        method = unittest.defaultTestLoader.getTestCaseNames(cls)[0]
        return cls(method), method

    def test_each_module_import_restores_exact_api_objects_in_both_orders(self):
        before = self.snapshot()
        for order in (CASES, tuple(reversed(CASES))):
            for index, (filename, _, _, targets) in enumerate(order):
                with self.subTest(filename=filename, order=order is CASES):
                    module = load(filename, 'collection_' + str(index))
                    self.assertEqual(tuple(module.FENCE_TARGETS), targets)
                    self.assert_restored(before)

    def test_selected_case_denies_during_setup_and_restores_after_cleanup(self):
        for index, (filename, class_name, _, targets) in enumerate(CASES):
            with self.subTest(filename=filename):
                before = self.snapshot()
                module = load(filename, 'selected_' + str(index))
                case, _ = self.case(module, class_name)
                try:
                    case.setUp()
                    self.assert_fenced(targets, before)
                finally:
                    case.doCleanups()
                self.assert_restored(before)

    def test_exception_during_real_helper_import_restores_all_api_objects(self):
        for index, (filename, _, source, targets) in enumerate(CASES):
            with self.subTest(filename=filename):
                before = self.snapshot()

                def interrupted(name, path, *args, **kwargs):
                    if Path(path) == ROOT / source:
                        self.assert_fenced(targets, before)
                        raise RuntimeError('inert_helper_import_failure')
                    return SPEC(name, path, *args, **kwargs)

                with patch.object(importlib.util, 'spec_from_file_location', side_effect=interrupted):
                    with self.assertRaisesRegex(RuntimeError, 'inert_helper_import_failure'):
                        load(filename, 'failed_import_' + str(index))
                self.assert_restored(before)

    def test_outer_runner_guards_and_unrelated_named_sibling_are_preserved(self):
        before = self.snapshot()
        with ExitStack() as stack:
            for target in TARGETS:
                stack.enter_context(patch(target, return_value='inert_outer_sentinel'))
            outer = self.snapshot()
            for index, (filename, _, _, _) in enumerate(CASES):
                load(filename, 'outer_' + str(index))
                self.assert_restored(outer)
            # This named inert sentinel models an unrelated later test. It
            # cannot execute a command and must not inherit this suite's denial.
            self.assertEqual(subprocess.run('inert_sibling'), 'inert_outer_sentinel')
        self.assert_restored(before)

    def test_setup_failure_installs_guards_first_and_unittest_cleans_them(self):
        for index, (filename, class_name, _, targets) in enumerate(CASES):
            with self.subTest(filename=filename):
                before = self.snapshot()
                module = load(filename, 'setup_failure_' + str(index))
                case, method = self.case(module, class_name)

                def fail(*args, **kwargs):
                    self.assert_fenced(targets, before)
                    raise RuntimeError('inert_setup_failure')

                target = (module.M, 'document') if class_name == 'ProfilePreparationTests' else (module.tempfile, 'TemporaryDirectory')
                with patch.object(*target, side_effect=fail), patch.object(case, method) as body:
                    result = unittest.TestResult()
                    case.run(result)
                    body.assert_not_called()
                self.assertEqual(result.testsRun, 1)
                self.assertEqual(len(result.errors), 1)
                self.assertIn('inert_setup_failure', result.errors[0][1])
                self.assertFalse(result.failures or result.skipped)
                self.assert_restored(before)


if __name__ == '__main__':
    unittest.main()
