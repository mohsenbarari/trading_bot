"""Owned /proc/SQLite fixtures only; no native service or network operations."""
from contextlib import ExitStack, nullcontext
from datetime import datetime, timedelta, timezone
import copy
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('native_refresh', ROOT/'deploy/maintenance/brotli_native_refresh.py')
M = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(M)
BOOT_ID = '11111111-2222-4333-8444-555555555555'


class NativeBrotliRefreshTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack(); self.addCleanup(self.stack.close)
        for name in ('subprocess.Popen','subprocess.run','os.system','socket.socket','socket.create_connection','ctypes.CDLL'):
            self.stack.enter_context(patch(name, side_effect=AssertionError('native_network_forbidden')))
        self.folder = Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix='.native-refresh-', dir=ROOT)))

    def test_expired_future_and_overlong_windows_are_refused(self):
        now = datetime.now(timezone.utc)
        fmt = lambda d:d.strftime('%Y-%m-%dT%H:%M:%SZ')
        for a,b in ((now-timedelta(minutes=3),now-timedelta(minutes=1)),
                    (now+timedelta(minutes=1),now+timedelta(minutes=2)),
                    (now-timedelta(seconds=1),now+timedelta(minutes=11))):
            with self.assertRaisesRegex(RuntimeError,'fresh_window_required'): M.Window(fmt(a),fmt(b))
        valid = M.Window(fmt(now-timedelta(seconds=1)),fmt(now+timedelta(minutes=8)))
        self.assertGreater(valid.remaining(), 0)
        valid.monotonic_end = -1
        with self.assertRaisesRegex(RuntimeError,'refresh_window_expired'):valid.remaining()

    def test_real_systemd_timeout_format_is_parsed_without_infinity_fallback(self):
        self.assertEqual(M.duration('1min 30s'),90)
        self.assertEqual(M.duration('30s'),30)
        for value in ('infinity','90','1min extra','-1s'):
            with self.assertRaises(RuntimeError): M.duration(value)

    def test_changed_installer_source_is_rejected_before_execution(self):
        source = self.folder/'helper.py';source.write_text('raise AssertionError("must-not-execute")\n')
        with self.assertRaisesRegex(RuntimeError,'installer_source_changed|installer_source_metadata'):
            M.load_installer(source)

    def test_map_parser_keeps_only_public_code_identity_and_old_deleted_inode(self):
        targets={'python_module':{'path':'/public/module.so','device':'8:1','inode':20}}
        raw=(b'1000-2000 r--p 000000 08:01 10 /public/module.so (deleted)\n'
             b'2000-3000 r-xp 000000 08:01 10 /public/module.so (deleted)\n'
             b'3000-4000 r--p 000000 08:01 99 /private/secret-data\n')
        records=M.map_records(raw,targets)
        self.assertEqual(records,[{'target':'python_module','device':'8:1','inode':10,'segments':2,'old':True}])
        self.assertNotIn('secret',repr(records))

    def test_module_without_any_mapping_or_with_old_sibling_library_is_not_patched(self):
        self.assertFalse(M.module_is_patched({'mappings':[]}))
        self.assertFalse(M.module_is_patched({'mappings':[{'target':'python_module','old':False},{'target':'dec','old':True}]}))
        self.assertTrue(M.module_is_patched({'mappings':[{'target':'python_module','old':False}]}))

    def proc_fixture(self, *, foreign=False):
        proc=self.folder/'proc';proc.mkdir()
        target=self.folder/'new-module';target.write_bytes(b'public-code')
        meta=target.stat();dev=f'{os.major(meta.st_dev):x}:{os.minor(meta.st_dev):x}'
        targets={'python_module':{'path':'/public/module.so','device':dev,'inode':meta.st_ino}}
        for pid in (7,8):
            folder=proc/str(pid)/'root/public';folder.mkdir(parents=True)
            if foreign:
                (folder/'module.so').write_bytes(b'other-image-code')
            else:
                (folder/'module.so').symlink_to(target)
        raw=f'1000-2000 r--p 0 {dev} {meta.st_ino+100} /public/module.so (deleted)\n'.encode()
        self.stack.enter_context(patch.object(M,'PROC',proc))
        self.stack.enter_context(patch.object(M,'start_ticks',return_value=100))
        self.stack.enter_context(patch.object(M,'proc_read',return_value=raw))
        return targets

    def test_unknown_old_native_consumer_is_refused(self):
        targets=self.proc_fixture()
        with self.assertRaisesRegex(RuntimeError,'unknown_old_native_brotli_consumer'):
            M.scan_consumers(targets,{7})

    def test_two_owned_old_consumers_are_recorded_without_private_paths(self):
        targets=self.proc_fixture()
        value=M.scan_consumers(targets,{7,8})
        self.assertEqual(set(value),{7,8})
        self.assertTrue(value[7]['mappings'][0]['old'])
        self.assertNotIn('/public',repr(value))

    def test_same_filename_in_container_root_is_not_a_native_package_consumer(self):
        targets=self.proc_fixture(foreign=True)
        self.assertEqual(M.scan_consumers(targets,set()),{})

    def test_incomplete_proc_reads_and_pid_reuse_refuse(self):
        targets=self.proc_fixture()
        with patch.object(M,'proc_read',side_effect=PermissionError),self.assertRaisesRegex(RuntimeError,'native_consumer_scan_incomplete'):
            M.scan_consumers(targets,{7,8})
        with patch.object(M,'start_ticks',side_effect=[100,101]),self.assertRaisesRegex(RuntimeError,'proc_pid_reused_during_scan'):
            M.scan_consumers(targets,{7,8})

    def test_proc_aggregate_is_early_bounded(self):
        targets=self.proc_fixture()
        with patch.object(M,'MAX_SCAN_BYTES',1),self.assertRaisesRegex(RuntimeError,'proc_scan_total_bound'):
            M.scan_consumers(targets,{7,8})

    def heartbeat(self, stamp=None):
        stamp=stamp or datetime.now(timezone.utc)
        return {'schema_version':1,'sources':{'PUBLIC_MARKET_TELEGRAM':{
            'status':'HEALTHY','heartbeat_at_utc':stamp.isoformat(),'last_success_at_utc':stamp.isoformat(),
            'error_code':None,'details':{'connected':True,'channel_count':3}}}}

    def test_collector_health_requires_fresh_connected_registered_source(self):
        now=datetime.now(timezone.utc);payload=self.heartbeat(now)
        N=SimpleNamespace(read=lambda *a:json.dumps(payload).encode())
        self.assertTrue(M.heartbeat_ready(N,now-timedelta(seconds=1)))
        self.assertFalse(M.heartbeat_ready(N,now+timedelta(seconds=1)))
        payload['sources']['PUBLIC_MARKET_TELEGRAM']['details']['connected']=False
        self.assertFalse(M.heartbeat_ready(N,now-timedelta(seconds=1)))
        payload=self.heartbeat(now-timedelta(minutes=2))
        self.assertFalse(M.heartbeat_ready(N,now-timedelta(minutes=3)))

    def test_old_pid_or_pre_request_ready_journal_cannot_satisfy_golden(self):
        row={'_BOOT_ID':BOOT_ID.replace('-',''),'_PID':'8','_SYSTEMD_UNIT':M.UNITS[1],'__MONOTONIC_TIMESTAMP':'200',
             'MESSAGE':'2026-10-24 12:00:00,001 - telegram.ext.Application - INFO - Application started'}
        calls=[]
        N=SimpleNamespace(boot_id=lambda:BOOT_ID,command=lambda argv,**kw:calls.append(argv) or json.dumps(row))
        self.assertTrue(M.journal_ready(N,M.UNITS[1],8,100))
        self.assertFalse(M.journal_ready(N,M.UNITS[1],9,100))
        self.assertFalse(M.journal_ready(N,M.UNITS[1],8,201))
        self.assertTrue(all(any(arg.startswith('--grep=') for arg in argv) for argv in calls))
        self.assertFalse(any('Environment' in arg for argv in calls for arg in argv))

    def test_collector_ready_requires_actual_live_event_from_new_pid(self):
        row={'_BOOT_ID':BOOT_ID.replace('-',''),'_PID':'7','_SYSTEMD_UNIT':M.UNITS[0],'__MONOTONIC_TIMESTAMP':'200',
             'MESSAGE':json.dumps({'event':'telegram_live_ready','channels':['public-source']})}
        N=SimpleNamespace(boot_id=lambda:BOOT_ID,command=lambda *a,**kw:json.dumps(row))
        self.assertTrue(M.journal_ready(N,M.UNITS[0],7,100))
        row['MESSAGE']=json.dumps({'event':'live_message','channels':['public-source']})
        self.assertFalse(M.journal_ready(N,M.UNITS[0],7,100))

    def test_both_ready_markers_reject_old_boot_pid_reuse_and_missing_boot(self):
        messages=(json.dumps({'event':'telegram_live_ready','channels':['public-source']}),
                  '2026-10-24 12:00:00,001 - telegram.ext.Application - INFO - Application started')
        for unit,message in zip(M.UNITS,messages):
            calls=[]
            row={'_BOOT_ID':'aaaaaaaa222243338444555555555555','_PID':'8','_SYSTEMD_UNIT':unit,
                 '__MONOTONIC_TIMESTAMP':'900000000','MESSAGE':message}
            N=SimpleNamespace(boot_id=lambda:BOOT_ID,command=lambda argv,**kw:calls.append(argv) or json.dumps(row))
            with self.subTest(unit=unit,boot='previous'):
                self.assertFalse(M.journal_ready(N,unit,8,100))
            del row['_BOOT_ID']
            with self.subTest(unit=unit,boot='missing'):
                self.assertFalse(M.journal_ready(N,unit,8,100))
            row['_BOOT_ID']=BOOT_ID.replace('-','')
            with self.subTest(unit=unit,boot='current'):
                self.assertTrue(M.journal_ready(N,unit,8,100))
            self.assertTrue(all('--boot='+BOOT_ID.replace('-','') in argv for argv in calls))

    def test_invalid_current_boot_identity_refuses_before_journal_query(self):
        for value in ('',None,BOOT_ID.replace('-','')):
            N=SimpleNamespace(boot_id=lambda:value,
                              command=lambda *a,**kw:self.fail('invalid boot must not query journal'))
            with self.subTest(boot=value),self.assertRaisesRegex(RuntimeError,'invalid_boot_identity'):
                M.journal_ready(N,M.UNITS[1],8,100)

    def test_missing_golden_database_cannot_be_created(self):
        target=self.folder/'missing.db'
        with patch.object(M,'GOLDEN_DB',target),patch.object(M.sqlite3,'connect',side_effect=AssertionError('must-not-open')):
            with self.assertRaises(FileNotFoundError):M.golden_db_ready()
        self.assertFalse(target.exists())

    def test_real_observer_requires_ten_seconds_of_stable_patched_application_readiness(self):
        now=[0.0]
        before={'MainPID':'7','TimeoutStartUSec':'90s','TimeoutStopUSec':'30s','NRestarts':'0','Restart':'always'}
        after={**before,'MainPID':'107','ActiveState':'active','SubState':'running'}
        before.update(ActiveState='active',SubState='running')
        targets={'python_module':{'path':'/public/module.so','device':'8:1','inode':20}}
        raw=b'1000-2000 r--p 0 08:01 20 /public/module.so\n'
        window=SimpleNamespace(remaining=lambda:500)
        with patch.object(M.time,'monotonic',side_effect=lambda:now[0]), \
             patch.object(M.time,'sleep',side_effect=lambda seconds:now.__setitem__(0,now[0]+seconds)), \
             patch.object(M,'units',return_value={M.UNITS[0]:after}),patch.object(M,'start_ticks',return_value=200), \
             patch.object(M,'proc_read',return_value=raw),patch.object(M,'journal_ready',return_value=True), \
             patch.object(M,'heartbeat_ready',return_value=True):
            value,process=M.observe_replacement(None,M.UNITS[0],before,targets,window,datetime.now(timezone.utc),0)
        self.assertEqual(process['pid'],107);self.assertGreaterEqual(now[0],10)

    def test_stable_process_without_application_readiness_does_not_pass(self):
        now=[0.0]
        before={'MainPID':'7','TimeoutStartUSec':'1s','TimeoutStopUSec':'1s','NRestarts':'0','Restart':'always','ActiveState':'active','SubState':'running'}
        after={**before,'MainPID':'107'}
        targets={'python_module':{'path':'/public/module.so','device':'8:1','inode':20}}
        with patch.object(M.time,'monotonic',side_effect=lambda:now[0]), \
             patch.object(M.time,'sleep',side_effect=lambda seconds:now.__setitem__(0,now[0]+seconds)), \
             patch.object(M,'units',return_value={M.UNITS[0]:after}),patch.object(M,'start_ticks',return_value=200), \
             patch.object(M,'proc_read',return_value=b'1000-2000 r--p 0 08:01 20 /public/module.so\n'), \
             patch.object(M,'journal_ready',return_value=False):
            with self.assertRaisesRegex(RuntimeError,'native_consumer_readiness_not_proven'):
                M.observe_replacement(None,M.UNITS[0],before,targets,SimpleNamespace(remaining=lambda:500),datetime.now(timezone.utc),0)

    def test_actual_owned_sqlite_readonly_quick_check_preserves_database(self):
        path=self.folder/'business-fixture.db'
        connection=sqlite3.connect(path)
        try:connection.execute('create table fixture(value text)');connection.commit()
        finally:connection.close()
        before=path.read_bytes()
        with patch.object(M,'GOLDEN_DB',path):self.assertTrue(M.golden_db_ready())
        self.assertEqual(path.read_bytes(),before)

    def transaction(self):
        install=self.folder/'installed';install.mkdir();receipt=install/'receipt.json';receipt.write_bytes(b'original-package-receipt')
        marker=self.folder/'marker';marker.write_bytes(b'original-marker')
        refresh=self.folder/'refresh'
        units={u:{'MainPID':str(i+7),'ActiveState':'active','SubState':'running','NRestarts':'0',
                   'TimeoutStartUSec':'90s','TimeoutStopUSec':'30s','Restart':'always','KillMode':'control-group'} for i,u in enumerate(M.UNITS)}
        targets={'python_module':{'path':'/public/module.so','device':'8:1','inode':20}}
        old={i+7:{'pid':i+7,'start_ticks':100,'mappings':[{'target':'python_module','device':'8:1','inode':10,'segments':5,'old':True}]} for i in range(2)}
        current=copy.deepcopy(old);writes=[];commands=[]
        identity=lambda s:(s.st_dev,s.st_ino,s.st_size,s.st_mode,s.st_mtime_ns,s.st_ctime_ns)
        def write(path,raw,exclusive):
            if exclusive:self.assertFalse(path.exists())
            path.write_bytes(raw);writes.append(json.loads(raw)['phase'])
        def command(argv,**kw):
            if argv[:2]==['/usr/bin/dpkg','--verify']:return ''
            self.assertEqual(argv[:3],['/usr/bin/systemctl','--no-block','restart'])
            self.assertEqual(json.loads((refresh/'receipt.json').read_bytes())['phase'],'restart_requested')
            commands.append(argv);unit=argv[-1];pid=int(units[unit]['MainPID']);units[unit]['MainPID']=str(pid+100)
            current.pop(pid);current[pid+100]={'pid':pid+100,'start_ticks':200,'mappings':[{'target':'python_module','device':'8:1','inode':20,'segments':5,'old':False}]}
            return ''
        N=SimpleNamespace(ROOT=install,MARKER=marker,NEW='new',PACKAGES=('libbrotli1','python3-brotli'),DEADLINE=None,
            read=lambda p,*a:p.read_bytes(),identity=identity,write=write,native=lambda **kw:None,
            versions=lambda:{'libbrotli1':'new','python3-brotli':'new'},runtime=lambda:['retained-fifteen'],
            acquire_marker=lambda fd:None,legacy_source_guard=lambda:nullcontext(),safe_parent=lambda path:None,
            boot_id=lambda:'same-boot',command=command)
        self.stack.enter_context(patch.object(M,'ROOT',refresh))
        self.stack.enter_context(patch.object(M,'before_state',return_value=(receipt.read_bytes(),identity(receipt.stat()),['retained-fifteen'],copy.deepcopy(units),targets,copy.deepcopy(old))))
        self.stack.enter_context(patch.object(M,'units',side_effect=lambda _:copy.deepcopy(units)))
        self.stack.enter_context(patch.object(M,'current_targets',return_value=targets))
        self.stack.enter_context(patch.object(M,'scan_consumers',side_effect=lambda *a:copy.deepcopy(current)))
        def ticks(pid):
            if pid not in current:raise FileNotFoundError
            return current[pid]['start_ticks']
        self.stack.enter_context(patch.object(M,'start_ticks',side_effect=ticks))
        self.stack.enter_context(patch.object(M,'journal_ready',return_value=True))
        self.stack.enter_context(patch.object(M,'heartbeat_ready',return_value=True))
        self.stack.enter_context(patch.object(M,'golden_db_ready',return_value=True))
        self.stack.enter_context(patch.object(M,'observe_replacement',side_effect=lambda n,u,*args: (copy.deepcopy(units[u]),copy.deepcopy(current[int(units[u]['MainPID'])]))))
        now=datetime.now(timezone.utc)
        window=SimpleNamespace(start=now,end=now+timedelta(minutes=9),monotonic_end=10**12,remaining=lambda:590)
        return N,window,refresh,commands,writes,units

    def test_default_check_has_no_journal_marker_or_service_write(self):
        N,window,folder,commands,writes,_=self.transaction()
        with patch.object(M.os,'open',side_effect=AssertionError('read-only-no-marker')):
            result=M.action(N,'check')
        self.assertFalse(folder.exists());self.assertFalse(commands);self.assertFalse(writes)
        self.assertFalse(result['security_transition_completed'])

    def test_two_ordered_single_restart_requests_are_journalled_before_action(self):
        N,window,folder,commands,writes,_=self.transaction()
        result=M.action(N,'refresh',window)
        self.assertEqual([a[-1] for a in commands],list(M.UNITS))
        self.assertEqual(writes.count('restart_requested'),2)
        self.assertTrue(result['native_brotli_runtime_verified']);self.assertFalse(result['security_transition_completed'])
        self.assertEqual((N.ROOT/'receipt.json').read_bytes(),b'original-package-receipt')
        self.assertEqual(N.MARKER.read_bytes(),b'original-marker')

    def test_first_failure_does_not_restart_next_unit_or_rollback_packages(self):
        N,window,folder,commands,writes,_=self.transaction()
        with patch.object(M,'observe_replacement',side_effect=RuntimeError('not-ready')):
            with self.assertRaisesRegex(RuntimeError,'native_brotli_refresh_incomplete'):M.action(N,'refresh',window)
        self.assertEqual(len(commands),1)
        value=json.loads((folder/'receipt.json').read_bytes())
        self.assertEqual(value['phase'],'partial_refresh_incident_open')
        self.assertFalse(value['security_transition_completed'])

    def test_changed_baseline_after_lock_refuses_before_journal_or_restart(self):
        N,window,folder,commands,writes,_=self.transaction();N.runtime=lambda:['foreign-runtime']
        with self.assertRaisesRegex(RuntimeError,'refresh_baseline_changed_while_waiting'):M.action(N,'refresh',window)
        self.assertFalse(folder.exists());self.assertFalse(commands);self.assertFalse(writes)

    def test_insufficient_window_refuses_before_any_control_write(self):
        N,window,folder,commands,writes,_=self.transaction();window.remaining=lambda:1
        with self.assertRaisesRegex(RuntimeError,'refresh_window_too_short'):M.action(N,'refresh',window)
        self.assertFalse(folder.exists());self.assertFalse(commands);self.assertFalse(writes)


if __name__ == '__main__':unittest.main()
