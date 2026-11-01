"""Finite public backend telemetry for this isolated hosted CI only.

An observed or unavailable diagnostic never grants health/test acceptance.
No lifecycle, logs, complete Config/Env, credentials or recovery commands.
"""
import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import time
from types import ModuleType

ROOT=Path(__file__).resolve().parents[2]
NAMES=('trading_bot_app','trading_bot_db','trading_bot_redis')
PINS={
 'scripts/npm_security_ci/browser_evidence.py':(10369,'453153185f1bed7bbf88baea8dc6959c32042eb175e9c245c6df9138b0545327'),
 'deploy/maintenance/glibc_backport_native239/host_io_original.py':(53351,'fc9fc8e4ae7fe1af75fd0b4c63fedb0ebfa5820148bb311c5eaafcaaa5bbbb82'),
}
INSPECT='{"Name":{{json .Name}},"Id":{{json .Id}},"Running":{{json .State.Running}},"Pid":{{json .State.Pid}},"ExitCode":{{json .State.ExitCode}},"OOMKilled":{{json .State.OOMKilled}},"StartedAt":{{json .State.StartedAt}},"FinishedAt":{{json .State.FinishedAt}},"RestartCount":{{json .RestartCount}},"Memory":{{json .HostConfig.Memory}},"NanoCpus":{{json .HostConfig.NanoCpus}},"Health":{{with index .State "Health"}}{{json .Status}}{{else}}"none"{{end}}}'
STATS='{"Name":{{json .Name}},"ID":{{json .ID}},"CPUPerc":{{json .CPUPerc}},"MemUsage":{{json .MemUsage}},"MemPerc":{{json .MemPerc}},"PIDs":{{json .PIDs}}}'
def need(value,code):
    if not value:raise ValueError(code)
def frame(s):return(s.st_dev,s.st_ino,s.st_mode,s.st_uid,s.st_gid,s.st_nlink,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
def load(relative):
    path=ROOT/relative;pin=PINS[relative]
    need(path.resolve(strict=True)==path,'source_path')
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC)
    try:
        before=os.fstat(fd)
        need(stat.S_ISREG(before.st_mode) and before.st_nlink==1 and before.st_uid==os.geteuid() and not before.st_mode&0o022 and before.st_size==pin[0]<=65536,'source_metadata')
        raw=os.pread(fd,pin[0]+1,0)
        need((len(raw),sha256(raw).hexdigest())==pin and frame(before)==frame(os.fstat(fd))==frame(path.lstat()),'source_pin')
    finally:os.close(fd)
    module=ModuleType('_finite_backend_'+path.stem);module.__file__=str(path)
    exec(compile(raw,str(path),'exec'),module.__dict__);return module
def hosted(env):
    return os.getuid()==os.geteuid()==1001 and all(env.get(k)==v for k,v in {
      'GITHUB_ACTIONS':'true','RUNNER_ENVIRONMENT':'github-hosted','RUNNER_OS':'Linux',
      'GITHUB_REPOSITORY':'mohsenbarari/trading_bot','GITHUB_REPOSITORY_ID':'1065680631',
      'GITHUB_EVENT_NAME':'workflow_dispatch','GITHUB_WORKFLOW':'Isolated npm security backport qualification',
      'GITHUB_WORKSPACE':str(ROOT)}.items())
def subject(args,env):
    need(re.fullmatch('[a-f0-9]{40}',args.head) and args.browser in {'chromium','firefox','webkit'} and type(args.shard)is int and 1<=args.shard<=4,'subject')
    run,attempt=env.get('GITHUB_RUN_ID',''),env.get('GITHUB_RUN_ATTEMPT','')
    need(re.fullmatch('[1-9][0-9]{0,19}',run) and re.fullmatch('[1-9][0-9]{0,2}',attempt),'run_subject')
    need(args.outcome in ({'pending'}if args.phase=='before'else{'success','failure','cancelled','skipped'}),'gate_outcome')
    return {'head':args.head,'run_id':int(run),'run_attempt':int(attempt),'browser':args.browser,'shard':args.shard,'phase':args.phase,'original_gate_outcome':args.outcome}
def inspect_row(value,name):
    need(type(value)is dict and set(value)=={'Name','Id','Running','Pid','ExitCode','OOMKilled','StartedAt','FinishedAt','RestartCount','Memory','NanoCpus','Health'},'inspect_shape')
    need(value['Name']=='/'+name and re.fullmatch('[a-f0-9]{64}',value['Id']) and all(type(value[k])is bool for k in ('Running','OOMKilled')),'inspect_subject')
    need(all(type(value[k])is int and 0<=value[k]<=2**63-1 for k in ('Pid','ExitCode','RestartCount','Memory','NanoCpus')),'inspect_integer')
    need(all(type(value[k])is str and re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?Z',value[k]) for k in ('StartedAt','FinishedAt')) and value['Health']in {'none','starting','healthy','unhealthy'},'inspect_status')
    return value
def stats_rows(raw,document,rows):
    lines=raw.splitlines();need(len(lines)==3,'stats_count');result={}
    for line in lines:
        value=document(line);need(type(value)is dict and set(value)=={'Name','ID','CPUPerc','MemUsage','MemPerc','PIDs'},'stats_shape')
        name=value['Name'];need(type(name)is str and name in NAMES and name not in result and name in rows and value['ID']==rows[name]['Id'],'stats_subject')
        need(all(type(value[k])is str and re.fullmatch(r'[0-9]+(?:\.[0-9]+)?%',value[k]) and len(value[k])<=32 for k in ('CPUPerc','MemPerc')),'stats_percentage')
        need(type(value['PIDs'])is str and re.fullmatch('[0-9]{1,12}',value['PIDs']) and type(value['MemUsage'])is str and len(value['MemUsage'])<=80 and re.fullmatch(r'[0-9]+(?:\.[0-9]+)?[kKMGTPE]?i?B / [0-9]+(?:\.[0-9]+)?[kKMGTPE]?i?B',value['MemUsage']),'stats_usage')
        result[name]=value
    return result
def collect(run,document,env):
    rows={};errors=[];end=time.monotonic()+20
    codes={'command':'bounded_command_unavailable','json':'bounded_json_invalid','validation':'bounded_metadata_invalid'}
    def command(argv,maximum):
        raw=run(['/usr/bin/docker','--host=unix:///var/run/docker.sock',*argv],env=env,deadline=end,maximum=maximum)
        need(type(raw)is bytes and len(raw)<=maximum,'command_bound');return raw
    for name in NAMES:
        stage='command'
        try:
            raw=command(['container','inspect','--format',INSPECT,name],8192)
            stage='json';value=document(raw)
            stage='validation';rows[name]=inspect_row(value,name)
        except Exception:errors.append({'operation':'inspect','container':name,'stage':stage,'code':codes[stage]})
    stats={}
    stage='command'
    def stats_document(raw):
        nonlocal stage
        stage='json';value=document(raw);stage='validation';return value
    try:
        raw=command(['stats','--no-stream','--no-trunc','--format',STATS,*NAMES],16384)
        stage='validation';stats=stats_rows(raw,stats_document,rows)
    except Exception:errors.append({'operation':'stats','stage':stage,'code':codes[stage]})
    return {'status':'OBSERVED'if not errors else'PARTIAL_OR_UNAVAILABLE','states':rows,'stats':stats,'errors':errors}
def main(argv=None):
    if not hosted(os.environ):
        print('{"status":"UNARMED","Docker_called":false,"security_accepted":false}');return 0
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('phase',choices=('before','after'))
    parser.add_argument('--browser',required=True);parser.add_argument('--shard',type=int,required=True);parser.add_argument('--head',required=True);parser.add_argument('--outcome',default='pending')
    try:
        args=parser.parse_args(argv);bound=subject(args,os.environ)
        temporary=Path(os.environ['RUNNER_TEMP']);need(temporary.is_absolute() and temporary.resolve(strict=True)==temporary and temporary.is_dir(),'temporary_directory')
        output=temporary/'npm-browser-backend-diagnostics';output.mkdir(mode=0o700,exist_ok=True)
        need(output.resolve(strict=True)==output and output.stat().st_uid==os.geteuid() and not output.stat().st_mode&0o077,'output_directory')
        E=load('scripts/npm_security_ci/browser_evidence.py');M=load('deploy/maintenance/glibc_backport_native239/host_io_original.py')
        def document(raw):return json.loads(raw,object_pairs_hook=E.unique,parse_constant=lambda _:need(False,'nonfinite_JSON'))
        with tempfile.TemporaryDirectory(prefix='empty-docker-client-',dir=output)as client:
            result=collect(M.run,document,{'PATH':'/usr/bin:/bin','HOME':client,'DOCKER_CONFIG':client,'DOCKER_HOST':'unix:///var/run/docker.sock','LANG':'C.UTF-8','LC_ALL':'C.UTF-8'})
        result.update(schema='npm_browser_public_backend_diagnostics/1',subject=bound,health_assessment=None,diagnostic_is_test_or_security_acceptance=False,security_accepted=False)
        E.write_new(output/f'{args.browser}-{args.shard}-{args.phase}.json',result)
        print(json.dumps({'status':result['status'],'diagnostic_artifact_written':True,'security_accepted':False}));return 0
    except Exception:
        # Diagnostic availability must not suppress or erase the original test
        # gate. Never expose raw Docker output or private exception payloads.
        print('{"status":"DIAGNOSTIC_UNAVAILABLE","diagnostic_artifact_written":false,"test_gate_unchanged":true,"security_accepted":false}');return 0
if __name__=='__main__':raise SystemExit(main())
