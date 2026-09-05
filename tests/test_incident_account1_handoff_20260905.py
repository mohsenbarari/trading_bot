import copy
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('handoff',Path(__file__).resolve().parents[1] / 'scripts/incident_account1_handoff_20260905.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

spec_liveness = importlib.util.spec_from_file_location(
    'liveness_handoff',
    Path(__file__).resolve().parents[1] / 'scripts/incident_account1_liveness_handoff_20260905.py',
)
l = importlib.util.module_from_spec(spec_liveness)
spec_liveness.loader.exec_module(l)

class Guards(unittest.TestCase):
    def configs(self):
        old = {'services':{m.ROLE:{'image':m.OLD_IMAGE,'environment':{
            'MARKET_PIPELINE_RELEASE_SHA':m.OLD,'MARKET_CAPTURE_BACKFILL_NOT_BEFORE_UTC':'',
            'MARKET_CAPTURE_BACKFILL_SOURCE_CODES':''},'labels':{'org.opencontainers.image.revision':m.OLD}},
            'market-capture-account2':{'image':'untouched'}}}
        new = copy.deepcopy(old)
        new['services'][m.ROLE]['image'] = m.NEW_IMAGE
        new['services'][m.ROLE]['environment']['MARKET_PIPELINE_RELEASE_SHA'] = m.NEW
        new['services'][m.ROLE]['labels']['org.opencontainers.image.revision'] = m.NEW
        return old,new

    def test_only_approved_delta_passes(self):
        m.validate_config(*self.configs())

    def test_bystander_mutation_rejected(self):
        a,b = self.configs()
        b['services']['market-capture-account2']['image'] = 'changed'
        with self.assertRaisesRegex(RuntimeError,'unrelated_service_drift'):
            m.validate_config(a,b)

    def test_target_behavior_mutation_rejected(self):
        a,b = self.configs()
        b['services'][m.ROLE]['environment']['MARKET_CAPTURE_BACKFILL_SOURCE_CODES'] = 'XAUUSD'
        with self.assertRaisesRegex(RuntimeError,'unexpected_target_config_drift'):
            m.validate_config(a,b)

    def test_atomic_replacement_retains_mode_and_content(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)/'journal'
            m.atomic(p,{'status':'PREPARED'},os.getuid(),os.getgid())
            m.file_check(p,os.getuid())
            m.atomic(p,{'status':'TRANSFERRED'},os.getuid(),os.getgid())
            self.assertEqual(json.loads(p.read_text()),{'status':'TRANSFERRED'})

    def test_lock_excludes_second_owner_without_truncating(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)/'lock'
            m.atomic(p,{'binding':'retain'},os.getuid(),os.getgid())
            before = p.read_bytes()
            with m.held(p,os.getuid()):
                with self.assertRaises(BlockingIOError):
                    with m.held(p,os.getuid()):
                        self.fail('duplicate owner')
            self.assertEqual(p.read_bytes(),before)

    def test_symlink_lock_refused(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)/'lock'
            m.atomic(p,{},os.getuid(),os.getgid())
            s = Path(d)/'symlink'
            s.symlink_to(p)
            with self.assertRaisesRegex(RuntimeError,'symlink_path'):
                with m.held(s,os.getuid()):
                    self.fail('symlink accepted')

    def test_liveness_handoff_allows_only_the_account1_probe_and_identity_delta(self):
        old = {
            'services': {
                l.ROLE: {
                    'image': 'sha256:' + 'a' * 64,
                    'environment': {'MARKET_PIPELINE_RELEASE_SHA': 'b' * 40},
                    'labels': {'org.opencontainers.image.revision': 'b' * 40},
                    'healthcheck': {'test': ['CMD', 'strict']},
                },
                'market-capture-account2': {'image': 'unchanged'},
            },
            'networks': {'market': {}},
            'volumes': {},
            'secrets': {},
            'configs': {},
        }
        new = copy.deepcopy(old)
        new['services'][l.ROLE]['image'] = 'sha256:' + 'c' * 64
        new['services'][l.ROLE]['environment']['MARKET_PIPELINE_RELEASE_SHA'] = 'd' * 40
        new['services'][l.ROLE]['labels']['org.opencontainers.image.revision'] = 'd' * 40
        new['services'][l.ROLE]['healthcheck']['test'] = l.liveness_test()
        l.validate_config(old, new, target_release='d' * 40, target_image='sha256:' + 'c' * 64)

        new['services']['market-capture-account2']['image'] = 'mutated'
        with self.assertRaisesRegex(l.HandoffError, 'unrelated_service_drift'):
            l.validate_config(old, new, target_release='d' * 40, target_image='sha256:' + 'c' * 64)

if __name__ == '__main__':
    unittest.main()
