"""Synthetic HOT3 ownership, capacity and lifecycle tests; no ambient commands."""
from contextlib import ExitStack, contextmanager
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import importlib.util
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from scripts import backup_market_pipeline_archive as B

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('hot3_fixture', ROOT/'deploy/maintenance/web_market_hot3.py')
M = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(M)


class Hot3Tests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack(); self.addCleanup(self.stack.close)
        for name in ('subprocess.run', 'subprocess.Popen', 'os.system', 'os.killpg', 'socket.socket',
                     'socket.create_connection', 'socket.getaddrinfo', 'urllib.request.urlopen', 'resource.setrlimit'):
            self.stack.enter_context(patch(name, side_effect=AssertionError('ambient_native_network_or_limit_forbidden')))
        self.folder = Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix='.hot3-', dir=ROOT)))
        self.folder.chmod(0o700)
        self.stage, self.work, self.managed = (self.folder/name for name in ('stage','work','managed'))
        for path in (self.stage, self.work, self.managed): path.mkdir(mode=0o700)
        for name, value in (('STAGE',self.stage),('WORK',self.work),('MANAGED',self.managed)):
            self.stack.enter_context(patch.object(M,name,value))
        self.stack.enter_context(patch.object(M,'CURRENT_PHASE','cli_admission'))
        self.stack.enter_context(patch.object(M,'OWNED_STAGE',None))
        self.stack.enter_context(patch.object(M,'TRUSTED_ERRORS',()))
        self.nonce = '1'*16+'2'*16
        self.digest = 'a'*64
        self.deadline = (datetime.now(timezone.utc)+timedelta(minutes=10)).isoformat().replace('+00:00','Z')
        self.intent = {'nonce': self.nonce, 'deadline_utc': self.deadline, 'created_at_utc': '2026-01-01T00:00:00Z',
            'marker_identity': [1,2], 'pg_application_name': 'hot3-dump-'+self.nonce,
            'database': {'container_id':'b'*64, 'image':'sha256:'+'c'*64, 'database':'market_archive', 'user':'market_data'},
            'restores':[M.resource_binding(self.nonce[:16]),M.resource_binding(self.nonce[16:])],
            'scopes': {kind:'security-market-hot3-'+self.nonce+'-'+kind+'.scope' for kind in ('worker','watchdog')},
            'dump_max_bytes':100000, 'max_cipher_bytes':104096}

    def state(self, name, value):
        M.write_json(self.stage/name, value)

    def test_failure_codes_allow_only_exact_trusted_types_and_closed_tokens(self):
        class Trusted(RuntimeError):pass
        class PretendRefusal(M.Refusal):pass
        with patch.object(M,'TRUSTED_ERRORS',(Trusted,)):
            self.assertEqual(M.safe_reason(Trusted('backup_source_schema_changed')),'backup_source_schema_changed')
            self.assertEqual(M.safe_reason(M.Refusal('hot3_marker_busy')),'hot3_marker_busy')
            for error in (Trusted('/private/path value'),Trusted('x'*81),Trusted('safe', 'private'),
                    RuntimeError('private_alphanumeric_secret'),PretendRefusal('private_alphanumeric_secret')):
                self.assertEqual(M.safe_reason(error),'hot3_unexpected_exception')
        for error,reason in ((FileNotFoundError('/private/path'),'hot3_unexpected_os_error'),
                (KeyError('private_value'),'hot3_unexpected_key_error'),(TypeError('private_value'),'hot3_unexpected_type_error')):
            self.assertEqual(M.safe_reason(error),reason)

    def test_preflight_checkpoints_precede_actions_and_failure_log_contains_no_exception_data(self):
        M.OWNED_STAGE=M.identity(self.stage.lstat())[:5]
        M.preflight_step('database_binding',persist=True)
        checkpoint=M.document(self.stage/'preflight-database_binding.json')
        self.assertEqual(checkpoint,{'schema':'security_web_market_hot3_preflight/1','operation_id':M.OP,
            'attempt_id':M.ATTEMPT_ID,'phase':'database_binding','status':'entered'})
        payload=M.failure_result(KeyError('/private/SECRET_ENV_VALUE'))
        self.assertEqual(payload['reason_code'],'hot3_unexpected_key_error')
        self.assertEqual(payload['phase'],'database_binding')
        self.assertEqual(M.document(self.stage/'preflight-failure.json'),payload)
        self.assertNotIn('SECRET',json.dumps(payload));self.assertNotIn('/private',json.dumps(payload))

    def test_diagnostics_never_adopt_or_write_an_unowned_or_replaced_stage(self):
        M.preflight_step('fresh_namespace')
        M.failure_result(M.Refusal('hot3_partial_operation_exists'))
        self.assertEqual(list(self.stage.iterdir()),[])
        M.OWNED_STAGE=M.identity(self.stage.lstat())[:5]
        moved=self.folder/'old-stage';self.stage.rename(moved);self.stage.mkdir(mode=0o700)
        M.failure_result(OSError('/private/value'))
        self.assertEqual(list(self.stage.iterdir()),[]);self.assertEqual(list(moved.iterdir()),[])

    def public_plan_fixture(self):
        # Sanitized actual datastore-probe shape: immutable ID, no name field.
        return {'machine_id':'synthetic-public-machine-id','containers':[
            {'id':'b'*64,'image':'sha256:'+'c'*64,'project':'market-private-pipeline-primary',
             'name':'/market-private-pipeline-primary-market-database-1'}],
            'datastore_probes':[{'id':'d'*64,'kind':'redis'},
                {'id':'b'*64,'kind':'postgres','database':'market_archive','user':'market_data'}]}

    def test_actual_public_probe_schema_binds_exact_container_id_without_name(self):
        plan=self.public_plan_fixture()
        self.assertEqual(M.database_binding(plan),self.intent['database'])
        for probe in ({'name':plan['containers'][0]['name'],'kind':'postgres','database':'market_archive','user':'market_data'},
                {**plan['datastore_probes'][1],'id':'d'*64},
                {**plan['datastore_probes'][1],'name':plan['containers'][0]['name']},
                {**plan['datastore_probes'][1],'user':True}):
            changed={**plan,'datastore_probes':[probe]}
            with self.subTest(probe=probe),self.assertRaisesRegex(M.Refusal,'database_probe_scope'):M.database_binding(changed)
        with self.assertRaisesRegex(M.Refusal,'database_probe_scope'):
            M.database_binding({**plan,'datastore_probes':[plan['datastore_probes'][1]]*2})

    def test_preintent_execute_reaches_transport_with_sanitized_actual_schema(self):
        self.stage.rmdir();self.work.rmdir();plan=self.public_plan_fixture();profile={'public':'fixture'}
        P=SimpleNamespace(safe_parent=Mock(),document=Mock(return_value=profile))
        marker=self.folder/'marker';marker.write_bytes(b'public-marker-fixture');marker.chmod(0o600)
        H=SimpleNamespace(controls=Mock(return_value=(plan,{},{})),MARKER=marker,
            boundaries=Mock(),public_profile=Mock(return_value=profile))
        args=SimpleNamespace(deadline_utc=self.deadline,nonce=self.nonce,transport_sha256='f'*64)
        with patch.object(M,'OPERATION_DEADLINE',self.deadline),patch.object(M,'load_modules',return_value={}),\
             patch.object(M,'roles',return_value=(P,object(),object(),H,object())),\
             patch.object(Path,'read_text',return_value=plan['machine_id']),patch.object(M,'check_marker'),\
             patch.object(M.fcntl,'flock'),patch.object(M,'transport',side_effect=M.Refusal('hot3_transport_scope')):
            with self.assertRaisesRegex(M.Refusal,'transport_scope'):M.execute(args,{'public.py':b'public-only'},b'producer-public')
        self.assertTrue((self.stage/'preflight-database_binding.json').is_file())
        self.assertTrue((self.stage/'preflight-transport_binding.json').is_file())
        self.assertFalse((self.stage/'intent.json').exists());self.assertFalse(self.work.exists())

    def test_attempt03_keeps_operation_transport_and_original_deadline_without_adopting_prior_attempts(self):
        self.assertEqual(M.ATTEMPT_ID,M.OP+'-attempt03')
        self.assertEqual(M.TRANSPORT,Path('/root/.ssh')/M.OP/'transport.json')
        args=SimpleNamespace(deadline_utc='2099-01-01T00:00:00Z')
        with self.assertRaisesRegex(M.Refusal,'operation_deadline_changed'):M.execute(args,{},b'')

    def capacity_fixture(self, maximum):
        return {'max_cipher_bytes':maximum,'free_bytes':maximum+M.RESERVE+M.OFFHOST_CONTROL_ALLOWANCE,
            'reserve_bytes':M.RESERVE,'control_allowance_bytes':M.OFFHOST_CONTROL_ALLOWANCE,
            'device':2064,'root':'/srv/trading-bot/backups','files_created':False,'plaintext_materialized_offhost':False}

    def capacity_preflight(self, capacities):
        self.stage.rmdir();self.work.rmdir();plan=self.public_plan_fixture();profile={'public':'fixture'}
        P=SimpleNamespace(safe_parent=Mock(),document=Mock(return_value=profile))
        marker=self.folder/'marker';marker.write_bytes(b'public-marker-fixture');marker.chmod(0o600)
        H=SimpleNamespace(controls=Mock(return_value=(plan,{},{})),MARKER=marker,
            boundaries=Mock(),public_profile=Mock(return_value=profile))
        args=SimpleNamespace(deadline_utc=self.deadline,nonce=self.nonce,transport_sha256='f'*64)
        remote=Mock(side_effect=[self.capacity_fixture(value) for value in capacities])
        self.stack.enter_context(patch.object(M,'OPERATION_DEADLINE',self.deadline))
        self.stack.enter_context(patch.object(M,'load_modules',return_value={}))
        self.stack.enter_context(patch.object(M,'roles',return_value=(P,object(),object(),H,object())))
        self.stack.enter_context(patch.object(Path,'read_text',return_value=plan['machine_id']))
        self.stack.enter_context(patch.object(M,'check_marker'))
        self.stack.enter_context(patch.object(M.fcntl,'flock'))
        self.stack.enter_context(patch.object(M,'transport',return_value={'public_transport':'fixture'}))
        self.stack.enter_context(patch.object(M,'Runtime',side_effect=lambda intent,digest,modules:
            SimpleNamespace(intent=intent,intent_sha=digest,offhost=remote)))
        return args,remote

    def test_declining_native_capacity_with_actual_public_schema_reaches_watchdog_arm(self):
        initial=4*1024**3
        args,remote=self.capacity_preflight([initial,initial-4096])
        with patch.object(M,'launch_scope',side_effect=M.Refusal('hot3_test_watchdog_boundary')) as launch:
            with self.assertRaisesRegex(M.Refusal,'test_watchdog_boundary'):
                M.execute(args,{'public.py':b'public-only'},b'producer-public')
        self.assertEqual(remote.call_count,2);launch.assert_called_once()
        self.assertEqual(launch.call_args.args[2],'watchdog')
        self.assertTrue(self.work.is_dir());self.assertTrue((self.stage/'preflight-watchdog_arm.json').is_file())
        intent=M.document(self.stage/'intent.json');recheck=M.document(self.stage/'native-recheck.json')
        self.assertEqual(intent['max_cipher_bytes'],initial);self.assertFalse(intent['native_capacity_reserved'])
        self.assertEqual(recheck['native_capacity']['max_cipher_bytes'],initial-4096)

    def test_genuinely_insufficient_native_recheck_refuses_before_work_or_watchdog(self):
        args,remote=self.capacity_preflight([4*1024**3,M.CIPHER_ALLOWANCE])
        with patch.object(M,'launch_scope') as launch,self.assertRaisesRegex(M.Refusal,'native_capacity_insufficient'):
            M.execute(args,{'public.py':b'public-only'},b'producer-public')
        launch.assert_not_called();self.assertFalse(self.work.exists())
        self.assertTrue((self.stage/'intent.json').exists());self.assertEqual(remote.call_count,2)

    def test_native_capacity_arithmetic_preserves_full_reserve_and_control_allowance(self):
        capacity=self.capacity_fixture(10000)
        self.assertEqual(M.native_cipher_capacity(capacity),10000)
        for change in ({'reserve_bytes':M.RESERVE-1},{'control_allowance_bytes':0},
                {'free_bytes':capacity['free_bytes']-1},{'max_cipher_bytes':True},
                {'device':2049},{'root':'/'},{'files_created':True}):
            with self.subTest(change=change),self.assertRaisesRegex(M.Refusal,'native_capacity_invalid'):
                M.native_cipher_capacity({**capacity,**change})

    def fake_watch(self, *, succeeded=True, phase_deadline=None, daemon_complete=False, phase='source-metadata'):
        now = [99.]
        intent = {**self.intent, 'deadline_utc': datetime.fromtimestamp(200,timezone.utc).isoformat().replace('+00:00','Z')}
        rt = SimpleNamespace(intent=intent, intent_sha=self.digest, P=object(), H=object(),
            pg_rows=Mock(return_value=[]), stop_pg=Mock(), restore_resources=Mock(return_value=True), scope=Mock(return_value=True))
        self.state('phase.json', {'intent_sha256':self.digest,'phase':phase,'daemon_deadline_epoch_seconds':phase_deadline,
                                  'daemon_requests_complete':daemon_complete})
        if succeeded is not None:
            self.state('worker-complete.json', {'intent_sha256':self.digest,'status':'PASS' if succeeded else 'FAIL'})
        self.stack.enter_context(patch.object(M,'check_marker'))
        self.stack.enter_context(patch.object(M,'capacity_guard'))
        self.stack.enter_context(patch.object(M.time,'time',side_effect=lambda:now[0]))
        def pause(seconds): now[0] += 1.1
        return rt, now, pause

    def test_success_requires_worker_scope_and_every_owned_resource_absent(self):
        rt, now, pause = self.fake_watch()
        rt.scope.side_effect = [False, True]
        self.assertTrue(M.watchdog(rt, 70, pause=pause))
        result = M.document(self.stage/'cleanup.json')
        self.assertTrue(result['all_owned_resources_absent']); self.assertTrue(result['worker_succeeded'])
        rt.stop_pg.assert_not_called()
        self.assertTrue(all(call.args == ('worker',) and not call.kwargs for call in rt.scope.call_args_list))

    def test_killed_worker_keeps_watchdog_until_absolute_deadline_and_cleans_late_rpc(self):
        rt, now, pause = self.fake_watch(succeeded=False, phase_deadline=100)
        late = {'pid':12,'backend_start':'2026-10-24T01:00:00Z','database':'market_archive',
                'user':'market_data','application':self.intent['pg_application_name']}
        rt.pg_rows.side_effect = [[],[late],[]]
        self.assertFalse(M.watchdog(rt, 70, pause=pause))
        self.assertGreaterEqual(now[0],100)
        self.assertIn(([late],),[call.args for call in rt.stop_pg.call_args_list])
        self.assertTrue(all(call.args==('worker',) for call in rt.scope.call_args_list))
        self.assertFalse(M.document(self.stage/'cleanup.json')['worker_succeeded'])

    def test_ambiguous_create_rpc_never_claims_cleanup_even_after_final_absence(self):
        rt,now,pause=self.fake_watch(succeeded=None,phase_deadline=100,phase='create')
        self.state('supervisor-failed.json',{'intent_sha256':self.digest,'worker_launch_requested':True})
        late={'pid':12,'backend_start':'2026-10-24T01:00:00Z','database':'market_archive',
              'user':'market_data','application':self.intent['pg_application_name']}
        def resources(*,remove=False):
            # A late daemon object can appear after the first negative query.
            return now[0] >= 101
        rt.restore_resources.side_effect=resources
        rt.pg_rows.side_effect=lambda:[late] if 100 <= now[0] < 101 else []
        with self.assertRaisesRegex(M.Refusal,'daemon_creation_outcome_unknown'):
            M.watchdog(rt,70,pause=pause)
        self.assertGreaterEqual(now[0],320)
        self.assertFalse((self.stage/'cleanup.json').exists())
        unresolved=M.document(self.stage/'cleanup-unresolved.json')
        self.assertFalse(unresolved['daemon_request_completion_proven'])
        self.assertTrue(unresolved['manual_recovery_required'])
        self.assertFalse(unresolved['automatic_retry_or_adoption_permitted'])
        self.assertTrue(any(call.kwargs.get('remove') is True for call in rt.restore_resources.call_args_list))

    def test_acknowledged_creation_and_cleanup_allow_bounded_cancellation_after_crypto(self):
        rt,now,pause=self.fake_watch(succeeded=False,phase_deadline=180,phase='encrypt',daemon_complete=True)
        self.assertFalse(M.watchdog(rt,70,pause=pause))
        self.assertLess(now[0],180)
        self.assertTrue(M.document(self.stage/'cleanup.json')['all_owned_resources_absent'])

    def test_sigkill_without_worker_done_is_seen_by_live_watchdog(self):
        rt,now,pause=self.fake_watch(succeeded=None,phase_deadline=100)
        self.state('supervisor-failed.json',{'intent_sha256':self.digest,'worker_launch_requested':True,'daemon_outcome_unknown':True})
        self.state('worker-launch-requested.json',{'intent_sha256':self.digest,'worker_launch_requested':True})
        self.assertFalse(M.watchdog(rt,70,pause=pause))
        self.assertTrue(any(call.kwargs.get('stop') is True for call in rt.scope.call_args_list))
        self.assertGreaterEqual(now[0],100)
        self.assertFalse(M.document(self.stage/'cleanup.json')['worker_succeeded'])

    def test_supervisor_failure_is_durable_before_even_a_failing_stop_rpc(self):
        self.state('worker-launch-requested.json',{'intent_sha256':self.digest,'worker_launch_requested':True})
        def stop(*args,**kwargs):
            self.assertEqual(M.document(self.stage/'supervisor-failed.json')['intent_sha256'],self.digest)
            raise M.Refusal('hot3_command_timeout')
        rt=SimpleNamespace(intent_sha=self.digest,scope=Mock(side_effect=stop))
        M.supervisor_failure(rt,70,SimpleNamespace(poll=lambda:None))
        self.assertFalse(M.document(self.stage/'supervisor-stop-unconfirmed.json')['stop_rpc_confirmed'])

    def test_transient_pg_query_and_stop_timeout_do_not_kill_independent_watchdog(self):
        rt,now,pause=self.fake_watch(succeeded=False,phase_deadline=100)
        rt.pg_rows.side_effect=[M.Refusal('hot3_command_timeout'),[],[]]
        rt.scope.side_effect=[M.Refusal('hot3_command_timeout'),True,True]
        self.assertFalse(M.watchdog(rt,70,pause=pause))
        self.assertEqual(M.document(self.stage/'watchdog-errors.json')['errors'],2)
        self.assertTrue(M.document(self.stage/'cleanup.json')['all_owned_resources_absent'])
        self.assertFalse(M.document(self.stage/'cleanup.json')['worker_succeeded'])

    def test_foreign_identity_is_never_adopted_after_transient_absence(self):
        rt,now,pause=self.fake_watch(succeeded=False,phase_deadline=100)
        rt.restore_resources.side_effect=M.Refusal('hot3_restore_owner')
        def jump(seconds):now[0]+=100
        with self.assertRaisesRegex(M.Refusal,'cleanup_deadline_unproven'):M.watchdog(rt,70,pause=jump)
        self.assertTrue(M.document(self.stage/'watchdog-errors.json')['ownership_refused'])
        self.assertFalse((self.stage/'cleanup.json').exists())

    def test_capacity_breach_stops_only_owned_work_and_never_accepts(self):
        rt, now, pause = self.fake_watch(succeeded=None, phase_deadline=100)
        with patch.object(M,'capacity_guard',side_effect=M.Refusal('hot3_disk_reserve_breached')):
            self.assertFalse(M.watchdog(rt,70,pause=pause))
        self.assertTrue(any(call.kwargs.get('stop') is True for call in rt.scope.call_args_list))
        self.assertTrue(all(call.args==('worker',) for call in rt.scope.call_args_list))
        self.assertFalse(M.document(self.stage/'cleanup.json')['worker_succeeded'])

    def test_unknown_cleanup_or_expanded_deadline_cannot_publish_success(self):
        rt, now, pause = self.fake_watch(succeeded=False, phase_deadline=201)
        def jump(seconds):now[0]+=100
        with self.assertRaisesRegex(M.Refusal,'cleanup_deadline_unproven'):
            M.watchdog(rt,70,pause=jump)
        self.assertFalse((self.stage/'cleanup.json').exists())
        self.assertTrue(M.document(self.stage/'watchdog-errors.json')['ownership_refused'])

    def test_cleanup_deadline_retains_failure_evidence_without_claiming_absence(self):
        rt, now, pause = self.fake_watch(succeeded=False, phase_deadline=100)
        rt.restore_resources.return_value = False
        def long_pause(seconds): now[0] += 100
        with self.assertRaisesRegex(M.Refusal,'cleanup_deadline_unproven'):
            M.watchdog(rt,70,pause=long_pause)
        self.assertFalse((self.stage/'cleanup.json').exists())
        self.assertTrue((self.stage/'worker-complete.json').exists())

    def test_scope_constructor_preserves_marker_fd_and_separates_worker_watchdog(self):
        intent = self.intent
        fd = os.open(self.folder/'marker', os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600); self.addCleanup(os.close,fd)
        with patch.object(M.subprocess,'Popen',return_value=object()) as spawn:
            M.launch_scope(intent,self.digest,'watchdog',fd,b'public-source')
            M.launch_scope(intent,self.digest,'worker',fd,b'public-source')
        for call,kind in zip(spawn.call_args_list,('watchdog','worker')):
            self.assertEqual(call.kwargs['pass_fds'],(fd,))
            argv=call.args[0]
            self.assertIn('--unit='+intent['scopes'][kind],argv)
            self.assertIn('--property=KillMode=control-group',argv)
            self.assertIn('--property=SendSIGKILL=yes',argv)
            self.assertIn(b'public-source'.hex(),argv)
            self.assertIn(sha256(b'public-source').hexdigest(),argv)

    def test_capacity_is_measured_bytes_with_restore_wal_and_reserve(self):
        self.assertEqual(M.capacity_required(100,200,same_filesystem=True),(100+400+M.WAL_TEMP+M.RESERVE,))
        self.assertEqual(M.capacity_required(100,200,same_filesystem=False),(100+M.RESERVE,400+M.WAL_TEMP+M.RESERVE))
        with patch.object(M,'free_bytes',return_value=M.RESERVE-1), self.assertRaisesRegex(M.Refusal,'disk_reserve'):
            M.capacity_guard([self.work])
        for bad in (True,0,-1):
            with self.subTest(bad=bad), self.assertRaises(M.Refusal): M.capacity_required(bad,200,same_filesystem=True)

    def test_dump_budget_uses_both_measured_filesystems_and_helper_limit(self):
        restore=400+M.WAL_TEMP
        self.assertEqual(M.web_dump_budget(1000,200,[M.RESERVE+restore+77],same_filesystem=True),77)
        self.assertEqual(M.web_dump_budget(1000,200,[M.RESERVE+88,M.RESERVE+restore],same_filesystem=False),88)
        self.assertEqual(M.web_dump_budget(100*1024**3,200,[200*1024**3],same_filesystem=True),64*1024**3)
        with self.assertRaisesRegex(M.Refusal,'web_capacity_insufficient'):
            M.web_dump_budget(1000,200,[M.RESERVE+88,M.RESERVE+restore-1],same_filesystem=False)
        with self.assertRaisesRegex(M.Refusal,'web_capacity_insufficient'):
            M.web_dump_budget(1000,200,[M.RESERVE+restore],same_filesystem=True)

    def transport_fixture(self):
        key=self.folder/'transport-key';key.write_bytes(b'synthetic-private-key-never-read');key.chmod(0o600)
        known=self.folder/'known_hosts';known.write_bytes(b'public-pinned-host-record');known.chmod(0o600)
        value={'schema':'security_web_market_hot3_transport/1','operation_id':M.OP,
            'identity_file':'/root/.ssh/security-web-market-hot3-20261024-01/id_ed25519',
            'known_hosts_file':str(known),'known_hosts_sha256':sha256(known.read_bytes()).hexdigest(),
            'expires_at_utc':self.deadline,'identity_metadata':list(M.identity(key.lstat())),
            'source_host':'65.109.220.59','destination_host':'65.109.216.187','port':22,'user':'root'}
        target=Path(value['identity_file']);real_lstat=Path.lstat
        self.stack.enter_context(patch.object(Path,'lstat',lambda path,*a,**k:
            real_lstat(key,*a,**k) if path==target else real_lstat(path,*a,**k)))
        return key,known,value

    def test_new_transport_binds_receipt_metadata_and_expiry_without_private_key_read(self):
        key,known,value=self.transport_fixture()
        P=SimpleNamespace(document=Mock(return_value=value),safe_parent=Mock())
        plan={'offhost':{'known_hosts_file':str(known),'known_hosts_sha256':value['known_hosts_sha256']}}
        deadline=datetime.fromisoformat(self.deadline.replace('Z','+00:00'))
        real_read=Path.read_bytes
        def guarded(path):
            self.assertNotEqual(path,key)
            self.assertNotEqual(path,Path(value['identity_file']))
            return real_read(path)
        with patch.object(Path,'read_bytes',guarded):
            self.assertEqual(M.transport(P,plan,'f'*64,deadline),value)
        P.document.assert_called_once_with(M.TRANSPORT,'f'*64)
        for change,reason in (({'expires_at_utc':'2000-01-01T00:00:00Z'},'expiry'),
             ({'identity_file':'/root/.ssh/security-controller-20261023-host-patch-07/id_ed25519'},'scope'),
             ({'identity_metadata':[True]+value['identity_metadata'][1:]},'key_metadata'),
             ({'port':True},'scope')):
            P.document.return_value={**value,**change}
            with self.subTest(change=change),self.assertRaisesRegex(M.Refusal,reason):M.transport(P,plan,'f'*64,deadline)

    def test_transport_calls_receiver_cli_with_fixed_host_and_cipher_stream_only(self):
        key,known,value=self.transport_fixture();rt=self.runtime()
        rt.intent={**self.intent,'offhost':value,'transport_receipt_sha256':'f'*64}
        rt.P=SimpleNamespace(safe_parent=Mock(),document=Mock(),
            file_digest=Mock(return_value=(known,value['known_hosts_sha256'])))
        rt.clock=SimpleNamespace(remaining=lambda:30)
        reply={'operation_id':M.OP,'nonce':self.nonce,'manifest_sha256':self.digest,'deadline_utc':self.deadline,'status':'received'}
        rt.command=Mock(return_value=json.dumps(reply).encode())
        stream=io.BytesIO(b'synthetic-cipher-only')
        with patch.object(M,'read_public',return_value=b'public-receiver-main'):
            self.assertEqual(rt.offhost('receive',size=21,digest='d'*64,stream=stream),reply)
        call=rt.command.call_args;argv=call.args[0]
        self.assertEqual(call.kwargs['stdin'],stream)
        self.assertIn(value['identity_file'],argv);self.assertIn('root@65.109.216.187',argv)
        import shlex
        remote=shlex.split(argv[-1])
        self.assertEqual(remote[:5],['/usr/bin/python3','-I','-B','-c','public-receiver-main'])
        self.assertEqual(remote[5],'receive');self.assertIn('--cipher-size',remote);self.assertIn('21',remote)
        self.assertEqual(rt.P.document.call_args_list, [unittest.mock.call(M.TRANSPORT,'f'*64)]*2)

    def runtime(self):
        rt = object.__new__(M.Runtime); rt.intent=self.intent; rt.intent_sha=self.digest
        return rt

    def test_foreign_restore_owner_is_never_removed(self):
        rt = self.runtime(); calls=[]
        def command(argv,**kwargs):
            calls.append(argv)
            if argv[1:3]==['container','ls']: return ('d'*64+'\n').encode()
            if argv[1:3]==['container','inspect']:
                return json.dumps({'id':'d'*64,'name':'/'+self.intent['restores'][0]['container'],'owner':'foreign'}).encode()
            raise AssertionError('unadmitted-command')
        rt.command=command
        with self.assertRaisesRegex(M.Refusal,'restore_owner'): rt.restore_resources(remove=True)
        self.assertFalse(any('rm' in argv for argv in calls))

    def test_unknown_daemon_inventory_is_not_absence(self):
        rt=self.runtime();rt.command=Mock(side_effect=M.Refusal('hot3_command_timeout'))
        with self.assertRaisesRegex(M.Refusal,'timeout'):rt.restore_resources()

    def test_backend_termination_uses_exact_pid_start_database_user_and_nonce(self):
        rt=self.runtime();rt.command=Mock(return_value=b't')
        row={'pid':123,'backend_start':'2026-10-24T01:02:03+00:00','database':'market_archive',
             'user':'market_data','application':self.intent['pg_application_name']}
        rt.stop_pg([row])
        sql=rt.command.call_args.args[0][-1]
        for value in ('pid=123',"backend_start='2026-10-24T01:02:03+00:00'",self.intent['pg_application_name'],
                      "datname='market_archive'","usename='market_data'"):self.assertIn(value,sql)
        for changed in ({**row,'pid':'123;DROP'}, {**row,'application':'production-writer'}):
            with self.subTest(changed=changed),self.assertRaisesRegex(M.Refusal,'pg_owner'):rt.stop_pg([changed])
        self.assertEqual(rt.command.call_count,1)

    def test_public_control_aba_and_foreign_replace_refuse(self):
        path=self.stage/'control.json';self.state('control.json',{'intent_sha256':self.digest,'value':1})
        with self.assertRaisesRegex(M.Refusal,'control_exists'):
            M.write_json(path,{'intent_sha256':'f'*64,'value':2},replace=True)
        read=os.pread
        def changed(fd,count,offset):
            raw=read(fd,count,offset);path.write_bytes(raw+b' ');path.write_bytes(raw);return raw
        with patch.object(M.os,'pread',side_effect=changed),self.assertRaisesRegex(M.Refusal,'public_changed'):
            M.document(path)

    def test_native_stream_cap_kills_only_its_named_mock_process(self):
        rd,wr=os.pipe();os.write(wr,b'123456789');os.close(wr)
        child=SimpleNamespace(pid=99999999,stdout=os.fdopen(rd,'rb'),wait=Mock(return_value=0))
        with patch.object(M.subprocess,'Popen',return_value=child),patch.object(M.os,'killpg') as kill:
            with self.assertRaisesRegex(M.Refusal,'output_bound'):M.native(['/unexecuted-fixed-command'],maximum=8)
        kill.assert_called_once_with(99999999,signal.SIGKILL)
        self.assertTrue(child.stdout.closed)

    def test_clock_refuses_expired_extended_and_rollback_does_not_extend_budget(self):
        for value in ('2000-01-01T00:00:00Z',(datetime.now(timezone.utc)+timedelta(hours=3)).isoformat().replace('+00:00','Z')):
            with self.assertRaises(M.Refusal):M.Clock(value)
        clock=M.Clock(self.deadline);clock.started-=1000
        with self.assertRaisesRegex(M.Refusal,'deadline_expired'):clock.check()

    def worker_fixture(self, *, bad_crypto=False, bad_remote=False, dirty_cleanup=False, native_caps=None,
            dump_payload=b'synthetic-verified-dump', cipher_payload=b'authenticated-synthetic-cipher'):
        events=[]
        original=self.folder/'original.env';original.write_bytes(b'private-fixture-unopened');original.chmod(0o600)
        managed=self.managed/'web.release.env';managed.write_bytes(b'private-fixture-unopened');managed.chmod(0o600)
        key=self.folder/'key';key.write_bytes(b'synthetic-key-never-read-by-caller');key.chmod(0o600)
        cfg={'release_sha':'f'*40,'release_tree':'d'*40}
        image={'release_sha':'a'*40,'release_tree':'b'*40,'image_input_signature':'c'*64}
        profile={'configuration_source':cfg,'source_image':image,'containers':['current-public-intent']}
        class Bound:
            def __init__(self,path):self.path=path
            def __enter__(self):return self
            def __exit__(self,*args):return None
            def check(self):events.append('bound-check')
        def create(**arguments):
            events.append('create-restore')
            self.created_limits=arguments['execution_limits']
            self.assertTrue(arguments['allow_running_writers']);self.assertFalse(arguments['allow_target_identity_mismatch'])
            self.assertTrue(arguments['allow_private_primary_source'])
            self.assertEqual(arguments['restore_resource_binding'],self.intent['restores'][0])
            self.assertEqual(arguments['release_sha'],cfg['release_sha'])
            artifact=arguments['backup_dir']/'synthetic.dump';artifact.write_bytes(dump_payload);artifact.chmod(0o600)
            arguments['receipt'].write_bytes(b'public-create-receipt');arguments['receipt'].chmod(0o600)
            journal=arguments['backup_dir']/'journal.json';journal.write_bytes(b'public-journal');journal.chmod(0o600)
            return {'status':'PASS','backup_mode':'hot','restore_smoke':{'cleanup_status':'PASS'},
                    'backup':{'path':str(artifact),'size_bytes':artifact.stat().st_size,'sha256':sha256(artifact.read_bytes()).hexdigest()}}
        def verify(**arguments):
            events.append('independent-restore')
            self.assertEqual(arguments['restore_resource_binding'],self.intent['restores'][1])
            self.assertFalse(arguments['allow_target_identity_mismatch'])
            self.assertTrue(arguments['allow_private_primary_source'])
            return {'fresh_restore_verified':True,'eligible_for_new_deployment':False}
        fakeB=SimpleNamespace(BoundRoleEnv=Bound,BackupExecutionLimits=B.BackupExecutionLimits,
            validate_release_env=Mock(return_value={'MARKET_POSTGRES_USER':'market_data','MARKET_POSTGRES_DB':'market_archive'}),
            inspect_source_database=Mock(return_value={'container_id':self.intent['database']['container_id'],'database_size_bytes':4096}),
            create_backup=create,verify_hot_receipt=verify,file_digest=B.file_digest,
            _journal_path=lambda folder:folder/'journal.json',_pinned_backup_artifact=B._pinned_backup_artifact)
        encrypted={}
        native_caps=iter(native_caps or [self.intent['max_cipher_bytes']]*2)
        def encrypt(**arguments):
            events.append('encrypt')
            self.assertGreater(events.index('independent-restore'),events.index('create-restore'))
            self.assertEqual(events[-2],'pg-absent')
            arguments['destination'].write_bytes(cipher_payload);arguments['destination'].chmod(0o600)
            arguments['receipt'].write_bytes(b'public-encryption-receipt');arguments['receipt'].chmod(0o600)
            encrypted.update(plaintext_sha256='0'*64 if bad_crypto else B.file_digest(arguments['source']),
                plaintext_size_bytes=arguments['source'].stat().st_size,
                ciphertext_sha256=B.file_digest(arguments['destination']),ciphertext_size_bytes=arguments['destination'].stat().st_size)
            return encrypted
        def decrypt_verify(**arguments):
            events.append('decrypt-verify')
            self.assertEqual(arguments['expected_plaintext_sha256'],sha256(dump_payload).hexdigest())
            self.assertEqual(arguments['expected_plaintext_size_bytes'],len(dump_payload))
            return encrypted
        def remote(mode,**arguments):
            events.append('offhost-'+mode)
            if mode=='capacity':return self.capacity_fixture(next(native_caps))
            if mode=='receive':self.assertEqual(arguments['stream'].read(),cipher_payload)
            return {'ciphertext_size_bytes':encrypted['ciphertext_size_bytes'],'ciphertext_sha256':encrypted['ciphertext_sha256'],
                'fsync_completed':False if bad_remote else True,'plaintext_materialized_offhost':False}
        rt=SimpleNamespace(intent=self.intent,intent_sha=self.digest,B=fakeB,
            P=SimpleNamespace(document=Mock(return_value=profile),safe_parent=Mock()),
            R=SimpleNamespace(verify_web_metadata=Mock()),
            H=SimpleNamespace(ENV=original,CONFIGURATION=cfg,PROVENANCE=image,ANCHOR='sha256:'+'e'*64,
                controls=Mock(return_value=({'backup_key_file':str(key),'deadline_utc':'2000-01-01T00:00:00Z'},None,{})),
                public_profile=Mock(return_value=profile),boundaries=Mock()),
            C=SimpleNamespace(encrypt=encrypt,verify=decrypt_verify),
            offhost=remote)
        def absent():events.append('resources-absent');return not dirty_cleanup
        def pg():events.append('pg-absent');return []
        rt.restore_resources=absent;rt.pg_rows=pg
        real_stat=Path.stat
        self.stack.enter_context(patch.object(Path,'stat',lambda path,*a,**k:
            SimpleNamespace(st_dev=real_stat(self.work).st_dev) if path==Path('/var/lib/docker') else real_stat(path,*a,**k)))
        self.stack.enter_context(patch.object(M,'check_marker'))
        self.stack.enter_context(patch.object(M,'free_bytes',return_value=1024**4))
        self.stack.enter_context(patch.object(M.resource,'setrlimit'))
        return rt,events

    def test_real_worker_phase_order_requires_two_clean_restores_then_crypto_then_cipher_only_offhost(self):
        rt,events=self.worker_fixture()
        result=M.worker(rt,70)
        self.assertLess(events.index('create-restore'),events.index('independent-restore'))
        self.assertLess(events.index('independent-restore'),events.index('encrypt'))
        self.assertLess(events.index('decrypt-verify'),events.index('offhost-receive'))
        self.assertLess(events.index('offhost-receive'),events.index('offhost-verify'))
        self.assertFalse(result['security_accepted']);self.assertFalse(result['production_services_changed'])
        self.assertEqual(M.document(self.stage/'phase.json')['phase'],'worker-verified')
        rt.B.validate_release_env.assert_called_once_with(self.managed/'web.release.env',release_sha='f'*40,
            image_id='sha256:'+'e'*64,allow_target_identity_mismatch=False,allow_private_primary_source=True)

    def test_dirty_restore_cleanup_prevents_encryption_or_transfer(self):
        rt,events=self.worker_fixture(dirty_cleanup=True)
        with self.assertRaisesRegex(M.Refusal,'create_cleanup_unproven'):M.worker(rt,70)
        self.assertNotIn('encrypt',events);self.assertNotIn('offhost-receive',events)

    def test_measured_web_shortage_refuses_before_creating_dump_or_restore(self):
        rt,events=self.worker_fixture()
        with patch.object(M,'free_bytes',return_value=M.RESERVE+1),self.assertRaisesRegex(M.Refusal,'web_capacity_insufficient'):
            M.worker(rt,70)
        self.assertNotIn('create-restore',events);self.assertNotIn('independent-restore',events)
        self.assertNotIn('encrypt',events)

    def test_worker_fresh_native_measurement_reduces_effective_dump_budget(self):
        rt,events=self.worker_fixture(native_caps=[self.intent['max_cipher_bytes']-4096,self.intent['max_cipher_bytes']-8192])
        M.worker(rt,70)
        self.assertLess(events.index('offhost-capacity'),events.index('create-restore'))
        self.assertEqual(self.created_limits.dump_max_bytes,self.intent['dump_max_bytes']-4096)
        budget=M.document(self.stage/'dump-budget.json')
        self.assertEqual(budget['effective_dump_max_bytes'],self.created_limits.dump_max_bytes)
        self.assertEqual(budget['native_capacity']['max_cipher_bytes'],self.intent['max_cipher_bytes']-4096)
        self.assertFalse(budget['native_capacity_reserved'])
        self.assertEqual(M.document(self.stage/'result.json')['dump_budget_sha256'],sha256((self.stage/'dump-budget.json').read_bytes()).hexdigest())

    def test_actual_cipher_must_fit_a_later_fresh_native_sample(self):
        rt,events=self.worker_fixture(native_caps=[self.intent['max_cipher_bytes'],M.CIPHER_ALLOWANCE+1],
            dump_payload=b's'*6000,cipher_payload=b'c'*6016)
        with self.assertRaisesRegex(M.Refusal,'native_cipher_does_not_fit'):M.worker(rt,70)
        self.assertIn('decrypt-verify',events);self.assertNotIn('offhost-receive',events)
        self.assertFalse((self.stage/'result.json').exists())

    def test_crypto_reconciliation_or_offhost_durability_failure_cannot_publish_result(self):
        rt,events=self.worker_fixture(bad_crypto=True)
        with self.assertRaisesRegex(M.Refusal,'encryption_reconciliation'):M.worker(rt,70)
        self.assertNotIn('offhost-receive',events);self.assertFalse((self.stage/'result.json').exists())

    def test_durable_remote_verification_is_required_after_receive(self):
        rt,events=self.worker_fixture(bad_remote=True)
        with self.assertRaisesRegex(M.Refusal,'offhost_durability_unproven'):M.worker(rt,70)
        self.assertIn('offhost-verify',events);self.assertFalse((self.stage/'result.json').exists())

    def test_recipe_loads_only_real_pinned_public_buffers_without_host_access(self):
        sources={name:(ROOT/('scripts' if name in {'prepare_market_pipeline_release.py',
            'prepare_market_pipeline_primary_release.py','backup_market_pipeline_archive.py','crypt_market_pipeline_backup.py'}
            else 'deploy/maintenance')/name).read_bytes() for name in M.PINS}
        with patch.dict(M.sys.modules):
            for name in list(M.sys.modules):
                if name=='scripts' or name.startswith('scripts.'):del M.sys.modules[name]
            modules=M.load_modules(sources)
            self.assertEqual(modules['backup_market_pipeline_archive.py'].RECEIPT_SCHEMA,'market_pipeline_backup_restore/1.4')
            self.assertEqual(modules['web_market_hot3_offhost.py'].UUID,'9d19fc8e-f71a-4089-b826-5dddfe83ea28')

    def test_wrapper_source_and_every_helper_are_exact_pinned_bounded_arguments(self):
        import re
        import yaml
        folder=ROOT/'deploy/maintenance'
        play=yaml.safe_load((folder/'web_market_hot3.yml').read_text())[0]
        argv=play['vars']['hot3_argv'];source=(folder/'web_market_hot3.py').read_bytes()
        self.assertEqual(argv[argv.index('--own-sha256')+1],sha256(source).hexdigest())
        self.assertEqual(argv[8],argv[argv.index('--own-source')+1])
        self.assertLess(len(source.hex()),131072)
        for name,digest in M.PINS.items():
            flag='--'+name.removesuffix('.py').replace('_','-')
            expression=argv[argv.index(flag)+1]
            relative=re.fullmatch(r'\{\{ lookup\(\x27ansible.builtin.file\x27, playbook_dir \+ \x27([^\x27]+)\x27, rstrip=false\) \}\}',expression).group(1)
            raw=(folder/relative.lstrip('/')).resolve().read_bytes()
            self.assertEqual(sha256(raw).hexdigest(),digest)
            self.assertLess(len(raw),131072)
        task=next(task for task in play['tasks'] if 'ansible.builtin.command' in task)
        self.assertEqual(task['ansible.builtin.command']['argv'],'{{ hot3_argv }}')
        self.assertEqual(task['timeout'],7350)


if __name__=='__main__':unittest.main()
