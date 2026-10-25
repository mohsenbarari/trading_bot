"""Real test-module imports restore ambient APIs; no host/network call is made."""
from contextlib import ExitStack
import ast
import builtins
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
from unittest.mock import Mock, patch, _patch


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
    ('test_util_linux_restricted_subdir_contract.py', 'ContractTests',
     'deploy/maintenance/util_linux_build/restricted_subdir_contract.py',
     ('subprocess.Popen', 'subprocess.run', 'subprocess.call', 'subprocess.check_call', 'subprocess.check_output',
      'socket.socket', 'socket.create_connection', 'socket.getaddrinfo', 'ctypes.CDLL',
      'os.system', 'os.popen', 'os.fork', 'os.posix_spawn', 'os.posix_spawnp', 'os.kill', 'os.killpg', 'resource.setrlimit')),
    ('test_backup_quiesced_primary.py', 'QuiescedPrimaryTests',
     'scripts/backup_market_pipeline_archive.py',
     ('subprocess.Popen', 'subprocess.run', 'subprocess.call', 'subprocess.check_call', 'subprocess.check_output',
      'socket.socket', 'socket.create_connection', 'socket.getaddrinfo', 'ctypes.CDLL',
      'os.system', 'os.popen', 'os.fork', 'os.posix_spawn', 'os.posix_spawnp')),
    ('test_verify_acl_consumer_presence.py', 'ElfTests',
     'scripts/verify_acl_consumer_presence.py',
     ('subprocess.Popen', 'subprocess.run', 'subprocess.call', 'subprocess.check_call', 'subprocess.check_output',
      'socket.socket', 'socket.create_connection', 'socket.getaddrinfo', 'ctypes.CDLL', 'ctypes.PyDLL',
      'os.system', 'os.popen', 'os.fork', 'os.posix_spawn', 'os.posix_spawnp',
      'os.execv', 'os.execve', 'os.execvp', 'os.execvpe', 'os.kill', 'os.killpg')),
    ('test_verify_acl_consumer_presence.py', 'InventoryTests',
     'scripts/verify_acl_consumer_presence.py',
     ('subprocess.Popen', 'subprocess.run', 'subprocess.call', 'subprocess.check_call', 'subprocess.check_output',
      'socket.socket', 'socket.create_connection', 'socket.getaddrinfo', 'ctypes.CDLL', 'ctypes.PyDLL',
      'os.system', 'os.popen', 'os.fork', 'os.posix_spawn', 'os.posix_spawnp',
      'os.execv', 'os.execve', 'os.execvp', 'os.execvpe', 'os.kill', 'os.killpg')),
    ('test_native_runtime_vulnerability_audit.py', 'NativeAuditTests',
     'deploy/maintenance/web_runtime_vulnerability_audit.py',
     ('subprocess.run', 'subprocess.Popen', 'subprocess.call', 'subprocess.check_call', 'subprocess.check_output',
      'socket.socket', 'socket.create_connection', 'socket.getaddrinfo', 'ctypes.CDLL',
      'os.system', 'os.popen', 'os.fork', 'os.posix_spawn', 'os.posix_spawnp', 'os.kill', 'os.killpg', 'resource.setrlimit')),
    ('test_web_engine_current_controller.py', 'CurrentControllerTests',
     'deploy/maintenance/web_engine_current_controller.py',
     ('subprocess.Popen', 'subprocess.run', 'subprocess.call', 'subprocess.check_call', 'subprocess.check_output',
      'socket.socket', 'socket.create_connection', 'socket.getaddrinfo', 'ctypes.CDLL',
      'os.system', 'os.popen', 'os.fork', 'os.posix_spawn', 'os.posix_spawnp', 'os.kill', 'os.killpg')),
    ('test_web_kernel_reboot.py', 'WebKernelRebootTests',
     'deploy/maintenance/web_kernel_reboot_guard.py',
     ('subprocess.Popen', 'subprocess.run', 'subprocess.call', 'subprocess.check_call', 'subprocess.check_output',
      'socket.socket', 'socket.create_connection', 'socket.getaddrinfo', 'ctypes.CDLL',
      'os.system', 'os.popen', 'os.fork', 'os.posix_spawn', 'os.kill', 'os.killpg',
      'signal.alarm', 'signal.pause', 'resource.setrlimit')),
    ('test_web_boot_guards.py', 'WebBootGuards',
     'deploy/maintenance/web_boot_guard.py',
     ('subprocess.Popen', 'subprocess.run', 'subprocess.check_output', 'os.system',
      'socket.socket.connect', 'socket.create_connection', 'socket.getaddrinfo', 'ctypes.CDLL', 'os.kill', 'os.killpg')),
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


def import_boundary_leaks(source):
    """Inspect executed import scope, following local helpers and import aliases."""
    tree = ast.parse(source)
    aliases = {}
    helpers = {node.name: node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
    for node in tree.body:
        if isinstance(node, ast.ImportFrom):
            aliases.update({item.asname or item.name: (node.module or '') + '.' + item.name for item in node.names})
        elif isinstance(node, ast.Import):
            aliases.update({item.asname or item.name: item.name for item in node.names})
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name): aliases[target.id] = node.value

    def name(node, seen=()):
        if isinstance(node, ast.Name):
            if node.id in seen: return node.id
            value = aliases.get(node.id, node.id)
            return value if isinstance(value, str) else name(value, (*seen, node.id))
        if isinstance(node, ast.Attribute): return name(node.value, seen) + '.' + node.attr
        if isinstance(node, ast.Call): return name(node.func, seen)
        return ''

    def cleanup(nodes):
        return {name(call.func.value) for item in nodes for call in ast.walk(item)
                if isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
                and call.func.attr in {'close', 'stop'}}

    finally_closed = set().union(*(cleanup(node.finalbody) for node in tree.body if isinstance(node, ast.Try)))
    leaks = []
    def visit(node, protected=False, active=()):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            # Definitions execute defaults and decorators, but not their body.
            for item in [*getattr(node, 'decorator_list', ()), *node.args.defaults,
                         *[value for value in node.args.kw_defaults if value is not None]]:
                visit(item, protected, active)
            return
        if isinstance(node, ast.With):
            owns = any(name(item.context_expr).endswith(('ExitStack', 'MonkeyPatch.context'))
                       or 'patch' in name(item.context_expr) for item in node.items)
            for item in node.items: visit(item.context_expr, protected or owns, active)
            for item in node.body: visit(item, protected or owns, active)
            return
        if isinstance(node, ast.Try):
            for item in [*node.body, *node.handlers, *node.orelse]: visit(item, protected or bool(cleanup(node.finalbody)), active)
            for item in node.finalbody: visit(item, protected, active)
            return
        if isinstance(node, ast.Call):
            target = name(node.func)
            constructor = target.endswith(('ExitStack', 'MonkeyPatch'))
            entry = target.endswith('.enter_context')
            start = target.endswith('.start')
            monkey = 'MonkeyPatch.' in target and target.rsplit('.', 1)[-1] in {'setattr', 'setitem', 'delattr', 'delitem', 'setenv', 'delenv', 'syspath_prepend', 'chdir'}
            if not protected and (entry or start or monkey or (constructor and target not in finally_closed)):
                leaks.append((node.lineno, target))
            if isinstance(node.func, ast.Name) and node.func.id in helpers and node.func.id not in active:
                for item in helpers[node.func.id].body: visit(item, protected, (*active, node.func.id))
        for child in ast.iter_child_nodes(node): visit(child, protected, active)
    visit(tree)
    return leaks


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
            with self.assertRaisesRegex(AssertionError, 'ambient_|unmocked_host_command|unmocked_network|no native/network calls'):
                value()

    def case(self, module, class_name):
        cls = getattr(module, class_name)
        cls.setUpClass()
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
                started = tuple(_patch._active_patches)
                module = load(filename, 'selected_' + str(index))
                case, _ = self.case(module, class_name)
                setup_complete = False
                try:
                    case.setUp()
                    setup_complete = True
                    self.assert_fenced(targets, before)
                finally:
                    if setup_complete:case.tearDown()
                    case.doCleanups()
                self.assert_restored(before)
                self.assertEqual(tuple(_patch._active_patches), started)

    def test_exception_during_real_helper_import_restores_all_api_objects(self):
        for index, (filename, _, source, targets) in enumerate(CASES):
            with self.subTest(filename=filename):
                before = self.snapshot()

                def interrupted(name, path, *args, **kwargs):
                    if Path(path) == ROOT / source:
                        self.assert_fenced(targets, before)
                        raise RuntimeError('inert_helper_import_failure')
                    return SPEC(name, path, *args, **kwargs)

                original_import = builtins.__import__
                def interrupted_import(name, globals=None, locals=None, fromlist=(), level=0):
                    if filename == 'test_backup_quiesced_primary.py' and name == 'scripts' and 'backup_market_pipeline_archive' in fromlist:
                        self.assert_fenced(targets, before)
                        raise RuntimeError('inert_helper_import_failure')
                    return original_import(name, globals, locals, fromlist, level)

                with patch.object(importlib.util, 'spec_from_file_location', side_effect=interrupted), patch.object(builtins, '__import__', side_effect=interrupted_import):
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

                if class_name == 'ElfTests':
                    original = module.fenced
                    def fence_then_fail(stack):
                        original(stack)
                        fail()
                    target = (module, 'fenced');failure = fence_then_fail
                else:
                    fixture_module = module.fixtures if class_name == 'QuiescedPrimaryTests' else module
                    if class_name == 'CurrentControllerTests':target = (module.fixtures.CurrentProfileTests, 'fixture')
                    elif class_name == 'ProfilePreparationTests':target = (module.M, 'document')
                    else:target = (fixture_module.tempfile, 'TemporaryDirectory')
                    failure = fail
                with patch.object(*target, side_effect=failure), patch.object(case, method) as body:
                    result = unittest.TestResult()
                    case.run(result)
                    body.assert_not_called()
                self.assertEqual(result.testsRun, 1)
                self.assertEqual(len(result.errors), 1)
                self.assertIn('inert_setup_failure', result.errors[0][1])
                self.assertFalse(result.failures or result.skipped)
                self.assert_restored(before)

    def test_all_test_modules_have_no_unscoped_import_boundary_patch(self):
        leaks = {}
        for path in sorted((ROOT / 'tests').rglob('*.py')):
            found = import_boundary_leaks(path.read_bytes())
            if found: leaks[str(path.relative_to(ROOT))] = found
        self.assertEqual(leaks, {})

    def test_semantic_audit_follows_aliases_helpers_and_nonstandard_stack_names(self):
        leaking = (
            'from contextlib import ExitStack as E\nOTHER=E()\nOTHER.enter_context(value)\n',
            'from unittest.mock import patch as p\nowned=p("socket.socket")\nowned.start()\n',
            'import pytest as p\nm=p.MonkeyPatch()\nm.setattr("module.name", 1)\n',
            'from unittest.mock import patch\ndef install():\n patch("socket.socket").start()\ninstall()\n',
            'from unittest.mock import patch\ndef factory():\n return patch("socket.socket")\nother=factory()\nother.start()\n',
            'from unittest.mock import patch\nowned=patch("socket.socket")\n@owned.start()\ndef later(): pass\n',
        )
        for source in leaking:
            with self.subTest(source=source):self.assertTrue(import_boundary_leaks(source))
        scoped = (
            'from contextlib import ExitStack as E\nwith E() as own:\n own.enter_context(value)\n',
            'from contextlib import ExitStack\nS=ExitStack()\ntry:\n S.enter_context(value)\nfinally:\n S.close()\n',
            'from unittest.mock import patch as p\n@p("socket.socket")\ndef later(): pass\n',
        )
        for source in scoped:
            with self.subTest(source=source):self.assertEqual(import_boundary_leaks(source), [])


if __name__ == '__main__':
    unittest.main()
