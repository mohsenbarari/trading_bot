"""Owned public ZIP/FD and Native coordinator completion tests; no live actions.

Every source/evidence value below is an explicitly synthetic unit fixture. It
never establishes an actual CI image, vulnerability disposition or LOAD result.
The required maintenance CI lane runs with its real UID0; no UID is disguised.
"""
from contextlib import ExitStack, redirect_stdout, redirect_stderr
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import importlib.util
import io
import json
import os
import sys
import tarfile
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import warnings
import zipfile

ROOT = Path(__file__).resolve().parents[1]
TARGETS = ('subprocess.Popen', 'subprocess.run', 'subprocess.call', 'subprocess.check_call',
           'subprocess.check_output', 'os.system', 'os.popen', 'os.kill', 'os.killpg',
           'socket.socket', 'socket.create_connection', 'socket.getaddrinfo',
           'urllib.request.urlopen', 'ctypes.CDLL', 'ctypes.PyDLL', 'signal.signal',
           'signal.alarm', 'resource.setrlimit')


def fence(stack):
    for target in TARGETS:
        stack.enter_context(patch(target, side_effect=AssertionError('ambient operation denied')))


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


with ExitStack() as import_guard:
    fence(import_guard)
    C = load('_owned_datastore_contract', ROOT/'deploy/maintenance/datastore_candidate_contract.py')
    L = load('_owned_datastore_launcher', ROOT/'deploy/maintenance/datastore_candidate_launch.py')
    W = load('_owned_datastore_worker', ROOT/'deploy/maintenance/datastore_candidate_stage.py')
    T = load('_owned_datastore_archive', ROOT/'deploy/maintenance/runtime_image_archive_transport.py')
    V = load('_owned_datastore_moby', ROOT/'scripts/admit_security_component_image_reuse.py')


class ContractCompletionTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack(); self.addCleanup(self.stack.close); fence(self.stack)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix='owned-datastore-completion-')))
        self.root.chmod(0o700)
        before = os.umask(0o077); self.addCleanup(os.umask, before)

    def private(self, path, raw):
        path.write_bytes(raw); path.chmod(0o600); return path

    def evidence(self, profile='redis', mutate=None):
        spec = deepcopy(C.PROFILES[profile])
        spec['config_id'] = 'sha256:' + sha256(b'EXPLICIT_SYNTHETIC_IMAGE_ID').hexdigest()
        spec['layers'] = ['sha256:' + sha256(b'EXPLICIT_SYNTHETIC_LAYER').hexdigest()]
        unknown = [{'VulnerabilityID': 'CVE-2026-39824', 'PkgName': 'golang.org/x/sys',
                    'InstalledVersion': 'v0.1.0', 'Severity': 'UNKNOWN'}] if profile == 'postgres' else []
        configuration = {'Env': ['PUBLIC_SYNTHETIC_FIXTURE=ordinary'], 'Cmd': ['ordinary-fixture']}
        documents = {
            'reports/candidate.json': {'schema': 'alpine_datastore_security_candidate/1',
                'status': 'built_tested_not_accepted', 'profile': profile, 'image_id': spec['config_id'],
                'server_bytes_unchanged': True, 'whole_runtime_config_unchanged': True,
                'full_role_matrix': 'NOT_TESTED', 'production_data_used': False,
                'production_installed': False, 'security_accepted': False, 'scanner_gate_waived': False,
                'candidate_layers': spec['layers'], 'runtime_config': deepcopy(configuration)},
            'reports/candidate-image.json': {'Id': spec['config_id'], 'Os': 'linux',
                'Architecture': 'amd64', 'RootFS': {'Type': 'layers', 'Layers': spec['layers']},
                'Config': deepcopy(configuration)},
            'reports/vulnerabilities.json': {'Metadata': {'ImageID': spec['config_id']},
                'Results': [{'Vulnerabilities': unknown}]},
            'reports/sbom.cdx.json': {'bomFormat': 'CycloneDX', 'synthetic_owned_fixture': True},
            'source/identity.json': {'source_commit': 'ecfb667ee1583168f2777a6fa5f54766a63ef787',
                                   'synthetic_owned_fixture': True},
            'reports/ordinary-public-proof.json': {'synthetic_owned_fixture': True, 'values': ['ordinary']},
        }
        if mutate: mutate(documents)
        members = {name: C.encode(value) if type(value) is dict else value for name, value in documents.items()}
        for key, name in (('candidate_sha256', 'candidate.json'), ('candidate_image_sha256', 'candidate-image.json'),
                          ('raw_sha256', 'vulnerabilities.json'), ('sbom_sha256', 'sbom.cdx.json')):
            spec[key] = sha256(members['reports/'+name]).hexdigest()
        exported = C.document((ROOT/'tests/fixtures/security_datastore_candidates'/(profile+'-export.json')).read_bytes())
        exported['image_id'] = spec['config_id']; exported['candidate_receipt_sha256'] = spec['candidate_sha256']
        proof = {'ordinary-public-proof.json': sha256(members['reports/ordinary-public-proof.json']).hexdigest()}
        exported['reports_sha256'] = proof; exported['evidence_sha256'] = proof
        members['image-export.json'] = C.encode(exported)
        spec['export_sha256'] = sha256(members['image-export.json']).hexdigest()
        spec['export_bytes'] = len(members['image-export.json'])
        raw = self.zip(members)
        spec['evidence_bytes'] = len(raw); spec['evidence_sha256'] = sha256(raw).hexdigest()
        return raw, spec, members

    def zip(self, members, duplicate=None):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            for name, raw in members.items(): archive.writestr(name, raw)
            if duplicate:
                with warnings.catch_warnings(record=True) as observed:
                    warnings.simplefilter('always', UserWarning)
                    archive.writestr(duplicate, members[duplicate])
                self.assertEqual(len(observed), 1)
        return stream.getvalue()

    def repin(self, raw, spec):
        return {**spec, 'evidence_bytes': len(raw), 'evidence_sha256': sha256(raw).hexdigest()}

    def test_pinned_public_evidence_reconciles_all_report_hashes_without_acceptance(self):
        for profile in ('postgres', 'redis'):
            raw, spec, _ = self.evidence(profile)
            value = C.evidence(raw, spec)
            self.assertEqual(value['image']['Id'], spec['config_id'])
            self.assertEqual(value['image']['RootFS']['Layers'], spec['layers'])
            self.assertFalse(value['export']['security_accepted'])
            self.assertFalse(value['export']['production_data_used'])
            self.assertEqual(value['export']['full_role_matrix'], 'NOT_TESTED')
            self.assertEqual(value['export']['raw_counts'], {'UNKNOWN': 1} if profile == 'postgres' else {})

    def test_outer_zip_identity_inner_report_sha_duplicate_and_oversize_refuse(self):
        raw, spec, members = self.evidence()
        with self.assertRaisesRegex(C.Refusal, 'evidence_zip'): C.evidence(raw+b'x', spec)
        wrong = dict(members); wrong['reports/candidate.json'] += b' '
        changed = self.zip(wrong)
        with self.assertRaisesRegex(C.Refusal, 'evidence_member_sha'): C.evidence(changed, self.repin(changed, spec))
        wrong = dict(members); wrong['reports/ordinary-public-proof.json'] += b' '
        changed = self.zip(wrong)
        with self.assertRaisesRegex(C.Refusal, 'evidence_report_sha'): C.evidence(changed, self.repin(changed, spec))
        changed = self.zip(members, duplicate='reports/candidate.json')
        with self.assertRaisesRegex(C.Refusal, 'evidence_inventory'): C.evidence(changed, self.repin(changed, spec))
        raw, spec, _ = self.evidence(mutate=lambda docs: docs.update({'reports/sbom.cdx.json': b' '*(C.MAX_JSON+1)}))
        with self.assertRaisesRegex(C.Refusal, 'evidence_member_bound'): C.evidence(raw, spec)

    def test_transport_correct_semantic_drift_never_inherits_security_acceptance(self):
        changes = (
            ('evidence_recipe', 'redis', lambda d: d['source/identity.json'].update(source_commit='f'*40)),
            ('candidate_receipt', 'redis', lambda d: d['reports/candidate.json'].update(security_accepted=1)),
            ('candidate_image', 'redis', lambda d: d['reports/candidate-image.json'].update(Architecture='arm64')),
            ('raw_report', 'redis', lambda d: d['reports/vulnerabilities.json']['Results'][0].update(Vulnerabilities=[{'Severity': 'HIGH'}])),
            ('raw_scope', 'postgres', lambda d: d['reports/vulnerabilities.json']['Results'][0]['Vulnerabilities'][0].update(VulnerabilityID='ORDINARY_UNREVIEWED_FIXTURE')),
        )
        for code, profile, mutate in changes:
            raw, spec, _ = self.evidence(profile, mutate)
            with self.subTest(code=code), self.assertRaisesRegex(C.Refusal, code): C.evidence(raw, spec)

    def test_public_read_and_same_buffer_module_refuse_wrong_pin_or_replaced_file(self):
        body = b'PUBLIC_FIXTURE_VALUE = 23\n'
        path = self.private(self.root/'ordinary_module.py', body); pin = sha256(body).hexdigest()
        self.assertEqual(C.read(path, pin=pin), body)
        self.assertEqual(C.sealed(path, pin).PUBLIC_FIXTURE_VALUE, 23)
        with self.assertRaisesRegex(C.Refusal, 'input_sha256'): C.sealed(path, 'f'*64)
        with ExitStack() as stack:
            module, check = L.module(stack, path, pin)
            self.assertEqual(module.PUBLIC_FIXTURE_VALUE, 23)
            self.assertEqual(module.__file__, str(path))
            other = self.private(self.root/'replacement.py', body); os.replace(other, path)
            with self.assertRaisesRegex(RuntimeError, 'control_changed'): check()
        self.assertEqual(path.read_bytes(), body)
        with self.assertRaisesRegex(RuntimeError, 'control_sha256'), ExitStack() as stack:
            L.module(stack, path, 'e'*64)

    def test_public_single_link_input_refuses_hardlink_without_removing_either(self):
        body = b'PUBLIC_FIXTURE_VALUE = 23\n'; path = self.private(self.root/'ordinary.py', body)
        other = self.root/'ordinary-hardlink.py'; os.link(path, other)
        with self.assertRaisesRegex(C.Refusal, 'input_metadata'): C.read(path, pin=sha256(body).hexdigest())
        self.assertEqual(path.read_bytes(), body); self.assertEqual(other.read_bytes(), body)

    def operation(self, marker, source):
        now = datetime.now(timezone.utc)
        return {'schema': 'datastore_candidate_operation/1', 'operation_id': W.OPERATION,
            'profile': 'postgres', 'phase': 'stage-web', 'nonce': 'a'*32,
            'issued_at_utc': (now-timedelta(seconds=1)).isoformat().replace('+00:00', 'Z'),
            'deadline_utc': (now+timedelta(seconds=60)).isoformat().replace('+00:00', 'Z'),
            'source_sha256': {**C.HELPERS, 'load_native8da.py': W.STREAM_HELPER_SHA,
                **{name: 'b'*64 for name in ('datastore_candidate_contract.py', 'datastore_candidate_stage.py', 'datastore_candidate_launch.py')}},
            'baselines': {site: {'path': '/ordinary-public-fixture/'+site+'.json', 'sha256': 'c'*64} for site in ('bot', 'web')},
            'evidence_path': '/ordinary-public-fixture/evidence.zip', 'source_nonce': None,
            'source_admission': None, 'web_loaded': None,
            'fences': {'marker': list(C.identity(marker.lstat())), 'source_mutex': list(C.identity(source.lstat()))},
            'security_accepted': False, 'activation_allowed': False}

    def test_pre_rpc_error_with_foreign_reservation_reports_cleanup_unknown_and_preserves_source(self):
        marker = self.private(self.root/'marker', b'ordinary public marker')
        source = self.private(self.root/'source', b''); before = C.identity(source.lstat())
        reservation = self.root/'reservation'; workroot = self.root/'owned-work'; workroot.mkdir(mode=0o700)
        launcher = self.private(self.root/'datastore_candidate_launch.py', (ROOT/'deploy/maintenance/datastore_candidate_launch.py').read_bytes())
        op = self.operation(marker, source); op['source_sha256'][launcher.name] = sha256(launcher.read_bytes()).hexdigest()
        controls = self.root/'controls'; controls.mkdir(mode=0o700)
        raw = C.encode(op); control = self.private(controls/(op['nonce']+'.json'), raw)
        worker = SimpleNamespace(control=W.control, ROOTS={'bot': workroot}, OPERATION=W.OPERATION,
            MARKER=marker, SOURCE_LOCK=source, RESERVATION=reservation)
        foreign = b'ordinary foreign guard fixture'
        def fail_before_rpc(call, *, reservation_owned, fence_check, on_rpc):
            self.assertTrue(reservation_owned); fence_check()
            replacement = self.private(self.root/'foreign', foreign); os.replace(replacement, reservation)
            raise RuntimeError('capacity_floor')
        worker.execute = Mock(side_effect=fail_before_rpc)
        def modules(stack, path, pin, **owners):
            return (worker if path.name.endswith('_stage.py') else C), lambda: None
        args = SimpleNamespace(control=str(control), control_sha256=sha256(raw).hexdigest(), apply=W.CONFIRM)
        with patch.object(L, 'HERE', self.root), patch.object(L, 'module', side_effect=modules):
            with self.assertRaises(L.CleanupUnknown) as caught: L.launch(args)
        self.assertEqual(caught.exception.primary_code, 'capacity_floor')
        worker.execute.assert_called_once(); self.assertEqual(reservation.read_bytes(), foreign)
        self.assertEqual(C.identity(source.lstat()), before); self.assertEqual(source.read_bytes(), b'')
        self.assertFalse(any(workroot.iterdir()))

    def invoke_main(self, value=None, status=0, error=None, contract=C):
        argv = ['datastore_candidate_launch.py', '--control', str(self.root/'public-control.json'),
                '--control-sha256', 'c'*64, '--apply', W.CONFIRM]
        output = io.StringIO()
        launch = Mock(side_effect=error, return_value=(value, status))
        with patch.object(L, 'CONTRACT', contract), patch.object(L, 'launch', launch), \
             patch.object(sys, 'argv', argv), \
             redirect_stdout(output):
            result = L.main()
        return result, json.loads(output.getvalue()), launch

    def test_cli_success_and_pending_receipt_preserve_status_and_public_shape(self):
        for value, status in (({'status': 'ordinary_owned_fixture', 'security_accepted': False}, 0),
                              ({'status': 'known_outcome_final_receipt_pending', 'security_accepted': False}, 1)):
            result, printed, called = self.invoke_main(value, status)
            self.assertEqual(result, status); self.assertEqual(printed, value)
            self.assertEqual(called.call_args.args[0].apply, W.CONFIRM)
            self.assertEqual(set(vars(called.call_args.args[0])), {'control', 'control_sha256', 'apply'})

    def test_cli_unknown_or_before_entry_failure_never_reflects_exception_values(self):
        for contract, primary in ((C, 'unexpected_failure'), (None, 'entry_not_verified')):
            result, printed, _ = self.invoke_main(error=ValueError('EXPLICIT_INERT_PRIVATE_ERROR'), contract=contract)
            self.assertEqual(result, 1); self.assertEqual(printed['primary_failure'], primary)
            self.assertFalse(printed['retry_load_allowed']); self.assertFalse(printed['daemon_cancellation_proven'])
            self.assertFalse(printed['security_accepted']); self.assertNotIn('EXPLICIT_INERT_PRIVATE_ERROR', json.dumps(printed))
        result, printed, _ = self.invoke_main(error=L.CleanupUnknown('capacity_floor'))
        self.assertEqual(result, 1); self.assertEqual(printed['code'], 'reservation_cleanup_unknown')
        self.assertEqual(printed['primary_failure'], 'capacity_floor')

    def test_cli_exposes_no_filesystem_owner_override(self):
        argv = ['datastore_candidate_launch.py', '--control', str(self.root/'public.json'),
                '--control-sha256', 'c'*64, '--apply', W.CONFIRM, '--expected-uid', '0']
        with patch.object(sys, 'argv', argv), patch.object(L, 'launch') as launch, redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as caught: L.main()
        self.assertEqual(caught.exception.code, 2); launch.assert_not_called()



    def test_repeated_logical_empty_layers_keep_both_diffids_and_hash_physical_blob_once(self):
        # Normal OCI/Docker exports can reference one physical empty TAR twice.
        empty = io.BytesIO()
        with tarfile.open(fileobj=empty, mode='w', format=tarfile.USTAR_FORMAT): pass
        body = empty.getvalue(); diff = 'sha256:'+sha256(body).hexdigest()
        config = {'architecture':'amd64','created':'2026-10-25T01:02:03Z','history':[],
            'os':'linux','rootfs':{'type':'layers','diff_ids':[diff,diff]},'config':{}}
        raw_config = C.encode(config); config_id = 'sha256:'+sha256(raw_config).hexdigest()
        blob = lambda raw: 'blobs/sha256/'+sha256(raw).hexdigest()
        descriptor = {'mediaType':'application/vnd.oci.image.layer.v1.tar',
                      'digest':diff,'size':len(body)}
        manifest = C.encode({'schemaVersion':2,'mediaType':C.OCI_MANIFEST,
            'config':{'mediaType':'application/vnd.oci.image.config.v1+json','digest':config_id,'size':len(raw_config)},
            'layers':[descriptor,descriptor]})
        index = C.encode({'schemaVersion':2,'mediaType':C.OCI_INDEX,'manifests':[
            {'mediaType':C.OCI_MANIFEST,'digest':'sha256:'+sha256(manifest).hexdigest(),'size':len(manifest)}]})
        members = {blob(raw_config):raw_config,blob(manifest):manifest,blob(body):body,
            'index.json':index,'oci-layout':C.encode({'imageLayoutVersion':'1.0.0'}),
            'manifest.json':C.encode([{'Config':blob(raw_config),'Layers':[blob(body),blob(body)],'RepoTags':[]}]),
            **C.legacy_configs(config,V)}
        outer=io.BytesIO()
        with tarfile.open(fileobj=outer,mode='w',format=tarfile.USTAR_FORMAT) as archive:
            for name,raw in members.items():
                info=tarfile.TarInfo(name);info.size=len(raw);info.mode=0o644
                archive.addfile(info,io.BytesIO(raw))
        raw=outer.getvalue();data={'spec':{'profile':'ordinary_owned_fixture','config_id':config_id,
            'layers':[diff,diff],'archive':{'uncompressed_bytes':len(raw)}},'image':{'Config':{}}}
        with patch.object(T,'layer_digest',wraps=T.layer_digest) as physical:
            proof=C.validate_archive(io.BytesIO(raw),len(raw),__import__('time').monotonic()+30,data,T,V)
        self.assertEqual(physical.call_count,1)
        self.assertEqual(proof['rootfs_layers'],[diff,diff])
        self.assertEqual(proof['expanded_layer_bytes'],2*len(body))
        self.assertEqual(proof['legacy_compatibility_configs'],2)
        self.assertFalse(proof['daemon_identity_measured']);self.assertFalse(proof['security_accepted'])

if __name__ == '__main__': unittest.main()
