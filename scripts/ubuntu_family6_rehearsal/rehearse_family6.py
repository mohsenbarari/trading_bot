"""Root-derived inputs, one hosted disposable exact-six forward and rollback.

Default inert. This never grants real Host, PID1, activation or security authority.
"""
from pathlib import Path,PurePosixPath
from types import ModuleType
from hashlib import sha256
from uuid import uuid4
import argparse,errno,io,json,os,re,stat,sys,tempfile,time,zipfile
HERE=Path(__file__).absolute().parent
RECEIPT_PIN=(40229,'bd092ff4f49ed00fcca21afacb68628913fba76407b87c5a1738bcbe4aacc797')
# Bound only after independent Root OCI layer inspection of these exact bases.
POLICY_PINS={key:{'bytes':19,'sha256':'c2bcd9decf63ff2c0d9f473f38bc3607900530aad80f99139855d56678456230','mode':0o100755,'uid':0,'gid':0,'nlink':1}for key in ('ubuntu239','ubuntu243')}
PINS={'source_io.py':(8831,'b2e6159d62ded26c81c5a72ad9dbda73f6029719451ff2b27d7edeefeae7b568'),
 'host_io_original.py':(53351,'fc9fc8e4ae7fe1af75fd0b4c63fedb0ebfa5820148bb311c5eaafcaaa5bbbb82'),
 'worker.py':(18861, 'af2522c0ea070d6c07cbda78a04b930fad7d539da3b1c51a65563968e82651b7'),
 'profile239.py':(5262,'420c3b55567fd93d200386b4572dfc143da1df8b5bc2114409babf2209c9f767'),
 'profile243.py':(5953,'202ee45a680e100056abe167a916fa56f71bcdfdc68e866527ba1e1ce3c29d14'),
 'ROOT-DERIVED-INPUTS.json':RECEIPT_PIN,
 'old-signed-family6.json':(13864,'c589251337ef4d22940a42de5447df9052c9743a7b227cdd54995533646bc85a'),
 'snapshot-public-isrg-root-x1.crt':(1939,'22b557a27055b33606b6559f37703928d3e4ad79f110b407d04986e1843543d1')}
SUBJECTS={'ubuntu239':('Native',41279931704,124769599828,13074712047,'2.39-0ubuntu8.9','ubuntu:24.04@sha256:78d4744ead1f5b19b04d0df558ab182a5300903900755e515b2ca4d10eb2044d'),
 'ubuntu243':('Web',41279933499,124769606293,13074862752,'2.43-2ubuntu2.4','ubuntu:26.04@sha256:157dc3d6e4a3e4f2c68d12df9df9ed6e808b59af6d4c3757b432b4f519916725')}
FAMILIES={'ubuntu239':{'libc-bin','libc-dev-bin','libc-devtools','libc6','libc6-dev','locales'},'ubuntu243':{'libc-bin','libc-dev-bin','libc-gconv-modules-extra','libc6','libc6-dev','locales'}}
DOCKER=['/usr/bin/docker','--host=unix:///var/run/docker.sock'];LABEL='security.family6.nonce'
INSPECT='{"Id":{{json .Id}},"Image":{{json .Image}},"Name":{{json .Name}},"Owner":{{json (index .Config.Labels "security.family6.nonce")}},"Running":{{json .State.Running}},"Pid":{{json .State.Pid}},"Privileged":{{json .HostConfig.Privileged}},"Memory":{{json .HostConfig.Memory}},"MemorySwap":{{json .HostConfig.MemorySwap}},"NanoCpus":{{json .HostConfig.NanoCpus}},"PidsLimit":{{json .HostConfig.PidsLimit}},"Restart":{{json .HostConfig.RestartPolicy.Name}},"Mounts":{{json .Mounts}},"Networks":{{json .NetworkSettings.Networks}}}'
POLICY_READ='''set -eu
p=/usr/sbin/policy-rc.d
test -f "$p"; test ! -L "$p"
test "$(stat -c %s -- "$p")" -le 8192
stat -c '%d %i %f %u %g %h %s' -- "$p"
sha256sum -- "$p"
cat -- "$p"
printf '\\n'
stat -c '%d %i %f %u %g %h %s' -- "$p"
'''
def need(value,code):
    if not value:raise RuntimeError(code)
def encoded(value):
    parts=[];total=1
    for part in json.JSONEncoder(sort_keys=True,separators=(',',':'),allow_nan=False).iterencode(value):
        raw=part.encode();need(total+len(raw)<=4*1024**2,'public_result_bound');parts.append(raw);total+=len(raw)
    return b''.join(parts)+b'\n'
def pairs(rows):
    out={}
    for key,value in rows:need(key not in out,'duplicate_JSON');out[key]=value
    return out
def document(raw):return json.loads(raw,object_pairs_hook=pairs,parse_constant=lambda _:need(False,'nonfinite_JSON'))
def frame(s):return(s.st_dev,s.st_ino,s.st_mode,s.st_uid,s.st_gid,s.st_nlink,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
def module(name,raw):
    result=ModuleType('_family6_'+name.replace('.','_'));result.__file__=str(HERE/name);exec(compile(raw,str(HERE/name),'exec'),result.__dict__);return result
def bootstrap():
    path=HERE/'source_io.py';pin=PINS[path.name];fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
    try:
        before=os.fstat(fd);need(stat.S_ISREG(before.st_mode)and before.st_nlink==1 and before.st_uid==os.geteuid()and not before.st_mode&0o022 and before.st_size==pin[0],'source_metadata')
        raw=os.pread(fd,pin[0]+1,0);need((len(raw),sha256(raw).hexdigest())==pin and frame(before)==frame(os.fstat(fd))==frame(path.lstat()),'source_pin')
    finally:os.close(fd)
    try:os.fstat(fd)
    except OSError as error:need(error.errno==errno.EBADF,'source_close')
    else:need(False,'source_close')
    return module('source_io.py',raw)
def validate_receipt(value,old):
    need(value.get('schema')=='Root_derived_Ubuntu_family6_rehearsal_inputs/1'and value.get('scope')=='EXACT_TWO_UBUNTU_SIX_PACKAGE_DISPOSABLE_ORDINARY_FORWARD_ROLLBACK_REHEARSAL_INPUTS_ONLY','Root_receipt_purpose')
    need(all(value.get(k)is False for k in ('Host_install_allowed','activation_authority','security_accepted','normal_configuration_admitted','original_CI_ABI_loop_reached'))and value.get('old_producer_wrappers_remain_failed')is value.get('original_normalized_provenance_absent')is True,'Root_receipt_authority')
    need(value.get('new_derived_static_headers_dynamic_ABI_literal_docs_qualified')is True and value['Root_signed_old_family_manifest']['sha256']==PINS['old-signed-family6.json'][1],'Root_qualification_scope')
    rows=value.get('profiles');need(type(rows)is list and len(rows)==2 and{r.get('profile')for r in rows}==set(SUBJECTS),'Root_profiles')
    for row in rows:
        key=row['profile'];site,run,job,artifact,version,base=SUBJECTS[key];names=FAMILIES[key]
        need(type(row.get('attempt'))is int and(row.get('site'),row.get('original_run'),row.get('original_job'),row.get('attempt'),row.get('head'),row.get('immutable_base'))==(site,run,job,1,'48c9706b10d7db802e0a7552e26aea6ab4921299',base),'Root_original_subject')
        need(row.get('original_wrapper_exit_code')==1 and type(row.get('original_wrapper_exit_code'))is int and type(row.get('Root_derivative_exit_code'))is type(row.get('actual_dpkg_vendor_exit_code'))is int and row.get('Root_derivative_exit_code')==row.get('actual_dpkg_vendor_exit_code')==0 and row.get('ordinary_producer_success')is False and row.get('missing_original_provenance')is True and row.get('derived_rehearsal_input_qualified')is True and row.get('Source_or_package_official_signature_claim')is False,'Root_failed_producer_retained')
        products=row.get('products');need(type(products)is list and len(products)==6,'Root_family_size')
        new={p['original_producer_control']['Package']:p for p in products};need(set(new)==set(row['rollback_old_six'])==names,'Root_family_scope')
        members=row.get('product_ZIP_members');need(type(members)is list and len(members)==6 and len({m['member']for m in members})==6,'Root_ZIP_member_scope')
        prefix='reports/native239-raw-failure/'if key=='ubuntu239'else'reports/two-profile-raw-failure/'
        indexed={m['member']:m for m in members}
        prior=next(p for p in old['records']if p['site']==site);need(set(prior['packages'])==names and prior['version']==version,'Root_old_family')
        for name,p in new.items():
            need(p['original_producer_control']=={'Package':name,'Version':version+'+gold1','Architecture':'all'if name=='locales'else'amd64','Source':'glibc'},'Root_product_control')
            need(type(p['bytes'])is int and 0<p['bytes']<=8*1024**2 and re.fullmatch('[a-f0-9]{64}',p['sha256']),'Root_product_pin')
            member=prefix+name+'_'+version+'+gold1_'+('all'if name=='locales'else'amd64')+'.deb'
            need(member in indexed and(indexed[member]['bytes'],indexed[member]['sha256'])==(p['bytes'],p['sha256']),'Root_product_ZIP_join')
            previous=prior['packages'][name]['DEB'];back=row['rollback_old_six'][name];need((back['bytes'],back['sha256'])==(previous['bytes'],previous['sha256']),'Root_rollback_pin')
        need(row['original_artifact']['id']==artifact and row['original_artifact']['digest']=='sha256:'+row['original_ZIP']['sha256'] and row['original_artifact']['size_in_bytes']==row['original_ZIP']['bytes'],'Root_artifact_pin')
    return{r['profile']:r for r in rows}
def policy(raw):
    first,rest=raw.split(b'\n',1);digest,rest=rest.split(b'\n',1);fields=first.decode('ascii').split(' ')
    need(len(fields)==7,'policy_metadata');numbers=[int(x,16 if i==2 else 10)for i,x in enumerate(fields)];size=numbers[-1]
    need(all(n>=0 for n in numbers)and numbers[1]>0 and 0<size<=8192 and stat.S_ISREG(numbers[2])and numbers[3:6]==[0,0,1],'policy_metadata')
    body=rest[:size];tail=rest[size:];need(tail==b'\n'+first+b'\n'and digest.decode('ascii')==sha256(body).hexdigest()+'  /usr/sbin/policy-rc.d','policy_changed')
    text=body.decode('utf8','strict');return{'bytes':size,'sha256':sha256(body).hexdigest(),'mode':numbers[2],'uid':0,'gid':0,'nlink':1,'public_body':text,'local_before_after_identity':numbers}
def validate_owned(row,cid,nonce,base,iid,inputs,running,offline=False):
    need(row['Id']==cid and row['Image']==iid and row['Owner']==nonce and row['Name']=='/family6-'+nonce and row['Running']is running and type(row['Pid'])is int and(row['Pid']>0 if running else row['Pid']==0),'owned_identity')
    need(row['Privileged']is False and row['Memory']==row['MemorySwap']==4*1024**3 and row['NanoCpus']==2*10**9 and row['PidsLimit']==512 and row['Restart']=='no','owned_resources')
    mounts=row['Mounts'];need(type(mounts)is list and len(mounts)==1 and mounts[0]['Type']=='bind'and mounts[0]['Source']==str(inputs)and mounts[0]['Destination']=='/inputs'and mounts[0]['RW']is False,'owned_readonly_inputs')
    need(type(row['Networks'])is dict and set(row['Networks'])==(set()if offline else{'bridge'}),'owned_network_scope')
def public_directory(path,scope):
    # mkdir is exclusive: borrowed paths never reach fchmod. Admit no child
    # Source until this exact newly owned original directory FD has closed.
    scope.check();parent=path.parent;parent_before=frame(parent.lstat())[:5]
    need(parent.resolve(strict=True)==parent and stat.S_ISDIR(parent_before[2])and parent_before[3:5]==(scope.owner,scope.gid)and not parent_before[2]&0o022,'public_directory_parent')
    path.mkdir(mode=0o755);before=frame(path.lstat());fd=None;primary=None;after=None
    try:
        need(stat.S_ISDIR(before[2])and before[3:5]==(scope.owner,scope.gid)and path.resolve(strict=True)==path,'public_directory_owner')
        fd=os.open(path,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC)
        need(frame(os.fstat(fd))==frame(path.lstat())==before,'public_directory_identity');scope.check();os.fchmod(fd,0o755);after=frame(os.fstat(fd))
        need(after[:2]==before[:2]and after[2]==stat.S_IFDIR|0o755 and after[3:8]==before[3:8]and after[8]>=before[8]and frame(path.lstat())==after,'public_directory_chmod_identity');scope.check()
    except BaseException as error:primary=error
    finally:
        if fd is not None:
            try:
                os.close(fd)
                try:os.fstat(fd)
                except OSError as error:need(error.errno==errno.EBADF,'public_directory_close_unknown')
                else:need(False,'public_directory_close_unknown')
                need(after is not None and path.resolve(strict=True)==path and frame(path.lstat())==after and frame(parent.lstat())[:5]==parent_before,'public_directory_postclose_identity');scope.check()
            except BaseException as error:
                if primary is None:primary=error
                primary.public_directory_finality_failed=True
    if primary is not None:raise primary
    return {'name':path.name,'before':before,'after':after,'original_directory_FD_physically_closed':True}

def execute(args):
    need(os.getuid()==os.geteuid()==1001 and all(os.environ.get(k)==v for k,v in {'GITHUB_ACTIONS':'true','RUNNER_ENVIRONMENT':'github-hosted','RUNNER_OS':'Linux','RUNNER_ARCH':'X64','GITHUB_REPOSITORY':'mohsenbarari/trading_bot','GITHUB_REPOSITORY_ID':'1065680631','GITHUB_EVENT_NAME':'workflow_dispatch','GITHUB_REF':'refs/heads/main'}.items()),'hosted_UID1001_only')
    workspace=Path(os.environ['GITHUB_WORKSPACE']);need(workspace.is_absolute()and workspace.resolve(strict=True)==workspace and HERE.resolve(strict=True)==HERE and HERE.is_relative_to(workspace),'hosted_source_workspace')
    need(args.profile in SUBJECTS and re.fullmatch('[a-f0-9]{64}',args.source_sha256),'source_confirmation')
    IO=bootstrap();scope=IO.Scope(3600);held={};primary=None;final=[];cid=None;closure='NO_CONTAINER';work=None;phase='source';result=None;create_requested=False;expected_name=None;prefix_lookup_attempted=False;prefix_qualified=False;empty_outputs=[];public_api_request=None;failure_output=None;public_directories=[]
    try:
        held['self']=scope.open(Path(__file__).absolute(),(Path(__file__).stat().st_size,args.source_sha256),131072)
        for name,pin in PINS.items():held[name]=scope.open(HERE/name,pin,131072)
        def raw(name):
            r=held[name];return b''.join(r.read(min(65536,r.before[6]-offset),offset)for offset in range(0,r.before[6],65536))
        M=module('host_io_original.py',raw('host_io_original.py'));profiles=validate_receipt(document(raw('ROOT-DERIVED-INPUTS.json')),document(raw('old-signed-family6.json')));p=profiles[args.profile]
        old=document(raw('old-signed-family6.json'));prior=next(x for x in old['records']if x['site']==p['site'])
        temp=Path(os.environ['RUNNER_TEMP']);need(temp.resolve(strict=True)==temp and temp.is_dir(),'runner_temp')
        need(os.statvfs(temp).f_bavail*os.statvfs(temp).f_frsize>=8*1024**3,'rehearsal_disk_floor')
        nonce=uuid4().hex;isolated=False;closing=False;work=Path(tempfile.mkdtemp(prefix='family6-'+nonce+'-',dir=temp));inputs=work/'inputs';public_directories.append(public_directory(inputs,scope));client=work/'empty-docker-client';client.mkdir(mode=0o700)
        end=time.monotonic()+3300;environment={'PATH':'/usr/bin:/bin','HOME':str(client),'DOCKER_CONFIG':str(client),'DOCKER_HOST':'unix:///var/run/docker.sock','LANG':'C.UTF-8','LC_ALL':'C.UTF-8'}
        def command(argv,maximum=4*1024**2,auth=False):
            env={**environment,**({'GH_TOKEN':os.environ['GH_TOKEN']}if auth else{})}
            return M.run(argv,env=env,deadline=end if closing else end-45,maximum=maximum,guard=scope.check)
        def save(path,body,mode=0o600):
            need(type(body)is bytes and len(body)<=32*1024**2,'publication_bound')
            if not body:
                need(path.parent==work and path.name in {'bootstrap-public.log','prepare.json','exercise.json','failed-public-phase-output.bin'},'unexpected_empty_publication')
                empty_outputs.append({'name':path.name,'bytes':0,'sha256':sha256(body).hexdigest(),'raw_output_was_empty':True});return
            writer=scope.create(path,32*1024**2)
            for offset in range(0,len(body),65536):writer.append(body[offset:offset+65536])
            writer.check();before=writer.before;os.fchmod(writer.original_fd,mode);after=IO.frame(os.fstat(writer.original_fd))
            need(after[:2]==before[:2]and after[2]==stat.S_IFREG|mode and after[3:8]==before[3:8]and after[8]>=before[8]and IO.frame(path.lstat())==after,'publication_chmod_identity')
            writer.before=after;writer.check();reference=writer.seal((len(body),sha256(body).hexdigest()))
            reader=scope.open(path,(reference['bytes'],reference['sha256']),32*1024**2)
            need(reader.before==writer.before,'publication_reader_writer_identity')
            writer.close();reader.check();need(reader.hash()==reference['sha256'],'publication_postwriter_readback');reader.close();reader.named();scope.check()
        def api(path,maximum=65536,accept='application/vnd.github+json'):
            nonlocal public_api_request
            public_api_request={'endpoint':path,'accept':accept}
            response=command(['/usr/bin/gh','api','--method','GET','-H','Accept: '+accept,'repos/mohsenbarari/trading_bot/'+path],maximum,True)
            public_api_request=None;return response
        cfg={'profile':args.profile,'old_version':prior['version'],'new_version':prior['version']+'+gold1','old':{},'new':{}}
        if not args.inspect_inherited_policy:
            phase='original_artifact_admission';run=document(api('actions/runs/'+str(p['original_run'])+'/attempts/1'));job=document(api('actions/jobs/'+str(p['original_job'])));artifact=document(api('actions/artifacts/'+str(p['original_artifact']['id'])))
            need(all(type(run[k])is int for k in ('id','run_attempt'))and(run['id'],run['run_attempt'],run['head_sha'],run['status'],run['conclusion'],run['repository']['id'],run['head_repository']['id'])==(p['original_run'],1,p['head'],'completed','failure',1065680631,1065680631),'original_run_changed')
            need(all(type(job[k])is int for k in ('id','run_id'))and(job['id'],job['run_id'],job['head_sha'],job['status'],job['conclusion'])==(p['original_job'],p['original_run'],p['head'],'completed','failure'),'original_job_changed')
            need(type(artifact['id'])is type(artifact['size_in_bytes'])is int and artifact['id']==p['original_artifact']['id']and artifact['digest']=='sha256:'+p['original_ZIP']['sha256']and artifact['size_in_bytes']==p['original_ZIP']['bytes']and artifact['expired']is False and artifact['workflow_run']['id']==p['original_run']and artifact['workflow_run']['head_sha']==p['head'],'original_artifact_changed')
            archive=api('actions/artifacts/'+str(artifact['id'])+'/zip',p['original_ZIP']['bytes']);need((len(archive),sha256(archive).hexdigest())==(p['original_ZIP']['bytes'],p['original_ZIP']['sha256']),'original_ZIP_pin');save(work/'original-producer-evidence.zip',archive);save(work/'original-api-admission.json',encoded({'original_run':run['id'],'attempt':run['run_attempt'],'head':run['head_sha'],'original_job':job['id'],'run_conclusion':run['conclusion'],'job_conclusion':job['conclusion'],'artifact_id':artifact['id'],'artifact_bytes':artifact['size_in_bytes'],'artifact_digest':artifact['digest'],'original_wrapper_exit_code':1,'original_provenance_missing':True,'Root_receipt_sha256':RECEIPT_PIN[1],'security_accepted':False}))
            with zipfile.ZipFile(io.BytesIO(archive))as z:
                infos=z.infolist();need(0<len(infos)<=1024 and len({x.filename for x in infos})==len(infos)and sum(x.file_size for x in infos)<=512*1024**2,'ZIP_inventory')
                need(all(not x.flag_bits&1 and not x.filename.startswith('/')and '..'not in PurePosixPath(x.filename).parts for x in infos),'ZIP_paths')
                public_directories.append(public_directory(inputs/'new',scope));public_directories.append(public_directory(inputs/'old',scope))
                for record in p['product_ZIP_members']:
                    need(0<record['bytes']<=8*1024**2 and z.getinfo(record['member']).file_size==record['bytes'],'ZIP_product_bound')
                    with z.open(record['member'])as source:body=source.read(record['bytes']+1)
                    need((len(body),sha256(body).hexdigest())==(record['bytes'],record['sha256']),'ZIP_product_pin')
                    leaf=PurePosixPath(record['member']).name;name=next(n for n in FAMILIES[args.profile]if leaf.startswith(n+'_'));cfg['new'][name]={'bytes':len(body),'sha256':record['sha256']};save(inputs/'new'/leaf,body,0o644)
            for name,old_record in prior['packages'].items():
                path=old_record['archive_filename'];need(path.startswith('pool/main/g/glibc/')and PurePosixPath(path).name==path.split('/')[-1],'old_package_URL')
                expected=old_record['DEB'];body=command(['/usr/bin/curl','--proto','=https','--tlsv1.2','--fail','--silent','--show-error','--max-time','120','--retry','0','https://snapshot.ubuntu.com/ubuntu/20261101T000000Z/'+path],expected['bytes'])
                need((len(body),sha256(body).hexdigest())==(expected['bytes'],expected['sha256']),'old_package_pin');cfg['old'][name]={'bytes':len(body),'sha256':expected['sha256']};save(inputs/'old'/PurePosixPath(path).name,body,0o644)
        for name in ('worker.py','host_io_original.py','snapshot-public-isrg-root-x1.crt'):save(inputs/name,raw(name),0o644)
        data=encoded(cfg);save(inputs/'input.json',data,0o644);input_sha=sha256(data).hexdigest()
        base=p['immutable_base'];phase='immutable_base';command(DOCKER+['pull',base]);image=document(command(DOCKER+['image','inspect','--format','{"Id":{{json .Id}},"RepoDigests":{{json .RepoDigests}},"Os":{{json .Os}},"Architecture":{{json .Architecture}}}',base]));iid=image['Id'];need(type(image['RepoDigests'])is list and('ubuntu@'+base.split('@',1)[1]in image['RepoDigests']or'docker.io/library/ubuntu@'+base.split('@',1)[1]in image['RepoDigests'])and image['Os']=='linux'and image['Architecture']=='amd64'and re.fullmatch('sha256:[a-f0-9]{64}',iid),'immutable_base_identity')
        expected_name='family6-'+nonce
        def inspect(running=True,offline=None,identifier=None):
            offline=isolated if offline is None else offline;target=identifier if identifier is not None else cid;need(target is not None,'owned_lookup_unknown')
            value=document(command(DOCKER+['container','inspect','--format',INSPECT,target],32768));actual_id=value.get('Id');need(type(actual_id)is str and re.fullmatch('[a-f0-9]{64}',actual_id),'owned_lookup_ID')
            need(cid is None or actual_id==cid,'owned_lookup_identity');observed_running=value.get('Running')if running is None else running
            need(type(observed_running)is bool,'owned_lookup_state');validate_owned(value,actual_id,nonce,base,iid,inputs,observed_running,offline);return value
        phase='owned_container_create';create_requested=True;closure='CREATE_PREFIX_UNKNOWN'
        created=command(DOCKER+['container','create','--name','family6-'+nonce,'--label',LABEL+'='+nonce,'--memory',str(4*1024**3),'--memory-swap',str(4*1024**3),'--cpus','2','--pids-limit','512','--restart','no','--network','bridge','--mount','type=bind,src='+str(inputs)+',dst=/inputs,readonly','--workdir','/work','--env','FAMILY6_NONCE='+nonce,base,'/bin/sleep','3600'],4096).decode().strip();need(re.fullmatch('[a-f0-9]{64}',created),'created_ID');cid=created;closure='OWNED_PREFIX_PENDING'
        command(DOCKER+['container','start',cid],4096);inspect();phase='inherited_policy_preflight'
        observed=policy(command(DOCKER+['container','exec',cid,'/bin/sh','-c',POLICY_READ],12288));save(work/'inherited-policy-public.json',encoded({'profile':args.profile,'base':base,'image_id':iid,'fixed_path':'/usr/sbin/policy-rc.d',**observed,'Host_install_allowed':False,'security_accepted':False}))
        if args.inspect_inherited_policy:
            result={'status':'POLICY_PREFLIGHT_ONLY','exact_Root_policy_binding_present':POLICY_PINS[args.profile]is not None}
        else:
            binding=POLICY_PINS[args.profile];need(binding is not None and binding=={k:observed[k]for k in ('bytes','sha256','mode','uid','gid','nlink')},'Root_inherited_policy_pin_required')
            inspect();phase='approved_disposable_policy_removal'
            removal='set -eu; p=/usr/sbin/policy-rc.d; test -f "$p"; test ! -L "$p"; test "$(stat -c "%d:%i:%f:%u:%g:%h:%s" -- "$p")" = "$2"; test "$(sha256sum -- "$p" | cut -d " " -f 1)" = "$1"; test "$(stat -c "%d:%i:%f:%u:%g:%h:%s" -- "$p")" = "$2"; rm -- "$p"; test ! -e "$p"; test ! -L "$p"; printf "REMOVED_EXACT_APPROVED_PUBLIC_POLICY\\n"'
            signature=':'.join(format(v,'x')if i==2 else str(v)for i,v in enumerate(observed['local_before_after_identity']))
            removed=command(DOCKER+['container','exec',cid,'/bin/sh','-c',removal,'-',binding['sha256'],signature],4096);need(removed==b'REMOVED_EXACT_APPROVED_PUBLIC_POLICY\n','approved_policy_removal_unproven');save(work/'policy-removal.json',encoded({'fixed_path':'/usr/sbin/policy-rc.d','old_pin':binding,'observed_original_identity':observed['local_before_after_identity'],'actual_unlink_absence_marker':True,'Host_policy_changed':False}));inspect()
            profile_module=module('profile',raw('profile239.py'if args.profile=='ubuntu239'else'profile243.py'));prepare=profile_module.HOST['bootstrap_prepare'];need(profile_module.HOST['base']==base and prepare[:2]==['/bin/sh','-c'],'bootstrap_profile')
            phase='signed_disposable_helper_preparation';bootstrap_output=command(DOCKER+['container','exec',cid,'/bin/sh','-c',prepare[2].replace('/public-context/','/inputs/')],4*1024**2);save(work/'bootstrap-public.log',bootstrap_output);inspect()
            phase='ordinary_old_six_baseline';prepared=command(DOCKER+['container','exec',cid,'/usr/bin/python3','-I','-S','-B','/inputs/worker.py','--owned-family6','prepare',input_sha],8*1024**2);save(work/'prepare.json',prepared)
            need(document(prepared)['status']=='PASS_DISPOSABLE_ONLY'and re.fullmatch('[a-f0-9]{64}',document(prepared)['baseline_snapshot_sha256']),'preparation_result');inspect();phase='physical_network_disconnect';command(DOCKER+['network','disconnect','bridge',cid],4096);isolated=True;inspect(offline=True)
            phase='offline_exact_six_forward_rollback';observed_worker=command(DOCKER+['container','exec',cid,'/usr/bin/python3','-I','-S','-B','/inputs/worker.py','--owned-family6','exercise',input_sha],8*1024**2);save(work/'exercise.json',observed_worker);verdict=document(observed_worker)
            need(verdict['status']=='PASS_DISPOSABLE_ONLY'and verdict['baseline_snapshot_sha256']==document(prepared)['baseline_snapshot_sha256']and all(verdict.get(k)is True for k in ('forward_exact_six','rollback_exact_six','nonfamily_packages_unchanged','configuration_restored_exactly'))and verdict['Host_install_allowed']is verdict['real_Host_PID1_reexec_proven']is verdict['security_accepted']is False,'exercise_result');inspect(offline=True)
            result={'status':'PASS_DISPOSABLE_FORWARD_ROLLBACK_ONLY','profile':args.profile,'original_wrapper_exit_code':1,'original_provenance_missing':True,'Root_receipt_sha256':RECEIPT_PIN[1]}
        phase='stop_owned';closing=True;inspect();command(DOCKER+['container','stop','--timeout','10',cid],4096);inspect(False);closure='STOPPED'
    except BaseException as error:
        primary=error
        if getattr(error,'public_directory_finality_failed',False)is True:final.append('public_directory_finality_unknown')
        truth=getattr(error,'public_command_failure',None)
        if type(truth)is dict and phase in {'original_artifact_admission','signed_disposable_helper_preparation','ordinary_old_six_baseline','offline_exact_six_forward_rollback'}:
            captured=truth.get('captured_output')
            if type(captured)is bytes:
                failure_output={'captured_bytes':len(captured),'raw_retention_limit_bytes':8*1024**2,'full_raw_output_retained':False,'raw_output_was_empty':not captured,'diagnostic_truncated':len(captured)>8*1024**2}
                if len(captured)<=8*1024**2:
                    try:save(work/'failed-public-phase-output.bin',captured);failure_output['full_raw_output_retained']=True
                    except BaseException:final.append('failure_output_retention_unknown')
                else:
                    failure_output.update(prefix_hex=captured[:16384].hex(),tail_hex=captured[-16384:].hex(),prefix_tail_limit_each_bytes=16384)
            else:final.append('failure_output_retention_unknown')
    finally:
        if create_requested and closure!='STOPPED':
            closing=True
            try:
                prefix_lookup_attempted=True;prefix=inspect(None,identifier=cid or expected_name);cid=prefix['Id'];prefix_qualified=True
                if prefix['Running']:command(DOCKER+['container','stop','--timeout','10',cid],4096)
                inspect(False);closure='STOPPED'
            except BaseException:final.append('owned_closure_unknown')
        try:scope.finish()
        except BaseException:final.append('source_finality_unknown')
    if primary is not None or final:
        text=str(primary);code=text if type(primary)is RuntimeError and re.fullmatch('[A-Za-z0-9_]{1,100}',text)else'rehearsal_refused'
        result={'status':'FAILED','phase':phase,'code':code,'finalizer_codes':final,'owned_container_closure':closure,'create_request_prefix_retained':create_requested,'expected_owned_name':expected_name,'automatic_retry':False,'failed_public_api_request':public_api_request if phase=='original_artifact_admission'else None,'failure_output_retention':failure_output,'failed_command_truth':({k:getattr(primary,'public_command_failure')[k]for k in ('actual_returncode','observed_output_bytes','actual_pipe_closed','stdout_eof_observed','maximum_output_bytes')}if type(getattr(primary,'public_command_failure',None))is dict else None)}
    result.update(create_request_prefix_retained=create_requested,expected_owned_name=expected_name,expected_owner_nonce=nonce if expected_name else None,qualified_prefix_lookup_attempted=prefix_lookup_attempted,owned_prefix_qualified=prefix_qualified,empty_public_outputs=empty_outputs,new_public_directory_proofs=public_directories,profile=args.profile,owned_container_closure=closure,source_sha256=args.source_sha256,schema='Root_Ubuntu_family6_disposable_ordinary_rehearsal/1',source_original_FDs_physically_closed=len(scope.rows)if scope.finished and not final else None,source_finality_proven=scope.finished and not final,Host_install_allowed=False,real_Host_PID1_reexec_proven=False,activation_authority=False,security_accepted=False)
    sys.stdout.write(encoded(result).decode());return 0 if result['status']in{'POLICY_PREFLIGHT_ONLY','PASS_DISPOSABLE_FORWARD_ROLLBACK_ONLY'}else 1
def main(argv=None):
    argv=list(sys.argv[1:]if argv is None else argv)
    if '--execute-family6-rehearsal'not in argv:
        print('{"status":"UNARMED","Host_action":false,"security_accepted":false}');return 0
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--execute-family6-rehearsal',dest='profile',choices=tuple(SUBJECTS),required=True);parser.add_argument('--source-sha256',required=True);parser.add_argument('--inspect-inherited-policy',action='store_true')
    try:return execute(parser.parse_args(argv))
    except BaseException:
        print('{"status":"REFUSED_BEFORE_CONTEXT","Host_install_allowed":false,"security_accepted":false}');return 1
if __name__=='__main__':raise SystemExit(main())
