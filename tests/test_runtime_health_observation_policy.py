"""Pure public facts: health observations never authorize serving/runtime changes."""
from copy import deepcopy
from pathlib import Path
import importlib.util,unittest

path=Path(__file__).resolve().parents[1]/'deploy/maintenance/runtime_health_observation_policy.py'
spec=importlib.util.spec_from_file_location('_pure_runtime_health',path);P=importlib.util.module_from_spec(spec);spec.loader.exec_module(P)

def row(cid='1'*64,health='healthy'):
    return {'id':cid,'image':'sha256:'+'2'*64,'health':health,'pid':123,'started':'2026-01-01T00:00:00Z',
        'restarts':0,'running':True,'restarting':False,'paused':False,'dead':False,
        'host':{'Memory':384*1024**2,'CapDrop':['ALL']},'execution':{'Config':{'Cmd':['public'],'Env':['PUBLIC=SAME']}},
        'mounts':[{'Destination':'/public/data','Source':'/public/source'}],'networks':{'public':{'IPAddress':'192.0.2.1'}}}

def scope(site='web'):
    return {'operation':'security-market-candidate-images-20261025-06','operation_sha256':'a'*64,
        'health_policy_sha256':'b'*64,'nonce':'c'*32,'site':site,'not_before':100,'deadline':200}

class RuntimeHealthPolicyTests(unittest.TestCase):
    def test_both_directions_both_sites_exact_comparison_clone_and_raw_unchanged(self):
        for site in ('bot','web'):
            for initial,final in (('healthy','unhealthy'),('unhealthy','healthy')):
                before=[row(health=initial)];after=[row(health=final)];saved=deepcopy((before,after))
                self.assertTrue(P.health_equivalence(before[0],after[0]))
                self.assertTrue(P.same(P.comparison_clone(site,before,after),before))
                self.assertEqual((before,after),saved)

    def test_any_nonhealth_drift_is_refused_including_full_nested_config_host_mount_network(self):
        before=[row()]
        changes=[lambda r:r.update(pid=124),lambda r:r.update(started='2026-01-01T00:00:01Z'),
            lambda r:r.update(restarts=1),lambda r:r.update(image='sha256:'+'3'*64),
            lambda r:r.update(paused=True),lambda r:r.update(dead=True),lambda r:r.update(restarting=True),lambda r:r.update(running=False),
            lambda r:r['host'].update(Memory=1),lambda r:r['execution']['Config'].update(Env=['PUBLIC=CHANGED']),
            lambda r:r['mounts'][0].update(Source='/public/other'),lambda r:r['networks']['public'].update(IPAddress='192.0.2.2'),
            lambda r:r.update(unmodeled='PUBLIC'),lambda r:r.update(pid=True)]
        for change in changes:
            after=[row(health='unhealthy')];change(after[0])
            with self.subTest(change=changes.index(change)):self.assertIs(P.comparison_clone('web',before,after),False)

    def test_none_unknown_starting_and_invalid_health_changes_refuse(self):
        for first in (None,'starting','unknown',True):
            for last in ('healthy','unhealthy'):
                with self.subTest(first=first,last=last):
                    self.assertIs(P.comparison_clone('bot',[row(health=first)],[row(health=last)]),False)
                    self.assertIs(P.comparison_clone('web',[row(health=last)],[row(health=first)]),False)
        self.assertEqual(P.comparison_clone('web',[row(health=None)],[row(health=None)]),[row(health=None)])

    def test_f79_is_never_normalized_or_observed_by_shared_policy(self):
        before=[row(P.F79)];after=[row(P.F79,'unhealthy')]
        self.assertEqual(P.comparison_clone('web',before,after),after)
        with self.assertRaises(ValueError):P.observation(scope(),before[0],after[0],150)
        self.assertEqual(P.observations(scope(),before,after,150),[])

    def test_shape_duplicate_id_changed_id_count_and_unknown_site_refuse(self):
        for before,after in (([],[]),([row(),row()],[row(),row()]),([row()],[row('3'*64)]),([row()],[row(),row('3'*64)]),([None],[None])):
            self.assertIs(P.comparison_clone('web',before,after),False)
        for site in (None,True,[],{},'foreign'):self.assertIs(P.comparison_clone(site,[row()],[row()]),False)

    def test_closed_record_exact_scope_and_identity_fields(self):
        value=P.observation(scope(),row(),row(health='unhealthy'),150)
        self.assertEqual(set(value),P.FIELDS);self.assertIs(value['runtime_health_issues_open'],True);self.assertIs(value['security_accepted'],False)
        self.assertEqual(P.admit_observations(scope(),[value],[row()]),[value])
        for key,bad in (('operation','another-operation'),('operation_sha256','d'*64),('health_policy_sha256','d'*64),('nonce','d'*32),
                ('site','bot'),('container_id','3'*64),('image_id','sha256:'+'3'*64),('pid',True),('pid',124),
                ('started','2026-01-01T00:00:01Z'),('restarts',True),('baseline_health','unhealthy'),('observed_health','healthy'),
                ('observed_at',99),('observed_at',201),('observed_at',True),('security_accepted',0),('runtime_health_issues_open',False)):
            with self.subTest(key=key,bad=bad):self.assertIsNone(P.admit_observations(scope(),[{**value,key:bad}],[row()]))
        self.assertIsNone(P.admit_observations(scope(),[{**value,'raw_payload':'PUBLIC NEVER ADMITTED'}],[row()]))

    def test_immutable_first_observations_survive_return_to_baseline_and_allow_monotonic_new_cids(self):
        before=[row(),row('3'*64)];after=[row(health='unhealthy'),row('3'*64)]
        first=P.observations(scope(),before,after,140)
        self.assertEqual(P.observations(scope(),before,before,150,first),first)
        both=P.observations(scope(),before,[row(health='unhealthy'),row('3'*64,'unhealthy')],160,first)
        self.assertEqual(len(both),2);self.assertEqual(both[0],first[0])
        self.assertEqual(P.admit_observations(scope(),both,before,first),both)
        self.assertIsNone(P.admit_observations(scope(),first,before,both))
        self.assertIsNone(P.admit_observations(scope(),[{**first[0],'observed_at':141}],before,first))
        self.assertIsNone(P.admit_observations(scope(),list(reversed(both)),before))
        self.assertIsNone(P.admit_observations(scope(),both+[both[0]],before))

    def test_scope_and_timestamp_validation_are_closed_and_typed(self):
        for change in ({'site':[]},{'deadline':True},{'not_before':200},{'nonce':'bad'},{'operation_sha256':'bad'},{'extra':'PUBLIC'}):
            value={**scope(),**change};self.assertFalse(P.scope_valid(value))
            with self.assertRaises(ValueError):P.observations(value,[row()],[row(health='unhealthy')],150)
        for value in ('bad','2026-99-99T00:00:00Z','2026-01-01T00:00:00+00:00',None):self.assertFalse(P.started(value))
        self.assertTrue(P.started('2026-01-01T00:00:00.123456789Z'))
        for key,value in (('pid',0),('restarts',-1),('started','bad')):
            value_row=row();value_row[key]=value;self.assertFalse(P.stable_row(value_row))

    def test_record_and_shape_bounds(self):
        record=P.observation(scope(),row(),row(health='unhealthy'),150)
        self.assertIsNone(P.admit_observations(scope(),[record]*129,[row()]))
        self.assertIsNone(P.admit_observations(scope(),{},[row()]))
        self.assertIsNone(P.admit_observations(scope(),[record],[]))
        self.assertIs(P.comparison_clone('web',[row(f'{i:064x}') for i in range(129)],[row(f'{i:064x}') for i in range(129)]),False)
        self.assertFalse(P.same(True,1));self.assertFalse(P.same({'n':1},{'n':True}))

if __name__=='__main__':unittest.main()
