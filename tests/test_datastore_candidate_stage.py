"""Inert archive/FD/protocol fixtures; no ambient host or network execution."""
from contextlib import ExitStack, contextmanager
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import ast
import fcntl
import gzip
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import tarfile
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
TARGETS = ('subprocess.run', 'subprocess.Popen', 'os.system', 'os.kill', 'os.killpg',
           'socket.socket', 'socket.create_connection', 'socket.getaddrinfo', 'urllib.request.urlopen',
           'ctypes.CDLL', 'ctypes.PyDLL', 'signal.signal', 'signal.alarm', 'resource.setrlimit')


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


with ExitStack() as import_fence:
    for target in TARGETS: import_fence.enter_context(patch(target, side_effect=AssertionError('ambient_forbidden')))
    C = load('datastore_contract_fixture', ROOT/'deploy/maintenance/datastore_candidate_contract.py')
    W = load('datastore_stage_fixture', ROOT/'deploy/maintenance/datastore_candidate_stage.py')
    L = load('datastore_launcher_fixture', ROOT/'deploy/maintenance/datastore_candidate_launch.py')
    T = load('datastore_reader_fixture', ROOT/'deploy/maintenance/runtime_image_archive_transport.py')
    M = load('datastore_moby_fixture', ROOT/'scripts/admit_security_component_image_reuse.py')


class DatastoreCandidateTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack(); self.addCleanup(self.stack.close)
        for target in TARGETS: self.stack.enter_context(patch(target, side_effect=AssertionError('ambient_forbidden')))
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix='datastore-public-fixture-')))
        self.root.chmod(0o700)
        previous = os.umask(0o077); self.addCleanup(os.umask, previous)

    def private(self, path, raw):
        path.write_bytes(raw); path.chmod(0o600); return path

    def output(self, path, size):
        return C.Output(path, size, expected_uid=os.geteuid(), expected_gid=os.getegid())

    def tar(self, rows):
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode='w', format=tarfile.USTAR_FORMAT) as archive:
            for name, raw in rows:
                info = tarfile.TarInfo(name); info.size = len(raw); info.mode = 0o644
                archive.addfile(info, io.BytesIO(raw))
        return stream.getvalue()

    def archive(self, *, legacy=True, damage=None, metadata=None, inspect_metadata=None):
        bodies = [b'ordinary fixed source layer', b'ordinary second source layer']
        layers = ['sha256:' + sha256(v).hexdigest() for v in bodies]
        config = {'architecture': 'amd64', 'created': '2026-10-25T01:02:03.120000000Z',
            'history': [], 'os': 'linux', 'comment': 'ordinary builder comment',
            'rootfs': {'type': 'layers', 'diff_ids': layers},
            'config': {'Env': ['PUBLIC_FIXTURE=ordinary'], 'Cmd': ['postgres'],
                'Entrypoint': ['docker-entrypoint.sh'], 'Volumes': {'/ordinary': {}},
                'ExposedPorts': {'5432/tcp': {}}, 'WorkingDir': '', 'StopSignal': 'SIGINT'}}
        if metadata: metadata(config)
        config_raw = C.encode(config); config_id = 'sha256:' + sha256(config_raw).hexdigest()
        name = lambda raw: 'blobs/sha256/' + sha256(raw).hexdigest()
        if damage == 'diffid': bodies[0] = b'X' + bodies[0][1:]
        descriptors = [{'mediaType': 'application/vnd.oci.image.layer.v1.tar',
                        'size': len(b), 'digest': 'sha256:' + sha256(b).hexdigest()} for b in bodies]
        manifest_raw = C.encode({'schemaVersion': 2, 'mediaType': C.OCI_MANIFEST,
            'config': {'mediaType': 'application/vnd.oci.image.config.v1+json',
                       'digest': config_id, 'size': len(config_raw)}, 'layers': descriptors})
        manifest_id = 'sha256:' + sha256(manifest_raw).hexdigest()
        index = {'schemaVersion': 2, 'mediaType': C.OCI_INDEX,
                 'manifests': [{'mediaType': C.OCI_MANIFEST, 'digest': manifest_id, 'size': len(manifest_raw)}]}
        rows = [(name(config_raw), config_raw), (name(manifest_raw), manifest_raw),
            ('index.json', C.encode(index)), ('oci-layout', C.encode({'imageLayoutVersion': '1.0.0'})),
            ('manifest.json', C.encode([{'Config': name(config_raw), 'Layers': [name(b) for b in bodies], 'RepoTags': None}]))]
        rows.extend((name(b), b) for b in bodies)
        if legacy: rows.extend(C.legacy_configs(config, M).items())
        if damage == 'layer': rows = [(n, b'X' + b[1:] if n == name(bodies[0]) else b) for n, b in rows]
        if damage == 'legacy':
            target = next(iter(C.legacy_configs(config, M))); rows = [(n, b + b' ' if n == target else b) for n, b in rows]
        if damage == 'extra': rows.append(('blobs/sha256/' + 'e'*64, b'ordinary unexpected extra'))
        if damage == 'duplicate': rows.append(rows[0])
        data = {'spec': {'profile': 'postgres', 'config_id': config_id, 'layers': layers,
                        'archive': {'uncompressed_bytes': 0}},
                'image': {'Config': deepcopy(config['config']), 'Comment': config.get('comment', ''), 'Author': '', 'DockerVersion': ''}}
        if inspect_metadata: data['image'].update(inspect_metadata)
        raw = self.tar(rows); data['spec']['archive']['uncompressed_bytes'] = len(raw)
        return raw, data, config

    def operation(self, phase='stage-web'):
        now = datetime.now(timezone.utc)
        binding = {'paths': {'bot': '/ordinary/bot-control.json', 'web': '/ordinary/web-control.json'}, 'sha256': 'c'*64}
        return {'schema': 'datastore_candidate_operation/1', 'operation_id': W.OPERATION,
            'profile': 'postgres', 'phase': phase, 'nonce': 'a'*32,
            'issued_at_utc': (now - timedelta(seconds=1)).isoformat().replace('+00:00', 'Z'),
            'deadline_utc': (now + timedelta(seconds=60)).isoformat().replace('+00:00', 'Z'),
            'source_sha256': {**C.HELPERS, 'load_native8da.py': W.STREAM_HELPER_SHA,
                **{n: 'b'*64 for n in ('datastore_candidate_contract.py', 'datastore_candidate_stage.py', 'datastore_candidate_launch.py')}},
            'baselines': {k: {'path': '/ordinary/' + k + '.json', 'sha256': 'd'*64} for k in ('bot', 'web')},
            'evidence_path': '/ordinary/evidence.zip', 'source_nonce': None if phase == 'stage-web' else 'e'*32,
            'source_admission': None if phase == 'stage-web' else binding,
            'web_loaded': binding if phase == 'load-native' else None, 'fences': {},
            'security_accepted': False, 'activation_allowed': False}

    def test_real_public_export_fields_sparse_counters_and_actual_layer_fixtures(self):
        for profile, length, unknown in (('postgres', 12, 1), ('redis', 8, 0)):
            spec = C.PROFILES[profile]
            raw = (ROOT/'tests/fixtures/security_datastore_candidates'/(profile+'-export.json')).read_bytes()
            identity = C.document((ROOT/'tests/fixtures/security_datastore_candidates'/(profile+'-public-identity.json')).read_bytes())
            self.assertEqual(sha256(raw).hexdigest(), spec['export_sha256'])
            value = C.export_record(C.document(raw), spec)
            self.assertEqual(value['raw_counts'], {'UNKNOWN': 1} if unknown else {})
            self.assertEqual(identity['rootfs'], {'Type': 'layers', 'Layers': spec['layers']})
            self.assertEqual(len(spec['layers']), length)
            self.assertEqual(identity['image_id'], spec['config_id'])
            self.assertFalse(identity['config_values_included'])

    def test_counter_bool_unknown_negative_wrong_nonzero_and_missing_required_nonzero_refuse(self):
        spec = C.PROFILES['postgres']
        value = C.document((ROOT/'tests/fixtures/security_datastore_candidates/postgres-export.json').read_bytes())
        for wrong in ({'UNKNOWN': True}, {'UNKNOWN': -1}, {'UNKNOWN': 2}, {}, {'UNKNOWN': 1, 'other': 0}, {'HIGH': 1, 'UNKNOWN': 1}):
            changed = deepcopy(value); changed['raw_counts'] = wrong
            with self.subTest(wrong=wrong), self.assertRaises(C.Refusal): C.export_record(changed, spec)

    def test_unknown_export_field_wrong_image_and_false_flag_drift_refuse(self):
        spec = C.PROFILES['redis']; original = C.document((ROOT/'tests/fixtures/security_datastore_candidates/redis-export.json').read_bytes())
        for key, value in (('unexpected', 0), ('image_id', 'sha256:'+'0'*64), ('scanner_gate_waived', True), ('full_role_matrix', 'PASS')):
            changed = deepcopy(original); changed[key] = value
            with self.subTest(key=key), self.assertRaises(C.Refusal): C.export_record(changed, spec)

    def test_optional_api_defaults_absent_equals_expanded_but_types_are_strict(self):
        empty = C.container_config()
        self.assertEqual(C.container_config({}), empty)
        self.assertEqual(C.container_config({'ArgsEscaped': False, 'StopSignal': '', 'Labels': None}), empty)
        self.assertNotIn('ArgsEscaped', empty); self.assertNotIn('StopSignal', empty)
        self.assertIn('OnBuild', empty); self.assertIsNone(empty['OnBuild'])
        self.assertEqual(C.container_config({'StopTimeout': 0})['StopTimeout'], 0)
        for value in ({'ArgsEscaped': 0}, {'StopTimeout': False}, {'Healthcheck': {}}, {'unexpected': ''}):
            with self.subTest(value=value), self.assertRaises(C.Refusal): C.container_config(value)

    def test_moby_serializer_preserves_struct_order_comment_ports_volume_and_optional_signal(self):
        _, _, config = self.archive()
        value = C.container_config(config['config'])
        self.assertLess(list(value).index('ExposedPorts'), list(value).index('Tty'))
        self.assertLess(list(value).index('Volumes'), list(value).index('WorkingDir'))
        self.assertEqual(value['StopSignal'], 'SIGINT')
        rows = list(C.legacy_configs(config, M).values()); final = json.loads(rows[-1])
        self.assertEqual(final['comment'], 'ordinary builder comment')
        self.assertTrue(rows[-1].index(b'"comment"') < rows[-1].index(b'"created"'))
        self.assertIn(b'"OnBuild":null', rows[-1]); self.assertIn(b'"ExposedPorts":{"5432/tcp":{}}', rows[-1])
        self.assertEqual(final['config']['Volumes'], {'/ordinary': {}})

    def test_normal_oci_with_complete_moby_compatibility_is_source_proof_not_daemon_measurement(self):
        for legacy in (False, True):
            raw, data, _ = self.archive(legacy=legacy)
            proof = C.validate_archive(io.BytesIO(raw), len(raw), __import__('time').monotonic()+30, data, T, M)
            self.assertEqual(proof['config_id'], data['spec']['config_id'])
            self.assertEqual(proof['legacy_compatibility_configs'], 2 if legacy else 0)
            self.assertTrue(proof['physical_graph_closed']); self.assertFalse(proof['daemon_identity_measured'])
            self.assertFalse(proof['configuration_values_disclosed']); self.assertNotIn('Config', C.encode(proof).decode())

    def test_layer_content_legacy_body_extra_and_duplicate_members_refuse(self):
        for damage in ('layer', 'diffid', 'legacy', 'extra', 'duplicate'):
            raw, data, _ = self.archive(damage=damage)
            with self.subTest(damage=damage), self.assertRaises(Exception): C.validate_archive(io.BytesIO(raw), len(raw), __import__('time').monotonic()+30, data, T, M)

    def metadata_admission(self, mutate, expected):
        raw,data,config=self.archive(metadata=mutate,inspect_metadata=expected)
        proof=C.validate_archive(io.BytesIO(raw),len(raw),__import__('time').monotonic()+30,data,T,M)
        self.assertEqual(proof['config_id'],data['spec']['config_id']);self.assertTrue(proof['physical_graph_closed'])
        self.assertFalse(proof['security_accepted']);self.assertEqual(proof['rootfs_layers'],config['rootfs']['diff_ids'])
        return proof

    def test_nonempty_top_comment_wins_over_every_history_comment(self):
        def mutate(config):config['history']=[{'comment':'earlier'},{'comment':'last differs'}]
        self.metadata_admission(mutate,{'Comment':'ordinary builder comment'})
        raw,data,_=self.archive(metadata=mutate,inspect_metadata={'Comment':'last differs'})
        with self.assertRaisesRegex(C.Refusal,'archive_image_metadata'):C.validate_archive(io.BytesIO(raw),len(raw),__import__('time').monotonic()+30,data,T,M)

    def test_missing_and_empty_top_comment_use_only_the_last_history_comment(self):
        for missing in (True,False):
            def mutate(config):
                if missing:config.pop('comment')
                else:config['comment']=''
                config['history']=[{'comment':'earlier'},{'comment':'last exact'}]
            with self.subTest(missing=missing):
                self.metadata_admission(mutate,{'Comment':'last exact'})
                raw,data,_=self.archive(metadata=mutate,inspect_metadata={'Comment':'earlier'})
                with self.assertRaisesRegex(C.Refusal,'archive_image_metadata'):C.validate_archive(io.BytesIO(raw),len(raw),__import__('time').monotonic()+30,data,T,M)

    def test_empty_history_or_last_missing_comment_projects_empty_without_searching_backwards(self):
        for history in ([],[{}],[{'comment':'earlier'},{}],[{'comment':'earlier'},{'comment':''}]):
            def mutate(config):config.pop('comment');config['history']=deepcopy(history)
            with self.subTest(history=history):self.metadata_admission(mutate,{'Comment':''})
        self.assertEqual(C.expected_inspect_comment({}),'')

    def test_actual_public_postgres_shape_no_top_comment_25_history_comments(self):
        # Actual public artifact12579072479 / SHA5a56ff8c901fb7553b596cbab6d706360163e1de07b895a61a5baa4b964b361d:
        # OCI top comment absent; inspect Comment equals every one of25 history comments.
        # This owned tiny archive reproduces that shape; it is not the actual image or a raw-gate waiver.
        def mutate(config):config.pop('comment');config['history']=[{'comment':'buildkit.dockerfile.v0'} for _ in range(25)]
        self.metadata_admission(mutate,{'Comment':'buildkit.dockerfile.v0'})

    def test_comment_projection_rejects_malformed_history_and_comment_types(self):
        invalid=[None,False,0,{},'history', [None],[False],['row'],[{'comment':None}],[{'comment':False}],[{'comment':1}]]
        for history in invalid:
            with self.subTest(history=history),self.assertRaisesRegex(C.Refusal,'archive_image_metadata'):C.expected_inspect_comment({'comment':'nonempty','history':history})
        for comment in (None,False,0,[],{}):
            with self.subTest(comment=comment),self.assertRaisesRegex(C.Refusal,'archive_image_metadata'):C.expected_inspect_comment({'comment':comment,'history':[]})
        for config in (None,[],False):
            with self.assertRaisesRegex(C.Refusal,'archive_image_metadata'):C.expected_inspect_comment(config)

    def test_direct_author_docker_version_and_inspect_comment_types_remain_strict(self):
        for field,value in [('Author','drift'),('DockerVersion','drift'),('Comment',None),('Author',False),('DockerVersion',0)]:
            raw,data,_=self.archive(inspect_metadata={field:value})
            with self.subTest(field=field,value=value),self.assertRaisesRegex(C.Refusal,'archive_image_metadata'):C.validate_archive(io.BytesIO(raw),len(raw),__import__('time').monotonic()+30,data,T,M)
        def both_numeric(config):config['author']=1
        raw,data,_=self.archive(legacy=False,metadata=both_numeric,inspect_metadata={'Author':True})
        with self.assertRaisesRegex(C.Refusal,'archive_image_metadata'):C.validate_archive(io.BytesIO(raw),len(raw),__import__('time').monotonic()+30,data,T,M)

    def test_retained_fd_metadata_aba_and_preexisting_output_refuse_without_unlink(self):
        path = self.private(self.root/'public', b'ordinary')
        with self.assertRaises(C.Refusal), C.held(path, 100, expected_uid=os.geteuid(), expected_gid=os.getegid()) as (fd, _, check):
            other = self.private(self.root/'replacement', b'ordinary'); os.replace(other, path)
            with self.assertRaises(C.Refusal): check()
            self.assertEqual(os.pread(fd, 8, 0), b'ordinary')
        with self.assertRaises(FileExistsError): self.output(path, 100)
        self.assertEqual(path.read_bytes(), b'ordinary')

    def test_output_bound_and_growth_checked_before_next_write(self):
        out = self.output(self.root/'new', 8)
        try:
            out.write(b'ordinary'); out.finish()
            with self.assertRaises(C.Refusal): out.write(b'x')
            with out.path.open('ab') as f: f.write(b'x')
            with self.assertRaises(C.Refusal): out.check()
        finally: out.close()

    def test_operation_requires_closed_profile_fresh_deadline_and_no_future_authority(self):
        value = self.operation(); raw = C.encode(value)
        parsed, _ = W.control(raw, sha256(raw).hexdigest(), 'stage-web', 'bot', C)
        self.assertEqual(parsed['profile'], 'postgres')
        for key, replacement in (('profile', 'arbitrary'), ('activation_allowed', True), ('security_accepted', True), ('deadline_utc', '2020-01-01T00:00:00Z')):
            wrong = deepcopy(value); wrong[key] = replacement; raw = C.encode(wrong)
            with self.subTest(key=key), self.assertRaises(RuntimeError): W.control(raw, sha256(raw).hexdigest(), 'stage-web', 'bot', C)
        with self.assertRaises(RuntimeError): W.control(C.encode(value), sha256(C.encode(value)).hexdigest(), 'stream-web', 'web', C)

    def test_operation_cannot_exceed_fifteen_minutes_or_change_helper_pins(self):
        value = self.operation()
        value['deadline_utc'] = (datetime.now(timezone.utc)+timedelta(seconds=901)).isoformat().replace('+00:00', 'Z')
        raw = C.encode(value)
        with self.assertRaises(RuntimeError): W.control(raw, sha256(raw).hexdigest(), 'stage-web', 'bot', C)
        value = self.operation(); value['source_sha256']['load_native8da.py'] = '0'*64; raw = C.encode(value)
        with self.assertRaises(RuntimeError): W.control(raw, sha256(raw).hexdigest(), 'stage-web', 'bot', C)

    def test_source_bindings_have_explicit_per_site_paths_without_native_archive_path(self):
        value = self.operation('load-native'); raw = C.encode(value)
        W.control(raw, sha256(raw).hexdigest(), 'load-native', 'bot', C)
        self.assertEqual(set(value['source_admission']['paths']), {'bot', 'web'})
        wrong = deepcopy(value); wrong['source_admission'] = {'path': '/one/host/path', 'sha256': 'a'*64}; raw = C.encode(wrong)
        with self.assertRaises(RuntimeError): W.control(raw, sha256(raw).hexdigest(), 'load-native', 'bot', C)

    def test_classic_config_branch_never_requests_descriptor_and_api_absence_is_valid(self):
        spec = C.PROFILES['postgres']; proof = {'source_identities': {spec['config_id']: {'kind': 'Docker_config_identity'}}, 'archive_sha256': 'a'*64, 'archive_bytes': 4096}
        ctx = SimpleNamespace(C=C, spec=spec, data={'image': {'Config': {'Cmd': ['ordinary']}}}, baseline={'host': '65.109.216.187', 'port': 22})
        calls = []
        def observed(ctx, argv):
            calls.append(argv); self.assertNotIn('Descriptor', argv[-2])
            return C.encode({'id': spec['config_id'], 'os': 'linux', 'architecture': 'amd64',
                'rootfs': {'Type': 'layers', 'Layers': spec['layers']}, 'tags': None, 'config': {'Cmd': ['ordinary']}})
        with patch.object(W, 'command', side_effect=observed): leaf = W.observe_loaded(ctx, proof, spec['config_id'])
        self.assertEqual(len(calls), 1); self.assertEqual(leaf['binding']['kind'], 'Docker_config_identity')
        self.assertFalse(leaf['configuration_values_disclosed'])

    def test_manifest_identity_requires_guarded_matching_descriptor_and_missing_is_refusal(self):
        spec = C.PROFILES['redis']; actual = 'sha256:'+'d'*64
        proof = {'source_identities': {actual: {'kind': 'OCI_manifest_to_config', 'media_type': C.OCI_MANIFEST, 'size': 123}}, 'archive_sha256': 'e'*64, 'archive_bytes': 4096}
        ctx = SimpleNamespace(C=C, spec=spec, data={'image': {'Config': {}}}, baseline={'host': '65.109.220.59', 'port': 37067})
        for descriptor in (None, {'digest': actual, 'mediaType': C.OCI_MANIFEST, 'size': True}, {'digest': actual, 'mediaType': C.OCI_MANIFEST, 'size': 123}):
            def observed(ctx, argv):
                if 'Descriptor' in argv[-2]:
                    self.assertIn('index . "Descriptor"', argv[-2]); return C.encode(descriptor)
                return C.encode({'id': actual, 'os': 'linux', 'architecture': 'amd64', 'rootfs': {'Type': 'layers', 'Layers': spec['layers']}, 'tags': [], 'config': {}})
            with self.subTest(descriptor=descriptor), patch.object(W, 'command', side_effect=observed):
                if descriptor is not None and type(descriptor['size']) is int: W.observe_loaded(ctx, proof, actual)
                else:
                    with self.assertRaises(RuntimeError): W.observe_loaded(ctx, proof, actual)

    def test_public_receipt_cannot_include_configuration_or_environment(self):
        for value in ({'Config': {}}, {'nested': [{'Env': ['ordinary']}]}, {'environment': {}}):
            with self.assertRaises(RuntimeError): W.public(value)

    def test_exception_diagnostics_use_only_the_closed_code_catalog(self):
        self.assertEqual(C.error_code(RuntimeError('capacity_floor')), 'capacity_floor')
        self.assertEqual(C.error_code(RuntimeError('ordinary_private_value_never_echoed')), 'unexpected_failure')
        self.assertEqual(C.error_code(OSError('ordinary_private_value_never_echoed')), 'unexpected_failure')

    def test_exclusive_output_closes_its_fd_if_initial_metadata_read_fails(self):
        opened = []; original = os.open
        def record(*args, **kwargs):
            fd = original(*args, **kwargs); opened.append(fd); return fd
        with patch.object(C.os, 'open', side_effect=record), patch.object(C.os, 'fstat', side_effect=OSError('inert_metadata_failure')):
            with self.assertRaises(OSError): self.output(self.root/'output', 1024)
        self.assertEqual(len(opened), 1)
        with self.assertRaises(OSError): os.fstat(opened[0])

    def test_entry_bootstrap_and_sources_compile_without_any_ambient_action(self):
        ast.parse(W.BOOTSTRAP)
        for name in ('datastore_candidate_contract.py', 'datastore_candidate_stage.py', 'datastore_candidate_launch.py'):
            ast.parse((ROOT/'deploy/maintenance'/name).read_bytes())

    def test_real_zip_gzip_tar_pipeline_hashes_each_owned_fd_and_preserves_source(self):
        tar_raw, data, _ = self.archive()
        spec = deepcopy(C.PROFILES['postgres']); spec.update(data['spec'])
        compressed = gzip.compress(tar_raw, mtime=0)
        spec['archive'] = {'name': 'candidate-image.tar.gz', 'format': 'docker-archive+gzip',
            'size_bytes': len(compressed), 'sha256': sha256(compressed).hexdigest(), 'uncompressed_bytes': len(tar_raw)}
        exported = C.document((ROOT/'tests/fixtures/security_datastore_candidates/postgres-export.json').read_bytes())
        exported['image_id'] = spec['config_id']; exported['archive'] = spec['archive']
        receipt = C.encode(exported); spec['export_sha256'] = sha256(receipt).hexdigest(); spec['export_bytes'] = len(receipt)
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, 'w') as archive:
            archive.writestr('image-export.json', receipt); archive.writestr('candidate-image.tar.gz', compressed)
        raw = stream.getvalue(); spec['image_zip_bytes'] = len(raw); spec['image_zip_sha256'] = sha256(raw).hexdigest(); data['spec'] = spec
        path = self.private(self.root/'ordinary.zip', raw); output = self.root/'output'; output.mkdir(mode=0o700)
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW); factory = C.Output
        try:
            with patch.object(C, 'Output', side_effect=lambda path, maximum: factory(path, maximum,
                    expected_uid=os.geteuid(), expected_gid=os.getegid())):
                capacity = Mock()
                proof, binding = C.unpack_image(fd, len(raw), output, data, T, M, time.monotonic()+30, capacity)
            self.assertEqual(proof['archive_sha256'], sha256(tar_raw).hexdigest())
            self.assertEqual((output/'candidate-image.tar').read_bytes(), tar_raw)
            self.assertEqual(binding['identity'][1], (output/'candidate-image.tar').stat().st_ino)
            self.assertEqual(path.read_bytes(), raw); self.assertGreater(capacity.call_count, 1)
            self.assertEqual(os.fstat(fd).st_ino, path.stat().st_ino)
        finally: os.close(fd)

    def test_compressed_reader_checks_deadline_and_short_read_before_decoding(self):
        path = self.private(self.root/'ordinary-compressed', b'ordinary'); fd = os.open(path, os.O_RDONLY)
        try:
            reader = C.CompressedReader(fd, 8, time.monotonic()+30)
            self.assertEqual(reader.read(65536), b'ordinary'); self.assertEqual(reader.read(1), b'')
            expired = C.CompressedReader(fd, 8, time.monotonic()-1)
            with patch.object(C.os, 'pread', side_effect=AssertionError('read_before_deadline')):
                with self.assertRaises(C.Refusal): expired.read(1)
        finally: os.close(fd)

    def web_load_scenario(self, kind):
        import subprocess
        source_root = self.root/'web'; source_root.mkdir(mode=0o700)
        source_nonce = 'b'*32; folder = source_root/('postgres-'+source_nonce); folder.mkdir(mode=0o700)
        raw = b'ordinary retained artifact'; path = self.private(folder/'candidate-image.tar', raw)
        proof = {'archive_bytes': len(raw), 'archive_sha256': sha256(raw).hexdigest(), 'expanded_layer_bytes': len(raw),
                 'source_identities': {C.PROFILES['postgres']['config_id']: {'kind': 'Docker_config_identity'}}}
        admitted = {'archive_binding': proof, 'tar_binding': {'identity': list(C.identity(path.stat()))}}
        error_fd, output_fd = os.memfd_create('inert-error'), os.memfd_create('inert-output')
        self.addCleanup(os.close, error_fd); self.addCleanup(os.close, output_fd)
        ctx = SimpleNamespace(C=C, spec=C.PROFILES['postgres'], site='web', checks=[], end=time.monotonic()+30,
            op={'profile': 'postgres', 'nonce': 'a'*32, 'source_nonce': source_nonce,
                'source_admission': {'sha256': 'c'*64}}, pin='d'*64, baseline={'image_map': []},
            error_fd=error_fd, output_fd=output_fd, N=SimpleNamespace(stop=Mock(return_value=True)), load_requested=False)
        calls = []; child = SimpleNamespace(wait=Mock(return_value=0))
        if kind == 'timeout': child.wait.side_effect = subprocess.TimeoutExpired('ordinary-image-load', 1)
        def publish(ctx, name, value):
            W.checkpoint(ctx)
            calls.append(name)
            if kind == 'request_write' and name == 'load-request.json': raise OSError('inert_public_write_failure')
            return 'e'*64
        def spawn(ctx, argv, **kwargs):
            self.assertEqual(calls, ['load-request.json']); self.assertEqual(argv, [*W.DOCKER, 'image', 'load', '--quiet'])
            fd = kwargs['stdin']; self.assertEqual(os.fstat(fd).st_ino, path.stat().st_ino)
            self.assertEqual(os.pread(fd, len(raw)+1, 0), raw)
            if kind == 'source_aba':
                replacement = self.private(folder/'replacement', raw); os.replace(replacement, path)
            return child
        real_held = C.held
        def held(path, *args, **kwargs):
            return real_held(path, *args, **kwargs, expected_uid=os.geteuid(), expected_gid=os.getegid())
        with patch.dict(W.ROOTS, {'web': source_root}), patch.object(W, 'read_admission', return_value=admitted), \
             patch.object(W, 'observe', return_value={'public': 'unchanged'}), patch.object(W, 'image_map', return_value=[]), \
             patch.object(W, 'capacity'), patch.object(W, 'publish', side_effect=publish), \
             patch.object(W, 'spawn', side_effect=spawn) as invoked, patch.object(C, 'held', side_effect=held), \
             patch.object(W, 'measured', side_effect=lambda *args: (W.checkpoint(ctx) or ({'image_loaded': True}, {'public': 'unchanged'}))) as measured:
            if kind == 'success':
                result = W.load_web(ctx); self.assertEqual(result['status'], 'loaded_measured_not_accepted')
                self.assertEqual(invoked.call_count, 1); self.assertEqual(calls, ['load-request.json', 'loaded-site.json'])
            else:
                with self.assertRaises(Exception): W.load_web(ctx)
                self.assertNotIn('loaded-site.json', calls)
                self.assertEqual(invoked.call_count, 0 if kind == 'request_write' else 1)
                if kind in {'timeout', 'request_write'}: measured.assert_not_called()
        self.assertEqual(path.read_bytes(), raw)

    def test_web_load_uses_same_retained_fd_and_durable_request_before_exactly_one_rpc(self):
        self.web_load_scenario('success')

    def test_web_request_publication_failure_prevents_rpc(self):
        self.web_load_scenario('request_write')

    def test_web_client_timeout_is_unknown_without_measurement_or_retry(self):
        self.web_load_scenario('timeout')

    def test_web_source_path_aba_cannot_publish_a_loaded_result(self):
        self.web_load_scenario('source_aba')

    def launcher_scenario(self, kind):
        marker = self.private(self.root/'marker', b'ordinary immutable marker')
        source = self.private(self.root/'source', b''); reservation = self.root/'reservation'
        source_identity = C.identity(source.stat()); marker_identity = C.identity(marker.stat())
        attached = self.root/'attached'; attached.mkdir(mode=0o700)
        value = self.operation(); value['fences'] = {'marker': list(marker_identity), 'source_mutex': list(source_identity)}
        launcher = self.private(self.root/'datastore_candidate_launch.py', (ROOT/'deploy/maintenance/datastore_candidate_launch.py').read_bytes())
        value['source_sha256'][launcher.name] = sha256(launcher.read_bytes()).hexdigest()
        controls = self.root/'controls'; controls.mkdir(mode=0o700)
        raw = C.encode(value); path = self.private(controls/(value['nonce']+'.json'), raw)
        args = SimpleNamespace(control=str(path), control_sha256=sha256(raw).hexdigest(), apply=W.CONFIRM)
        fake = SimpleNamespace(control=W.control, OPERATION=W.OPERATION, ROOTS={'bot': attached},
            MARKER=marker, SOURCE_LOCK=source, RESERVATION=reservation)
        executed = []
        def execute(call, *, reservation_owned, fence_check, on_rpc):
            self.assertTrue(reservation_owned); fence_check(); executed.append(True)
            probe = os.open(source, os.O_RDWR | os.O_NOFOLLOW)
            try:
                with self.assertRaises(BlockingIOError): fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
            finally: os.close(probe)
            on_rpc()
            if kind == 'unknown': raise RuntimeError('client_outcome_unknown')
            if kind == 'foreign':
                other = self.private(self.root/'foreign', reservation.read_bytes()); os.replace(other, reservation)
            work = attached/('postgres-'+value['nonce']); work.mkdir(mode=0o700)
            return {'status': 'archive_admitted_not_loaded', 'receipt_sha256': 'f'*64, 'security_accepted': False}
        fake.execute = execute
        original = C.Output
        def output(p, limit, **kwargs):
            if kind == 'final_receipt' and Path(p).name == 'controller-result.json': raise OSError('inert_final_write_failure')
            result = original(p, limit, **kwargs)
            if kind == 'init_write' and Path(p) == reservation:
                result.write = Mock(side_effect=OSError('inert_initial_publication_failure'))
            if kind == 'reservation_busy' and Path(p) == reservation:
                fd = os.open(reservation, os.O_RDWR | os.O_NOFOLLOW)
                self.addCleanup(os.close, fd); fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return result
        def module(stack, p, pin, **kwargs): return (fake if p.name.endswith('_stage.py') else C), lambda: None
        locked = None
        if kind == 'busy':
            locked = os.open(source, os.O_RDWR); fcntl.flock(locked, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            with patch.object(L, 'HERE', self.root), patch.object(L, 'module', side_effect=module), patch.object(C, 'Output', side_effect=output):
                if kind in {'unknown', 'foreign', 'busy', 'init_write', 'reservation_busy'}:
                    with self.assertRaises(Exception): L.launch(args, expected_uid=os.geteuid(), expected_gid=os.getegid())
                else:
                    result, code = L.launch(args, expected_uid=os.geteuid(), expected_gid=os.getegid())
                    self.assertEqual(code, 1 if kind == 'final_receipt' else 0)
                    self.assertEqual(result['status'], 'known_outcome_final_receipt_pending' if code else 'archive_admitted_not_loaded')
                    self.assertTrue(result['source_mutex_preserved']); self.assertTrue(result['reservation_removed'])
        finally:
            if locked is not None: os.close(locked)
        self.assertEqual(C.identity(source.stat()), source_identity); self.assertEqual(source.read_bytes(), b'')
        self.assertEqual(C.identity(marker.stat()), marker_identity)
        self.assertEqual(reservation.exists(), kind in {'unknown', 'foreign', 'reservation_busy'})
        if kind == 'busy': self.assertFalse(executed)
        if kind == 'init_write': self.assertFalse(executed)
        if kind == 'reservation_busy': self.assertFalse(executed)
        if kind == 'foreign': self.assertEqual(reservation.stat().st_nlink, 1)

    def test_launcher_real_persistent_empty_source_lock_preserves_inode_and_cleans_only_reservation(self):
        self.launcher_scenario('success')

    def test_launcher_busy_source_refuses_before_reservation_or_worker(self):
        self.launcher_scenario('busy')

    def test_launcher_unknown_rpc_retains_reservation_without_replaying(self):
        self.launcher_scenario('unknown')

    def test_launcher_foreign_reservation_replacement_is_never_deleted(self):
        self.launcher_scenario('foreign')

    def test_launcher_final_receipt_failure_never_turns_verified_outcome_into_success(self):
        self.launcher_scenario('final_receipt')

    def test_launcher_initial_reservation_write_failure_cleans_only_owned_partial_inode(self):
        self.launcher_scenario('init_write')

    def test_launcher_reservation_lock_uncertainty_preserves_the_inode_without_any_rpc(self):
        self.launcher_scenario('reservation_busy')

    def native_stream_scenario(self, kind):
        raw = b'ordinary authenticated source stream'
        spec = deepcopy(C.PROFILES['postgres']); op = self.operation('load-native')
        proof = {'archive_bytes': len(raw), 'archive_sha256': sha256(raw).hexdigest(),
                 'expanded_layer_bytes': len(raw), 'source_identities': {spec['config_id']: {'kind': 'Docker_config_identity'}}}
        tar_identity = [2049, 123, 0o100600, 0, 0, 1, len(raw), 1, 2]
        admission = {'archive_binding': proof, 'tar_binding': {'identity': tar_identity}}
        web_leaf = {'host': '65.109.220.59', 'port': 37067, 'daemon_image_id': spec['config_id'],
            'image_loaded': True, 'stable_before_after': True, 'configuration_values_disclosed': False,
            'binding': {'schema': 'runtime_image_archive_binding/1', 'security_accepted': False}}
        known_web = {'schema': 'datastore_candidate_site_loaded/1', 'status': 'loaded_measured_not_accepted',
                     'profile': 'postgres', 'security_accepted': False, 'loaded_site': web_leaf}
        header = {'schema': 'datastore_candidate_source_stream/1', 'nonce': op['nonce'], 'profile': 'postgres',
            'operation_sha256': 'a'*64, 'archive_binding': proof, 'source_tar_identity': tar_identity,
            'web_loaded_site': web_leaf, 'web_loaded_receipt_sha256': op['web_loaded']['sha256'],
            'configuration_values_disclosed': False, 'security_accepted': False}
        footer = {'schema': 'datastore_candidate_source_complete/1', 'nonce': op['nonce'],
            'operation_sha256': 'a'*64, 'archive_sha256': proof['archive_sha256'], 'archive_bytes': len(raw),
            'source_tar_identity': tar_identity, 'source_fd_held_and_hash_verified': True,
            'web_public_profile_and_image_map_stable': True, 'configuration_values_disclosed': False,
            'security_accepted': False}
        if kind == 'header': header = {**header, 'profile': 'redis'}
        if kind == 'footer': footer = {**footer, 'archive_sha256': 'f'*64}
        if kind == 'boolean_footer': footer = {**footer, 'source_fd_held_and_hash_verified': 1}
        source_read, source_write = os.pipe(); load_read, load_write = os.pipe()
        self.addCleanup(os.close, source_write); self.addCleanup(os.close, load_read)
        source = SimpleNamespace(stdout=os.fdopen(source_read, 'rb', buffering=0), stdin=None,
            wait=Mock(return_value=1 if kind == 'source_exit' else 0))
        child = SimpleNamespace(stdin=os.fdopen(load_write, 'wb', buffering=0), stdout=None,
            wait=Mock(return_value=1 if kind == 'docker_exit' else 0))
        error_fd, output_fd = os.memfd_create('inert-native-error'), os.memfd_create('inert-native-output')
        self.addCleanup(os.close, error_fd); self.addCleanup(os.close, output_fd)
        writes = []; calls = []
        N = SimpleNamespace(public_daemon=Mock(return_value={'runtime_override_absence_proven': False}),
            read_frame=Mock(side_effect=[header, footer]), read_n=Mock(return_value=raw),
            read_n_or_eof=Mock(return_value=b''), write_all=Mock(side_effect=lambda fd, b, end, pulse: writes.append(b)),
            stop=Mock(return_value=True))
        ctx = SimpleNamespace(C=C, N=N, site='bot', op=op, spec=spec, pin='a'*64, checks=[],
            end=time.monotonic()+30, baseline={'image_map': []}, error_fd=error_fd, output_fd=output_fd,
            load_requested=False, work=self.root)
        def spawned(ctx, argv, **kwargs):
            if argv[:len(W.DOCKER)] == W.DOCKER:
                self.assertEqual(calls, ['load-request.json']); return child
            return source
        def publish(ctx, name, value): calls.append(name); W.public(value); return 'b'*64
        def read(path, maximum=C.MAX_JSON, pin=None):
            self.assertEqual(str(path), op['web_loaded']['paths']['bot']); self.assertEqual(pin, op['web_loaded']['sha256'])
            return C.encode(known_web)
        with patch.object(W, 'observe', return_value={'public': 'unchanged'}), patch.object(W, 'image_map', return_value=[]), \
             patch.object(W, 'read_admission', return_value=admission), patch.object(W, 'capacity'), \
             patch.object(W, 'reserve_native_load', return_value={'ordinary_root_fallback_fixture': True}), \
             patch.object(W, 'remote', return_value=['ordinary-reviewed-ssh-source']), patch.object(W, 'spawn', side_effect=spawned) as invoked, \
             patch.object(W, 'publish', side_effect=publish), patch.object(C, 'read', side_effect=read), \
             patch.object(W, 'measured', return_value=({'image_loaded': True}, {'public': 'unchanged'})) as measured:
            if kind == 'success':
                result = W.load_native(ctx)
                self.assertEqual(result['status'], 'both_sites_loaded_measured_not_accepted')
                self.assertEqual(calls, ['load-request.json', 'clients-observed.json', 'loaded-candidate.json'])
                self.assertEqual(writes, [raw]); self.assertEqual(invoked.call_count, 2)
            else:
                with self.assertRaises(Exception): W.load_native(ctx)
                measured.assert_not_called(); self.assertNotIn('loaded-candidate.json', calls)
                self.assertEqual(invoked.call_count, 1 if kind == 'header' else 2)
        self.assertTrue(source.stdout.closed); self.assertTrue(child.stdin.closed or kind == 'header')
        if not child.stdin.closed: child.stdin.close()
        self.assertFalse(any(p.suffix in {'.tar', '.zip', '.gz'} for p in self.root.iterdir()))

    def test_native_single_stream_hash_footer_reap_and_both_site_result(self):
        self.native_stream_scenario('success')

    def test_native_header_failure_never_starts_docker_load(self):
        self.native_stream_scenario('header')

    def test_native_wrong_or_boolean_footer_cannot_publish_success_or_retry(self):
        for kind in ('footer', 'boolean_footer'):
            with self.subTest(kind=kind): self.native_stream_scenario(kind)

    def test_native_source_or_docker_client_failure_never_measures_or_retries(self):
        for kind in ('source_exit', 'docker_exit'):
            with self.subTest(kind=kind): self.native_stream_scenario(kind)

    def test_remote_transport_retains_strict_ssh_and_bounded_private_scope(self):
        op = self.operation('load-native'); ctx = SimpleNamespace(op=op, pin='a'*64)
        argv = W.remote(ctx, 'stream-web'); self.assertEqual(argv[:len(W.SSH)], W.SSH)
        self.assertIn('StrictHostKeyChecking=yes', argv); self.assertIn('ForwardAgent=no', argv)
        self.assertIn('UpdateHostKeys=no', argv); self.assertIn('ControlMaster=no', argv)
        remote = __import__('shlex').split(argv[-1])
        for item in ('--property=CPUQuota=100%', '--property=MemoryMax=2G', '--property=MemorySwapMax=0', '--property=TasksMax=64'):
            self.assertIn(item, remote)
        for item in ('--property=RuntimeMaxSec=900s', '--property=TimeoutStopSec=5s',
                     '--property=KillMode=control-group', '--property=SendSIGKILL=yes'):
            self.assertIn(item, remote)
        self.assertIn(W.BOOTSTRAP, remote); self.assertNotIn('pull', remote)

    def test_capacity_failure_prevents_any_native_stream_or_load(self):
        ctx = SimpleNamespace(C=C, site='bot', N=SimpleNamespace(public_daemon=Mock(return_value={})),
            op=self.operation('load-native'), baseline={'image_map': []}, end=time.monotonic()+30,
            spec=C.PROFILES['postgres'])
        admission = {'archive_binding': {'archive_bytes': 1024, 'expanded_layer_bytes': 1024, 'source_identities': {}}}
        with patch.object(W, 'observe', return_value={}), patch.object(W, 'image_map', return_value=[]), \
             patch.object(W, 'read_admission', return_value=admission), \
             patch.object(W, 'capacity', side_effect=RuntimeError('capacity_floor')), \
             patch.object(W, 'root_temp_storage', return_value=[]), patch.object(W, 'spawn') as invoked:
            with self.assertRaisesRegex(RuntimeError, 'capacity_floor'): W.load_native(ctx)
        invoked.assert_not_called()

    def test_boolean_client_exit_cannot_be_success(self):
        out, err = os.memfd_create('inert-output'), os.memfd_create('inert-error')
        try:
            ctx = SimpleNamespace(N=SimpleNamespace(stop=Mock()), output_fd=out, error_fd=err,
                                  end=time.monotonic()+10)
            child = SimpleNamespace(wait=Mock(return_value=False))
            with self.assertRaisesRegex(RuntimeError, 'client_outcome_unknown'): W.wait_success(ctx, child)
        finally: os.close(out); os.close(err)



    @contextmanager
    def root_fallback_fixture(self):
        # Real owned directories/UID/GID; no production path or fabricated root UID.
        root = self.root/'ordinary-os-root'; root.mkdir(mode=0o700)
        temporary = root/'tmp'; temporary.mkdir(mode=0o1777); temporary.chmod(0o1777)
        original = W.root_temp_storage
        with patch.object(W, 'OS_ROOT', root), patch.object(W, 'OS_TMP', temporary), \
             patch.object(W, 'root_temp_storage', side_effect=lambda: original(
                 expected_uid=os.geteuid(), expected_gid=os.getegid())):
            yield root, temporary

    def root_free(self, amount):
        return SimpleNamespace(f_bavail=amount//4096, f_frsize=4096)

    def test_root_fallback_reserves_actual_admitted_archive_and_expanded_budget(self):
        with self.root_fallback_fixture():
            ctx = SimpleNamespace(site='bot', C=C)
            proof = {'archive_bytes': C.PROFILES['postgres']['archive']['uncompressed_bytes'],
                     'expanded_layer_bytes': 250*1024**2}
            with patch.object(W, 'capacity') as capacity:
                value = W.reserve_native_load(ctx, proof)
            budget = 2*proof['archive_bytes'] + 2*proof['expanded_layer_bytes']
            self.assertEqual(ctx.root_fallback_budget, budget)
            capacity.assert_called_once_with(ctx, budget+W.MARGIN)
            self.assertEqual(value['minimum_root_free_bytes'], W.ROOT_RESERVE+W.MARGIN+W.MAX_JSON+budget)
            self.assertFalse(value['runtime_override_absence_proven'])
            self.assertFalse(value['allocator_quota_asserted'])

    def test_root_just_above_old_floor_but_below_load_budget_refuses_before_rpc(self):
        ctx = SimpleNamespace(C=C, site='bot', N=SimpleNamespace(public_daemon=Mock(return_value={})),
            op=self.operation('load-native'), baseline={'image_map': []}, end=time.monotonic()+30,
            spec=C.PROFILES['postgres'])
        proof = {'archive_bytes': C.PROFILES['postgres']['archive']['uncompressed_bytes'],
                 'expanded_layer_bytes': 250*1024**2, 'source_identities': {}}
        free = W.ROOT_RESERVE+W.MARGIN+W.MAX_JSON+1024**2
        with self.root_fallback_fixture(), patch.object(W.os, 'statvfs', return_value=self.root_free(free)), \
             patch.object(W, 'observe', return_value={}), patch.object(W, 'image_map', return_value=[]), \
             patch.object(W, 'read_admission', return_value={'archive_binding': proof}), \
             patch.object(W, 'capacity', side_effect=lambda context, additional=0: W.root_capacity(context)), \
             patch.object(W, 'spawn') as invoked:
            with self.assertRaisesRegex(RuntimeError, 'native_root_capacity_pressure'): W.load_native(ctx)
        invoked.assert_not_called()

    def test_root_budget_is_retained_at_every_later_capacity_check(self):
        with self.root_fallback_fixture():
            ctx = SimpleNamespace(C=C, site='bot', root_fallback_budget=8*1024**2,
                                  root_fallback_storage=W.root_temp_storage())
            minimum = W.ROOT_RESERVE+W.MARGIN+W.MAX_JSON+ctx.root_fallback_budget
            with patch.object(W.os, 'statvfs', return_value=self.root_free(minimum)):
                self.assertEqual(W.root_capacity(ctx), minimum)
            with patch.object(W.os, 'statvfs', return_value=self.root_free(minimum-4096)):
                with self.assertRaisesRegex(RuntimeError, 'native_root_capacity_pressure'): W.root_capacity(ctx)
            self.assertEqual(ctx.root_fallback_budget, 8*1024**2)

    def test_root_tmp_inode_replacement_and_mode_drift_refuse(self):
        with self.root_fallback_fixture() as (root, temporary):
            ctx = SimpleNamespace(C=C, site='bot', root_fallback_budget=8*1024**2,
                                  root_fallback_storage=W.root_temp_storage())
            with patch.object(W.os, 'statvfs', return_value=self.root_free(8*1024**3)):
                temporary.rename(root/'former-tmp'); temporary.mkdir(mode=0o1777); temporary.chmod(0o1777)
                with self.assertRaisesRegex(RuntimeError, 'native_root_temp_changed'): W.root_capacity(ctx)
                temporary.chmod(0o755)
                with self.assertRaisesRegex(RuntimeError, 'native_root_temp_metadata'): W.root_capacity(ctx)

    def test_root_tmp_device_drift_refuses_using_scoped_owned_metadata_fixture(self):
        with self.root_fallback_fixture() as (_, temporary):
            ctx = SimpleNamespace(C=C, site='bot', root_fallback_budget=8*1024**2,
                                  root_fallback_storage=W.root_temp_storage())
            original = Path.lstat
            def metadata(path, *args, **kwargs):
                value = original(path, *args, **kwargs)
                if path == temporary:
                    return SimpleNamespace(st_dev=value.st_dev+1, st_ino=value.st_ino,
                        st_mode=value.st_mode, st_uid=value.st_uid, st_gid=value.st_gid)
                return value
            with patch.object(Path, 'lstat', metadata), \
                 patch.object(W.os, 'statvfs', return_value=self.root_free(8*1024**3)):
                with self.assertRaisesRegex(RuntimeError, 'native_root_temp_filesystem'): W.root_capacity(ctx)

    def test_native_wait_rechecks_root_budget_between_owned_client_waits(self):
        import subprocess
        out, err = os.memfd_create('ordinary-wait-output'), os.memfd_create('ordinary-wait-error')
        try:
            with self.root_fallback_fixture():
                ctx = SimpleNamespace(C=C, site='bot', root_fallback_budget=8*1024**2,
                    root_fallback_storage=W.root_temp_storage(), N=SimpleNamespace(stop=Mock()),
                    output_fd=out, error_fd=err, end=time.monotonic()+30)
                child = SimpleNamespace(wait=Mock(side_effect=[subprocess.TimeoutExpired('ordinary', .25), 0]))
                with patch.object(W.os, 'statvfs', return_value=self.root_free(8*1024**3)), \
                     patch.object(W, 'capacity', side_effect=lambda context: W.root_capacity(context)) as pulse:
                    W.wait_success(ctx, child)
                self.assertEqual(pulse.call_count, 2)
                self.assertTrue(all(call.kwargs['timeout']<=.25 for call in child.wait.call_args_list))
        finally: os.close(out); os.close(err)

    def test_native_wait_cannot_accept_client_after_root_pressure(self):
        out, err = os.memfd_create('ordinary-wait-output'), os.memfd_create('ordinary-wait-error')
        try:
            with self.root_fallback_fixture():
                ctx = SimpleNamespace(C=C, site='bot', root_fallback_budget=8*1024**2,
                    root_fallback_storage=W.root_temp_storage(), N=SimpleNamespace(stop=Mock()),
                    output_fd=out, error_fd=err, end=time.monotonic()+30)
                child = SimpleNamespace(wait=Mock(return_value=0))
                free = W.ROOT_RESERVE+W.MARGIN+W.MAX_JSON+4096
                with patch.object(W.os, 'statvfs', return_value=self.root_free(free)), \
                     patch.object(W, 'capacity', side_effect=lambda context: W.root_capacity(context)):
                    with self.assertRaisesRegex(RuntimeError, 'native_root_capacity_pressure'): W.wait_success(ctx, child)
                child.wait.assert_not_called()
        finally: os.close(out); os.close(err)

    def test_all_current_literal_refusal_codes_remain_in_closed_catalog(self):
        codes = set()
        for name in ('datastore_candidate_contract.py','datastore_candidate_stage.py','datastore_candidate_launch.py'):
            for node in ast.walk(ast.parse((ROOT/'deploy/maintenance'/name).read_bytes())):
                if not isinstance(node, ast.Call): continue
                target = node.func.id if isinstance(node.func, ast.Name) else node.func.attr if isinstance(node.func, ast.Attribute) else ''
                index = 1 if target in {'need','require'} else 0
                if target in {'need','require','RuntimeError','Refusal','CleanupUnknown'} and len(node.args)>index \
                   and isinstance(node.args[index], ast.Constant) and type(node.args[index].value) is str:
                    codes.add(node.args[index].value)
        self.assertTrue(codes<=C.PUBLIC_FAILURE_CODES, codes-C.PUBLIC_FAILURE_CODES)

if __name__ == '__main__': unittest.main()
