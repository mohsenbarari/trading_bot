"""Pure security-release contracts. No live Docker/SSH/env/config reads."""
from contextlib import ExitStack
from copy import deepcopy
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

PATH = Path(__file__).resolve().parents[1] / 'deploy/maintenance/security_consumer_contract.py'
SPEC = importlib.util.spec_from_file_location('_security_consumer_contract_test', PATH)
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)


class ConsumerContracts(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        def denied(*args, **kwargs): raise AssertionError('unexpected command/network')
        for name in ('subprocess.run', 'subprocess.Popen', 'os.system', 'socket.socket.connect',
                     'socket.getaddrinfo', 'socket.create_connection', 'urllib.request.urlopen'):
            self.stack.enter_context(patch(name, side_effect=denied))
        self.old = {'Config': {'Env': ['LANG=C.UTF-8', 'PYTHON_VERSION=3.11.14'], 'Cmd': ['python3'],
                               'Entrypoint': None, 'WorkingDir': '/app', 'User': '', 'StopSignal': ''}}
        self.new = deepcopy(self.old)
        self.new['Config']['Env'] = ['LANG=C.UTF-8', 'PYTHON_VERSION=3.11.17']
        self.env = {'PRODUCT_ESTIMATOR_SNAPSHOT_MODE': 'LEGACY',
                    'COIN_INTELLIGENCE_INFERENCE_AUTO_SELECTION_ENABLED': 'false',
                    'SYNTHETIC_SECRET': 'never-emit-this-synthetic-value'}
        self.service = {'container_name': 'trading_bot_app', 'command': ['uvicorn', 'main:app'],
                        'environment': self.env, 'restart': 'no',
                        'ports': [{'target': 8000, 'published': '8000', 'host_ip': '127.0.0.1', 'protocol': 'tcp'}],
                        'volumes': [{'type': 'bind', 'source': '/srv/snapshots', 'target': p, 'read_only': True}
                                    for p in sorted(M.SNAPSHOTS)], 'networks': {'trading_net': None}}
        self.rendered = {'services': {'app': self.service}, 'networks': {'trading_net': {'name': 'trading_bot_trading_net'}}}
        labels = {'com.docker.compose.project': 'trading_bot', 'com.docker.compose.service': 'app',
                  'com.docker.compose.project.config_files': ','.join(M.SITES['bot']['files']),
                  'com.docker.compose.project.working_dir': M.SITES['bot']['workdir']}
        self.actual = {'Id': '1' * 64, 'Name': '/trading_bot_app', 'Image': 'sha256:' + '2' * 64,
                       'State': {'Running': True}, 'Config': {'Labels': labels, 'Cmd': ['uvicorn', 'main:app'],
                        'Entrypoint': None, 'Env': [*self.old['Config']['Env'], *[k + '=' + v for k, v in self.env.items()]],
                        'WorkingDir': '/app', 'User': '', 'StopSignal': ''},
                       'HostConfig': {'RestartPolicy': {'Name': 'no', 'MaximumRetryCount': 0},
                                      'PortBindings': {'8000/tcp': [{'HostIp': '127.0.0.1', 'HostPort': '8000'}]}},
                       'Mounts': [{'Type': 'bind', 'Source': '/srv/snapshots', 'Destination': p, 'RW': False}
                                  for p in sorted(M.SNAPSHOTS)],
                       'NetworkSettings': {'Networks': {'trading_bot_trading_net': {}}}}

    def verify(self):
        return M.assert_config_parity('bot', 'app', self.actual, self.rendered, self.old, self.new)

    def test_real_finite_whitelist_three_native_two_web(self):
        self.assertEqual(set(M.SITES['bot']['services']), {'app', 'bot', 'sync_worker'})
        self.assertEqual(set(M.SITES['web']['services']), {'app', 'sync_worker'})
        self.assertEqual(M.SITES['web']['project'], 'current')
        self.assertNotEqual(M.SITES['web']['project'], M.SITES['bot']['project'])

    def test_security_overlay_labels_distinguish_baseline_payload_and_published_recipe(self):
        record = {'source_commit': 'a' * 40, 'source_tree': 'b' * 40,
                  'payload': {'baseline_commit': M.BASELINE, 'baseline_tree': M.BASELINE_TREE,
                              'payload_tree': 'c' * 40, 'manifest_sha256': 'd' * 64}}
        labels = {'org.opencontainers.image.revision': M.BASELINE,
                  'org.opencontainers.image.source': 'https://github.com/mohsenbarari/trading_bot',
                  'io.gold-trade.release.tree': 'c' * 40,
                  'io.gold-trade.security.baseline.tree': M.BASELINE_TREE,
                  'io.gold-trade.security.overlay.revision': 'a' * 40,
                  'io.gold-trade.security.overlay.tree': 'b' * 40,
                  'io.gold-trade.security.payload.sha256': 'd' * 64}
        M.assert_payload_provenance(record, labels)
        for name in labels:
            with self.subTest(label=name), self.assertRaises(M.Refusal):
                M.assert_payload_provenance(record, {**labels, name: 'wrong'})
        with self.assertRaises(M.Refusal):
            M.assert_payload_provenance(record, {'org.opencontainers.image.revision': record['source_commit'],
                                                 'io.gold-trade.release.tree': record['source_tree']})
        with self.assertRaises(M.Refusal): M.assert_payload_provenance({**record, 'payload': None}, labels)
        with self.assertRaises(M.Refusal):
            M.assert_payload_provenance({**record, 'payload': {**record['payload'], 'baseline_commit': 'e' * 40}}, labels)

    def test_noop_configuration_with_security_python_refresh_preserves_env_and_contract(self):
        result = self.verify()
        self.assertEqual(set(result), {'id', 'name', 'image', 'project', 'service'})
        self.assertNotIn(self.env['SYNTHETIC_SECRET'], repr(result))

    def test_secrets_never_appear_in_failure_diagnostics(self):
        self.actual['Config']['Env'].append('EXTRA_SECRET=another-never-emit')
        with self.assertRaises(M.Refusal) as caught: self.verify()
        self.assertEqual(str(caught.exception), 'application_environment_drift')
        self.assertNotIn('never-emit', str(caught.exception))

    def test_market_staging_legacy_and_wrong_project_are_not_targets(self):
        for name, project, service in (('/market-private-pipeline-primary-market-processor-1', 'market-private-pipeline-primary', 'market-processor'),
                                       ('/trading_bot_staging-app-1', 'trading_bot_staging', 'app'),
                                       ('/three-site-old-webapp-fi_api-1', 'three-site-old', 'webapp_fi_api'),
                                       ('/trading_bot_app', 'current', 'app')):
            value = deepcopy(self.actual)
            value['Name'] = name
            value['Config']['Labels'].update({'com.docker.compose.project': project, 'com.docker.compose.service': service})
            with self.subTest(name=name), self.assertRaises(M.Refusal): M.assert_target('bot', value)

    def test_changed_compose_source_or_workingdir_refused(self):
        for key in ('com.docker.compose.project.config_files', 'com.docker.compose.project.working_dir'):
            value = deepcopy(self.actual)
            value['Config']['Labels'][key] = '/tmp/unreviewed'
            with self.subTest(key=key), self.assertRaises(M.Refusal): M.assert_target('bot', value)

    def test_only_explicit_owned_override_provenance_is_accepted(self):
        override = '/root/private/security.override.json'
        self.actual['Config']['Labels']['com.docker.compose.project.config_files'] += ',' + override
        with self.assertRaises(M.Refusal): self.verify()
        M.assert_config_parity('bot', 'app', self.actual, self.rendered, self.old, self.new,
                               managed_override=override)
        with self.assertRaises(M.Refusal):
            M.assert_target('bot', self.actual, managed_override='/root/foreign.override.json')

    def test_stopped_owned_partial_consumer_requires_explicit_rollback_mode(self):
        self.actual['State']['Running'] = False
        with self.assertRaisesRegex(M.Refusal, 'consumer_not_baseline_running'): self.verify()
        M.assert_config_parity('bot', 'app', self.actual, self.rendered, self.old, self.new, running=False)

    def test_market_mode_or_auto_selection_can_never_activate(self):
        for key, value in (('PRODUCT_ESTIMATOR_SNAPSHOT_MODE', 'PRIVATE_PRIMARY'),
                           ('COIN_INTELLIGENCE_INFERENCE_AUTO_SELECTION_ENABLED', 'true')):
            with self.subTest(key=key):
                previous = self.env[key]
                self.env[key] = value
                self.actual['Config']['Env'] = [*self.old['Config']['Env'], *[k + '=' + v for k, v in self.env.items()]]
                with self.assertRaisesRegex(M.Refusal, 'market_behavior_not_frozen'): self.verify()
                self.env[key] = previous

    def test_candidate_image_cannot_add_application_flag_defaults(self):
        self.new['Config']['Env'].append('NEW_MARKET_WRITER_ENABLED=true')
        with self.assertRaisesRegex(M.Refusal, 'image_application_defaults_drift'): self.verify()

    def test_command_or_default_entrypoint_drift_refused(self):
        self.service['command'] = ['python', 'scripts/activate_producer.py']
        with self.assertRaisesRegex(M.Refusal, 'consumer_command_drift'): self.verify()
        self.service['command'] = ['uvicorn', 'main:app']
        self.new['Config']['Entrypoint'] = ['python', 'scripts/migrate.py']
        with self.assertRaisesRegex(M.Refusal, 'image_default_command_drift'): self.verify()

    def test_snapshot_mount_cannot_be_missing_relocated_or_writable(self):
        self.actual['Mounts'][0]['RW'] = True
        with self.assertRaisesRegex(M.Refusal, 'consumer_mount_drift'): self.verify()

    def test_changing_both_rendered_and_actual_to_rw_still_not_accepted(self):
        self.actual['Mounts'][0]['RW'] = True
        self.service['volumes'][0]['read_only'] = False
        with self.assertRaisesRegex(M.Refusal, 'snapshot_not_readonly'): self.verify()

    def test_bind_propagation_and_nested_volume_options_cannot_silently_change(self):
        self.actual['Mounts'][0]['Propagation'] = 'rshared'
        with self.assertRaisesRegex(M.Refusal, 'consumer_bind_propagation_drift'): self.verify()
        self.actual['Mounts'][0].pop('Propagation')
        self.service['volumes'][0]['volume'] = {'subpath': 'unreviewed'}
        with self.assertRaisesRegex(M.Refusal, 'unsupported_volume_options'): self.verify()

    def test_new_port_or_network_refused(self):
        self.service['ports'][0]['host_ip'] = '0.0.0.0'
        with self.assertRaisesRegex(M.Refusal, 'consumer_port_drift'): self.verify()
        self.service['ports'][0]['host_ip'] = '127.0.0.1'
        self.rendered['networks']['trading_net']['name'] = 'unreviewed-network'
        with self.assertRaisesRegex(M.Refusal, 'consumer_network_drift'): self.verify()

    def test_logging_and_ulimits_are_preserved_not_silently_reset(self):
        self.service['ulimits'] = {'nofile': {'soft': 65536, 'hard': 65536}}
        self.service['logging'] = {'driver': 'json-file', 'options': {'max-size': '20m', 'max-file': '5'}}
        with self.assertRaisesRegex(M.Refusal, 'consumer_ulimit_drift'): self.verify()
        self.actual['HostConfig']['Ulimits'] = [{'Name': 'nofile', 'Soft': 65536, 'Hard': 65536}]
        with self.assertRaisesRegex(M.Refusal, 'consumer_logging_drift'): self.verify()
        self.actual['HostConfig']['LogConfig'] = {'Type': 'json-file', 'Config': {'max-size': '20m', 'max-file': '5'}}
        self.verify()

    def test_manual_resource_overrides_refuse_instead_of_resetting_them(self):
        for key, value in (('Memory', 1024), ('NanoCpus', 1000), ('PidsLimit', 256), ('Runtime', 'unreviewed')):
            with self.subTest(key=key):
                self.actual['HostConfig'][key] = value
                with self.assertRaises(M.Refusal): self.verify()
                self.actual['HostConfig'].pop(key)
        self.service['mem_limit'] = '1g'
        with self.assertRaisesRegex(M.Refusal, 'resource_override_requires_separate_review'): self.verify()

    def test_privilege_static_endpoint_and_hook_changes_refused(self):
        for key, value in (('privileged', True), ('post_start', [{'command': 'migrate'}]),
                           ('pid', 'host'), ('secrets', ['extra'])):
            with self.subTest(key=key):
                self.service[key] = value
                with self.assertRaises(M.Refusal): self.verify()
                self.service.pop(key)
        self.service['networks']['trading_net'] = {'ipv4_address': '10.0.0.8'}
        with self.assertRaisesRegex(M.Refusal, 'static_endpoint_requires_separate_review'): self.verify()

    def test_image_override_only_exact_services_immutable_digest_and_never_pull(self):
        for site in ('bot', 'web'):
            result = M.image_only_override(site, 'sha256:' + 'f' * 64)
            self.assertEqual(set(result['services']), set(M.SITES[site]['services']))
            self.assertTrue(all(set(value) == {'image', 'pull_policy'} and value['pull_policy'] == 'never'
                                for value in result['services'].values()))
        with self.assertRaises(M.Refusal): M.image_only_override('web', 'latest')

    def test_apply_command_has_no_dependencies_build_pull_migration_market_or_staging(self):
        for site in ('bot', 'web'):
            command = M.compose_apply_argv(site, '/root/secure/security-consumer.override.json')
            self.assertIn('--no-deps', command)
            self.assertIn('--no-build', command)
            self.assertEqual(command[command.index('--pull') + 1], 'never')
            after = command[command.index('--force-recreate') + 1:]
            self.assertEqual(set(after), set(M.SITES[site]['services']))
            self.assertNotIn('migration', after)
            self.assertNotIn('db', after)
            self.assertNotIn('redis', after)


if __name__ == '__main__': unittest.main()
