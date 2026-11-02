"""One bounded, disposable, same-image observation for the normal Market build.

Default inert. No production target, mount, environment or activation API.
The normal driver retains/authenticates every Source and runtime-vector input.
"""
from hashlib import sha256
from pathlib import Path,PurePosixPath
from types import ModuleType
import errno,json,os,re,stat,sys,tempfile,time,uuid

MAX_BUNDLE=60000
MAX_SOURCE=60000
MAX_FILE=128*1024**2
MAX_FILES=8192
MAX_TOTAL=512*1024**2
LABEL='io.gold-trade.normal-runtime-observation'

def need(value,code):
    if not value:raise RuntimeError(code)

def encoded(value):return(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False)+'\n').encode()

def document(raw):
    def unique(rows):
        result={}
        for key,value in rows:need(key not in result,'runtime_duplicate_json');result[key]=value
        return result
    return json.loads(raw,object_pairs_hook=unique,parse_constant=lambda _:need(False,'runtime_nonfinite_json'))

def source(raw,name,pin):
    need(type(raw)is str and 0<len(raw.encode())<=32768 and sha256(raw.encode()).hexdigest()==pin,'runtime_verifier_source')
    module=ModuleType(name);module.__file__='<sealed-'+name+'>';exec(compile(raw,module.__file__,'exec'),module.__dict__);return module

def request(value):
    need(type(value)is dict and set(value)=={'image_id','source_commit','source_tree','input_signature','run_id','run_attempt','vector_sha256','paths','packages','component_source','component_sha256','acl_source','acl_sha256'},'runtime_request')
    need(re.fullmatch('sha256:[a-f0-9]{64}',value['image_id'])and all(type(value[name])is str and re.fullmatch('[a-f0-9]{40}',value[name])for name in('source_commit','source_tree')),'runtime_identity')
    need(all(type(value[name])is str and re.fullmatch('[a-f0-9]{64}',value[name])for name in('input_signature','vector_sha256','component_sha256','acl_sha256')),'runtime_identity')
    need(type(value['run_id'])is int and value['run_id']>0 and type(value['run_attempt'])is int and 1<=value['run_attempt']<=1000,'runtime_identity')
    names=value['paths'];need(type(names)is list and 0<len(names)<=MAX_FILES and len(set(names))==len(names),'runtime_paths')
    need(all(type(name)is str and len(name)<=4096 and name.startswith(('/usr/','/lib/','/lib64/','/bin/','/sbin/'))and str(PurePosixPath(name))==name and '..'not in PurePosixPath(name).parts and all(ord(c)>=32 for c in name)for name in names),'runtime_paths')
    packages=value['packages'];need(type(packages)is list and 0<len(packages)<=128 and len(set(packages))==len(packages)and all(type(name)is str and re.fullmatch('[a-z0-9][a-z0-9+.-]{0,127}',name)for name in packages),'runtime_packages')
    need(len(encoded(value))<=MAX_BUNDLE,'runtime_bundle_bound');return value

def prepare_request(build,vector_raw,sqlite_lock_raw,component_raw,acl_raw):
    """Reuse authenticated expected bytes, never old App runtime observations."""
    need(type(vector_raw)is bytes and(len(vector_raw),sha256(vector_raw).hexdigest())==(99169,'6ffcd1dc523a8b859179b8236ad00be884d7a8087982c9ac300e48cde8ca6e14'),'runtime_original_vector')
    original=document(vector_raw);need(original['schema']=='image_security_evidence_proof/1'and original['kind']=='runtime_payload'
        and original['scope']['image_id']=='sha256:2389c900dbbf520bd7d7f723e8c48a5c258f1612c43e80d2b66c6d046f9f8434'
        and original['observations']['inventory_complete']is True,'runtime_original_vector')
    expected=[]
    for package in original['observations']['packages']:
        need(package['actual_files']==package['expected_files'],'runtime_original_vector')
        if package['package']not in{'libsystemd0','libudev1','perl-base'}:
            expected.append({key:value for key,value in package.items()if key!='actual_files'})
    sqlite=document(sqlite_lock_raw);package=sqlite['package'];library=sqlite['installed_library']
    need(sqlite.get('armed')is True and sqlite.get('schema')=='sqlite_wal_image_package_lock/1'and package['name']=='libsqlite3-0','runtime_sqlite_lock')
    current=next(item for item in expected if item['package']=='libsqlite3-0');old=current['expected_files'][0]
    version=package['version'];parts=version.split('-',1);need(len(parts)==2,'runtime_sqlite_lock')
    current.update(installed_version=version,source_version=parts[0],source_release=parts[1],deb_sha256=sqlite['outputs'][package['path']]['sha256'],
        expected_files=[{'path':library['path'],'bytes':library['bytes'],'sha256':library['sha256'],**{key:old[key]for key in('mode','uid','gid')}}])
    need(len(expected)==20,'runtime_expected_packages')
    paths=[file['path']for package in expected for file in package['expected_files']]
    receipt=build['source_inputs']
    bundle={'image_id':build['image_id'],'source_commit':receipt['release_commit'],'source_tree':receipt['release_tree'],
        'input_signature':build['two_git_input_signature'],'run_id':build['run_id'],'run_attempt':build['run_attempt'],
        'vector_sha256':sha256(vector_raw).hexdigest(),'paths':sorted(paths),'packages':sorted(item['package']for item in expected),
        'component_source':component_raw.decode(),'component_sha256':sha256(component_raw).hexdigest(),'acl_source':acl_raw.decode(),'acl_sha256':sha256(acl_raw).hexdigest()}
    return expected,request(bundle)

def payload_observations(expected,actual):
    """Only current measured rows can become the runtime_payload observation."""
    need(actual.get('declared_runtime_files_complete')is True and actual.get('all_declared_file_readers_closed')is True
        and actual.get('owned_container_cleanup_proven')is True,'runtime_observation_incomplete')
    files=actual.get('files');need(type(files)is list and len(files)<=MAX_FILES and len({row['path']for row in files})==len(files),'runtime_actual_files')
    by_path={row['path']:row for row in files};need(set(by_path)=={row['path']for item in expected for row in item['expected_files']},'runtime_actual_files')
    result=[]
    for package in expected:
        record=actual['packages'].get(package['package']);need(type(record)is dict and record.get('installed_version')==package['installed_version'],'runtime_installed_version')
        need(type(record.get('source'))is str and record['source'].split(' ',1)[0]==package['source_package']
            and record.get('architecture')in('amd64','all'),'runtime_installed_source')
        observed=[by_path[row['path']]for row in package['expected_files']];need(observed==package['expected_files'],'runtime_payload_mismatch')
        result.append({**package,'actual_files':observed})
    return{'inventory_complete':True,'packages':result}

def ordinary_caller_smoke(component,guard):
    """Real ABI/use smoke only in this container's empty bounded tmpfs."""
    completed=[]
    def run(argv):
        guard();raw=component.bounded_command(argv,maximum=16384,timeout=10);guard();completed.append(argv[0]);return raw
    with tempfile.TemporaryDirectory(prefix='normal-public-caller-',dir='/tmp')as temporary:
        base=Path(temporary);original=base/'public';original.write_bytes(b'PUBLIC FIXTURE\n');original.chmod(0o640)
        copied=base/'copied';run(['/usr/bin/cp','--preserve=mode',str(original),str(copied)])
        need(copied.read_bytes()==b'PUBLIC FIXTURE\n'and stat.S_IMODE(copied.stat().st_mode)==0o640,'runtime_cp_smoke')
        installed=base/'installed';run(['/usr/bin/install','-m','0640',str(original),str(installed)])
        need(installed.read_bytes()==b'PUBLIC FIXTURE\n'and stat.S_IMODE(installed.stat().st_mode)==0o640,'runtime_install_smoke')
        packed=base/'public.tar';run(['/usr/bin/tar','-cf',str(packed),'-C',str(base),'public'])
        need(run(['/usr/bin/tar','-tf',str(packed)])==b'public\n','runtime_tar_smoke')
        unpacked=base/'unpacked';unpacked.mkdir();run(['/usr/bin/tar','-xf',str(packed),'-C',str(unpacked)])
        need((unpacked/'public').read_bytes()==b'PUBLIC FIXTURE\n','runtime_tar_smoke')
        need(run(['/usr/bin/sed','s/PUBLIC/OWNED/',str(original)])==b'OWNED FIXTURE\n','runtime_sed_smoke')
        for tool in('/usr/bin/ls','/usr/bin/dir','/usr/bin/vdir'):run([tool,'-d',str(original)])
        for tool in('/usr/sbin/useradd','/usr/sbin/usermod'):run([tool,'--help'])
    guard();return{'ordinary_caller_smoke_passed':True,'successful_command_invocations':len(completed),'public_owned_fixtures_only':True,'fixtures_removed':True}

def observe(value):
    """Runs only inside the new immutable-image container; no inspected code imports."""
    value=request(value);component=source(value['component_source'],'component',value['component_sha256']);acl=source(value['acl_source'],'acl',value['acl_sha256'])
    started=time.monotonic();deadline=started+150
    def guard():need(time.monotonic()<deadline,'runtime_observation_deadline')
    presence=component.probe(value['image_id'],value['source_commit'],value['component_sha256']);guard()
    caller=acl.probe(value['image_id'],value['source_commit'],value['acl_sha256']);guard()
    status=component.read_regular(Path('/var/lib/dpkg/status'),4*1024**2);installed={}
    for paragraph in status.decode().split('\n\n'):
        fields={}
        for line in paragraph.splitlines():
            if line and not line[0].isspace()and ': 'in line:
                name,item=line.split(': ',1);need(name not in fields,'runtime_package_duplicate');fields[name]=item
        if fields.get('Status')=='install ok installed'and fields.get('Package')in value['packages']:
            name=fields['Package'];need(name not in installed and re.fullmatch('[A-Za-z0-9.+:~_-]{1,128}',fields.get('Version','')),'runtime_package_record')
            installed[name]={'installed_version':fields['Version'],'source':fields.get('Source',name),'architecture':fields.get('Architecture')}
    need(set(installed)==set(value['packages']),'runtime_package_missing')
    files=[];total=0
    for name in value['paths']:
        guard();fd=os.open(name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC)
        try:
            before=acl.identity(os.fstat(fd));info=os.fstat(fd);need(stat.S_ISREG(info.st_mode)and 0<=info.st_size<=MAX_FILE,'runtime_file_metadata')
            total+=info.st_size;need(total<=MAX_TOTAL,'runtime_total_bound');digest=sha256();count=0
            while count<info.st_size:
                guard();raw=os.read(fd,min(65536,info.st_size-count));need(raw,'runtime_file_short');count+=len(raw);digest.update(raw)
            need(os.read(fd,1)==b''and before==acl.identity(os.fstat(fd))==acl.identity(Path(name).lstat()),'runtime_file_changed')
            files.append({'path':name,'bytes':count,'sha256':digest.hexdigest(),'mode':info.st_mode,'uid':info.st_uid,'gid':info.st_gid})
        finally:os.close(fd)
        try:os.fstat(fd)
        except OSError as error:need(error.errno==errno.EBADF,'runtime_file_close')
        else:raise RuntimeError('runtime_file_close')
    smoke=ordinary_caller_smoke(component,guard)
    guard();return{'schema':'normal_market_same_image_runtime_observation/1','status':'OBSERVED_NOT_ASSESSED',
        **{name:value[name]for name in('image_id','source_commit','source_tree','input_signature','run_id','run_attempt','vector_sha256')},
        'observed_at':int(time.time()),'packages':installed,'files':files,'component_presence':presence,'acl_consumers':caller,'ordinary_caller_smoke':smoke,
        'declared_runtime_files_complete':True,'all_declared_file_readers_closed':True,'production_mounts_or_data_used':False,'security_accepted':False}

def collect(M,root,bundle,helper_raw,guard):
    """Only the existing normal driver's bounded command/clock own Docker work."""
    request(bundle);need(type(helper_raw)is bytes and 0<len(helper_raw)<=MAX_SOURCE and callable(guard),'runtime_collector_input')
    need(M.NORMAL_OPERATION is not None and M.NORMAL_OPERATION.remaining()>=270,'runtime_original_budget')
    name='normal-market-runtime-'+uuid.uuid4().hex;owner=name.rsplit('-',1)[1];image=bundle['image_id'];docker=M.DOCKER;env=M.environment(root)
    template='{"id":{{json .Id}},"name":{{json .Name}},"image":{{json .Image}},"owner":{{json (index .Config.Labels "'+LABEL+'")}}}'
    def listed():return M.command([*docker,'container','ls','--all','--no-trunc','--quiet','--filter','name=^/'+name+'$'],environment=env,seconds=10,maximum=256,guard=guard).decode().strip()
    guard();need(listed()=='','runtime_container_preexisting')
    bootstrap='import hashlib,sys;h=sys.argv.pop(1);p=sys.argv.pop(1);assert len(h)<=120000;raw=bytes.fromhex(h);assert hashlib.sha256(raw).hexdigest()==p;exec(compile(raw,"<sealed-normal-runtime>","exec"),{"__name__":"__main__"})'
    argv=[*docker,'run','--rm','--pull=never','--name',name,'--label',LABEL+'='+owner,'--network','none','--read-only','--cap-drop','ALL','--security-opt','no-new-privileges',
        '--tmpfs','/tmp:rw,noexec,nosuid,nodev,size=16m','--user','0:0','--memory','512m','--cpus','1','--pids-limit','64','--entrypoint','/usr/local/bin/python3',image,'-I','-S','-B','-c',bootstrap,helper_raw.hex(),sha256(helper_raw).hexdigest(),'--inside',encoded(bundle).hex()]
    primary=None;output=None;cleanup_error=None
    try:output=M.command(argv,environment=env,seconds=180,maximum=4*1024**2,guard=guard)
    except BaseException as error:primary=error
    finally:
        try:
            cid=listed()
            if cid:
                need(re.fullmatch('[a-f0-9]{64}',cid),'runtime_cleanup_identity')
                actual=document(M.command([*docker,'container','inspect','--format',template,cid],environment=env,seconds=10,maximum=2048,guard=guard))
                need(actual=={'id':cid,'name':'/'+name,'image':image,'owner':owner},'runtime_cleanup_identity')
                M.command([*docker,'container','rm','--force',cid],environment=env,seconds=15,maximum=256,guard=guard)
            need(listed()=='','runtime_cleanup_incomplete');guard()
        except BaseException as error:cleanup_error=error
    if primary is not None and cleanup_error is not None:raise BaseExceptionGroup('runtime observation and owned cleanup',[primary,cleanup_error])
    if cleanup_error is not None:raise cleanup_error
    if primary is not None:raise primary
    value=document(output);need(type(value)is dict and value.get('schema')=='normal_market_same_image_runtime_observation/1'and value.get('status')=='OBSERVED_NOT_ASSESSED'
        and all(value.get(key)==bundle[key]for key in('image_id','source_commit','source_tree','input_signature','run_id','run_attempt','vector_sha256'))
        and value.get('declared_runtime_files_complete')is True and value.get('all_declared_file_readers_closed')is True
        and value.get('production_mounts_or_data_used')is False and value.get('security_accepted')is False,'runtime_receipt_identity')
    smoke=value.get('ordinary_caller_smoke');need(type(smoke)is dict and smoke.get('ordinary_caller_smoke_passed')is True
        and type(smoke.get('successful_command_invocations'))is int and smoke['successful_command_invocations']==11
        and smoke.get('public_owned_fixtures_only')is True and smoke.get('fixtures_removed')is True,'runtime_caller_smoke')
    value['owned_container_cleanup_proven']=True;guard();return value

if __name__=='__main__':
    if len(sys.argv)==3 and sys.argv[1]=='--inside':
        need(len(sys.argv[2])<=2*MAX_BUNDLE,'runtime_bundle_bound');raw=bytes.fromhex(sys.argv[2]);need(len(raw)<=MAX_BUNDLE,'runtime_bundle_bound')
        result=encoded(observe(document(raw)));need(len(result)<=4*1024**2,'runtime_report_bound');sys.stdout.buffer.write(result)
    else:print(json.dumps({'status':'UNARMED_SOURCE_ONLY','security_accepted':False}))
