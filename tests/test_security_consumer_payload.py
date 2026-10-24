"""Synthetic Git objects only: no actual Git, build, network or runtime access."""
from contextlib import ExitStack
import ast
from hashlib import sha1, sha256
import importlib.util
import io
import json
import os
import pathlib
from pathlib import Path
import re
import stat
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

import yaml

REPO = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('_payload_fixture', REPO / 'scripts/prepare_security_consumer_payload.py')
M = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(M)


def blob(raw):
    return sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()


def listing(entries):
    return b''.join(f'{mode} blob {oid}\t{name}\0'.encode() for name, (mode, oid) in entries.items())


class SecurityPayload(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack(); self.addCleanup(self.stack.close)
        for name in ('subprocess.run', 'subprocess.Popen', 'os.system', 'socket.socket.connect',
                     'socket.create_connection', 'socket.getaddrinfo', 'urllib.request.urlopen'):
            self.stack.enter_context(patch(name, side_effect=AssertionError('synthetic only')))
        self.temp = Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix='.payload-fixture-', dir=REPO)))
        self.base_raw = {'core/market_intelligence/coin_rate_engine.py': b'unchanged market baseline\n',
                         'scripts/baseline.py': b'unchanged baseline script\n',
                         'frontend/src/components/JalaliDatePicker.vue': b'unchanged baseline calendar\n',
                         'main.py': b'old main\n'}
        self.overlay_raw = {name: ('public security fixture ' + name + '\n').encode() for name in M.OVERLAY}
        self.base = {name: ('100644', blob(raw)) for name, raw in self.base_raw.items()}
        self.overlay = {name: ('100644', blob(raw)) for name, raw in self.overlay_raw.items()}
        self.stack.enter_context(patch.object(M, 'BASELINE_TREE', M.tree_digest(self.base)))

    def test_git_tree_hash_empty_and_known_raw_tree_order(self):
        self.assertEqual((REPO / 'scripts/prepare_security_consumer_payload.py').read_text().count(
            "['/usr/bin/git', '--no-optional-locks', '-C'"), 2)
        self.assertEqual(M.tree_digest({}), '4b825dc642cb6eb9a060e54bf8d69288fbee4904')
        files = {'a/z': ('100644', '1' * 40), 'a.c': ('100755', '2' * 40), 'a0': ('100644', '3' * 40)}
        child = b'100644 z\0' + bytes.fromhex('1' * 40)
        child_oid = sha1(b'tree ' + str(len(child)).encode() + b'\0' + child).digest()
        raw = b'100755 a.c\0' + bytes.fromhex('2' * 40) + b'40000 a\0' + child_oid + b'100644 a0\0' + bytes.fromhex('3' * 40)
        self.assertEqual(M.tree_digest(files), sha1(b'tree ' + str(len(raw)).encode() + b'\0' + raw).hexdigest())

    def test_closed_overlay_preserves_all_market_calendar_and_incident_absence(self):
        self.assertEqual(len(M.OVERLAY), 24)
        self.assertIn('scripts/verify_sqlite_runtime.py', M.OVERLAY)
        value = M.compose(self.base, self.overlay)
        for name in self.base_raw:
            if name not in M.OVERLAY: self.assertEqual(value[name], self.base[name])
        for name in ('scripts/market_pipeline_watchdog.py', 'scripts/incident_new_market_handoff.py'):
            self.assertNotIn(name, value)
        self.assertNotIn('frontend/src/components/JalaliDatePicker.vue', M.OVERLAY)
        for mutation in ({**self.overlay, 'core/market_intelligence/new.py': ('100644', 'a' * 40)},
                         {name: item for name, item in self.overlay.items() if name != 'main.py'},
                         {**self.overlay, 'main.py': ('120000', 'b' * 40)}):
            with self.assertRaises(RuntimeError): M.compose(self.base, mutation)
        with patch.object(M, 'BASELINE_TREE', '0' * 40), self.assertRaises(RuntimeError):
            M.compose(self.base, self.overlay)

    def test_git_path_parser_refuses_links_from_overlay_and_escape_or_duplicate_descriptors(self):
        self.assertEqual(M.entries(listing(self.base)), self.base)
        for name in ('../outside', '/absolute', 'a/../b', 'a//b', 'a\nb'):
            with self.subTest(name=name), self.assertRaises(RuntimeError):
                M.entries(listing({name: ('100644', '1' * 40)}))
        with self.assertRaises(RuntimeError): M.entries(listing(self.base) * 2)
        with self.assertRaises(RuntimeError): M.entries(b'160000 commit ' + b'1' * 40 + b'\tmodule\0')

    def fake_git(self, repo, *args, data=None, **kwargs):
        if args[:1] == ('rev-parse',):
            return ((M.BASELINE_TREE if args[1] == M.BASELINE + '^{tree}' else 'e' * 40) + '\n').encode()
        if args[:2] == ('ls-tree', '-r'):
            return listing(self.base if args[3] == M.BASELINE else self.overlay)
        self.assertEqual(args, ('cat-file', '--batch-check'))
        raw_by_oid = {blob(raw): raw for raw in [*self.base_raw.values(), *self.overlay_raw.values()]}
        return b''.join(oid + b' blob ' + str(len(raw_by_oid[oid.decode()])).encode() + b'\n' for oid in data.splitlines())

    def prepare(self, output=None, *, corrupt=False):
        raw_by_oid = {blob(raw): raw for raw in [*self.base_raw.values(), *self.overlay_raw.values()]}
        def batch(argv, *, input, stdout, **kwargs):
            self.assertEqual(argv[-2:], ['cat-file', '--batch'])
            for oid in input.splitlines():
                raw = raw_by_oid[oid.decode()]
                stdout.write(oid + b' blob ' + str(len(raw)).encode() + b'\n' + (raw.upper() if corrupt else raw) + b'\n')
        with patch.object(M, 'published_source', return_value='https://github.com/owned/project'), \
             patch.object(M, 'git', side_effect=self.fake_git), patch.object(M.subprocess, 'run', side_effect=batch) as child:
            value = M.prepare(self.temp, 'a' * 40, output)
            if output is None: child.assert_not_called()
            return value

    def test_default_check_has_no_output_or_subprocess_build(self):
        record = self.prepare()
        self.assertEqual(list(self.temp.iterdir()), [])
        self.assertEqual(record['baseline_commit'], M.BASELINE)
        self.assertFalse(record['application_activation'])
        self.assertNotEqual(record['payload_tree'], record['source_tree'])

    def test_preparation_writes_only_git_bytes_in_fresh_private_context_with_real_digests(self):
        output = self.temp / 'new'; record = self.prepare(output)
        self.assertEqual(json.loads((output / 'payload.json').read_bytes()), record)
        self.assertEqual((output / 'context/main.py').read_bytes(), self.overlay_raw['main.py'])
        self.assertEqual((output / 'context/core/market_intelligence/coin_rate_engine.py').read_bytes(),
                         self.base_raw['core/market_intelligence/coin_rate_engine.py'])
        self.assertEqual(list((output / 'context/pip_packages').iterdir()), [])
        for item in record['files']:
            path = output / 'context' / item['path']
            self.assertEqual(sha256(path.read_bytes()).hexdigest(), item['sha256'])
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        with self.assertRaisesRegex(RuntimeError, 'existing_output_refused'): self.prepare(output)

    def test_corrupted_blob_cannot_publish_acceptance_manifest(self):
        output = self.temp / 'corrupt'
        with self.assertRaisesRegex(RuntimeError, 'blob_stream_changed'): self.prepare(output, corrupt=True)
        self.assertFalse((output / 'payload.json').exists())

    def test_post_npm_validation_refuses_unlisted_backend_bytes_and_changed_source(self):
        output = self.temp / 'frontend'; record = self.prepare(output); context = output / 'context'
        dist = context / 'mini_app_dist'; dist.mkdir(mode=0o700)
        (dist / 'index.html').write_bytes(b'public frontend build'); (dist / 'index.html').chmod(0o600)
        digest = M.verify_context(context, record); self.assertEqual(len(digest), 64)
        extra = context / 'core/market_intelligence/unapproved.py'; extra.write_bytes(b'never ship'); extra.chmod(0o600)
        with self.assertRaisesRegex(RuntimeError, 'unlisted_context_file'): M.verify_context(context, record)
        extra.unlink(); original = (context / 'main.py').read_bytes(); (context / 'main.py').write_bytes(b'changed')
        with self.assertRaisesRegex(RuntimeError, 'sealed_context_source_changed'): M.verify_context(context, record)
        (context / 'main.py').write_bytes(original); alias = dist / 'bad.js'; alias.symlink_to(context / 'main.py')
        with self.assertRaisesRegex(RuntimeError, 'context_entry_metadata'): M.verify_context(context, record)

    def test_labels_never_claim_full_main_as_the_product_source(self):
        record = self.prepare()
        labels = M.image_labels(record, 'b' * 64)
        self.assertEqual(labels['org.opencontainers.image.revision'], M.BASELINE)
        self.assertEqual(labels['io.gold-trade.release.tree'], record['payload_tree'])
        self.assertEqual(labels['io.gold-trade.security.overlay.revision'], 'a' * 40)
        self.assertEqual(labels['io.gold-trade.security.payload.sha256'], 'b' * 64)
        with self.assertRaises(RuntimeError): M.image_labels(record, 'unbound')

    def test_output_parent_symlink_or_writable_refuses_before_any_new_file(self):
        parent = self.temp / 'parent'; parent.mkdir(mode=0o700)
        alias = self.temp / 'alias'; alias.symlink_to(parent, target_is_directory=True)
        with self.assertRaises(RuntimeError): self.prepare(alias / 'refuse')
        parent.chmod(0o777)
        with self.assertRaises(RuntimeError): self.prepare(parent / 'refuse')
        self.assertEqual(list(parent.iterdir()), [])

    def test_untrusted_main_branch_publication_remote_or_dirty_checkout_refuses(self):
        def valid(repo, *args, **kwargs):
            if args == ('rev-parse', '--show-object-format'): return b'sha1\n'
            if args == ('symbolic-ref', '--short', 'HEAD'): return b'main\n'
            if args == ('rev-parse', '--abbrev-ref', '@{u}'): return b'origin/main\n'
            if args[0] == 'rev-parse': return b'a' * 40 + b'\n'
            if args[0] == 'status': return b''
            if args[0] == 'remote': return b'https://github.com/mohsenbarari/trading_bot.git\n'
            if args[0] == 'ls-remote': return b'a' * 40 + b'\trefs/heads/main\n'
            raise AssertionError('unexpected Git operation')
        with patch.object(M, 'git', side_effect=valid):
            self.assertEqual(M.published_source(self.temp, 'a' * 40), 'https://github.com/mohsenbarari/trading_bot')
        for command, reply in (('status', b' M main.py\n'), ('symbolic-ref', b'candidate\n'),
                               ('remote', b'https://credential@github.com/owned/project'),
                               ('ls-remote', b'b' * 40 + b'\trefs/heads/main\n')):
            def changed(repo, *args, **kwargs): return reply if args[0] == command else valid(repo, *args, **kwargs)
            with self.subTest(command=command), patch.object(M, 'git', side_effect=changed), self.assertRaises(RuntimeError):
                M.published_source(self.temp, 'a' * 40)
        with patch.object(M, 'git', side_effect=lambda repo, *args, **kw:
                          b'https://github.com/different/repository.git' if args[0] == 'remote' else valid(repo, *args, **kw)):
            with self.assertRaisesRegex(RuntimeError, 'wrong_source_repository'):
                M.published_source(self.temp, 'a' * 40)

    def test_ci_is_explicit_public_payload_only_no_activation_or_scanner_waiver(self):
        workflow = yaml.safe_load((REPO / '.github/workflows/pre-release-gate.yml').read_text())
        job = workflow['jobs']['security-consumer-payload']
        self.assertEqual(job['if'], "github.event_name == 'workflow_dispatch' && inputs.security_payload && github.ref == 'refs/heads/main' && (inputs.git_ref == '' || inputs.git_ref == 'main' || inputs.git_ref == github.sha)")
        self.assertEqual(job['permissions'], {'contents': 'read'})
        self.assertEqual(job['runs-on'], 'ubuntu-latest')
        self.assertEqual(job['env']['TRIVY_IGNORE_UNFIXED'], 'false')
        self.assertEqual(job['env']['TRIVY_IGNOREFILE'], '/dev/null')
        self.assertNotIn('TRIVY_IGNORE_FILE', job['env'])
        self.assertIn("git = ['/usr/bin/git', '--no-optional-locks', '-C', str(REPO)]",
                      (REPO / 'deploy/maintenance/security_consumer_release.py').read_text())
        scanners = [step for step in job['steps'] if step.get('uses', '').startswith('aquasecurity/trivy-action@')]
        self.assertEqual(len(scanners), 2)
        self.assertTrue(all(step['uses'].endswith('ed142fd0673e97e23eac54620cfb913e5ce36c25') for step in scanners))
        gate = scanners[0]['with']
        self.assertEqual(gate['severity'], 'HIGH,CRITICAL'); self.assertEqual(gate['exit-code'], '1')
        self.assertEqual(gate['ignore-unfixed'], 'false')
        self.assertNotIn('continue-on-error', scanners[0])
        steps = json.dumps(job['steps'])
        for forbidden in ('security_consumer_release.py', 'compose up', 'production_deploy_online.sh', 'secrets.'):
            self.assertFalse(forbidden in steps, f'forbidden candidate CI operation: {forbidden}')
        self.assertTrue('helper.verify_context' in steps, 'candidate context verification is required')
        self.assertTrue("'--network','none'" in steps, 'candidate tests must have no network')
        for step in job['steps']:
            script = step.get('run', '')
            if "<<'PY'" in script:
                import ast
                body = script.split("<<'PY'", 1)[1].split('\nPY', 1)[0].lstrip('\n')
                ast.parse(body)

    def workflow_loader(self):
        workflow = yaml.safe_load((REPO / '.github/workflows/pre-release-gate.yml').read_text())
        job = workflow['jobs']['security-consumer-payload']
        script = next(step['run'] for step in job['steps'] if step.get('id') == 'payload-build')
        body = script.split("<<'PY'", 1)[1].split('\nPY', 1)[0].lstrip('\n')
        loader = next(node for node in ast.parse(body).body if isinstance(node, ast.FunctionDef) and node.name == 'load_reviewed_helper')
        namespace = {'os': os, 'pathlib': pathlib, 're': re, 'stat': stat, 'types': types, 'sha256': sha256}
        exec(compile(ast.Module(body=[loader], type_ignores=[]), '<finite-ci-helper-loader>', 'exec'), namespace)
        return namespace['load_reviewed_helper']

    def ci_program(self, name):
        job = yaml.safe_load((REPO / '.github/workflows/pre-release-gate.yml').read_text())['jobs']['security-consumer-payload']
        step = next(step for step in job['steps'] if step.get('id') == name or step['name'] == name)
        body = step['run'].split("<<'PY'", 1)[1].split('\nPY', 1)[0].lstrip('\n')
        return ast.parse(body), job

    def sealed_test_fixture(self):
        tree, _ = self.ci_program('payload_seal')
        function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'seal_test_sources')
        namespace = {'re': re, 'json': json, 'sha1': sha1, 'sha256': sha256}
        exec(compile(ast.Module(body=[function], type_ignores=[]), '<finite-test-source-sealer>', 'exec'), namespace)
        raw = b'import subprocess,unittest\nclass Verified(unittest.TestCase):\n def test_fence(self):\n  with self.assertRaises(AssertionError):subprocess.run(["host-command-must-not-run"])\n'
        contents = {name: raw for name in ('test_chat_upload_image_security.py', 'test_jwt_codec_security.py',
                    'test_frontend_static_security.py', 'test_coin_intelligence_market_store.py', 'test_coin_intelligence_coin_rate_engine.py')}
        contents['unit-test.env.example'] = b'PUBLIC_CONFIG=synthetic-only\n'
        by_oid = {blob(value): value for value in contents.values()}
        def git(_root, *args, **kwargs):
            if args[0] == 'ls-tree':
                name = args[-1].split('/')[-1]
                expected = M.BASELINE if name.startswith('test_coin_intelligence_') else 'a' * 40
                self.assertEqual(args[3], expected)
                return listing({args[-1]: ('100644', blob(contents[name]))})
            self.assertEqual(args[:2], ('cat-file', 'blob'))
            return by_oid[args[2]]
        out = self.temp / 'sealed-tests'; out.mkdir(mode=0o700)
        with patch.object(M, 'git', side_effect=git):
            digest = namespace['seal_test_sources'](self.temp, out, 'a' * 40, M)
        return out, digest

    def test_ci_seals_five_exact_git_test_blobs_and_public_config_before_npm(self):
        out, digest = self.sealed_test_fixture()
        raw = (out / 'test-sources.json').read_bytes(); proof = json.loads(raw)
        self.assertEqual(sha256(raw).hexdigest(), digest)
        self.assertEqual(len(proof['files']), 6)
        self.assertFalse(proof['runtime_data_included'])
        for row in proof['files']:
            data = (out / 'tests' / row['name']).read_bytes()
            self.assertEqual(row['git_blob'], blob(data)); self.assertEqual(row['sha256'], sha256(data).hexdigest())
            self.assertEqual(row['bytes'], len(data))
            expected = M.BASELINE if row['name'].startswith('test_coin_intelligence_') else 'a' * 40
            self.assertEqual(row['source_commit'], expected)
        tree, job = self.ci_program('payload_seal')
        source = next(step['run'] for step in job['steps'] if step.get('id') == 'payload_seal')
        self.assertIn('test_sources_sha256=', source)
        self.assertNotIn('cp tests/', json.dumps(job['steps']))

    def offline_runner(self, out):
        tree, _ = self.ci_program('Exercise exact candidate security and baseline Market behavior without live data')
        value = next(node.value for node in tree.body if isinstance(node, ast.Assign) and
                     any(isinstance(target, ast.Name) and target.id == 'runner' for target in node.targets))
        raw = ast.literal_eval(value)
        raw = raw.replace("'/app/tests'", repr(str(out / 'tests')))
        raw = raw.replace("'/proof/test-sources.json'", repr(str(out / 'test-sources.json')))
        return raw

    def test_offline_runner_executes_only_verified_buffers_under_absolute_host_fence(self):
        out, digest = self.sealed_test_fixture(); code = self.offline_runner(out)
        with patch.object(sys, 'argv', ['fixture', digest, 'a' * 40, 'sha256:' + 'b' * 64]), \
             patch.object(sys, 'path', list(sys.path)), patch.dict(sys.modules, {'magic': types.ModuleType('magic')}), \
             patch.dict(os.environ, {}), patch('builtins.print') as report, patch.object(sys, 'stderr', io.StringIO()):
            with self.assertRaises(SystemExit) as result:
                exec(compile(code, '<synthetic-offline-runner>', 'exec'), {})
        self.assertEqual(result.exception.code, 0)
        proof = json.loads(report.call_args.args[0])
        self.assertEqual(proof['modules'], 5); self.assertEqual(proof['tests'], 5)
        self.assertEqual(proof['status'], 'passed')
        self.assertLess(code.index('import magic'), code.index('with ExitStack() as fence'))

    def test_offline_runner_rejects_post_npm_test_changes_before_loading_modules(self):
        out, digest = self.sealed_test_fixture(); code = self.offline_runner(out)
        (out / 'tests/test_frontend_static_security.py').write_bytes(b'raise RuntimeError("unverified test executed")')
        with patch.object(sys, 'argv', ['fixture', digest, 'a' * 40, 'sha256:' + 'b' * 64]), \
             patch.object(unittest.TextTestRunner, 'run') as run, self.assertRaises(AssertionError):
            exec(compile(code, '<synthetic-offline-runner>', 'exec'), {})
        run.assert_not_called()

    def test_ci_export_and_all_runtime_checks_use_completed_immutable_image_output(self):
        _, job = self.ci_program('payload_export')
        expected = '${{ steps.payload-build.outputs.image_id }}'
        scans = [step for step in job['steps'] if step.get('uses', '').startswith('aquasecurity/trivy-action@')]
        self.assertTrue(all(step['with']['image-ref'] == expected for step in scans))
        export = next(step for step in job['steps'] if step.get('id') == 'payload_export')
        self.assertEqual(export['env']['PAYLOAD_IMAGE'], expected)
        self.assertEqual(export['if'], "always() && steps.payload-build.outcome == 'success'")
        archive = next(step for step in job['steps'] if step['name'].startswith('Retain exact public image archive'))
        self.assertEqual(archive['with']['retention-days'], 7)
        self.assertIn("steps.payload_export.outcome == 'success'", archive['if'])
        self.assertIn('exported_not_accepted', export['run'])
        self.assertIn("'image','save',image", export['run'])
        self.assertNotIn('security-consumer-candidate:ci', export['run'])

    def test_export_stream_binds_bytes_to_exact_image_and_keeps_acceptance_false(self):
        tree, _ = self.ci_program('payload_export')
        target = self.temp / '.local/security-payload'; target.mkdir(parents=True, mode=0o700)
        image = 'sha256:' + 'b' * 64
        raw = json.dumps({'image_id': image, 'deployment_performed': False, 'source_commit': 'a' * 40}).encode()
        (target / 'image-build.json').write_bytes(raw)
        public = b'public image export fixture; not an actual candidate'
        class Child:
            pid = 987654
            stdout = io.BytesIO(public)
            def wait(self, timeout): return 0
        def save(argv, **kwargs):
            self.assertEqual(argv, ['docker', '--host=unix:///var/run/docker.sock', 'image', 'save', image])
            return Child()
        with patch.object(pathlib.Path, 'cwd', return_value=self.temp), \
             patch.dict(os.environ, {'PAYLOAD_IMAGE': image, 'PAYLOAD_IMAGE_BUILD_SHA256': sha256(raw).hexdigest()}), \
             patch('subprocess.Popen', side_effect=save), patch('os.killpg'), patch('signal.signal'), patch('signal.alarm'):
            exec(compile(tree, '<synthetic-image-export>', 'exec'), {})
        proof = json.loads((target / 'image-export.json').read_bytes())
        archive = (target / 'candidate-image.tar.gz').read_bytes()
        self.assertEqual(proof['status'], 'exported_not_accepted')
        self.assertEqual(proof['image_id'], image)
        self.assertEqual(proof['archive']['sha256'], sha256(archive).hexdigest())
        self.assertEqual(proof['archive']['size_bytes'], len(archive))
        self.assertEqual(proof['archive']['uncompressed_bytes'], len(public))
        self.assertFalse(proof['runtime_data_included']); self.assertFalse(proof['deployment_performed'])

    def test_export_refuses_changed_build_receipt_before_docker_or_archive_creation(self):
        tree, _ = self.ci_program('payload_export')
        target = self.temp / '.local/security-payload'; target.mkdir(parents=True, mode=0o700)
        image = 'sha256:' + 'b' * 64
        (target / 'image-build.json').write_bytes(b'changed public receipt')
        with patch.object(pathlib.Path, 'cwd', return_value=self.temp), \
             patch.dict(os.environ, {'PAYLOAD_IMAGE': image, 'PAYLOAD_IMAGE_BUILD_SHA256': '0' * 64}), \
             patch('subprocess.Popen') as launch, self.assertRaises(AssertionError):
            exec(compile(tree, '<synthetic-image-export>', 'exec'), {})
        launch.assert_not_called()
        self.assertFalse((target / 'candidate-image.tar.gz').exists())
        self.assertFalse((target / 'image-export.json').exists())

    def test_export_size_refusal_reaps_only_owned_child_and_never_publishes_proof(self):
        tree, _ = self.ci_program('payload_export')
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'maximum' for t in node.targets):
                node.value = ast.Constant(value=128)  # Small explicit fixture, never allocate a GiB.
        ast.fix_missing_locations(tree)
        target = self.temp / '.local/security-payload'; target.mkdir(parents=True, mode=0o700)
        image = 'sha256:' + 'b' * 64
        raw = json.dumps({'image_id': image, 'deployment_performed': False}).encode()
        (target / 'image-build.json').write_bytes(raw)
        child = types.SimpleNamespace(pid=987654, stdout=io.BytesIO(b'x' * 129), wait=lambda timeout: 0)
        with patch.object(pathlib.Path, 'cwd', return_value=self.temp), \
             patch.dict(os.environ, {'PAYLOAD_IMAGE': image, 'PAYLOAD_IMAGE_BUILD_SHA256': sha256(raw).hexdigest()}), \
             patch('subprocess.Popen', return_value=child), patch('os.killpg') as kill, \
             patch('signal.signal'), patch('signal.alarm'), self.assertRaises(AssertionError):
            exec(compile(tree, '<synthetic-bounded-image-export>', 'exec'), {})
        kill.assert_called_once_with(child.pid, __import__('signal').SIGKILL)
        self.assertTrue(child.stdout.closed)
        self.assertFalse((target / 'image-export.json').exists())

    def test_post_npm_helper_uses_a_pre_npm_step_output_not_mutable_workspace_hash(self):
        workflow = yaml.safe_load((REPO / '.github/workflows/pre-release-gate.yml').read_text())
        steps = workflow['jobs']['security-consumer-payload']['steps']
        seal = next(step for step in steps if step.get('id') == 'payload_seal')
        build = next(step for step in steps if step.get('id') == 'payload-build')
        frontend = next(step for step in steps if step['name'] == 'Build only the sealed security frontend source')
        self.assertLess(steps.index(seal), steps.index(frontend))
        self.assertLess(steps.index(frontend), steps.index(build))
        self.assertIn('helper_sha256=', seal['run'])
        self.assertIn('source.read(131073)', seal['run'])
        self.assertEqual(build['env']['PAYLOAD_EXPECTED_HELPER'], '${{ steps.payload_seal.outputs.helper_sha256 }}')
        self.assertEqual(build['env']['PAYLOAD_EXPECTED_MANIFEST'], '${{ inputs.security_payload_manifest_sha256 }}')
        self.assertEqual(build['env']['PAYLOAD_SOURCE_COMMIT'], '${{ github.sha }}')
        self.assertNotIn('exec_module', build['run'])

    def test_changed_post_npm_helper_cannot_execute_and_verified_buffer_is_not_reopened(self):
        loader = self.workflow_loader()
        root = self.temp / 'loader'; (root / 'scripts').mkdir(parents=True, mode=0o700)
        path = root / 'scripts/prepare_security_consumer_payload.py'
        good = b'VALUE = 23\n'; path.write_bytes(good); path.chmod(0o600)
        expected = sha256(good).hexdigest()
        self.assertEqual(loader(root, expected).VALUE, 23)
        path.write_bytes(b'raise RuntimeError("changed helper executed")\n')
        with self.assertRaises(AssertionError): loader(root, expected)
        path.write_bytes(good)
        original = compile
        def change_after_verification(raw, filename, mode):
            self.assertEqual(raw, good)
            path.write_bytes(b'raise RuntimeError("reopened helper executed")\n')
            return original(raw, filename, mode)
        with patch('builtins.compile', side_effect=change_after_verification):
            self.assertEqual(loader(root, expected).VALUE, 23)

    def test_post_npm_helper_rejects_links_writable_or_oversized_source(self):
        loader = self.workflow_loader()
        root = self.temp / 'unsafe-loader'; (root / 'scripts').mkdir(parents=True, mode=0o700)
        path = root / 'scripts/prepare_security_consumer_payload.py'
        raw = b'VALUE = 23\n'; path.write_bytes(raw); path.chmod(0o600)
        expected = sha256(raw).hexdigest()
        path.chmod(0o666)
        with self.assertRaises(AssertionError): loader(root, expected)
        path.chmod(0o600); alias = root / 'alias'; os.link(path, alias)
        with self.assertRaises(AssertionError): loader(root, expected)
        alias.unlink(); path.rename(alias); path.symlink_to(alias)
        with self.assertRaises(AssertionError): loader(root, expected)
        path.unlink(); alias.rename(path); path.write_bytes(b'x' * 131073)
        with self.assertRaises(AssertionError): loader(root, expected)


if __name__ == '__main__': unittest.main()
