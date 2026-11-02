"""Public inert filesystem fixtures; no host/network/service call is permitted."""
from contextlib import ExitStack, nullcontext, redirect_stderr
from copy import deepcopy
from datetime import datetime,timedelta,timezone
from hashlib import sha256
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

FENCE_TARGETS=('subprocess.Popen','subprocess.run','os.system','os.kill','os.killpg','socket.socket',
    'socket.create_connection','socket.getaddrinfo','urllib.request.urlopen','ctypes.CDLL','signal.signal','signal.setitimer')
def fenced(stack):
    for name in FENCE_TARGETS:
        stack.enter_context(patch(name,side_effect=AssertionError('ambient_host_network_forbidden')))
    return stack
ROOT=Path(__file__).resolve().parents[1]
with ExitStack() as _import_fence:
    fenced(_import_fence)
    spec=importlib.util.spec_from_file_location('dashboard_tls',ROOT/'deploy/maintenance/native_dashboard_tls.py')
    M=importlib.util.module_from_spec(spec);spec.loader.exec_module(M)


class DashboardTests(unittest.TestCase):
    def setUp(self):
        self.stack=ExitStack();self.addCleanup(self.stack.close)
        fenced(self.stack)
        self.root=Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix='public-dashboard-tls-')))
        self.root.chmod(0o700);old=os.umask(0o077);self.addCleanup(os.umask,old)
        self.stack.enter_context(patch.object(M,'parents',lambda path:None))
        self.dropin=self.root/'systemd/dashboard.service.d/security.conf'
        self.dropin.parent.parent.mkdir()
        self.nginx=self.root/'nginx/security.conf';self.nginx.parent.mkdir()
        self.control=self.root/'control';self.control.mkdir()
        self.marker=self.root/'marker';self.marker.write_bytes(b'public inert marker');self.marker.chmod(0o600)
        self.bodies={self.dropin:M.DROPIN_BYTES,self.nginx:M.NGINX_BYTES}
        for key,value in (('DROPIN',self.dropin),('NGINX',self.nginx),('BODIES',self.bodies),('CONTROL',self.control),
            ('MARKER',self.marker),('SOURCE_MUTEX',self.root/'source-mutex'),('RESERVATION',self.root/'reservation')):
            self.stack.enter_context(patch.object(M,key,value))
        self.public(M.SOURCE_MUTEX,b'')
        self.machine='a'*32;self.nonce='b'*32
        self.deadline=(datetime.now(timezone.utc)+timedelta(minutes=10)).strftime('%Y-%m-%dT%H:%M:%SZ')

    def public(self,path,raw,mode=0o600):
        path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw);path.chmod(mode);return path

    def fake_machine(self):
        original=M.read
        return self.stack.enter_context(patch.object(M,'read',side_effect=lambda path,*a,**k:
            self.machine.encode()+b'\n' if path==Path('/etc/machine-id') else original(path,*a,**k)))

    def flow(self,*,fail=None,journal_fail=False):
        self.fake_machine();self.stack.enter_context(patch.object(M,'budget',side_effect=lambda seconds:nullcontext()))
        self.stack.enter_context(patch.object(M,'readiness',side_effect=lambda end,tls=False:
            {'main_pid':202 if tls else 101,'tls_local_head_verified':tls,'response_body_read':False}))
        calls=[];failed=[False]
        def command(argv,**kwargs):
            calls.append(argv)
            if fail==argv and not failed[0]:failed[0]=True;raise M.Refusal('command_failed')
            return b''
        self.stack.enter_context(patch.object(M,'command',side_effect=command))
        if journal_fail:
            old=M.state_write;fired=[False];self.journal_failure_triggered=False
            def state(path,value):
                if len(value['files'])==2 and not fired[0]:
                    fired[0]=True;self.journal_failure_triggered=True;raise OSError('inert control write failure')
                return old(path,value)
            self.stack.enter_context(patch.object(M,'state_write',side_effect=state))
        return calls

    def test_closed_public_config_and_exact_normal_environment_names(self):
        self.assertIn(b'COIN_RATE_ESTIMATOR_HOST=127.0.0.1 COIN_RATE_ESTIMATOR_PORT=30487',M.DROPIN_BYTES)
        self.assertIn(b'/usr/bin/bash /root/trading-bot/trading_bot/apps/coin_rate_estimator/run_live.sh',M.DROPIN_BYTES)
        self.assertNotIn(b'EnvironmentFile=',M.DROPIN_BYTES);self.assertNotIn(b'Environment=',M.DROPIN_BYTES)
        self.assertIn(b'listen 30486 ssl;',M.NGINX_BYTES);self.assertIn(b'proxy_set_header Host $http_host;',M.NGINX_BYTES)
        self.assertIn(b'proxy_cookie_flags coin_session_token secure;',M.NGINX_BYTES)
        self.assertIn(b'error_page 497 =308 https://coin.362514.ir:30486$request_uri;',M.NGINX_BYTES)
        self.assertNotIn(b'allow ',M.NGINX_BYTES);self.assertNotIn(b'auth_basic',M.NGINX_BYTES)

    def test_default_check_has_no_file_or_service_mutations(self):
        self.fake_machine()
        with patch.object(M,'budget',side_effect=lambda x:nullcontext()),patch.object(M,'readiness',return_value={'safe':True}),patch.object(M,'publish') as publish,patch.object(M,'marker_lock') as lock:
            value=M.execute('check','','','')
        self.assertEqual(value['status'],'checked');publish.assert_not_called();lock.assert_not_called()

    def test_invalid_machine_nonce_and_expired_or_expanded_windows_refuse_before_actions(self):
        self.fake_machine()
        with patch.object(M,'marker_lock') as lock:
            for nonce,end,machine in (('bad',self.deadline,self.machine),(self.nonce,'2026-10-24T00:00:00Z',self.machine),
                (self.nonce,(datetime.now(timezone.utc)+timedelta(hours=2)).strftime('%Y-%m-%dT%H:%M:%SZ'),self.machine),
                (self.nonce,self.deadline,'f'*32)):
                with self.assertRaises(M.Refusal):M.execute('apply',nonce,end,machine)
            lock.assert_not_called()

    def test_public_file_stable_fd_rejects_aba_and_hardlinks(self):
        path=self.public(self.root/'source.py',b'ordinary public source',0o755)
        with self.assertRaisesRegex(M.Refusal,'public_file_changed'):
            with M.retained(path,4096):os.replace(self.public(self.root/'replacement',b'ordinary public source',0o755),path)
        os.link(path,self.root/'alias')
        with self.assertRaisesRegex(M.Refusal,'public_file_metadata'):M.read(path,4096)

    def test_exclusive_publication_and_partial_write_cleanup_no_foreign_removal(self):
        path=self.root/'fresh.conf'
        with patch.object(M.os,'fsync',side_effect=OSError('inert fsync failure')):
            with self.assertRaises(OSError):M.publish(path,b'ordinary',0o644)
        self.assertFalse(path.exists())
        self.public(path,b'foreign',0o644)
        with self.assertRaises(FileExistsError):M.publish(path,b'ordinary',0o644)
        self.assertEqual(path.read_bytes(),b'foreign')
        path.unlink();foreign=self.public(self.root/'foreign',b'foreign',0o644)
        def replace_and_fail(fd):os.replace(foreign,path);raise OSError('inert failure')
        with patch.object(M.os,'fsync',side_effect=replace_and_fail):
            with self.assertRaises(OSError):M.publish(path,b'ordinary',0o644)
        self.assertEqual(path.read_bytes(),b'foreign')

    def test_marker_same_inode_is_retained_without_read_or_write(self):
        before=self.marker.read_bytes()
        with M.marker_lock() as recheck:recheck()
        self.assertEqual(self.marker.read_bytes(),before)
        with self.assertRaisesRegex(M.Refusal,'marker_changed'):
            with M.marker_lock():os.replace(self.public(self.root/'replacement-marker',before),self.marker)

    def test_other_controller_fence_and_preexisting_public_files_refuse(self):
        self.public(self.root/'reservation',b'foreign control')
        with self.assertRaisesRegex(M.Refusal,'other_controller_present'):
            with M.marker_lock():pass
        (self.root/'reservation').unlink();self.flow();self.public(self.nginx,b'foreign nginx',0o644)
        with self.assertRaisesRegex(M.Refusal,'fresh_operation_required'):M.execute('apply',self.nonce,self.deadline,self.machine)
        self.assertEqual(self.nginx.read_bytes(),b'foreign nginx')

    def test_persistent_empty_source_mutex_keeps_inode_and_bytes_without_read_or_write(self):
        before=M.identity(M.SOURCE_MUTEX.lstat())
        with patch.object(M.os,'read',side_effect=AssertionError('source payload read')),\
             patch.object(M.os,'write',side_effect=AssertionError('source payload write')):
            with M.marker_lock() as recheck:recheck()
        self.assertEqual(M.identity(M.SOURCE_MUTEX.lstat()),before)
        self.assertEqual(M.SOURCE_MUTEX.read_bytes(),b'')

    def test_busy_nonempty_linked_or_replaced_source_mutex_is_never_adopted(self):
        import fcntl
        fd=os.open(M.SOURCE_MUTEX,os.O_RDWR)
        try:
            fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            with self.assertRaisesRegex(M.Refusal,'source_mutex_busy'):
                with M.marker_lock():pass
        finally:os.close(fd)
        os.link(M.SOURCE_MUTEX,self.root/'source-alias')
        with self.assertRaisesRegex(M.Refusal,'source_mutex_metadata'):
            with M.marker_lock():pass
        (self.root/'source-alias').unlink();M.SOURCE_MUTEX.write_bytes(b'unknown operation')
        with self.assertRaisesRegex(M.Refusal,'source_mutex_metadata'):
            with M.marker_lock():pass
        M.SOURCE_MUTEX.write_bytes(b'')
        with self.assertRaisesRegex(M.Refusal,'source_mutex_changed'):
            with M.marker_lock():os.replace(self.public(self.root/'new-mutex',b''),M.SOURCE_MUTEX)

    def test_exact_public_launch_projection_and_counterfeit_refusal(self):
        old='{ path=/usr/bin/bash ; argv[]=/usr/bin/bash '+str(M.LAUNCHER)+' ; ignore_errors=no ; pid=101 ; }'
        new='{ path=/usr/bin/env ; argv[]=/usr/bin/env COIN_RATE_ESTIMATOR_HOST=127.0.0.1 COIN_RATE_ESTIMATOR_PORT=30487 /usr/bin/bash '+str(M.LAUNCHER)+' ; ignore_errors=no ; pid=202 ; }'
        for tls,raw in ((False,old),(True,new)):
            with patch.object(M,'command',return_value=raw.encode()):M.launch_projection(999999999,tls=tls)
        with patch.object(M,'command',return_value=new.replace('30487','30488').encode()):
            with self.assertRaisesRegex(M.Refusal,'dashboard_launch_contract'):M.launch_projection(999999999,tls=True)

    def test_backend_must_be_loopback_and_exact_current_mainpid(self):
        with (patch.object(M,'source_check'),patch.object(M,'service',return_value={'MainPID':'202','ExecMainStartTimestampMonotonic':'123'}),
             patch.object(M,'launch_projection'),patch.object(M,'listeners',return_value=[{'local':'0.0.0.0:30487','pids':[202]}])):
            with self.assertRaisesRegex(M.Refusal,'dashboard_listener_changed'):M.readiness(999999999,tls=True)

    def test_tls_head_reads_no_body_and_uses_normal_certificate_validation(self):
        response=SimpleNamespace(status=501);connection=Mock();connection.getresponse.return_value=response
        with patch.object(M.ssl,'create_default_context',return_value='normal trust context'),patch.object(M.http.client,'HTTPSConnection',return_value=connection) as constructor:
            M.head(tls=True,end=999999999)
        constructor.assert_called_once();self.assertEqual(constructor.call_args.kwargs['context'],'normal trust context')
        connection.request.assert_called_once_with('HEAD','/');connection.close.assert_called_once()
        response=Mock(status=308);response.getheader.return_value='https://coin.362514.ir:30486/';connection=Mock();connection.getresponse.return_value=response
        with patch.object(M.http.client,'HTTPConnection',return_value=connection):M.head(tls=False,end=999999999)
        response.read.assert_not_called()

    def test_public_certificate_requires_live_hostname_and_current_native_dns(self):
        now=datetime.now(timezone.utc);lo=(now-timedelta(days=1)).strftime('%b %d %H:%M:%S %Y GMT')
        hi=(now+timedelta(days=30)).strftime('%b %d %H:%M:%S %Y GMT')
        raw=('X509v3 Subject Alternative Name:\n DNS:coin.362514.ir\nnotBefore='+lo+'\nnotAfter='+hi+'\n').encode()
        with patch.object(M,'command',return_value=raw) as command,patch.object(M.socket,'getaddrinfo',return_value=[(0,0,0,'',(M.ADDRESS,30486))]):
            value=M.certificate(999999999);self.assertEqual(value['hostname'],M.HOST)
        self.assertEqual(command.call_args.args[0][2],'-in');self.assertEqual(command.call_args.args[0][3],str(M.CERT))
        self.assertNotIn('privkey',str(command.call_args))
        with patch.object(M,'command',return_value=raw),patch.object(M.socket,'getaddrinfo',return_value=[(0,0,0,'',('192.0.2.1',30486))]):
            with self.assertRaisesRegex(M.Refusal,'hostname_address_changed'):M.certificate(999999999)
        with patch.object(M,'command',return_value=raw.replace(b'DNS:coin.362514.ir',b'DNS:unrelated.invalid')):
            with self.assertRaisesRegex(M.Refusal,'certificate_hostname'):M.certificate(999999999)

    def test_exact_service_properties_do_not_query_environment_or_cmdline(self):
        values={'LoadState':'loaded','ActiveState':'active','SubState':'running','MainPID':'101',
            'ExecMainStartTimestampMonotonic':'123','WorkingDirectory':str(M.PROJECT),'User':'root',
            'FragmentPath':'/etc/systemd/system/'+M.UNIT,'NeedDaemonReload':'no','Job':'0'}
        raw=''.join(k+'='+v+'\n' for k,v in values.items()).encode()
        with patch.object(M,'command',return_value=raw) as command:self.assertEqual(M.service(999999999),values)
        self.assertFalse(any('Environment' in arg for arg in command.call_args.args[0]))
        with patch.object(M,'command',return_value=raw.replace(b'Job=0',b'Job=999')):
            with self.assertRaisesRegex(M.Refusal,'dashboard_service_contract'):M.service(999999999)

    def test_absolute_alarm_is_owned_and_restored_without_real_signals(self):
        with patch.object(M.signal,'getitimer',return_value=(0.0,0.0)),patch.object(M.signal,'getsignal',return_value=M.signal.SIG_DFL),patch.object(M.signal,'signal') as handler,patch.object(M.signal,'setitimer') as timer:
            with self.assertRaisesRegex(M.Refusal,'operation_deadline'):
                with M.budget(60):handler.call_args.args[1](None,None)
            self.assertEqual(timer.call_args_list[0].args,(M.signal.ITIMER_REAL,60));self.assertEqual(timer.call_args_list[-1].args,(M.signal.ITIMER_REAL,0))
            self.assertEqual(handler.call_args.args,(M.signal.SIGALRM,M.signal.SIG_DFL))

    def test_apply_publishes_only_two_owned_public_files_and_then_tls(self):
        calls=self.flow();result=M.execute('apply',self.nonce,self.deadline,self.machine)
        self.assertEqual(result['status'],'tls_local_verified_external_peer_pending');self.assertFalse(result['security_accepted'])
        self.assertEqual(self.dropin.read_bytes(),M.DROPIN_BYTES);self.assertEqual(self.nginx.read_bytes(),M.NGINX_BYTES)
        self.assertEqual(stat.S_IMODE(self.dropin.stat().st_mode),0o644);self.assertEqual(stat.S_IMODE(self.dropin.parent.stat().st_mode),0o755)
        self.assertEqual(calls,[['/usr/sbin/nginx','-t'],['/usr/bin/systemctl','daemon-reload'],
            ['/usr/bin/systemctl','restart',M.UNIT],['/usr/bin/systemctl','reload','nginx']])
        record=json.loads(M.state_path(self.nonce).read_bytes());self.assertFalse(record['runtime_env_file_read_or_changed'])
        self.assertFalse(record['private_key_diagnostically_read']);self.assertEqual(set(record['files']),{str(self.dropin),str(self.nginx)})

    def test_normal_failure_rolls_back_only_owned_files_and_original_service(self):
        calls=self.flow(fail=['/usr/bin/systemctl','reload','nginx'])
        with self.assertRaisesRegex(M.Refusal,'command_failed'):M.execute('apply',self.nonce,self.deadline,self.machine)
        self.assertFalse(self.dropin.exists());self.assertFalse(self.nginx.exists())
        record=json.loads(M.state_path(self.nonce).read_bytes());self.assertEqual(record['status'],'rolled_back')
        self.assertFalse(record['security_accepted']);self.assertEqual(calls[-1],['/usr/bin/systemctl','restart',M.UNIT])

    def test_bad_nginx_config_never_restarts_the_existing_dashboard(self):
        calls=self.flow(fail=['/usr/sbin/nginx','-t'])
        with self.assertRaisesRegex(M.Refusal,'command_failed'):M.execute('apply',self.nonce,self.deadline,self.machine)
        self.assertFalse(self.dropin.exists());self.assertFalse(self.nginx.exists())
        self.assertNotIn(['/usr/bin/systemctl','restart',M.UNIT],calls)
        self.assertEqual(json.loads(M.state_path(self.nonce).read_bytes())['status'],'rolled_back')

    def test_owned_restart_waits_only_for_empty_listener_and_stops_when_ready(self):
        for code in ('dashboard_listener_pending','tls_front_pending'):
            with self.subTest(code=code),patch.object(M,'readiness',side_effect=[M.Refusal(code),{'ready':True}]) as ready,\
                 patch.object(M.time,'monotonic',return_value=10),patch.object(M.time,'sleep') as sleep:
                self.assertEqual(M.wait_readiness(100,tls=True),{'ready':True})
                self.assertEqual(ready.call_count,2);sleep.assert_called_once_with(.25)

    def test_readiness_wrong_bind_or_config_is_terminal_and_empty_wait_is_finite(self):
        for code in ('dashboard_listener_changed','dashboard_launch_contract','certificate_expired'):
            with self.subTest(code=code),patch.object(M,'readiness',side_effect=M.Refusal(code)),\
                 patch.object(M.time,'sleep') as sleep:
                with self.assertRaisesRegex(M.Refusal,code):M.wait_readiness(999999999)
                sleep.assert_not_called()
        with patch.object(M,'readiness',side_effect=M.Refusal('dashboard_listener_pending')) as ready,\
             patch.object(M.time,'monotonic',side_effect=[0,46]),patch.object(M.time,'sleep') as sleep:
            with self.assertRaisesRegex(M.Refusal,'dashboard_listener_pending'):M.wait_readiness(100)
            self.assertEqual(ready.call_count,1);sleep.assert_not_called()

    def test_postpublication_journal_failure_still_removes_exact_owned_files(self):
        self.flow(journal_fail=True)
        with self.assertRaises(OSError):M.execute('apply',self.nonce,self.deadline,self.machine)
        self.assertTrue(self.journal_failure_triggered)
        self.assertFalse(self.dropin.exists());self.assertFalse(self.nginx.exists())
        self.assertEqual(json.loads(M.state_path(self.nonce).read_bytes())['status'],'rolled_back')

    def test_changed_public_file_during_postcheck_is_not_accepted_or_deleted(self):
        self.flow()
        def observed(end,*,tls=False):
            if tls:self.public(self.nginx,b'foreign replacement content',0o644)
            return {'main_pid':202 if tls else 101,'tls_local_head_verified':tls}
        with patch.object(M,'readiness',side_effect=observed):
            with self.assertRaisesRegex(M.Refusal,'owned_file_changed'):M.execute('apply',self.nonce,self.deadline,self.machine)
        self.assertEqual(self.nginx.read_bytes(),b'foreign replacement content')
        self.assertEqual(json.loads(M.state_path(self.nonce).read_bytes())['status'],'manual_recovery_required')

    def test_explicit_rollback_never_deletes_a_foreign_replacement(self):
        self.flow();M.execute('apply',self.nonce,self.deadline,self.machine)
        self.dropin.unlink();self.public(self.dropin,b'foreign config',0o644)
        with self.assertRaisesRegex(M.Refusal,'owned_file_changed'):M.execute('rollback',self.nonce,self.deadline,self.machine)
        self.assertEqual(self.dropin.read_bytes(),b'foreign config')

    def test_rollback_rejects_unknown_phase_and_boolean_inode_fields(self):
        self.flow();M.execute('apply',self.nonce,self.deadline,self.machine)
        record=json.loads(M.state_path(self.nonce).read_bytes());row=deepcopy(record['files'][str(self.dropin)])
        row['identity'][5]=True
        with self.assertRaisesRegex(M.Refusal,'owned_file_changed'):M.owned_remove(self.dropin,row)
        self.assertTrue(self.dropin.exists());record['status']='unknown_legacy_state'
        with self.assertRaisesRegex(M.Refusal,'rollback_state'):M.rollback(record,M.state_path(self.nonce),999999999,lambda:None)

    def test_explicit_rollback_removes_exact_own_files_and_preserves_other_dropins(self):
        self.flow();M.execute('apply',self.nonce,self.deadline,self.machine)
        other=self.public(self.dropin.parent/'unrelated.conf',b'normal foreign configuration',0o644)
        result=M.execute('rollback',self.nonce,self.deadline,self.machine)
        self.assertEqual(result['status'],'rolled_back');self.assertEqual(other.read_bytes(),b'normal foreign configuration')
        self.assertFalse(self.dropin.exists());self.assertFalse(self.nginx.exists())

    def test_closed_reason_does_not_expose_unknown_exception_detail(self):
        with patch.object(M,'execute',side_effect=RuntimeError('ordinary-unrecognized-detail')),redirect_stderr(io.StringIO()) as output:
            self.assertEqual(M.main([]),1)
        self.assertEqual(json.loads(output.getvalue())['reason'],'dashboard_tls_refused')
        self.assertNotIn('ordinary-unrecognized-detail',output.getvalue())

    def test_ansible_defaults_and_sealed_buffer_have_no_hidden_apply_or_whitespace_drift(self):
        import ast,yaml
        helper=(ROOT/'deploy/maintenance/native_dashboard_tls.py').read_bytes()
        play=yaml.safe_load((ROOT/'deploy/maintenance/native_dashboard_tls.yml').read_bytes())[0]
        self.assertEqual(play['hosts'],'security_bot');self.assertEqual(play['vars']['dashboard_tls_action'],'check')
        task=next(t for t in play['tasks'] if 'ansible.builtin.command' in t)
        self.assertTrue(task['no_log']);self.assertEqual(task['timeout'],450)
        argv=task['ansible.builtin.command']['argv'];self.assertEqual(argv[:4],['/usr/bin/python3','-I','-B','-c'])
        code=argv[4];self.assertIn(sha256(helper).hexdigest(),code);self.assertIn('rstrip=False',code)
        code=__import__('re').sub(r'\{\{.*?\}\}',repr('ordinary-base64-fixture'),code)
        ast.parse(code)
        self.assertIn('--machine-id',argv);self.assertIn('--deadline-utc',argv)


if __name__=='__main__':unittest.main()
