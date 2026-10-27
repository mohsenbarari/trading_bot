"""Owned SOURCE/DTO/FS checks, never Git, Docker, SSH, SQL or real images."""
from contextlib import redirect_stdout
from copy import deepcopy
from hashlib import sha1, sha256
from pathlib import Path
from types import ModuleType
from unittest.mock import patch
import ast
import fcntl
import io
import json
import os
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def module(name):
    path = ROOT / name
    value = ModuleType('owned_' + path.stem)
    value.__file__ = str(path)
    exec(compile(path.read_bytes(), str(path), 'exec'), value.__dict__)
    return value


class RoleBuilderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.S = module('scripts/prepare_market_product_source.py')
        cls.M = module('scripts/prepare_market_capture2_image.py')
        cls.B = module('scripts/market_business81.py')
        cls.O = module('scripts/market_business81_outer.py')
        cls.R = module('deploy/market-data/business81/run_business81.py')
        cls.P = module('scripts/verify_market_role_product_source.py')
        cls.catalog = (ROOT / cls.S.ROLE_CATALOG_PATH).read_bytes()
        cls.legacy_reader = module('scripts/verify_market_product_source.py')
        cls.F = module('tests/market_security_source_fixtures.py')

    def row(self, name):
        raw = (ROOT / name).read_bytes()
        return {'path': name, 'mode': '100644', 'bytes': len(raw),
                'sha256': sha256(raw).hexdigest(),
                'git_blob': sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()}

    def receipt(self, role):
        # Release object IDs are labelled DTO placeholders. The business IDs
        # and every expected business row are the actual published identities.
        selected = self.S.role_data(self.catalog, role)
        spec = self.S.ROLE_MAP[role]
        locked = {'business_commit': spec['business_commit'],
                  'business_tree': spec['business_tree'],
                  'repository_identity': {'host': 'github.com', 'repository': 'mohsenbarari/trading_bot'},
                  'role': role, 'catalog_sha256': self.S.ROLE_CATALOG_SHA,
                  'source_policy': 'whole_git_component_no_main_fallback'}
        value = {'schema': 'normal_market_git_component_inputs/2',
                 'component': spec['component'], 'role': role,
                 'release_commit': 'b' * 40, 'release_tree': 'c' * 40,
                 'business_commit': spec['business_commit'],
                 'business_tree': spec['business_tree'],
                 'repository_identity': locked['repository_identity'],
                 'product_lock_sha256': sha256(self.S.encoded(locked)).hexdigest(),
                 'business_manifest_sha256': spec['manifest_sha256'],
                 'product_files': selected['product_files'],
                 'release_files': [self.row(name) for name in sorted(self.S.release_files(role))],
                 'role_source': deepcopy(spec), 'role_catalog_sha256': self.S.ROLE_CATALOG_SHA,
                 'signature_format': 'normal-market-two-git-sources-v1',
                 'live_remote_main_gate_performed': False,
                 'activation_authority': False, 'security_accepted': False}
        return value, sha256(self.S.encoded(value)).hexdigest()

    def historical(self, role=None):
        names = (*self.M.CONTROLLER_FILES,
                 *((self.M.ROLE_READER, self.M.ROLE_CATALOG_PATH) if role else ()))
        raws = {name: (ROOT / name).read_bytes() for name in names}
        if role:
            receipt, signature = self.receipt(role)
        else:
            receipt = {'release_commit': 'b' * 40,
                       'release_files': [self.row(name) for name in
                                        ('deploy/market-data/Dockerfile.capture2',
                                         'deploy/market-data/accepted-f79-business.manifest.json')]}
            signature = 'c' * 64
        return self.M.historical81_sources(raws, ROOT, receipt,
                                           'sha256:' + 'a' * 64, signature, ROOT)

    def observations(self, plans, manifest_sha):
        # Inert DTOs exercise strict joins, never real unit completions.
        result = {}
        for profile, plan in plans.items():
            value = {'schema': 'normal_market_business81_profile_result/2',
                     **{key: plan[key] for key in ('image_id', 'release_commit',
                          'business_commit', 'business_tree', 'two_git_input_signature')},
                     'test_sources_sha256': manifest_sha, 'profile': profile,
                     'execution_uid': plan['uid'], 'execution_gid': plan['gid'],
                     'discarded_fixture_stdout_bytes': 0,
                     'selected_test_ids': sorted(plan['test_ids']),
                     'tests': plan['expected_test_count'],
                     **{key: 0 for key in ('skips', 'expected_failures', 'unexpected_successes', 'failures', 'errors')},
                     'status': 'passed', 'actual_candidate_source_verified': True,
                     'candidate_source_files': plan['candidate_product']['role_source']['count'],
                     'candidate_manifest_sha256': plan['candidate_product']['role_source']['manifest_sha256'],
                     'fixture_business_provenance': self.B.fixture_business_provenance(),
                     'runtime_data_used': False, 'business_data_freshness_proven': False,
                     'security_accepted': False}
            result[profile] = {'actual_outer_returncode': 0,
                               'actual_container_exit_code': 0, 'observation': value}
        return result

    def test_exact_catalog_and_all_five_consumer_contracts(self):
        for consumer in (self.S, self.M, self.B, self.R, self.P):
            self.assertEqual(consumer.ROLE_MAP, self.S.ROLE_MAP)
            for role in self.S.ROLE_MAP:
                manifest = consumer.role_data(self.catalog, role)
                self.assertEqual(consumer.ROLE_MAP[role]['count'], len(manifest['product_files']))
            with self.assertRaises(Exception):
                consumer.role_data(self.catalog + b' ', 'native/coin-estimator')

    def test_all_nine_real_component_receipts_build_closed_source_contracts(self):
        for role, spec in self.S.ROLE_MAP.items():
            receipt, signature = self.receipt(role)
            self.S.input_receipt(receipt, signature)
            self.M.require_selected(receipt, role)
            contract = self.S.build_contract(receipt, signature, Path('/OWNED-NOT-OPENED/sealed'))
            argv = self.M.build_argv(Path('/OWNED-NOT-OPENED'), contract,
                                     'd' * 64, 1793085646, '123', '1', role=role)
            self.assertIn('BUSINESS_SOURCE_COMMIT=' + spec['business_commit'], argv)
            self.assertIn('BUSINESS_SOURCE_MANIFEST_SHA256=' + spec['manifest_sha256'], argv)
            self.assertEqual(len(receipt['release_files']), 21)
            self.assertNotIn('--secret', argv)

    def test_receipt_mutation_cannot_relabel_f79_or_different_component(self):
        role = 'native/coin-estimator'
        original, _ = self.receipt(role)
        changes = [('business_commit', 'a' * 40), ('business_tree', 'a' * 40),
                   ('business_manifest_sha256', self.S.MANIFEST_SHA),
                   ('product_lock_sha256', 'f' * 64), ('role_catalog_sha256', 'f' * 64),
                   ('product_files', json.loads((ROOT / self.S.MANIFEST_PATH).read_bytes())['product_files'])]
        for key, value in changes:
            bad = deepcopy(original)
            bad[key] = value
            with self.assertRaises(Exception):
                self.S.input_receipt(bad, sha256(self.S.encoded(bad)).hexdigest())
        with self.assertRaises(Exception):
            self.M.require_selected(original, None)
        with self.assertRaises(Exception):
            self.M.require_selected(original, 'web/market-processor')

    def test_shared_source_still_requires_exact_selected_role(self):
        receipt, _ = self.receipt('native/estimator-snapshot-sender')
        self.assertEqual(self.S.ROLE_MAP['native/estimator-snapshot-sender'],
                         self.S.ROLE_MAP['web/market-capture-external'])
        with self.assertRaises(Exception):
            self.M.require_selected(receipt, 'web/market-capture-external')

    def test_controller_and_complete_parcel_counts_follow_exact_role(self):
        for role, spec in self.S.ROLE_MAP.items():
            receipt, signature = self.receipt(role)
            controllers = [self.row(name) for name in sorted(self.M.controller_files(role))]
            # Inert metadata tests the controller/parcel codec only. The real
            # authentic Coreutils lock independently gates actual preparation.
            cache = {'pip_packages/OWNED-DTO-ONLY/cache-' + str(i):
                     {'bytes': 1, 'sha256': sha256(b'X').hexdigest()} for i in range(26)}
            value, _ = self.M.receipt_contract(receipt, signature, controllers, cache, 1793085646)
            parcel = self.M.controller_parcel_inputs(Path('/OWNED-NOT-OPENED'), receipt,
                                                     controllers, cache)
            self.assertFalse(value['activation_authority'])
            self.assertEqual(len(parcel), 21 + spec['count'] + len(controllers) + 26)
            self.assertIn('controller/' + self.M.ROLE_READER, parcel)
            self.assertIn('release/' + self.M.ROLE_CATALOG_PATH, parcel)

    def test_probe_plan_has_two_exact_readonly_public_mounts_and_actual_role(self):
        root = Path('/OWNED-NOT-OPENED')
        for role in self.M.ROLE_MAP:
            plan = self.M.probe_plan(root, 'sha256:' + 'a' * 64, 'b' * 64, role=role)
            self.assertNotIn('source93', plan)
            argv = plan['source-role']['argv']
            self.assertEqual(argv[-2:], ['--role', role])
            self.assertEqual(sum(value.startswith('type=bind,') for value in argv), 2)
            self.assertNotIn('--env', argv)
            self.assertEqual(plan['source-role']['user'], '10001:10001')

    def test_owned_unapproved_assertion_refuses_in_real_cheap_source_preflight(self):
        raws = {name: (ROOT / name).read_bytes() for name in self.M.CONTROLLER_FILES}
        with self.F.owned_unapproved_preflight_sources(self.M, raws) as negative, patch.object(subprocess, 'Popen', side_effect=AssertionError('no command')):
            with self.assertRaisesRegex(RuntimeError, 'business81_assertion_approval'):
                self.M.preflight_assertion_sources(negative)

    def test_typed_role_source_probe_rejects_old_schema_boolean_count_and_wrong_scope(self):
        role = 'native/coin-estimator'
        receipt, _ = self.receipt(role)
        spec = self.M.ROLE_MAP[role]
        value = {'schema': 'market_role_public_source_readonly_observation/1',
                 'status': 'match', 'role': role,
                 'business_commit': spec['business_commit'], 'business_tree': spec['business_tree'],
                 'expected_manifest_sha256': spec['manifest_sha256'],
                 'expected_rows_sha256': spec['rows_sha256'],
                 'role_catalog_sha256': self.M.ROLE_CATALOG_SHA,
                 'source_files': spec['count'], 'excluded_cache_metadata_files': 0,
                 'all_source_rows_match': True, 'source_only_nonatomic_observation': True,
                 **{key: False for key in ('project_cache_bodies_read', 'unknown_noncache_files_admitted',
                     'project_imported', 'already_running_code_proven', 'business_data_freshness_proven', 'security_accepted')}}
        self.M.validate_source_probe(value, receipt)
        for key, wrong in (('schema', 'market_public_source93_readonly_observation/1'),
                           ('source_files', True), ('source_files', 93),
                           ('role', 'native/market-fact-receiver'),
                           ('expected_manifest_sha256', self.M.MANIFEST_SHA),
                           ('security_accepted', True), ('extra', 'not-admitted')):
            with self.assertRaises(Exception):
                self.M.validate_source_probe({**value, key: wrong}, receipt)

    def test_all_role_bundles_preserve_original81_body_selection_and_fixture_provenance(self):
        _, _, legacy_contents, legacy_manifest, legacy_plans = self.historical()
        for role in self.M.ROLE_MAP:
            _, _, contents, manifest, plans = self.historical(role)
            self.assertEqual(manifest['schema'], 'normal_market_business81_sources/3')
            self.assertEqual(manifest['fixture_business_provenance'], self.B.fixture_business_provenance())
            self.assertNotIn('business_commit', manifest)
            self.assertEqual(manifest['candidate_product']['role'], role)
            self.assertEqual(sum(plan['expected_test_count'] for plan in plans.values()), 81)
            self.assertEqual(manifest['files'], legacy_manifest['files'])
            for row in manifest['files']:
                self.assertEqual(contents[row['path']], legacy_contents[row['path']])
            for profile in plans:
                self.assertEqual(plans[profile]['test_ids'], legacy_plans[profile]['test_ids'])
                self.assertEqual(plans[profile]['business_commit'], self.M.ROLE_MAP[role]['business_commit'])
            self.assertFalse(manifest['security_accepted'])

    def test_inert_typed_profile_joins_do_not_waive_tests_or_claim_real_outer_execution(self):
        role = 'native/market-fact-receiver'
        B, _, contents, _, plans = self.historical(role)
        selection = json.loads(contents['f79-business-test-selection.json'])
        digest = sha256(contents['suite-manifest.json']).hexdigest()
        observations = self.observations(plans, digest)
        result = B.join_actual_results(selection, plans, observations,
                                       expected_manifest_sha256=digest)
        self.assertEqual(result['schema'], 'normal_market_historical81_business_tests/2')
        self.assertEqual(result['business_commit'], self.M.ROLE_MAP[role]['business_commit'])
        self.assertEqual(result['fixture_business_provenance'], B.fixture_business_provenance())
        self.assertEqual(result['tests'], 81)
        self.assertTrue(result['actual_outer_authentication_and_cleanup_still_required'])
        for key, wrong in (('schema', 'normal_market_business81_profile_result/1'),
                           ('candidate_source_files', True), ('candidate_source_files', 93),
                           ('candidate_manifest_sha256', B.BUSINESS_SHA), ('skips', 1), ('failures', 1)):
            bad = deepcopy(observations)
            bad['base-nonroot']['observation'][key] = wrong
            with self.assertRaises(Exception):
                B.join_actual_results(selection, plans, bad,
                                       expected_manifest_sha256=digest)

    def test_legacy_f79_join_keeps_original_schema_and_identity(self):
        B, _, contents, _, plans = self.historical()
        selection = json.loads(contents['f79-business-test-selection.json'])
        digest = sha256(contents['suite-manifest.json']).hexdigest()
        result = {}
        for profile, plan in plans.items():
            observed = {'schema': 'normal_market_business81_profile_result/1',
                        **{key: plan[key] for key in ('image_id', 'release_commit',
                             'business_commit', 'business_tree', 'two_git_input_signature')},
                        'test_sources_sha256': digest, 'profile': profile,
                        'execution_uid': plan['uid'], 'execution_gid': plan['gid'],
                        'discarded_fixture_stdout_bytes': 0,
                        'selected_test_ids': sorted(plan['test_ids']),
                        'tests': plan['expected_test_count'],
                        **{key: 0 for key in ('skips', 'expected_failures', 'unexpected_successes', 'failures', 'errors')},
                        'status': 'passed', 'actual_candidate_source93_verified': True,
                        'runtime_data_used': False, 'business_data_freshness_proven': False,
                        'security_accepted': False}
            result[profile] = {'actual_outer_returncode': 0,
                               'actual_container_exit_code': 0, 'observation': observed}
        joined = B.join_actual_results(selection, plans, result,
                                        expected_manifest_sha256=digest)
        self.assertEqual(joined['schema'], 'normal_market_historical81_business_tests/1')
        self.assertEqual(joined['business_commit'], B.BUSINESS_COMMIT)
        self.assertNotIn('candidate_product', joined)
        self.assertTrue(joined['actual_outer_authentication_and_cleanup_still_required'])

    def test_original_source_inspector_body_is_byte_exact_and_owned_fs_still_detects_extra(self):
        def body(name):
            text = (ROOT / name).read_text()
            node = next(n for n in ast.parse(text).body
                        if isinstance(n, ast.FunctionDef) and n.name == '_inspect')
            return ast.get_source_segment(text, node)
        self.assertEqual(body('scripts/verify_market_product_source.py'),
                         body('scripts/verify_market_role_product_source.py'))
        with tempfile.TemporaryDirectory(prefix='.owned-role-source-', dir=ROOT / 'tests') as temporary:
            root = Path(temporary)
            names = ['core/__init__.py', 'core/market_intelligence/owned.py',
                     'config/market_data_sources.v1.json', 'contracts/market_data/owned.json',
                     'deploy/market-data/migrations/owned.sql']
            rows = []
            for name in names:
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b'OWNED PUBLIC')
                path.chmod(0o644)
                rows.append({'path': name, 'mode': '100644', 'bytes': 12,
                             'sha256': sha256(b'OWNED PUBLIC').hexdigest()})
            owner = (os.geteuid(), os.getegid())
            self.assertEqual(self.P._inspect(root, rows, owner)['source_files'], 5)
            (root / 'core/market_intelligence/unadmitted.py').write_bytes(b'EXTRA')
            with self.assertRaises(Exception):
                self.P._inspect(root, rows, owner)

    def test_new_public_catalog_retains_reader_before_actual_writer_close_and_refuses_late_change(self):
        with tempfile.TemporaryDirectory(prefix='.owned-role-proof-', dir=ROOT / 'tests') as temporary:
            path = Path(temporary) / 'catalog'
            retained = []
            close = os.close
            triggered = []
            def check():
                for row in retained:
                    row.check()
            def physical(fd):
                chosen = bool(retained) and (fcntl.fcntl(fd, fcntl.F_GETFL) & os.O_ACCMODE) == os.O_WRONLY
                if chosen:
                    self.assertIsNotNone(retained[0].fd)
                    retained[0].check()
                close(fd)
                if chosen:
                    triggered.append(True)
                    before = path.stat()
                    os.utime(path, ns=(before.st_mtime_ns + 1, before.st_mtime_ns + 1))
            with patch.object(self.M.os, 'close', side_effect=physical), self.assertRaises(Exception):
                self.M.public_probe_file(path, b'OWNED PUBLIC', retained, check)
            self.assertEqual(triggered, [True])
            for row in retained:
                if row.fd is not None:
                    with self.assertRaises(Exception):
                        row.close()

    def test_unarmed_all_roles_and_workflow_choice_propagation_have_no_commands(self):
        with patch.object(self.M, 'EXECUTION_ARMED', False), patch.object(subprocess, 'Popen', side_effect=AssertionError('no commands')):
            for role in self.M.ROLE_MAP:
                with self.assertRaisesRegex(RuntimeError, 'normal_main_integration_unarmed'):
                    self.M.main(['--execute', '--role', role])
            with redirect_stdout(io.StringIO()) as output:
                self.M.main([])
            self.assertEqual(json.loads(output.getvalue())['status'], 'UNARMED')
            with self.assertRaises(Exception):
                self.P.observe_role('native/coin-estimator')
        workflow = (ROOT / '.github/workflows/market-capture2-image.yml').read_text()
        self.assertIn('default: capture2-f79', workflow)
        self.assertEqual(workflow.count("sys.argv+=['--role',os.environ['NORMAL_MARKET_ROLE']]"), 4)
        for role in self.M.ROLE_MAP:
            self.assertIn('- ' + role, workflow)
        scripts = workflow.split("python3 -I -S -B - <<'PY'\n")[1:]
        self.assertEqual(len(scripts), 4)
        for script in scripts:
            body = script.split('\n          PY', 1)[0]
            ast.parse('\n'.join(line[10:] if line.startswith(' ' * 10) else line
                                for line in body.splitlines()))


if __name__ == '__main__':
    unittest.main()
