"""Public schema fixtures; every native/network boundary is denied by default."""
from contextlib import ExitStack
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import ctypes
import importlib.util
import io
import json
import os
from pathlib import Path
import signal
import socket
import fcntl
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

DENIALS=ExitStack()
for module,names in ((subprocess,('Popen','run','call','check_call','check_output')),
        (socket,('socket','create_connection','getaddrinfo')),(ctypes,('CDLL',)),
        (os,('system','popen','fork','posix_spawn','posix_spawnp','kill','killpg'))):
    for name in names:
        if hasattr(module,name):DENIALS.enter_context(patch.object(module,name,side_effect=AssertionError('ambient_execution_denied')))

ROOT=Path(__file__).resolve().parents[1]
SPEC=importlib.util.spec_from_file_location('current_controller_fixture',ROOT/'deploy/maintenance/web_engine_current_controller.py')
M=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(M)
PUBLIC_READ=M.read
from tests import test_web_engine_current_profile as fixtures


def raw(value):return (json.dumps(value,sort_keys=True)+'\n').encode()


class CurrentControllerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        saved={k:v for k,v in sys.modules.items() if k in ('scripts','deploy','deploy.maintenance') or k.startswith(('scripts.','deploy.maintenance.'))}
        for key in saved:sys.modules.pop(key)
        try:
            def public_source(path,maximum=131072,digest=None):
                data=Path(path).read_bytes()
                if digest is not None:assert sha256(data).hexdigest()==digest
                assert 0<len(data)<=maximum
                return data
            with patch.object(M,'read',side_effect=public_source):cls.recipe=M.load_recipe(ROOT)
        finally:
            for key in list(sys.modules):
                if key in ('scripts','deploy','deploy.maintenance') or key.startswith(('scripts.','deploy.maintenance.')):sys.modules.pop(key)
            sys.modules.update(saved)

    def setUp(self):
        self.stack=ExitStack();self.addCleanup(self.stack.close)
        self.recipe=self.__class__.recipe
        self.docs=fixtures.CurrentProfileTests().fixture();old=self.docs['c07_plan']
        old.update(machine_id='public-machine',marker={'path':str(M.MARKER),'device':2049,'inode':648637,'size':9,'sha256':'a'*64},
            units=[],engine_intent=[],backup_key_file='/root/secure-envs/public-normal-tool-key',
            old_packages=[],new_packages=[],ansible_binary='/root/public-venv/bin/ansible-playbook',
            files=[{'path':'/root/public-venv/bin/ansible-playbook','size':11,'sha256':'b'*64}],health_probes=[],
            absent_control_paths=['/root/secure-envs/trading-bot/.production-runtime-source.lock',
                '/root/secure-envs/trading-bot/market-pipeline-cutover/bot-legacy-handoff-4ef8e6dc.json'])
        plan_sha=sha256(fixtures.raw(old)).hexdigest()
        for key in ('qa_parking','capture06','closure'):self.docs[key]['original_plan_sha256']=plan_sha
        park_sha=sha256(fixtures.raw(self.docs['qa_parking'])).hexdigest()
        self.docs['capture06']['parking_audit_sha256']=park_sha
        self.docs['closure'].update(qa_parking_audit_sha256=park_sha,
            accepted_capture06_audit_sha256=sha256(fixtures.raw(self.docs['capture06'])).hexdigest())
        self.buffers={k:fixtures.raw(v) for k,v in self.docs.items()}
        self.pins={k:sha256(v).hexdigest() for k,v in self.buffers.items()}
        self.stack.enter_context(patch.object(self.recipe.R,'CONTROL_SHA256',self.pins))
        self.now=datetime.now(timezone.utc)
        self.window={'schema':'web_engine_current_window/1','operation_id':M.OP,'nonce':'4'*32,
            'not_before_utc':self.now.isoformat().replace('+00:00','Z'),
            'deadline_utc':(self.now+timedelta(minutes=90)).isoformat().replace('+00:00','Z'),
            'approved_sources_sha256':{**M.PINS,**{k:'c'*64 for k in M.OWN_SOURCES}},'profile_control_sha256':self.pins,
            'engine_packages_allowed':True,**{k:False for k in ('reboot_allowed','kernel_package_changes_allowed',
                'container_recreation_allowed','writer_dns_changes_allowed','capture_owner_changes_allowed','automatic_downgrade_allowed')}}
        self.public={M.ROOT/'window.json':raw(self.window),**{M.ROOT/'evidence'/(k+'.json'):v for k,v in self.buffers.items()},
            M.ROOT/'evidence/app-backup.json':b'{"public":"fresh-app"}\n',M.ROOT/'evidence/app-offhost.json':b'{"public":"fresh-offhost"}\n'}
        def public_read(path,maximum=2*1024**2,digest=None):
            self.assertIn(Path(path),self.public,'only explicitly named public controls may be read')
            data=self.public[Path(path)];self.assertLessEqual(len(data),maximum)
            if digest is not None:self.assertEqual(sha256(data).hexdigest(),digest)
            return data
        self.reader=self.stack.enter_context(patch.object(M,'read',side_effect=public_read))
        self.arguments={'transport_sha256':'d'*64,'destination_sha256':'e'*64,
            'app_sha256':sha256(self.public[M.ROOT/'evidence/app-backup.json']).hexdigest(),
            'app_offhost_sha256':sha256(self.public[M.ROOT/'evidence/app-offhost.json']).hexdigest()}
        self.plan=M.build_plan(self.recipe,raw(self.window),now=self.now,**self.arguments)
        self.rt=M.Runtime(self.recipe,self.plan,'f'*64)
        self.stack.enter_context(patch.object(self.rt,'assert_watchdog'))

    def test_real_pinned_helper_loader_and_actual_four_key_postgres_probe_schema(self):
        self.assertEqual(len(self.plan['containers']),27)
        self.assertEqual(sum(v['running'] for v in self.plan['containers']),16)
        self.assertEqual(len(self.plan['datastore_ids']),6)
        postgres=[p for p in self.plan['datastore_probes'] if p['kind']=='postgres']
        self.assertTrue(all(set(p)=={'id','kind','user','database'} for p in postgres))
        self.assertEqual(self.rt.intent['database']['container_id'],next(p['id'] for p in postgres if p['id']==self.rt.intent['database']['container_id']))
        self.assertNotEqual(self.plan['current_profile']['configuration_source']['release_sha'],self.plan['current_profile']['source_image']['release_sha'])
        self.assertEqual(self.rt.common_backup()['release_sha'],self.recipe.R.CONFIGURATION['release_sha'])
        self.assertEqual(self.rt.common_backup()['image_id'],self.recipe.R.IMAGE['image_id'])
        self.assertNotEqual(self.plan['operation_id'],self.docs['c07_plan']['operation_id'])

    def test_fresh_window_cannot_rewrite_history_or_enable_reboot_recreation_or_bool_pins(self):
        for key,value in (('reboot_allowed',True),('engine_packages_allowed',1),('automatic_downgrade_allowed',True),
                ('nonce',False),('operation_id',self.docs['c07_plan']['operation_id'])):
            changed=deepcopy(self.window);changed[key]=value
            with self.subTest(key=key),self.assertRaises(M.Refusal):M.checked_window(changed,self.recipe,now=self.now)
        changed=deepcopy(self.window);changed['deadline_utc']=(self.now+timedelta(minutes=91)).isoformat().replace('+00:00','Z')
        with self.assertRaisesRegex(M.Refusal,'expanded'):M.checked_window(changed,self.recipe,now=self.now)
        with self.assertRaisesRegex(M.Refusal,'start_reserve'):
            M.checked_window(self.window,self.recipe,apply=True,now=self.now+timedelta(minutes=26))

    def test_closed_manifest_rejects_arbitrary_private_paths_extra_ids_and_mutated_history(self):
        path=M.ROOT/'plan.json'
        for change in (lambda p:p.update(backup_key_file='/private/arbitrary'),
                lambda p:p['containers'].append(deepcopy(p['containers'][0])),lambda p:p.update(machine_id='other'),
                lambda p:p['current_profile']['containers'][0].update(running=not p['current_profile']['containers'][0]['running']),lambda p:p.update(deadline_utc='2100-01-01T00:00:00Z')):
            changed=deepcopy(self.plan);change(changed);self.public[path]=raw(changed)
            with self.subTest(change=change),self.assertRaises(M.Refusal):
                M.checked_plan(self.recipe,path,sha256(self.public[path]).hexdigest())
        self.public[path]=raw(self.plan)
        self.assertEqual(M.checked_plan(self.recipe,path,sha256(self.public[path]).hexdigest()),self.plan)

    def test_exact_apt_closure_uses_local_files_and_no_remote_lists_without_force(self):
        self.rt.plan['new_packages']=[{'path':'/public/'+name+'.deb'} for name in self.recipe.C.P.NEW_PACKAGES]
        simulation=self.rt.apt_argv(simulation=True);install=self.rt.apt_argv(simulation=False)
        self.assertIn('--simulate',simulation);self.assertIn('--no-download',simulation)
        self.assertNotIn('--no-download',install);self.assertIn('--yes',install)
        self.assertIn('Dir::Etc::sourcelist=/dev/null',install);self.assertIn('Dir::Etc::sourceparts=-',install)
        self.assertEqual([a for a in install if a.endswith('.deb')],[p['path'] for p in self.rt.plan['new_packages']])
        good='\n'.join(['Inst '+v for v in self.recipe.C.P.NEW_PACKAGES]+['Remv '+v for v in self.recipe.C.P.OLD_PACKAGES])
        self.recipe.C.apt_scope(good)
        for bad in (good+'\nInst unrelated',good+'\nRemv unrelated',good.replace('Inst docker-ce-cli','')):
            with self.assertRaises(self.recipe.C.P.Refusal):self.recipe.C.apt_scope(bad)
        self.assertFalse(any('force' in a or 'downgrade' in a for a in install))

    def test_sanitized_failure_diagnostics_require_exact_trusted_exception_type(self):
        self.assertEqual(M.safe_reason(M.Refusal('current_closed_reason')),'current_closed_reason')
        fake=type('Refusal',(RuntimeError,),{})
        for error in (fake('private_value'),M.Refusal('/private/path=secret'),M.Refusal('a'*81),RuntimeError('secret')):
            self.assertEqual(M.safe_reason(error),'current_unexpected_failure')
        self.assertEqual(M.safe_reason(KeyError('private_value')),'current_keyerror_unknown')

    def test_checkpoint_retry_rechecks_without_advancing_twice(self):
        with patch.object(self.rt,'invariant'),patch.object(self.rt,'runtime',return_value=[]),\
                patch.object(self.rt,'state',return_value={'phase':'datastores'}),patch.object(self.rt,'advance') as advance:
            self.rt.checkpoint('datastores');self.rt.checkpoint('datastores')
        advance.assert_not_called()

    def test_fresh_dump_nonce_is_bound_to_this_operation_before_artifact_io(self):
        profile=self.plan['current_profile'];nonce='5'*32
        fresh={'schema':'web_engine_fresh_quiesced_operand/1','operation_id':M.OP,'nonce':nonce,'backup_mode':'quiesced',
            'release_sha':profile['configuration_source']['release_sha'],'release_tree':profile['configuration_source']['release_tree'],
            'image_id':profile['source_image']['image_id'],'image_input_signature':profile['source_image']['image_input_signature'],
            'path':str(M.work_root(nonce)/'market/market-archive-before-f3be9ae2c4e9-20261024T190000Z-12345678.dump'),
            'size_bytes':20,'sha256':'a'*64,'receipt_sha256':'b'*64,'journal_sha256':'c'*64,'restore_status':'PASS','cleanup_status':'PASS'}
        with patch.object(self.rt,'state',return_value={'fresh_quiesced_backup':fresh}):
            with self.assertRaisesRegex(M.Refusal,'nonce'):self.rt.verified_fresh_dump()

    def test_limited_backup_uses_exact_primary_api_one_owned_restore_and_strict_verify(self):
        state={'phase':'writers_quiesced'};calls=[];artifact={'path':str(self.rt.work/'market/market-archive-before-f3be9ae2c4e9-20261024T190000Z-12345678.dump'),
            'size_bytes':1024,'sha256':'a'*64}
        created={'status':'PASS','backup_mode':'quiesced',**{k:v for k,v in self.rt.common_backup().items() if k!='env_file'},
            'backup':artifact,'restore_smoke':{'status':'PASS','cleanup_status':'PASS'}}
        def create(**kwargs):
            calls.append(('create',kwargs));self.assertEqual(state['backup_requests_complete'],False);return created
        def verify(**kwargs):calls.append(('verify',kwargs));return created
        def saved(value,**kwargs):state.update(value)
        def advanced(name,**kwargs):state.update(phase=name,**kwargs)
        with patch.object(self.rt,'invariant'),patch.object(self.rt,'runtime'),patch.object(self.rt,'state',side_effect=lambda:dict(state)),\
                patch.object(self.rt,'save',side_effect=saved),patch.object(self.rt,'advance',side_effect=advanced),\
                patch.object(self.rt,'command',return_value=b'1000'),patch.object(M,'free',return_value=40*1024**3),\
                patch.object(self.rt,'limits',return_value='named-limits'),\
                patch.object(self.rt.B,'create_quiesced_primary_backup',side_effect=create),\
                patch.object(self.rt.B,'verify_quiesced_primary_receipt',side_effect=verify),\
                patch.object(self.rt.B,'file_digest',return_value='c'*64),\
                patch.object(self.rt,'restore_resources',return_value=True),patch.object(self.rt,'pg_rows',return_value=[]):
            self.rt.market_backup()
        self.assertEqual([v[0] for v in calls],['create','verify'])
        self.assertEqual(calls[0][1]['restore_resource_binding'],self.rt.intent['restores'][0])
        self.assertEqual(calls[1][1]['maximum_age_seconds'],7200)
        self.assertNotIn('allow_target_identity_mismatch',calls[0][1]);self.assertNotIn('allow_running_writers',calls[0][1])
        self.assertEqual(state['phase'],'market_backed_up');self.assertIs(state['backup_requests_complete'],True)

    def test_backup_capacity_or_dirty_cleanup_cannot_advance_to_cold_archive(self):
        with patch.object(self.rt,'invariant'),patch.object(self.rt,'runtime'),\
                patch.object(self.rt,'state',return_value={'phase':'writers_quiesced'}),\
                patch.object(self.rt,'command',return_value=b'12000000000'),patch.object(M,'free',return_value=M.RESERVE),\
                patch.object(self.rt.B,'create_quiesced_primary_backup') as create:
            with self.assertRaisesRegex(M.Refusal,'capacity_insufficient'):self.rt.market_backup()
            create.assert_not_called()

    def watch_fixture(self,state,*,alive=True):
        rt=SimpleNamespace(plan={**self.plan,'parent_pid':123,'parent_start_ticks':'456'},digest='f'*64,work=self.rt.work,
            deadline=datetime.now(timezone.utc)+timedelta(seconds=2),state=Mock(return_value=state),record=Mock(),
            proof=Mock(return_value=None),scope=Mock(return_value=True),pg_rows=Mock(return_value=[]),
            restore_resources=Mock(return_value=True),stop_pg=Mock())
        self.stack.enter_context(patch.object(M,'check_descriptors'))
        self.stack.enter_context(patch.object(M,'parent_start',return_value='456' if alive else 'different-start'))
        self.stack.enter_context(patch.object(M,'free',return_value=40*1024**3))
        return rt

    def test_watchdog_survives_transient_metadata_error_then_cleans_acknowledged_failure(self):
        rt=self.watch_fixture({'phase':'backing_up','backup_requests_started':True,'backup_requests_complete':True})
        rt.state.side_effect=[OSError('private'),{'phase':'backing_up','backup_requests_started':True,'backup_requests_complete':True}]
        self.assertFalse(M.watch(rt,[4,5,6],pause=lambda _:None))
        self.assertTrue(any(call.args[0]=='watch-errors' for call in rt.record.call_args_list))
        self.assertTrue(any(call.args[0]=='watch-cleanup' for call in rt.record.call_args_list))

    def test_watchdog_sigkill_parent_retains_fences_and_never_claims_runtime_recovery(self):
        rt=self.watch_fixture({'phase':'writers_quiesced','backup_requests_started':False,'backup_requests_complete':True},alive=False)
        def expire(_):rt.deadline=datetime.now(timezone.utc)-timedelta(seconds=M.CLEANUP+1)
        with patch.object(M.time,'time',side_effect=[1,1,10**20]),patch.object(M.time,'monotonic',return_value=0):
            with self.assertRaisesRegex(M.Refusal,'cleanup_unproven'):M.watch(rt,[4,5,6],pause=expire)
        self.assertFalse(any(call.args[0]=='watch-cleanup' for call in rt.record.call_args_list))
        self.assertTrue(any(call.args[0]=='cleanup-unresolved' for call in rt.record.call_args_list))

    def test_late_create_rpc_remains_unknown_even_after_resource_absence(self):
        rt=self.watch_fixture({'phase':'writers_quiesced','backup_requests_started':True,'backup_requests_complete':False})
        rt.proof.return_value={'reason_code':'current_worker_failure'}
        ticks=iter([1,1,10**20])
        with patch.object(M.time,'time',side_effect=lambda:next(ticks)),patch.object(M.time,'monotonic',return_value=0):
            with self.assertRaisesRegex(M.Refusal,'cleanup_unproven'):M.watch(rt,[4,5,6],pause=lambda _:None)
        rt.restore_resources.assert_any_call(remove=True)
        self.assertFalse(any(call.args[0]=='watch-cleanup' for call in rt.record.call_args_list))
        unresolved=[call for call in rt.record.call_args_list if call.args[0]=='cleanup-unresolved'][0]
        self.assertEqual(unresolved.kwargs['reason_code'],'current_daemon_creation_unknown')

    def test_completed_watch_requires_both_stage_cgroups_empty(self):
        rt=self.watch_fixture({'phase':'complete','backup_requests_complete':True})
        rt.scope.side_effect=[False,True,True]
        self.assertTrue(M.watch(rt,[4,5,6],pause=lambda _:None))
        cleanup=[c for c in rt.record.call_args_list if c.args[0]=='watch-cleanup'][0]
        self.assertIs(cleanup.kwargs['worker_succeeded'],True)

    def test_actual_yaml_order_keeps_qa_stopped_six_stores_first_and_native_timers_last(self):
        import yaml
        from jinja2.nativetypes import NativeEnvironment
        play=yaml.safe_load((ROOT/'deploy/maintenance/web_engine_current_transaction.yml').read_text())[0]
        runtime={k:self.plan[k] for k in ('containers','datastore_ids','units')}
        runtime['units']=[{'name':'timer','kind':'timer','active':True},{'name':'native','kind':'daemon','active':True},
            {'name':'inactive','kind':'daemon','active':False},{'name':'oneshot','kind':'oneshot','active':True}]
        env=NativeEnvironment();actions=[]
        for stage in ('pre_install','install_restore'):
            for task in play['tasks']:
                if stage not in task.get('tags',[]):continue
                loop=task.get('loop',[None])
                if isinstance(loop,str):loop=env.from_string(loop).render(runtime=runtime)
                for item in loop:
                    if 'ansible.builtin.command' in task:
                        argv=[env.from_string(str(v)).render(runtime=runtime,item=item,guard='guard',web_engine_manifest='manifest',
                            web_engine_manifest_sha256='f'*64) for v in task['ansible.builtin.command']['argv']]
                        actions.append(tuple(argv))
                    elif 'ansible.builtin.systemd_service' in task:
                        values=task['ansible.builtin.systemd_service'];name=env.from_string(values['name']).render(item=item)
                        actions.append(('systemd',name,values['state']))
        def index_mode(mode):return next(i for i,a in enumerate(actions) if mode in a)
        market,cold,install=index_mode('market-backup'),index_mode('cold-backup'),index_mode('install')
        stopped=[(i,a[-1]) for i,a in enumerate(actions) if a[:2]==('/usr/bin/docker','stop')]
        stores=set(self.plan['datastore_ids'])
        self.assertEqual({ident for i,ident in stopped if i>market},stores)
        self.assertTrue(all(market<i<cold for i,ident in stopped if ident in stores))
        nonstores={r['id'] for r in self.plan['containers'] if r['running'] and r['id'] not in stores}
        self.assertEqual({ident for i,ident in stopped if i<market},nonstores)
        starts=[(i,a[-1]) for i,a in enumerate(actions) if a[:2]==('/usr/bin/docker','start')]
        self.assertEqual(len(starts),16);self.assertEqual({ident for _,ident in starts[:6]},stores)
        self.assertEqual({ident for _,ident in starts[6:]},nonstores)
        self.assertTrue(all(i>install for i,_ in starts))
        self.assertFalse(set(self.plan['current_profile']['parked_ids'])&{ident for _,ident in starts})
        self.assertGreater(actions.index(('systemd','timer','started')),actions.index(('systemd','native','started')))
        self.assertNotIn(('systemd','oneshot','started'),actions);self.assertNotIn(('systemd','inactive','started'),actions)
        restored=[a for a in actions if a[:2]==('/usr/bin/docker','update') and a[2]!='--restart=no']
        self.assertEqual({a[-1] for a in restored},{r['id'] for r in self.plan['containers'] if r['restart_policy']['Name']!='no'})

    def run_fixture(self,root,*,failure=None):
        marker=root/'permanent.lock';marker.write_bytes(b'public marker\n');marker.chmod(0o600)
        control=root/'controls';control.mkdir(mode=0o700)
        for name,value in (('MARKER',marker),('SOURCE_LOCK',control/'source.lock'),('RESERVATION',control/'controller.lock'),('JOURNAL',control/'journal.json')):
            self.stack.enter_context(patch.object(M,name,value))
        self.rt.work=root/'new-work';state={};timeline=[]
        self.stack.enter_context(patch.object(self.rt,'preflight'))
        self.stack.enter_context(patch.object(self.rt,'invariant'))
        self.stack.enter_context(patch.object(self.rt,'runtime'))
        self.stack.enter_context(patch.object(self.rt,'state',side_effect=lambda:dict(state)))
        self.stack.enter_context(patch.object(self.rt,'save',side_effect=lambda value,**_:state.update(value)))
        self.stack.enter_context(patch.object(self.rt,'record',side_effect=lambda name,**_:timeline.append(name)))
        self.stack.enter_context(patch.object(self.rt,'scope',side_effect=lambda stage,**_:(timeline.append('stop-'+stage),(_ for _ in ()).throw(M.Refusal('current_stop_unknown')))[1]))
        self.stack.enter_context(patch.object(M,'document',return_value=self.window))
        self.stack.enter_context(patch.object(M,'parent_start',return_value='12345'))
        self.stack.enter_context(patch.object(self.recipe.C.B,'private_parent',side_effect=lambda path:self.assertTrue(Path(path).is_relative_to(root))))
        def exclusive(source,target):
            self.assertTrue(Path(target).is_relative_to(root));os.link(source,target);Path(source).unlink()
        self.stack.enter_context(patch.object(self.recipe.C.B,'publish_noreplace',side_effect=exclusive))
        self.stack.enter_context(patch.object(self.recipe.C,'ansible_preflight'))
        watcher=Mock();watcher.poll.return_value=None
        def launch(rt,descriptors,path):
            self.assertEqual(len(descriptors),3)
            for fd,target in zip(descriptors,(M.MARKER,M.SOURCE_LOCK,M.RESERVATION)):
                self.assertEqual(os.fstat(fd).st_ino,target.stat().st_ino)
                other=os.open(target,os.O_RDONLY)
                try:
                    with self.assertRaises(BlockingIOError):fcntl.flock(other,fcntl.LOCK_EX|fcntl.LOCK_NB)
                finally:os.close(other)
            timeline.append('watch-launch');return watcher
        self.stack.enter_context(patch.object(M,'launch_watch',side_effect=launch))
        self.stack.enter_context(patch.object(M,'await_watch',side_effect=lambda *a,**kw:timeline.append('watch-ready' if kw.get('ready') else 'watch-cleanup') or
            {'worker_succeeded':True,'all_owned_resources_absent':True}))
        def stage(plan,digest,argv,env,deadline,descriptors,name):
            timeline.append(name);self.assertIn('C.UTF-8',env.values())
            if failure:raise failure
            state['phase']='backup_ready' if name=='pre_install' else 'complete';return 0
        self.stack.enter_context(patch.object(self.recipe.C,'execute_stage',side_effect=stage))
        return marker,state,timeline

    def test_parent_actual_fresh_flocks_watch_arm_two_stages_then_exact_fence_release(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);marker,state,timeline=self.run_fixture(root)
            before=marker.stat().st_ino
            result=M.run(self.rt,M.ROOT/'plan.json',apply=True)
            self.assertEqual(result['status'],'completed');self.assertIs(result['security_accepted'],False)
            self.assertEqual(timeline,['watch-launch','watch-ready','pre_install','install_restore','watch-cleanup'])
            self.assertEqual(marker.stat().st_ino,before);self.assertFalse(M.SOURCE_LOCK.exists());self.assertFalse(M.RESERVATION.exists())
            self.assertEqual(state['phase'],'complete');self.assertEqual(self.rt.preflight.call_count,2)

    def test_parent_publishes_failure_before_stop_rpc_timeout_and_retains_ambiguous_fences(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);failure=self.recipe.C.StageLivenessError('stage_descendant_quiescence_unproven')
            marker,state,timeline=self.run_fixture(root,failure=failure)
            with self.assertRaises(self.recipe.C.StageLivenessError):M.run(self.rt,M.ROOT/'plan.json',apply=True)
            self.assertLess(timeline.index('failure-requested'),timeline.index('stop-pre_install'))
            self.assertTrue(M.SOURCE_LOCK.exists());self.assertTrue(M.RESERVATION.exists());self.assertTrue(marker.exists())

    def test_complete_public_runtime_schema_accepts_current_27_and_known_health_only(self):
        rows={r['id']:{**deepcopy(r),'health':'unhealthy' if r['id'] in self.recipe.R.KNOWN_HEALTH else None}
            for r in self.plan['containers']}
        running={k for k,v in rows.items() if v['running']}
        def command(argv):
            if argv==['/usr/bin/docker','ps','-aq','--no-trunc']:return '\n'.join(rows)
            if argv[:3]==['/usr/bin/docker','info','--format']:
                if 'CgroupDriver' in argv[-1]:return json.dumps({'driver':'systemd','version':'2'})
                return json.dumps({'root':'/var/lib/docker','driver':'overlayfs','driver_status':[['driver-type','io.containerd.snapshotter.v1']],
                    'live_restore':False})
            if argv[:3]==['/usr/bin/docker','inspect','--format']:
                self.assertNotIn('Config.Env',argv[3]);return json.dumps(rows[argv[-1]])
            base=['/usr/bin/ctr','--address','/run/containerd/containerd.sock']
            if argv==base+['namespaces','list','--quiet']:return 'moby\nmoby_history'
            if argv==base+['--namespace','moby','tasks','list','--quiet']:return '\n'.join(running)
            if argv==base+['--namespace','moby_history','tasks','list','--quiet']:return ''
            if argv==base+['plugins','list']:return 'TYPE ID PLATFORMS STATUS\nio.containerd.snapshotter.v1 overlayfs linux/amd64 ok'
            self.fail('unlisted native argv '+repr(argv))
        with patch.object(self.rt,'parked_storage'),patch.object(self.recipe.C.B,'command',side_effect=command):
            self.assertEqual(set(self.rt.runtime('baseline')),self.recipe.R.KNOWN_HEALTH)
            parked=next(iter(self.plan['current_profile']['parked_ids']));rows[parked]['running']=True
            with self.assertRaisesRegex(M.Refusal,'container_intent'):self.rt.runtime('baseline')
            rows[parked]['running']=False
            target=next(k for k,v in rows.items() if v['running'] and k not in self.recipe.R.KNOWN_HEALTH)
            rows[target]['health']='unhealthy'
            with self.assertRaisesRegex(M.Refusal,'unexpected_health'):self.rt.runtime('baseline')
            rows[target]['health']=None;rows[target]['mounts'][0]['Source']='/foreign'
            with self.assertRaisesRegex(M.Refusal,'container_intent'):self.rt.runtime('baseline')

    def test_daemon_stopped_cold_phase_does_not_query_source_database_or_running_containerd(self):
        # Current quiesced controls are checked without calling the running
        # daemon again; a failed DB query here would strand stopped services.
        state={'phase':'quiesced'}
        with patch.object(self.rt,'invariant'),patch.object(self.rt,'state',return_value=state),\
                patch.object(self.recipe.C,'engine_stopped'),patch.object(self.rt,'no_native_jobs'),\
                patch.object(self.rt,'verified_fresh_dump',side_effect=M.Refusal('current_named_stop')),\
                patch.object(self.rt,'runtime',side_effect=AssertionError('stopped_daemon_query_forbidden')):
            with self.assertRaisesRegex(M.Refusal,'named_stop'):self.rt.cold_backup()

    def test_actual_backup_limits_pin_deadline_and_throttle_only_expensive_checks(self):
        self.rt.cutoff=datetime.now(timezone.utc)+timedelta(seconds=1200)
        with patch.object(M,'free',return_value=40*1024**3),patch.object(self.rt,'marker') as marker,\
                patch.object(self.rt,'owners') as owners,patch.object(self.rt,'watch_alive',return_value=True),\
                patch.object(M.time,'monotonic',side_effect=[1,1,1.01,1.02,2.1,2.1]):
            limits=self.rt.limits(123456)
            for _ in range(4):limits.checkpoint()
        self.assertEqual(limits.daemon_deadline_epoch_seconds,int(self.rt.cutoff.timestamp()))
        self.assertEqual(limits.pg_application_name,'engine8-'+self.plan['nonce']);self.assertEqual(limits.dump_max_bytes,123456)
        self.assertEqual(marker.call_count,2);self.assertEqual(owners.call_count,2)

    def cold_fixture(self,root,*,tamper=False,durable=True,fresh=None):
        self.rt.work=root;provided=fresh is not None
        if not provided:
            dump=root/'fresh.dump';dump.write_bytes(b'public fresh dump');dump.chmod(0o600)
            fresh={'path':str(dump),'size_bytes':dump.stat().st_size,'sha256':sha256(dump.read_bytes()).hexdigest()}
        else:dump=Path(fresh['path'])
        state={'phase':'quiesced','fresh_quiesced_backup':fresh};events=[]
        budget={'fresh_quiesced_dump_included':True,'cold_archive_inputs':['/var/lib/docker',str(dump)],
            'capacity':{'archive_max_bytes':10000,'ciphertext_max_bytes':14096}}
        self.stack.enter_context(patch.object(self.rt,'invariant'))
        self.stack.enter_context(patch.object(self.rt,'state',side_effect=lambda:dict(state)))
        if not provided:self.stack.enter_context(patch.object(self.rt,'verified_fresh_dump',return_value=fresh))
        self.stack.enter_context(patch.object(self.rt,'archive_plan',return_value=budget))
        self.stack.enter_context(patch.object(self.rt,'no_native_jobs'))
        self.stack.enter_context(patch.object(self.recipe.C,'engine_stopped'))
        self.stack.enter_context(patch.object(self.rt,'advance',side_effect=lambda phase,**v:state.update(phase=phase,**v)))
        self.stack.enter_context(patch.object(M,'free',return_value=40*1024**3))
        def tar(argv,**kwargs):
            self.assertEqual(argv[0],'/usr/bin/tar');self.assertIn('--one-file-system',argv)
            self.assertIn(str(dump).lstrip('/'),argv);self.assertLessEqual(kwargs['maximum'],10000)
            os.write(kwargs['stdout'],b'public synthetic cold archive');events.append('tar-create');return ''
        self.stack.enter_context(patch.object(self.recipe.C,'bounded',side_effect=tar))
        def command(argv,**kwargs):
            plain=root/'archive/engine.tar.gz'
            if argv[0]=='/usr/bin/tar':
                self.assertIn('--compare',argv);events.append('tar-compare');return b''
            self.assertEqual(argv[:5],['/usr/bin/python3','-I','-B',str(M.SOURCE/'scripts/crypt_market_pipeline_backup.py'),'encrypt'])
            self.assertEqual(events,['tar-create','tar-compare']);events.append('encrypt')
            cipher=root/'archive/engine.enc';cipher.write_bytes(b'public cipher only');cipher.chmod(0o600)
            proof={'schema':'market_pipeline_backup_encryption/1.1','plaintext_materialized_offhost':False,'secrets_disclosed':False,
                'plaintext_size_bytes':plain.stat().st_size,'plaintext_sha256':sha256(plain.read_bytes()).hexdigest(),
                'ciphertext_size_bytes':cipher.stat().st_size,'ciphertext_sha256':sha256(cipher.read_bytes()).hexdigest()}
            receipt=root/'archive/encryption.json';receipt.write_bytes(raw(proof));receipt.chmod(0o600)
            if tamper:
                replacement=root/'archive/other';replacement.write_bytes(plain.read_bytes());replacement.chmod(0o600);os.replace(replacement,plain)
            return b''
        self.stack.enter_context(patch.object(self.rt,'command',side_effect=command))
        def document(path,digest=None):
            self.assertIn(path,{root/'archive/encryption.json',root/'market/market-pipeline-backup-receipt.json',self.rt.B._journal_path(root/'market')})
            value=path.read_bytes()
            if digest is not None:self.assertEqual(sha256(value).hexdigest(),digest)
            return json.loads(value)
        self.stack.enter_context(patch.object(M,'document',side_effect=document))
        def offhost(mode,**kwargs):
            events.append(mode)
            if mode=='capacity':return {'max_cipher_bytes':14096}
            if mode=='receive':
                self.assertEqual(kwargs['stream'].read(),b'public cipher only')
                self.assertEqual(os.fstat(kwargs['stream'].fileno()).st_ino,(root/'archive/engine.enc').stat().st_ino)
            return {'fsync_completed':durable,'plaintext_materialized_offhost':False,
                'ciphertext_size_bytes':kwargs['size'],'ciphertext_sha256':kwargs['digest']}
        self.stack.enter_context(patch.object(self.rt,'offhost',side_effect=offhost))
        return state,events

    def test_real_artifact_fds_compare_encrypt_and_send_only_cipher_then_separate_verify(self):
        with tempfile.TemporaryDirectory() as folder:
            state,events=self.cold_fixture(Path(folder));self.rt.cold_backup()
            self.assertEqual(events,['tar-create','tar-compare','encrypt','capacity','receive','verify'])
            self.assertEqual(state['phase'],'backup_ready');self.assertIs(state['fresh_dump_in_cold_archive'],True)
            self.assertIs(state['redundant_plaintext_retained'],True)

    def test_plain_archive_aba_during_crypto_or_missing_remote_fsync_refuses_package_gate(self):
        for tamper,durable in ((True,True),(False,False)):
            with self.subTest(tamper=tamper),tempfile.TemporaryDirectory() as folder:
                with ExitStack() as part:
                    original=self.stack;self.stack=part
                    try:
                        state,events=self.cold_fixture(Path(folder),tamper=tamper,durable=durable)
                        with self.assertRaises((M.Refusal,self.recipe.B.BackupError)):self.rt.cold_backup()
                        self.assertEqual(state['phase'],'backing_up')
                        if tamper:self.assertNotIn('receive',events)
                    finally:self.stack=original

    def test_watchdog_health_gate_refuses_errors_cleanup_or_inactive_scope(self):
        for alive,proofs in ((False,{'watch-ready':{}}),(True,{'watch-ready':{},'watch-errors':{}}),
                (True,{'watch-ready':{},'watch-cleanup':{}})):
            with patch.object(self.rt,'watch_alive',return_value=alive),patch.object(self.rt,'proof',side_effect=lambda name:proofs.get(name)):
                with self.assertRaisesRegex(M.Refusal,'not_healthy'):M.Runtime.assert_watchdog(self.rt)

    def test_second_publication_failure_removes_only_own_source_and_preserves_foreign_reservation(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);marker,state,timeline=self.run_fixture(root);before=marker.stat().st_ino
            original=self.recipe.C.B.publish_noreplace.side_effect;calls=[]
            def publication(source,target):
                calls.append(target)
                if len(calls)==2:
                    target.write_bytes(b'foreign immutable reservation\n');target.chmod(0o600)
                    raise M.Refusal('current_foreign_reservation')
                return original(source,target)
            self.recipe.C.B.publish_noreplace.side_effect=publication
            with self.assertRaisesRegex(M.Refusal,'foreign_reservation'):M.run(self.rt,M.ROOT/'plan.json',apply=True)
            self.assertFalse(M.SOURCE_LOCK.exists());self.assertEqual(M.RESERVATION.read_bytes(),b'foreign immutable reservation\n')
            self.assertEqual(marker.stat().st_ino,before);self.assertEqual(timeline,[]);self.assertEqual(state,{})

    def test_journal_or_postpublication_initialization_failure_removes_exact_own_fences_without_actions(self):
        for postpublish in (False,True):
            with self.subTest(postpublish=postpublish),tempfile.TemporaryDirectory() as folder:
                with ExitStack() as part:
                    original_stack=self.stack;self.stack=part
                    try:
                        marker,state,timeline=self.run_fixture(Path(folder));before=marker.stat().st_ino
                        if postpublish:
                            original=self.recipe.C.B.publish_noreplace.side_effect;calls=[]
                            def publication(source,target):
                                original(source,target);calls.append(target)
                                if len(calls)==2:raise OSError('named postpublication failure')
                            self.recipe.C.B.publish_noreplace.side_effect=publication
                        else:self.rt.save.side_effect=OSError('named journal failure')
                        with self.assertRaises(OSError):M.run(self.rt,M.ROOT/'plan.json',apply=True)
                        self.assertFalse(M.SOURCE_LOCK.exists());self.assertFalse(M.RESERVATION.exists())
                        self.assertEqual(marker.stat().st_ino,before);self.assertEqual(timeline,[])
                    finally:self.stack=original_stack

    def test_proc_metadata_reads_request_the_bound_before_allocating_or_parsing(self):
        class Bounded(io.BytesIO):
            def read(inner,count=-1):
                self.assertIn(count,(8193,2*1024**2+1));return super().read(count)
        with patch.object(Path,'open',return_value=Bounded(b'x'*9000)):
            with self.assertRaisesRegex(M.Refusal,'parent_identity'):M.parent_start(123)
        owner={'schema':'web_engine_current_owner/1','operation_id':M.OP,'nonce':self.plan['nonce'],'manifest_sha256':self.rt.digest,'pid':123}
        with patch.object(M,'document',return_value=owner),patch.object(Path,'open',return_value=Bounded(b'x'*(2*1024**2+2))):
            with self.assertRaisesRegex(M.Refusal,'locks_bound'):self.rt.owners()

    def test_real_quiesced_primary_receipt_four_field_backup_reaches_cold_phase_and_wrong_format_refuses(self):
        from tests import test_backup_quiesced_primary as Q
        q=Q.QuiescedPrimaryTests();q.setUp()
        try:
            q.backup_dir=q.root/'market';q.receipt=q.backup_dir/'market-pipeline-backup-receipt.json'
            q.values.update(MARKET_PIPELINE_RELEASE_SHA=self.recipe.R.CONFIGURATION['release_sha'],MARKET_PIPELINE_IMAGE=self.recipe.R.IMAGE['image_id'])
            Q.fixtures._write_env(q.env_file,q.values)
            q.common.update(receipt=q.receipt,release_sha=self.recipe.R.CONFIGURATION['release_sha'],
                release_tree=self.recipe.R.CONFIGURATION['release_tree'],image_id=self.recipe.R.IMAGE['image_id'],
                image_input_signature=self.recipe.R.IMAGE['image_input_signature'])
            q.source['container_id']=self.rt.intent['database']['container_id'];q.resources=self.rt.intent['restores'][0]
            created=q.create();self.assertEqual(set(created['backup']),{'format','path','size_bytes','sha256'})
            fresh={'schema':'web_engine_fresh_quiesced_operand/1','operation_id':M.OP,'nonce':self.plan['nonce'],'backup_mode':'quiesced',
                **{k:created[k] for k in ('release_sha','release_tree','image_id','image_input_signature')},
                **{k:created['backup'][k] for k in ('path','size_bytes','sha256')},'receipt_sha256':Q.B.file_digest(q.receipt),
                'journal_sha256':Q.B.file_digest(Q.B._journal_path(q.backup_dir)),'restore_status':'PASS','cleanup_status':'PASS'}
            validate=self.recipe.A.fresh_operand
            def fixture_path(value,profile):
                self.assertEqual(Path(value['path']).parent,q.root/'market')
                validate({**value,'path':str(M.work_root(value['nonce'])/'market'/Path(value['path']).name)},profile)
                return value
            self.stack.enter_context(patch.object(self.recipe.A,'fresh_operand',side_effect=fixture_path))
            state,events=self.cold_fixture(q.root,fresh=fresh);self.rt.cold_backup()
            self.assertEqual(state['phase'],'backup_ready');self.assertEqual(events[-2:],['receive','verify'])
            before=list(events);created['backup']['format']='pg_plain';q.receipt.write_bytes(raw(created));fresh['receipt_sha256']=Q.B.file_digest(q.receipt)
            state['phase']='quiesced'
            with self.assertRaisesRegex(M.Refusal,'fresh_backup_controls'):self.rt.cold_backup()
            self.assertEqual(events,before)
        finally:q.tearDown();q.doCleanups()

    def test_owned_mutable_control_retries_real_atomic_phase_replace_without_cancelling_stage(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);path=root/'journal.json';self.stack.enter_context(patch.object(M,'JOURNAL',path))
            value={'schema':'web_engine_current_transaction/1','operation_id':M.OP,'nonce':self.plan['nonce'],'manifest_sha256':self.rt.digest,
                'phase':'healthy','backup_requests_started':True,'backup_requests_complete':True}
            path.write_bytes(raw(value));path.chmod(0o600);value['phase']='complete'
            original_pread=os.pread;count=[]
            def atomic(fd,maximum,offset):
                data=original_pread(fd,maximum,offset);count.append(fd)
                if len(count)==1:
                    other=root/'next.json';other.write_bytes(raw(value));other.chmod(0o600);os.replace(other,path)
                return data
            original_lstat=Path.lstat
            def safe_tmp(item):
                info=original_lstat(item)
                if item==Path('/tmp'):
                    values=list(info);values[0]=0o40755;return os.stat_result(values)
                return info
            rt=self.watch_fixture(value);rt.state=self.rt.state
            with patch.object(M,'read',PUBLIC_READ),patch.object(Path,'lstat',safe_tmp),patch.object(os,'pread',side_effect=atomic),patch.object(M.time,'sleep'):
                self.assertTrue(M.watch(rt,[4,5,6],pause=lambda _:None))
            self.assertEqual(len(count),2);self.assertTrue(all(call.kwargs.get('stop') is not True for call in rt.scope.call_args_list))

    def test_owned_mutable_control_refuses_repeated_instability_and_stable_wrong_owner(self):
        for error in (M.Refusal('current_public_changed'),None):
            calls=[]
            def document(path):
                calls.append(path)
                if error:raise error
                return {'schema':'web_engine_current_transaction/1','operation_id':M.OP,'nonce':'foreign','manifest_sha256':self.rt.digest,
                    'phase':'complete','backup_requests_started':True,'backup_requests_complete':True}
            with patch.object(M,'document',side_effect=document),patch.object(M.time,'sleep'):
                with self.assertRaises(M.Refusal):self.rt.state()
            self.assertEqual(len(calls),3 if error else 1)
        with self.assertRaisesRegex(M.Refusal,'mutable_control_scope'):
            self.rt.mutable_control(M.ROOT/'window.json',lambda _:None)

    def test_real_public_reader_denies_symlink_hardlink_and_inplace_replacement(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);path=root/'public.json';path.write_bytes(b'{}\n');path.chmod(0o600)
            # /tmp is intentionally world-writable, so the owned test root
            # replaces only the named parent-metadata boundary for this test.
            original=Path.lstat
            def metadata(item):
                info=original(item)
                if item==Path('/tmp'):
                    values=list(info);values[0]=0o40755;return os.stat_result(values)
                return info
            with patch.object(Path,'lstat',metadata),patch.object(M,'read',PUBLIC_READ):
                self.assertEqual(M.read(path),b'{}\n')
                os.link(path,root/'other')
                with self.assertRaisesRegex(M.Refusal,'metadata'):M.read(path)
                (root/'other').unlink();path.unlink();path.symlink_to(root/'missing')
                with self.assertRaises((M.Refusal,OSError)):M.read(path)


if __name__=='__main__':unittest.main()
