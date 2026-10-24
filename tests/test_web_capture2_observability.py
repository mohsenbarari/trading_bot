import copy
from contextlib import ExitStack
import importlib.util
import json
import fcntl
import os
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('capture_observability',Path(__file__).resolve().parents[1]/'deploy/maintenance/web_capture2_observability.py')
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)
OLD = '9'*64
NEW = 'a'*64
RESTORED = 'b'*64
BASE = 'sha256:'+'2'*64
CANDIDATE = 'sha256:'+'3'*64
ROOT = Path('/root/.local/public-synthetic-compose')

def environment(items):
    return dict(item.split('=',1) for item in items)

def settings():
    return SimpleNamespace(CID=OLD,BASE=BASE,CANDIDATE=CANDIDATE,FUNCTIONAL_IMAGE=CANDIDATE,
        FUNCTIONAL_OVERLAY=ROOT/'functional.yml',FUNCTIONAL_LABELS={'io.gold-trade.capture.replay.status':'functional_fix_not_security_acceptance'},
        PATCHED_SOURCE='f'*64,ROOT=ROOT,
        ROLE_ENV=Path('/srv/public-synthetic-release/web.release.env'),PROJECT='market-private-pipeline-primary',
        SERVICE='market-capture-account2',DOCKER='/usr/bin/docker',environment=environment)

def rows(ident=OLD,image=BASE,*,running=True):
    V = settings()
    provenance = {'com.docker.compose.project':V.PROJECT,'com.docker.compose.service':V.SERVICE,
        'com.docker.compose.container-number':'1','com.docker.compose.oneoff':'False','com.docker.compose.depends_on':'',
        'com.docker.compose.config-hash':'c'*64,'com.docker.compose.image':image,
        'com.docker.compose.project.config_files':','.join(str(ROOT/name) for name in ('compose.yml','compose.web.yml','capture2-image-only.yml'))+','+str(V.FUNCTIONAL_OVERLAY),
        'com.docker.compose.project.working_dir':str(ROOT),'com.docker.compose.project.environment_file':str(V.ROLE_ENV),
        'com.docker.compose.version':'2.40.3'}
    if image == BASE and ident != OLD:
        provenance['com.docker.compose.project.config_files'] += ','+str(M.TASK_ROOT/'rollback.yml')
    labels = {'org.opencontainers.image.revision':'4'*40,**provenance}
    if image == CANDIDATE:
        labels.update({'io.gold-trade.observability.base-image':BASE,'io.gold-trade.observability.source-sha256':V.PATCHED_SOURCE,
            'io.gold-trade.observability.status':'diagnostic_not_security_acceptance',**V.FUNCTIONAL_LABELS})
    target = {'id':ident,'name':'/'+V.PROJECT+'-'+V.SERVICE+'-1','image':image,'running':running,'restarting':False,
        'pid':123 if running else 0,'started':'fixed','restarts':0,
        'config':{'Env':['PRIVATE=synthetic-do-not-emit'],'Image':image,'Hostname':ident[:12],'Labels':labels,
                  'Cmd':['service','--role',V.SERVICE],'User':'10001:10001'},
        'host':{'ReadonlyRootfs':True},'mounts':[{'Source':M.SESSION,'Destination':'/var/lib/market-data/session','Type':'bind','RW':True}],
        'networks':{}}
    result = {f'{n:064x}':{'id':f'{n:064x}','name':'/other-'+str(n),'image':BASE,'running':False,'restarting':False,
        'pid':0,'started':'fixed','restarts':0,'config':{'Labels':{},'Env':['OTHER=synthetic-do-not-emit']},'host':{},'mounts':[],'networks':{}} for n in range(26)}
    result[ident] = target
    return result

class CaptureObservabilityTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        for name in ('subprocess.Popen','subprocess.run','os.system','socket.socket','socket.create_connection','ctypes.CDLL'):
            self.stack.enter_context(patch(name,side_effect=AssertionError('native_action_forbidden')))

    def run_operation(self, snapshots, desired):
        commands = []
        def command(V,clock,argv,seconds=30):
            commands.append(argv)
            return (BASE+'\n').encode() if 'inspect' in argv else b''
        with patch.object(M,'snapshot',side_effect=snapshots),patch.object(M,'command',side_effect=command),patch.object(M,'legacy',return_value={'persistent_boot_guard_verified':False}):
            result = M.stop_and_start(None,settings(),SimpleNamespace(),rows(),desired)
        return result,commands

    def test_candidate_normal_stop_before_start_and_complete_private_parity(self):
        result,commands = self.run_operation([rows(),rows(running=False),rows(NEW,CANDIDATE,running=False),rows(NEW,CANDIDATE)],CANDIDATE)
        self.assertEqual(result[0]['id'],NEW)
        self.assertIn('stop',commands[0])
        self.assertIn('up',commands[1])
        for flag in ('--no-deps','--no-build','--pull','never','--force-recreate'):
            self.assertIn(flag,commands[1])
        self.assertIn('--no-start',commands[1])
        self.assertEqual(commands[2],['/usr/bin/docker','start',NEW])

    def test_missing_target_after_failed_create_can_restore_exact_base(self):
        absent = rows()
        absent.pop(OLD)
        result,commands = self.run_operation([absent,rows(RESTORED,BASE,running=False),rows(RESTORED,BASE)],BASE)
        self.assertEqual(result[0]['id'],RESTORED)
        self.assertNotIn('stop',commands[0])
        self.assertIn(str(M.TASK_ROOT/'rollback.yml'),commands[0])

    def test_failed_candidate_private_parity_then_replacement_cid_rollback(self):
        bad = rows(NEW,CANDIDATE)
        bad[NEW]['config']['Env'] = ['PRIVATE=changed-secret']
        with self.assertRaisesRegex(RuntimeError,'capture_private_environment_changed'):
            self.run_operation([rows(),rows(running=False),{**bad,NEW:{**bad[NEW],'running':False,'pid':0}}],CANDIDATE)
        result,commands = self.run_operation([bad,rows(NEW,CANDIDATE,running=False),rows(RESTORED,BASE,running=False),rows(RESTORED,BASE)],BASE)
        self.assertEqual(result[0]['image'],BASE)
        self.assertIn('stop',commands[0])

    def test_absent_target_with_changed_other_scope_is_refused_before_start(self):
        absent = rows()
        absent.pop(OLD)
        absent.pop(next(iter(absent)))
        with patch.object(M,'snapshot',return_value=absent),patch.object(M,'command') as command:
            with self.assertRaises(RuntimeError):
                M.stop_and_start(None,settings(),SimpleNamespace(),rows(),BASE)
            command.assert_not_called()

    def test_old_pid_must_be_zero_before_up(self):
        stopped = rows(running=False)
        stopped[OLD]['pid'] = 123
        commands = []
        with patch.object(M,'snapshot',side_effect=[rows(),stopped]),patch.object(M,'command',side_effect=lambda *a,**k:commands.append(a[2]) or b''):
            with self.assertRaisesRegex(RuntimeError,'old_session_owner_not_stopped'):
                M.stop_and_start(None,settings(),SimpleNamespace(),rows(),CANDIDATE)
        self.assertTrue(all('up' not in row for row in commands))

    def test_same_ancestor_and_descendant_session_binds_refused(self):
        for source in (M.SESSION,str(Path(M.SESSION).parent),M.SESSION+'/nested'):
            inventory = rows()
            other = inventory[f'{0:064x}']
            other.update(running=True,mounts=[{'Source':source}])
            with self.subTest(source=source),self.assertRaisesRegex(RuntimeError,'other_session_mount_owner'):
                M.same_session_owner(inventory,OLD)

    def test_unknown_host_and_existing_other_configuration_changes_refuse(self):
        candidate = rows(NEW,CANDIDATE)[NEW]
        candidate['host']['UnknownPrivilege'] = True
        with self.assertRaisesRegex(RuntimeError,'capture_host_configuration_changed'):
            M.post_parity(settings(),rows()[OLD],candidate,CANDIDATE)
        after = rows(NEW,CANDIDATE)
        after[f'{0:064x}']['config']['Env'] = ['OTHER=changed']
        with self.assertRaisesRegex(RuntimeError,'other_container_runtime_changed'):
            M.other_runtime(rows(),after,OLD,NEW)

    def test_only_bound_existing_incident_state_changes_are_admitted_and_recorded(self):
        before,after = rows(),rows(NEW,CANDIDATE)
        selected = frozenset({f'{0:064x}',f'{1:064x}'})
        after[f'{0:064x}']['restarts'] = 4
        M.other_runtime(before,after,OLD,NEW,known_incidents=selected)
        proof = M.incident_deltas(before,after,selected)
        self.assertEqual(proof[0]['incident_status'],'open')
        self.assertEqual(proof[0]['after']['restarts'],4)
        after[f'{0:064x}']['config']['Env'] = ['OTHER=changed']
        with self.assertRaisesRegex(RuntimeError,'other_container_runtime_changed'):
            M.other_runtime(before,after,OLD,NEW,known_incidents=selected)

    def test_verified_2403_replace_label_uses_the_fixed_service_replica(self):
        before = rows()[OLD]
        candidate = rows(NEW,CANDIDATE)[NEW]
        candidate['config']['Labels']['com.docker.compose.replace'] = settings().SERVICE+'-1'
        M.post_parity(settings(),before,candidate,CANDIDATE,replaced=OLD)
        restored = rows(RESTORED,BASE)[RESTORED]
        restored['config']['Labels']['com.docker.compose.replace'] = settings().SERVICE+'-1'
        M.post_parity(settings(),before,restored,BASE,replaced=NEW)
        restored['config']['Labels']['com.docker.compose.replace'] = OLD
        with self.assertRaisesRegex(RuntimeError,'capture_replace_provenance_invalid'):
            M.post_parity(settings(),before,restored,BASE,replaced=OLD)
        candidate['config']['Labels']['com.docker.compose.version'] = '2.38.2'
        with self.assertRaisesRegex(RuntimeError,'capture_compose_version_unverified'):
            M.post_parity(settings(),before,candidate,CANDIDATE,replaced=OLD)

    def test_absent_target_create_rejects_unowned_replace_label(self):
        restored = rows(RESTORED,BASE)[RESTORED]
        restored['config']['Labels']['com.docker.compose.replace'] = OLD
        with self.assertRaisesRegex(RuntimeError,'capture_replace_provenance_invalid'):
            M.post_parity(settings(),rows()[OLD],restored,BASE,replaced=None)

    def test_only_static_error_schema_is_extracted_from_private_log_buffer(self):
        valid = {'status':'fail','reason_code':'runtime_dependency_failure','error_type':'PermissionError','origin':'telethon_sqlite_session','origin_line':88}
        hostile = {**valid,'error_type':['private-value']}
        raw = b'private-provider-xml-do-not-emit\n'+json.dumps(hostile).encode()+b'\n'+json.dumps(valid).encode()
        V = settings()
        V.bounded = lambda *a,**k:(0,b'',raw)
        clock = SimpleNamespace(seconds=lambda value:value)
        self.assertEqual(M.diagnostics(V,clock,NEW),[valid])

    def test_diagnostics_poll_is_finite_and_does_not_claim_repair(self):
        with patch.object(M,'diagnostics',return_value=[]),patch.object(M.time,'monotonic',side_effect=[0,61]):
            self.assertEqual(M.poll_diagnostics(settings(),SimpleNamespace(seconds=lambda x:x),NEW),[])

    def test_static_error_codes_never_echo_private_exception_text(self):
        self.assertEqual(M.code(ValueError('secret-value')),'capture_diagnostic_operation_failed')
        self.assertEqual(M.code(RuntimeError('/private/path secret-value')),'capture_diagnostic_operation_failed')

    def test_full_mount_record_permutation_is_semantically_equal(self):
        before = rows()
        ident = f'{0:064x}'
        before[ident]['mounts'] = [
            {'Destination':'/one','Source':'/public/one','RW':False,'Type':'bind','UnrecognizedField':{'kept':True}},
            {'Destination':'/two','Source':'/public/two','RW':True,'Type':'bind'}]
        after = copy.deepcopy(before)
        after[ident]['mounts'].reverse()
        M.other_runtime(before,after,OLD)
        after[ident]['mounts'][1]['UnrecognizedField']['kept'] = False
        with self.assertRaisesRegex(RuntimeError,'other_container_runtime_changed'):
            M.other_runtime(before,after,OLD)
        after = copy.deepcopy(before)
        after[ident]['mounts'][0]['Source'] = '/changed/source'
        with self.assertRaisesRegex(RuntimeError,'other_container_runtime_changed'):
            M.other_runtime(before,after,OLD)

    def test_duplicate_mount_destination_is_rejected_even_when_records_match(self):
        before = rows()
        ident = f'{0:064x}'
        before[ident]['mounts'] = [{'Destination':'/one','Source':'/public/one'},
                                  {'Destination':'/one','Source':'/public/two'}]
        with self.assertRaisesRegex(RuntimeError,'duplicate_mount_destination'):
            M.other_runtime(before,copy.deepcopy(before),OLD)

    def test_safe_diagnostic_is_saved_before_a_postconfiguration_failure(self):
        bad = rows(NEW,CANDIDATE)
        bad[NEW]['host']['Unexpected'] = True
        fixed = [{'status':'fail','reason_code':'runtime_dependency_failure','error_type':'PermissionError',
                  'origin':'telethon_sqlite_session','origin_line':88}]
        saved = []
        with patch.object(M,'snapshot',side_effect=[rows(),rows(running=False),rows(NEW,CANDIDATE,running=False),bad]),patch.object(M,'command',return_value=b''),patch.object(M,'legacy',return_value={}),patch.object(M,'poll_diagnostics',return_value=fixed):
            with self.assertRaisesRegex(RuntimeError,'capture_host_configuration_changed'):
                M.stop_and_start(None,settings(),SimpleNamespace(),rows(),CANDIDATE,
                    diagnostic_sink=lambda ident,proof:saved.append((ident,copy.deepcopy(proof))))
        self.assertEqual(saved,[(NEW,fixed)])

    def test_current_inventory_profile_changes_only_the_typed_capture_identity(self):
        V = settings()
        V.AFTER_CAPTURE05_ID = NEW
        plan = {'schema':'web_engine_transaction_plan/1','operation_id':'original',
                'containers':[copy.deepcopy(row) for row in rows().values()], 'datastore_probes':['unchanged']}
        original = copy.deepcopy(plan)
        profile = M.current_incident_profile(plan,V)
        self.assertEqual(plan,original)
        self.assertEqual(profile['schema'],'capture2_current_inventory/1')
        self.assertEqual(profile['original_operation_id'],'original')
        self.assertEqual(profile['datastore_probes'],plan['datastore_probes'])
        self.assertEqual({row['id'] for row in profile['containers']},(set(rows())-{OLD})|{NEW})

    def test_capture_readiness_admits_datastore_healthcheck_exec_ids(self):
        C = SimpleNamespace(ExecIDs=['ordinary-healthcheck-exec'])
        P = SimpleNamespace(inventory=unittest.mock.Mock(),web_datastores_native=unittest.mock.Mock())
        R = SimpleNamespace(hot_finished=unittest.mock.Mock(),no_backup_exec=unittest.mock.Mock(side_effect=AssertionError('unrelated_backup_gate')))
        M.capture_readiness(C,P,R,None,{'schema':'capture2_current_inventory/1'})
        R.hot_finished.assert_called_once()
        P.inventory.assert_called_once()
        P.web_datastores_native.assert_called_once()
        R.no_backup_exec.assert_not_called()

    def test_capture_readiness_still_blocks_owned_hot_operation(self):
        R = SimpleNamespace(hot_finished=unittest.mock.Mock(side_effect=RuntimeError('hot_backup_still_active')))
        P = SimpleNamespace(inventory=unittest.mock.Mock(),web_datastores_native=unittest.mock.Mock())
        with self.assertRaisesRegex(RuntimeError,'hot_backup_still_active'):
            M.capture_readiness(None,P,R,None,{})
        P.inventory.assert_not_called()
        P.web_datastores_native.assert_not_called()

    def test_disjoint_host_mount_order_only_is_normalized_and_all_fields_retained(self):
        host = {'ReadonlyRootfs':True,'Binds':['/one:/state:rw','/two:/capture:rw'],
                'Mounts':[{'Target':'/run/secrets/a','Source':'/key-a','Type':'bind','Unknown':{'kept':1}},
                          {'Target':'/run/secrets/b','Source':'/key-b','Type':'bind'}]}
        permuted = copy.deepcopy(host)
        permuted['Binds'].reverse();permuted['Mounts'].reverse()
        self.assertEqual(M.canonical_host(host),M.canonical_host(permuted))
        permuted['Mounts'][1]['Unknown']['kept'] = 2
        self.assertNotEqual(M.canonical_host(host),M.canonical_host(permuted))
        nested = {'Binds':['/one:/data:rw','/two:/data/nested:rw'],'Mounts':[]}
        self.assertNotEqual(M.canonical_host(nested),M.canonical_host({**nested,'Binds':list(reversed(nested['Binds']))}))
        with self.assertRaisesRegex(RuntimeError,'duplicate_host_mount_destination'):
            M.canonical_host({'Binds':['/one:/data:rw'],'Mounts':[{'Target':'/data'}]})

    def test_network_observations_are_dynamic_and_unknown_configuration_stays_strict(self):
        old = {'net':{'NetworkID':'fixed','IPAMConfig':None,'DriverOpts':None,'Links':None,'Aliases':['a','b'],
                      'Gateway':'','IPPrefixLen':0,'EndpointID':'','GwPriority':0}}
        new = copy.deepcopy(old)
        new['net'].update(Gateway='192.0.2.1',IPPrefixLen=24,EndpointID='new-endpoint',Aliases=['b','a'])
        self.assertEqual(M.static_networks(old),M.static_networks(new))
        new['net']['GwPriority']=1
        self.assertNotEqual(M.static_networks(old),M.static_networks(new))
        new['net']['Aliases']=['a','a']
        with self.assertRaisesRegex(RuntimeError,'network_alias_inventory_invalid'):
            M.static_networks(new)

    def test_stopped_candidate_config_failure_never_starts_it(self):
        bad = rows(NEW,CANDIDATE,running=False)
        bad[NEW]['host']['UnexpectedPrivilege']=True
        commands=[]
        with patch.object(M,'snapshot',side_effect=[rows(),rows(running=False),bad]),patch.object(M,'command',side_effect=lambda *a,**k:commands.append(a[2]) or b''),patch.object(M,'legacy',return_value={}):
            with self.assertRaisesRegex(RuntimeError,'capture_host_configuration_changed'):
                M.stop_and_start(None,settings(),SimpleNamespace(),rows(),CANDIDATE)
        self.assertFalse(any(len(argv)>1 and argv[1]=='start' for argv in commands))

    def test_only_verified_stopped_candidate_can_defer_network_id_allocation(self):
        before = rows()[OLD]
        after = rows(NEW,CANDIDATE,running=False)[NEW]
        ident = 'd'*64
        declared = {'NetworkID':ident,'IPAMConfig':None,'DriverOpts':None,'Links':None,'Aliases':['service','name'],
                    'GwPriority':0,'Gateway':'192.0.2.1','IPPrefixLen':24}
        before['networks']={'net':declared}
        after['networks']={'net':{**declared,'NetworkID':'','Gateway':'','IPPrefixLen':0,'Aliases':['name','service'],
                                'IPAMConfig':{'IPv4Address':'','IPv6Address':''}}}
        M.post_parity(settings(),before,after,CANDIDATE,network_ids={'net':ident})
        after['networks']['net']['IPAMConfig']['Unrecognized']=''
        with self.assertRaisesRegex(RuntimeError,'capture_explicit_ipam_unreviewed'):
            M.post_parity(settings(),before,after,CANDIDATE,network_ids={'net':ident})
        after['networks']['net']['IPAMConfig']={'IPv4Address':'','IPv6Address':''}
        after['running']=True;after['pid']=123
        with self.assertRaisesRegex(RuntimeError,'unallocated_network_after_start'):
            M.post_parity(settings(),before,after,CANDIDATE,network_ids={'net':ident})
        after['networks']['net']['NetworkID']=ident
        M.post_parity(settings(),before,after,CANDIDATE,network_ids={'net':ident})
        after['networks']['net']['NetworkID']='e'*64
        with self.assertRaisesRegex(RuntimeError,'candidate_declared_network_identity_changed'):
            M.post_parity(settings(),before,after,CANDIDATE,network_ids={'net':ident})

    def test_declared_network_identity_comes_from_exact_native_name_resolution(self):
        commands=[]
        def result(V,clock,argv,seconds=30):
            commands.append(argv)
            return json.dumps({'name':argv[-1],'id':'d'*64}).encode()
        with patch.object(M,'command',side_effect=result):
            self.assertEqual(M.network_identities(settings(),None,['second','first']),{'first':'d'*64,'second':'d'*64})
        self.assertEqual([row[-1] for row in commands],['first','second'])
        self.assertTrue(all(row[1:3]==['network','inspect'] for row in commands))

    def test_only_oom_enabled_pointer_defaults_are_semantically_equal(self):
        before={'OomKillDisable':None,'Memory':402653184,'Binds':[],'Mounts':[]}
        created={**before,'OomKillDisable':False}
        self.assertEqual(M.canonical_host(before),M.canonical_host(created))
        self.assertNotEqual(M.canonical_host(before),M.canonical_host({**before,'OomKillDisable':True}))
        self.assertNotEqual(M.canonical_host(before),M.canonical_host({**created,'Memory':1}))

    def test_closed_never_started_baseline_resolves_only_its_deferred_network_ids(self):
        V=settings();V.AFTER_CAPTURE05_ID=OLD
        before=rows(running=False)[OLD];after=rows(NEW,CANDIDATE,running=False)[NEW]
        net={'NetworkID':'','IPAMConfig':{'IPv4Address':'','IPv6Address':''},'DriverOpts':None,'Links':None,'Aliases':['service','name'],'GwPriority':0}
        before['networks']={'net':copy.deepcopy(net)};after['networks']={'net':copy.deepcopy(net)}
        M.post_parity(V,before,after,CANDIDATE,network_ids={'net':'d'*64})
        V.AFTER_CAPTURE05_ID='e'*64
        with self.assertRaisesRegex(RuntimeError,'prior_declared_network_identity_changed'):
            M.post_parity(V,before,after,CANDIDATE,network_ids={'net':'d'*64})

    def test_marker_wait_acquires_after_short_real_owner_unlock_without_mutation(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'marker';path.write_bytes(b'public-owned-marker')
            before=path.stat();owner=os.open(path,os.O_RDWR);waiter=os.open(path,os.O_RDWR)
            fcntl.flock(owner,fcntl.LOCK_EX|fcntl.LOCK_NB)
            thread=threading.Thread(target=lambda:(time.sleep(.12),fcntl.flock(owner,fcntl.LOCK_UN)))
            thread.start()
            try:
                M.acquire_marker(waiter,SimpleNamespace(seconds=lambda maximum:.8))
                self.assertEqual((path.stat().st_dev,path.stat().st_ino),(before.st_dev,before.st_ino))
                self.assertEqual(path.read_bytes(),b'public-owned-marker')
            finally:
                thread.join();os.close(owner);os.close(waiter)

    def test_marker_busy_times_out_statically_without_ownership_change(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'marker';path.write_bytes(b'public-owned-marker')
            owner=os.open(path,os.O_RDWR);waiter=os.open(path,os.O_RDWR)
            fcntl.flock(owner,fcntl.LOCK_EX|fcntl.LOCK_NB)
            try:
                with self.assertRaisesRegex(RuntimeError,'capture_marker_busy'):
                    M.acquire_marker(waiter,SimpleNamespace(seconds=lambda maximum:.12))
                with self.assertRaises(BlockingIOError):
                    fcntl.flock(waiter,fcntl.LOCK_EX|fcntl.LOCK_NB)
                self.assertEqual(path.read_bytes(),b'public-owned-marker')
            finally:
                os.close(owner);os.close(waiter)

if __name__ == '__main__':
    unittest.main()
