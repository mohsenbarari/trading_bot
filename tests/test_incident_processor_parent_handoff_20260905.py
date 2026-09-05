import copy
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest

spec=importlib.util.spec_from_file_location('handoff', str(Path(__file__).resolve().parents[1] / 'scripts' / 'incident_processor_parent_handoff_20260905.py'))
m=importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

class Guards(unittest.TestCase):
    def configs(self):
        old={'services':{m.ROLE:{'image':m.OLD_IMAGE,'environment':{'MARKET_PIPELINE_RELEASE_SHA':m.OLD},
                                  'labels':{'org.opencontainers.image.revision':m.OLD}},
                         'market-capture-account1':{'image':'untouched'}}}
        new=copy.deepcopy(old)
        new['services'][m.ROLE]['image']=m.NEW_IMAGE
        new['services'][m.ROLE]['environment']['MARKET_PIPELINE_RELEASE_SHA']=m.NEW
        new['services'][m.ROLE]['labels']['org.opencontainers.image.revision']=m.NEW
        return old,new

    def test_exact_delta(self):
        m.validate_config(*self.configs())

    def test_bystander_change_fails(self):
        a,b=self.configs(); b['services']['market-capture-account1']['image']='changed'
        with self.assertRaisesRegex(RuntimeError,'unrelated_service_drift'): m.validate_config(a,b)

    def test_target_extra_change_fails(self):
        a,b=self.configs(); b['services'][m.ROLE]['environment']['OTHER']='changed'
        with self.assertRaisesRegex(RuntimeError,'unexpected_target_config_drift'): m.validate_config(a,b)

    def test_atomic_and_lock_preserve_bytes(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'lock'; m.atomic(p,{'retain':True}); before=p.read_bytes()
            with m.held(p,os.getuid()):
                with self.assertRaises(BlockingIOError):
                    with m.held(p,os.getuid()): pass
            self.assertEqual(p.read_bytes(),before)

if __name__=='__main__': unittest.main()
