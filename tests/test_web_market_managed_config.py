"""Closed source staging fixtures; no host commands, network or private inputs."""
from contextlib import ExitStack
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import importlib.util
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import unittest
from unittest import mock

from scripts import backup_market_pipeline_archive as B
from scripts import prepare_market_pipeline_primary_release as R
from tests.test_prepare_market_pipeline_primary_release import _web_values

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('managed_configuration_fixture', ROOT/'deploy/maintenance/web_market_managed_config.py')
M = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(M)
P_SPEC = importlib.util.spec_from_file_location('managed_preflight_fixture', ROOT/'deploy/maintenance/web_engine_preflight.py')
P = importlib.util.module_from_spec(P_SPEC); P_SPEC.loader.exec_module(P)


class ManagedConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack(); self.addCleanup(self.stack.close)
        prior_umask = os.umask(0o077); self.addCleanup(os.umask, prior_umask)
        for name in ('subprocess.run', 'subprocess.Popen', 'os.system', 'socket.socket',
                     'socket.create_connection', 'socket.getaddrinfo', 'urllib.request.urlopen'):
            self.stack.enter_context(mock.patch(name, side_effect=AssertionError('ambient_host_or_network_forbidden')))
        self.folder = Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix='.managed-config-', dir=ROOT)))
        self.folder.chmod(0o700)
        self.env = self.folder/'original.env'; self.managed = self.folder/'managed'
        self.marker = self.folder/'marker'; self.marker.write_bytes(b'public-marker'); self.marker.chmod(0o600)
        self.source = self.folder/'absent-source'; self.reservation = self.folder/'absent-reservation'
        self.plan_path = self.folder/'plan.json'
        for name, value in {'ENV': self.env, 'MANAGED': self.managed, 'MARKER': self.marker,
                            'SOURCE': self.source, 'RESERVATION': self.reservation, 'PLAN': self.plan_path}.items():
            self.stack.enter_context(mock.patch.object(M, name, value))
        self.stack.enter_context(mock.patch.object(P, 'PENDING', self.folder/'absent-pending'))
        info = self.marker.stat()
        self.stack.enter_context(mock.patch.object(M, 'MARKER_DEVICE', info.st_dev))
        self.stack.enter_context(mock.patch.object(M, 'MARKER_INODE', info.st_ino))
        self.stack.enter_context(mock.patch.object(M, 'MARKER_SHA', sha256(b'public-marker').hexdigest()))
        values = R._rendered({**_web_values(), 'CUSTOM_RETAINED': 'retained'},
                             release_sha=M.CONFIGURATION['release_sha'], image_id='source:legacy-tag',
                             project_name='market-private-pipeline-primary')
        self.env.write_text(''.join(key+'='+value+'\n' for key, value in sorted(values.items())))
        self.env.chmod(0o600); self.original_values = values
        self.rows = []
        for index in range(27):
            ident = format(index + 1, '064x')
            row = {'id': ident, 'name': '/retained-'+str(index), 'image': 'sha256:'+format(index+10, '064x'),
                   'running': index != 26, 'restart_policy': {'Name': 'unless-stopped', 'MaximumRetryCount': 0},
                   'project': 'three-site-dc32d90392b0-webapp-fi' if index < 10 else 'current',
                   'network_mode': 'default', 'network_names': ['private'], 'port_bindings': {}, 'mounts': []}
            self.rows.append(row)
        self.rows[10].update(id=M.OLD_CAPTURE, image=M.BASE, project='market-private-pipeline-primary',
                             name='/market-private-pipeline-primary-market-capture-account2-1')
        self.rows[11].update(image=M.ANCHOR, project='market-private-pipeline-primary',
                             name='/market-private-pipeline-primary-market-capture-account1-1')
        self.parked = [row['id'] for row in self.rows[:10]]
        self.plan = {'machine_id': 'fixture-host', 'containers': self.rows, 'absent_control_paths': [],
                     'market_backup': {**M.PROVENANCE, 'env_file': str(self.env), 'env_file_identity': list(P.identity(self.env.stat()))},
                     'marker': {'path': str(self.marker), 'device': info.st_dev, 'inode': info.st_ino, 'sha256': M.MARKER_SHA}}
        self.write_json(self.plan_path, self.plan)
        self.stack.enter_context(mock.patch.object(M, 'PLAN_SHA', sha256(self.plan_path.read_bytes()).hexdigest()))
        self.parking = {'phase': 'qa_parked_production_incident_open', 'qa_parking_verified': True, 'parked_count': 10,
                        'retained_count': 17, 'security_transition_completed': False, 'parked_ids': self.parked}
        capture = {'schema': 'capture2_observability_activation/1', 'phase': 'diagnostic_image_active_incident_open',
                   'new_capture_id': M.CAPTURE, 'candidate_image': M.FUNCTIONAL, 'candidate_configuration_accepted': True,
                   'all_other_26_configuration_unchanged': True, 'original_plan_sha256': M.PLAN_SHA,
                   'security_transition_completed': False}
        controls = [(self.plan_path, self.plan), (self.folder/'closure.json', {'phase': 'aborted_before_install_fences_released',
                       'security_transition_completed': False}), (self.folder/'parking.json', self.parking),
                    (self.folder/'capture.json', capture)]
        for path, value in controls:
            self.write_json(path, value)
        self.stack.enter_context(mock.patch.object(M, 'CONTROLS', {path: sha256(path.read_bytes()).hexdigest() for path, _ in controls}))
        read_text = Path.read_text
        self.stack.enter_context(mock.patch.object(Path, 'read_text', lambda path, *args, **kwargs:
            'fixture-host\n' if path == Path('/etc/machine-id') else read_text(path, *args, **kwargs)))
        self.command = self.stack.enter_context(mock.patch.object(P, 'command', side_effect=self.native_metadata))
        self.clock = M.Clock((datetime.now(timezone.utc)+timedelta(minutes=5)).isoformat().replace('+00:00', 'Z'))

    def write_json(self, path, value):
        path.write_text(json.dumps(value, sort_keys=True)); path.chmod(0o600)

    def native_metadata(self, arguments):
        self.assertEqual(arguments[0], '/usr/bin/docker')
        if arguments[1:] == ['ps', '-aq', '--no-trunc']:
            return '\n'.join(M.CAPTURE if row['id'] == M.OLD_CAPTURE else row['id'] for row in self.rows)
        if arguments[1:4] == ['inspect', '--format', M.INSPECT]:
            ident = arguments[-1]
            row = deepcopy(next(row for row in self.rows if row['id'] == (M.OLD_CAPTURE if ident == M.CAPTURE else ident)))
            row.update(id=ident, restarting=False, functional=M.FUNCTIONAL_LABELS if ident == M.CAPTURE else {})
            if ident == M.CAPTURE:
                row['image'] = M.FUNCTIONAL
            if ident in self.parked:
                row.update(running=False, restart_policy={'Name': 'no', 'MaximumRetryCount': 0})
            return json.dumps(row)
        if arguments[1:5] == ['image', 'inspect', '--format', M.IMAGE]:
            ident = arguments[-1]
            return json.dumps({'id': ident, **{key: value for key, value in M.PROVENANCE.items() if key != 'image_id'},
                               'layers': ['base'] if ident != M.FUNCTIONAL else ['base', 'overlay', 'functional'],
                               'functional': M.FUNCTIONAL_LABELS if ident == M.FUNCTIONAL else {}})
        raise AssertionError('unmocked_host_command')

    def run_producer(self):
        return M.provision(P, B, R, self.clock)

    def test_real_renderer_and_validator_change_only_image_and_keep_original_metadata(self):
        original, before = self.env.read_bytes(), P.identity(self.env.stat())
        open_file = os.open
        def guarded_open(path, flags, *args, **kwargs):
            self.assertFalse(str(path).startswith('/srv/'), 'referenced_secret_or_session_opened')
            if path == self.marker:
                self.assertFalse(flags & os.O_CREAT)
            return open_file(path, flags, *args, **kwargs)
        with mock.patch.object(B, 'file_digest', side_effect=AssertionError('env_hash_forbidden')), \
                mock.patch.object(R, '_digest', side_effect=AssertionError('env_hash_forbidden')), \
                mock.patch.object(R, 'sha256', side_effect=AssertionError('payload_hash_forbidden')), \
                mock.patch.object(os, 'open', side_effect=guarded_open), \
                mock.patch.object(os, 'chmod', side_effect=AssertionError('existing_chmod_forbidden')):
            result = self.run_producer()
        self.assertTrue(result['validation_passed']); self.assertFalse(result['runtime_changed'])
        self.assertFalse(result['backup_created']); self.assertFalse(result['deployment_eligible'])
        self.assertEqual(self.env.read_bytes(), original); self.assertEqual(P.identity(self.env.stat()), before)
        self.assertEqual(B.validate_release_env(self.managed/'web.release.env', release_sha=M.CONFIGURATION['release_sha'],
            image_id=M.ANCHOR, allow_target_identity_mismatch=False, allow_private_primary_source=True),
            {**self.original_values, 'MARKET_PIPELINE_IMAGE': M.ANCHOR})
        self.assertEqual({path.name for path in self.managed.iterdir()},
                         {'web.release.env', 'web-managed-receipt.json', 'public-source-evidence.json'})
        receipt = json.loads((self.managed/'web-managed-receipt.json').read_text())
        raw = (self.managed/'public-source-evidence.json').read_bytes()
        self.assertEqual(sha256(raw).hexdigest(), receipt['caller_source_evidence_sha256'])
        self.assertEqual(receipt['configuration_source']['release_sha'], M.CONFIGURATION['release_sha'])
        self.assertEqual(receipt['source_image']['oci_revision'], M.PROVENANCE['release_sha'])
        self.assertNotEqual(receipt['configuration_source']['release_sha'], receipt['source_image']['oci_revision'])
        self.assertEqual(json.loads(raw)['configuration_source'], M.CONFIGURATION)
        self.assertEqual(raw, json.dumps(json.loads(raw), sort_keys=True, separators=(',', ':')).encode())
        self.assertNotIn('/srv/trading-bot/secure/market', raw.decode())
        self.assertNotIn('.Config.Env', M.INSPECT + M.IMAGE)
        self.assertTrue(all(call.args[0][1] in {'ps', 'inspect', 'image'} for call in self.command.call_args_list))

    def test_original_release_mismatch_refuses_without_env_or_receipt_outputs(self):
        self.env.write_text(self.env.read_text().replace(M.CONFIGURATION['release_sha'], '0'*40))
        self.plan['market_backup']['env_file_identity'] = list(P.identity(self.env.stat()))
        self.write_json(self.plan_path, self.plan)
        M.CONTROLS[self.plan_path] = sha256(self.plan_path.read_bytes()).hexdigest()
        with mock.patch.object(M, 'PLAN_SHA', M.CONTROLS[self.plan_path]), mock.patch.object(M, 'controls',
                return_value=(self.plan, self.parking, {path: P.identity(path.stat()) for path in M.CONTROLS})):
            with self.assertRaisesRegex(R.PrimaryReleaseError, 'source_release_mismatch'):
                self.run_producer()
        self.assertEqual(list(self.managed.iterdir()), [])

    def test_old_fences_are_never_created_or_adopted(self):
        for target in (self.source, self.reservation):
            target.write_bytes(b'foreign'); target.chmod(0o600)
            with self.assertRaisesRegex(M.Refusal, 'foreign_operation'):
                self.run_producer()
            self.assertEqual(target.read_bytes(), b'foreign'); target.unlink()
        self.assertFalse(self.managed.exists())

    def test_existing_managed_directory_refuses_without_chmod_or_adoption(self):
        self.managed.mkdir(mode=0o755); self.managed.chmod(0o755)
        with self.assertRaisesRegex(M.Refusal, 'destination_exists'):
            self.run_producer()
        self.assertEqual(self.managed.stat().st_mode & 0o777, 0o755)

    def test_retained_or_parked_runtime_drift_refuses_before_output(self):
        original = self.native_metadata
        for key, value in (('image', M.ANCHOR), ('restarting', True), ('running', True),
                           ('network_mode', 'host'), ('mounts', [{'Destination': 'foreign'}])):
            def changed(arguments):
                raw = original(arguments)
                if arguments[1:4] == ['inspect', '--format', M.INSPECT] and arguments[-1] == self.parked[0]:
                    row = json.loads(raw); row[key] = value; return json.dumps(row)
                return raw
            with self.subTest(key=key), mock.patch.object(P, 'command', side_effect=changed):
                with self.assertRaises((M.Refusal, KeyError)):
                    self.run_producer()
            self.assertFalse(self.managed.exists())

    def test_source_oci_and_capture_lineage_are_separate_strict_bindings(self):
        original = self.native_metadata
        for image, field, value in ((M.ANCHOR, 'release_sha', '0'*40),
                                    (M.FUNCTIONAL, 'layers', ['foreign', 'a', 'b']),
                                    (M.FUNCTIONAL, 'functional', {})):
            def changed(arguments):
                raw = original(arguments)
                if arguments[1:5] == ['image', 'inspect', '--format', M.IMAGE] and arguments[-1] == image:
                    row = json.loads(raw); row[field] = value; return json.dumps(row)
                return raw
            with self.subTest(image=image, field=field), mock.patch.object(P, 'command', side_effect=changed):
                with self.assertRaises(M.Refusal):
                    self.run_producer()
            self.assertFalse(self.managed.exists())

    def test_public_drift_after_render_prevents_success_and_partial_output_is_not_reused(self):
        profile = M.public_profile
        count = []
        def changed(*args):
            value = profile(*args); count.append(True)
            return value if len(count) == 1 else {**value, 'foreign': True}
        with mock.patch.object(M, 'public_profile', side_effect=changed):
            with self.assertRaisesRegex(M.Refusal, 'public_source_changed'):
                self.run_producer()
        with self.assertRaisesRegex(M.Refusal, 'destination_exists'):
            self.run_producer()

    def test_marker_or_control_aba_is_not_accepted_after_publication(self):
        render = R.render_web_metadata
        def changed(**arguments):
            result = render(**arguments)
            self.marker.write_bytes(self.marker.read_bytes())
            return result
        with mock.patch.object(R, 'render_web_metadata', side_effect=changed):
            with self.assertRaisesRegex(M.Refusal, 'marker_changed'):
                self.run_producer()

    def test_persisted_public_evidence_tampering_prevents_success(self):
        validate = B.validate_release_env
        def changed(*args, **kwargs):
            result = validate(*args, **kwargs)
            (self.managed/'public-source-evidence.json').write_bytes(b'{}')
            return result
        with mock.patch.object(B, 'validate_release_env', side_effect=changed):
            with self.assertRaisesRegex(P.Refusal, 'manifest_digest_changed'):
                self.run_producer()

    def test_cli_failure_is_redacted_and_deadline_is_explicit_and_bounded(self):
        for value in ('2000-01-01T00:00:00Z', (datetime.now(timezone.utc)+timedelta(minutes=11)).isoformat().replace('+00:00', 'Z')):
            with self.assertRaises(M.Refusal): M.Clock(value)
        arguments = ['--deadline-utc', (datetime.now(timezone.utc)+timedelta(minutes=5)).isoformat().replace('+00:00', 'Z')]
        for name in M.PINS: arguments += ['--'+name.removesuffix('.py').replace('_', '-'), 'public-fixture']
        output = io.StringIO()
        with mock.patch.object(M, 'load_sources', side_effect=OSError('/private/secret')), mock.patch.object(M.sys, 'stdout', output):
            self.assertEqual(M.main(arguments), 1)
        self.assertNotIn('/private', output.getvalue()); self.assertFalse(json.loads(output.getvalue())['runtime_changed'])

    def test_public_sources_are_pinned_fresh_only_and_execute_verified_buffers(self):
        sources = {name: (ROOT / ('deploy/maintenance/' if name == 'web_engine_preflight.py' else 'scripts/') / name).read_bytes()
                   for name in M.PINS}
        stage = self.folder/'public-stage'
        with mock.patch.object(M, 'STAGE', stage), mock.patch.dict(M.sys.modules):
            for name in list(M.sys.modules):
                if name == 'scripts' or name.startswith('scripts.'):
                    del M.sys.modules[name]
            preflight, backup, primary = M.load_sources(sources)
            self.assertEqual(backup.RECEIPT_SCHEMA, 'market_pipeline_backup_restore/1.4')
            self.assertEqual(primary.WEB_METADATA_SCHEMA, R.WEB_METADATA_SCHEMA)
            self.assertEqual({path.name for path in (stage/'scripts').iterdir()}, set(M.PINS)|{'__init__.py'})
            for name, data in sources.items():
                self.assertEqual((stage/'scripts'/name).read_bytes(), data)
            with self.assertRaisesRegex(M.Refusal, 'public_stage_exists'):
                M.load_sources(sources)
        with mock.patch.object(Path, 'mkdir', side_effect=AssertionError('directory_created')):
            with self.assertRaisesRegex(M.Refusal, 'public_source_changed'):
                M.load_sources({**sources, 'web_engine_preflight.py': b'changed-public-source'})


if __name__ == '__main__':
    unittest.main()
