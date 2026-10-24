"""Public inert fixtures; ambient commands/network/native loading denied pre-import."""
from contextlib import ExitStack, redirect_stderr
from copy import deepcopy
from hashlib import sha1, sha256
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

FENCE = ExitStack()
for target in ('subprocess.Popen', 'subprocess.run', 'os.system', 'os.kill', 'os.killpg',
               'socket.socket', 'socket.create_connection', 'socket.getaddrinfo',
               'urllib.request.urlopen', 'urllib.request.OpenerDirector.open', 'ctypes.CDLL'):
    FENCE.enter_context(patch(target, side_effect=AssertionError('ambient_host_network_forbidden')))
ROOT = Path(__file__).resolve().parents[1]
def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module); return module
M = load('datastore_recipe', ROOT/'scripts/compose_alpine_datastore_security.py')
C = load('datastore_common', ROOT/'scripts/compose_security_os_candidate.py')
M.COMMON = C


class DatastoreTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack(); self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix='public-datastore-')))
        self.root.chmod(0o700)
        previous = os.umask(0o077); self.addCleanup(os.umask, previous)
        self.stack.enter_context(patch.dict(os.environ, {'GITHUB_RUN_ID': '123', 'GITHUB_RUN_ATTEMPT': '1',
            'GITHUB_SHA': 'a'*40, 'GITHUB_OUTPUT': str(self.root/'output')}))
        self.stack.enter_context(patch.object(M, 'COMMON', C))

    def write(self, path, raw):
        path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(raw); path.chmod(0o600)
        return path

    def registry(self, profile='postgres'):
        p = M.PROFILES[profile]
        return {'reference': p['reference'], 'top_digest': p['reference'].split('@')[1],
            'platform_manifest_digest': 'sha256:'+'d'*64, 'config_digest': p['config_id'], 'layer_descriptors': []}

    def image(self, profile='postgres', *, candidate=False):
        p = M.PROFILES[profile]
        return {'Id': 'sha256:'+'c'*64 if candidate else p['config_id'], 'Os': 'linux', 'Architecture': 'amd64',
            'RootFS': {'Type': 'layers', 'Layers': p['layers']+(['sha256:'+'e'*64] if candidate else [])},
            'RepoDigests': [p['reference']], 'Config': {'Volumes': {p['volume']: {}}, 'User': '',
                'Cmd': ['postgres'] if profile == 'postgres' else ['redis-server'],
                'Entrypoint': ['docker-entrypoint.sh'], 'Env': ['PUBLIC_DEFAULT=yes'], 'Labels': None}}

    def audit(self, profile='postgres', *, candidate=False):
        p = M.PROFILES[profile]
        files = {p['server']: p['server_sha256'], '/usr/local/bin/docker-entrypoint.sh': p['entrypoint_sha256'],
                 '/usr/local/lib/ordinary-support': '8'*64}
        if profile == 'postgres':
            files['/usr/local/bin/gosu'] = '7'*64 if candidate else p['gosu_sha256']
        return {'metadata': {'alpine': '3.24.1' if profile == 'postgres' else '3.21.7',
            'world': '1'*64, 'musl': '2'*64, 'repositories': '5'*64, 'server': p['version'],
            'gosu': ('1.19 (go1.26.10 on linux/amd64; gc)' if candidate else '1.19 (go1.24.6 on linux/amd64; gc)') if profile == 'postgres' else 'ABSENT'},
            'packages': {'musl': 'unchanged', 'ordinary': 'unchanged', **(p['updates'] if candidate else {n: 'old-version' for n in p['updates']})},
            'files': files, 'nodes': {name: '755:0:0:regular file' for name in files}, 'links': {},
            'keys': {'/etc/apk/keys/ordinary-vendor.rsa.pub': '4'*64}}

    def audit_raw(self, value):
        kinds = {'metadata': 'meta', 'packages': 'pkg', 'files': 'file', 'nodes': 'node', 'links': 'link', 'keys': 'key'}
        return ''.join(kinds[k]+'\t'+name+'\t'+v+'\n' for k, rows in value.items() for name, v in rows.items()).encode()

    def test_two_fixed_profiles_and_distinct_registry_identities(self):
        self.assertEqual(set(M.PROFILES), {'postgres', 'redis'})
        self.assertEqual([len(M.PROFILES[p]['layers']) for p in M.PROFILES], [11, 7])
        self.assertEqual(M.PROFILES['postgres']['version'], 'postgres (PostgreSQL) 15.19')
        self.assertNotIn('994e5b', repr(M.PROFILES)); self.assertNotIn('fe0737', repr(M.PROFILES))
        for p in M.PROFILES.values():
            self.assertNotEqual(p['reference'].split('@')[1], p['config_id'])
        self.assertEqual(M.GO[1], 66936659); self.assertEqual(len(M.GOSU_FILES), 5)

    def test_host_calls_refuse_before_loading_or_any_command(self):
        with patch.dict(os.environ, {'GITHUB_ACTIONS': 'false'}):
            for fn in (M.compose, M.finalize):
                with self.assertRaisesRegex(RuntimeError, 'github_ci_only'): fn(self.root, 'postgres')
            stderr = io.StringIO()
            with redirect_stderr(stderr):
                self.assertEqual(M.main(['compose', '--root', str(self.root), '--profile', 'postgres']), 1)
            self.assertEqual(json.loads(stderr.getvalue())['code'], 'github_ci_only')
            self.assertFalse((self.root/'reports').exists())

    def test_stable_source_read_and_aba_replacement(self):
        path = self.write(self.root/'source.py', b'ordinary public bytes')
        with M.sealed(path, 1024) as raw: self.assertEqual(raw, b'ordinary public bytes')
        with self.assertRaisesRegex(RuntimeError, 'sealed_changed'):
            with M.sealed(path, 1024):
                replacement = self.write(self.root/'replacement', b'ordinary public bytes')
                os.replace(replacement, path)
        with self.assertRaisesRegex(RuntimeError, 'sealed_metadata'):
            with M.sealed(path, 1): pass
        os.link(path, self.root/'alias')
        with self.assertRaisesRegex(RuntimeError, 'sealed_metadata'):
            with M.sealed(path, 1024): pass

    def test_source_manifest_compiles_only_exact_checked_common_buffer(self):
        source = self.root/'source'; source.mkdir()
        names = ('compose_alpine_datastore_security.py', 'compose_security_os_candidate.py'); records = {}
        for name in names:
            raw = (ROOT/'scripts'/name).read_bytes(); self.write(source/name, raw)
            records[name] = {'bytes': len(raw), 'sha256': sha256(raw).hexdigest()}
        self.write(source/'identity.json', M.encoded({'recipe_commit': 'a'*40, 'files': records}))
        with patch.object(M, '__file__', str(source/names[0])):
            self.assertEqual(M.load_source(self.root)['files'], records)
            self.write(source/names[1], b'ordinary changed source')
            with self.assertRaisesRegex(RuntimeError, 'source_changed'): M.load_source(self.root)

    def test_baseline_accepts_only_proven_typed_aliases_and_exact_layer_inventory(self):
        for profile in M.PROFILES:
            proof = self.registry(profile); image = self.image(profile)
            for digest in (proof['top_digest'], proof['platform_manifest_digest'], proof['config_digest']):
                image['Id'] = digest; M.baseline(profile, image, proof)
            image['Id'] = 'sha256:'+'9'*64
            with self.assertRaisesRegex(RuntimeError, 'baseline_identity'): M.baseline(profile, image, proof)
            image = self.image(profile); image['RootFS']['Layers'].reverse()
            with self.assertRaisesRegex(RuntimeError, 'baseline_identity'): M.baseline(profile, image, proof)
            image = self.image(profile); image['Config']['Volumes']['/extra'] = {}
            with self.assertRaisesRegex(RuntimeError, 'baseline_identity'): M.baseline(profile, image, proof)
            image = self.image(profile); image['RepoDigests'] = []
            with self.assertRaisesRegex(RuntimeError, 'baseline_repository'): M.baseline(profile, image, proof)

    def manifest_fixture(self, *, index=False, duplicate=False):
        p = deepcopy(M.PROFILES['postgres'])
        manifest = {'schemaVersion': 2, 'mediaType': 'application/vnd.oci.image.manifest.v1+json',
            'config': {'digest': p['config_id'], 'size': 9091, 'mediaType': 'application/vnd.oci.image.config.v1+json'},
            'layers': [{'digest': value, 'size': 1024, 'mediaType': 'application/vnd.oci.image.layer.v1.tar+gzip'} for value in p['layers']]}
        raw = M.encoded(manifest); selected = 'sha256:'+sha256(raw).hexdigest()
        responses = [raw]
        if index:
            descriptor = {'digest': selected, 'size': len(raw), 'platform': {'os': 'linux', 'architecture': 'amd64'}}
            top = M.encoded({'schemaVersion': 2, 'mediaType': 'application/vnd.oci.image.index.v1+json',
                             'manifests': [descriptor, descriptor] if duplicate else [descriptor]})
            responses.insert(0, top)
        p['reference'] = 'postgres@sha256:'+sha256(responses[0]).hexdigest()
        return p, responses, selected

    def test_real_registry_parser_preserves_index_manifest_and_config_ids(self):
        for index in (False, True):
            p, responses, selected = self.manifest_fixture(index=index)
            with patch.dict(M.PROFILES, {'postgres': p}), patch.object(M, 'docker', side_effect=responses) as call:
                value = M.registry_identity(self.root, 'postgres')
            self.assertEqual(value['platform_manifest_digest'], selected)
            self.assertEqual(value['config_digest'], p['config_id'])
            self.assertEqual(call.call_count, 2 if index else 1)

    def test_registry_drift_and_duplicate_platform_refuse(self):
        p, responses, _ = self.manifest_fixture(index=True, duplicate=True)
        with patch.dict(M.PROFILES, {'postgres': p}), patch.object(M, 'docker', side_effect=responses):
            with self.assertRaisesRegex(RuntimeError, 'registry_platform'): M.registry_identity(self.root, 'postgres')
        p, responses, _ = self.manifest_fixture()
        with patch.dict(M.PROFILES, {'postgres': p}), patch.object(M, 'docker', return_value=responses[0]+b' '):
            with self.assertRaisesRegex(RuntimeError, 'registry_digest'): M.registry_identity(self.root, 'postgres')

    def test_real_audit_parser_and_minimal_delta_both_profiles(self):
        for profile in M.PROFILES:
            before, after = self.audit(profile), self.audit(profile, candidate=True)
            self.assertEqual(M.parse_audit(self.audit_raw(before)), before)
            M.compare_audits(profile, before, after)
            changed = deepcopy(after); changed['files']['/usr/local/lib/ordinary-support'] = '9'*64
            with self.assertRaisesRegex(RuntimeError, 'server_or_support_changed'): M.compare_audits(profile, before, changed)

    def test_extra_removed_downgraded_and_musl_packages_refuse(self):
        before, after = self.audit(), self.audit(candidate=True)
        cases = [lambda d: d['packages'].update({'extra': '1'}), lambda d: d['packages'].pop('ordinary'),
                 lambda d: d['packages'].update({'musl': 'changed'}),
                 lambda d: d['packages'].update({'libssl3': '3.5.7-r0'})]
        for change in cases:
            candidate = deepcopy(after); change(candidate)
            with self.assertRaisesRegex(RuntimeError, 'package_delta'): M.compare_audits('postgres', before, candidate)

    def test_world_libc_server_entrypoint_mode_and_symlink_changes_refuse(self):
        before, after = self.audit(), self.audit(candidate=True)
        cases = [('world', 'protected_runtime_changed'), ('musl', 'protected_runtime_changed'), ('server', 'software_baseline'), ('repositories', 'protected_runtime_changed')]
        for key, reason in cases:
            candidate = deepcopy(after); candidate['metadata'][key] = '3'*64
            with self.assertRaisesRegex(RuntimeError, reason): M.compare_audits('postgres', before, candidate)
        for kind, path, value in [('files', '/usr/local/bin/postgres', '3'*64),
            ('files', '/usr/local/bin/docker-entrypoint.sh', '3'*64),
            ('nodes', '/usr/local/bin/postgres', '777:0:0:regular file'),
            ('links', '/usr/local/bin/alias', 'changed')]:
            candidate = deepcopy(after); candidate[kind][path] = value
            with self.assertRaises(RuntimeError): M.compare_audits('postgres', before, candidate)
        candidate = deepcopy(after); candidate['keys']['/etc/apk/keys/ordinary-vendor.rsa.pub'] = '3'*64
        with self.assertRaisesRegex(RuntimeError, 'protected_runtime_changed'): M.compare_audits('postgres', before, candidate)

    def test_gosu_compiler_real_version_and_absence_contract(self):
        before, after = self.audit(), self.audit(candidate=True)
        after['metadata']['gosu'] = before['metadata']['gosu']
        with self.assertRaisesRegex(RuntimeError, 'gosu_version'): M.compare_audits('postgres', before, after)
        after = self.audit('redis', candidate=True); after['metadata']['gosu'] = 'unexpected'
        with self.assertRaisesRegex(RuntimeError, 'unexpected_gosu'): M.compare_audits('redis', self.audit('redis'), after)

    def test_audit_duplicate_and_missing_records_refuse(self):
        raw = self.audit_raw(self.audit())
        with self.assertRaisesRegex(RuntimeError, 'audit_duplicate'): M.parse_audit(raw+raw.splitlines()[0]+b'\n')
        with self.assertRaisesRegex(RuntimeError, 'audit_nodes'): M.parse_audit(b'\n'.join(line for line in raw.splitlines() if not line.startswith(b'node\t')))

    class Response(io.BytesIO):
        def __init__(self, value, url): super().__init__(value); self.url = url; self.requests = []
        def geturl(self): return self.url
        def read(self, size=-1): self.requests.append(size); return super().read(size)

    def test_download_bounded_before_accumulation_and_pinned_hash_git_blob(self):
        raw = b'ordinary public input'; url = 'https://raw.githubusercontent.com/tianon/gosu/'+M.GOSU_COMMIT+'/main.go'
        response = self.Response(raw, url)
        blob = sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()
        with patch.object(M.urllib.request, 'urlopen', return_value=response):
            row = M.download(url, self.root/'source', len(raw), size=len(raw), digest=sha256(raw).hexdigest(), blob=blob)
        self.assertEqual(row['bytes'], len(raw)); self.assertEqual(response.requests, [len(raw)+1, 1])
        response = self.Response(raw+b'extra', url)
        with patch.object(M.urllib.request, 'urlopen', return_value=response):
            with self.assertRaisesRegex(RuntimeError, 'download_bound'): M.download(url, self.root/'overflow', len(raw))
        self.assertEqual(response.requests, [len(raw)+1])

    def test_download_bad_identity_redirect_or_unapproved_origin_refuses(self):
        url = 'https://dl.google.com/go/'+M.GO[0]
        for arguments in ({'digest': '0'*64}, {'size': 2}):
            with patch.object(M.urllib.request, 'urlopen', return_value=self.Response(b'ordinary', url)):
                with self.assertRaisesRegex(RuntimeError, 'download_identity'):
                    M.download(url, self.root/('input'+str(len(list(self.root.iterdir())))), 64, **arguments)
        with patch.object(M.urllib.request, 'urlopen', return_value=self.Response(b'ordinary', 'https://other.invalid/input')):
            with self.assertRaisesRegex(RuntimeError, 'download_redirect'): M.download(url, self.root/'redirect', 64)
        with self.assertRaisesRegex(RuntimeError, 'download_origin'): M.download('https://other.invalid/input', self.root/'no', 64)

    def test_retained_build_inputs_refuse_hash_and_aba_drift(self):
        path = self.write(self.root/'context/repo/x86_64/fixture.apk', b'ordinary package')
        self.write(self.root/'context/repositories', b'/capsule/repo\n'); self.write(self.root/'context/Dockerfile', b'FROM fixed\n')
        rows = {'fixture.apk': {'bytes': path.stat().st_size, 'sha256': sha256(path.read_bytes()).hexdigest()}}
        with M.retained_inputs(self.root, rows): pass
        with self.assertRaisesRegex(RuntimeError, 'input_changed'):
            with M.retained_inputs(self.root, rows):
                replacement = self.write(self.root/'new', path.read_bytes()); os.replace(replacement, path)
        rows['fixture.apk']['sha256'] = '0'*64
        with self.assertRaisesRegex(RuntimeError, 'build_input_changed'):
            with M.retained_inputs(self.root, rows): pass

    def test_recipe_only_signed_offline_runtime_library_install_no_config_instructions(self):
        for profile in M.PROFILES:
            recipe = M.dockerfile(profile); lines = recipe.splitlines()
            self.assertTrue(lines[-2].startswith('FROM '+M.PROFILES[profile]['reference']))
            self.assertTrue(lines[-1].startswith('RUN --network=none '))
            command = json.loads(lines[-1][lines[-1].index('["/bin/sh"'):])
            self.assertEqual(command[:3], ['/bin/sh', '-eu', '-c'])
            self.assertIn('fix --upgrade '+' '.join(M.PROFILES[profile]['updates']), command[3])
            self.assertNotIn('allow-untrusted', recipe); self.assertNotIn('apk add', recipe)
            self.assertFalse(any(line.startswith(('ENV ', 'LABEL ', 'VOLUME ', 'USER ', 'CMD ', 'ENTRYPOINT ')) for line in lines))
        recipe = M.dockerfile('postgres')
        self.assertIn('CGO_ENABLED=0 GOTOOLCHAIN=local GOPROXY=off GOSUMDB=off', recipe)
        self.assertIn('-ldflags="-d -w"', recipe); self.assertNotIn('-s ', recipe)
        self.assertNotIn('gosu_build', M.dockerfile('redis'))

    def test_redis_fixture_waits_for_its_foreground_child_before_each_restart(self):
        script = M.REDIS_SMOKE
        self.assertIn('--daemonize no', script); self.assertNotIn('--daemonize yes', script)
        self.assertIn('server=$!', script); self.assertIn('for attempt in $(seq 1 100)', script)
        self.assertIn('stop() { cli shutdown nosave; wait "$server"; }', script)
        self.assertEqual(script.splitlines().count('stop'), 2)
        self.assertLess(script.index('start yes\n'), script.index('\nstop\n'))

    def test_isolated_fixture_owns_all_declared_volumes_and_no_network_or_host_mounts(self):
        (self.root/'reports').mkdir()
        calls = []; cid = 'b'*64; image = M.PROFILES['postgres']['config_id']
        def docker(root, argv, **kwargs):
            calls.append(argv)
            if argv[0] == 'create': return (cid+'\n').encode()
            if argv[:2] == ['start', '--attach']: return b'ordinary\n'
            if argv[:2] == ['inspect', '--format']: return b'0\n'
            if argv[0] == 'inspect': return M.encoded([{'Id': cid, 'Name': '/'+calls[0][calls[0].index('--name')+1],
                'Image': image, 'Config': {'Labels': {M.LABEL: M.owner()}}}])
            return b''
        with patch.object(M, 'docker', side_effect=docker): M.isolated(self.root, 'postgres', image, 'true', 'fixture', user='70:70')
        create = calls[0]
        self.assertIn('--network=none', create); self.assertIn('--read-only', create); self.assertIn('--cap-drop=ALL', create)
        self.assertEqual([create[i+1].split(':')[0] for i, value in enumerate(create) if value == '--tmpfs'], ['/tmp', '/var/lib/postgresql/data'])
        self.assertNotIn('--mount', create); self.assertEqual(calls[-1], ['rm', '--force', cid])

    def test_unknown_create_stays_failure_even_after_successful_owned_cleanup(self):
        (self.root/'reports').mkdir()
        with patch.object(M, 'docker', side_effect=RuntimeError('command_timeout')), patch.object(M, 'cleanup_container') as cleanup:
            with self.assertRaisesRegex(RuntimeError, 'command_timeout'):
                M.isolated(self.root, 'redis', M.PROFILES['redis']['config_id'], 'true', 'timeout')
        self.assertEqual(cleanup.call_count, 1)
        outcome = json.loads((self.root/'reports/timeout-daemon-outcome.json').read_bytes())
        self.assertEqual(outcome['daemon_outcome'], 'UNKNOWN'); self.assertFalse(outcome['cancellation_proven'])

    def test_cleanup_foreign_image_or_owner_never_removes(self):
        image = M.PROFILES['redis']['config_id']; cid = 'b'*64
        for field in ('Image', 'owner'):
            value = {'Id': cid, 'Name': '/own', 'Image': image, 'Config': {'Labels': {M.LABEL: M.owner()}}}
            if field == 'Image': value[field] = 'sha256:'+'0'*64
            else: value['Config']['Labels'][M.LABEL] = 'foreign'
            with patch.object(M, 'docker', return_value=M.encoded([value])) as call:
                with self.assertRaisesRegex(RuntimeError, 'cleanup_owner'): M.cleanup_container(self.root, cid, 'own', image)
                self.assertEqual(call.call_count, 1)

    def test_whole_oci_config_and_rootfs_prefix_comparison_is_real(self):
        before, after = self.image(), self.image(candidate=True); C.compare_images(before, after)
        for mutate in (lambda d: d['Config']['Env'].append('UNAPPROVED=yes'),
                       lambda d: d['Config'].update({'Labels': {'new': 'label'}}),
                       lambda d: d['RootFS']['Layers'].reverse()):
            candidate = deepcopy(after); mutate(candidate)
            with self.assertRaises(RuntimeError): C.compare_images(before, candidate)

    def report_fixture(self):
        image = 'sha256:'+'c'*64; runtime = self.image()['Config']; layers = self.image(candidate=True)['RootFS']['Layers']
        record = {'image_id': image, 'runtime_config': runtime, 'candidate_layers': layers, 'baseline': M.PROFILES['postgres']}
        raw = {'SchemaVersion': 2, 'ArtifactType': 'container_image', 'Metadata': {'ImageID': image,
            'ImageConfig': {'config': runtime, 'rootfs': {'diff_ids': layers}}},
            'Results': [{'Vulnerabilities': [{'VulnerabilityID': 'ordinary-unfixed', 'Severity': 'HIGH', 'FixedVersion': ''}]}]}
        sbom = {'metadata': {'component': {'name': image, 'type': 'container', 'properties': [
            {'name': 'aquasecurity:trivy:ImageID', 'value': image}]}},
            'components': [{'name': n, 'version': v} for n, v in M.PROFILES['postgres']['updates'].items()]}
        return record, raw, sbom

    def test_raw_unfixed_rows_preserved_and_identity_sbom_versions_bound(self):
        record, raw, sbom = self.report_fixture(); original = deepcopy(raw)
        self.assertEqual(M.validate_reports(record, raw, sbom), {'HIGH': 1}); self.assertEqual(raw, original)
        raw['Metadata']['ImageID'] = 'sha256:'+'0'*64
        with self.assertRaisesRegex(RuntimeError, 'raw_identity'): M.validate_reports(record, raw, sbom)
        record, raw, sbom = self.report_fixture(); sbom['components'][0]['version'] = 'old'
        with self.assertRaisesRegex(RuntimeError, 'sbom_package'): M.validate_reports(record, raw, sbom)

    def test_default_app_workflow_and_optional_datastore_flags(self):
        import yaml
        workflow = yaml.safe_load((ROOT/'.github/workflows/security-os-candidate-compose.yml').read_bytes())
        dispatch = workflow.get('on', workflow.get(True))['workflow_dispatch']['inputs']['datastore_profile']
        self.assertEqual(dispatch['default'], 'none'); self.assertEqual(dispatch['options'], ['none', 'postgres', 'redis'])
        self.assertEqual(set(workflow['jobs']), {'composition', 'datastore'})
        steps = workflow['jobs']['datastore']['steps']
        scan = next(row for row in steps if row.get('id') == 'vulnerabilities')['with']
        self.assertEqual(scan['severity'], 'UNKNOWN,LOW,MEDIUM,HIGH,CRITICAL'); self.assertEqual(scan['ignore-unfixed'], 'false')
        self.assertEqual(scan['exit-code'], '0'); self.assertEqual(scan['scanners'], 'vuln')
        self.assertTrue(next(row for row in steps if row.get('id') == 'seal')['run'].startswith('python3 -I -B'))

    def test_raw_api_default_omission_only_and_no_unknown_fields_or_false_int_equivalence(self):
        record, raw, sbom = self.report_fixture()
        raw['Metadata']['ImageConfig']['config'] = deepcopy(raw['Metadata']['ImageConfig']['config'])
        raw['Metadata']['ImageConfig']['config'].pop('Labels'); raw['Metadata']['ImageConfig']['config'].pop('User')
        self.assertEqual(M.validate_reports(record, raw, sbom), {'HIGH': 1})
        for key, value in [('UnrecognizedField', None), ('Tty', 0), ('Cmd', ['changed'])]:
            altered = deepcopy(raw); altered['Metadata']['ImageConfig']['config'][key] = value
            with self.assertRaisesRegex(RuntimeError, 'raw_config'): M.validate_reports(record, altered, sbom)

    def flow(self, profile, *, drift=False):
        """Exercise actual compose/acquire/isolated/finalize, mocking only external commands/downloads."""
        pin = M.PROFILES[profile]; before = self.image(profile); after = self.image(profile, candidate=True)
        initial = self.audit(profile); installed = self.audit(profile, candidate=True)
        if drift: installed['files'][pin['server']] = '0'*64
        identity = {'recipe_commit': 'a'*40, 'files': {}}
        containers = {}; events = []; next_id = iter(range(1, 20)); proof = self.registry(profile)
        def docker(root, argv, **kwargs):
            events.append((argv, kwargs))
            if argv[0] == 'pull': return b'public pull complete\n'
            if argv[:2] == ['image', 'inspect']:
                return M.encoded([after if argv[2] == after['Id'] else before])
            if argv[0] == 'create':
                cid = f'{next(next_id):064x}'; name = argv[argv.index('--name')+1]
                image_index = len(argv)-6
                # No bind mounts may replace a software directory or expose a daemon.
                self.assertNotIn('/var/run/docker.sock', repr(argv)); self.assertIn('--read-only', argv)
                self.assertIn(pin['volume']+':rw,nosuid,nodev,noexec,size=768m,uid='+argv[argv.index('--user')+1].split(':')[0], repr(argv))
                containers[cid] = {'Id': cid, 'Name': '/'+name, 'Image': argv[image_index],
                    'Config': {'Labels': {M.LABEL: M.owner()}}, 'script': argv[image_index+3]}
                return (cid+'\n').encode()
            if argv[:2] == ['start', '--attach']:
                value = containers[argv[2]]; script = value['script']; candidate = value['Image'] == after['Id']
                if script == M.AUDIT: output = self.audit_raw(installed if candidate else initial)
                elif script in (M.POSTGRES_SMOKE, M.REDIS_SMOKE): output = b'ordinary server output\nDATASTORE_FIXTURE_PASS\n'
                elif script == M.GOSU_SMOKE: output = b'GOSU_FIXTURE_PASS\n'
                else:
                    self.assertTrue(script.startswith('/sbin/apk verify ')); self.assertIn('--simulate fix --upgrade', script)
                    self.assertNotIn('--allow-untrusted', script); output = b'vendor signatures and plan verified\n'
                self.write(kwargs['log_path'], output)
                return output
            if argv[:2] == ['inspect', '--format']: return b'0\n'
            if argv[0] == 'inspect':
                item = containers.get(argv[1]) or next((v for v in containers.values() if v['Name'] == '/'+argv[1]), None)
                return M.encoded([item])
            if argv[0] == 'rm': containers.pop(argv[-1]); return b'removed\n'
            if argv[0] == 'build':
                self.assertEqual(argv[argv.index('--builder')+1], 'default')
                recipe = (root/'context/Dockerfile').read_bytes()
                self.assertEqual(recipe.decode(), M.dockerfile(profile))
                self.write(Path(argv[argv.index('--iidfile')+1]), (after['Id']+'\n').encode())
                self.write(kwargs['log_path'], b'ordinary offline security build\n'); return b'ordinary offline security build\n'
            raise AssertionError(('unexpected_mock_command', argv))
        def download(url, path, maximum, **kwargs):
            raw = ('ordinary public acquisition '+path.name).encode(); self.write(path, raw)
            if path.name == M.GO[0]:
                self.assertEqual((kwargs['size'], kwargs['digest']), (M.GO[1], M.GO[2]))
            return {'bytes': len(raw), 'sha256': sha256(raw).hexdigest(), 'url': url}
        def openssl(argv, **kwargs):
            self.assertEqual(argv[:3], ['/usr/bin/openssl', 'req', '-x509'])
            self.write(Path(argv[argv.index('-keyout')+1]), b'public disposable key')
            self.write(Path(argv[argv.index('-out')+1]), b'public disposable certificate')
            return b''
        patches = ExitStack()
        with patches:
            patches.enter_context(patch.object(M.os, 'statvfs', return_value=SimpleNamespace(f_bavail=16*1024**3, f_frsize=1)))
            patches.enter_context(patch.object(M, 'ci'))
            patches.enter_context(patch.object(M, 'load_source', return_value=identity))
            patches.enter_context(patch.object(M, 'registry_identity', return_value=proof))
            patches.enter_context(patch.object(M, 'docker', side_effect=docker))
            patches.enter_context(patch.object(M, 'download', side_effect=download))
            patches.enter_context(patch.object(C, 'command', side_effect=openssl))
            patches.enter_context(patch.object(C, 'inspect_builder', side_effect=lambda root: self.write(root/'reports/builder-inspect.log', b'default docker running\n')))
            if drift:
                with self.assertRaisesRegex(RuntimeError, 'software_baseline'): M.compose(self.root, profile)
                self.assertFalse((self.root/'reports/candidate.json').exists()); self.assertFalse(containers)
                return
            M.compose(self.root, profile)
            self.assertFalse(containers)
            record = json.loads((self.root/'reports/candidate.json').read_bytes())
            self.assertEqual(record['runtime_config'], before['Config']); self.assertFalse(record['security_accepted'])
            self.assertEqual(record['inputs']['APKINDEX.tar.gz']['url'], 'https://dl-cdn.alpinelinux.org/alpine/'+pin['branch']+'/main/x86_64/APKINDEX.tar.gz')
            self.assertEqual(set(record['evidence_sha256']), {p.name for p in (self.root/'reports').iterdir() if p.stat().st_size and p.name != 'candidate.json'})
            raw = {'SchemaVersion': 2, 'ArtifactType': 'container_image', 'Metadata': {'ImageID': after['Id'],
                'ImageConfig': {'config': after['Config'], 'rootfs': {'diff_ids': after['RootFS']['Layers']}}},
                'Results': [{'Vulnerabilities': [{'Severity': 'HIGH', 'FixedVersion': ''}]}]}
            sbom = {'metadata': {'component': {'name': after['Id'], 'type': 'container', 'properties': [
                {'name': 'aquasecurity:trivy:ImageID', 'value': after['Id']}]}},
                'components': [{'name': n, 'version': v} for n, v in pin['updates'].items()]}
            self.write(self.root/'reports/vulnerabilities.json', M.encoded(raw)); self.write(self.root/'reports/sbom.cdx.json', M.encoded(sbom))
            with patch.object(C, 'export_image', return_value={'name': 'candidate-image.tar.gz', 'sha256': 'f'*64}) as export:
                M.finalize(self.root, profile); export.assert_called_once_with(self.root, after['Id'])
            receipt = json.loads((self.root/'image-export.json').read_bytes())
            self.assertEqual(receipt['raw_counts'], {'HIGH': 1}); self.assertFalse(receipt['security_accepted'])
            self.assertEqual(receipt['candidate_receipt_sha256'], sha256((self.root/'reports/candidate.json').read_bytes()).hexdigest())

    def test_full_postgres_control_flow_build_smokes_raw_binding_export(self): self.flow('postgres')

    def test_full_redis_control_flow_build_smokes_raw_binding_export(self): self.flow('redis')

    def test_full_control_flow_server_drift_prevents_receipt_and_export(self): self.flow('postgres', drift=True)


if __name__ == '__main__':
    unittest.main()
