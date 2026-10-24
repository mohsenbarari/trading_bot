"""Build-only source contracts. Never invoke Docker, compiler, network or host IO."""
import ast
from contextlib import ExitStack
from hashlib import sha256
import importlib.util
import os
from pathlib import Path
import tempfile
from io import StringIO
import unittest
from unittest.mock import patch

import yaml

ROOT = Path(__file__).resolve().parents[1]
RECIPE = ROOT / 'deploy/maintenance/brotli_build'
PLAY = yaml.safe_load((ROOT / 'deploy/maintenance/brotli_native_build.yml').read_text())[0]


def module(name, filename):
    spec = importlib.util.spec_from_file_location(name, RECIPE / filename)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


BUILD = module('brotli_build_fixture', 'build.py')
VERIFY = module('brotli_verify_fixture', 'verify.py')


class NativeBrotliBuildTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack(); self.addCleanup(self.stack.close)
        for target in ('subprocess.run', 'subprocess.Popen', 'socket.socket.connect',
                       'socket.create_connection', 'socket.getaddrinfo', 'urllib.request.urlopen',
                       'urllib.request.OpenerDirector.open', 'ctypes.CDLL'):
            self.stack.enter_context(patch(target, side_effect=AssertionError('synthetic only: no build/native/network')))
        self.temp = Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix='.brotli-contract-', dir=ROOT)))

    def test_official_exact_noble_amd64_pin_and_apt_authentication_are_retained(self):
        source = (RECIPE / 'Dockerfile').read_text()
        self.assertIn('ubuntu:24.04@sha256:a2d43bffc46670d7108bb3c0468c67c5557c4c92e83e1e34cbff1322ad1b94dc', source)
        self.assertIn('Types: deb deb-src', source)
        self.assertIn('apt-get source --download-only brotli=1.1.0-2build2', source)
        for dependency in ('cmake', 'debhelper', 'dh-python', 'libpython3-all-dev', 'python3-all-dev', 'python3-setuptools'):
            self.assertIn(dependency, source)
        for forbidden in ('allow-unauthenticated', 'trusted=yes', 'AllowInsecure', 'nocheck', '--privileged'):
            self.assertNotIn(forbidden, source)
        self.assertIn('timeout --signal=KILL 600', source)

    def test_real_official_patch_is_pinned_and_changes_only_two_expected_paths(self):
        raw = (RECIPE / 'CVE-2026-111497.patch').read_bytes()
        self.assertEqual(len(raw), 9821)
        self.assertEqual(sha256(raw).hexdigest(), BUILD.PATCH_SHA)
        self.assertTrue(raw.startswith(b'From da9806362874b9bd4b552a3af360b5d6347beb12 '))
        self.assertEqual(set(BUILD.BEFORE), {'c/dec/decode.c', 'c/include/brotli/decode.h'})
        self.assertEqual(set(BUILD.AFTER), set(BUILD.BEFORE))
        self.assertIn(b'BROTLI_SAFE_WITH_STATUS', raw)
        self.assertIn(b'BLOCK_SWITCH, -17', raw)

    def test_input_admission_checks_all_three_sha_and_patch_before_any_build(self):
        inputs, recipe = self.temp / 'inputs', self.temp / 'recipe'
        inputs.mkdir(); recipe.mkdir()
        fixtures = {}
        for index, name in enumerate(BUILD.SOURCE):
            raw = ('public fixture ' + str(index)).encode()
            (inputs / name).write_bytes(raw)
            fixtures[name] = (len(raw), sha256(raw).hexdigest())
        patch_raw = (RECIPE / 'CVE-2026-111497.patch').read_bytes()
        (recipe / 'CVE-2026-111497.patch').write_bytes(patch_raw)
        with patch.object(BUILD, 'SOURCE', fixtures):
            BUILD.pinned_inputs(inputs, recipe)
            first = inputs / next(iter(fixtures)); first.write_bytes(b'changed')
            with self.assertRaisesRegex(RuntimeError, 'source_digest'):
                BUILD.pinned_inputs(inputs, recipe)
        with self.assertRaisesRegex(RuntimeError, 'source_digest'):
            BUILD.pinned_inputs(inputs, recipe)

    def test_safe_read_refuses_link_writable_fifo_oversize_and_detects_digest_drift(self):
        path = self.temp / 'input'; path.write_bytes(b'public')
        self.assertEqual(BUILD.read(path), b'public')
        with self.assertRaises(RuntimeError): BUILD.read(path, maximum=1)
        path.chmod(0o666)
        with self.assertRaises(RuntimeError): BUILD.read(path)
        path.chmod(0o600); linked = self.temp / 'hard'; os.link(path, linked)
        with self.assertRaises(RuntimeError): BUILD.read(path)
        linked.unlink(); symbolic = self.temp / 'symbolic'; symbolic.symlink_to(path)
        with self.assertRaises(OSError): BUILD.read(symbolic)
        fifo = self.temp / 'fifo'; os.mkfifo(fifo)
        with self.assertRaises(RuntimeError): BUILD.read(fifo)

    def test_patch_and_packaging_guards_preserve_distro_rules_and_run_real_tests(self):
        source = (RECIPE / 'build.py').read_text()
        self.assertIn("original[name] == digest, 'patch_preimage'", source)
        self.assertIn("changed[name] == digest, 'patch_postimage'", source)
        self.assertIn("set(AFTER), 'unexpected_source_change'", source)
        self.assertIn("'packaging_drift'", source)
        self.assertIn("'--dry-run', '--batch', '--forward', '--fuzz=0'", source)
        self.assertIn("'/usr/bin/dpkg-buildpackage', '-us', '-uc', '-b'", source)
        self.assertNotIn('nocheck', source)
        self.assertNotIn('dpkg -i', source); self.assertNotIn('apt-get install', source)
        self.assertEqual(BUILD.PACKAGES, {'brotli', 'libbrotli1', 'libbrotli-dev', 'python3-brotli'})
        self.assertIn("'DEB_BUILD_OPTIONS': 'parallel=2'", source)
        self.assertIn("signal.alarm(1500)", source)
        self.assertIn("'host_installed': False", source)

    def test_abi_negatives_reject_removed_export_soname_or_dependency_change(self):
        before = {'soname': ['libbrotlidec.so.1'], 'exports': ['a', 'b'], 'needed': ['libc.so.6']}
        VERIFY.compare_abi(before, dict(before), soname='libbrotlidec.so.1')
        for field, bad in [('soname', ['libbrotlidec.so.2']), ('exports', ['a']), ('needed', ['libother.so.1'])]:
            with self.subTest(field=field), self.assertRaises(RuntimeError):
                VERIFY.compare_abi(before, {**before, field: bad}, soname='libbrotlidec.so.1')

    def test_actual_elf_parser_refuses_newer_glibc_before_calling_candidate_library(self):
        outputs = ['Class: ELF64\nMachine: Advanced Micro Devices X86-64\n',
                   '(SONAME) Library soname: [libbrotlidec.so.1]\n', 'Name: GLIBC_2.40\n']
        with patch.object(VERIFY, 'capture', side_effect=outputs):
            with self.assertRaisesRegex(RuntimeError, 'glibc_too_new'):
                VERIFY.elf(self.temp / 'synthetic.so')
        with patch.object(VERIFY, 'capture', return_value='Class: ELF32\nMachine: ARM\n'):
            with self.assertRaisesRegex(RuntimeError, 'elf_architecture'):
                VERIFY.elf(self.temp / 'synthetic.so')

    def test_candidate_tests_bind_embedded_python_and_c_api_without_live_exploit(self):
        source = (RECIPE / 'verify.py').read_text()
        self.assertIn("BrotliDecoderErrorString(-17) == b'_ERROR_FORMAT_BLOCK_SWITCH'", source)
        self.assertIn('BrotliDecoderDecompressStream', source)
        self.assertIn('range(10000)', source)
        self.assertIn('len(body) <= len(plain)', source)
        self.assertIn('is_relative_to(candidate)', source)
        self.assertIn('old_shared_decoder_loaded', source)
        self.assertIn('encoded[:-1]', source)
        self.assertIn('512 * 1024 * 1024', source)
        self.assertIn('signal.alarm(30)', source)

    def test_embedded_python_proof_precedes_global_libraries_and_refuses_interposition(self):
        calls = []
        with patch.object(VERIFY.Path, 'is_file', return_value=True), \
             patch.object(VERIFY.resource, 'setrlimit'), patch.object(VERIFY.signal, 'signal'), \
             patch.object(VERIFY.signal, 'alarm'), patch.object(VERIFY, 'compare_abi'), \
             patch.object(VERIFY, 'elf', return_value={'soname': ['fixture'], 'glibc': [], 'needed': []}), \
             patch.object(VERIFY, 'python_api', side_effect=lambda _: calls.append('python')), \
             patch.object(VERIFY, 'c_api', side_effect=lambda _: calls.append('c')), patch('builtins.print'):
            VERIFY.run(Path('/work/old'), Path('/work/candidate'))
        self.assertEqual(calls, ['python', 'c'])
        with patch.object(VERIFY.sys, 'getdlopenflags', return_value=0), \
             patch.object(VERIFY.Path, 'open', return_value=StringIO('public fixture no shared decoder')):
            VERIFY.python_isolation()
        with patch.object(VERIFY.sys, 'getdlopenflags', return_value=0), \
             patch.object(VERIFY.Path, 'open', return_value=StringIO('/usr/lib/libbrotlidec.so.1')):
            with self.assertRaisesRegex(RuntimeError, 'shared_decoder_loaded_before_python_proof'):
                VERIFY.python_isolation()
        with patch.object(VERIFY.sys, 'getdlopenflags', return_value=os.RTLD_GLOBAL):
            with self.assertRaisesRegex(RuntimeError, 'python_global_loader_flags'):
                VERIFY.python_isolation()
        source = ast.parse((RECIPE / 'verify.py').read_text())
        function = next(n for n in source.body if isinstance(n, ast.FunctionDef) and n.name == 'python_api')
        isolation_calls = [n for n in function.body if isinstance(n, ast.Expr) and
                           isinstance(n.value, ast.Call) and isinstance(n.value.func, ast.Name) and
                           n.value.func.id == 'python_isolation']
        self.assertEqual(len(isolation_calls), 2)

    def test_default_playbook_has_no_writes_before_explicit_apply_boundary(self):
        tasks = PLAY['tasks']
        boundary = next(i for i, task in enumerate(tasks) if task.get('ansible.builtin.meta') == 'end_play')
        self.assertFalse(PLAY['vars']['brotli_build_apply'])
        self.assertTrue(all('ansible.builtin.copy' not in t for t in tasks[:boundary]))
        self.assertTrue(all('ansible.builtin.file' not in t for t in tasks[:boundary]))
        self.assertTrue(all('read only' in t['name'].lower() or t.get('changed_when') is False or 'ansible.builtin.assert' in t for t in tasks[:boundary]))
        admission = PLAY['vars']['brotli_host_admission']
        self.assertIn('12 if site', admission); self.assertIn('else 44', admission)
        self.assertIn('floor+2*1024**3', admission)
        self.assertIn('not os.path.lexists(root)', admission)

    def test_compile_container_is_offline_limited_readonly_and_mounts_only_new_output(self):
        task = next(t for t in PLAY['tasks'] if t['name'].startswith('Compile and test'))
        code = task['ansible.builtin.command']['argv'][-1]
        ast.parse(code)
        for required in ('--network=none', '--read-only', '--cap-drop=ALL', '--security-opt=no-new-privileges',
                         '--cpus=2', '--memory=2g', '--memory-swap=2g', '--pids-limit=256', 'timeout=1600', 'RLIMIT_FSIZE'):
            self.assertIn(required, code)
        self.assertEqual(code.count('type=bind'), 1)
        self.assertIn("str(root/'output')+',target=/work'", code)
        for forbidden in ('/var/run/docker.sock,', '/root/.ssh', 'secure-envs', '--privileged', '--network=host'):
            self.assertNotIn(forbidden, code)
        self.assertIn('DOCKER_CONFIG', code)
        self.assertGreater(task['timeout'], 1600)

    def test_every_embedded_python_program_compiles_without_execution(self):
        compile(PLAY['vars']['brotli_host_admission'], '<admission>', 'exec')
        for task in PLAY['tasks']:
            argv = task.get('ansible.builtin.command', {}).get('argv', [])
            if '-c' in argv: compile(argv[argv.index('-c') + 1], '<ansible-source>', 'exec')


if __name__ == '__main__': unittest.main()
