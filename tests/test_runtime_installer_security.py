"""Synthetic installer fixtures only; never uninstall host pip or import a DB."""
from contextlib import ExitStack
import importlib.metadata
import importlib.util
import io
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts' / filename)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


R = load('_runtime_installer_removal_fixture', 'remove_runtime_pip.py')
V = load('_runtime_installer_verifier_fixture', 'verify_psycopg2_runtime.py')


class RuntimeInstallerSecurity(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack(); self.addCleanup(self.stack.close)
        for name in ('subprocess.run', 'subprocess.Popen', 'os.system', 'socket.socket.connect',
                     'socket.create_connection', 'socket.getaddrinfo', 'urllib.request.urlopen'):
            self.stack.enter_context(patch(name, side_effect=AssertionError('no host uninstall/network')))
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix='.runtime-pip-fixture-', dir=ROOT)))
        self.stdlib = self.root / 'lib'; self.stdlib.mkdir(mode=0o700)
        self.ensure = self.stdlib / 'ensurepip'; self.ensure.mkdir(mode=0o700)
        self.bundle = self.ensure / '_bundled'; self.bundle.mkdir(mode=0o700)
        self.bin = self.root / 'bin'; self.bin.mkdir(mode=0o700)
        self.site = self.root / 'site-packages'; self.site.mkdir(mode=0o700)

    def wheels(self):
        for name in R.BUNDLED: (self.bundle / name).write_bytes(b'synthetic wheel bytes, not an executable archive')

    def final_context(self, *, distributions=(), module=None, alternate=None):
        self.stack.enter_context(patch.object(V.importlib.util, 'find_spec', return_value=module))
        self.stack.enter_context(patch.object(V.importlib.metadata, 'distributions', return_value=distributions))
        self.stack.enter_context(patch.object(V, 'RUNTIME_BIN_DIRS', (self.bin,)))
        paths = {'scripts': str(self.bin), 'stdlib': str(self.stdlib), 'purelib': str(self.site), 'platlib': str(self.site)}
        self.stack.enter_context(patch.object(V.sysconfig, 'get_path', side_effect=paths.__getitem__))
        self.stack.enter_context(patch.object(V.sysconfig, 'get_config_var', return_value=alternate))

    def test_only_two_exact_cpython_archives_are_removed_after_genuine_uninstall_command(self):
        self.wheels(); marker = self.stdlib / 'unrelated-stdlib.py'; marker.write_bytes(b'preserve')
        healthy = self.site / 'setuptools'; healthy.mkdir(); (healthy / 'marker').write_bytes(b'healthy installed code')
        calls = []
        def uninstall(argv, **kwargs):
            self.assertEqual(argv, [sys.executable, '-I', '-B', '-m', 'pip', '--disable-pip-version-check', 'uninstall', '--yes', 'pip'])
            self.assertEqual(kwargs['timeout'], 60); self.assertTrue(kwargs['check'])
            self.assertEqual(kwargs['env']['PIP_CONFIG_FILE'], os.devnull)
            self.assertNotIn('PYTHONPATH', kwargs['env']); calls.append(argv)
        with patch.object(R, 'image_layout', return_value=self.stdlib), patch.object(R.subprocess, 'run', side_effect=uninstall):
            result = R.strip_installer()
        self.assertEqual(len(calls), 1); self.assertEqual(result['bootstrap_wheels_removed'], 2)
        self.assertEqual(list(self.bundle.iterdir()), [])
        self.assertEqual(marker.read_bytes(), b'preserve')
        self.assertEqual((healthy / 'marker').read_bytes(), b'healthy installed code')

    def test_unknown_archive_scope_refuses_before_uninstall_or_any_deletion(self):
        self.wheels(); (self.bundle / 'unreviewed.whl').write_bytes(b'preserve')
        with patch.object(R, 'image_layout', return_value=self.stdlib), patch.object(R.subprocess, 'run') as run:
            with self.assertRaises(ValueError): R.strip_installer()
        run.assert_not_called(); self.assertEqual(len(list(self.bundle.iterdir())), 3)

    def test_bundle_links_hardlinks_fifo_and_writable_archives_refuse(self):
        name = next(iter(R.BUNDLED)); path = self.bundle / name
        outside = self.root / 'outside'; outside.write_bytes(b'preserve outside')
        for kind in ('symlink', 'hardlink', 'fifo', 'writable'):
            with self.subTest(kind=kind):
                if kind == 'symlink': path.symlink_to(outside)
                elif kind == 'hardlink': os.link(outside, path)
                elif kind == 'fifo': os.mkfifo(path, mode=0o600)
                else: path.write_bytes(b'preserve'); path.chmod(0o666)
                with self.assertRaises(ValueError): R.bundled_plan(self.stdlib)
                path.unlink()
        self.assertEqual(outside.read_bytes(), b'preserve outside')

    def test_redirected_parent_is_refused_even_if_bundle_is_missing(self):
        self.bundle.rmdir(); self.ensure.rmdir()
        elsewhere = self.root / 'elsewhere'; elsewhere.mkdir(mode=0o700)
        self.ensure.symlink_to(elsewhere, target_is_directory=True)
        with self.assertRaises(ValueError): R.bundled_plan(self.stdlib)

    def test_foreign_bundle_ownership_and_external_bootstrap_source_are_refused(self):
        self.wheels(); actual = os.geteuid()
        with patch.object(R.os, 'geteuid', return_value=actual + 1):
            with self.assertRaises(ValueError): R.bundled_plan(self.stdlib)
        with patch.object(R.sys, 'flags', types.SimpleNamespace(isolated=True)), \
             patch.object(R.sys, 'version_info', (3, 11, 17)), patch.object(R.sys, 'prefix', '/usr/local'), \
             patch.object(R.sys, 'base_prefix', '/usr/local'), patch.object(R.os, 'geteuid', return_value=0), \
             patch.object(R.sysconfig, 'get_path', return_value='/usr/local/lib/python3.11'), \
             patch.object(R.sysconfig, 'get_config_var', return_value='/alternate/wheels'):
            with self.assertRaises(ValueError): R.image_layout()

    def test_failed_uninstall_preserves_bootstrap_archives(self):
        self.wheels()
        with patch.object(R, 'image_layout', return_value=self.stdlib), \
             patch.object(R.subprocess, 'run', side_effect=R.subprocess.CalledProcessError(1, ['synthetic'])):
            with self.assertRaises(R.subprocess.CalledProcessError): R.strip_installer()
        self.assertEqual(len(list(self.bundle.iterdir())), 2)

    def test_bundle_drift_during_uninstall_refuses_without_deleting_changed_file(self):
        self.wheels(); path = self.bundle / next(iter(R.BUNDLED))
        def changed(*args, **kwargs): path.write_bytes(b'changed public archive')
        with patch.object(R, 'image_layout', return_value=self.stdlib), patch.object(R.subprocess, 'run', side_effect=changed):
            with self.assertRaises(ValueError): R.strip_installer()
        self.assertEqual(path.read_bytes(), b'changed public archive')

    def test_normal_cli_without_explicit_image_opt_in_cannot_mutate(self):
        with patch.object(sys, 'argv', ['remove_runtime_pip.py']), patch.object(R, 'strip_installer') as strip, \
             patch.object(sys, 'stderr', io.StringIO()):
            with self.assertRaises(SystemExit): R.main()
        strip.assert_not_called()
        with patch.object(R.sys, 'version_info', (3, 12, 0)):
            with self.assertRaises(ValueError): R.image_layout()

    def test_final_mode_accepts_absent_installer_and_keeps_healthy_packages(self):
        healthy = types.SimpleNamespace(metadata={'Name': 'setuptools'}, requires=['packaging>=26', 'pip; extra == "optional-build"'])
        self.final_context(distributions=[healthy]); V.final_installer_absent()

    def test_final_mode_refuses_module_metadata_active_dependency_entrypoint_stash_and_wheels(self):
        self.final_context()
        for fault in ('module', 'distribution', 'dependency', 'entrypoint', 'stash', 'wheel', 'alternate'):
            with self.subTest(fault=fault), ExitStack() as changes:
                if fault == 'module': changes.enter_context(patch.object(V.importlib.util, 'find_spec', return_value=object()))
                elif fault in ('distribution', 'dependency'):
                    item = types.SimpleNamespace(metadata={'Name': 'PIP' if fault == 'distribution' else 'healthy'},
                                                 requires=[] if fault == 'distribution' else ['pip>=1'])
                    changes.enter_context(patch.object(V.importlib.metadata, 'distributions', return_value=[item]))
                elif fault == 'entrypoint':
                    path = self.bin / 'pip3.11'; path.symlink_to(self.root / 'nonexistent'); changes.callback(path.unlink)
                elif fault == 'stash':
                    path = self.site / '~ip'; path.mkdir(); changes.callback(path.rmdir)
                elif fault == 'wheel':
                    path = self.bundle / 'pip-24.0-py3-none-any.whl'; path.write_bytes(b'old bootstrap'); changes.callback(path.unlink)
                else: changes.enter_context(patch.object(V.sysconfig, 'get_config_var', return_value='/alternate/wheels'))
                with self.assertRaises(ValueError): V.final_installer_absent()

    def test_default_tool_mode_still_requires_patched_pip_before_any_db_import(self):
        with patch.object(V.sys, 'version_info', (3, 11, 17)), patch.object(V.importlib.metadata, 'version', return_value='26.2.1'), \
             patch.object(V, 'final_installer_absent') as final:
            with self.assertRaises(ValueError): V.verify()
        final.assert_not_called()

    def test_final_mode_reruns_actual_abi_gate_without_reading_removed_pip_version(self):
        module = types.ModuleType('psycopg2'); module.__version__ = '2.9.13 (synthetic fixture)'
        module.__libpq_version__ = 150001
        module.extensions = types.SimpleNamespace(libpq_version=lambda: 150001)
        maps = '\n'.join('1-2 r--p 00000000 00:00 0 /usr/lib/x86_64-linux-gnu/' + name
                         for name in ('libpq.so.5.15', 'libssl.so.3', 'libcrypto.so.3'))
        with patch.dict(sys.modules, {'psycopg2': module}), patch.object(V.sys, 'version_info', (3, 11, 17)), \
             patch.object(V, 'final_installer_absent') as absent, \
             patch.object(V.importlib.metadata, 'version', side_effect=AssertionError('pip version must not be read')), \
             patch.object(V.importlib.metadata, 'distribution', return_value=types.SimpleNamespace(version='2.9.13', files=[])), \
             patch('builtins.open', side_effect=lambda *a, **kw: io.StringIO(maps)), \
             patch.object(Path, 'resolve', lambda path, **kwargs: path):
            result = V.verify(final_runtime=True)
            self.assertEqual(result['pip_policy'], 'absent')
            self.assertEqual(result['system_dynamic_libraries'], ['libcrypto', 'libpq', 'libssl'])
            self.assertEqual(result['libpq_compile_runtime'], 150001)
            absent.assert_called_once_with()
            module.__libpq_version__ = 150002
            with self.assertRaises(ValueError): V.verify(final_runtime=True)

    def test_final_cli_passes_explicit_mode_without_weakening_default(self):
        for flags, expected in (([], False), (['--final-runtime'], True)):
            with self.subTest(flags=flags), patch.object(sys, 'argv', ['verify.py', *flags]), \
                 patch.object(V, 'verify', return_value={'status': 'PASS'}) as verify, patch('builtins.print'):
                self.assertEqual(V.main(), 0)
                verify.assert_called_once_with(final_runtime=expected)

    def test_three_final_images_strip_after_installer_abi_checks_and_bind_helper_to_recipe(self):
        for name in ('Dockerfile', 'Dockerfile.iran', 'deploy/market-data/Dockerfile'):
            source = (ROOT / name).read_text(); builder, runtime = source.split('FROM patched-base AS runtime', 1)
            self.assertNotIn('remove_runtime_pip.py --image-build', builder)
            self.assertIn('verify_psycopg2_runtime.py', runtime)
            strip = runtime.index('remove_runtime_pip.py --image-build')
            self.assertLess(runtime.index('verify_psycopg2_runtime.py'), strip)
            self.assertLess(runtime.index('verify_sqlite_runtime.py'), strip)
            self.assertGreater(runtime.index('--final-runtime'), strip)
            self.assertNotIn('pip install', runtime[strip:])
            if name.endswith('market-data/Dockerfile'):
                self.assertLess(strip, runtime.index('USER 10001:10001'))
                self.assertIn('COPY scripts/remove_runtime_pip.py', source)
        controller = (ROOT / 'scripts/production_deploy_online.sh').read_text()
        for function in ('market_pipeline_image_input_signature', 'prepare_market_pipeline_control_payload'):
            body = controller.split(function + '() {', 1)[1].split('\n}', 1)[0]
            self.assertIn('scripts/remove_runtime_pip.py', body)
        for controller_name, function in (('deploy.sh', 'foreign_image_signature'), ('scripts/production_deploy_online.sh', 'build_image_bundle_signature')):
            body = (ROOT / controller_name).read_text().split(function + '() {', 1)[1].split('\n}', 1)[0]
            self.assertIn('scripts', body)
        self.assertIn("'scripts/remove_runtime_pip.py'", (ROOT / 'scripts/prepare_security_consumer_payload.py').read_text())


if __name__ == '__main__': unittest.main()
