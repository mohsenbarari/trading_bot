"""Owned legacy state/FD/argv scenarios; no host inspection or live mutation.

Systemd, Docker and Ansible observations are explicitly inert named fixtures.
Filesystem identities/flocks use real owned temporary controls and the real UID.
"""
from contextlib import ExitStack, redirect_stdout
from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
import fcntl
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT=Path(__file__).resolve().parents[1]
TARGETS=('subprocess.Popen','subprocess.run','subprocess.call','subprocess.check_call','subprocess.check_output',
         'os.system','os.popen','os.kill','os.killpg','socket.socket','socket.create_connection','socket.getaddrinfo',
         'urllib.request.urlopen','ctypes.CDLL','ctypes.PyDLL','signal.signal','signal.alarm','resource.setrlimit')


def fence(stack):
    for name in TARGETS:stack.enter_context(patch(name,side_effect=AssertionError('ambient operation denied')))


with ExitStack() as importing:
    fence(importing)
    spec=importlib.util.spec_from_file_location('_owned_legacy_completion',ROOT/'deploy/maintenance/security_legacy_parking.py')
    M=importlib.util.module_from_spec(spec);spec.loader.exec_module(M)
    # Import the module, never its TestCase class: no duplicated test discovery.
    from tests import test_security_legacy_parking as fixtures


class LegacyCompletionTests(unittest.TestCase):
    def setUp(self):
        self.stack=ExitStack();self.addCleanup(self.stack.close);fence(self.stack)
        self.root=Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix='owned-legacy-completion-')))
        self.root.chmod(0o700);before=os.umask(0o077);self.addCleanup(os.umask,before)
        self.now,self.window,self.profile,self.app=fixtures.context();now=self.now
        class Clock(datetime):
            @classmethod
            def now(cls,tz=None):return now
        self.stack.enter_context(patch.object(M,'datetime',Clock))
        self.stack.enter_context(patch.object(M,'TRUSTED',M.TRUSTED))

    def private(self,path,raw):
        path.write_bytes(raw);path.chmod(0o600);return path

    def primitive(self):return fixtures.primitives()

    def test_public_module_uses_verified_bytes_and_held_descriptor_rejects_replacement(self):
        raw=b'PUBLIC_FIXTURE_VALUE=17\n';path=self.private(self.root/'ordinary_helper.py',raw)
        with ExitStack() as stack:
            module,check=M.load(stack,path,sha256(raw).hexdigest())
            self.assertEqual(module.PUBLIC_FIXTURE_VALUE,17)
            replacement=self.private(self.root/'replacement',raw);os.replace(replacement,path)
            with self.assertRaisesRegex(M.Refusal,'public_changed'):check()
        self.assertEqual(path.read_bytes(),raw)

    def runtime_primitive(self,rows):
        def command(argv):
            if argv[2]=='ls':return '\n'.join(row['id'] for row in rows)
            self.assertEqual(argv[:3],['/usr/bin/docker','container','inspect'])
            return '\n'.join(M.encoded(row).decode().strip() for row in rows)
        return SimpleNamespace(command=command,decode=json.loads)

    def test_public_runtime_keeps_persistent_policy_and_never_requests_env(self):
        rows=deepcopy(self.profile['sites']['bot']['runtime'])
        for row in rows:
            row['ports']=None
            row['mounts']=[{'Destination':'/z','Type':'bind'},{'Destination':'/a','Type':'bind'}]
            row['networks']['ordinary'].update(EndpointID='transient-fixture',IPAddress='ordinary-generated-fixture')
        b=self.runtime_primitive(rows);original=b.command;b.command=Mock(side_effect=original)
        actual=M.runtime(b)
        self.assertEqual(len(actual),15);self.assertEqual([r['id'] for r in actual],sorted(r['id'] for r in rows))
        self.assertEqual(actual[0]['mounts'][0]['Destination'],'/a');self.assertEqual(actual[0]['ports'],{})
        self.assertNotIn('EndpointID',actual[0]['networks']['ordinary'])
        self.assertEqual(actual[0]['networks']['ordinary']['Aliases'],['ordinary'])
        template=b.command.call_args_list[1].args[0][4]
        self.assertNotIn('.Config.Env',template)
        broken=deepcopy(rows);broken[0]['paused']=True
        with self.assertRaisesRegex(M.Refusal,'runtime_state'):M.runtime(self.runtime_primitive(broken))

    def test_web_capture_guards_are_exact_public_files_and_never_start_collectors(self):
        persistent=self.root/'etc-systemd';runtime=self.root/'run-systemd'
        persistent.mkdir();runtime.mkdir();body=b'[Unit]\nConditionPathExists=/run/trading-bot/market-capture-legacy-authority-enabled\nRefuseManualStart=yes\n'
        header=b'# Owned Web boot guards 2026-10-24; exact-content cleanup only.\n'
        names=('coin-capture.service','market-channel-capture.service');files={}
        for name in names:
            p=persistent/(name+'.d')/'90-security-20261024-web-boot.conf';p.parent.mkdir();self.private(p,header+body)
            r=runtime/(name+'.d')/'market-container-authority.conf';r.parent.mkdir();self.private(r,body)
            files[name]=(p,r)
        def paths(value):
            return {'/etc/systemd/system':persistent,'/run/systemd/system':runtime}.get(str(value),Path(value))
        calls=[]
        def command(argv):
            calls.append(argv)
            if argv[1].startswith('list-'):return ''
            name=argv[2];p,r=files[name]
            return 'LoadState=loaded\nActiveState=inactive\nUnitFileState=disabled\nMainPID=0\nJob=0\nNeedDaemonReload=no\nRefuseManualStart=yes\nTriggeredBy=\nDropInPaths='+str(p)+' '+str(r)+'\n'
        b=SimpleNamespace(command=command,read=lambda p,**k:Path(p).read_bytes())
        with patch.object(M,'Path',side_effect=paths):
            actual=M.drivers(b,'a'*32,'web',{})
            self.assertEqual(set(actual),set(names))
            self.assertTrue(all(len(row['verified_public_guards'])==2 for row in actual.values()))
            files[names[0]][0].write_bytes(b'ordinary changed public guard')
            with self.assertRaisesRegex(M.Refusal,'web_legacy_guard_bytes_changed'):M.drivers(b,'a'*32,'web',{})
        self.assertTrue(all(call[1] in {'list-unit-files','list-units','show'} for call in calls))

    def snapshot_fixture(self,site):
        profile=deepcopy(self.profile);b=self.primitive();b.PENDING=self.root/'pending'
        stop=self.private(self.root/'STOP',b'STOP\n');token=self.root/'capture-token'
        mapped={}
        for index,row in enumerate(profile['sites'][site]['store_roots']):
            root=self.root/('store-'+str(index));root.mkdir(mode=0o700);mapped[row['path']]=root
            row['identity']=list(M.ident(root.lstat())[:5])
        def paths(value):return mapped.get(str(value),Path(value))
        b.read=lambda path,**kw:(profile['sites'][site]['machine_id'].encode() if str(path)=='/etc/machine-id' else Path(path).read_bytes())
        b.command=lambda argv:profile['sites'][site]['store_roots'][0]['uuid']
        return profile,b,stop,token,paths

    def test_fresh_bot_and_web_snapshot_preserve_stores_and_refuse_pending_or_legacy_owner(self):
        for site in ('bot','web'):
            sub=self.root/site;sub.mkdir();old=self.root;self.root=sub
            try:
                profile,b,stop,token,paths=self.snapshot_fixture(site)
                with patch.object(M,'Path',side_effect=paths),patch.object(M,'STOP',stop),patch.object(M,'TOKEN',token), \
                     patch.object(M,'runtime',return_value=profile['sites'][site]['runtime']), \
                     patch.object(M,'drivers',return_value=profile['sites'][site]['drivers']):
                    rows,incidents=M.snapshot(site,profile,{},b,'a'*32)
                    self.assertEqual(len(rows),15 if site=='bot' else 27);self.assertEqual(incidents,[])
                    if site=='bot':
                        self.private(b.PENDING,b'ordinary pending operation')
                        with self.assertRaisesRegex(M.Refusal,'parking_pending_release'):M.snapshot(site,profile,{},b,'a'*32)
                    else:
                        self.private(token,b'ordinary legacy owner fixture')
                        with self.assertRaisesRegex(M.Refusal,'legacy_capture_authority_present'):M.snapshot(site,profile,{},b,'a'*32)
            finally:self.root=old

    def test_coordinator_owner_requires_three_real_owned_flocks_and_empty_source_identity(self):
        b=self.primitive();b.MARKER=self.private(self.root/'marker',b'ordinary public marker')
        b.SOURCE=self.private(self.root/'source',b'');b.RESERVATION=self.private(self.root/'reservation',b'ordinary guard')
        profile=deepcopy(self.profile);profile['sites']['bot']['source_mutex_identity']=list(M.ident(b.SOURCE.lstat()))
        owner={'schema':'security_legacy_parking_guard/1','operation_id':M.OP,'nonce':self.window['nonce'],
            'window_sha256':'f'*64,'pid':os.getpid(),'inode':b.RESERVATION.lstat().st_ino}
        b.document=lambda path:deepcopy(owner)
        with ExitStack() as stack:
            rows=[]
            for index,path in enumerate((b.MARKER,b.SOURCE,b.RESERVATION)):
                fd=os.open(path,os.O_RDONLY);stack.callback(os.close,fd);fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
                info=os.fstat(fd);rows.append(f'{index}: FLOCK ADVISORY WRITE {os.getpid()} {os.major(info.st_dev):x}:{os.minor(info.st_dev):x}:{info.st_ino} 0 EOF')
            b.command=lambda argv:'\n'.join(rows)
            M.coordinator_owner(self.window,profile,'f'*64,b)
            rows.pop()
            with self.assertRaisesRegex(M.Refusal,'parking_owner_flocks_missing'):M.coordinator_owner(self.window,profile,'f'*64,b)
        self.assertEqual(b.SOURCE.read_bytes(),b'')

    def test_mutation_restores_stdout_stderr_and_treats_nonzero_as_unknown(self):
        path=self.private(self.root/'owned-lock',b'');fd=os.open(path,os.O_RDONLY)
        try:
            before=[M.ident(os.fstat(n))[:2] for n in (1,2)]
            argv=['/usr/bin/docker','update','--restart=no',M.ORDER[0]]
            b=SimpleNamespace(execute_ansible=Mock(return_value=0))
            M.mutation(b,argv,self.window['deadline_utc'],(fd,))
            self.assertEqual([M.ident(os.fstat(n))[:2] for n in (1,2)],before)
            self.assertEqual(b.execute_ansible.call_args.args[0],argv)
            self.assertEqual(b.execute_ansible.call_args.kwargs['lock_fds'],(fd,))
            b.execute_ansible.return_value=1
            with self.assertRaisesRegex(M.Refusal,'parking_rpc_outcome_unknown'):M.mutation(b,argv,self.window['deadline_utc'],fd)
            self.assertEqual([M.ident(os.fstat(n))[:2] for n in (1,2)],before)
        finally:os.close(fd)

    def restoration(self):
        profile=deepcopy(self.profile);intents={r['id']:deepcopy(r) for data in profile['sites'].values() for r in data['runtime'] if r['id'] in M.TARGETS}
        for data in profile['sites'].values():
            for row in data['runtime']:
                if row['id'] in M.TARGETS:row.update(running=False,restart_policy=deepcopy(M.NO))
        parent={'schema':'security_legacy_parking_result/1','operation_id':M.OP,'action':'park',
            'status':'reversibly_parked','canonical_reservation_removed':True,'legacypark_FINAL':False,
            'security_accepted':False,'original_target_intents':intents}
        return profile,parent

    def test_restore_requires_completed_eleven_owned_intents_and_preserves_static_policy(self):
        profile,parent=self.restoration();intents=M.restore_parent(parent,profile)
        self.assertEqual(set(intents),set(M.TARGETS))
        bad=deepcopy(parent);next(iter(bad['original_target_intents'].values()))['mounts']=[]
        with self.assertRaisesRegex(M.Refusal,'owned_restore_intent_changed'):M.restore_parent(bad,profile)
        bad=deepcopy(parent);bad['canonical_reservation_removed']=1
        with self.assertRaisesRegex(M.Refusal,'completed_owned_parking_required'):M.restore_parent(bad,profile)

    def test_restored_datastores_require_readiness_and_transient_failure_is_bounded(self):
        pg=next(i for i in M.TARGETS if i.startswith('757a'));redis=next(i for i in M.TARGETS if i.startswith('c95c'))
        b=SimpleNamespace(Refusal=M.Refusal,command=Mock(return_value=''))
        self.assertTrue(M.datastore_ready(pg,b,self.window['deadline_utc']))
        b.command=Mock(side_effect=[M.Refusal('ordinary_not_ready'),'PONG'])
        with patch.object(M.time,'monotonic',side_effect=[0,0,1,2]),patch.object(M.time,'sleep') as sleep:
            self.assertTrue(M.datastore_ready(redis,b,self.window['deadline_utc']))
        sleep.assert_called_once_with(.5)
        b.command.assert_called_with(['/usr/bin/docker','exec',redis,'redis-cli','ping'])
        self.assertFalse(M.datastore_ready(M.ORDER[0],b,self.window['deadline_utc']))
        b.command.reset_mock()
        with patch.object(M.time,'monotonic',side_effect=[0,0,46]):
            with self.assertRaisesRegex(M.Refusal,'legacy_restored_datastore_not_ready'):M.datastore_ready(pg,b,self.window['deadline_utc'])
        b.command.assert_not_called()

    def test_inputs_seal_public_files_and_owned_restore_parent_without_live_resources(self):
        for restore in (False,True):
            root=self.root/str(restore);root.mkdir();main=root/'main';scripts=main/'scripts';scripts.mkdir(parents=True)
            helper=self.private(scripts/'validate_docker_daemon_maintenance.py',b'import json\nclass Refusal(RuntimeError):pass\ndef decode(raw):return json.loads(raw)\n')
            window=deepcopy(self.window);profile=deepcopy(self.profile);parent=None
            if restore:
                profile,parent=self.restoration();window['action']='restore'
            helper_sha=sha256(helper.read_bytes()).hexdigest();window['controls']['helper']=helper_sha
            source=ROOT/'deploy/maintenance/security_legacy_parking.py';window['controls']['script']=sha256(source.read_bytes()).hexdigest()
            profile_file=self.private(root/'legacy-profile.json',M.encoded(profile));app_file=self.private(root/'controller-result.json',M.encoded(self.app))
            profile_sha=sha256(profile_file.read_bytes()).hexdigest();app_sha=sha256(app_file.read_bytes()).hexdigest()
            window.update(profile_sha256=profile_sha,app_result_sha256=app_sha)
            parent_file=parent_sha=None
            if restore:
                parent_file=self.private(root/'parent-parking.json',M.encoded(parent));parent_sha=sha256(parent_file.read_bytes()).hexdigest();window['parent_park_sha256']=parent_sha
            window_file=self.private(root/'legacy-window.json',M.encoded(window))
            args=SimpleNamespace(role='coordinate',primitives=helper,window=window_file,window_sha256=sha256(window_file.read_bytes()).hexdigest(),
                profile=profile_file,profile_sha256=profile_sha,app_result=app_file,app_sha256=app_sha,parent=parent_file,parent_sha256=parent_sha)
            with patch.object(M,'MAIN',main),patch.object(M,'HELPER_SHA',helper_sha),patch.object(M.signal,'alarm') as alarm,ExitStack() as stack:
                result=M.inputs(args,stack)
                self.assertEqual(result[0]['action'],'restore' if restore else 'park')
                self.assertEqual(result[1],profile);self.assertEqual(result[2],self.app)
                self.assertEqual(result[-1],parent['original_target_intents'] if restore else None)
                for check in result[-2]:check()
                alarm.assert_called_once_with(900)

    def test_worker_check_and_web_target_preserve_single_capture_authority(self):
        for mode in ('check','target'):
            root=self.root/mode;root.mkdir();b=self.primitive();profile=deepcopy(self.profile)
            b.MARKER=self.private(root/'marker',b'permanent public marker');before=M.ident(b.MARKER.lstat())
            profile['sites']['web']['marker_identity']=list(before);b.SOURCE=root/'absent-source';b.RESERVATION=root/'absent-reservation'
            seal={'schema':'security_legacy_parking_stage/1','nonce':self.window['nonce'],
                'source_sha256':self.window['controls']['script'],'window_sha256':'a'*64,'profile_sha256':'b'*64,'app_sha256':'c'*64}
            self.private(root/'public-stage.json',M.encoded(seal))
            target=M.ORDER[4];projection={i:{'running':False,'restart_policy':deepcopy(M.NO)} for i in M.ORDER[:4]}
            args=SimpleNamespace(site='web',directory=root,projection=M.encoded(projection).decode() if mode=='target' else '{}',
                mode=mode,target=target,confirm=M.CONFIRM,window_sha256='a'*64,profile_sha256='b'*64,app_sha256='c'*64)
            def known(*args,**kw):kw['before_rpc']();return {'status':'known_target_verified'}
            with patch.object(M,'HERE',root),patch.object(M,'workspace',return_value=root), \
                 patch.object(M,'inputs',return_value=(self.window,profile,self.app,b,[],None)), \
                 patch.object(M,'snapshot',return_value=(profile['sites']['web']['runtime'],[])), \
                 patch.object(M,'target_step',side_effect=known) as requested:
                result=M.worker(args)
            self.assertEqual(result['status'],'checked' if mode=='check' else 'known_target_verified')
            self.assertEqual(requested.call_count,0 if mode=='check' else 1)
            self.assertFalse((root/'site-operation.lock').exists());self.assertFalse(b.SOURCE.exists());self.assertFalse(b.RESERVATION.exists())
            self.assertEqual(M.ident(b.MARKER.lstat()),before)

    def coordinator(self,mode,fail_receipt=False):
        base=self.root/('coordinator-'+mode);base.mkdir();main=base/'main';here=main/'deploy/maintenance';here.mkdir(parents=True)
        b=self.primitive();b.MARKER=self.private(base/'marker',b'permanent public marker');b.SOURCE=self.private(base/'source',b'')
        b.RESERVATION=base/'reservation';b.PENDING=base/'pending';stop=self.private(base/'STOP',b'STOP\n')
        before=M.ident(b.SOURCE.lstat());profile=deepcopy(self.profile)
        profile['sites']['bot']['marker_identity']=list(M.ident(b.MARKER.lstat()));profile['sites']['bot']['source_mutex_identity']=list(before)
        window=deepcopy(self.window);pins={}
        for name in ('security_legacy_parking.yml','security_consumers_inventory.yml','security_legacy_parking_ansible.cfg'):
            file=self.private(here/name,b'# explicit inert public control fixture\n');pins[name]=sha256(file.read_bytes()).hexdigest()
        window['controls'].update(playbook=pins['security_legacy_parking.yml'],inventory=pins['security_consumers_inventory.yml'],config=pins['security_legacy_parking_ansible.cfg'])
        root=main/'.local'/(M.OP+'-'+window['nonce']);root.mkdir(parents=True);(root/'results').mkdir()
        if mode=='apply':self.private(root/'controller-stage-result.json',M.encoded({'schema':'security_legacy_parking_result/1','nonce':window['nonce'],
            'status':'public_assets_staged','profile_sha256':'b'*64,'window_sha256':'a'*64,'canonical_reservation_removed':True}))
        actions=[]
        def invoke(args,action,sites,window,projection,b,locks,target=None):
            actions.append((action,target))
            if action=='target':
                value={'schema':'security_legacy_parking_target_result/1','nonce':window['nonce'],'id':target,'action':'park',
                    'status':'known_target_verified','projection':{'running':False,'restart_policy':M.NO},'legacypark_FINAL':False,'security_accepted':False}
                self.private(root/'results'/(sites[0]+'-'+target+'.json'),M.encoded(value))
            return 0
        original=M.write
        def write(b,path,value,*a,**kw):
            if fail_receipt and path.name=='controller-result.json':raise OSError('ordinary final receipt fixture failure')
            return original(b,path,value,*a,**kw)
        args=SimpleNamespace(mode=mode,confirm=M.CONFIRM,window_sha256='a'*64,profile_sha256='b'*64,app_sha256='c'*64)
        with patch.object(M,'MAIN',main),patch.object(M,'HERE',here),patch.object(M,'STOP',stop), \
             patch.object(M,'INVENTORY_SHA',pins['security_consumers_inventory.yml']),patch.object(M,'CONFIG_SHA',pins['security_legacy_parking_ansible.cfg']), \
             patch.object(M,'inputs',return_value=(window,profile,self.app,b,[],None)),patch.object(M,'invoke',side_effect=invoke),patch.object(M,'write',side_effect=write):
            value=M.coordinate(args)
        self.assertEqual(M.ident(b.SOURCE.lstat()),before);self.assertEqual(b.SOURCE.read_bytes(),b'');self.assertFalse(b.RESERVATION.exists())
        return value,actions,root

    def test_coordinator_preflight_and_public_stage_never_invoke_container_targets(self):
        for mode,status in (('check','checked'),('stage','public_assets_staged')):
            value,actions,_=self.coordinator(mode);self.assertEqual(value['status'],status)
            self.assertEqual(actions,[('check' if mode=='check' else 'stage',None)])
            self.assertFalse(value['security_accepted'])

    def test_all_eleven_stop_only_results_precede_known_pending_final_receipt(self):
        value,actions,root=self.coordinator('apply',True)
        self.assertEqual([target for action,target in actions if action=='target'],list(M.ORDER))
        self.assertEqual(value['status'],'known_outcome_receipt_pending');self.assertFalse(value['receipt_written'])
        self.assertFalse(value['legacypark_FINAL']);self.assertFalse(value['data_deletion_authorized'])
        self.assertFalse(value['clean_stopped_backup_restore_coverage_proven']);self.assertFalse((root/'controller-result.json').exists())

    def test_cli_roles_pending_receipt_and_error_shape_do_not_grant_retry(self):
        for role,status in (('coordinate','ordinary_checked_fixture'),('worker','known_outcome_receipt_pending')):
            argv=['security_legacy_parking.py','--role',role,'--window',str(self.root/'legacy-window.json'),'--window-sha256','a'*64,
                '--profile',str(self.root/'legacy-profile.json'),'--profile-sha256','b'*64,'--app-result',str(self.root/'controller-result.json'),
                '--app-sha256','c'*64,'--primitives',str(self.root/'public-helper.py')]
            output=io.StringIO()
            with patch.object(sys,'argv',argv),patch.object(M.resource,'setrlimit'),patch.object(M,role,return_value={'status':status}) as selected,redirect_stdout(output):
                code=M.main()
            self.assertEqual(code,1 if role=='worker' else 0);self.assertFalse(json.loads(output.getvalue())['security_accepted']);selected.assert_called_once()
        output=io.StringIO()
        with patch.object(sys,'argv',argv),patch.object(M.resource,'setrlimit'),patch.object(M,'worker',side_effect=ValueError('EXPLICIT_INERT_PRIVATE_ERROR')),redirect_stdout(output):
            self.assertEqual(M.main(),1)
        value=json.loads(output.getvalue());self.assertEqual(value['code'],'parking_incomplete_or_unknown')
        self.assertFalse(value['rpc_retry_allowed']);self.assertNotIn('EXPLICIT_INERT_PRIVATE_ERROR',output.getvalue())



    def test_worker_failure_receipt_error_preserves_primary_unknown_and_its_owned_fence(self):
        for submitted in (False,True):
            root=self.root/('worker-failure-'+str(submitted));root.mkdir();b=self.primitive();profile=deepcopy(self.profile)
            b.MARKER=self.private(root/'marker',b'permanent public marker');before=M.ident(b.MARKER.lstat())
            profile['sites']['web']['marker_identity']=list(before);b.SOURCE=root/'absent-source';b.RESERVATION=root/'absent-reservation'
            self.private(root/'public-stage.json',M.encoded({'schema':'security_legacy_parking_stage/1',
                'nonce':self.window['nonce'],'source_sha256':self.window['controls']['script'],
                'window_sha256':'a'*64,'profile_sha256':'b'*64,'app_sha256':'c'*64}))
            projection={i:{'running':False,'restart_policy':deepcopy(M.NO)} for i in M.ORDER[:4]}
            args=SimpleNamespace(site='web',directory=root,projection=M.encoded(projection).decode(),mode='target',
                target=M.ORDER[4],confirm=M.CONFIRM,window_sha256='a'*64,profile_sha256='b'*64,app_sha256='c'*64)
            def target(*args,**kw):
                if submitted:kw['before_rpc']()
                raise M.Refusal('owned_fixture_rpc_unknown')
            original=M.write
            def write(b,path,value,*args,**kw):
                if path.name.startswith('failure-'):raise OSError('ordinary failure-publication fixture')
                return original(b,path,value,*args,**kw)
            with patch.object(M,'HERE',root),patch.object(M,'workspace',return_value=root), \
                 patch.object(M,'inputs',return_value=(self.window,profile,self.app,b,[],None)), \
                 patch.object(M,'target_step',side_effect=target) as requested,patch.object(M,'write',side_effect=write):
                with self.assertRaisesRegex(M.Refusal,'owned_fixture_rpc_unknown'):M.worker(args)
            requested.assert_called_once()
            self.assertEqual((root/'site-operation.lock').exists(),submitted)
            self.assertFalse(b.SOURCE.exists());self.assertFalse(b.RESERVATION.exists())
            self.assertEqual(M.ident(b.MARKER.lstat()),before)

if __name__=='__main__':unittest.main()
