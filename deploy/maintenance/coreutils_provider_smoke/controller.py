"""Separate opt-in hosted provider capsule; no old App32/inventory execution."""
from contextlib import ExitStack
from hashlib import sha256
from pathlib import Path
from types import ModuleType
import argparse
import base64
import json
import os
import re
import time

CONFIRM='RUN-ONLY-2389-COREUTILS-PROVIDER-OWNED-FIXTURES-20261029-01'
IMAGE='sha256:2389c900dbbf520bd7d7f723e8c48a5c258f1612c43e80d2b66c6d046f9f8434'
MAX_RESULT=2*1024**2
TMPFS={p:'rw,noexec,nosuid,nodev,mode=0700,uid=10001,gid=10001,size=33554432' for p in('/proof','/target')}
PUBLIC_CODES=frozenset(('provider_entry','provider_host','provider_source','provider_create','provider_container',
    'provider_result','provider_deadline','provider_cleanup','provider_source_finality','provider_outer_finality','provider_argument_bound'))
LAST_FAILURE=None


def need(ok,code):
    if not ok: raise RuntimeError(code)


def encoded(value):
    parts=[];size=0
    for part in json.JSONEncoder(sort_keys=True,separators=(',',':'),allow_nan=False).iterencode(value):
        b=part.encode();need(size+len(b)+1<=MAX_RESULT,'provider_result');size+=len(b);parts.append(b)
    return b''.join(parts)+b'\n'


def closed(error,allowed=PUBLIC_CODES):
    code=error.args[0]if type(error)is RuntimeError and len(error.args)==1 else None
    if type(code)is not str:code=None
    return {'code':code if code in allowed else 'provider_failure','exception_class':type(error).__name__
        if type(error).__name__ in {'RuntimeError','OSError','ValueError','TypeError','KeyError','TimeoutError','FileNotFoundError'} else 'unclassified_exception',
        'os_errno':error.errno if isinstance(error,OSError)and type(error.errno)is int and 0<error.errno<=4095 else None}


BOOTSTRAP=r'''import base64,errno,hashlib,json,os,sys,types
def need(v,c):
 if not v:raise RuntimeError(c)
IO_CODES=frozenset(('fixed_input_pin','failed_scope','input_parent_type','input_path','input_type','input_flags','input_hash','input_HELD','input_alias','input_name','input_parent','input_FD','failed_or_closed_read','short_input','close_replay','close_HELD','close_unknown','writer_parent_type','writer_flags','writer_type','writer_HELD','writer_alias','writer_name','writer_parent','writer_FD','failed_or_closed_write','writer_progress','writer_close_replay','writer_close_HELD','writer_close_unknown','output_bound','output_readback','normal_linker_binding','normal_link_replay','original_deadline','configure_recovery_HELD'))
def closed(e):
 code=e.args[0]if(type(e)is RuntimeError or(M is not None and type(e)is getattr(M,'Held',None)))and len(e.args)==1 else None
 if type(code)is not str:code=None
 allowed=IO_CODES|frozenset(('provider_uid','provider_argument_bound','provider_source_pin','provider_source_copy','provider_entry','provider_outer_finality'))|(frozenset()if module is None else getattr(module,'PUBLIC_CODES',frozenset()))
 return {'code':code if code in allowed else 'provider_failure','exception_class':type(e).__name__ if type(e).__name__ in {'RuntimeError','OSError','ValueError','TypeError'} else 'unclassified_exception','os_errno':e.errno if isinstance(e,OSError)and type(e.errno)is int and 0<e.errno<=4095 else None}
def decode(arg,pin):
 need(type(arg)is str and len(arg)<=116*1024,'provider_argument_bound');b=base64.b64decode(arg,validate=True)
 need((len(b),hashlib.sha256(b).hexdigest())==tuple(pin),'provider_source_pin');return b
M=None;s=None;module=None;primary=None;result=None;copy=None;final=None
try:
 need(os.getuid()==os.geteuid()==os.getgid()==os.getegid()==10001,'provider_uid')
 need(len(sys.argv)==5 and all(type(a)is str and 0<len(a)<=116*1024 for a in sys.argv[1:]),'provider_argument_bound')
 pins=json.loads(sys.argv[1]);need(type(pins)is dict and set(pins)=={'io','capsule','contract'},'provider_source_pin')
 io=decode(sys.argv[2],pins['io']);raw=decode(sys.argv[3],pins['capsule']);contractraw=decode(sys.argv[4],pins['contract'])
 M=types.ModuleType('frozen_provider_io');M.__file__='/proof/configure_recovery.py';exec(compile(io,M.__file__,'exec'),M.__dict__)
 s=M.State();copies={}
 for name,b in(('configure_recovery.py',io),('capsule.py',raw),('contract.json',contractraw)):
  copies[name]=M.fixed_write(s,'/proof/'+name,b,mode=0o644,maximum=131072)
 need(copies=={n:{'bytes':p[0],'sha256':p[1],'mode':'0o644'}for n,p in(('configure_recovery.py',pins['io']),('capsule.py',pins['capsule']),('contract.json',pins['contract']))},'provider_source_copy')
 for name in copies:
  row=next(r for r in s.rows if type(r)is M.Loan and str(r.path)=='/proof/'+name)
  need(row.admitted and not row.closed and row.before[2:7]==(0o100644,10001,10001,1,copies[name]['bytes']) and M.frame(os.stat(row.path,follow_symlinks=False))==row.before,'provider_source_copy')
  copies[name]['original_full9']=list(row.before)
 module=types.ModuleType('frozen_provider_capsule');module.__file__='/proof/capsule.py';exec(compile(raw,module.__file__,'exec'),module.__dict__)
 result=module.main(['--execute-reviewed-provider-smoke','--confirm',CONFIRM],M=M,state=s,contract=module.document(contractraw))
except BaseException as e:primary=e
if M is not None and s is not None:
 try:M.finish(s)
 except BaseException as e:final=e
if primary is not None or final is not None:
 error=final if final is not None else primary
 out={'schema':'current_coreutils_provider_bootstrap_failure/1','status':'REFUSED_OR_FAILED','primary':None if primary is None else closed(primary),'finalizer':None if final is None else closed(final),'source_io_error_count':0 if s is None else len(s.errors),'source_io_errors':[]if s is None else[closed(e)for e in s.errors[:128]],'error_list_overflow':s is not None and len(s.errors)>128,'physical_finalizers_complete':s is not None and all(r.closed for r in s.rows),'security_accepted':False}
 if primary is not None and hasattr(primary,'provider_child_evidence'):out['child']=primary.provider_child_evidence
 if primary is not None and hasattr(primary,'provider_fixture_state'):
  owned=primary.provider_fixture_state;out['fixture_writer']={'original_prefix_count':len(owned.rows),'all_physically_closed':all(r.closed for r in owned.rows),'closed_errors':[closed(e)for e in owned.errors[:128]],'error_count':len(owned.errors)}
  out['physical_finalizers_complete']=out['physical_finalizers_complete']and all(r.closed for r in owned.rows)
 if primary is not None and hasattr(primary,'provider_object_prefixes'):
  out['fixture_objects']=[{'original_FD_acquired':r.original_fd is not None,'admitted':r.admitted,'physically_closed':r.closed,'initial_errors':[closed(e)for e in r.errors]}for r in primary.provider_object_prefixes]
  out['physical_finalizers_complete']=out['physical_finalizers_complete']and all(r.closed for r in primary.provider_object_prefixes)
 message=M.encode(out,maximum=2*1024**2)if M is not None and callable(getattr(M,'encode',None))else(json.dumps(out,sort_keys=True,separators=(',',':')).encode()+b'\n')
 sys.stdout.buffer.write(message);sys.stdout.buffer.flush();raise SystemExit(1)
need(result is not None and s is not None and not s.errors and all(r.closed for r in s.rows),'provider_outer_finality')
result['source_provider_finality_pending_bootstrap']=False;result['source_provider_original_finalizers_complete']=True
result['original_input_and_writer_count']=len(s.rows);result['source_copies']=copies
sys.stdout.buffer.write(module.encoded(result));sys.stdout.buffer.flush()
'''.replace('CONFIRM',repr(CONFIRM))


def bootstrap_arguments(raws):
    pins={k:[len(raws[n]),sha256(raws[n]).hexdigest()]for k,n in
          (('io','configure_recovery.py'),('capsule','capsule.py'),('contract','contract.json'))}
    args=[encoded(pins).decode().strip()]+[base64.b64encode(raws[n]).decode()for n in('configure_recovery.py','capsule.py','contract.json')]
    need(all(len(a.encode())<=116*1024 for a in args),'provider_argument_bound')
    return ['-I','-S','-B','-c',BOOTSTRAP,*args]


def create_arguments(old,name,image,raws):
    need(image==IMAGE and re.fullmatch('app-ci-provider-[1-9][0-9]*-[1-9][0-9]*',name),'provider_create')
    args=['create','--name',name,'--label',old.LABEL+'='+old.owner(),'--pull=never','--network=none','--read-only',
          '--cap-drop=ALL','--security-opt=no-new-privileges','--user=10001:10001','--memory=512m','--memory-swap=512m',
          '--cpus=1','--pids-limit=64','--ulimit','core=0:0','--log-driver=none','--restart=no','--workdir=/proof']
    for path,options in TMPFS.items():args+=['--tmpfs',path+':'+options]
    return args+['--entrypoint=/usr/local/bin/python3',image,*bootstrap_arguments(raws)]


INSPECT='{"id":{{json .Id}},"name":{{json .Name}},"image":{{json .Image}},"reference":{{json .Config.Image}},"owner":{{json (index .Config.Labels "io.gold-trade.app-image-ci-smoke-owner")}},"entrypoint":{{json .Config.Entrypoint}},"cmd":{{json .Config.Cmd}},"user":{{json .Config.User}},"workdir":{{json .Config.WorkingDir}},"readonly":{{json .HostConfig.ReadonlyRootfs}},"network":{{json .HostConfig.NetworkMode}},"tmpfs":{{json .HostConfig.Tmpfs}},"binds":{{json .HostConfig.Binds}},"mounts":{{json .Mounts}},"caps_add":{{json .HostConfig.CapAdd}},"caps_drop":{{json .HostConfig.CapDrop}},"security":{{json .HostConfig.SecurityOpt}},"privileged":{{json .HostConfig.Privileged}},"memory":{{json .HostConfig.Memory}},"memory_swap":{{json .HostConfig.MemorySwap}},"cpus":{{json .HostConfig.NanoCpus}},"pids":{{json .HostConfig.PidsLimit}},"restart":{{json .HostConfig.RestartPolicy.Name}},"auto_remove":{{json .HostConfig.AutoRemove}},"running":{{json .State.Running}},"paused":{{json .State.Paused}},"restarting":{{json .State.Restarting}},"oom":{{json .State.OOMKilled}},"status":{{json .State.Status}},"exit":{{json .State.ExitCode}},"pid":{{json .State.Pid}}}'


def validate_container(v,old,cid,name,raws,*,finished):
    need(type(v)is dict and v.get('id')==cid and v.get('name')=='/'+name and v.get('image')==v.get('reference')==IMAGE
         and v.get('owner')==old.owner() and v.get('entrypoint')==['/usr/local/bin/python3']
         and v.get('cmd')==bootstrap_arguments(raws),'provider_container')
    for k,val in {'user':'10001:10001','workdir':'/proof','readonly':True,'network':'none','tmpfs':TMPFS,'binds':None,
                  'caps_add':None,'caps_drop':['ALL'],'security':['no-new-privileges'],'privileged':False,
                  'memory':536870912,'memory_swap':536870912,'cpus':1000000000,'pids':64,'restart':'no','auto_remove':False,
                  'running':False,'paused':False,'restarting':False,'oom':False,'pid':0}.items():
        need(type(v.get(k))is type(val) and v[k]==val,'provider_container')
    need(v.get('status')==('exited'if finished else'created') and type(v.get('exit'))is int and v['exit']==0,'provider_container')
    mounts=v.get('mounts');need(type(mounts)is list and len(mounts)in(0,2),'provider_container')
    need(all(type(r)is dict and r.get('Type')=='tmpfs' and r.get('Destination')in TMPFS and r.get('RW')is True and r.get('Source','')==''for r in mounts),'provider_container')
    need(len({r['Destination']for r in mounts})==len(mounts),'provider_container')
    return {'id':cid,'image':IMAGE,'user':'10001:10001','status':v['status'],'exit':v['exit'],
            'public_command_sha256':sha256(encoded(v['cmd'])).hexdigest(),'network':'none','readonly':True,'tmpfs':TMPFS,'mounts':mounts}


def validate_result(result,raws):
    need(type(result)is dict and result.get('schema')=='current_coreutils_provider_owned_smoke/1'and result.get('image_id')==IMAGE
         and type(result.get('real_uid'))is int and result['real_uid']==10001 and type(result.get('fixture_cases_passed'))is int
         and result['fixture_cases_passed']==4 and result.get('different_tmpfs_devices_proven')is True
         and type(result.get('source_device'))is int and type(result.get('destination_device'))is int
         and result['source_device']!=result['destination_device']
         and result.get('source_provider_original_finalizers_complete')is True
         and result.get('source_provider_finality_pending_bootstrap')is False
         and result.get('candidate_CI_compatibility_only')is True and result.get('production_activation_performed')is False
         and result.get('security_accepted')is False and result.get('provider_accepted')is False,'provider_result')
    contract=json.loads(raws['contract.json']);bodies={r['path']:r for r in contract['bodies']}
    need(result.get('real_Coreutils_loader_bindings_observed')is True,'provider_result')
    need(result.get('ordinary_lazy_execution_timing_proven')is False
         and result.get('loader_binding_scope')=='CI-only eager relocations using LD_BIND_NOW=1; not function-call events or ordinary lazy timing','provider_result')
    expected_prefixes=6+len(bodies)+sum(len(r['symlink_aliases'])for r in bodies.values())+len(contract['directory_aliases'])
    need(type(result.get('original_input_and_writer_count'))is int and result['original_input_and_writer_count']==expected_prefixes,'provider_result')
    readers=result.get('original_body_readers')
    need(type(readers)is list and len(readers)==9 and {r['path']for r in readers}==set(bodies),'provider_result')
    for r in readers:
        b=bodies[r['path']];f=r.get('original_full9');flags=r.get('original_reader_flags')
        need(r.get('bytes')==b['bytes']and r.get('sha256')==b['sha256']and r.get('held_through_all_four_CLI_cases')is True
             and type(f)is list and len(f)==9 and all(type(v)is int for v in f)and f[2:7]==[0o100000|b['mode'],0,0,1,b['bytes']]
             and type(flags)is list and len(flags)==2 and all(type(v)is int for v in flags)and flags[0]&3==0 and flags[1]==1,'provider_result')
    interpreters=result.get('program_interpreters')
    need(type(interpreters)is list and len(interpreters)==3 and {r['program']for r in interpreters}=={'/usr/bin/cp','/usr/bin/install','/usr/bin/mv'}
         and all(r.get('resolved_interpreter')=='/usr/lib/x86_64-linux-gnu/ld-linux-x86-64.so.2'for r in interpreters),'provider_result')
    need(result.get('fixture_object_original_finalizers_complete')is True and type(result.get('fixture_writers'))is list
         and len(result['fixture_writers'])==4 and all(type(r)is dict and r.get('original_writer_reader_finalizers_complete')is True for r in result['fixture_writers']),'provider_result')
    cases=result.get('cases');expected={'cp_tree':'/usr/bin/cp','install_mode':'/usr/bin/install','mv_EXDEV_tree':'/usr/bin/mv','cp_parents':'/usr/bin/cp'}
    need(type(cases)is list and len(cases)==4 and {r['case']for r in cases}==set(expected),'provider_result')
    for r in cases:
        need(type(r)is dict and type(r.get('argv'))is list and r['argv']and r['argv'][0]==expected[r['case']]
             and type(r.get('actual_returncode'))is int and r['actual_returncode']==0
             and r.get('physical_child_and_pipes_finalized')is True and type(r.get('loader'))is dict
             and r['loader'].get('program_initialized')==expected[r['case']],'provider_result')
        need(r.get('loader_environment')=={'PATH':'/usr/bin:/bin','LC_ALL':'C','LD_DEBUG':'libs,versions,bindings','LD_BIND_NOW':'1'},'provider_result')
        loader=r['loader'];trace=loader.get('complete_trace_ascii');bindings=loader.get('bindings');versions=loader.get('version_requirements')
        need(type(trace)is str and trace.isascii() and 0<len(trace.encode())<=256*1024
             and loader.get('trace_bytes')==len(trace.encode())and loader.get('trace_sha256')==sha256(trace.encode()).hexdigest()
             and type(bindings)is list and 0<len(bindings)<=2048 and type(versions)is list and 0<len(versions)<=2048,'provider_result')
        acl='/usr/lib/x86_64-linux-gnu/libacl.so.1.2.2400';attr='/usr/lib/x86_64-linux-gnu/libattr.so.1.1.2502'
        direct={acl,attr,'/usr/lib/x86_64-linux-gnu/libc.so.6','/usr/lib/x86_64-linux-gnu/libselinux.so.1'}
        need(all(type(b)is dict and b.get('from')==expected[r['case']]and b.get('to')in bodies for b in bindings)
             and direct<={b['to']for b in bindings}
             and {('acl_set_file_at','ACL_1.3',acl),('acl_delete_def_file_at','ACL_1.3',acl),('attr_copy_fd','ATTR_1.1',attr)}
                 <={(b.get('symbol'),b.get('version'),b['to'])for b in bindings}
             and {(acl,'ACL_1.3'),(attr,'ATTR_1.1')}<={(v.get('provider'),v.get('version'))for v in versions},'provider_result')
    copies=result.get('source_copies');need(type(copies)is dict and set(copies)=={'configure_recovery.py','capsule.py','contract.json'},'provider_result')
    for name,row in copies.items():
        need(type(row)is dict and row.get('bytes')==len(raws[name])and row.get('sha256')==sha256(raws[name]).hexdigest()
             and row.get('mode')=='0o644'and type(row.get('original_full9'))is list and len(row['original_full9'])==9
             and all(type(v)is int for v in row['original_full9'])and row['original_full9'][2:7]==[0o100644,10001,10001,1,len(raws[name])],'provider_result')
    return result


def make_context(old,root,stack,load,guard,keeper):
    # Same old ci admission, helper identities, transport and daemon APIs. The
    # outer cohort already holds stronger original FD/full9/flags source loans;
    # no historical profile, entrypoint, admission predicate or module is edited.
    old.ci(root);ctx=old.Controller.__new__(old.Controller)
    ctx.root=root;ctx.stack=stack;ctx.checks=[guard,keeper.check];ctx.created=[];ctx.network=None
    ctx.end=time.monotonic()+1800;ctx.cleanup_end=None;ctx.modules={name:load(name)for name in old.HELPERS}
    for attr,name in(('C','compose_security_os_candidate.py'),('P','compose_app_mountpoint_image.py'),
                     ('M','compose_market_security_os_candidate.py'),('L','admit_security_component_image_reuse.py'),
                     ('D','datastore_candidate_contract.py'),('T','runtime_image_archive_transport.py')):
        setattr(ctx,attr,ctx.modules[name])
    ctx.C.PROOF_KEEPER=keeper;ctx.P.PRIOR,ctx.P.LEGACY=ctx.M,ctx.L
    return ctx


def run(root,*,keeper,guard,load,raw):
    old=load('run_app_image_ci_smoke.py');G=load('gold2_admission.py');P=load('producer02_controller.py')
    builder=load('prepare_canonical_app_image.py');H=load('build_hosted.py')
    intake=G.checked_document(raw('intake.json'),G.INTAKE_PIN)
    oldmanifest=G.checked_document(raw('source-manifest.json'),G.MANIFEST_PIN);G.validate_inputs(intake,oldmanifest)
    control=intake['control'];ctx=None
    def allowed():return G.allowed_codes(old,ctx)|P.CODES|PUBLIC_CODES
    sources=G.ObservationSources(lambda e:closed(e,allowed()));ctx=make_context(old,root,sources,load,guard,keeper)
    primary=None;cleanup=[];final=[];result=None
    def cli(argv,maximum=65536,seconds=60):
        ctx.check()
        return H.run([*old.DOCKER,*argv],env={'PATH':'/usr/bin:/bin','LC_ALL':'C.UTF-8','DOCKER_CONFIG':str(root/'docker-config')},
                     deadline=min(ctx.end,time.monotonic()+seconds),maximum=maximum,guard=ctx.check)
    raws={n:raw(n)for n in('configure_recovery.py','capsule.py','contract.json')}
    try:
        folder=root/'evidence';folder.mkdir(mode=0o700);path=folder/'public-evidence.zip'
        ctx.download(control['evidence_artifact_id'],path,control['evidence_zip_bytes'],control['evidence_zip_sha256'])
        with ctx.C.pinned(path,control['evidence_zip_bytes'])as(stream,size):
            G.need(size==control['evidence_zip_bytes'] and ctx.C.stream_digest(stream,size)==control['evidence_zip_sha256'],'evidence_identity')
            stream.seek(0);files=P.read_zip(stream,G.EVIDENCE_NAMES)
        evidence=G.admit_evidence(files,intake,builder)
        image=P.admit_app(ctx,old,control,evidence);need(image==IMAGE,'provider_container')
        name='app-ci-provider-'+old.owner();ctx.created.append((name,image)) # intent before CREATE
        cid=cli(create_arguments(old,name,image,raws),maximum=128).decode().strip();need(re.fullmatch('[a-f0-9]{64}',cid),'provider_create')
        before=validate_container(G.document(cli(['container','inspect','--format',INSPECT,cid],maximum=1024**2)),old,cid,name,raws,finished=False)
        ctx.write(root/'reports/provider-container-before.json',before)
        output=cli(['start','--attach',cid],maximum=MAX_RESULT,seconds=120)
        result=G.document(output)
        validate_result(result,raws)
        after=validate_container(G.document(cli(['container','inspect','--format',INSPECT,cid],maximum=1024**2)),old,cid,name,raws,finished=True)
        ctx.write(root/'reports/provider-container-after.json',after)
        ctx.C.write(root/'reports/provider-owned-result.json',output)
    except BaseException as e:
        primary=e
        observed=getattr(e,'public_command_failure',None)
        if type(observed)is dict:
            try:
                captured=observed['captured_output'];need(type(captured)is bytes and len(captured)<=MAX_RESULT,'provider_result')
                if captured:ctx.C.write(root/'reports/provider-command-output.log',captured)
                ctx.write(root/'reports/provider-command-failure.json',{'schema':'provider_partial_command/1','actual_returncode':observed['actual_returncode'],
                'observed_output_bytes':observed['observed_output_bytes'],'actual_pipe_closed':observed['actual_pipe_closed'],
                'stdout_eof_observed':observed['stdout_eof_observed'],'prefix_ascii':observed['prefix'].decode('ascii',errors='replace'),
                'tail_ascii':observed['tail'].decode('ascii',errors='replace'),'captured_output_bytes':len(captured),'captured_output_sha256':sha256(captured).hexdigest(),
                'maximum_output_bytes':observed['maximum_output_bytes'],'security_accepted':False})
            except BaseException as e:final.append(e)
    try:cleanup=old.cleanup(ctx)
    except BaseException as e:cleanup=['provider_cleanup'];final.append(e)
    try:ctx.write(root/'reports/provider-cleanup.json',{'owned_cleanup_complete':not cleanup,'failures':cleanup,'security_accepted':False})
    except BaseException as e:final.append(e)
    sources.close();final.extend(sources.failures)
    try:guard();keeper.check()
    except BaseException as e:final.append(e)
    if primary is not None or cleanup or final:
        record={'status':'REFUSED_OR_FAILED','primary':None if primary is None else closed(primary,allowed()),'cleanup':cleanup,
                'finalizers':[closed(e,allowed())if isinstance(e,BaseException)else e for e in final],'security_accepted':False}
        error=RuntimeError('provider_source_finality'if final else'provider_cleanup'if cleanup else'provider_result')
        error.provider_failure=record;raise error from primary
    return {'schema':'provider_hosted_inner_completed/1','image_id':IMAGE,'cases_passed':4,'owned_cleanup_complete':True,
            'inner_sources_finalized':True,'outer_finality_pending':True,'security_accepted':False}


def main(argv=None,*,outer_keeper=None,outer_check=None,load=None,raw=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',type=Path);p.add_argument('--execute-reviewed-provider-smoke',action='store_true');p.add_argument('--confirm')
    args=p.parse_args(argv)
    if not args.execute_reviewed_provider_smoke:return {'status':'UNARMED','hosted_operation_performed':False,'security_accepted':False}
    need(args.confirm==CONFIRM and outer_keeper is not None and outer_check is not None and load is not None and raw is not None,'provider_entry')
    return run(args.root,keeper=outer_keeper,guard=outer_check,load=load,raw=raw)


if __name__=='__main__':
    try:print(encoded(main()).decode(),end='')
    except BaseException as e:print(json.dumps({'status':'REFUSED_OR_FAILED','failure':closed(e),'security_accepted':False}));raise SystemExit(1)from None
