"""Hermetic negative proofs for the fixed App build-only directory layer."""
from contextlib import ExitStack, contextmanager, redirect_stderr
from copy import deepcopy
from hashlib import sha256
import ast
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from zipfile import ZipFile, ZIP_STORED

ROOT = Path(__file__).resolve().parents[1]


def load():
    path = ROOT / 'scripts/compose_app_mountpoint_image.py'
    spec = importlib.util.spec_from_file_location('_mountpoint_fixture', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


with ExitStack() as fence:
    for target in ('subprocess.run', 'subprocess.Popen', 'os.system', 'os.kill', 'os.killpg',
                   'socket.create_connection', 'socket.getaddrinfo', 'urllib.request.urlopen'):
        fence.enter_context(patch(target, side_effect=AssertionError('ambient operation denied')))
    M = load()


def tar_rows(rows):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode='w', format=tarfile.USTAR_FORMAT) as archive:
        for name, kind, mode, uid, gid, body in rows:
            entry = tarfile.TarInfo(name)
            entry.type, entry.mode, entry.uid, entry.gid = kind, mode, uid, gid
            entry.size = len(body)
            if kind in (tarfile.SYMTYPE, tarfile.LNKTYPE):
                entry.linkname = 'unrelated'
            archive.addfile(entry, io.BytesIO(body) if body else None)
    output.seek(0)
    return output


class AppMountpointImageTests(unittest.TestCase):
    def setUp(self):
        self.fence = ExitStack()
        self.addCleanup(self.fence.close)
        for target in ('subprocess.run', 'subprocess.Popen', 'os.system', 'os.kill', 'os.killpg',
                       'socket.create_connection', 'socket.getaddrinfo', 'urllib.request.urlopen'):
            self.fence.enter_context(patch(target, side_effect=AssertionError('ambient operation denied')))
        prior = load_prior()
        self.fence.enter_context(patch.object(M, 'PRIOR', prior))
        path = ROOT / 'scripts/admit_security_component_image_reuse.py'
        spec = importlib.util.spec_from_file_location('_mountpoint_app_legacy', path)
        legacy = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(legacy)
        self.fence.enter_context(patch.object(M, 'LEGACY', legacy))

    def rows(self):
        return [(name, tarfile.DIRTYPE, 0o755, 0, 0, b'') for name in M.DIRECTORIES]

    def test_exact_three_directories_pass(self):
        result = M.layer_directories(tar_rows(self.rows()), [0o755, 0, 0])
        self.assertEqual(result['entries'], 3)
        self.assertEqual(result['non_directory_entries'], 0)

    def test_unchanged_app_directory_header_is_allowed(self):
        result = M.layer_directories(tar_rows([('app', tarfile.DIRTYPE, 0o700, 0, 0, b''), *self.rows()]), [0o700, 0, 0])
        self.assertEqual(result['entries'], 4)

    def test_changed_app_mode_refuses(self):
        with self.assertRaisesRegex(RuntimeError, 'directory_layer_mode'):
            M.layer_directories(tar_rows([('app', tarfile.DIRTYPE, 0o700, 0, 0, b''), *self.rows()]), [0o755, 0, 0])

    def test_file_symlink_hardlink_whiteout_and_device_refuse(self):
        for kind, name in ((tarfile.REGTYPE, 'app/runtime/extra'), (tarfile.SYMTYPE, 'app/runtime'),
                           (tarfile.LNKTYPE, 'app/runtime'), (tarfile.CHRTYPE, 'app/.wh.runtime')):
            with self.subTest(kind=kind, name=name), self.assertRaisesRegex(RuntimeError, 'directory_layer_outside_scope'):
                M.layer_directories(tar_rows([(name, kind, 0o755, 0, 0, b''), *self.rows()]), [0o755, 0, 0])

    def test_unrelated_directory_refuses(self):
        with self.assertRaisesRegex(RuntimeError, 'directory_layer_outside_scope'):
            M.layer_directories(tar_rows([('usr', tarfile.DIRTYPE, 0o755, 0, 0, b''), *self.rows()]), [0o755, 0, 0])

    def test_directory_traversal_refuses(self):
        with self.assertRaisesRegex(RuntimeError, 'directory_layer_outside_scope'):
            M.layer_directories(tar_rows([('../app', tarfile.DIRTYPE, 0o755, 0, 0, b''), *self.rows()]), [0o755, 0, 0])

    def test_duplicate_directory_refuses(self):
        with self.assertRaisesRegex(RuntimeError, 'directory_layer_outside_scope'):
            M.layer_directories(tar_rows([*self.rows(), self.rows()[0]]), [0o755, 0, 0])

    def test_missing_directory_refuses(self):
        with self.assertRaisesRegex(RuntimeError, 'directory_layer_incomplete'):
            M.layer_directories(tar_rows(self.rows()[:-1]), [0o755, 0, 0])

    def test_nonroot_owner_and_wrong_modes_refuse(self):
        for mode, uid, gid in ((0o777, 0, 0), (0o755, 1, 0), (0o755, 0, 1)):
            rows = self.rows()
            rows[0] = (rows[0][0], tarfile.DIRTYPE, mode, uid, gid, b'')
            with self.subTest(mode=mode, uid=uid, gid=gid), self.assertRaisesRegex(RuntimeError, 'directory_layer_mode'):
                M.layer_directories(tar_rows(rows), [0o755, 0, 0])

    def test_nonzero_trailing_payload_refuses(self):
        raw = tar_rows(self.rows()).getvalue() + b'X' * 512
        with self.assertRaisesRegex(RuntimeError, 'directory_layer_trailer'):
            M.layer_directories(io.BytesIO(raw), [0o755, 0, 0])

    def test_missing_tar_trailer_refuses(self):
        raw = tar_rows(self.rows()).getvalue()[:1536]
        with self.assertRaisesRegex(RuntimeError, 'directory_layer_short'):
            M.layer_directories(io.BytesIO(raw), [0o755, 0, 0])

    def test_gzip_same_directory_layer_passes(self):
        import gzip
        result = M.layer_directories(io.BytesIO(gzip.compress(tar_rows(self.rows()).getvalue())), [0o755, 0, 0])
        self.assertEqual(result['entries'], 3)

    def archive(self, *, extra=False, wrong_diff=False, bad_trailer=False):
        layer = tar_rows(self.rows()).getvalue()
        config = {'architecture': 'amd64', 'os': 'linux', 'created': '2026-10-25T01:00:00Z', 'history': [],
                  'rootfs': {'type': 'layers', 'diff_ids': ['sha256:' + ('0' * 64 if wrong_diff else sha256(layer).hexdigest())]},
                  'config': {'Cmd': ['server'], 'Env': ['PUBLIC_TEST=1'], 'Labels': {'parent': 'fixture'}, 'WorkingDir': '/app'}}
        raw = json.dumps(config, separators=(',', ':')).encode()
        image = 'sha256:' + sha256(raw).hexdigest()
        manifest = json.dumps([{'Config': image[7:] + '.json', 'Layers': ['fixture/layer.tar'], 'RepoTags': None}]).encode()
        rows = [(image[7:] + '.json', tarfile.REGTYPE, 0o600, 0, 0, raw),
                ('fixture/layer.tar', tarfile.REGTYPE, 0o600, 0, 0, layer),
                ('manifest.json', tarfile.REGTYPE, 0o600, 0, 0, manifest)]
        if extra: rows.append(('unaccounted', tarfile.REGTYPE, 0o600, 0, 0, b'x'))
        result = tar_rows(rows).getvalue()
        if bad_trailer: result += b'X' * 512
        return io.BytesIO(result), len(result), image

    def test_complete_classic_graph_and_physical_directory_layer_pass(self):
        stream, size, image = self.archive()
        result = M.validate_archive(stream, size, image, last_layer=[0o755, 0, 0])
        self.assertTrue(result['physical_graph_closed'])
        self.assertEqual(result['new_layer']['new_directories'], list(M.DIRECTORIES))
        self.assertEqual(result['layers'], 1)

    def test_unknown_archive_blob_refuses(self):
        stream, size, image = self.archive(extra=True)
        with self.assertRaisesRegex(RuntimeError, 'archive_closed_inventory'):
            M.validate_archive(stream, size, image)

    def test_changed_layer_content_refuses_diffid(self):
        stream, size, image = self.archive(wrong_diff=True)
        with self.assertRaisesRegex(RuntimeError, 'archive_layer_diff_id'):
            M.validate_archive(stream, size, image)

    def test_whole_archive_nonzero_trailer_refuses(self):
        stream, size, image = self.archive(bad_trailer=True)
        with self.assertRaisesRegex(RuntimeError, 'archive_trailing_payload'):
            M.validate_archive(stream, size, image)

    def test_wrong_config_identity_refuses_before_any_execution(self):
        stream, size, _ = self.archive()
        with self.assertRaisesRegex(RuntimeError, 'archive_config_identity'):
            M.validate_archive(stream, size, 'sha256:' + '0' * 64)

    def test_duplicate_json_and_nonfinite_numbers_refuse(self):
        for raw in (b'{"x":1,"x":2}', b'{"x":NaN}', b'{"x":Infinity}'):
            with self.subTest(raw=raw), self.assertRaises(RuntimeError):
                M.document(raw)

    def base(self):
        return {'Id': M.PARENT, 'Config': {'Env': ['PUBLIC_TEST=1'], 'Cmd': ['server'], 'User': '',
                    'Labels': {'ancestry': 'preserved'}}, 'RootFS': {'Layers': ['sha256:' + str(i).zfill(64) for i in range(32)]}}

    def pair(self):
        before = self.base()
        after = deepcopy(before)
        after['Id'] = 'sha256:' + 'b' * 64
        after['Config'] = M.expected_config(before['Config'], 'c' * 40, 'd' * 64)
        after['RootFS']['Layers'].append('sha256:' + 'e' * 64)
        return before, after

    def test_only_four_closed_label_additions_pass(self):
        before, after = self.pair()
        M.compare_images(before, after, 'c' * 40, 'd' * 64)
        self.assertEqual(len(after['Config']['Labels']), len(before['Config']['Labels']) + 4)
        self.assertEqual(before, self.base())

    def test_every_nonlabel_runtime_change_refuses(self):
        for key, value in (('Env', ['PUBLIC_TEST=2']), ('Cmd', ['other']), ('User', '1'), ('Entrypoint', ['new']), ('Volumes', {})):
            before, after = self.pair()
            after['Config'][key] = value
            with self.subTest(key=key), self.assertRaisesRegex(RuntimeError, 'whole_runtime_config_changed'):
                M.compare_images(before, after, 'c' * 40, 'd' * 64)

    def test_label_collision_and_unexpected_added_label_refuse(self):
        before, after = self.pair()
        before['Config']['Labels'][M.LABEL_PREFIX + 'status'] = 'old'
        with self.assertRaisesRegex(RuntimeError, 'parent_label_collision'):
            M.expected_config(before['Config'], 'c' * 40, 'd' * 64)
        before, after = self.pair()
        after['Config']['Labels']['unreviewed'] = 'addition'
        with self.assertRaisesRegex(RuntimeError, 'whole_runtime_config_changed'):
            M.compare_images(before, after, 'c' * 40, 'd' * 64)

    def test_changed_prefix_missing_or_two_new_layers_refuse(self):
        for action in ('prefix', 'missing', 'two'):
            before, after = self.pair()
            if action == 'prefix': after['RootFS']['Layers'][1] = 'sha256:' + 'f' * 64
            elif action == 'missing': after['RootFS']['Layers'].pop()
            else: after['RootFS']['Layers'].append('sha256:' + 'f' * 64)
            with self.subTest(action=action), self.assertRaisesRegex(RuntimeError, 'exact_layer_prefix_changed'):
                M.compare_images(before, after, 'c' * 40, 'd' * 64)

    def files(self):
        before = {'app_root': [0o755, 0, 0], 'entries': {'/app/main.py': ['file', 0o600, 0, 0, 4, 'abcd'],
                  '/usr/local/bin/python3': ['link', 0o777, 0, 0, 'python3.11']}}
        after = deepcopy(before)
        after['entries'].update({'/' + name: ['dir', 0o755, 0, 0] for name in M.DIRECTORIES})
        return before, after

    def test_complete_existing_bytes_metadata_and_links_are_equal(self):
        result = M.compare_files(*self.files())
        self.assertTrue(result['complete_existing_entries_equal'])

    def test_existing_byte_mode_owner_link_and_extra_path_changes_refuse(self):
        for key in (1, 2, 3, 4, 5):
            before, after = self.files()
            after['entries']['/app/main.py'][key] = 'changed'
            with self.subTest(key=key), self.assertRaisesRegex(RuntimeError, 'effective_filesystem_changed'):
                M.compare_files(before, after)
        before, after = self.files()
        after['entries']['/app/extra'] = ['dir', 0o755, 0, 0]
        with self.assertRaisesRegex(RuntimeError, 'effective_filesystem_changed'):
            M.compare_files(before, after)

    def test_signature_binds_full_parent_recipe_and_exact_directory_metadata(self):
        identity = {'recipe_commit': 'a' * 40}
        value, result = M.signature(identity, self.base()['RootFS']['Layers'])
        self.assertEqual(result, sha256(M.encoded(value)).hexdigest())
        self.assertEqual(len(value['parent_layers']), 32)
        self.assertEqual(value['parent_config_id'], M.PARENT)
        self.assertEqual(value['dockerfile_sha256'], M.RECIPE_SHA)
        self.assertTrue(all(q['uid'] == q['gid'] == 0 and q['mode'] == '0755' for q in value['directories']))
        self.assertEqual(value['directories'][0]['children'], ['coin-inference', 'product-estimator'])
        self.assertFalse(value['directories'][0]['empty'])
        self.assertTrue(all(q['empty'] and q['children'] == [] for q in value['directories'][1:]))

    def test_actual_sealed_manifest_distinguishes_installed_sources_from_build_inputs(self):
        raw = (ROOT / 'tests/fixtures/security_app_mountpoint_payload.json').read_bytes()
        manifest = json.loads(raw)
        roots = {'api', 'bot', 'core', 'src', 'migrations', 'models', 'templates', 'fonts', 'scripts'}
        direct = {'requirements.txt', 'alembic.ini', 'main.py', 'manage.py', 'run_bot.py',
                  'schemas.py', 'seed_fake_data.py', 'trading_settings.json'}
        entries = {'/app/' + q['path']: ['file', 0o600, 0, 0, q['bytes'], q['sha256']]
                   for q in manifest['files'] if q['path'].split('/')[0] in roots or q['path'] in direct}
        common = type('PublicSourceIdentity', (), {'SOURCE': '19d4c2da778f8f1edc567db4c9616c2d153980c9',
                                                   'BASELINE': 'ba1f7616ade57558f51bb3a7fbb903243a33aab4'})
        with patch.object(M, 'COMMON', common):
            result = M.verify_payload(raw, {'entries': entries})
            self.assertEqual(result['sealed_build_input_files'], 1450)
            self.assertEqual(result['python_source_files_verified'], 857)
            self.assertEqual(result['python_source_bytes'], 13194027)
            self.assertGreater(result['build_inputs_not_installed'], 549)
            entries['/app/main.py'][-1] = 'changed'
            with self.assertRaisesRegex(RuntimeError, 'payload_file_mismatch'):
                M.verify_payload(raw, {'entries': entries})

    def test_entrypoints_refuse_nonhosted_before_any_command_or_write(self):
        with patch.dict(os.environ, {'GITHUB_ACTIONS': 'false'}):
            for call in (M.compose, M.finalize, M.cleanup):
                with self.subTest(call=call.__name__), self.assertRaisesRegex(RuntimeError, 'hosted_ci_only'):
                    call(Path('/deliberately-unavailable'))

    def test_literal_refusals_have_public_codes(self):
        tree = ast.parse((ROOT / 'scripts/compose_app_mountpoint_image.py').read_text())
        actual = {call.args[1].value for call in ast.walk(tree) if isinstance(call, ast.Call)
                  and isinstance(call.func, ast.Name) and call.func.id == 'require' and len(call.args) >= 2
                  and isinstance(call.args[1], ast.Constant) and isinstance(call.args[1].value, str)}
        self.assertFalse(actual - M.PUBLIC_CODES)

    def test_fixed_parent_artifacts_and_helpers_are_source_pinned(self):
        self.assertEqual(M.IMAGE_ARTIFACT[0], 12573279010)
        for name, expected in M.HELPERS.items():
            self.assertEqual(sha256((ROOT / 'scripts' / name).read_bytes()).hexdigest(), expected)
        self.assertEqual(sha256((ROOT / 'deploy/maintenance/app_mountpoint_image/Dockerfile').read_bytes()).hexdigest(), M.RECIPE_SHA)
        manifest = (ROOT / 'tests/fixtures/security_app_mountpoint_payload.json').read_bytes()
        self.assertEqual(len(manifest), 330066)
        self.assertEqual(sha256(manifest).hexdigest(), M.PAYLOAD_SHA)
        self.assertEqual(len(json.loads(manifest)['files']), 1450)

    def test_workflow_is_explicit_default_off_and_never_deploys(self):
        source = (ROOT / '.github/workflows/app-mountpoint-image.yml').read_text()
        self.assertIn('default: false', source)
        self.assertIn('inputs.build_candidate', source)
        self.assertIn('cancel-in-progress: false', source)
        self.assertIn("ignore-unfixed: 'false'", source)
        self.assertIn('UNKNOWN,LOW,MEDIUM,HIGH,CRITICAL', source)
        self.assertNotIn('ssh ', source)
        self.assertNotIn('docker login', source)
        self.assertNotIn('docker push', source)
        self.assertIn('gate --root', source)
        import re
        pin = re.search(r'(?m)^      CONTROLLER_SHA: ([a-f0-9]{64})$', source)
        self.assertIsNotNone(pin)
        self.assertEqual(pin.group(1), sha256((ROOT/'scripts/compose_app_mountpoint_image.py').read_bytes()).hexdigest())


def load_prior():
    path = ROOT / 'scripts/compose_market_security_os_candidate.py'
    spec = importlib.util.spec_from_file_location('_mountpoint_pure_prior', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class AppMountpointOrchestrationTests(unittest.TestCase):
    """Named public fixture daemon; outer native/network fence remains active."""
    base = AppMountpointImageTests.base
    pair = AppMountpointImageTests.pair
    def setUp(self):
        AppMountpointImageTests.setUp(self)
        self.temp = self.fence.enter_context(tempfile.TemporaryDirectory(prefix='mountpoint-public-fixture-'))
        self.root = Path(self.temp)
        self.root.chmod(0o700)
        self.calls = []
        self.before = self.base()
        self.after = self.pair()[1]
        self.commit, self.input_sha = 'c' * 40, None
        self.identity = {'recipe_commit': self.commit}
        _, signature = M.signature(self.identity, self.before['RootFS']['Layers'])
        self.input_sha = signature
        self.after['Config'] = M.expected_config(self.before['Config'], self.commit, signature)
        self.parent_tar = b'public parent archive fixture'
        self.parent = {'config_id': M.PARENT, 'config': {'config': self.before['Config'],
            'rootfs': {'type': 'layers', 'diff_ids': self.before['RootFS']['Layers']}},
            'layers': 32, 'archive_bytes': len(self.parent_tar), 'archive_sha256': sha256(self.parent_tar).hexdigest(),
            'oci_manifest_ids': [], 'physical_graph_closed': True}
        self.fixture_files = self.payload_files()
        self.common = SimpleNamespace(
            SOURCE='19d4c2da778f8f1edc567db4c9616c2d153980c9', BASELINE='ba1f7616ade57558f51bb3a7fbb903243a33aab4',
            AUDIT='named_package_inventory_fixture', NATIVE='named_native_fixture', FOCUSED='named_historical_fixture',
            EVIDENCE=(12553191740, 190111, 'fixture-test-evidence'), PROBE_SHA=M.HELPERS['verify_image_component_presence.py'],
            PUBLIC_FAILURE_CODES=frozenset({'command_failed'}), fetch_artifact=self.fetch,
            docker=self.docker, inspect_builder=self.builder, image_record=lambda raw, expected: json.loads(raw)[0],
            isolated=self.isolated, admit_test_proof=lambda path: b'{"named_test_source_proof":true}\n',
            fetch_tests=lambda path: self.calls.append(('fetch_tests', str(path))),
            validate_component=lambda raw, image: self.calls.append(('component_validation', image)),
            acl_consumer_probe=self.acl, export_image=self.export)
        @contextmanager
        def named_sealed_sources(root):
            self.assertEqual(root, self.root)
            yield self.identity
        self.fence.enter_context(patch.object(M, 'sources', named_sealed_sources))
        self.fence.enter_context(patch.object(M, 'COMMON', self.common))
        self.fence.enter_context(patch.object(M, 'COMPONENT', SimpleNamespace(in_container=self.component)))
        self.fence.enter_context(patch.object(M, 'ci', lambda root: self.assertEqual(root, self.root)))
        self.fence.enter_context(patch.object(M, 'admit_evidence', lambda path: {'scanner_config': {}, 'composition': {}}))
        self.fence.enter_context(patch.object(M, 'admit_parent', self.admit))
        self.fence.enter_context(patch.object(M, 'inspect_binding', self.binding))
        self.fence.enter_context(patch.object(M, 'TAR_BYTES', len(self.parent_tar)))
        self.fence.enter_context(patch.object(M, 'TAR_SHA', sha256(self.parent_tar).hexdigest()))
        self.fence.enter_context(patch.object(M.os, 'statvfs', return_value=SimpleNamespace(f_bavail=16*1024**3, f_frsize=1)))
        self.fence.enter_context(patch.dict(os.environ, {'GITHUB_SHA': self.commit, 'GITHUB_RUN_ID': '1234',
            'GITHUB_RUN_ATTEMPT': '1', 'GITHUB_OUTPUT': str(self.root / 'outputs')}))
        (self.root / 'source').mkdir(mode=0o700)
        M.write(self.root / 'source/Dockerfile', (ROOT / 'deploy/maintenance/app_mountpoint_image/Dockerfile').read_bytes())
        M.write(self.root / 'source/payload_manifest.json', (ROOT / 'tests/fixtures/security_app_mountpoint_payload.json').read_bytes())
        M.write(self.root / 'source/verify_image_component_presence.py', (ROOT / 'scripts/verify_image_component_presence.py').read_bytes())

    def payload_files(self):
        manifest = json.loads((ROOT / 'tests/fixtures/security_app_mountpoint_payload.json').read_bytes())
        roots = {'api', 'bot', 'core', 'src', 'migrations', 'models', 'templates', 'fonts', 'scripts'}
        direct = {'requirements.txt', 'alembic.ini', 'main.py', 'manage.py', 'run_bot.py', 'schemas.py', 'seed_fake_data.py', 'trading_settings.json'}
        rows = {'/app/' + row['path']: ['file', 0o600, 0, 0, row['bytes'], row['sha256']]
                for row in manifest['files'] if row['path'].split('/')[0] in roots or row['path'] in direct}
        rows['/usr/local/bin/python3'] = ['link', 0o777, 0, 0, 'python3.11']
        return {'app_root': [0o755, 0, 0], 'entries': rows}

    def fetch(self, artifact, target, size, digest):
        self.calls.append(('fetch', artifact))
        M.write(target, b'named bounded fixed public artifact')

    def admit(self, path, root, evidence):
        self.calls.append(('parent_admission', M.PARENT))
        M.write(root / 'parent.tar', self.parent_tar)
        return deepcopy(self.parent)

    def binding(self, root, operand, proof):
        self.calls.append(('binding', operand))
        return {**deepcopy(self.before), 'RepoTags': ['security-app-mountpoints-1234-1:ci']}

    def docker(self, root, argv, **kwargs):
        self.calls.append(('docker', tuple(argv)))
        if argv[0] == 'build':
            self.assertIn('--pull=false', argv)
            self.assertIn('--network=none', argv)
            self.assertIn('APP_SECURITY_PARENT=security-app-mountpoints-1234-1:ci', argv)
            self.assertNotIn('push', argv)
            M.write(root / 'candidate.id', self.after['Id'].encode())
            M.write(root / 'reports/build.log', b'named offline build fixture')
            return b''
        if argv[:2] == ['image', 'inspect']:
            return json.dumps([self.after]).encode()
        if argv[:2] == ['image', 'load']:
            self.assertEqual(kwargs['input_stream'].read(), self.parent_tar)
        if argv[:2] == ['image', 'ls']:
            return b''  # Last-tag fixture: parent image record may disappear.
        return b''

    def builder(self, root):
        self.calls.append(('builder',))
        M.write(root / 'reports/builder-inspect.log', b'named local docker builder fixture')

    def isolated(self, root, image, program, purpose, **kwargs):
        self.calls.append(('isolated', purpose, image))
        if program == self.common.AUDIT:
            result = {'inventory': {'libc6': 'same'}, 'libc_sha256': 'same', 'bytes': 100,
                      'files': 13 if purpose == 'after' else 10, 'app_and_python_sha256': purpose + '-honestly-different'}
        elif program == M.FILES:
            result = deepcopy(self.fixture_files)
            if purpose == 'files-after':
                result['entries'].update({'/' + path: ['dir', 0o755, 0, 0] for path in M.DIRECTORIES})
        elif program == self.common.FOCUSED:
            self.assertEqual(image, self.after['Id'])
            self.assertEqual(kwargs['mounts'][0][1], '/app/tests')
            result = {'image_id': image, 'tests': 56, 'modules': 5, 'status': 'passed'}
        else:
            self.assertEqual(program, self.common.NATIVE)
            result = {'native': 'named passed fixture'}
        raw = M.encoded(result)
        M.write(root / 'reports' / (purpose + '-run.log'), raw)
        return raw

    def component(self, image, source, raw, digest):
        self.calls.append(('component', image))
        self.assertEqual(image, self.after['Id'])
        return M.encoded({'named_component_fixture': True})

    def acl(self, root, image, commit):
        self.calls.append(('acl', image))
        self.assertEqual(image, self.after['Id'])
        self.assertEqual(commit, self.commit)
        M.write(root / 'reports/acl-consumer-presence.json', b'{"named_acl_fixture":true}\n')
        M.write(root / 'reports/acl-run.log', b'{"named_acl_fixture":true}\n')

    def export(self, root, image):
        import gzip
        self.calls.append(('export', image))
        self.assertEqual(image, self.after['Id'])
        body = b'named child export physical parser tested independently'
        raw = gzip.compress(body)
        M.write(root / 'candidate-image.tar.gz', raw)
        return {'name': 'candidate-image.tar.gz', 'format': 'docker-archive+gzip', 'size_bytes': len(raw),
                'sha256': sha256(raw).hexdigest(), 'uncompressed_bytes': len(body)}

    def archive_check(self, stream, size, image, scanner=None, **kwargs):
        self.calls.append(('physical_archive', image))
        if image == M.PARENT:
            return deepcopy(self.parent)
        self.assertEqual(image, self.after['Id'])
        self.assertEqual(kwargs['last_layer'], [0o755, 0, 0])
        return {'config_id': image, 'config': {'config': self.after['Config'], 'rootfs': {'diff_ids': self.after['RootFS']['Layers']}},
                'layers': 33, 'new_layer': {'new_directories': list(M.DIRECTORIES)}, 'physical_graph_closed': True}

    def scan_fixtures(self):
        image = self.after['Id']
        raw = {'SchemaVersion': 2, 'ArtifactType': 'container_image', 'Metadata': {'ImageID': image, 'ImageConfig': {}},
               'Results': [{'Vulnerabilities': [{'Severity': 'HIGH'}, {'Severity': 'UNKNOWN'}]}]}
        sbom = {'metadata': {'component': {'type': 'container', 'name': image,
                    'properties': [{'name': 'aquasecurity:trivy:ImageID', 'value': image}]}}}
        (self.root / 'reports/vulnerabilities.json').write_bytes(M.encoded(raw))
        (self.root / 'reports/sbom.cdx.json').write_bytes(M.encoded(sbom))

    def test_named_daemon_contract_builds_tests_exports_then_releases_only_alias(self):
        M.compose(self.root)
        self.scan_fixtures()
        with patch.object(M, 'validate_archive', self.archive_check):
            M.finalize(self.root)
            M.cleanup(self.root)
        export = json.loads((self.root / 'image-export.json').read_bytes())
        self.assertFalse(export['security_accepted'])
        self.assertFalse(export['findings_waived'])
        self.assertTrue(export['physical_new_layer_verified'])
        self.assertEqual(export['raw_counts'], {'UNKNOWN': 1, 'LOW': 0, 'MEDIUM': 0, 'HIGH': 1, 'CRITICAL': 0})
        self.assertEqual(export['historical_tests'], 56)
        record = json.loads((self.root / 'reports/composition.json').read_bytes())
        self.assertNotIn('Config', json.dumps(record))
        self.assertNotIn('PUBLIC_TEST', json.dumps(record))
        events = [row[0] for row in self.calls]
        self.assertLess(events.index('parent_admission'), events.index('builder'))
        self.assertLess(events.index('component'), events.index('export'))
        self.assertLess(events.index('acl'), events.index('export'))
        self.assertEqual([row for row in self.calls if row[0] == 'docker' and row[1][:2] == ('image', 'rm')],
                         [('docker', ('image', 'rm', '--no-prune', 'security-app-mountpoints-1234-1:ci'))])
        self.assertEqual(json.loads((self.root / 'reports/base-alias-cleanup.json').read_bytes())['status'], 'removed_exact_owned_alias')

    def test_bad_parent_admission_prevents_build_and_export(self):
        with patch.object(M, 'admit_parent', side_effect=RuntimeError('parent_archive_identity')):
            with self.assertRaisesRegex(RuntimeError, 'parent_archive_identity'):
                M.compose(self.root)
        self.assertFalse(any(row[0] == 'docker' and row[1][0] == 'build' for row in self.calls))
        self.assertFalse((self.root / 'image-export.json').exists())

    def test_physical_export_scope_failure_never_publishes_export_receipt(self):
        M.compose(self.root)
        self.scan_fixtures()
        def fail_child(stream, size, image, scanner=None, **kwargs):
            if image == M.PARENT: return deepcopy(self.parent)
            raise RuntimeError('directory_layer_outside_scope')
        with patch.object(M, 'validate_archive', fail_child), self.assertRaisesRegex(RuntimeError, 'directory_layer_outside_scope'):
            M.finalize(self.root)
        self.assertFalse((self.root / 'image-export.json').exists())

    def test_cleanup_missing_plan_is_noop_only_before_any_composition(self):
        (self.root / 'reports').mkdir(mode=0o700)
        M.cleanup(self.root)
        self.assertFalse(self.calls)
        M.write(self.root / 'reports/composition.json', b'{"named_fixture":true}\n')
        with self.assertRaisesRegex(RuntimeError, 'alias_plan_missing'):
            M.cleanup(self.root)

    def test_raw_high_gate_remains_failed_and_cli_failure_is_public(self):
        M.write(self.root / 'image-export.json', M.encoded({'schema': 'app_mountpoint_image_export/1',
                    'security_accepted': False, 'raw_counts': {'HIGH': 1, 'CRITICAL': 0}}))
        output = io.StringIO()
        with redirect_stderr(output):
            self.assertEqual(M.main(['gate', '--root', str(self.root)]), 1)
        self.assertEqual(json.loads(output.getvalue())['code'], 'raw_gate_failed')
        with patch.object(M, 'compose', side_effect=RuntimeError({'private_fixture': 'never emit'})), redirect_stderr(io.StringIO()) as out:
            self.assertEqual(M.main(['compose', '--root', str(self.root)]), 1)
        self.assertNotIn('private_fixture', out.getvalue())


class AppMountpointAdmissionTests(unittest.TestCase):
    """Real owned filesystem/archive fixtures, never host configuration/data."""
    def setUp(self):
        AppMountpointImageTests.setUp(self)
        self.root = Path(self.fence.enter_context(tempfile.TemporaryDirectory(prefix='mountpoint-admission-fixture-')))
        self.root.chmod(0o700)

    def test_hosted_authority_uses_fixed_runner_identity_and_private_root(self):
        target = self.root / 'security-app-mountpoints-owned'
        target.mkdir(mode=0o700)
        original = Path.stat
        def named_runner_stat(path, *args, **kwargs):
            if path == target: return SimpleNamespace(st_uid=1001, st_mode=stat.S_IFDIR | 0o700)
            return original(path, *args, **kwargs)
        previous = os.umask(0o077)
        self.addCleanup(os.umask, previous)
        with patch.object(M.os, 'geteuid', return_value=1001), patch.object(Path, 'stat', named_runner_stat), \
                patch.dict(os.environ, {'GITHUB_ACTIONS': 'true', 'RUNNER_ENVIRONMENT': 'github-hosted',
                    'GITHUB_REPOSITORY': 'mohsenbarari/trading_bot', 'RUNNER_TEMP': str(self.root),
                    'GITHUB_SHA': 'a' * 40, 'GITHUB_RUN_ID': '1234', 'GITHUB_RUN_ATTEMPT': '1'}):
            M.ci(target)
            with patch.dict(os.environ, {'GITHUB_SHA': 'abbreviated'}), self.assertRaisesRegex(RuntimeError, 'ci_identity'):
                M.ci(target)

    def test_all_sealed_sources_are_executed_from_first_verified_buffers(self):
        (self.root / 'source').mkdir(mode=0o700)
        files = {name: (ROOT / 'scripts' / name).read_bytes() for name in M.HELPERS}
        files['compose_app_mountpoint_image.py'] = (ROOT / 'scripts/compose_app_mountpoint_image.py').read_bytes()
        files['Dockerfile'] = (ROOT / 'deploy/maintenance/app_mountpoint_image/Dockerfile').read_bytes()
        files['payload_manifest.json'] = (ROOT / 'tests/fixtures/security_app_mountpoint_payload.json').read_bytes()
        records = {}
        for name, raw in files.items():
            M.write(self.root / 'source' / name, raw)
            records[name] = {'bytes': len(raw), 'sha256': sha256(raw).hexdigest()}
        identity = {'recipe_commit': 'a' * 40, 'files': records}
        M.write(self.root / 'source/identity.json', M.encoded(identity))
        with patch.dict(os.environ, {'GITHUB_SHA': 'a' * 40}), \
                patch.object(M, '__file__', str(self.root / 'source/compose_app_mountpoint_image.py')), \
                patch.object(M, 'COMMON', None), patch.object(M, 'PRIOR', None), \
                patch.object(M, 'LEGACY', None), patch.object(M, 'COMPONENT', None), patch.object(M, 'HELPER_CODES', frozenset()):
            with M.sources(self.root) as actual:
                self.assertEqual(actual, identity)
                self.assertIn('reuse_legacy_config_projection', M.HELPER_CODES)
                self.assertEqual(M.COMMON.SOURCE, '19d4c2da778f8f1edc567db4c9616c2d153980c9')
                self.assertIsNotNone(M.PRIOR)

    def evidence_fixture(self):
        layers = ['sha256:' + str(i).zfill(64) for i in range(32)]
        composition = {'image_id': M.PARENT, 'recipe': {'recipe_commit': M.PARENT_RECIPE}, 'security_accepted': False,
                       'deployment_performed': False, 'app_and_python_unchanged': True, 'runtime_config_unchanged': True,
                       'candidate_layers': layers, 'evidence_sha256': {'bound.json': sha256(b'bound').hexdigest()}}
        report = {'Metadata': {'ImageID': M.PARENT, 'ImageConfig': {'rootfs': {'diff_ids': layers}}}}
        rows = {'reports/composition.json': M.encoded(composition), 'reports/vulnerabilities.json': M.encoded(report),
                'reports/sbom.cdx.json': b'{"public_sbom_fixture":true}', 'reports/bound.json': b'bound',
                'reports/base-alias-cleanup.json': b'{"status":"removed_owned_alias"}'}
        rows.update({'padding-' + str(i): b'x' for i in range(16)})
        def archive():
            value = io.BytesIO()
            with ZipFile(value, 'w', ZIP_STORED) as output:
                for name, raw in rows.items(): output.writestr(name, raw)
            return value.getvalue()
        first = archive()
        rows['padding-0'] = b'x' * (248816 - len(first) + 1)
        raw = archive()
        self.assertEqual(len(raw), 248816)
        path = self.root / 'fixed-public-evidence.zip'
        M.write(path, raw)
        return path, rows, raw

    def test_bounded_evidence_zip_checks_every_bound_member_and_full_layer_list(self):
        path, rows, raw = self.evidence_fixture()
        with patch.object(M, 'EVIDENCE_SHA', sha256(raw).hexdigest()), \
                patch.object(M, 'COMPOSITION_SHA', sha256(rows['reports/composition.json']).hexdigest()), \
                patch.object(M, 'RAW_SHA', sha256(rows['reports/vulnerabilities.json']).hexdigest()), \
                patch.object(M, 'SBOM_SHA', sha256(rows['reports/sbom.cdx.json']).hexdigest()), \
                patch.object(M, 'LAYERS_SHA', sha256(json.dumps(json.loads(rows['reports/composition.json'])['candidate_layers'], separators=(',', ':')).encode()).hexdigest()):
            result = M.admit_evidence(path)
        self.assertEqual(len(result['composition']['candidate_layers']), 32)
        with self.assertRaisesRegex(RuntimeError, 'parent_evidence_zip'):
            M.admit_evidence(path)

    def test_parent_zip_bound_export_and_gzip_are_read_and_verified_on_same_owned_fds(self):
        import gzip
        content = b'bounded public parent tar fixture'
        compressed = gzip.compress(content)
        export = {'schema': 'security_os_composition_image_export/1', 'status': 'exported_not_accepted', 'image_id': M.PARENT,
                  'composition_sha256': M.COMPOSITION_SHA, 'reports_sha256': {'vulnerabilities.json': M.RAW_SHA, 'sbom.cdx.json': M.SBOM_SHA},
                  'evidence_sha256': {'proof': 'same'}, 'security_accepted': False, 'deployment_performed': False,
                  'archive': {'name': 'candidate-image.tar.gz', 'format': 'docker-archive+gzip', 'size_bytes': len(compressed),
                              'sha256': sha256(compressed).hexdigest(), 'uncompressed_bytes': len(content)}}
        raw = io.BytesIO()
        with ZipFile(raw, 'w', ZIP_STORED) as archive:
            archive.writestr('candidate-image.tar.gz', compressed)
            archive.writestr('image-export.json', M.encoded(export))
        path = self.root / 'fixed-public-parent.zip'
        M.write(path, raw.getvalue())
        proof = {'layers': 32, 'archive_bytes': len(content), 'archive_sha256': sha256(content).hexdigest()}
        def parser(stream, size, image, scanner):
            self.assertEqual(stream.read(), content)
            self.assertEqual(image, M.PARENT)
            return proof
        with patch.object(M, 'IMAGE_ARTIFACT', (99, len(raw.getvalue()), sha256(raw.getvalue()).hexdigest())), \
                patch.object(M, 'TAR_BYTES', len(content)), patch.object(M, 'TAR_SHA', sha256(content).hexdigest()), \
                patch.object(M, 'validate_archive', parser):
            result = M.admit_parent(path, self.root, {'composition': {'evidence_sha256': {'proof': 'same'}}, 'scanner_config': {}})
        self.assertEqual(result, proof)
        self.assertEqual((self.root / 'parent.tar').read_bytes(), content)

    def test_typed_daemon_binding_accepts_measured_manifest_but_rejects_config_drift(self):
        config = {'Env': ['PUBLIC_FIXTURE=1'], 'Labels': {'parent': 'unchanged'}}
        proof = {'config_id': M.PARENT, 'oci_manifest_ids': ['sha256:' + 'f' * 64],
                 'config': {'config': config, 'rootfs': {'diff_ids': ['sha256:' + 'd' * 64]}}}
        row = {'Id': proof['oci_manifest_ids'][0], 'Config': deepcopy(config), 'Os': 'linux', 'Architecture': 'amd64',
               'RootFS': {'Type': 'layers', 'Layers': proof['config']['rootfs']['diff_ids']}}
        with patch.object(M, 'COMMON', SimpleNamespace(docker=lambda *a, **k: M.encoded([row]))):
            actual = M.inspect_binding(self.root, 'owned-alias', proof)
            self.assertEqual(actual['Id'], proof['oci_manifest_ids'][0])
            row['Config']['Env'] = ['PUBLIC_FIXTURE=2']
            with self.assertRaisesRegex(RuntimeError, 'daemon_config_identity'):
                M.inspect_binding(self.root, 'owned-alias', proof)

    def test_success_cli_returns_zero_without_mutating_any_real_host(self):
        with patch.object(M, 'cleanup', return_value=None) as cleanup:
            self.assertEqual(M.main(['cleanup', '--root', str(self.root)]), 0)
        cleanup.assert_called_once_with(self.root)

    def structured_archive(self, *, oci=True, depth=1, shared_references=1,
                           mismatched_reference=False, duplicate_physical_member=False):
        import gzip
        layer = tar_rows([(name, tarfile.DIRTYPE, 0o755, 0, 0, b'') for name in M.DIRECTORIES]).getvalue()
        config = {'architecture': 'amd64', 'os': 'linux', 'created': '2026-10-25T01:00:00Z', 'history': [],
                  'rootfs': {'type': 'layers', 'diff_ids': ['sha256:' + sha256(layer).hexdigest()] * shared_references},
                  'config': {'Cmd': ['server'], 'Env': ['PUBLIC_TEST=1'], 'Labels': {'parent': 'fixture'}, 'WorkingDir': '/app'}}
        if mismatched_reference:
            config['rootfs']['diff_ids'][1] = 'sha256:' + 'f' * 64
        raw_config = json.dumps(config, separators=(',', ':')).encode()
        image = 'sha256:' + sha256(raw_config).hexdigest()
        data = {}
        if oci:
            def blob(raw):
                h = sha256(raw).hexdigest()
                data['blobs/sha256/' + h] = raw
                return {'digest': 'sha256:' + h, 'size': len(raw)}
            config_descriptor = blob(raw_config)
            compressed = gzip.compress(layer)
            layer_descriptor = blob(compressed)
            value = {'schemaVersion': 2, 'config': config_descriptor,
                     'layers': [layer_descriptor] * shared_references}
            selected = blob(M.encoded(value))
            for _ in range(depth):
                selected = blob(M.encoded({'schemaVersion': 2, 'manifests': [selected]}))
            data['index.json'] = M.encoded({'schemaVersion': 2, 'manifests': [selected]})
            data['oci-layout'] = b'{"imageLayoutVersion":"1.0.0"}'
            config_name = 'blobs/sha256/' + image[7:]
            layers = ['blobs/sha256/' + layer_descriptor['digest'][7:]] * shared_references
            data.update(M.LEGACY.moby_legacy_configs(config))
        else:
            legacy = next(iter(M.LEGACY.moby_legacy_configs(config).values()))
            row = json.loads(legacy)
            prefix = row['id'] + '/'
            config_name = image[7:] + '.json'
            layers = [prefix + 'layer.tar']
            data.update({config_name: raw_config, layers[0]: layer, prefix + 'VERSION': b'1.0\n',
                         prefix + 'json': legacy, 'repositories': b'{"public-fixture":{}}'})
        data['manifest.json'] = M.encoded([{'Config': config_name, 'Layers': layers, 'RepoTags': None}])
        rows = [(name, tarfile.REGTYPE, 0o600, 0, 0, body) for name, body in data.items()]
        if duplicate_physical_member:
            rows.append(next(row for row in rows if row[0] == layers[0]))
        raw = tar_rows(rows).getvalue()
        return io.BytesIO(raw), len(raw), image, config

    def test_repeated_logical_oci_references_share_one_unique_physical_blob(self):
        stream, size, image, config = self.structured_archive(shared_references=3)
        result = M.validate_archive(stream, size, image, config, last_layer=[0o755, 0, 0])
        self.assertEqual(result['layers'], 3)
        self.assertEqual(len(set(result['config']['rootfs']['diff_ids'])), 1)
        self.assertEqual(len(result['oci_manifest_ids']), 1)
        self.assertTrue(result['physical_graph_closed'])
        self.assertEqual(result['new_layer']['new_directories'], list(M.DIRECTORIES))

    def test_repeated_oci_reference_must_match_each_ordered_config_diff_id(self):
        stream, size, image, config = self.structured_archive(shared_references=3, mismatched_reference=True)
        with self.assertRaisesRegex(RuntimeError, 'archive_layer_diff_id'):
            M.validate_archive(stream, size, image, config)

    def test_reused_oci_blob_does_not_allow_duplicate_physical_tar_members(self):
        stream, size, image, config = self.structured_archive(shared_references=3, duplicate_physical_member=True)
        with self.assertRaisesRegex(RuntimeError, 'archive_member'):
            M.validate_archive(stream, size, image, config)

    def test_nested_oci_graph_gzip_layer_and_exact_moby_compatibility_metadata_pass(self):
        stream, size, image, config = self.structured_archive()
        result = M.validate_archive(stream, size, image, config, last_layer=[0o755, 0, 0])
        self.assertEqual(result['layers'], 1)
        self.assertEqual(len(result['oci_manifest_ids']), 1)
        self.assertTrue(result['scanner_projection']['every_other_key_type_value_equal'])
        self.assertEqual(result['scanner_projection']['original_nanosecond_config_sha256'], image[7:])

    def test_excessively_deep_oci_index_refuses(self):
        stream, size, image, config = self.structured_archive(depth=5)
        with self.assertRaisesRegex(RuntimeError, 'oci_depth'):
            M.validate_archive(stream, size, image)

    def test_complete_classic_version_json_and_repositories_are_bound(self):
        stream, size, image, config = self.structured_archive(oci=False)
        result = M.validate_archive(stream, size, image, config)
        self.assertEqual(result['layers'], 1)
        self.assertEqual(result['oci_manifest_ids'], [])


if __name__ == '__main__':
    unittest.main()
