"""Actual collector + held public Source copies; owned log/command fixtures only.

The existing unchanged formatter test supplies its genuine public formatter AST
with identity redaction/empty metadata seams. No application configuration,
Docker command, actual runtime log, credential, Env or network is accessed.
"""
from contextlib import contextmanager
from copy import deepcopy
import errno
import fcntl
from hashlib import sha256
import json
import os
from pathlib import Path
import tempfile
import time
from types import ModuleType
import unittest
from unittest.mock import patch

ROOT=Path(__file__).absolute().parents[1]
CANARY='PUBLIC_OWNED_COLLECTOR_VALUE_CANARY'
FRAME='api/routers/offers.py'

def public_bytes(path,maximum,pin=None):
    frame=lambda s:(s.st_dev,s.st_ino,s.st_mode,s.st_uid,s.st_gid,s.st_nlink,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC);before=frame(os.fstat(fd));flags=(fcntl.fcntl(fd,fcntl.F_GETFL),fcntl.fcntl(fd,fcntl.F_GETFD));offset=os.lseek(fd,0,os.SEEK_CUR)
    try:
        if path.resolve()!=path or not 0<before[6]<=maximum:raise AssertionError('public Source bound')
        raw=os.pread(fd,before[6]+1,0)
        if len(raw)!=before[6]or(pin is not None and(len(raw),sha256(raw).hexdigest())!=pin):raise AssertionError('public Source pin')
        if frame(os.fstat(fd))!=frame(path.lstat())or frame(path.lstat())!=before or flags!=(fcntl.fcntl(fd,fcntl.F_GETFL),fcntl.fcntl(fd,fcntl.F_GETFD))or offset!=os.lseek(fd,0,os.SEEK_CUR):raise AssertionError('public Source drift')
    finally:os.close(fd)
    try:os.fstat(fd)
    except OSError as exc:
        if exc.errno!=errno.EBADF:raise
    else:raise AssertionError('public Source not closed')
    if frame(path.lstat())!=before:raise AssertionError('public Source postclose drift')
    return raw

def load_public(path,maximum,pin=None):
    raw=public_bytes(path,maximum,pin);module=ModuleType('_owned_collector_public_Source');module.__file__=str(path);exec(compile(raw,str(path),'exec'),module.__dict__);return module

class CollectorFailureDiagnosticsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.F=load_public(ROOT/'tests/test_npm_backend_json_exception_framing.py',32768,(14922,'df8a292db5f9b3fbed6956edebb09650e253f3ddbf77d9af68bb224bb709d2d9'))
        cls.D=cls.F.load_parser()
        mp=ROOT/'deploy/maintenance/glibc_backport_native239/host_io_original.py';cls.M=load_public(mp,65536,cls.D.PINS['deploy/maintenance/glibc_backport_native239/host_io_original.py'])
        cls.Formatter=cls.F.formatter_class()
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory(prefix='owned-npm-collector-',dir='/tmp');self.addCleanup(self.temporary.cleanup)
        self.root=Path(self.temporary.name)/'owned-project';self.root.mkdir(mode=0o700)
        for name,pin in self.D.SYNTHETIC_SOURCE_PINS.items():
            path=self.root/name;path.parent.mkdir(mode=0o700,parents=True,exist_ok=True);path.write_bytes(public_bytes(ROOT/name,131072,pin));path.chmod(0o600)
        self.before=self.root/'before.json';self.cid='a'*64;self.calls=[];self.inspect_count=0
        self.bound={'head':'b'*40,'run_id':1,'run_attempt':1,'browser':'firefox','shard':1,'phase':'after','original_gate_outcome':'failure'}
        self.observed={'Name':'/trading_bot_app','Id':self.cid,'Running':True,'Pid':1,'ExitCode':0,'OOMKilled':False,'StartedAt':'2026-11-01T15:41:55.533947534Z','FinishedAt':'0001-01-01T00:00:00Z','RestartCount':0,'Memory':0,'NanoCpus':0,'Health':'none'}
        self.original={'schema':'npm_browser_public_backend_diagnostics/1','subject':{**self.bound,'phase':'before','original_gate_outcome':'pending'},'states':{'trading_bot_app':self.observed}}
        self.write_before(self.original)
        project=self.root.name
        self.scope={'Name':'/trading_bot_app','Id':self.cid,'Image':'trading_bot_base','Service':'app','Project':project,'WorkingDir':str(self.root),'ConfigFiles':str(self.root/'docker-compose.yml'),'RestartCount':0,'OOMKilled':False,'Mounts':[
          {'Type':'bind','Source':'/tmp/trading-ci-certs','Destination':'/app/certs','Name':'','RW':True},
          {'Type':'bind','Source':'/tmp/trading-ci-models/coin','Destination':'/app/runtime/coin-inference','Name':'','RW':False},
          {'Type':'bind','Source':'/tmp/trading-ci-models/product','Destination':'/app/runtime/product-estimator','Name':'','RW':False},
          {'Type':'volume','Source':f'/var/lib/docker/volumes/{project}_uploads_data/_data','Destination':'/app/uploads','Name':f'{project}_uploads_data','RW':True},
          {'Type':'volume','Source':f'/var/lib/docker/volumes/{project}_audit_data/_data','Destination':'/app/audit_trail','Name':f'{project}_audit_data','RW':True}]}
        self.env={'PATH':'/usr/bin:/bin','HOME':'/owned-empty-client','DOCKER_CONFIG':'/owned-empty-client','DOCKER_HOST':'unix:///var/run/docker.sock','LANG':'C.UTF-8','LC_ALL':'C.UTF-8'}
        self.raw=b'';self.opened=[]
        self.root_patch=patch.object(self.D,'ROOT',self.root);self.root_patch.start();self.addCleanup(self.root_patch.stop)
    def write_before(self,value):self.before.write_text(json.dumps(value));self.before.chmod(0o600)
    def formatted(self):
        info=self.F.owned_info('raise ValueError(CANARY)',{'CANARY':CANARY})
        record=self.F.logging.LogRecord('owned.public',self.F.logging.ERROR,__file__,1,CANARY,(),info)
        record.headers={'Authorization':'Bearer '+CANARY};record.sql_parameters={'token':CANARY}
        return self.Formatter().format(record).encode()+b'\n'
    def command(self,argv,**kw):
        self.assertEqual(argv[:2],['/usr/bin/docker','--host=unix:///var/run/docker.sock']);self.assertEqual(argv[-1],self.cid);self.assertEqual(kw['env'],self.env)
        self.assertTrue(0<kw['deadline']-time.monotonic()<=20);kw['guard']();self.calls.append(argv)
        if argv[2:5]==['container','inspect','--format']:
            self.inspect_count+=1;self.assertEqual(argv[5],self.D.APP_SCOPE);self.assertEqual(kw['maximum'],8192);return json.dumps(self.scope).encode()
        self.assertEqual(argv[2:],['container','logs','--tail','2000','--timestamps',self.cid]);self.assertEqual(kw['maximum'],2*1024*1024);return self.raw
    def collect(self,command=None,hosted=True):
        with patch.object(self.D,'hosted',return_value=hosted):result=self.D.collect_app_exception(command or self.command,json.loads,self.env,self.bound,self.before)
        encoded=json.dumps(result);self.assertNotIn(CANARY,encoded);self.assertNotIn('Authorization',encoded);self.assertFalse(result['raw_log_retained']);self.assertFalse(result['security_accepted']);self.assertIsNone(result['health_assessment'])
        self.assertIn(result['failed_phase'],(None,*self.D.EXCEPTION_FAILURE_PHASES));self.assertIn(result['failure_code'],(None,'UNKNOWN',*self.D.EXCEPTION_FAILURE_CODES));return result
    def assert_failure(self,result,phase,code,status='UNAVAILABLE'):
        self.assertEqual(result['status'],status);self.assertEqual(result['failed_phase'],phase);self.assertEqual(result['failure_code'],code)
    def test_real_formatter_after_1999_owned_JSON_lines_survives_larger_cap(self):
        prefix=(json.dumps({'message':CANARY*12}).encode()+b'\n')*1999;self.raw=prefix+self.formatted()
        self.assertGreater(len(self.raw),256*1024);self.assertLess(len(self.raw),2*1024*1024);self.assertEqual(len(self.raw.splitlines()),2000)
        original_open=self.D.os.open
        def opened(*args,**kw):fd=original_open(*args,**kw);self.opened.append(fd);return fd
        with patch.object(self.D.os,'open',side_effect=opened):result=self.collect()
        self.assertEqual(result['status'],'OBSERVED_STRUCTURAL_TAIL');self.assertIsNone(result['failed_phase']);self.assertIsNone(result['failure_code']);self.assertTrue(result['original_scope_readers_closed'])
        self.assertEqual(len(self.opened),7)
        for fd in self.opened:
            with self.assertRaises(OSError)as raised:os.fstat(fd)
            self.assertEqual(raised.exception.errno,errno.EBADF)
        self.assertEqual(result['client']['observed_output_bytes'],len(self.raw));self.assertEqual(result['structure']['structured_exception_records_seen'],1)
        self.assertTrue(any(row['exception_class']=='ValueError'and{'file':FRAME,'line':1}in row['application_frames']for row in result['structure']['traces']));self.assertEqual(len(self.calls),3)
        self.assertFalse(result['complete_application_log']);self.assertEqual(result['coverage'],'last_2000_App_lines_only')
    def test_actual_owned_python_pipe_exceeds_2MiB_before_accumulation(self):
        def command(argv,**kw):
            if argv[2:4]!=['container','logs']:return self.command(argv,**kw)
            self.calls.append(argv)
            return self.M.run(['/usr/bin/python3','-I','-B','-c','import os;os.write(1,b"X"*2097153)'],env={'PATH':'/usr/bin:/bin','LANG':'C.UTF-8','LC_ALL':'C.UTF-8'},deadline=kw['deadline'],maximum=kw['maximum'],guard=kw['guard'])
        result=self.collect(command);self.assert_failure(result,'log_read','command_output_bound','PARTIAL_OR_UNAVAILABLE')
        client=result['client'];self.assertGreater(client['observed_output_bytes'],2*1024*1024);self.assertEqual(client['maximum_output_bytes'],2*1024*1024);self.assertTrue(client['output_bound_exceeded']);self.assertTrue(client['actual_pipe_closed']);self.assertFalse(client['stdout_eof_observed']);self.assertIs(type(client['actual_returncode']),int);self.assertTrue(result['original_scope_readers_closed'])
    def test_scope_failure_is_typed_and_no_command_runs(self):
        result=self.collect(hosted=False);self.assert_failure(result,'scope','exception_gate_scope');self.assertFalse(self.calls);self.assertFalse(result['original_scope_readers_closed'])
    def test_real_source_pin_failure_is_typed_and_no_command_runs(self):
        path=self.root/next(iter(self.D.SYNTHETIC_SOURCE_PINS));path.write_bytes(path.read_bytes()+b'\n')
        result=self.collect();self.assert_failure(result,'source_admission','public_source_pin');self.assertFalse(self.calls);self.assertFalse(result['original_scope_readers_closed'])
    def test_real_before_observer_subject_failure_retains_correct_body_phase(self):
        before=deepcopy(self.original);before['subject']['run_id']=2;self.write_before(before)
        result=self.collect();self.assert_failure(result,'before_observer','before_observer_subject');self.assertFalse(self.calls);self.assertFalse(result['original_scope_readers_closed'])
    def test_real_app_scope_rejection_happens_before_any_log_read(self):
        self.scope['Image']='owned-wrong-image'
        result=self.collect();self.assert_failure(result,'app_inspect','app_scope_subject');self.assertEqual(len(self.calls),1);self.assertFalse(result['original_scope_readers_closed'])
    def test_parse_failure_hook_does_not_leak_unknown_exception(self):
        with patch.object(self.D,'structural_traces',side_effect=ValueError(CANARY)):
            result=self.collect()
        self.assert_failure(result,'parse','UNKNOWN');self.assertEqual(len(self.calls),2);self.assertFalse(result['original_scope_readers_closed'])
    def test_after_inspect_rejection_retains_correct_body_phase(self):
        def command(argv,**kw):
            if argv[2:4]==['container','inspect']and self.inspect_count==1:self.scope['Image']='owned-changed-image'
            return self.command(argv,**kw)
        result=self.collect(command);self.assert_failure(result,'after_inspect','app_scope_subject');self.assertEqual(len(self.calls),3);self.assertFalse(result['original_scope_readers_closed'])
    def test_real_owned_source_offset_drift_during_finalization_is_not_closed_success(self):
        original_open=self.D.os.open
        def opened(*args,**kw):fd=original_open(*args,**kw);self.opened.append(fd);return fd
        def command(argv,**kw):
            raw=self.command(argv,**kw)
            if self.inspect_count==2:os.lseek(self.opened[0],1,os.SEEK_SET)
            return raw
        with patch.object(self.D.os,'open',side_effect=opened):result=self.collect(command)
        self.assert_failure(result,'finality','public_source_changed');self.assertFalse(result['original_scope_readers_closed'])
        self.assertEqual(len(self.opened),7)
        for fd in self.opened:
            with self.assertRaises(OSError)as raised:os.fstat(fd)
            self.assertEqual(raised.exception.errno,errno.EBADF)
    def test_finalizer_after_success_keeps_closed_flag_conservative_and_no_message(self):
        actual=self.D.held_public
        @contextmanager
        def held(path,*args,**kw):
            with actual(path,*args,**kw)as pair:yield pair
            if path==self.before:raise ValueError('public_source_close')
        self.raw=self.formatted()
        with patch.object(self.D,'held_public',side_effect=held):result=self.collect()
        self.assert_failure(result,'finality','public_source_close');self.assertFalse(result['original_scope_readers_closed']);self.assertTrue(result['structure']['traces'])
    def test_unknown_log_error_never_exports_message_class_or_arbitrary_arguments(self):
        class OwnedError(RuntimeError):
            def __str__(self):raise AssertionError('exception must never be stringified')
        def command(argv,**kw):
            if argv[2:4]==['container','logs']:raise OwnedError(CANARY)
            return self.command(argv,**kw)
        result=self.collect(command);self.assert_failure(result,'log_read','UNKNOWN','PARTIAL_OR_UNAVAILABLE');self.assertIsNone(result['client']['actual_returncode']);self.assertTrue(result['original_scope_readers_closed'])
    def test_failure_codes_are_exact_closed_literals_not_exception_types_or_prefixes(self):
        class DerivedValueError(ValueError):pass
        for error in(ValueError(CANARY),ValueError('public_source_pin '+CANARY),ValueError('public_source_pin',CANARY),RuntimeError('public_source_pin'),DerivedValueError('public_source_pin'),ValueError([CANARY])):
            with self.subTest(kind=type(error).__name__):self.assertEqual(self.D.exception_failure_code(error),'UNKNOWN')
        for code in self.D.EXCEPTION_FAILURE_CODES:self.assertEqual(self.D.exception_failure_code(ValueError(code)),code)
        for code in('deadline','command_output_bound','command_failed','command_physical_close'):self.assertEqual(self.D.exception_failure_code(RuntimeError(code)),code)
        self.assertEqual(set(self.D.EXCEPTION_FAILURE_PHASES),{'scope','source_admission','before_observer','app_inspect','log_read','parse','after_inspect','finality'})
    def test_unknown_finalizer_and_source_errors_use_fixed_code(self):
        actual=self.D.held_public
        for at_end in(False,True):
            @contextmanager
            def held(path,*args,**kw):
                if not at_end and path!=self.before:raise RuntimeError(CANARY)
                with actual(path,*args,**kw)as pair:yield pair
                if at_end and path==self.before:raise RuntimeError(CANARY)
            with patch.object(self.D,'held_public',side_effect=held):result=self.collect()
            self.assert_failure(result,'finality'if at_end else'source_admission','UNKNOWN');self.assertFalse(result['original_scope_readers_closed'])

if __name__=='__main__':unittest.main()
