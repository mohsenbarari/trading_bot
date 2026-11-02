"""Finite source/owned-IO QA only: never instantiate CDLL or run Coreutils.

Synthetic child factories invoke only isolated Python snippets. Fixture UID
injections test ownership logic, not the genuine UID10001 runtime assertion.
"""
from hashlib import sha256
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest import mock
import copy
import errno
import fcntl
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
import struct

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2]


def load(name,path=None):
    p=HERE/name if path is None else path
    raw=p.read_bytes();m=ModuleType('owned_test_'+name.replace('.','_'));m.__file__=str(p)
    exec(compile(raw,str(p),'exec'),m.__dict__);return m


C=load('capsule.py');H=load('controller.py');S=load('seal_and_run.py')
CONTRACT=json.loads((HERE/'contract.json').read_text())
IO=ROOT/'deploy/maintenance/coreutils_acl_build/configure_recovery.py'


def loader_trace(program='/usr/bin/cp'):
    acl=C.ACL;attr='/usr/lib/x86_64-linux-gnu/libattr.so.1.1.2502';sel='/usr/lib/x86_64-linux-gnu/libselinux.so.1'
    pcre='/usr/lib/x86_64-linux-gnu/libpcre2-8.so.0.14.0';ld='/usr/lib/x86_64-linux-gnu/ld-linux-x86-64.so.2'
    lines=[f'  71: initialize program: {program}']
    for dst,symbol,version in ((acl,'acl_set_file_at','ACL_1.3'),(acl,'acl_delete_def_file_at','ACL_1.3'),
                              (attr,'attr_copy_fd','ATTR_1.1'),(sel,'is_selinux_enabled','LIBSELINUX_1.0'),
                              (C.LIBC,'fchmod','GLIBC_2.2.5')):
        lines += [f"  71: checking for version `{version}' in file {dst} [0] required by file {program} [0]",
                  f"  71: binding file {program} [0] to {dst} [0]: normal symbol `{symbol}' [{version}]"]
    lines += [f"  71: checking for version `GLIBC_PRIVATE' in file {ld} [0] required by file {C.LIBC} [0]",
              f"  71: checking for version `PCRE2_8.0' in file {pcre} [0] required by file {sel} [0]",
              "  71: binding file linux-vdso.so.1 [0] to linux-vdso.so.1 [0]: normal symbol `__vdso_clock_gettime' [LINUX_2.6]"]
    return ('\n'.join(lines)+'\n').encode()


class Tests(unittest.TestCase):
    def setUp(self):
        (ROOT/'.local').mkdir(exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(prefix='provider-python-QA-',dir=ROOT/'.local')
        self.dir=Path(self.temp.name)
    def tearDown(self):self.temp.cleanup()
    def assert_code(self,code,function):
        with self.assertRaises(RuntimeError)as cm:function()
        self.assertEqual(cm.exception.args,(code,));return cm.exception
    def python_factory(self,script,owners):
        def factory(argv,**kwargs):
            self.assertIn(argv[0],('/usr/bin/cp','/usr/bin/install','/usr/bin/mv'))
            self.assertEqual(kwargs['env'],C.cli_environment())
            # Collector fixtures are plain Python children, never an actual
            # host loader/provider probe. Inspect the intended CLI environment
            # above, then remove diagnostic/eager flags from the test child.
            kwargs={**kwargs,'env':{'PATH':'/usr/bin:/bin','LC_ALL':'C'}}
            child=subprocess.Popen([sys.executable,'-I','-S','-B','-c',script],**kwargs)
            owners.append(child);return child
        return factory
    def io_module(self):
        b=IO.read_bytes();self.assertEqual((len(b),sha256(b).hexdigest()),(58042,'97a1985409957c98a49ec3de1fa78ccdff897207f1d8858d19cf624faecb4c19'))
        return load('configure_recovery.py',IO)
    def dispose_fd(self,fd,path):
        self.assertEqual(os.fstat(fd).st_ino,path.stat().st_ino);os.close(fd)
        with self.assertRaises(OSError)as cm:os.fstat(fd)
        self.assertEqual(cm.exception.errno,errno.EBADF)
    def test_all_three_entries_unarmed_without_file_or_process_or_provider(self):
        with (mock.patch.object(C.ctypes,'CDLL',side_effect=AssertionError('no provider')),
             mock.patch.object(C.subprocess,'Popen',side_effect=AssertionError('no process')),
             mock.patch.object(C.os,'open',side_effect=AssertionError('no file'))):
            self.assertEqual(C.main([])['status'],'UNARMED')
            self.assertEqual(H.main([])['status'],'UNARMED')
            self.assertEqual(S.main([])['status'],'UNARMED')
    def test_unknown_unhashable_error_operand_remains_closed_schema(self):
        for function in(C.closed,H.closed,S.failure):
            self.assertEqual(function(RuntimeError({'unknown':'operand'}))['code'],'provider_failure')
    def test_old_public_codes_and_numeric_errno_retained_without_secret_chain(self):
        G=load('gold2_admission.py',ROOT/'deploy/maintenance/app_gold2_smoke/controller.py')
        old=load('run_app_image_ci_smoke.py',ROOT/'deploy/maintenance/app_r2_observe02/run_app_image_ci_smoke.py')
        P=load('producer02_controller.py',ROOT/'deploy/maintenance/app_r2_observe02/producer02_controller.py')
        allowed=G.allowed_codes(old,None)|P.CODES|H.PUBLIC_CODES
        for code in('ci_capacity','export_shape'):
            error=RuntimeError(code);error.__cause__=RuntimeError('synthetic-secret-must-not-be-published')
            row=H.closed(error,allowed);self.assertEqual(row['code'],code)
            self.assertNotIn('synthetic-secret',json.dumps(row))
        row=H.closed(OSError(errno.EIO,'synthetic-private-message','/synthetic/private/path'),allowed)
        self.assertEqual(row['os_errno'],errno.EIO);self.assertNotIn('private',json.dumps(row))
        self.assertIsNone(H.closed(OSError(-1,'private'),allowed)['os_errno'])
    def test_bootstrap_actual_source_syntax_and_arg_limits(self):
        compile(H.BOOTSTRAP,'actual-provider-bootstrap','exec')
        raws={'configure_recovery.py':IO.read_bytes(),'capsule.py':(HERE/'capsule.py').read_bytes(),'contract.json':(HERE/'contract.json').read_bytes()}
        argv=H.bootstrap_arguments(raws)
        self.assertEqual(argv[:4],['-I','-S','-B','-c']);self.assertTrue(all(len(a.encode())<=116*1024 for a in argv[5:]))
        raws['capsule.py']=b'a'*(116*1024)
        self.assert_code('provider_argument_bound',lambda:H.bootstrap_arguments(raws))
    def test_contract_duplicate_and_wrong_image_refused(self):
        self.assertEqual(len(C.validate_contract(CONTRACT)),9)
        d=copy.deepcopy(CONTRACT);d['bodies'][1]=d['bodies'][0]
        self.assert_code('provider_contract',lambda:C.validate_contract(d))
        d=copy.deepcopy(CONTRACT);d['image_id']='sha256:'+'0'*64
        self.assert_code('provider_contract',lambda:C.validate_contract(d))
    def test_loader_real_wire_style_complete_bindings(self):
        r=C.parse_loader_trace(loader_trace(),'/usr/bin/cp',CONTRACT,lambda x:x)
        self.assertIn(C.ACL,r['selected_known_body_paths']);self.assertEqual(r['actual_pid'],71)
        self.assertTrue(any(x['symbol']=='attr_copy_fd' for x in r['bindings']))
    def test_loader_search_candidates_are_not_mappings(self):
        b=b'  71: initialize program: /usr/bin/cp\n  71: trying file=/usr/lib/x86_64-linux-gnu/libacl.so.1.2.2400\n'
        self.assert_code('provider_trace_mapping',lambda:C.parse_loader_trace(b,'/usr/bin/cp',CONTRACT,lambda x:x))
    def test_loader_wrong_acl_version_and_mixed_pid_refused(self):
        self.assert_code('provider_trace_symbol',lambda:C.parse_loader_trace(loader_trace().replace(b'ACL_1.3',b'ACL_1.2'),'/usr/bin/cp',CONTRACT,lambda x:x))
        self.assert_code('provider_trace_mapping',lambda:C.parse_loader_trace(loader_trace()+b'  72: other line\n','/usr/bin/cp',CONTRACT,lambda x:x))
    def test_attr_symbol_on_wrong_body_not_accepted(self):
        attr='/usr/lib/x86_64-linux-gnu/libattr.so.1.1.2502'
        b=loader_trace().replace((f"to {attr} [0]: normal symbol `attr_copy_fd'").encode(),(f"to {C.LIBC} [0]: normal symbol `attr_copy_fd'").encode())
        b+=f"  71: binding file /usr/bin/cp [0] to {attr} [0]: normal symbol `unrelated' [ATTR_1.1]\n".encode()
        self.assert_code('provider_trace_symbol',lambda:C.parse_loader_trace(b,'/usr/bin/cp',CONTRACT,lambda x:x))
    def test_unknown_absolute_provider_and_missing_transitive_refused(self):
        b=loader_trace()+b"  71: binding file /usr/bin/cp [0] to /unknown/libacl.so [0]: normal symbol `x' [ACL_1.3]\n"
        self.assert_code('provider_trace_mapping',lambda:C.parse_loader_trace(b,'/usr/bin/cp',CONTRACT,lambda x:x))
        b=b'\n'.join(x for x in loader_trace().splitlines()if b'PCRE2_8.0'not in x)+b'\n'
        self.assert_code('provider_trace_mapping',lambda:C.parse_loader_trace(b,'/usr/bin/cp',CONTRACT,lambda x:x))
    def test_actual_ELF_interpreter_field_and_duplicate_refusal(self):
        raw=bytearray(512);raw[:6]=b'\x7fELF\x02\x01';name=b'/lib64/ld-linux-x86-64.so.2\0'
        struct.pack_into('<Q',raw,32,64);struct.pack_into('<HH',raw,54,56,1)
        struct.pack_into('<IIQQQQQQ',raw,64,3,4,256,0,0,len(name),len(name),1);raw[256:256+len(name)]=name
        target='/usr/lib/x86_64-linux-gnu/ld-linux-x86-64.so.2'
        self.assertEqual(C.interpreter(bytes(raw),lambda _:target),{'PT_INTERP':name[:-1].decode(),'resolved_interpreter':target})
        self.assert_code('provider_contract',lambda:C.interpreter(bytes(raw),lambda _:'/wrong/loader'))
        struct.pack_into('<H',raw,56,2);struct.pack_into('<IIQQQQQQ',raw,120,3,4,256,0,0,len(name),len(name),1)
        self.assert_code('provider_contract',lambda:C.interpreter(bytes(raw),lambda _:target))
    def test_delayed_real_python_pipe_timestamp_is_not_source_drift(self):
        owners=[];r=C.child_command(['/usr/bin/cp'],lambda:None,time.monotonic()+3,
            popen=self.python_factory("import sys,time;time.sleep(.15);sys.stdout.write('ok');sys.stdout.flush();sys.stderr.write('public synthetic child\\n')",owners))
        self.assertEqual(r['stdout'],b'ok');self.assertTrue(r['physical_child_and_pipes_finalized'])
        self.assertIsNotNone(owners[0].returncode);self.assertTrue(owners[0].stdout.closed and owners[0].stderr.closed)
    def test_first_pipe_fstat_failure_still_closes_both_owned_prefixes(self):
        owners=[];original=C.os.fstat;target={'fd':None,'fired':False};marker=OSError(errno.EIO,'owned synthetic')
        create=self.python_factory('import time;time.sleep(3)',owners)
        def factory(argv,**kw):
            child=create(argv,**kw);target['fd']=child.stdout.fileno();return child
        def fstat(fd):
            if fd==target['fd']and not target['fired']:target['fired']=True;raise marker
            return original(fd)
        with mock.patch.object(C.os,'fstat',side_effect=fstat):
            with self.assertRaises(OSError)as cm:C.child_command(['/usr/bin/cp'],lambda:None,time.monotonic()+2,popen=factory)
        self.assertIs(cm.exception,marker);self.assertTrue(target['fired'])
        self.assertTrue(owners[0].stdout.closed and owners[0].stderr.closed);self.assertIsNotNone(owners[0].returncode)
        self.assertTrue(all(r['physically_closed']for r in marker.provider_child_evidence['pipe_prefixes']))
    def test_child_output_cap_refuses_before_append_and_reaps(self):
        owners=[]
        with self.assertRaises(RuntimeError)as cm:C.child_command(['/usr/bin/cp'],lambda:None,time.monotonic()+3,
            popen=self.python_factory('import sys;sys.stdout.buffer.write(b"x"*20000);sys.stdout.flush()',owners))
        self.assertEqual(cm.exception.args,('provider_stdout_bound',))
        r=cm.exception.provider_child_evidence;self.assertLessEqual(len(r['stdout_prefix']),C.STDOUT_MAX)
        self.assertGreater(r['observed_bytes']['stdout'],C.STDOUT_MAX)
        self.assertTrue(owners[0].stdout.closed and owners[0].stderr.closed);self.assertIsNotNone(owners[0].returncode)
    def test_all_symbol_binding_gate_requires_explicit_eager_environment(self):
        env=C.cli_environment();self.assertEqual(env['LD_BIND_NOW'],'1')
        self.assertEqual(set(env),{'PATH','LC_ALL','LD_DEBUG','LD_BIND_NOW'})
        lazy={k:v for k,v in env.items()if k!='LD_BIND_NOW'}
        with mock.patch.object(C,'cli_environment',return_value=lazy):
            self.assert_code('provider_environment',lambda:C.child_command(['/usr/bin/cp'],lambda:None,time.monotonic()+1,
                popen=lambda *a,**k:(_ for _ in()).throw(AssertionError('must refuse before spawn'))))
    def test_fixture_writer_uses_actual_readback_and_physical_finalizers(self):
        M=self.io_module();r=C.fresh_file(M,self.dir/'public')
        self.assertTrue(r['original_writer_reader_finalizers_complete']);self.assertEqual((self.dir/'public').read_bytes(),C.PAYLOAD)
        self.assert_code('provider_fixture_fresh',lambda:C.fresh_file(M,self.dir/'public'))
    def test_fixture_writer_first_fstat_error_retains_original_state(self):
        M=self.io_module();original=M.os.fstat;marker=OSError(errno.EIO,'owned synthetic');fired=False
        def fstat(fd):
            nonlocal fired
            if not fired:fired=True;raise marker
            return original(fd)
        with mock.patch.object(M.os,'fstat',side_effect=fstat):
            with self.assertRaises(BaseException)as cm:C.fresh_file(M,self.dir/'public')
        state=cm.exception.provider_fixture_state;self.assertTrue(any(e is marker for e in state.errors))
        self.assertTrue(state.rows and state.rows[0].original_fd is not None)
        self.dispose_fd(state.rows[0].original_fd,self.dir/'public')
    def test_object_first_fstat_error_retains_acquired_original_custody(self):
        path=self.dir/'object';path.write_bytes(b'public');fixture=C.ACLFixture.__new__(C.ACLFixture);fixture.guard=lambda:None;fixture.owned=[]
        original=C.os.fstat;marker=OSError(errno.EIO,'owned synthetic');fired=False
        def fstat(fd):
            nonlocal fired
            if not fired:fired=True;raise marker
            return original(fd)
        with mock.patch.object(C.os,'fstat',side_effect=fstat):
            with self.assertRaises(RuntimeError)as cm:fixture.held(path,lambda _:None)
        self.assertEqual(cm.exception.args,('provider_object_HELD',));row=cm.exception.provider_object_prefixes[0]
        self.assertIs(row.errors[0],marker);self.assertFalse(row.closed);self.dispose_fd(row.original_fd,path)
    def test_object_alias_refuses_and_keeps_original_and_alias_owned(self):
        path=self.dir/'object';path.write_bytes(b'public');row=C.ObjectPrefix(path)
        with mock.patch.object(C,'UID',os.getuid()):
            row.acquire();original=row.original_fd;alias=os.open(path,os.O_PATH|os.O_CLOEXEC)
            row.fd=row.original_fd=alias
            self.assert_code('provider_object_HELD',row.close)
            self.dispose_fd(original,path);self.dispose_fd(alias,path)
    def test_mutable_data_fixture_uses_same_owned_prefix_physical_finalizer(self):
        p=self.dir/'data';p.write_bytes(b'public');fixture=C.ACLFixture.__new__(C.ACLFixture);fixture.guard=lambda:None;fixture.owned=[]
        with mock.patch.object(C,'UID',os.getuid()):
            value=fixture.held(p,lambda fd:os.pread(fd,64,0),data=True)
        self.assertEqual(value,b'public');self.assertTrue(fixture.owned[0].closed)
    def bootstrap_fixture(self):
        p=self.dir/'public-bootstrap.py';p.write_bytes(b'# owned source fixture\n');p.chmod(0o644)
        self.assertIn(os.getuid(),(0,1001))
        b=p.read_bytes();return p,S.Bootstrap(p,(len(b),sha256(b).hexdigest()),lambda:None)
    def test_bootstrap_original_reader_flags_and_physical_close(self):
        p,row=self.bootstrap_fixture();actual=p.stat();original=S.frame
        def frame_fixture(s):
            f=list(original(s))
            # Only this owned source node uses synthetic hosted-owner metadata
            # on Root-only filesystems. The FD, flags, remaining full9 fields
            # and physical closes are real; this is not a hosted UID proof.
            if(s.st_dev,s.st_ino)==(actual.st_dev,actual.st_ino):f[3:5]=[1001,1001]
            return tuple(f)
        with mock.patch.object(S,'frame',side_effect=frame_fixture):
            row.acquire();fd=row.original_fd
            self.assertEqual(row.raw,p.read_bytes());self.assertEqual(len(row.before),9)
            self.assertTrue(row.fl[1]&fcntl.FD_CLOEXEC);row.close()
        with self.assertRaises(OSError)as cm:os.fstat(fd)
        self.assertEqual(cm.exception.errno,errno.EBADF)
    def test_bootstrap_first_fstat_error_preserves_owned_prefix_and_cause(self):
        p,row=self.bootstrap_fixture();original=S.os.fstat;marker=OSError(errno.EIO,'owned synthetic');fired=False
        def fstat(fd):
            nonlocal fired
            if not fired:fired=True;raise marker
            return original(fd)
        with mock.patch.object(S.os,'fstat',side_effect=fstat):
            with self.assertRaises(OSError)as cm:row.acquire()
        self.assertIs(cm.exception,marker);self.assertTrue(any(e is marker for e in row.errors))
        with self.assertRaises(RuntimeError):row.close()
        self.dispose_fd(row.original_fd,p)
    def test_create_is_exact_user_two_tmpfs_no_mount_or_pull(self):
        old=SimpleNamespace(LABEL='io.gold-trade.app-image-ci-smoke-owner',owner=lambda:'91-1')
        raws={'configure_recovery.py':IO.read_bytes(),'capsule.py':(HERE/'capsule.py').read_bytes(),'contract.json':(HERE/'contract.json').read_bytes()}
        args=H.create_arguments(old,'app-ci-provider-91-1',H.IMAGE,raws)
        self.assertIn('--user=10001:10001',args);self.assertIn('--read-only',args);self.assertIn('--network=none',args)
        self.assertIn('--pull=never',args);self.assertEqual(args.count('--tmpfs'),2);self.assertFalse(any('type=bind' in a for a in args))
        self.assertEqual(args[-len(H.bootstrap_arguments(raws))-1],H.IMAGE)
    def result_fixture(self):
        raws={'configure_recovery.py':IO.read_bytes(),'capsule.py':(HERE/'capsule.py').read_bytes(),'contract.json':(HERE/'contract.json').read_bytes()}
        programs={'cp_tree':'/usr/bin/cp','install_mode':'/usr/bin/install','mv_EXDEV_tree':'/usr/bin/mv','cp_parents':'/usr/bin/cp'}
        result={'schema':'current_coreutils_provider_owned_smoke/1','image_id':H.IMAGE,'real_uid':10001,'fixture_cases_passed':4,
            'different_tmpfs_devices_proven':True,'source_device':1,'destination_device':2,'source_provider_original_finalizers_complete':True,
            'source_provider_finality_pending_bootstrap':False,'candidate_CI_compatibility_only':True,'production_activation_performed':False,
            'security_accepted':False,'provider_accepted':False,'fixture_object_original_finalizers_complete':True,
            'fixture_writers':[{'original_writer_reader_finalizers_complete':True}for _ in range(4)],'real_Coreutils_loader_bindings_observed':True,
            'ordinary_lazy_execution_timing_proven':False,'loader_binding_scope':'CI-only eager relocations using LD_BIND_NOW=1; not function-call events or ordinary lazy timing',
            'original_input_and_writer_count':6+9+sum(len(r['symlink_aliases'])for r in CONTRACT['bodies'])+len(CONTRACT['directory_aliases']),
            'cases':[{'case':case,'argv':[program],'actual_returncode':0,'physical_child_and_pipes_finalized':True,'loader_environment':C.cli_environment(),
                      'loader':C.parse_loader_trace(loader_trace(program),program,CONTRACT,lambda x:x)}for case,program in programs.items()],
            'original_body_readers':[{'path':r['path'],'bytes':r['bytes'],'sha256':r['sha256'],'held_through_all_four_CLI_cases':True,
                                      'original_full9':[1,2,0o100000|r['mode'],0,0,1,r['bytes'],0,0],'original_reader_flags':[0,1]}for r in CONTRACT['bodies']],
            'program_interpreters':[{'program':p,'PT_INTERP':'/lib64/ld-linux-x86-64.so.2','resolved_interpreter':'/usr/lib/x86_64-linux-gnu/ld-linux-x86-64.so.2'}for p in sorted(set(programs.values()))],
            'source_copies':{n:{'bytes':len(b),'sha256':sha256(b).hexdigest(),'mode':'0o644','original_full9':[1,2,0o100644,10001,10001,1,len(b),0,0]}for n,b in raws.items()}}
        return result,raws
    def test_typed_receipt_refuses_incomplete_finality_bool_counts_and_wrong_body(self):
        result,raws=self.result_fixture();self.assertIs(H.validate_result(result,raws),result)
        bad=copy.deepcopy(result);bad['original_input_and_writer_count']=0
        self.assert_code('provider_result',lambda:H.validate_result(bad,raws))
        bad=copy.deepcopy(result);bad['real_uid']=True
        self.assert_code('provider_result',lambda:H.validate_result(bad,raws))
        bad=copy.deepcopy(result);bad['original_body_readers'][0]['original_full9'][3]=10001
        self.assert_code('provider_result',lambda:H.validate_result(bad,raws))
        bad=copy.deepcopy(result);bad['cases'][0]['loader']['bindings']=[]
        self.assert_code('provider_result',lambda:H.validate_result(bad,raws))
        bad=copy.deepcopy(result);bad['source_provider_finality_pending_bootstrap']=True
        self.assert_code('provider_result',lambda:H.validate_result(bad,raws))
        bad=copy.deepcopy(result);del bad['cases'][0]['loader_environment']['LD_BIND_NOW']
        self.assert_code('provider_result',lambda:H.validate_result(bad,raws))
    def test_original_artifact_representation_not_fresh_compressed_pin(self):
        intake=json.loads((ROOT/'deploy/maintenance/app_gold2_smoke/intake.json').read_text())['control']
        self.assertEqual((intake['run_id'],intake['image_artifact_id'],intake['image_zip_bytes']),(40844680297,12885065260,192672061))
        self.assertEqual(intake['image_zip_sha256'],'81e23bc7effc8f5185b95a8eb07371b1474f8cdb7b9f5585eb4863aa0dd4044f')
        self.assertEqual(intake['archive_observation']['archive_bytes'],476681728)
        self.assertNotEqual(intake['image_zip_bytes'],193210687)
    def test_complete_source_cohort_and_entry_workflow_pins(self):
        raw=(HERE/'source-manifest.json').read_bytes();self.assertEqual(S.MANIFEST_PIN,(len(raw),sha256(raw).hexdigest()))
        data=json.loads(raw);self.assertEqual(data['schema'],'current_provider_source_cohort/1')
        self.assertLessEqual(len(data['files']),32);self.assertEqual(len({r['name']for r in data['files']}),len(data['files']))
        for row in data['files']:
            b=(ROOT/row['path']).read_bytes();self.assertEqual((len(b),sha256(b).hexdigest()),(row['bytes'],row['sha256']))
        b=(ROOT/S.OUTER_PATH).read_bytes();self.assertEqual(S.OUTER_PIN,(len(b),sha256(b).hexdigest()))
        entry=(HERE/'seal_and_run.py').read_bytes();workflow=(ROOT/'.github/workflows/coreutils-provider-candidate-smoke.yml').read_text()
        self.assertIn('--source-sha256 '+sha256(entry).hexdigest(),workflow);self.assertIn('default: false',workflow)


if __name__=='__main__':unittest.main(verbosity=2)
