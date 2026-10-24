"""Hermetic source contracts; never compile/install/build or contact a network."""
import ast
from contextlib import ExitStack
from hashlib import sha256
import importlib.util
import io
import os
from pathlib import Path
import re
import tarfile
import tempfile
import textwrap
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
RECIPE = ROOT / 'deploy/maintenance/util_linux_build'
WORKFLOW = ROOT / '.github/workflows/trixie-util-linux-build.yml'


def load(name, file):
    spec = importlib.util.spec_from_file_location(name, RECIPE / file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


B = load('trixie_build_tests', 'build.py')
V = load('trixie_abi_tests', 'verify.py')
S = load('trixie_source_tests', 'source_regressions.py')


def blocks():
    text = WORKFLOW.read_text()
    return [textwrap.dedent(value) for value in re.findall(
        r"(?ms)^          python3 -I -B - <<'PY'\n(.*?)^          PY$", text)]


class TrixieUtilLinuxBuildTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack(); self.addCleanup(self.stack.close)
        for name in ('subprocess.Popen', 'subprocess.run', 'os.system', 'socket.socket',
                     'socket.create_connection', 'socket.getaddrinfo', 'urllib.request.urlopen', 'ctypes.CDLL'):
            self.stack.enter_context(patch(name, side_effect=AssertionError('no_native_build_install_network')))
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix='.util-linux-source-', dir=ROOT)))

    def test_exact_pinned_inputs_and_honest_local_versions(self):
        self.assertEqual(len(B.INPUTS), 7)
        for name, (url, size, digest) in B.INPUTS.items():
            self.assertRegex(digest, r'^[0-9a-f]{64}$')
            self.assertGreater(size, 0)
            self.assertLessEqual(size, 12 * 1024**2)
            self.assertTrue(url.startswith(('https://deb.debian.org/', 'https://www.kernel.org/', 'https://github.com/util-linux/util-linux/commit/')))
        self.assertEqual(B.expected_version('bsdutils'), '1:2.41.6-0+deb13u0+gold1')
        self.assertEqual(B.expected_version('login'), '1:4.16.0-2+really2.41.6-0+deb13u0+gold1')
        self.assertEqual(B.expected_version('mount'), B.VERSION)
        self.assertNotIn('2.42', B.VERSION)

    def test_runtime_nine_are_a_subset_of_complete_twenty_five_family_build(self):
        self.assertEqual(B.SELECTED, {'bsdutils', 'libblkid1', 'liblastlog2-2', 'libmount1',
                                     'libsmartcols1', 'libuuid1', 'login', 'mount', 'util-linux'})
        self.assertEqual(len(B.FULL_FAMILY), 25)
        self.assertLess(B.SELECTED, B.FULL_FAMILY)
        self.assertLessEqual(B.BASELINE, B.FULL_FAMILY)

    def test_failure_diagnostic_emits_only_closed_codes_not_arbitrary_exception_text(self):
        self.assertEqual(B.refusal_code(RuntimeError('input_metadata')), 'input_metadata')
        for value in ('/private/path/secret', 'unexpected raw data', {'private': 'value'}):
            self.assertEqual(B.refusal_code(RuntimeError(value)), 'RuntimeError')
        for value in B.ERROR_CODES:
            self.assertRegex(value, r'^[a-z0-9_]{1,64}$')

    def test_toolchain_is_trixie_authenticated_snapshot_and_nonroot(self):
        source = (RECIPE / 'Dockerfile').read_text()
        self.assertIn('python:3.11.17-slim-trixie@sha256:b95327ce1439e687237841b0210677aeadd62d55a060a5f6bcb9c15bf6251dd3', source)
        self.assertIn('20261024T000000Z', source)
        self.assertIn('Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg', source)
        self.assertIn('--build-profiles=noudeb util-linux=2.41.5-0+deb13u1', source)
        self.assertIn('USER 1001:1001', source)
        self.assertIn('COPY --chmod=0644 Dockerfile build.py verify.py source_regressions.py /recipe/', source)
        for word in ('sid', 'trusted=yes', 'allow-unauthenticated', 'nocheck', 'stage1', 'noverity'):
            self.assertNotIn(word, source)

    def test_build_environment_retains_all_tests_and_normal_feature_profiles(self):
        value = B.environment()
        self.assertEqual(value['DEB_BUILD_PROFILES'], 'noudeb')
        self.assertEqual(value['DEB_BUILD_OPTIONS'], 'parallel=2')
        self.assertEqual(value['QUILT_PATCH_OPTS'], '--fuzz=0')
        self.assertNotIn('GITLAB_CI', value)

    def test_safe_read_refuses_link_write_fifo_and_oversize_before_read(self):
        path = self.root / 'input'; path.write_bytes(b'public-proof')
        self.assertEqual(B.read(path), b'public-proof')
        with patch.object(B.os, 'read', side_effect=AssertionError('early_bound_missing')):
            with self.assertRaisesRegex(RuntimeError, 'input_metadata'):
                B.read(path, 1)
        path.chmod(0o666)
        with self.assertRaises(RuntimeError): B.read(path)
        path.chmod(0o600); alias = self.root / 'hardlink'; os.link(path, alias)
        with self.assertRaises(RuntimeError): B.read(path)
        alias.unlink(); alias.symlink_to(path)
        with self.assertRaises(OSError): B.read(alias)
        fifo = self.root / 'fifo'; os.mkfifo(fifo)
        with self.assertRaises(RuntimeError): B.read(fifo)

    def archive(self, name, *, kind=None, link=None):
        output = io.BytesIO()
        with tarfile.open(fileobj=output, mode='w:xz') as archive:
            info = tarfile.TarInfo(name); info.mode = 0o644
            if kind:
                info.type = kind; info.linkname = link or ''
                archive.addfile(info)
            else:
                info.size = 6; archive.addfile(info, io.BytesIO(b'public'))
        return output.getvalue()

    def test_real_tar_extraction_is_bounded_and_stays_inside_single_root(self):
        folder = B.unpack(self.archive('candidate/file'), self.root, 'candidate')
        self.assertEqual((folder / 'file').read_bytes(), b'public')
        for name in ('../escape', '/candidate/file', 'candidate/../escape', 'other/file'):
            with self.subTest(name=name), self.assertRaises(RuntimeError):
                B.unpack(self.archive(name), self.root, 'candidate')

    def test_public_tar_links_and_special_nodes_cannot_escape(self):
        for kind, target in ((tarfile.LNKTYPE, 'candidate/file'), (tarfile.SYMTYPE, '../../escape'),
                             (tarfile.SYMTYPE, '../other-source-tree'),
                             (tarfile.FIFOTYPE, None), (tarfile.CHRTYPE, None)):
            with self.subTest(kind=kind), self.assertRaises((RuntimeError, tarfile.FilterError)):
                B.unpack(self.archive('candidate/node', kind=kind, link=target), self.root, 'candidate')

    def test_upstream_root_skip_and_missing_test_count_do_not_pass(self):
        log = self.root / 'build.log'
        for text in ('Ignore util-linux test suite [non-root UID expected].\n', 'compiler completed\n', 'All 0 tests PASSED\n'):
            log.write_text(text)
            with self.assertRaises(RuntimeError): B.suite_evidence(log)
        log.write_text('some genuine suite output\nAll 638 tests PASSED\n')
        self.assertEqual(B.suite_evidence(log)['reported_suite_script_counts'], [638])

    def test_command_stream_bound_refuses_block_before_persisting_without_any_process(self):
        stdout = types.SimpleNamespace(fileno=lambda: 42, close=lambda: None)
        child = types.SimpleNamespace(stdout=stdout, poll=lambda: 0, wait=lambda **_: 0)
        key = types.SimpleNamespace(fileobj=stdout)
        selector = types.SimpleNamespace(register=lambda *_: None, get_map=lambda: {42: key},
                                         select=lambda _: [(key, None)], close=lambda: None)
        log = self.root / 'bounded.log'
        with patch.object(B.subprocess, 'Popen', return_value=child), \
             patch.object(B.selectors, 'DefaultSelector', return_value=selector), \
             patch.object(B.os, 'read', return_value=b'x' * 9), patch.object(B, 'LOG_LIMIT', 8):
            with self.assertRaisesRegex(RuntimeError, 'command_log_bound'):
                B.command(['/never-executed'], self.root, log)
        self.assertEqual(log.read_bytes(), b'')

    def test_only_two_integrated_series_entries_are_dropped(self):
        self.assertEqual(len(B.ORIGINAL_SERIES), 11)
        self.assertTrue(all(n.startswith('upstream/') for n in B.ORIGINAL_SERIES[-2:]))
        source = (RECIPE / 'build.py').read_text()
        self.assertIn('(*ORIGINAL_SERIES[:-2], *FOLLOWUPS)', source)
        self.assertEqual(set(B.FOLLOWUPS), {'79c2881c.patch', '485dbb67.patch', '233cf732.patch'})
        self.assertIn("'packaging_series_changed'", source)

    def test_abi_export_inventory_preserves_versions_but_ignores_undefined_imports(self):
        text = '\n'.join(('1: 000001 2 FUNC GLOBAL DEFAULT 12 old_api@@VERS_1',
                          '2: 000002 2 FUNC WEAK DEFAULT 12 weak_api@VERS_1',
                          '3: 000000 0 FUNC GLOBAL DEFAULT UND imported@GLIBC_2.2.5'))
        self.assertEqual(V.exports(text), {'old_api@VERS_1', 'weak_api@VERS_1'})
        self.assertEqual(V.soname(' 0x000 (SONAME) Library soname: [libmount.so.1]'), 'libmount.so.1')
        with self.assertRaises(RuntimeError): V.exports('no defined exports')
        with self.assertRaises(RuntimeError): V.soname('missing')

    def test_library_resolution_rejects_outside_symlink(self):
        folder = self.root / 'usr/lib/x86_64-linux-gnu'; folder.mkdir(parents=True)
        outside = self.root / 'outside'; outside.write_bytes(b'public')
        (folder / 'libmount.so.1').symlink_to(outside)
        with self.assertRaisesRegex(RuntimeError, 'library_escape'):
            V.library(self.root, 'mount')

    def test_old_callers_use_all_six_old_headers_and_retain_binary_hash_checks(self):
        self.assertEqual(set(V.CALLERS), {'blkid', 'mount', 'uuid', 'smartcols', 'fdisk', 'lastlog2'})
        source = (RECIPE / 'verify.py').read_text()
        self.assertIn("'-I' + str(old / 'usr/include')", source)
        self.assertIn('LD_LIBRARY_PATH', source)
        self.assertIn("'old_caller_changed'", source)
        self.assertIn('dlsym(RTLD_DEFAULT', source)
        self.assertIn("'pam_configuration_changed'", source)

    def test_c_function_extraction_preserves_actual_body_and_balanced_comments_strings(self):
        text = 'int selected(int x)\n{ const char *s="}"; /* { } */ if(x){x++;}\n return x;\n}\nint next(void){return 0;}\n'
        actual = S.function(text, 'selected')
        self.assertIn('return x;', actual)
        self.assertNotIn('int next', actual)
        for invalid in (text + text, 'int selected(void){', 'int unrelated(void){return 0;}'):
            with self.assertRaises(RuntimeError): S.function(invalid, 'selected')

    def test_main_join_extraction_keeps_real_closes_and_checks_before_target_credential_drop(self):
        old = 'if(cgroup_procs_fd>=0)\n join_into_cgroup();'
        new = 'if(cgroup_procs_fd>=0){\n join_into_cgroup();\n close(cgroup_procs_fd);\n cgroup_procs_fd=-1;\n}'
        suffix = '\nif(uid_gid_fd>=0){}\nsetgid(gid);\nsetuid(uid);\n}'
        for block in (old, new):
            actual = S.main_join_block('int main(void){\n' + block + suffix)
            self.assertEqual(actual.strip(), block)
        self.assertNotIn('close(', S.main_join_block('int main(void){\n' + old + suffix))
        with self.assertRaisesRegex(RuntimeError, 'main_credential_drop_order'):
            S.main_join_block('int main(void){\nsetgid(gid);\n' + new + '\nif(uid_gid_fd>=0){}\nsetuid(uid);\n}')

    def test_disabled_variant_runs_normal_distclean_after_primary_evidence(self):
        source = (RECIPE / 'build.py').read_text()
        abi = source.index("recipe / 'verify.py'")
        primary = source.index("artifacts / 'primary-config.h'")
        clean = source.index("['/usr/bin/make', 'distclean']")
        configure = source.index("'--disable-libmount-mountfd-support'")
        self.assertLess(abi, primary); self.assertLess(primary, clean); self.assertLess(clean, configure)
        self.assertIn("'primary_distclean_incomplete'", source)

    def test_disabled_mountfd_variant_builds_required_libblkid_and_libmount(self):
        source=ast.parse((RECIPE/'build.py').read_text())
        commands=[n.args[0] for n in ast.walk(source) if isinstance(n,ast.Call) and isinstance(n.func,ast.Name)
                  and n.func.id=='command' and n.args and isinstance(n.args[0],ast.List)]
        variant=next(n for n in commands if any(isinstance(v,ast.Constant) and v.value=='--disable-libmount-mountfd-support' for v in n.elts))
        options={v.value for v in variant.elts if isinstance(v,ast.Constant)}
        self.assertLessEqual({'--disable-all-programs','--enable-libblkid','--enable-libmount'},options)
        good='#define HAVE_LIBBLKID 1\n#define HAVE_LIBMOUNT 1\n/* #undef USE_LIBMOUNT_MOUNTFD_SUPPORT */\n'
        B.disabled_configuration(good)
        for invalid in (good.replace('#define HAVE_LIBBLKID 1\n',''),good.replace('#define HAVE_LIBMOUNT 1\n',''),
                        good+'#define USE_LIBMOUNT_MOUNTFD_SUPPORT 1\n',good+'#define USE_LIBMOUNT_MOUNTFD_SUPPORT 0\n'):
            with self.assertRaises(RuntimeError):B.disabled_configuration(invalid)

    def test_negative_control_that_unexpectedly_passes_is_a_failure(self):
        with patch.object(S.B, 'command'), patch.object(S.subprocess, 'run', return_value=types.SimpleNamespace(returncode=0)):
            with self.assertRaisesRegex(RuntimeError, 'compiled_negative_control_failed'):
                S.compile_run(self.root, 'old-probe', 'int main(void){return 0;}', [], False)

    def test_kernel_coverage_is_explicitly_not_tested_and_never_accepted(self):
        source = (RECIPE / 'source_regressions.py').read_text()
        for name in ('privileged_failed_helper_ownership', 'privileged_cgroup_namespace_credential_drop',
                     'restricted_symlink_procfs_mount_races'):
            self.assertIn("'" + name + "': 'NOT_TESTED'", source)
        self.assertIn("'security_accepted': False", source)
        self.assertIn("'CVE-2026-78409_vendor_binary_reconciliation': 'OPEN'", source)

    def test_every_workflow_python_program_and_nested_installer_parses(self):
        values = blocks()
        self.assertEqual(len(values), 5)
        for value in values:
            tree = ast.parse(value)
            for node in tree.body:
                if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'install' for t in node.targets):
                    ast.parse(ast.literal_eval(node.value))

    def test_workflow_compilation_is_offline_nonroot_and_not_privileged(self):
        text = WORKFLOW.read_text()
        self.assertIn('workflow_dispatch:', text)
        self.assertNotIn('pull_request:', text)
        self.assertNotIn('secrets.', text)
        self.assertNotIn('--privileged', text)
        self.assertNotIn('SYS_ADMIN', text.replace('SYS_ADMIN, networking', ''))
        self.assertIn("'--user=1001:1001','--network=none','--read-only'", text)
        self.assertIn("'--cap-drop=ALL'", text)
        self.assertIn("'--cpus=2','--memory=4g'", text)
        self.assertIn("'--pids-limit=512'", text)
        self.assertIn('timeout-minutes: 150', text)
        self.assertIn("'--no-optional-locks','cat-file','-s'", text)

    def test_raw_scans_keep_all_severities_unfixed_rows_and_separate_package_export(self):
        text = WORKFLOW.read_text()
        self.assertIn('severity: UNKNOWN,LOW,MEDIUM,HIGH,CRITICAL', text)
        self.assertIn("ignore-unfixed: 'false'", text)
        self.assertIn('TRIVY_IGNOREFILE: /dev/null', text)
        self.assertIn('32*1024**2', text)
        self.assertIn('8*1024**2', text)
        self.assertIn("steps.export.outcome == 'success'", text)
        self.assertIn("steps.reports.outcome == 'success'", text)
        self.assertEqual(text.count('retention-days: 7'), 2)

    def test_candidate_entrypoint_cannot_be_used_as_a_host_build(self):
        with self.assertRaisesRegex(RuntimeError, 'immutable_builder_required'):
            B.run('mutable-tag')
        with patch.object(B.os, 'geteuid', return_value=0), self.assertRaisesRegex(RuntimeError, 'nonroot_container_required'):
            B.run('sha256:' + 'a' * 64)

    def test_input_preparation_refuses_host_checkout_without_network(self):
        with patch.object(B.os, 'geteuid', return_value=0), self.assertRaisesRegex(RuntimeError, 'fetch_builder_only'):
            B.fetch_inputs()
        source = (RECIPE / 'build.py').read_text().split('def fetch_inputs():',1)[1].split('def unpack(',1)[0]
        self.assertNotIn("Path('/.dockerenv').is_file()", source)
        self.assertIn("Path('/recipe/build.py')", source)

    def test_report_admission_refuses_oversize_before_body_and_hardlink(self):
        block = next(value for value in blocks() if 'rows=[r for r in receipt' in value and "root/'reports'/name" in value)
        tree = ast.parse(block)
        function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'read')
        namespace = {'os': os, 'stat': __import__('stat'), 'json': __import__('json')}
        exec(compile(ast.Module(body=[function], type_ignores=[]), '<reviewed-report-read>', 'exec'), namespace)
        path = self.root / 'report'; path.write_bytes(b'{}')
        self.assertEqual(namespace['read'](path, 2), b'{}')
        with patch.object(os, 'read', side_effect=AssertionError('late_bound')):
            with self.assertRaises(AssertionError) as error: namespace['read'](path, 1)
        self.assertNotEqual(str(error.exception), 'late_bound')
        os.link(path, self.root / 'alias')
        with self.assertRaises(AssertionError): namespace['read'](path, 2)

    def test_actual_raw_scanner_identity_and_schema_are_bound_to_the_candidate(self):
        block = next(value for value in blocks() if 'def verify_report_identity' in value)
        tree = ast.parse(block)
        function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'verify_report_identity')
        namespace = {}
        exec(compile(ast.Module(body=[function], type_ignores=[]), '<reviewed-report-identity>', 'exec'), namespace)
        value = {'SchemaVersion': 2, 'ArtifactType': 'container_image', 'Metadata': {'ImageID': 'sha256:' + 'a'*64}, 'Results': [{'Class': 'os-pkgs'}]}
        namespace['verify_report_identity'](value, 'sha256:' + 'a'*64)
        for bad in ({}, {**value, 'SchemaVersion': 1}, {**value, 'Results': []}, {**value, 'Metadata': {'ImageID': 'sha256:'+'b'*64}}):
            with self.assertRaises(AssertionError): namespace['verify_report_identity'](bad, 'sha256:'+'a'*64)


if __name__ == '__main__':
    unittest.main()
