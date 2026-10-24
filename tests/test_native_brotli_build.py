"""Build-only source contracts. Never invoke Docker, compiler, network or host IO."""
import ast
from contextlib import ExitStack
from hashlib import sha256
import importlib.util
import json
import os
from pathlib import Path
import pathlib
import re
import stat
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

    def ci_job(self):
        workflow = yaml.safe_load((ROOT / '.github/workflows/noble-brotli-build.yml').read_text())
        return workflow, workflow['jobs']['noble-brotli']

    def ci_functions(self, title, names):
        _, job = self.ci_job()
        script = next(step['run'] for step in job['steps'] if step['name'].startswith(title))
        body = script.split("<<'PY'", 1)[1].split('\nPY', 1)[0].lstrip('\n')
        tree = ast.parse(body)
        nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names or
                 isinstance(node, ast.Assign) and any(isinstance(name, ast.Name) and name.id in {'RECIPE', 'RECIPE_COMMIT'} for name in node.targets)]
        namespace = {'os': os, 'pathlib': pathlib, 'stat': stat, 'sha256': sha256, 'json': json,
                     're': re, 'tempfile': tempfile}
        exec(compile(ast.Module(body=nodes, type_ignores=[]), '<finite-brotli-ci-functions>', 'exec'), namespace)
        return namespace

    def test_hosted_workflow_is_manual_one_job_without_production_authority(self):
        workflow, job = self.ci_job()
        self.assertEqual(workflow.get('on', workflow.get(True)), {'workflow_dispatch': None})
        self.assertEqual(workflow['permissions'], {'contents': 'read'})
        self.assertEqual(set(workflow['jobs']), {'noble-brotli'})
        self.assertEqual(job['if'], "github.ref == 'refs/heads/main'")
        self.assertEqual(job['runs-on'], 'ubuntu-24.04')
        self.assertFalse(workflow['concurrency']['cancel-in-progress'])
        self.assertEqual(job['timeout-minutes'], 80)
        checkout = job['steps'][0]
        self.assertFalse(checkout['with']['persist-credentials'])
        self.assertEqual(checkout['with']['ref'], '${{ github.sha }}')
        scripts = json.dumps(job['steps'])
        for forbidden in ('secrets.', 'ansible-playbook', 'ssh ', '--privileged', '--cap-add', 'docker.sock,', 'production_deploy'):
            self.assertNotIn(forbidden, scripts)
        compile_step = next(step for step in job['steps'] if step.get('id') == 'candidate')
        code = compile_step['run']
        for required in ('--network=none', '--read-only', '--cap-drop=ALL', '--security-opt=no-new-privileges',
                         '--cpus=2', '--memory=2g', '--memory-swap=2g', '--pids-limit=256', 'size=128m',
                         "image,image],'compile-and-tests',1600", "owned.st_uid==0", 'RLIMIT_FSIZE'):
            self.assertIn(required, code)
        self.assertEqual(code.count('type=bind'), 2)
        self.assertIn("'--entrypoint','/usr/bin/dpkg',image,'--install'", code)
        for step in job['steps']:
            script = step.get('run', '')
            if "<<'PY'" in script:
                compile(script.split("<<'PY'", 1)[1].split('\nPY', 1)[0].lstrip('\n'), '<hosted-ci-program>', 'exec')

    def test_ci_stages_only_exact_recipe_bytes_and_refuses_drift_before_build(self):
        namespace = self.ci_functions('Stage exactly', {'stage_recipe'})
        self.assertEqual(namespace['RECIPE_COMMIT'], '9b38f2e1970204bde863d7ad60023e4ec8d48768')
        self.assertEqual(set(namespace['RECIPE']), {'Dockerfile', 'build.py', 'verify.py', 'CVE-2026-111497.patch'})
        disk = type('Space', (), {'f_bavail': 8 * 1024**3, 'f_frsize': 1})()
        with patch.object(os, 'statvfs', return_value=disk), patch.dict(os.environ, {'GITHUB_SHA': 'a' * 40}):
            root, digest = namespace['stage_recipe'](RECIPE, self.temp)
            self.assertEqual({path.name for path in (root / 'context').iterdir()}, set(namespace['RECIPE']))
            self.assertEqual(sha256((root / 'recipe-identity.json').read_bytes()).hexdigest(), digest)
            for path in (root / 'context').iterdir():
                self.assertEqual(path.read_bytes(), (RECIPE / path.name).read_bytes())
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            wrong = self.temp / 'wrong'; wrong.mkdir(mode=0o700)
            for path in RECIPE.iterdir():
                if path.name in namespace['RECIPE']: (wrong / path.name).write_bytes(path.read_bytes())
            (wrong / 'build.py').write_bytes(b'changed public recipe')
            with self.assertRaises(AssertionError): namespace['stage_recipe'](wrong, self.temp)

    def ci_export_fixture(self):
        root = self.temp / ('export-' + str(len(list(self.temp.iterdir())))); root.mkdir(mode=0o700)
        for name in ('public', 'output', 'output/artifacts'): (root / name).mkdir(mode=0o700)
        constants = self.ci_functions('Stage exactly', set())
        identity = {'recipe_commit': constants['RECIPE_COMMIT'], 'recipe_sha256': constants['RECIPE'], 'workflow_commit': 'a' * 40}
        identity_raw = (json.dumps(identity, sort_keys=True) + '\n').encode(); (root / 'recipe-identity.json').write_bytes(identity_raw)
        tests = {'status': 'passed', 'python_embedded_decoder_rebuilt': True,
                 'abi': {name: {'exports_retained': True} for name in ('common', 'dec', 'enc')},
                 'c_api': dict.fromkeys(('roundtrip', 'streaming', 'security_error_minus17'), True),
                 'python_api': dict.fromkeys(('roundtrip', 'streaming', 'bounded_malformed', 'candidate_module_path', 'no_shared_decoder_interposition'), True)}
        image = 'sha256:' + 'b' * 64; rows = []
        for name in BUILD.PACKAGES:
            path = name + '_' + BUILD.VERSION + '_amd64.deb'; raw = ('synthetic-not-a-real-deb:' + name).encode()
            (root / 'output/artifacts' / path).write_bytes(raw)
            rows.append({'name': name, 'path': path, 'bytes': len(raw), 'sha256': sha256(raw).hexdigest()})
        receipt = {'schema': 'noble_brotli_source_build/1', 'status': 'passed', 'version': BUILD.VERSION, 'builder_image_id': image,
                   'host_installed': False, 'services_changed': False, 'maintainer_dsc_signature_verified': False,
                   'recipe_sha256': identity['recipe_sha256'], 'patch_sha256': BUILD.PATCH_SHA,
                   'source_sha256': {name: value[1] for name, value in BUILD.SOURCE.items()}, 'packages': rows, 'tests': tests}
        for name, raw in {'candidate-tests.json': json.dumps(tests).encode(), 'build-receipt.json': json.dumps(receipt).encode(),
                          'brotli.buildinfo': b'public synthetic build info', 'brotli.changes': b'public synthetic changes'}.items():
            (root / 'output/artifacts' / name).write_bytes(raw)
        return root, image, sha256(identity_raw).hexdigest()

    def test_ci_filtered_export_binds_all_eight_public_files_without_touching_private_work(self):
        namespace = self.ci_functions('Export only verified', {'read_public', 'export_public'})
        root, image, digest = self.ci_export_fixture()
        marker = root / 'output/private-build-marker'; marker.write_bytes(b'not an artifact')
        result = namespace['export_public'](root, image, digest, os.geteuid(), os.geteuid(), os.getegid())
        self.assertEqual(result['status'], 'candidate_built_not_installed')
        self.assertFalse(result['production_installed']); self.assertFalse(result['scanner_certifies_custom_backport'])
        self.assertEqual(len(result['files']), 8)
        self.assertEqual(len(list((root / 'public').iterdir())), 9)
        self.assertEqual(marker.read_bytes(), b'not an artifact')
        self.assertFalse((root / 'public/private-build-marker').exists())
        for row in result['files']:
            copied = root / 'public' / row['name']
            self.assertEqual(sha256(copied.read_bytes()).hexdigest(), row['sha256'])
            self.assertEqual(copied.stat().st_mode & 0o777, 0o600)

    def test_ci_export_refuses_tamper_unlisted_files_links_and_stale_identity_without_proof(self):
        namespace = self.ci_functions('Export only verified', {'read_public', 'export_public'})
        for fault in ('package', 'extra', 'link', 'identity'):
            with self.subTest(fault=fault):
                root, image, digest = self.ci_export_fixture(); source = root / 'output/artifacts'
                package = next(source.glob('*.deb'))
                if fault == 'package': package.write_bytes(b'changed')
                elif fault == 'extra': (source / 'unlisted-private-fixture').write_bytes(b'do not export')
                elif fault == 'link':
                    package.unlink(); package.symlink_to(root / 'recipe-identity.json')
                else: digest = 'c' * 64
                with self.assertRaises((AssertionError, OSError)):
                    namespace['export_public'](root, image, digest, os.geteuid(), os.geteuid(), os.getegid())
                self.assertFalse((root / 'public/ci-export.json').exists())

    def test_ci_scanner_inventory_requires_all_four_actual_versions_and_raw_findings_are_truthful(self):
        namespace = self.ci_functions('Require the actual scanner inventory', {'verify_inventory'})
        path = self.temp / 'sbom.json'
        value = {'components': [{'name': name, 'version': BUILD.VERSION} for name in BUILD.PACKAGES]}
        path.write_text(json.dumps(value)); namespace['verify_inventory'](path)
        value['components'][0]['version'] = '1.1.0-2build2'; path.write_text(json.dumps(value))
        with self.assertRaises(AssertionError): namespace['verify_inventory'](path)
        path.write_text('{"components":[]}')
        with self.assertRaises(AssertionError): namespace['verify_inventory'](path)
        _, job = self.ci_job()
        scans = [step for step in job['steps'] if step.get('uses', '').startswith('aquasecurity/trivy-action@')]
        self.assertEqual(len(scans), 2)
        self.assertTrue(all(step['with']['image-ref'] == '${{ steps.candidate.outputs.scan_image_id }}' for step in scans))
        self.assertEqual(scans[0]['with']['severity'], 'UNKNOWN,LOW,MEDIUM,HIGH,CRITICAL')
        self.assertEqual(scans[0]['with']['ignore-unfixed'], 'false')
        self.assertEqual(scans[0]['with']['exit-code'], '0')
        self.assertEqual(job['env']['TRIVY_IGNOREFILE'], '/dev/null')
        uploads = [step for step in job['steps'] if step.get('uses', '').startswith('actions/upload-artifact@')]
        self.assertTrue(all(step['with']['retention-days'] == 7 for step in uploads))
        self.assertIn("steps.package_export.outcome == 'success'", uploads[0]['if'])
        self.assertIn("steps.scan_reports.outcome == 'success'", uploads[1]['if'])

    def test_ci_scanner_export_refuses_unlisted_or_linked_reports(self):
        namespace = self.ci_functions('Require the actual scanner inventory', {'bound_reports'})
        folder = self.temp / 'reports'; folder.mkdir(mode=0o700)
        for name in ('candidate-image.json', 'vulnerabilities.json', 'sbom.cdx.json'): (folder / name).write_text('{}')
        namespace['bound_reports'](folder)
        extra = folder / 'private-marker'; extra.write_bytes(b'never upload')
        with self.assertRaises(AssertionError): namespace['bound_reports'](folder)
        extra.unlink(); (folder / 'vulnerabilities.json').unlink()
        (folder / 'vulnerabilities.json').symlink_to(folder / 'candidate-image.json')
        with self.assertRaises(OSError): namespace['bound_reports'](folder)

    def test_ci_failed_build_tails_only_fixed_public_logs_with_per_file_and_total_caps(self):
        namespace = self.ci_functions('Print only bounded fixed', {'failure_diagnostics'})
        root = self.temp / 'diagnostics'; root.mkdir(mode=0o700); (root / 'output').mkdir(mode=0o700)
        names = ('image-build.log', 'compile-and-tests.log', 'output/build.log', 'output/unpack.log',
                 'output/patch-check.log', 'output/quilt.log', 'output/candidate-tests.json', 'scan-container-install.log')
        for name in names: (root / name).write_bytes(b'old-public-output\n' * 1024 + b'LAST_PUBLIC_ERROR\n::error::must-remain-text\n')
        private = root / 'unlisted-private-marker'; private.write_bytes(b'NEVER_PRINT_THIS')
        records = namespace['failure_diagnostics'](root, os.geteuid())
        self.assertEqual(len(records), 8)
        self.assertLessEqual(sum(map(len, records)), 64 * 1024)
        self.assertTrue(all(len(line) <= 8192 for line in records))
        self.assertEqual({json.loads(line)['log'] for line in records}, set(names))
        self.assertTrue(all('LAST_PUBLIC_ERROR' in json.loads(line)['tail'] for line in records))
        self.assertNotIn(b'NEVER_PRINT_THIS', b''.join(records))
        self.assertTrue(all(line.startswith(b'{') and line.count(b'\n') == 1 for line in records))
        _, job = self.ci_job()
        step = next(step for step in job['steps'] if step['name'].startswith('Print only bounded fixed'))
        self.assertEqual(step['if'], "failure() && steps.recipe.outcome == 'success'")
        self.assertNotIn('continue-on-error', step)

    def test_ci_failure_diagnostics_refuse_links_hardlinks_fifo_and_writable_log(self):
        namespace = self.ci_functions('Print only bounded fixed', {'failure_diagnostics'})
        for kind in ('symlink', 'parent-link', 'hardlink', 'fifo', 'writable'):
            with self.subTest(kind=kind):
                root = self.temp / kind; root.mkdir(mode=0o700)
                marker = root / 'not-a-log'; marker.write_bytes(b'NEVER_PRINT_THIS')
                log = root / 'image-build.log'
                if kind == 'symlink': log.symlink_to(marker)
                elif kind == 'parent-link':
                    (root / 'output').symlink_to(self.temp, target_is_directory=True)
                elif kind == 'hardlink': os.link(marker, log)
                elif kind == 'fifo': os.mkfifo(log, mode=0o600)
                else: log.write_bytes(b'NEVER_PRINT_THIS'); log.chmod(0o666)
                records = namespace['failure_diagnostics'](root, os.geteuid())
                self.assertTrue(records)
                self.assertTrue(all(json.loads(line)['status'] == 'refused' for line in records))
                self.assertNotIn(b'NEVER_PRINT_THIS', b''.join(records))


if __name__ == '__main__': unittest.main()
