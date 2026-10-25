"""Synthetic-only worker safety tests: unmocked host commands always fail."""
from contextlib import ExitStack
from copy import deepcopy
import importlib.util
import ast
from hashlib import sha256
import io
import sys
from contextlib import redirect_stdout
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

SOURCE = Path(__file__).resolve().parents[1] / 'deploy/maintenance/security_consumer_site.py'
SPEC = importlib.util.spec_from_file_location('_security_consumer_site_test', SOURCE)
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)


class OwnedTargetTransitions(unittest.TestCase):
    def setUp(self):
        self.stack=ExitStack();self.addCleanup(self.stack.close)
        for name in ('subprocess.run','subprocess.Popen','os.system','socket.create_connection',
                     'socket.getaddrinfo','urllib.request.urlopen','ctypes.CDLL','resource.setrlimit','signal.signal','signal.alarm'):
            self.stack.enter_context(patch(name,side_effect=AssertionError('ambient command/network denied')))
        self.temp=Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix='.target-v2-',dir=SOURCE.parents[2])))
        self.temp.chmod(0o700);self.stack.enter_context(patch.object(M,'HERE',self.temp))
        spec=importlib.util.spec_from_file_location('_target_contract_fixture',SOURCE.parent/'security_consumer_contract.py')
        self.C=importlib.util.module_from_spec(spec);spec.loader.exec_module(self.C)
        self.c=SimpleNamespace(**{n:getattr(self.C,n) for n in ['ROLE_ORDER','CANDIDATE','OLD_IMAGES','RUNTIME_ENV_DELTA',
            'SUCCESSOR_OPERATION','target_override','target_apply_argv','assert_role_launch','assert_public_diff']},assert_window=Mock())
        self.record={'nonce':'a'*32,'source_commit':'b'*40,'images':{'web':self.C.CANDIDATE,'bot':self.C.CANDIDATE},
            'deadline_utc':'2099-01-01T00:00:00Z','recovery':{'deadline_utc':'2099-01-01T00:30:00Z'}}
        self.original={'targets':{'app':{'id':'1'*64,'restart_policy':{'Name':'always','MaximumRetryCount':0}}}}
        def publish(src,dst):
            try:os.link(src,dst);os.unlink(src)
            except OSError:raise self.b.Refusal('rename_noreplace_failed') from None
        def write(fd,raw):
            while raw:raw=raw[os.write(fd,raw):]
        self.b=SimpleNamespace(private_parent=lambda p:self.assertTrue(Path(p).is_relative_to(self.temp)),
                               write_all=write,publish_noreplace=publish)
        self.phase=self.stack.enter_context(patch.object(M,'successor_phase',return_value={
            'all_containers':[],'upload_boundary':{'file_contents_read':False},'source_receipts':{}}))
        self.command=self.stack.enter_context(patch.object(M,'private_json_command'))
        self.smoke=self.stack.enter_context(patch.object(M,'role_smoke',return_value={'passed':True}))
        self.stack.enter_context(patch.object(M,'datastore_smoke'))
        self.stack.enter_context(patch.object(M,'private_json',return_value=b''))
        def item(site,service,image,contract):
            env=['PATH='+self.C.PUBLIC_PATH,'PYTHON_VERSION=3.11.17','PYTHON_SHA256='+'c'*64,
                 'LANG=C.UTF-8','GPG_KEY='+'A'*40,'SYNTHETIC_BUSINESS=never-emit']
            if image==self.C.CANDIDATE:env.append('PYTHONDONTWRITEBYTECODE=1')
            return {'Id':'2'*64 if image==self.C.CANDIDATE else '3'*64,'Image':image,'State':{'Running':True,'Pid':123},
                'Config':{'Cmd':['sh','-c','exec uvicorn main:app --host 0.0.0.0 --port 8000 --workers 4'],
                    'Entrypoint':None,'WorkingDir':'/app','User':'','Env':env},
                'HostConfig':{'PidMode':'','Privileged':False}}
        self.stack.enter_context(patch.object(M,'owned_target',side_effect=item))

    def apply(self):return M.target_action('web','app',self.record,self.original,{},self.c,self.b,999)

    def test_one_known_target_receipt_has_no_private_values_or_global_acceptance(self):
        result=self.apply();self.assertEqual(result['status'],'verified');self.assertFalse(result['global_security_eligible'])
        argv=self.command.call_args.args[1];self.assertEqual(argv[argv.index('--force-recreate')+1:],['app'])
        override=json.loads((self.temp/'security-app.override.json').read_bytes())
        self.assertEqual(override['services']['app']['environment'],{'PYTHONDONTWRITEBYTECODE':'1'})
        self.assertNotIn('never-emit',repr(result))
        self.assertTrue(all(b'never-emit' not in p.read_bytes() for p in self.temp.iterdir()))

    def test_known_postcheck_failure_rolls_back_same_target_and_removes_only_new_env_delta(self):
        self.smoke.side_effect=[RuntimeError('successor_api_not_ready'),{'passed':True}]
        result=self.apply();self.assertEqual(result['status'],'rolled_back_after_postcheck_failure')
        self.assertEqual(result['first_failure_code'],'successor_api_not_ready')
        self.assertEqual(self.command.call_count,2)
        self.assertTrue(self.command.call_args.kwargs['recovery'])
        self.assertNotIn('environment',json.loads((self.temp/'rollback-app.override.json').read_bytes())['services']['app'])
        self.assertEqual(result['image'],self.C.OLD_IMAGES['web'])

    def test_unknown_client_failure_is_not_cancellation_and_never_triggers_rollback(self):
        self.command.side_effect=RuntimeError('consumer_command_failed')
        result=self.apply();self.assertEqual(result['status'],'write_outcome_unknown');self.assertIsNone(result['id'])
        self.assertEqual(self.command.call_count,1);self.smoke.assert_not_called()
        self.assertFalse((self.temp/'rollback-app.override.json').exists())

    def test_normal_imports_follow_retained_source_pid_and_writer_guards(self):
        order=[];checked=self.phase.return_value
        def phase(*a,**kw):
            order.append('source_pid_writer_guards' if kw.get('verify_sources') else 'preflight')
            return checked
        def smoke(*a,**kw):order.append('normal_imports');return {'passed':True}
        self.phase.side_effect=phase;self.smoke.side_effect=smoke
        self.assertEqual(self.apply()['status'],'verified')
        self.assertEqual(order,['preflight','source_pid_writer_guards','normal_imports'])

    def test_failed_candidate_source_guard_never_runs_candidate_import_probe(self):
        checked=self.phase.return_value
        self.phase.side_effect=[checked,RuntimeError('successor_public_source_drift'),checked,checked]
        result=self.apply()
        self.assertEqual(result['status'],'rolled_back_after_postcheck_failure')
        self.assertEqual(result['first_failure_code'],'successor_public_source_drift')
        self.smoke.assert_called_once();self.assertFalse(self.smoke.call_args.kwargs['candidate'])
        self.assertEqual(self.command.call_count,2)

    def test_nonselected_drift_refuses_before_any_override_or_write(self):
        self.phase.side_effect=RuntimeError('successor_nonselected_runtime_drift')
        with self.assertRaisesRegex(RuntimeError,'successor_nonselected_runtime_drift'):self.apply()
        self.command.assert_not_called();self.assertEqual(list(self.temp.iterdir()),[])

    def test_rollback_unknown_is_retained_as_unknown_not_reported_restored(self):
        self.command.side_effect=[None,RuntimeError('consumer_command_failed')]
        self.smoke.side_effect=RuntimeError('successor_api_not_ready')
        result=self.apply();self.assertEqual(result['status'],'write_outcome_unknown')
        self.assertIsNone(result['id']);self.assertEqual(self.command.call_count,2)

    def test_arbitrary_exception_text_does_not_enter_receipts_or_diagnostics(self):
        self.smoke.side_effect=[RuntimeError('never-emit-private-like-value'),{'passed':True}]
        result=self.apply();self.assertEqual(result['first_failure_code'],'guard_failed')
        self.assertNotIn('never-emit',repr(result))


class SiteControlFiles(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        def denied(*args, **kwargs): raise AssertionError('unexpected command/network')
        for name in ('subprocess.run', 'subprocess.Popen', 'subprocess.call', 'subprocess.check_call',
                     'subprocess.check_output', 'os.system', 'socket.socket.connect', 'socket.getaddrinfo',
                     'socket.create_connection', 'urllib.request.urlopen'):
            self.stack.enter_context(patch(name, side_effect=denied))
        self.temp = Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix='.consumer-io-', dir=SOURCE.parents[2])))
        self.stack.enter_context(patch.object(M, 'HERE', self.temp))
        helper = SOURCE.parent.parent.parent / 'scripts/validate_docker_daemon_maintenance.py'
        spec = importlib.util.spec_from_file_location('_consumer_io_primitives', helper)
        self.b = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.b)
        def publish(src,dst):
            try:os.link(src,dst);os.unlink(src)
            except OSError:raise self.b.Refusal('rename_noreplace_failed') from None
        self.stack.enter_context(patch.object(self.b,'publish_noreplace',side_effect=publish))

    def test_atomic_lock_is_complete_held_and_existing_lock_is_never_adopted(self):
        import fcntl
        fd = M.site_lock(self.b, 'synthetic-nonce', False)
        try:
            lock = self.temp / 'site-operation.lock'
            value = json.loads(lock.read_bytes())
            self.assertEqual(value['nonce'], 'synthetic-nonce')
            self.assertEqual(value['inode'], os.fstat(fd).st_ino)
            with self.assertRaisesRegex(RuntimeError, 'existing_site_operation_not_adopted'):
                M.site_lock(self.b, 'synthetic-nonce', False)
            with self.assertRaisesRegex(RuntimeError,'existing_site_operation_not_adopted'):M.site_lock(self.b,'synthetic-nonce',True)
            with self.assertRaisesRegex(RuntimeError, 'existing_site_operation_not_adopted'):
                M.site_lock(self.b, 'foreign', True)
            M.release_site_lock(fd,'synthetic-nonce')
        finally: os.close(fd)
        self.assertFalse(lock.exists())

    def test_matching_dead_lock_remains_a_blocker_after_fd_close(self):
        fd=M.site_lock(self.b,'a'*32,False);os.close(fd)
        before=(self.temp/'site-operation.lock').read_bytes()
        with self.assertRaisesRegex(RuntimeError,'existing_site_operation_not_adopted'):M.site_lock(self.b,'a'*32,True)
        self.assertEqual((self.temp/'site-operation.lock').read_bytes(),before)

    def test_interrupt_before_full_lock_publication_leaves_no_canonical_blocker(self):
        with patch.object(self.b, 'write_all', side_effect=RuntimeError('synthetic short-write interruption')):
            with self.assertRaises(RuntimeError): M.site_lock(self.b, 'nonce', False)
        self.assertEqual(list(self.temp.iterdir()), [])

    def test_json_publication_never_clobbers_foreign_existing_bytes(self):
        target = self.temp / 'result.json'
        M.write(self.b, target, {'complete': True}, exclusive=True)
        with self.assertRaises(self.b.Refusal): M.write(self.b, target, {'complete': False}, exclusive=True)
        self.assertEqual(json.loads(target.read_bytes()), {'complete': True})

    def test_read_rejects_symlink_hardlink_and_unsafe_mode_without_content_diagnostic(self):
        target = self.temp / 'private.json'
        target.write_bytes(b'never-emit-synthetic-value'); target.chmod(0o600)
        alias = self.temp / 'alias'; alias.symlink_to(target)
        with self.assertRaises(RuntimeError): M.read(alias)
        alias.unlink(); os.link(target, alias)
        with self.assertRaises(RuntimeError): M.read(target)
        alias.unlink(); target.chmod(0o644)
        with self.assertRaises(RuntimeError) as caught: M.read(target)
        self.assertNotIn('never-emit', str(caught.exception))
        self.assertEqual(M.read(target, public=True), b'never-emit-synthetic-value')

    def test_native_worker_requires_same_real_pid_flock_on_all_three_current_inodes(self):
        marker, source, reservation = (self.temp / name for name in ('marker', 'source', 'reservation'))
        for path in (marker, source, reservation): path.touch(mode=0o600)
        record = {'schema':'security_consumer_window/2','nonce': 'a'*32, 'source_commit': 'a' * 40,
                  'source_mutex':list(self.b.identity(source.stat()))}
        owner = {'schema': 'security_consumer_release_guard/2', 'operation_id': M.OPERATION,
                 'nonce': record['nonce'], 'source_commit': record['source_commit'], 'pid': 123,
                 'inode': reservation.lstat().st_ino,'window_sha256':'b'*64,'source_mutex':record['source_mutex']}
        reservation.write_text(json.dumps(owner))
        def row(path, pid=123):
            info = path.lstat()
            return f'1: FLOCK ADVISORY WRITE {pid} {os.major(info.st_dev):02x}:{os.minor(info.st_dev):02x}:{info.st_ino} 0 EOF'
        with patch.object(self.b, 'MARKER', marker), patch.object(self.b, 'SOURCE', source), \
                patch.object(self.b, 'RESERVATION', reservation):
            def command(argv):
                self.assertEqual(argv, ['/usr/bin/cat', '/proc/locks'])
                return '\n'.join(row(path) for path in (marker, source, reservation))
            with patch.object(self.b, 'command', side_effect=command): M.native_coordinator_owner(record, self.b,'b'*64)
            with patch.object(self.b, 'command', return_value='\n'.join(row(path) for path in (marker, reservation))):
                with self.assertRaisesRegex(RuntimeError, 'native_coordinator_fences_not_held'):
                    M.native_coordinator_owner(record, self.b,'b'*64)
            with patch.object(self.b, 'command', return_value='\n'.join(row(path, 124) for path in (marker, source, reservation))):
                with self.assertRaisesRegex(RuntimeError, 'native_coordinator_fences_not_held'):
                    M.native_coordinator_owner(record, self.b,'b'*64)
            source.write_bytes(b'not an operation token')
            with self.assertRaisesRegex(RuntimeError,'native_coordinator_source_missing'):
                M.native_coordinator_owner(record,self.b,'b'*64)


class RuntimeMetadataBoundaries(unittest.TestCase):
    def setUp(self):
        self.stack=ExitStack();self.addCleanup(self.stack.close)
        self.temp=Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix='.consumer-meta-',dir=SOURCE.parents[2])))
        self.temp.chmod(0o700)
        for name in ('subprocess.run','subprocess.Popen','os.system','socket.create_connection','socket.getaddrinfo',
                     'urllib.request.urlopen','ctypes.CDLL','ctypes.PyDLL','resource.setrlimit','signal.signal','signal.alarm'):
            self.stack.enter_context(patch(name,side_effect=AssertionError('ambient native/network denied')))

    def mounts(self):
        self.upload=self.temp/'uploads';self.audit=self.temp/'audit'
        for path in (self.upload,self.audit):path.mkdir(mode=0o700)
        self.secret=self.upload/'private-child-name';self.secret.write_bytes(b'private-like contents must not be read');self.secret.chmod(0o600)
        return [{'name':'/trading_bot_app','mounts':[{'Source':str(path),'Destination':dest,'RW':True}
            for path,dest in [(self.upload,'/app/uploads'),(self.audit,'/app/audit_trail')]]}]

    def test_upload_checks_real_metadata_without_reading_contents_or_returning_names(self):
        rows=self.mounts()
        with patch.object(M.os,'read',side_effect=AssertionError('file contents forbidden')):
            result=M.upload_boundary('web',rows,expected_uid=os.geteuid())
        self.assertFalse(result['file_contents_read']);self.assertEqual(result['regular_files'],1)
        self.assertNotIn('private-child-name',repr(result));self.assertNotIn('private-like contents',repr(result))

    def test_symlink_hardlink_permissions_and_unknown_parent_writer_are_refused(self):
        rows=self.mounts();alias=self.upload/'ordinary-alias'
        alias.symlink_to(self.secret)
        with self.assertRaisesRegex(RuntimeError,'successor_upload_entry'):M.upload_boundary('web',rows,expected_uid=os.geteuid())
        alias.unlink();os.link(self.secret,alias)
        with self.assertRaisesRegex(RuntimeError,'successor_upload_entry'):M.upload_boundary('web',rows,expected_uid=os.geteuid())
        alias.unlink();self.secret.chmod(0o666)
        with self.assertRaisesRegex(RuntimeError,'successor_upload_entry'):M.upload_boundary('web',rows,expected_uid=os.geteuid())
        self.secret.chmod(0o600)
        rows.append({'name':'/unapproved-writer','mounts':[{'Source':str(self.temp),'Destination':'/data','RW':True}]})
        with self.assertRaisesRegex(RuntimeError,'successor_unknown_upload_writer'):M.upload_boundary('web',rows,expected_uid=os.geteuid())

    def test_host_pid_namespace_and_changed_namespace_cannot_pass(self):
        item={'State':{'Pid':321}}
        with patch.object(M.os,'readlink',side_effect=['pid:[2]','pid:[1]','pid:[2]']):M.private_pid_namespace(item)
        for values in [['pid:[1]','pid:[1]'],['pid:[2]','pid:[1]','pid:[3]']]:
            with patch.object(M.os,'readlink',side_effect=values),self.assertRaisesRegex(RuntimeError,'successor_target_pid_namespace'):
                M.private_pid_namespace(item)
        with self.assertRaisesRegex(RuntimeError,'successor_target_pid_missing'):M.private_pid_namespace({'State':{'Pid':True}})

    def test_source_program_real_fds_byte_bounds_and_literal_root_owner_in_production_argv(self):
        public=self.temp/'public';public.mkdir(mode=0o700);(public/'core').mkdir(mode=0o700)
        rows=[];base,extra=divmod(13194027,857)
        for index in range(857):
            length=base+(index<extra);body=(b'# ordinary public source metadata fixture\n'*(length//42+2))[:length]
            name='core/module_'+str(index)+'.py';path=public/name;path.write_bytes(body);path.chmod(0o644)
            rows.append({'path':name,'bytes':length,'sha256':sha256(body).hexdigest(),'git_blob':'a'*40,'mode':'100644'})
        raw=json.dumps(rows,sort_keys=True,separators=(',',':')).encode();original_open=os.open;original_stat=os.stat
        def mapped_open(path,*args,**kwargs):return original_open(public if path=='/app' else path,*args,**kwargs)
        def mapped_stat(path,*args,**kwargs):return original_stat(public if path=='/app' else path,*args,**kwargs)
        input_stream=io.TextIOWrapper(io.BytesIO(raw),encoding='utf-8');output=io.StringIO()
        with patch.object(os,'open',side_effect=mapped_open),patch.object(os,'stat',side_effect=mapped_stat), \
             patch.object(sys,'stdin',input_stream),patch.object(sys,'argv',['source-probe',str(os.geteuid())]), \
             patch('resource.setrlimit'),patch('signal.signal'),patch('signal.alarm'),redirect_stdout(output):
            exec(compile(M.SOURCE_PROBE,'<sealed-public-source-program>','exec'),{})
        result=json.loads(output.getvalue());self.assertEqual(result['files'],857);self.assertEqual(result['bytes'],13194027)
        self.assertFalse(result['production_data_read']);self.assertFalse(result['application_imported'])
        tree=ast.parse(SOURCE.read_bytes())
        calls=[n for n in ast.walk(tree) if isinstance(n,ast.List) and any(isinstance(x,ast.Name) and x.id=='SOURCE_PROBE' for x in n.elts)]
        self.assertEqual(len(calls),1);self.assertEqual(calls[0].elts[-1].value,'0')

    def test_public_writer_rejects_config_and_every_environment_delta_except_the_one_reviewed_flag(self):
        spec=importlib.util.spec_from_file_location('_privacy_writer_helper',SOURCE.parents[2]/'scripts/validate_docker_daemon_maintenance.py')
        helper=importlib.util.module_from_spec(spec);spec.loader.exec_module(helper)
        for data in [{'Config':{'Env':['SYNTHETIC=never-emit']}},{'environment':{'OTHER':'never-emit'}},{'security_accepted':True}]:
            with self.assertRaises(RuntimeError):M.write(helper,self.temp/'forbidden',data,exclusive=True)
        self.assertFalse((self.temp/'forbidden').exists())


if __name__ == '__main__': unittest.main()
