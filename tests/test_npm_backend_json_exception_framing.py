"""Real public formatter AST -> strict diagnostic parser; owned records only.

No core.config/Env import, logging configuration, Docker or network operation.
Metadata/redaction seams are identity stubs to test the consumer independently
of upstream redaction. The formatter class AST itself remains unchanged.
"""
import ast
import builtins
from datetime import datetime,timezone
import errno
import fcntl
from hashlib import sha256
import json
import logging
import os
from pathlib import Path
import sys
import traceback
from types import ModuleType
from typing import Any
import unittest
from unittest.mock import patch

ROOT=Path(__file__).absolute().parents[1]
FORMATTER_PIN=(7026,'41751aad4f54aea1c5df525e881717e522c059bbbf36aff405bc46da713c455b')
CANARY='PUBLIC_OWNED_JSON_MESSAGE_CANARY'

def public_bytes(path,maximum,pin=None):
    frame=lambda s:(s.st_dev,s.st_ino,s.st_mode,s.st_uid,s.st_gid,s.st_nlink,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC);before=frame(os.fstat(fd));flags=(fcntl.fcntl(fd,fcntl.F_GETFL),fcntl.fcntl(fd,fcntl.F_GETFD));offset=os.lseek(fd,0,os.SEEK_CUR)
    try:
        if path.resolve()!=path or not 0<before[6]<=maximum:raise AssertionError('public Source bound')
        raw=os.pread(fd,before[6]+1,0)
        if len(raw)!=before[6]or(pin is not None and(len(raw),sha256(raw).hexdigest())!=pin):raise AssertionError('public Source pin')
        if frame(os.fstat(fd))!=frame(path.lstat())or frame(path.lstat())!=before:raise AssertionError('public Source drift')
        if flags!=(fcntl.fcntl(fd,fcntl.F_GETFL),fcntl.fcntl(fd,fcntl.F_GETFD))or offset!=os.lseek(fd,0,os.SEEK_CUR):raise AssertionError('public Source reader drift')
    finally:os.close(fd)
    try:os.fstat(fd)
    except OSError as exc:
        if exc.errno!=errno.EBADF:raise
    else:raise AssertionError('public Source reader not closed')
    if frame(path.lstat())!=before:raise AssertionError('public Source postclose drift')
    return raw

def load_parser():
    path=ROOT/'scripts/npm_security_ci/backend_diagnostics.py';raw=public_bytes(path,65536)
    module=ModuleType('_owned_public_trace_parser');module.__file__=str(path)
    exec(compile(raw,str(path),'exec'),module.__dict__);return module

def formatter_class():
    path=ROOT/'core/logging_config.py';raw=public_bytes(path,16384,FORMATTER_PIN);tree=ast.parse(raw)
    selected=[node for node in tree.body if(isinstance(node,ast.Assign)and any(getattr(t,'id',None)in{'SERVICE_NAME','_RESERVED_RECORD_ATTRS'}for t in node.targets))or(isinstance(node,ast.FunctionDef)and node.name=='_utc_now_iso')or(isinstance(node,ast.ClassDef)and node.name=='JsonLogFormatter')]
    namespace={'json':json,'logging':logging,'traceback':traceback,'datetime':datetime,'timezone':timezone,'Any':Any,
      '_runtime_metadata':lambda:{},'redact':lambda value:value,'is_safe_integrity_hash_key':lambda _:False,'is_sensitive_key':lambda _:False,'REDACTED':'OWNED_STUB_ONLY'}
    real_import=builtins.__import__
    def guarded_import(name,*args,**kwargs):
        if name.startswith(('core.config','core.log_redaction','core.request_context')):raise AssertionError('configured application imports forbidden')
        return real_import(name,*args,**kwargs)
    with patch.object(builtins,'__import__',side_effect=guarded_import):exec(compile(ast.Module(body=selected,type_ignores=[]),str(path),'exec'),namespace)
    return namespace['JsonLogFormatter']

def owned_info(code='raise ValueError(CANARY)',extra=None):
    namespace={'CANARY':CANARY,**(extra or{})}
    try:exec(compile(code,'/app/api/routers/offers.py','exec'),namespace)
    except Exception:return sys.exc_info()
    raise AssertionError('owned exception missing')

class JsonFramingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.D=load_parser();cls.Formatter=formatter_class()
    def formatted(self,info=None,message=CANARY):
        record=logging.LogRecord('owned.public',logging.ERROR,__file__,1,message,(),info or owned_info())
        record.headers={'Authorization':'Bearer '+CANARY};record.sql_parameters={'private_value':CANARY}
        return self.Formatter().format(record)
    def consume(self,text):return self.D.structural_traces(text.encode('utf-8'))
    def assert_private_absent(self,result):
        encoded=json.dumps(result)
        self.assertNotIn(CANARY,encoded);self.assertNotIn('Authorization',encoded);self.assertNotIn('sql_parameters',encoded)
    def test_unchanged_actual_formatter_producer_to_consumer(self):
        wire=self.formatted();payload=json.loads(wire)
        self.assertEqual(set(payload['exception']),{'type','message','stacktrace'});self.assertIn(CANARY,payload['message']);self.assertIn(CANARY,payload['exception']['message'])
        self.assertEqual(self.D.SYNTHETIC_SOURCE_PINS['core/logging_config.py'],FORMATTER_PIN)
        result=self.consume('2026-11-01T16:00:00.123Z '+wire)
        self.assertEqual(result['structured_exception_records_seen'],1);self.assertEqual(result['unsupported_framing_count'],0)
        self.assertTrue(any(row['exception_class']=='ValueError'and{'file':'api/routers/offers.py','line':1}in row['application_frames']for row in result['traces']));self.assert_private_absent(result)
    def test_chained_exception_formatter_roundtrip(self):
        info=owned_info('try:\n raise ValueError(CANARY)\nexcept ValueError as error:\n raise RuntimeError(CANARY) from error')
        result=self.consume(self.formatted(info));self.assertTrue({'ValueError','RuntimeError'}<=set(row['exception_class']for row in result['traces']));self.assert_private_absent(result)
    def test_exception_group_formatter_roundtrip(self):
        first=owned_info()[1];second=owned_info('raise TypeError(CANARY)')[1]
        info=owned_info('raise ExceptionGroup(CANARY, members)',{'members':[first,second]})
        result=self.consume(self.formatted(info));classes={row['exception_class']for row in result['traces']}
        self.assertTrue({'ExceptionGroup','ValueError','TypeError'}<=classes);self.assertEqual(result['unsupported_framing_count'],0);self.assert_private_absent(result)
    def test_nested_exception_group_formatter_roundtrip(self):
        leaf=owned_info()[1];inner=owned_info('raise ExceptionGroup(CANARY, members)',{'members':[leaf]})[1]
        info=owned_info('raise ExceptionGroup(CANARY, members)',{'members':[inner]})
        result=self.consume(self.formatted(info));self.assertTrue(any(row['exception_class']=='ValueError'for row in result['traces']));self.assert_private_absent(result)
    def test_json_escaped_unicode_and_newlines(self):
        payload=json.loads(self.formatted(message=CANARY+'\nپیام خصوصی 🔒'))
        result=self.consume(json.dumps(payload,ensure_ascii=True));self.assertTrue(result['traces']);self.assert_private_absent(result)
    def test_real_formatter_line_larger_than_4096_remains_useful(self):
        wire=self.formatted(message=CANARY*2000);self.assertGreater(len(wire),4096);self.assertLess(len(wire.encode()),self.D.APP_TAIL_MAX)
        result=self.consume(wire);self.assertTrue(result['traces']);self.assert_private_absent(result)
    def test_aggregate_bound_precedes_json_decode(self):
        with patch.object(self.D.json,'loads',side_effect=AssertionError('must not parse oversized raw input')):
            with self.assertRaises(ValueError):self.D.structural_traces(b'{'+b'X'*self.D.APP_TAIL_MAX)
    def test_deep_json_refused_before_json_decoder(self):
        raw='{"exception":'+('['*17)+'0'+(']'*17)+'}'
        with patch.object(self.D.json,'loads',side_effect=AssertionError('must not decode excessive depth')):
            result=self.consume(raw)
        self.assertFalse(result['traces']);self.assertEqual(result['unsupported_framing_count'],1)
    def test_malformed_duplicate_nonfinite_and_wrong_roots_refuse(self):
        cases=['{"exception":','{"exception":{},"exception":{}}','{"exception":NaN}','[]','{"exception":null}']
        for raw in cases:
            with self.subTest(raw=raw):
                result=self.consume(raw);self.assertFalse(result['traces']);self.assertGreater(result['unsupported_framing_count'],0)
    def test_exception_wrong_type_unknown_field_and_stack_type_refuse(self):
        payload=json.loads(self.formatted())
        for change in('wrong_exception','unknown_field','stack_list','stack_empty','surrogate'):
            value=json.loads(json.dumps(payload))
            if change=='wrong_exception':value['exception']=[]
            elif change=='unknown_field':value['exception']['private_extra']=CANARY
            elif change=='stack_list':value['exception']['stacktrace']=[CANARY]
            elif change=='surrogate':value['exception']['stacktrace']='\ud800'
            else:value['exception']['stacktrace']=''
            result=self.consume(json.dumps(value));self.assertFalse(result['traces']);self.assertGreater(result['unsupported_framing_count'],0);self.assert_private_absent(result)
    def test_messages_types_stack_info_and_unknown_json_values_are_never_selected(self):
        stack=json.loads(self.formatted())['exception']['stacktrace']
        values=[{'message':stack,'stack_info':stack,'type':stack,'private':{'stacktrace':stack}},
          {'message':stack,'exception':{'type':'ValueError','message':stack,'stacktrace':'no actual traceback'}}]
        for value in values:
            result=self.consume(json.dumps(value));self.assertFalse(result['traces']);self.assert_private_absent(result)
        value=json.loads(self.formatted());value['type']=CANARY;value['exception']['type']=CANARY;value['unrelated']={'token':CANARY}
        result=self.consume(json.dumps(value));self.assertTrue(result['traces']);self.assert_private_absent(result)
    def test_plain_uvicorn_traceback_still_supported(self):
        stack=json.loads(self.formatted())['exception']['stacktrace'];result=self.consume(stack)
        self.assertTrue(result['traces']);self.assertEqual(result['structured_exception_records_seen'],0);self.assert_private_absent(result)
    def test_old_privacy_canaries_fail_inside_json_too(self):
        for path,klass in[('/app/api/PUBLIC_OWNED_MESSAGE_VALUE_CANARY.py','ValueError'),('/usr/local/lib/python3.11/site-packages/sqlalchemy/PUBLIC_OWNED_MESSAGE_VALUE_CANARY.py','ValueError'),('/app/api/routers/offers.py','PUBLIC_OWNED_MESSAGE_VALUE_CANARYError')]:
            stack='Traceback (most recent call last):\n  File "'+path+'", line 42, in public_function\n'+klass+': '+CANARY+'\n'
            result=self.consume(json.dumps({'exception':{'type':CANARY,'message':CANARY,'stacktrace':stack}}))
            self.assertNotIn('PUBLIC_OWNED_MESSAGE_VALUE_CANARY',json.dumps(result));self.assert_private_absent(result)
            if path=='/app/api/routers/offers.py':self.assertEqual(result['traces'][0]['exception_class'],'UNKNOWN_EXCEPTION_CLASS')
            else:self.assertFalse(result['traces'])
    def pending_frame(self):
        return 'Traceback (most recent call last):\n  File "/app/api/routers/offers.py", line 42, in public_function\n'
    def json_stack(self,stack):
        return json.dumps({'exception':{'type':'ValueError','message':'OWNED_RECORD_B','stacktrace':stack}})+'\n'
    def assert_separate_records_do_not_join(self,first,second):
        self.assertFalse(self.consume(first)['traces']);self.assertFalse(self.consume(second)['traces'])
        combined=self.consume(first+second)
        self.assertFalse(combined['traces']);self.assertTrue(combined['unfinished_trace_discarded']);self.assert_private_absent(combined)
    def test_peer_plain_pending_to_json_class_only_cannot_join(self):
        self.assert_separate_records_do_not_join(self.pending_frame(),self.json_stack('ValueError: OWNED_RECORD_B'))
    def test_peer_json_pending_to_plain_class_cannot_join(self):
        first=json.dumps({'exception':{'type':'ValueError','message':'OWNED_RECORD_A','stacktrace':self.pending_frame()}})+'\n'
        self.assert_separate_records_do_not_join(first,'ValueError: OWNED_RECORD_B\n')
    def test_peer_malformed_json_ends_pending_plain(self):
        self.assert_separate_records_do_not_join(self.pending_frame()+'{malformed}\n','ValueError: OWNED_RECORD_B\n')
    def test_peer_nonexception_json_ends_pending_plain(self):
        self.assert_separate_records_do_not_join(self.pending_frame()+'{"level":"INFO","message":"OWNED_RECORD_B"}\n','ValueError: OWNED_RECORD_C\n')
    def test_all_rejected_structured_shapes_isolate_pending_trace(self):
        for record in['[]','{"exception":null}','{"exception":{}}','{"exception":NaN}','{"exception":{},"exception":{}}','{"exception":'+('['*17)+'0'+(']'*17)+'}']:
            with self.subTest(record=record):
                result=self.consume(self.pending_frame()+record+'\nValueError: OWNED_RECORD_B\n')
                self.assertFalse(result['traces']);self.assertTrue(result['unfinished_trace_discarded']);self.assertGreater(result['unsupported_framing_count'],0)
    def test_chain_and_group_inside_one_selected_record_are_not_reset(self):
        chain=owned_info('try:\n raise ValueError(CANARY)\nexcept ValueError as error:\n raise RuntimeError(CANARY) from error')
        group=owned_info('raise ExceptionGroup(CANARY, members)',{'members':[chain[1],owned_info('raise TypeError(CANARY)')[1]]})
        wire=self.formatted(group);standalone=self.consume(wire)
        isolated=self.consume(self.pending_frame()+wire+'\nValueError: OWNED_RECORD_AFTER\n')
        self.assertEqual(isolated['traces'],standalone['traces']);self.assertTrue(isolated['unfinished_trace_discarded'])
        self.assertTrue({'ExceptionGroup','ValueError','RuntimeError','TypeError'}<={row['exception_class']for row in isolated['traces']});self.assert_private_absent(isolated)
    def test_arbitrary_log_strings_cannot_forge_internal_record_boundary(self):
        fake_markers=['record_boundary','<object object at 0x1234>','SOURCE_JSON_RECORD_BOUNDARY','\x1eJSON_RECORD_END\x1e']
        for marker in fake_markers:
            with self.subTest(marker=marker):
                stack=self.pending_frame()+marker+'\nValueError: '+CANARY+'\n'
                for wire in(stack,self.json_stack(stack)):
                    result=self.consume(wire)
                    self.assertEqual(len(result['traces']),1);self.assertEqual(result['traces'][0]['exception_class'],'ValueError');self.assertFalse(result['unfinished_trace_discarded']);self.assert_private_absent(result)
    def test_complete_records_still_deduplicate_while_unfinished_eof_is_discarded(self):
        complete=self.pending_frame()+'ValueError: '+CANARY+'\n'
        result=self.consume(complete+self.json_stack(complete)+self.pending_frame())
        self.assertEqual(len(result['traces']),1);self.assertEqual(result['traces'][0]['count'],2);self.assertTrue(result['unfinished_trace_discarded']);self.assert_private_absent(result)

if __name__=='__main__':unittest.main()
