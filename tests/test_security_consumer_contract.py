"""Pure security-release contracts. No live Docker/SSH/env/config reads."""
from contextlib import ExitStack
from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
import importlib.util
import json
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

    def test_confirmed_public_native_extra_host_assignment_representation_has_parity(self):
        self.actual['HostConfig']['ExtraHosts']=['coin.gold-trade.ir:65.109.220.59']
        self.service['extra_hosts']=['coin.gold-trade.ir=65.109.220.59']
        self.verify()
        for key,docker_key,value in (('cap_add','CapAdd',['NET_BIND_SERVICE']),('cap_drop','CapDrop',['ALL']),('security_opt','SecurityOpt',['no-new-privileges:true'])):
            self.service[key]=value;self.actual['HostConfig'][docker_key]=value.copy();self.verify()
            self.actual['HostConfig'][docker_key]=[]
            with self.assertRaisesRegex(M.Refusal,'consumer_host_policy_drift'):self.verify()
            self.actual['HostConfig'][docker_key]=value.copy()

    def test_extra_host_changed_hostname_address_or_multiplicity_is_not_normalized_away(self):
        self.actual['HostConfig']['ExtraHosts']=['coin.gold-trade.ir:65.109.220.59']
        for expected in (['changed.gold-trade.ir=65.109.220.59'],['coin.gold-trade.ir=65.109.220.60'],[],['coin.gold-trade.ir=65.109.220.59']*2):
            with self.subTest(expected=expected):
                self.service['extra_hosts']=expected
                with self.assertRaisesRegex(M.Refusal,'consumer_host_policy_drift'):self.verify()
        self.actual['HostConfig']['ExtraHosts']*=2;self.service['extra_hosts']=['coin.gold-trade.ir=65.109.220.59']*2;self.verify()
        self.service['extra_hosts']=['coin.gold-trade.ir=65.109.220.59']
        with self.assertRaisesRegex(M.Refusal,'consumer_host_policy_drift'):self.verify()

    def test_extra_host_first_separator_preserves_entire_ipv6_target(self):
        for target in ('::1','2001:db8::12','[2001:db8::12]'):
            with self.subTest(target=target):
                self.actual['HostConfig']['ExtraHosts']=['v6.example:'+target];self.service['extra_hosts']=['v6.example='+target];self.verify()
                self.service['extra_hosts']=['v6.example='+target+'0']
                with self.assertRaisesRegex(M.Refusal,'consumer_host_policy_drift'):self.verify()
        self.assertEqual(M.extra_hosts(['v6.example:::1']),[('v6.example','::1')])
        self.assertNotEqual(M.extra_hosts(['v6.example:[::1]']),M.extra_hosts(['v6.example:::1']))

    def test_compose_extra_host_mapping_accepts_only_string_or_string_list_targets(self):
        self.actual['HostConfig']['ExtraHosts']=['ordinary.example:192.0.2.1','ordinary.example:2001:db8::1']
        self.service['extra_hosts']={'ordinary.example':['192.0.2.1','2001:db8::1']};self.verify()
        self.actual['HostConfig']['ExtraHosts']=['ordinary.example:192.0.2.1'];self.service['extra_hosts']={'ordinary.example':'192.0.2.1'};self.verify()
        for value in ({'ordinary.example':None},{'ordinary.example':True},{'ordinary.example':1},{'ordinary.example':[]},{'ordinary.example':['192.0.2.1',False]},{1:'192.0.2.1'}):
            with self.subTest(value=value):
                self.service['extra_hosts']=value
                with self.assertRaisesRegex(M.Refusal,'consumer_host_policy_drift'):self.verify()

    def test_extra_hosts_unknown_or_ambiguous_shapes_refuse_without_coercion(self):
        for value in (False,True,0,1,'ordinary.example=192.0.2.1',(),[False],[1],[{}],['no-separator'],['=192.0.2.1'],['ordinary.example='],['ordinary.example=192.0.2.1=extra'],[' ordinary.example=192.0.2.1'],['ordinary.example=192.0.2.1\n']):
            with self.subTest(value=value),self.assertRaisesRegex(M.Refusal,'consumer_host_policy_drift'):M.extra_hosts(value,mapping=True)
        with self.assertRaises(M.Refusal):M.extra_hosts({'ordinary.example':'192.0.2.1'})

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

    def test_strict_serving_aliases_and_actual_static_endpoint_cannot_be_silently_reset(self):
        network=self.actual['NetworkSettings']['Networks']['trading_bot_trading_net']
        network['Aliases']=['trading_bot_app','app']
        M.assert_config_parity('bot','app',self.actual,self.rendered,self.old,self.new,strict_serving=True)
        network['Aliases'].append('unreviewed-manual-alias')
        with self.assertRaisesRegex(M.Refusal,'unreviewed_endpoint_policy'):
            M.assert_config_parity('bot','app',self.actual,self.rendered,self.old,self.new,strict_serving=True)
        network['Aliases'].pop();network['IPAMConfig']={'IPv4Address':'192.0.2.1'}
        with self.assertRaisesRegex(M.Refusal,'unreviewed_endpoint_policy'):
            M.assert_config_parity('bot','app',self.actual,self.rendered,self.old,self.new,strict_serving=True)


class SuccessorContracts(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 24, 23, 0, tzinfo=timezone.utc)
        self.record = {'schema': M.SUCCESSOR_SCHEMA, 'operation_id': M.SUCCESSOR_OPERATION,
            'status': 'approved_for_finite_conditional_activation', 'nonce': 'a' * 32,
            'source_commit': '1' * 40, 'source_tree': '2' * 40,
            'not_before_utc': '2026-10-24T22:59:00Z', 'cutoff_utc': '2026-10-24T23:30:00Z',
            'deadline_utc': '2026-10-24T23:50:00Z', 'images': {'bot': M.CANDIDATE, 'web': M.CANDIDATE},
            'candidate_source_image_id': M.CANDIDATE,
            'recovery': {'purpose': 'owned_rollback_only', 'not_before_utc': '2026-10-24T22:59:00Z',
                'deadline_utc': '2026-10-25T00:20:00Z', 'owned_targets_only': True,
                'new_forward_actions_allowed': False, 'unknown_rpc_observation_only': True},
            'baseline_images': dict(M.OLD_IMAGES), 'permissions': {'five_consumer_images': True,
                'migrations': False, 'capture': False, 'writer_dns': False, 'engine_packages': False, 'reboot': False},
            'runtime_env_delta': dict(M.RUNTIME_ENV_DELTA), 'global_security_eligible': False,
            'checkout_exception': dict(M.USER_DOCUMENT_EXCEPTION),
            'payload': {'baseline_commit': M.BASELINE, 'baseline_tree': M.BASELINE_TREE,
                        'payload_tree': M.PAYLOAD_TREE, 'manifest_sha256': M.PAYLOAD_SHA256},
            'control_files': {'/owned/marker': '7' * 64, '/owned/history': '8' * 64, '/owned/stop': '9' * 64},
            'control_identities': {name: [1, index+1, 0o100600, 0, 0, 1, 5, 10, 10]
                                   for index, name in enumerate(['/owned/marker', '/owned/history', '/owned/stop'])},
            'source_mutex': [2064, 1835555, 0o100600, 0, 0, 1, 0, 1787336928392178187, 1788695466612272194],
            'raw_gate_passed': False, 'raw_gate_waived': False, 'acl_global_library_fixed': False,
            'evidence': {name: {'path': '/owned/public/' + name + '.json', 'sha256': '3' * 64}
                         for name in M.EVIDENCE_NAMES},
            'control_sources': {name: '4' * 64 for name in M.CONTROL_SOURCES}}
        for name, pin in [('finding_dispositions', M.DISPOSITION_SHA256),
                          ('source_closures', M.SOURCE_CLOSURE_SHA256), ('payload_manifest', M.PAYLOAD_SHA256),
                          ('web_current_profile', M.WEB_COMPLETED_PLAN_SHA256),
                          ('web_engine_completion', M.WEB_ENGINE_COMPLETION_SHA256),
                          ('web_kernel_postboot', M.WEB_KERNEL_POSTBOOT_SHA256),
                          ('web_host_maintenance', M.WEB_KERNEL_COMPLETION_SHA256),
                          ('native_public_baseline', M.NATIVE_PUBLIC_BASELINE_SHA256)]:
            self.record['evidence'][name]['sha256'] = pin
        self.record['evidence']['web_artifact_admission']['sha256'] = M.WEB_ARTIFACT_ADMISSION_SHA256
        self.defaults = ['PATH=' + M.PUBLIC_PATH, 'LANG=C.UTF-8', 'GPG_KEY=' + 'A' * 40,
                         'PYTHON_VERSION=3.11.17', 'PYTHON_SHA256=' + '5' * 64]

    def test_fresh_window_requires_explicit_scoped_authority_and_cleanup_reserve(self):
        M.assert_window(self.record, self.now, activating=True)
        for key, value in [('schema', 'security_consumer_acceptance/1'),
                           ('operation_id', 'security-consumers-20261023-01'), ('nonce', 'old'),
                           ('deadline_utc', '2026-10-24T01:30:00Z'),
                           ('cutoff_utc', '2026-10-24T23:39:00Z'), ('global_security_eligible', True),
                           ('raw_gate_waived', True), ('acl_global_library_fixed', True)]:
            with self.subTest(key=key), self.assertRaises(M.Refusal):
                M.assert_window({**self.record, key: value}, self.now)
        with self.assertRaises(M.Refusal):
            M.assert_window(self.record, datetime(2026, 10, 24, 23, 31, tzinfo=timezone.utc), activating=True)
        # Read-only verification still has its separately bounded cleanup allowance.
        M.assert_window(self.record, datetime(2026, 10, 24, 23, 31, tzinfo=timezone.utc))
        after = datetime(2026, 10, 25, 0, 0, tzinfo=timezone.utc)
        with self.assertRaises(M.Refusal): M.assert_window(self.record, after)
        M.assert_window(self.record, after, recovering=True)
        with self.assertRaises(M.Refusal): M.assert_window(self.record, after, activating=True, recovering=True)

    def test_changed_candidate_static_evidence_or_permission_never_adopts_old_authority(self):
        for mutate in [lambda x: x.update(candidate_source_image_id='sha256:' + '9' * 64),
                       lambda x: x['permissions'].update(capture=True),
                       lambda x: x['permissions'].update(five_consumer_images=1),
                       lambda x: x['runtime_env_delta'].update(PYTHONPATH='/unapproved'),
                       lambda x: x['checkout_exception'].update(status='M '),
                       lambda x: x['evidence']['finding_dispositions'].update(sha256='0' * 64),
                       lambda x: x['evidence'].pop('candidate_admission')]:
            value = deepcopy(self.record); mutate(value)
            with self.assertRaises(M.Refusal): M.assert_window(value, self.now)

    def test_source_mutex_requires_exact_typed_empty_regular_root_private_metadata(self):
        for index,value in [(0,True),(1,0),(2,0o120600),(2,0o100644),(3,1000),(4,1000),(5,2),(6,1),(7,-1)]:
            record=deepcopy(self.record);record['source_mutex'][index]=value
            with self.subTest(index=index,value=value),self.assertRaisesRegex(M.Refusal,'successor_source_mutex_identity'):
                M.assert_window(record,self.now)

    def test_payload_overlay_and_controller_revision_are_independent(self):
        record = {**self.record, 'payload': {'baseline_commit': M.BASELINE, 'baseline_tree': M.BASELINE_TREE,
                    'payload_tree': M.PAYLOAD_TREE, 'manifest_sha256': M.PAYLOAD_SHA256}}
        labels = {'org.opencontainers.image.revision': M.BASELINE,
            'org.opencontainers.image.source': 'https://github.com/mohsenbarari/trading_bot',
            'io.gold-trade.release.tree': M.PAYLOAD_TREE, 'io.gold-trade.security.baseline.tree': M.BASELINE_TREE,
            'io.gold-trade.security.overlay.revision': M.PAYLOAD_OVERLAY,
            'io.gold-trade.security.overlay.tree': M.PAYLOAD_OVERLAY_TREE,
            'io.gold-trade.security.payload.sha256': M.PAYLOAD_SHA256}
        M.assert_payload_provenance(record, labels)
        with self.assertRaises(M.Refusal):
            M.assert_payload_provenance(record, {**labels, 'io.gold-trade.security.overlay.revision': record['source_commit']})

    def test_only_measured_python_metadata_default_changes_are_allowed(self):
        new = {'Config': {'Env': list(self.defaults)}};old = deepcopy(new)
        old['Config']['Env'][-2:] = ['PYTHON_VERSION=3.11.14', 'PYTHON_SHA256=' + '6' * 64]
        M.assert_loader_defaults(old, new)
        for addition in ['SSL_CERT_FILE=/synthetic/unreviewed', 'PATH=/synthetic/bin', 'EXTRA_FLAG=true']:
            changed = deepcopy(new);changed['Config']['Env'].append(addition)
            with self.assertRaises(M.Refusal): M.assert_loader_defaults(old, changed)

    def test_exact_five_commands_pid_namespace_and_loader_inputs_are_independent_checks(self):
        for site, service in M.ROLE_ORDER:
            workers = '4' if site == 'web' else '2'
            cmd = (['sh', '-c', 'exec uvicorn main:app --host 0.0.0.0 --port 8000 --workers ' + workers]
                   if service == 'app' else ['python', 'run_bot.py'] if service == 'bot'
                   else ['python', '-m', 'core.sync_worker'])
            item = {'Config': {'Cmd': cmd, 'Entrypoint': None, 'WorkingDir': '/app', 'User': '',
                              'Env': [*self.defaults, 'PYTHONDONTWRITEBYTECODE=1', 'SYNTHETIC_BUSINESS=unchanged']},
                    'HostConfig': {'PidMode': '', 'Privileged': False}}
            M.assert_role_launch(site, service, item, candidate=True)
            for key in ['PYTHONPATH', 'PYTHONHOME', 'PYTHONUSERBASE', 'LD_PRELOAD', 'LD_AUDIT', 'LD_LIBRARY_PATH']:
                value = deepcopy(item);value['Config']['Env'].append(key + '=/synthetic/unapproved')
                with self.subTest(role=(site, service), loader=key), self.assertRaises(M.Refusal) as caught:
                    M.assert_role_launch(site, service, value, candidate=True)
                self.assertNotIn('/synthetic', str(caught.exception))
            value = deepcopy(item);value['HostConfig']['PidMode'] = 'host'
            with self.assertRaises(M.Refusal): M.assert_role_launch(site, service, value, candidate=True)
            value = deepcopy(item);value['Config']['Env'] = list(self.defaults)
            with self.assertRaises(M.Refusal): M.assert_role_launch(site, service, value, candidate=True)

    def test_owned_override_adds_only_no_bytecode_flag_and_rollback_restores_original_environment(self):
        for site, service in M.ROLE_ORDER:
            value = M.target_override(site, service, M.CANDIDATE, 'always')
            self.assertEqual(value, {'services': {service: {'image': M.CANDIDATE, 'pull_policy': 'never',
                'restart': 'always', 'environment': {'PYTHONDONTWRITEBYTECODE': '1'}}}})
            old = M.target_override(site, service, M.OLD_IMAGES[site], 'always', rollback=True)
            self.assertNotIn('environment', old['services'][service])
            argv = M.target_apply_argv(site, service, '/owned/override.json')
            self.assertEqual(argv[argv.index('--force-recreate') + 1:], [service])
            self.assertIn('--no-deps', argv);self.assertIn('--no-build', argv)
        with self.assertRaises(M.Refusal): M.target_override('web', 'bot', M.CANDIDATE, 'always')

    def test_normal_data_cow_is_metadata_only_and_no_code_or_cache_extension_is_ignored(self):
        M.assert_public_diff('A /tmp/ordinary\nC /app/app_logs\nA /app/uploads/owned-placeholder\n')
        for path in ['/app/main.py', '/app/core/__pycache__/ordinary.cpython-311.pyc',
                     '/usr/local/lib/python3.11/sitecustomize.py', '/usr/bin/tar', '/tmp/../app/main.py']:
            with self.subTest(path=path), self.assertRaisesRegex(M.Refusal, 'successor_public_code_cow_changed'):
                M.assert_public_diff('A ' + path + '\n')

    def test_postload_proof_separates_native_config_id_and_web_manifest_id(self):
        layers = ['sha256:' + format(index+1, '064x') for index in range(32)]
        pin = sha256(json.dumps(layers, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        record = deepcopy(self.record);record['images']['web'] = 'sha256:' + '9' * 64
        def observation(site):
            proof = {'kind': 'Docker_config_identity', 'docker_image_id': record['images'][site],
                'config_sha256': M.CANDIDATE[7:], 'rootfs_layers': list(layers), 'security_accepted': False}
            if site == 'web':proof.update(kind='OCI_manifest_to_config', schema='runtime_image_archive_binding/1',
                scanner_config_id=M.CANDIDATE, findings_waived=False, archive_sha256='a' * 64,
                archive_bytes=10240, manifest_sha256='9' * 64)
            return {'host': M.SITES[site]['host'], 'port': M.SITES[site]['port'], 'image_loaded': True,
                'stable_before_after': True, 'daemon_image_id': record['images'][site],
                'configuration_values_disclosed': False, 'binding': proof}
        admission = {'schema': 'security_consumer_loaded_candidate/2', 'source_image_id': M.CANDIDATE,
            'config_sha256': M.CANDIDATE[7:], 'artifact_zip_sha256': M.IMAGE_ZIP_SHA256,
            'artifact_zip_bytes': M.IMAGE_ZIP_BYTES, 'rootfs_layers_sha256': pin,
            'artifact_admission_sha256': M.WEB_ARTIFACT_ADMISSION_SHA256,
            'source_tar_sha256': M.SOURCE_TAR_SHA256, 'source_tar_bytes': M.SOURCE_TAR_BYTES,
            'artifact_bytes_verified': True, 'security_accepted': False,
            'sites': {site: observation(site) for site in ('bot', 'web')}}
        with patch.object(M, 'CANDIDATE_ROOTFS_SHA256', pin):
            M.assert_window(record, self.now)
            M.assert_loaded_candidate(record, admission)
            mutations = [lambda x: x.update(schema='app8da_candidate_staging_recovery/1'),
                lambda x: x['sites']['web'].update(image_loaded=False),
                lambda x: x['sites']['bot'].update(host=M.SITES['web']['host']),
                lambda x: x['sites']['web']['binding'].update(config_sha256='b' * 64),
                lambda x: x['sites']['web']['binding'].update(manifest_sha256='c' * 64),
                lambda x: x['sites']['web']['binding']['rootfs_layers'].reverse()]
            for mutation in mutations:
                value = deepcopy(admission);mutation(value)
                with self.assertRaises(M.Refusal): M.assert_loaded_candidate(record, value)

    def test_phase_projection_preserves_protected_identity_policy_and_stopped_recovery_scope(self):
        app={'id':'a'*64,'name':'/trading_bot_app','image':M.OLD_IMAGES['web'],'running':True,
            'paused':False,'restarting':False,'project':'current','service':'app','mounts':[],
            'restart_policy':{'Name':'always','MaximumRetryCount':0},'ports':{},'network_names':['current_net'],
            'network_mode':'current_net','pid_mode':'','privileged':False,'read_only':False,
            'network_policy':{'current_net':{'Aliases':['trading_bot_app','app'],'IPAMConfig':None}}}
        protected={**deepcopy(app),'id':'b'*64,'name':'/protected-market','image':'sha256:'+'c'*64,'service':'market'}
        initial=[app,protected];current=deepcopy(initial);current[0].update(id='d'*64,image=M.CANDIDATE)
        projection={'web/app':{'id':'d'*64,'image':M.CANDIDATE}}
        M.assert_phase_inventory('web',initial,current,projection)
        for field,value in [('id','e'*64),('image','sha256:'+'f'*64),('restart_policy',{'Name':'no','MaximumRetryCount':0}),
                            ('mounts',[{'unexpected':True}])]:
            changed=deepcopy(current);changed[1][field]=value
            with self.subTest(field=field),self.assertRaisesRegex(M.Refusal,'successor_nonselected_runtime_drift'):
                M.assert_phase_inventory('web',initial,changed,projection)
        stopped=deepcopy(current);stopped[0].update(running=False,restarting=True)
        with self.assertRaises(M.Refusal):M.assert_phase_inventory('web',initial,stopped,projection)
        M.assert_phase_inventory('web',initial,stopped,projection,recovering_service='app')
        stopped[1]['running']=False
        with self.assertRaises(M.Refusal):M.assert_phase_inventory('web',initial,stopped,projection,recovering_service='app')


if __name__ == '__main__': unittest.main()
