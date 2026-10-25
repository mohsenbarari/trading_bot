"""Public OP06 observations only; no host, daemon, Env or session operations."""
from contextlib import ExitStack
from copy import deepcopy
from datetime import datetime,timezone
from hashlib import sha256
import ast
import importlib.util
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
DENIED = ('subprocess.run','subprocess.Popen','os.system','os.popen','os.fork','os.kill','os.killpg',
          'socket.socket','socket.create_connection','socket.getaddrinfo','urllib.request.urlopen',
          'ctypes.CDLL','ctypes.PyDLL','resource.setrlimit','signal.signal','signal.alarm')


def fences(stack):
    for target in DENIED: stack.enter_context(patch(target,side_effect=AssertionError('ambient operation denied')))


def load(name,path,pin=None):
    raw = path.read_bytes()
    if pin is not None: assert sha256(raw).hexdigest() == pin
    spec = importlib.util.spec_from_file_location(name,path); module = importlib.util.module_from_spec(spec)
    exec(compile(raw,str(path),'exec'),module.__dict__); return module


with ExitStack() as importing:
    fences(importing)
    M = load('_market06_fixture',ROOT/'deploy/maintenance/market_candidate_images_v2.py')
    D = load('_market06_contract_fixture',ROOT/'deploy/maintenance/datastore_candidate_contract.py',
        'd210020e24fedd4986803a3fe2618a5016ecd99f9e3ca9588af02d77a967e17d')


class Market06Tests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack(); self.addCleanup(self.stack.close); fences(self.stack)
        self.stack.enter_context(patch.dict(os.environ,{'APP_ENV_FILE':'config/unit-test.env.example'},clear=True))
        self.now = 1792930000

    def stamp(self,seconds):return datetime.fromtimestamp(seconds,timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')

    def rows(self,site='web',health='healthy'):
        rows = []
        for index in range(27 if site == 'web' else 15):
            cid = f'{index+1:064x}'
            if site == 'web' and index == 0: cid = M.CAPTURE
            if site == 'web' and index == 1: cid = M.PROCESSOR
            running = index < 16
            rows.append({'id':cid,'image':'sha256:'+'b'*64,'name':'/public-role-'+str(index),
                'running':running,'restarting':False,'paused':False,'dead':False,'health':None,
                'pid':1000+index if running else 0,'started':self.stamp(self.now-120),'restarts':7,
                'host':{'ReadonlyRootfs':True,'CapDrop':['ALL'],'LogConfig':{'Type':'json-file','options':{'max-size':'1m'}}},
                'mounts':[{'Type':'bind','Source':'/public/source-'+str(index),'Destination':'/app/public.py','RW':False}],
                'execution':{'entry':[],'cmd':['ordinary-public'],'user':'10001:10001','workdir':'/app',
                    'labels':{'public.fixture':'true'},'healthcheck':None},
                'networks':{'public-network':{'NetworkID':'a'*64,'EndpointID':'b'*64,'MacAddress':'02:00:00:00:00:02',
                    'IPAddress':'192.0.2.7','Gateway':'192.0.2.1','IPAMConfig':None,'Aliases':['public-role'], 'DriverOpts':None}}})
        if site == 'web':
            capture = next(r for r in rows if r['id'] == M.CAPTURE); capture.update(image=M.F79_IMAGE,health='healthy')
            capture['networks'] = {name:deepcopy(capture['networks']['public-network']) for name in M.F79_NETWORKS}
            processor = next(r for r in rows if r['id'] == M.PROCESSOR); processor.update(image=M.PROCESSOR_IMAGE,health=health)
        return sorted(rows,key=lambda r:r['id'])

    def ctx(self,health='healthy',site='web'):
        baseline = {'containers':self.rows(site,health),'image_map':[{'id':'sha256:'+'c'*64,'repository':'public-base','tag':'one'}]}
        op = {'operation':M.OPERATION,'phase':'load-web','nonce':'a'*32,'not_before':self.now-10,'deadline':self.now+120,
            'baselines':{'web':deepcopy(baseline)}}
        return SimpleNamespace(site=site,D=D,baseline=baseline,op=op,pin='d'*64,end=1000,requested=False,
            checks=[],check=lambda:None,capacity=lambda:None)

    def processor(self,rows):return next(r for r in rows if r['id'] == M.PROCESSOR)
    def capture(self,rows):return next(r for r in rows if r['id'] == M.CAPTURE)

    def switched(self,rows):
        value = deepcopy(rows); row = self.processor(value); row['health'] = 'unhealthy' if row['health'] == 'healthy' else 'healthy'; return value

    def restart(self,rows):
        value = deepcopy(rows); row = self.capture(value); row.update(pid=row['pid']+1,restarts=row['restarts']+1,started=self.stamp(self.now-60))
        for index,net in enumerate(row['networks'].values()):net.update(EndpointID=str(index+3)*64,MacAddress='02:00:00:00:00:0'+str(index+3))
        return value

    def test_namespace_is_new_and_frozen_cd8_worker_is_unchanged(self):
        self.assertEqual(M.OPERATION,'security-market-candidate-images-20261025-06')
        self.assertEqual(sha256((ROOT/'deploy/maintenance/market_candidate_images.py').read_bytes()).hexdigest(),
            'cd8b11fbc8abc5f3f09db023e72613bf68f77ec622d647f50dbcf0103d56eb3e')
        self.assertTrue(all(str(path).endswith(M.OPERATION) for path in M.TOOLS.values()))
        self.assertEqual(M.CAPTURE_READY_SECONDS,45)

    def test_existing_guard_archive_client_and_mutating_rpc_functions_are_identical_ast(self):
        old = ast.parse((ROOT/'deploy/maintenance/market_candidate_images.py').read_bytes())
        new = ast.parse((ROOT/'deploy/maintenance/market_candidate_images_v2.py').read_bytes())
        before = {node.name:node for node in old.body if isinstance(node,(ast.FunctionDef,ast.ClassDef))}
        after = {node.name:node for node in new.body if isinstance(node,(ast.FunctionDef,ast.ClassDef))}
        for name in ('Context','Guard','seal','parents','check_parents','command_result','clients_close',
                     'source_archive','admitted_tar','legacy_a37','run','remote'):
            with self.subTest(name=name):self.assertEqual(ast.dump(after[name]),ast.dump(before[name]))

    def test_only_fixed_processor_healthy_unhealthy_equivalence_is_typed_and_nonmutating(self):
        for health in ('healthy','unhealthy'):
            before = self.processor(self.rows(health=health)); after = deepcopy(before)
            after['health'] = 'unhealthy' if health == 'healthy' else 'healthy'
            saved = deepcopy((before,after)); self.assertIs(M.processor_health_equivalence(before,after,D.same),True)
            self.assertTrue(D.same(before,saved[0]) and D.same(after,saved[1]))
            for bad in ('starting',None,False,1,'Healthy'):
                changed = deepcopy(after); changed['health'] = bad
                self.assertIs(M.processor_health_equivalence(before,changed,D.same),False)

    def test_processor_health_exception_refuses_lifecycle_identity_network_and_every_static_drift(self):
        before = self.processor(self.rows()); after = deepcopy(before); after['health'] = 'unhealthy'
        changes = (('id','f'*64),('image','sha256:'+'f'*64),('running',False),('running',1),('restarting',True),
            ('paused',True),('paused',0),('dead',True),('pid',0),('pid',True),('pid',before['pid']+1),
            ('restarts',True),('restarts',before['restarts']+1),('started',self.stamp(self.now-60)),('started','bad'),('name','/other'))
        for key,value in changes:
            changed = deepcopy(after); changed[key] = value
            with self.subTest(key=key,value=value):self.assertIs(M.processor_health_equivalence(before,changed,D.same),False)
        for key in ('host','mounts','execution','networks'):
            changed = deepcopy(after)
            if key == 'mounts': changed[key][0]['RW'] = True
            else: changed[key]['public_extra'] = True
            with self.subTest(key=key):self.assertIs(M.processor_health_equivalence(before,changed,D.same),False)
        for missing in ('paused','dead','pid','started','restarts'):
            changed = deepcopy(after); changed.pop(missing)
            with self.subTest(missing=missing):self.assertIs(M.processor_health_equivalence(before,changed,D.same),False)

    def test_processor_switch_is_orthogonal_to_original_single_f79_restart_and_never_rebases(self):
        before = self.rows(); after = self.switched(before)
        self.assertIsNone(M.image_store_equivalence('web',before,after,D.same,self.now))
        self.assertIs(M.image_store_equivalence('bot',before,after,D.same,self.now),False)
        restarted = self.restart(after); successor = M.image_store_equivalence('web',before,restarted,D.same,self.now)
        self.assertTrue(D.same(successor,self.capture(restarted)))
        self.assertTrue(D.same(M.image_store_equivalence('web',before,restarted,D.same,self.now,successor),successor))
        self.assertIs(M.image_store_equivalence('web',before,self.restart(restarted),D.same,self.now,successor),False)
        self.assertIs(M.image_store_equivalence('web',before,after,D.same,self.now,successor),False)
        other = deepcopy(after); next(r for r in other if r['id'] not in {M.PROCESSOR,M.CAPTURE})['health'] = 'unhealthy'
        self.assertIs(M.image_store_equivalence('web',before,other,D.same,self.now),False)

    def observation(self,ctx):
        row = self.processor(ctx.baseline['containers'])
        return {'schema':'market_processor_image_store_health/1','container_id':M.PROCESSOR,'image_id':M.PROCESSOR_IMAGE,
            'operation_sha256':ctx.pin,'nonce':ctx.op['nonce'],'baseline_health':row['health'],
            'observed_health':'unhealthy' if row['health'] == 'healthy' else 'healthy','pid':row['pid'],
            'started':row['started'],'restarts':row['restarts'],'observed_at':self.now,'processor_health_issue_open':True,'security_accepted':False}

    def test_closed_processor_observation_binds_nonce_control_lifecycle_and_original_baseline(self):
        ctx = self.ctx(); value = self.observation(ctx)
        self.assertTrue(D.same(M.processor_health_observation(ctx,value),value))
        changes = (('nonce','f'*32),('operation_sha256','f'*64),('container_id','f'*64),('image_id','sha256:'+'f'*64),
            ('pid',True),('pid',value['pid']+1),('restarts',True),('restarts',value['restarts']+1),('started',self.stamp(self.now-60)),
            ('observed_at',True),('observed_at',ctx.op['not_before']-1),('observed_at',ctx.op['deadline']+1),
            ('baseline_health','unhealthy'),('observed_health','starting'),('security_accepted',True),('processor_health_issue_open',1))
        for key,item in changes:
            bad = deepcopy(value); bad[key] = item
            with self.subTest(key=key,item=item):self.assertIsNone(M.processor_health_observation(ctx,bad))
        for bad in ({**value,'extra':'PUBLIC should not emit'}, {k:v for k,v in value.items() if k != 'pid'}):
            self.assertIsNone(M.processor_health_observation(ctx,bad))
        same_health = deepcopy(value); same_health['observed_health'] = value['baseline_health']
        self.assertIsNone(M.processor_health_observation(ctx,same_health))

    def test_first_real_processor_deviation_is_retained_and_both_observation_channels_are_closed(self):
        ctx = self.ctx(); changed = self.switched(ctx.baseline['containers'])
        with patch.object(M.time,'time',return_value=self.now),patch.object(M,'snapshot',return_value={'containers':changed,'image_map':ctx.baseline['image_map']}):
            M.stable(ctx); original = deepcopy(ctx.processor_health_observation); M.stable(ctx)
        self.assertEqual(ctx.processor_health_observation,original)
        recovered = deepcopy(ctx.baseline)
        with patch.object(M.time,'time',return_value=self.now+1),patch.object(M,'snapshot',return_value=recovered):M.stable(ctx)
        self.assertEqual(ctx.processor_health_observation,original)
        ctx.peer_processor_health_observation = deepcopy(original)
        details = M.observation_details(ctx); self.assertEqual(details['processor_health_observation'],original)
        self.assertEqual(details['peer_processor_health_observation'],original)
        ctx.peer_processor_health_observation['nonce'] = 'f'*32
        self.assertNotIn('peer_processor_health_observation',M.observation_details(ctx))
        self.assertEqual(ctx.baseline,self.ctx().baseline)

    def clock(self):
        clock = SimpleNamespace(now=100.0,sleeps=[])
        def sleep(seconds):
            self.assertGreater(seconds,0); self.assertLessEqual(seconds,1)
            clock.sleeps.append(seconds); clock.now += seconds
        self.stack.enter_context(patch.object(M.time,'monotonic',side_effect=lambda:clock.now))
        self.stack.enter_context(patch.object(M.time,'sleep',side_effect=sleep))
        self.stack.enter_context(patch.object(M.time,'time',return_value=self.now))
        return clock

    def warming(self,ctx,health='starting',restart=False,processor=False):
        value = deepcopy(ctx.baseline)
        if restart:value['containers'] = self.restart(value['containers'])
        if processor:value['containers'] = self.switched(value['containers'])
        self.capture(value['containers'])['health'] = health
        return value

    def test_warming_wait_pins_first_lifecycle_then_accepts_only_healthy_without_mutation(self):
        ctx = self.ctx(); clock = self.clock(); warming = self.warming(ctx,restart=True,processor=True)
        healthy = deepcopy(warming); self.capture(healthy['containers'])['health'] = 'healthy'
        original = deepcopy(ctx.baseline)
        with patch.object(M,'_snapshot_once',side_effect=[warming,healthy]) as observe:
            result = M.stable(ctx)
        self.assertEqual(result,healthy); self.assertEqual(observe.call_count,2); self.assertEqual(clock.sleeps,[1])
        self.assertEqual(ctx.baseline,original); self.assertEqual(ctx.f79_successor,self.capture(healthy['containers']))
        first = deepcopy(ctx.f79_restart_observation); processor = deepcopy(ctx.processor_health_observation)
        with patch.object(M,'_snapshot_once',return_value=healthy):M.stable(ctx)
        self.assertEqual(ctx.f79_restart_observation,first); self.assertEqual(ctx.processor_health_observation,processor)
        second_restart = deepcopy(healthy); second_restart['containers'] = self.restart(healthy['containers'])
        with patch.object(M,'_snapshot_once',return_value=second_restart),self.assertRaisesRegex(RuntimeError,'^runtime_changed$'):M.stable(ctx)

    def test_warming_timeout_is_exact_existing45_seconds_and_never_retries_an_image_rpc(self):
        ctx = self.ctx(); clock = self.clock(); warming = self.warming(ctx,'unhealthy')
        with patch.object(M,'_snapshot_once',return_value=warming) as observe:
            with self.assertRaisesRegex(RuntimeError,'^capture_readiness_timeout$'):M.snapshot(ctx)
        self.assertEqual(clock.now,145.0); self.assertEqual(len(clock.sleeps),45)
        self.assertLessEqual(observe.call_count,45); self.assertFalse(ctx.requested)
        self.assertEqual(M.SUBPHASE,'capture_readiness_wait')
        self.assertEqual(ctx.end,1000)

    def test_underlying_poll_commands_see_shortened_deadline_restored_on_success_or_failure(self):
        for fail in (False,True):
            ctx = self.ctx(); clock = self.clock(); warming = self.warming(ctx); healthy = deepcopy(warming)
            self.capture(healthy['containers'])['health'] = 'healthy'; observed = []
            def once(current):
                observed.append(current.end)
                if len(observed) == 1:return warming
                self.assertEqual(current.end,145)
                if fail:raise RuntimeError('public_inspect_deadline')
                return healthy
            with self.subTest(fail=fail),patch.object(M,'_snapshot_once',side_effect=once):
                if fail:
                    with self.assertRaisesRegex(RuntimeError,'^public_inspect_deadline$'):M.snapshot(ctx)
                else:self.assertEqual(M.snapshot(ctx),healthy)
            self.assertEqual(observed,[1000,145]); self.assertEqual(ctx.end,1000); self.assertEqual(clock.now,101)

    def test_warming_refuses_lifecycle_network_static_map_and_other_role_drift_immediately(self):
        for kind in ('pid','restarts','started','paused','dead','host','ip','endpoint','other','image_map'):
            ctx = self.ctx(); self.clock(); warming = self.warming(ctx); changed = deepcopy(warming)
            row = self.capture(changed['containers']); row['health'] = 'healthy'
            if kind == 'pid':row['pid'] += 1
            elif kind == 'restarts':row['restarts'] += 1
            elif kind == 'started':row['started'] = self.stamp(self.now-60)
            elif kind in {'paused','dead'}:row[kind] = True
            elif kind == 'host':row['host']['ReadonlyRootfs'] = False
            elif kind in {'ip','endpoint'}:row['networks'][M.F79_NETWORKS[0]]['IPAddress' if kind == 'ip' else 'EndpointID'] = '192.0.2.99' if kind == 'ip' else 'f'*64
            elif kind == 'other':next(r for r in changed['containers'] if r['id'] not in {M.CAPTURE,M.PROCESSOR})['pid'] += 1
            else:changed['image_map'].append({'id':'sha256:'+'f'*64,'repository':'unknown','tag':'one'})
            with self.subTest(kind=kind),patch.object(M,'_snapshot_once',side_effect=[warming,changed]) as observe:
                with self.assertRaisesRegex(RuntimeError,'^(runtime_changed|image_map_changed|capture_singleton)$'):M.snapshot(ctx)
                self.assertEqual(observe.call_count,2)

    def test_wait_preserves_capacity_failure_and_operation_deadline_without_retries(self):
        ctx = self.ctx(); clock = self.clock(); warming = self.warming(ctx)
        ctx.capacity = lambda:(_ for _ in ()).throw(RuntimeError('public_capacity_failure'))
        with patch.object(M,'_snapshot_once',return_value=warming) as observe:
            with self.assertRaisesRegex(RuntimeError,'^public_capacity_failure$'):M.snapshot(ctx)
        self.assertEqual(observe.call_count,1); self.assertEqual(clock.sleeps,[])
        ctx = self.ctx(); ctx.end = 102; warming = self.warming(ctx)
        with patch.object(M,'_snapshot_once',return_value=warming):
            with self.assertRaisesRegex(RuntimeError,'^capture_readiness_timeout$'):M.snapshot(ctx)
        self.assertEqual(clock.now,102)

    def test_only_expected_one_postload_image_delta_is_allowed_while_waiting(self):
        for admitted,requested,phase in ((True,True,'load-web'),(False,True,'load-web'),(True,False,'load-web'),(True,True,'receive-web')):
            ctx = self.ctx(); self.clock(); ctx.requested = requested; ctx.op['phase'] = phase
            warming = self.warming(ctx); new_id = 'sha256:'+'e'*64
            warming['image_map'].append({'id':new_id,'repository':'public-new','tag':'one'})
            healthy = deepcopy(warming); self.capture(healthy['containers'])['health'] = 'healthy'
            admission = {'source_identities':{new_id:{'kind':'Docker_config_identity'}}} if admitted else None
            with self.subTest(admitted=admitted,requested=requested,phase=phase),patch.object(M,'_snapshot_once',side_effect=[warming,healthy]):
                if admitted and requested and phase == 'load-web':self.assertEqual(M.snapshot(ctx,admission),healthy)
                else:
                    with self.assertRaisesRegex(RuntimeError,'^image_map_changed$'):M.snapshot(ctx,admission)

    def test_measured_passes_exact_admission_to_readiness_and_still_checks_one_image_delta(self):
        ctx = self.ctx(); new_id = 'sha256:'+'e'*64; admission = {'source_identities':{new_id:{'kind':'Docker_config_identity'}}}
        after = deepcopy(ctx.baseline); after['image_map'].append({'id':new_id,'repository':'public-new','tag':'one'})
        with patch.object(M,'stable',return_value=after) as stable,patch.object(M,'image',return_value={'public':'measured'}) as image:
            self.assertEqual(M.measured(ctx,admission),{'public':'measured'})
        stable.assert_called_once_with(ctx,admission); image.assert_called_once_with(ctx,new_id,admission)
        after['image_map'].append({'id':'sha256:'+'f'*64,'repository':'other','tag':'one'})
        with patch.object(M,'stable',return_value=after),patch.object(M,'image',side_effect=AssertionError('must refuse before inspect')):
            with self.assertRaisesRegex(RuntimeError,'^image_map_delta$'):M.measured(ctx,admission)

    def test_stream_header_footer_bind_first_processor_record_and_refuse_loss_or_tampering(self):
        ctx = self.ctx(); ctx.spec = {'profile':'public-profile'}; ctx.op['admission_sha256'] = 'e'*64
        admitted = {'archive_bytes':10240,'archive_sha256':'f'*64,'tar_identity':[1,2,0o100600,0,0,1,10240,3,4]}
        value = {'schema':'market_image_stream/1','profile':ctx.spec['profile'],'operation_sha256':ctx.pin,
            'admission_sha256':ctx.op['admission_sha256'],**admitted,'processor_health_observation':self.observation(ctx)}
        M.header(ctx,value,admitted); original = deepcopy(ctx.peer_processor_health_observation)
        complete = {**value,'schema':'market_image_stream_complete/1','source_fd_held_and_verified':True}
        M.header(ctx,complete,admitted,complete=True); self.assertEqual(ctx.peer_processor_health_observation,original)
        for mutate in (lambda v:v.pop('processor_health_observation'),lambda v:v['processor_health_observation'].update(nonce='f'*32),
                       lambda v:v['processor_health_observation'].update(observed_at=self.now+1),lambda v:v.update(extra='PUBLIC')):
            changed = deepcopy(complete); mutate(changed)
            with self.assertRaisesRegex(RuntimeError,'^stream_binding$'):M.header(ctx,changed,admitted,complete=True)

    def test_peer_failure_and_local_failure_details_preserve_only_closed_processor_record(self):
        ctx = self.ctx(); value = {'status':'refused_or_unknown','phase':ctx.op['phase'],'subphase':'capture_readiness_wait',
            'code':'capture_readiness_timeout','retry_allowed':False,'security_accepted':False,'exception_class':'RuntimeError',
            'os_errno':None,'processor_health_observation':self.observation(ctx)}
        admitted = M.peer_failure(ctx,value,3); self.assertIsNotNone(admitted); self.assertEqual(admitted['returncode'],3)
        ctx.peer_returncode = 3; ctx.peer_failure = admitted
        self.assertEqual(M.failure_details(ctx)['peer_failure']['processor_health_observation'],self.observation(ctx))
        for mutate in (lambda v:v['processor_health_observation'].update(pid=True),lambda v:v['processor_health_observation'].update(nonce='f'*32),
                       lambda v:v.update(extra='PUBLIC'),lambda v:v.update(retry_allowed=0)):
            changed = deepcopy(value); mutate(changed)
            self.assertIsNone(M.peer_failure(ctx,changed,3))

    def test_raw_snapshot_is_readonly_and_preserves_paused_dead_with_optional_health(self):
        ctx = self.ctx(); rows = deepcopy(ctx.baseline['containers']); calls = []
        for row in rows:
            log = row['host']['LogConfig']; row['host']['LogConfig'] = {'Type':log['Type'],'Config':log['options']}
        def command(current,args,**kwargs):
            calls.append(args); tail = args[len(M.DOCKER):]
            if tail[:2] == ['ps','-aq']:return ('\n'.join(r['id'] for r in rows)+'\n').encode()
            if tail[0] == 'inspect':
                fmt = tail[tail.index('--format')+1]; self.assertIn('.State.Paused',fmt); self.assertIn('.State.Dead',fmt)
                self.assertNotIn('.Config.Env',fmt); self.assertIn('with index .State "Health"',fmt)
                return b'\n'.join(M.encode(r).rstrip() for r in rows)+b'\n'
            self.assertEqual(tail[:2],['image','ls']); return b'\n'.join(M.encode(r).rstrip() for r in ctx.baseline['image_map'])+b'\n'
        with patch.object(M,'command',side_effect=command):actual = M._snapshot_once(ctx)
        self.assertEqual(actual,ctx.baseline); self.assertEqual(len(calls),3)


if __name__ == '__main__': unittest.main()
