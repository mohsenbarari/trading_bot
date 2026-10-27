"""Real synthetic SQLite semantics, hermetic metadata; no app/env/live stores."""
from contextlib import ExitStack, redirect_stdout
from io import StringIO
import importlib.util
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('_sqlite_security_fixture', ROOT / 'scripts/verify_sqlite_runtime.py')
G = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(G)
BASE = 'python:3.11.18-slim-trixie@sha256:0451b9eeed3e5a9523d8958cc33262eb5843c7af3114c50f45d99f56e2b883ac'


class SQLiteRuntimeSecurityTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack(); self.addCleanup(self.stack.close)
        for name in ('subprocess.run', 'subprocess.Popen', 'os.system', 'socket.socket.connect',
                     'socket.create_connection', 'socket.getaddrinfo', 'urllib.request.urlopen'):
            self.stack.enter_context(patch(name, side_effect=AssertionError('no host command or network')))
        self.temp = Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix='.sqlite-proof-test-', dir=ROOT)))

    def test_real_synthetic_wal_snapshot_backup_query_json_and_fts_contract(self):
        # This tests the proof, not the current host's CVE status or Trixie ABI.
        first, second = G.compatibility(), G.compatibility()
        self.assertEqual(first['query_result_sha256'], second['query_result_sha256'])
        for field in ('wal_snapshot', 'rollback_upsert', 'json_fts', 'backup_readonly_reopen', 'lookup_index'):
            self.assertIs(first[field], True)
        self.assertTrue(any('quote_lookup' in row for row in first['query_plan']))

    def test_readonly_reopen_really_refuses_a_writer(self):
        original = G.sqlite3.connect
        read_uris = []
        def tracked(path, *args, **kwargs):
            if kwargs.get('uri'):
                self.assertTrue(str(path).endswith('?mode=ro'))
                read_uris.append(path)
            return original(path, *args, **kwargs)
        with patch.object(G.sqlite3, 'connect', side_effect=tracked):
            G.compatibility()
        self.assertEqual(len(read_uris), 3)

    def test_vendor_floor_is_compared_by_dpkg_not_upstream_semver_alone(self):
        release = self.temp / 'os-release'
        release.write_text('ID=debian\nVERSION_ID="13"\nVERSION_CODENAME=trixie\n')
        release.chmod(0o600)
        # The synthetic file belongs to the runner, which need not be root.
        # Substitute only its owner metadata; real production files still
        # require uid0 and all other metadata remains real.
        actual_fstat = os.fstat
        def fixture_fstat(fd):
            info = actual_fstat(fd)
            expected = release.stat()
            self.assertEqual((info.st_dev, info.st_ino), (expected.st_dev, expected.st_ino))
            values = list(info); values[4] = 0
            return os.stat_result(values)
        self.stack.enter_context(patch.object(G.os, 'fstat', side_effect=fixture_fstat))
        real = Path.resolve
        def resolve(path, **kwargs):
            return release if str(path) == '/etc/os-release' else real(path, **kwargs)
        with patch.object(Path, 'resolve', resolve), patch.object(G.sqlite3, 'sqlite_version', '3.46.1'), \
             patch.object(G.sqlite3, 'sqlite_version_info', (3, 46, 1)), \
             patch.object(G, 'command', side_effect=['3.46.1-7+deb13u2+gold1', '', '']) as command:
            self.assertEqual(G.platform_package(), '3.46.1-7+deb13u2+gold1')
        self.assertEqual(command.call_args_list[1].args[0],
                         ['/usr/bin/dpkg', '--compare-versions', '3.46.1-7+deb13u2+gold1', 'ge', '3.46.1-7+deb13u2+gold1'])
        with patch.object(Path, 'resolve', resolve), patch.object(G, 'command',
                side_effect=['3.46.1-7+deb13u1', G.subprocess.CalledProcessError(1, 'dpkg')]), \
             self.assertRaises(G.subprocess.CalledProcessError):
            G.platform_package()
        with patch.object(Path, 'resolve', resolve), patch.object(G.sqlite3, 'sqlite_version', '3.40.1'), \
             patch.object(G, 'command', side_effect=['3.46.1-7+deb13u2+gold1', '', '']), \
             self.assertRaisesRegex(ValueError, 'sqlite_runtime_package_mismatch'):
            G.platform_package()
        with patch.object(Path, 'resolve', resolve), patch.object(G, 'command',
                side_effect=['3.46.1-7+deb13u3', '']), \
             self.assertRaisesRegex(ValueError, 'wal_reset_package_not_admitted'):
            G.platform_package()
        release.write_text('ID=debian\nVERSION_ID="12"\nVERSION_CODENAME=bookworm\n')
        with patch.object(Path, 'resolve', resolve), patch.object(G, 'command') as command, \
             self.assertRaisesRegex(ValueError, 'trixie_required'):
            G.platform_package()
        command.assert_not_called()

    def test_os_metadata_still_rejects_nonroot_owner_and_writable_files(self):
        for owner, mode in ((1001, 0o100600), (0, 0o100666)):
            with self.subTest(owner=owner, mode=mode), \
                 patch.object(G.os, 'open', return_value=43210), \
                 patch.object(G.os, 'close'), \
                 patch.object(G.os, 'fstat', return_value=SimpleNamespace(st_uid=owner, st_mode=mode, st_size=10)), \
                 patch.object(G.os, 'read') as read, patch.object(G, 'command') as command, \
                 self.assertRaisesRegex(ValueError, 'os_metadata'):
                G.platform_package()
            read.assert_not_called(); command.assert_not_called()

    def test_system_mapping_gate_rejects_vendored_deleted_missing_and_ambiguous_libraries(self):
        def maps(paths):
            return '\n'.join('1-2 r--p 00000000 00:00 0 ' + path for path in paths)
        system = '/usr/lib/x86_64-linux-gnu/libsqlite3.so.0.8.6'
        with patch.object(Path, 'resolve', lambda path, **kwargs: path):
            G.system_library(maps([system, system]), 'libsqlite3')
            for paths in ([], [system + ' (deleted)'], ['/opt/private.libs/libsqlite3.so.0'],
                          [system, '/usr/lib/other/libsqlite3.so.0']):
                with self.subTest(paths=paths), self.assertRaises(ValueError):
                    G.system_library(maps(paths), 'libsqlite3')
        with self.assertRaisesRegex(ValueError, 'mapping_bound'):
            G.system_library('x' * (2 * 1024 * 1024 + 1), 'libsqlite3')

    def test_unexpected_python_is_refused_before_package_or_scratch_access(self):
        with patch.object(G.sys, 'version_info', (3, 12, 0)), patch.object(G, 'platform_package') as package, \
             patch.object(G, 'compatibility') as compatibility, self.assertRaisesRegex(ValueError, 'python_version_changed'):
            G.verify()
        package.assert_not_called(); compatibility.assert_not_called()

    def test_app_gate_checks_magic_and_both_system_libraries_without_claiming_all_cves(self):
        fake_magic = SimpleNamespace(from_buffer=lambda body, **kwargs: 'text/plain')
        with patch.object(G.sys, 'version_info', (3, 11, 17)), \
             patch.object(G, 'platform_package', return_value='3.46.1-7+deb13u2+gold1'), \
             patch.object(G, 'loader_identity', return_value={'path':'PUBLIC_STDLIB_EXTENSION'}), \
             patch.object(G, 'installed_sqlite_identity', return_value={'path':'PUBLIC_SYSTEM_LIBRARY'}) as installed, \
             patch.dict('sys.modules', {'magic': fake_magic}), \
             patch('builtins.open', return_value=StringIO('bounded public mapping fixture')), \
             patch.object(G, 'system_library') as libraries:
            value = G.verify(require_libmagic=True)
        self.assertEqual([call.args[1] for call in libraries.call_args_list], ['libmagic'])
        installed.assert_called_once_with('bounded public mapping fixture')
        self.assertEqual(value['status'], 'PASS')
        self.assertFalse(value['production_data_accessed'])
        self.assertFalse(value['all_cves_attested'])

    def test_cli_error_is_bounded_content_free_and_cancels_its_deadline(self):
        output = StringIO()
        with patch.object(G.sys, 'argv', ['verify_sqlite_runtime.py']), patch.object(G.signal, 'signal'), \
             patch.object(G.signal, 'alarm') as alarm, \
             patch.object(G, 'verify', side_effect=ValueError('/private/never-print')), redirect_stdout(output):
            self.assertEqual(G.main(), 1)
        self.assertEqual(json.loads(output.getvalue()), {'status': 'FAIL', 'reason': 'sqlite_runtime_security_gate_failed'})
        self.assertEqual([call.args[0] for call in alarm.call_args_list], [20, 0])

    def test_all_three_recipes_use_one_verified_base_and_run_the_vendor_gate(self):
        for name in ('Dockerfile', 'Dockerfile.iran', 'deploy/market-data/Dockerfile'):
            with self.subTest(name=name):
                recipe = (ROOT / name).read_text()
                self.assertIn(BASE, recipe)
                self.assertNotIn('slim-bookworm', recipe)
                self.assertIn('verify_sqlite_runtime.py', recipe)
                self.assertIn('upgrade -y --no-install-recommends', recipe)
                self.assertIn('libpq-dev=$(dpkg-query', recipe)
                if name != 'deploy/market-data/Dockerfile':
                    self.assertIn('libmagic1t64', recipe)
                    self.assertIn('RUN python -I -B scripts/verify_sqlite_runtime.py --require-libmagic', recipe)
                else:
                    self.assertIn('RUN python -I -B /usr/local/libexec/verify_sqlite_runtime.py', recipe)

    def test_normal_release_keeps_cache_base_and_signed_market_control_payload_in_sync(self):
        source = (ROOT / 'scripts/production_deploy_online.sh').read_text()
        cache = source.split('prepare_pip_packages() {', 1)[1].split('\n}', 1)[0]
        signature = source.split('build_image_bundle_signature() {', 1)[1].split('\n}', 1)[0]
        self.assertIn(BASE, cache); self.assertIn(BASE, signature)
        for function in ('market_pipeline_image_input_signature', 'prepare_market_pipeline_control_payload'):
            body = source.split(function + '() {', 1)[1].split('\n}', 1)[0]
            self.assertIn('scripts/verify_sqlite_runtime.py', body)
        self.assertEqual(source.count(BASE), 2)


if __name__ == '__main__':
    unittest.main()
