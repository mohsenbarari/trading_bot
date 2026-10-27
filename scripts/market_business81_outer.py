"""Dedicated three-profile producer API; standalone entry is always UNARMED.

The authenticated normal two-Git controller calls produce() after its own
image build/admission. It must seal this module and both source dependencies
in the normal workflow's controller-input signature. No existing probe-kind
dispatcher or global completion gate is changed by this proposal.
"""
from hashlib import sha256
from pathlib import Path
from uuid import uuid4
import json,os,re,selectors,subprocess,time

PROFILES=('base-nonroot','base-root','added-replay')
OWNER='io.gold-trade.normal-business81-owner'
PROFILE='io.gold-trade.normal-business81-profile'
NORMAL_PIN=(83244, '03ff9f9ea1517f994356b3babaa4725da764773d1c9c2cce221ba41f91fee67e')
APPROVAL_PIN={'bytes':1021,'sha256':'4ab6ef5e065d4ed81a82675e3e5d40586f7479940d30686c1acbb978a65c6c83'}
PROPOSAL_PIN={'bytes':771,'sha256':'b3ef050fb81205924bf01c08959a603322ef5587320db4db00b6b539313f21df'}
PATCH_PIN={'bytes':333,'sha256':'2b6a4449a619069d203848c61478b91c48a2b29b855c7026e4844051ea78e38c'}
MAX_STDOUT=32768;MAX_STDERR=4*1024**2


def need(value,code):
 if not value:raise RuntimeError(code)


def assertion_approval(B,approval_raw,proposal_raw,patch_raw,manifest):
 for raw,pin in((approval_raw,APPROVAL_PIN),(proposal_raw,PROPOSAL_PIN),(patch_raw,PATCH_PIN)):
  need(type(raw)is bytes and(len(raw),sha256(raw).hexdigest())==(pin['bytes'],pin['sha256']),'business81_assertion_approval_pin')
 approval=json.loads(approval_raw);proposal=json.loads(proposal_raw)
 need(set(approval)=={'after','approved','approved_image_base','before','business_tests_executed','deployment_authority','historical_source_commit',
  'other_business_test_bodies_changed','patch','path','proposal_sha256','reviewed_at','reviewer','schema','scope','security_accepted'}
  and approval['schema']=='root_reviewed_historical_security_assertion_delta/1'and approval['approved']is True
  and approval['historical_source_commit']==B.BASE and approval['path']==B.FOUNDATION and approval['reviewer']=='Codex Root'
  and approval['proposal_sha256']==PROPOSAL_PIN['sha256']and approval['patch']==PATCH_PIN
  and approval['before']==proposal['before']and approval['after']==proposal['after']
  and all(approval[k]is False for k in('business_tests_executed','deployment_authority','other_business_test_bodies_changed','security_accepted')),'business81_assertion_approval')
 need(approval['approved_image_base'].encode()in B.NEW_ASSERTION
  and manifest['security_assertion_update']=={'path':B.FOUNDATION,'old_line':B.OLD_ASSERTION.decode(),'new_line':B.NEW_ASSERTION.decode(),
   'approved_source_delta_sha256':approval['after']['sha256']},'business81_assertion_scope')
 changed=[r for r in manifest['files']if r['transformation']=='reviewed_security_base_assertion_only']
 need(len(changed)==2 and{r['path']for r in changed}=={'base/'+B.FOUNDATION,'replay/'+B.FOUNDATION}
  and all({'bytes':r['bytes'],'sha256':r['sha256']}==approval['after']and{'bytes':r['original_bytes'],'sha256':r['original_sha256']}==approval['before']for r in changed),'business81_assertion_scope')
 return approval


def profile_container(value,*,name,cid,image,owner,profile,evidence,argv,expected_env):
 """Full actual isolation/identity checks; actual private Config stays in RAM."""
 need(type(value)is dict and value.get('Id')==cid and value.get('Name')=='/'+name and value.get('Image')==image,'business81_owner')
 config=value.get('Config');host=value.get('HostConfig');need(type(config)is dict and type(host)is dict,'business81_isolation')
 expected_user='0:0'if profile=='base-root'else'10001:10001'
 need(config.get('User')==expected_user and config.get('Entrypoint')==['/usr/local/bin/python3']and config.get('Cmd')==argv
  and config.get('Env')==expected_env and config.get('WorkingDir')=='/app'
  and config.get('Tty')is False and config.get('OpenStdin')is False
  and config.get('Labels',{}).get(OWNER)==owner and config.get('Labels',{}).get(PROFILE)==profile,'business81_owner')
 need(host.get('ReadonlyRootfs')is True and host.get('Privileged')is False and host.get('NetworkMode')=='none'
  and host.get('CapAdd')in(None,[])and host.get('CapDrop')in(['ALL'],['CAP_ALL'])
  and host.get('SecurityOpt')in(['no-new-privileges'],['no-new-privileges:true'])
  and host.get('LogConfig')=={'Type':'none','Config':{}},'business81_isolation')
 for key,expected in(('Memory',1073741824),('MemorySwap',1073741824),('NanoCpus',2000000000),('PidsLimit',128)):
  need(type(host.get(key))is int and host[key]==expected,'business81_resources')
 need(host.get('Tmpfs')=={'/tmp':'rw,nosuid,nodev,noexec,size=128m'},'business81_mounts')
 mounts=value.get('Mounts');need(type(mounts)is list,'business81_mounts')
 binds=[m for m in mounts if m.get('Type')=='bind'];other=[m for m in mounts if m.get('Type')!='bind']
 need(len(binds)==1 and binds[0].get('Source')==str(evidence)and binds[0].get('Destination')=='/evidence'and binds[0].get('RW')is False
  and all(m.get('Type')=='tmpfs'and m.get('Destination')=='/tmp'for m in other)and len(other)<=1,'business81_mounts')
 network=value.get('NetworkSettings');need(type(network)is dict and set(network.get('Networks',{}))=={'none'},'business81_network')
 endpoints=network['Networks']['none'];need(type(endpoints)is dict and not endpoints.get('IPAddress')and not endpoints.get('GlobalIPv6Address'),'business81_network')
 return value


def attached_once(M,*,cid,environment,log_fd,guard,seconds=220):
 """One fixed Docker START, distinct bounded stdout/stderr, true client RC."""
 need(type(cid)is str and re.fullmatch('[a-f0-9]{64}',cid)and type(seconds)is int and 0<seconds<=220,'business81_start')
 guard();end=time.monotonic()+seconds
 child=subprocess.Popen([*M.DOCKER,'container','start','--attach',cid],stdin=subprocess.DEVNULL,
  stdout=subprocess.PIPE,stderr=subprocess.PIPE,env=environment,close_fds=True,start_new_session=True)
 parts=[];counts={'stdout':0,'stderr':0};stderr_digest=sha256()
 try:
  with selectors.DefaultSelector()as selector:
   for key,pipe in(('stdout',child.stdout),('stderr',child.stderr)):
    os.set_blocking(pipe.fileno(),False);selector.register(pipe.fileno(),selectors.EVENT_READ,key)
   while selector.get_map():
    guard();left=end-time.monotonic();need(left>0,'business81_start_timeout')
    for event,_ in selector.select(min(1,left)):
     key=event.data;limit=MAX_STDOUT if key=='stdout'else MAX_STDERR
     raw=os.read(event.fd,min(65536,limit-counts[key]+1))
     if not raw:selector.unregister(event.fd);continue
     counts[key]+=len(raw);need(counts[key]<=limit,'business81_output_bound')
     if key=='stdout':parts.append(raw)
     else:
      stderr_digest.update(raw)
      view=memoryview(raw)
      while view:
       written=os.write(log_fd,view);need(written>0,'business81_output_write');view=view[written:]
   # Capture the original stderr writer BEFORE selector/pipe finalizers.
   # Its payload hash is already derived from the actual bounded writes.
   rc=child.wait(timeout=max(.1,end-time.monotonic()));guard();os.fsync(log_fd)
   q=os.fstat(log_fd);frame=(q.st_dev,q.st_ino,q.st_mode,q.st_uid,q.st_gid,q.st_nlink,q.st_size,q.st_mtime_ns,q.st_ctime_ns)
   need(frame[2]==0o100600 and frame[3:6]==(os.geteuid(),os.getegid(),1)
    and frame[6]==counts['stderr'],'source_changed')
   result={'actual_client_returncode':rc,'stdout':b''.join(parts),'stderr_bytes':counts['stderr'],
    'stderr_sha256':stderr_digest.hexdigest(),'stderr_frame':frame}
  return result
 except BaseException:
  M.cleanup_child(child);raise
 finally:
  child.stdout.close();child.stderr.close()


def close_all(rows):
 """Every physical close precedes the final named checks and completion."""
 error=None
 for row in reversed(rows):
  if row.fd is not None:
   try:row.close()
   except BaseException as exc:
    if error is None:error=exc
 for row in rows:
  try:row.named()
  except BaseException as exc:
   if error is None:error=exc
 if error is not None:raise error


def save_proof(M,path,raw,rows):
 """Bind known public bytes before the writer's physical close can alter them."""
 need(type(raw)is bytes and 0<len(raw)<=M.MAX_JSON,'business81_output_bound')
 binding={'bytes':len(raw),'sha256':sha256(raw).hexdigest()}
 M.save(path,raw)
 rows.append(M.Held(path,maximum=len(raw),binding=binding))


def publish_completion(M,path,value,rows):
 """A final file never bypasses prior physical closes or later named checks."""
 need(all(row.fd is None for row in rows),'business81_output_finalizers')
 completion=[]
 try:save_proof(M,path,M.encoded(value),completion)
 finally:close_all(completion)
 # This also detects drift during the completion writer and retained FD closes.
 for row in rows+completion:row.named()
 return value


def public_evidence_directory(M,evidence,path,owned):
 """Only this new owned public fixture tree gets traversable0755 modes."""
 need(path==evidence or path.is_relative_to(evidence),'business81_bundle')
 if path in owned:
  need(path.resolve(strict=True)==path and M.identity(path.lstat())[:5]==owned[path],'source_changed');return
 need(path==evidence or path.parent in owned,'business81_bundle')
 parent=path.parent;before_parent=M.identity(parent.lstat())
 need(parent.resolve(strict=True)==parent and before_parent[2]&0o170000==0o040000
  and before_parent[3:5]==(os.geteuid(),os.getegid())and not before_parent[2]&0o022,'source_changed')
 # EXCL mkdir: pre-existing or symlink paths are never adopted or chmodded.
 path.mkdir(mode=0o700);before=M.identity(path.lstat());fd=None
 try:
  need(path.resolve(strict=True)==path and before[2:5]==(0o040700,os.geteuid(),os.getegid()),'source_changed')
  fd=os.open(path,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC)
  need(M.identity(os.fstat(fd))==before,'source_changed');os.fchmod(fd,0o755);after=M.identity(os.fstat(fd))
  need(after[:2]==before[:2]and after[2]==0o040755 and after[3:8]==before[3:8]
   and M.identity(path.lstat())==after,'source_changed')
 finally:
  if fd is not None:os.close(fd)
 need(path.resolve(strict=True)==path and M.identity(path.lstat())==after
  and M.identity(parent.lstat())[:5]==before_parent[:5],'source_changed');owned[path]=after[:5]


def produce(M,B,*,root,repo,image,receipt,signature,controller_signature,contents,manifest,plans,source_paths,approval_paths,guard):
 """Called only by the reviewed normal controller; no JSON flag arms it.

 source_paths binds THIS module, business81.py and the normal driver's exact
 reviewed source bytes. The normal caller independently authenticates their
 Git blobs/controller signature and the two-Git receipt before this API.
 """
 M.hosted(root,repo)
 need(type(source_paths)is dict and set(source_paths)=={'outer','business','normal'}and isinstance(root,Path),'business81_source')
 need(receipt.get('schema')=='normal_market_git_component_inputs/1'and receipt.get('business_commit')==B.BUSINESS_COMMIT
  and receipt.get('business_tree')==B.BUSINESS_TREE and receipt.get('accepted_manifest_sha256')==B.BUSINESS_SHA
  and sha256(M.encoded(receipt)).hexdigest()==signature,'business81_two_git')
 need(manifest['image_id']==image and manifest['release_commit']==receipt['release_commit']and manifest['two_git_input_signature']==signature
  and set(plans)==set(PROFILES)and contents['suite-manifest.json']==B.encode(manifest),'business81_bundle')
 # This caller cannot supply an unsealed alternate primitive module.
 need(source_paths['normal']['binding']=={'bytes':NORMAL_PIN[0],'sha256':NORMAL_PIN[1]}
  and Path(M.__file__).resolve()==source_paths['normal']['path']and Path(B.__file__).resolve()==source_paths['business']['path']
  and Path(__file__).resolve()==source_paths['outer']['path'],'business81_source')
 rows=[];value=None;begun=time.monotonic();nonce=uuid4().hex;owner=signature+':'+nonce
 work=root/('business81-'+nonce);work.mkdir(mode=0o700);evidence=work/'evidence';evidence_dirs={}
 public_evidence_directory(M,evidence,evidence,evidence_dirs)
 def check():
  guard();need(time.monotonic()-begun<900,'business81_deadline')
  for row in rows:row.check()
 bounded=M.periodic(check)
 def cli(argv,*,seconds=30,maximum=M.MAX_JSON,enforce=True):
  return M.command([*M.DOCKER,*argv],environment=M.environment(root),seconds=seconds,maximum=maximum,guard=bounded if enforce else lambda:None)
 def image_frame():
  raw=cli(['image','inspect','--format','{{json .}}',image]);actual=M.document(raw)
  need(actual.get('Id')==image,'business81_image');frame=M.image_contract(actual,receipt,signature,controller_signature)
  inherited=actual['Config'].get('Env');need(inherited is None or type(inherited)is list and all(type(v)is str for v in inherited),'business81_image')
  return(frame,inherited) # Public-image defaults in RAM only; never serialize/hash.
 try:
  for spec in source_paths.values():rows.append(M.Held(spec['path'],maximum=131072,binding=spec['binding']))
  need(type(approval_paths)is dict and set(approval_paths)=={'approval','proposal','patch'},'business81_assertion_paths')
  approval_sources={}
  for name,pin in(('approval',APPROVAL_PIN),('proposal',PROPOSAL_PIN),('patch',PATCH_PIN)):
   row=M.Held(approval_paths[name],maximum=pin['bytes'],binding=pin);rows.append(row);approval_sources[name]=row.raw(pin['bytes'])
  approval=assertion_approval(B,approval_sources['approval'],approval_sources['proposal'],approval_sources['patch'],manifest)
  need(len(contents)==40 and set(contents)=={r['path']for r in manifest['files']}|set(B.HELPERS)|{'suite-manifest.json'},'business81_bundle')
  for name,(size,digest)in B.HELPERS.items():
   need(type(contents[name])is bytes and(len(contents[name]),sha256(contents[name]).hexdigest())==(size,digest),'business81_helper_pin')
  for spec in manifest['files']:
   need((len(contents[spec['path']]),sha256(contents[spec['path']]).hexdigest())==(spec['bytes'],spec['sha256']),'business81_fixture_pin')
  selection=M.document(contents['f79-business-test-selection.json'])
  need(B.encode(plans)==B.encode(B.profiles(selection,image=image,release_commit=receipt['release_commit'],two_git_signature=signature)),'business81_plans')
  for name,raw in sorted(contents.items()):
   need(B.relative(name)and type(raw)is bytes and 0<len(raw)<=4*1024**2,'business81_bundle')
   path=evidence/name;parent=evidence
   public_evidence_directory(M,evidence,evidence,evidence_dirs)
   for part in Path(name).parts[:-1]:
    need(part not in ('','.','..'),'business81_bundle');parent=parent/part
    public_evidence_directory(M,evidence,parent,evidence_dirs)
   M.save(path,raw,mode=0o644)
   rows.append(M.Held(path,maximum=4*1024**2,binding={'bytes':len(raw),'sha256':sha256(raw).hexdigest()}))
  manifest_sha=sha256(contents['suite-manifest.json']).hexdigest();initial=image_frame();observations={};cleaned=[]
  for profile in PROFILES:
   check();name='normal-market-business81-'+profile+'-'+nonce;cid=None;expected=B.container_args(profile,image=image,evidence=str(evidence),manifest_sha256=manifest_sha)
   cmd=expected[expected.index(image)+1:];profile_root=work/profile;profile_root.mkdir(mode=0o700)
   def inspect(enforce=True):
    actual=M.document(cli(['container','inspect','--format','{{json .}}',cid],enforce=enforce))
    return profile_container(actual,name=name,cid=cid,image=image,owner=owner,profile=profile,evidence=evidence,argv=cmd,expected_env=initial[1])
   save_proof(M,profile_root/'intent.json',M.encoded({'schema':'normal_market_business81_profile_intent/1','name':name,'profile':profile,
    'image_id':image,'owner':owner,'test_sources_sha256':manifest_sha,'automatic_retry':False}),rows)
   try:
    raw=cli(['container','create','--name',name,'--label',OWNER+'='+owner,'--label',PROFILE+'='+profile,*expected],maximum=128)
    cid=raw.decode().strip();need(re.fullmatch('[a-f0-9]{64}',cid),'business81_owner');inspect()
    log_path=profile_root/'tests.stderr';fd=os.open(log_path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,0o600)
    try:
     received=attached_once(M,cid=cid,environment=M.environment(root),log_fd=fd,guard=bounded)
     created=received['stderr_frame']
     need(M.identity(os.fstat(fd))==M.identity(log_path.lstat())==created,'source_changed')
     stderr_row=M.Held(log_path,maximum=MAX_STDERR,empty=True,
      binding={'bytes':received['stderr_bytes'],'sha256':received['stderr_sha256']})
     rows.append(stderr_row)
     need(stderr_row.before==created,'source_changed');stderr_row.check()
    finally:os.close(fd)
    # The original writer is closed only after its admitted read FD survives it.
    # Check all retained inputs immediately after that physical close.
    check()
    save_proof(M,profile_root/'received.json',M.encoded({'schema':'normal_market_business81_start_observation/1','actual_client_returncode':received['actual_client_returncode'],
     'stderr_bytes':received['stderr_bytes'],'stdout_bytes':len(received['stdout'])}),rows)
    actual=inspect();state=actual.get('State')
    need(received['actual_client_returncode']==0 and type(state)is dict and state.get('Running')is False and state.get('Paused')is False
     and type(state.get('Pid'))is int and state['Pid']==0 and type(state.get('ExitCode'))is int and state['ExitCode']==0,'business81_exit')
    save_proof(M,profile_root/'container-closed.json',M.encoded({'schema':'normal_market_business81_closed_container/1','profile':profile,
     'container_id':cid,'image_id':actual['Image'],'running':state['Running'],'paused':state['Paused'],'pid':state['Pid'],'exit_code':state['ExitCode']}),rows)
    observation=M.document(received['stdout'],maximum=MAX_STDOUT)
    observations[profile]={'actual_outer_returncode':received['actual_client_returncode'],'actual_container_exit_code':state['ExitCode'],'observation':observation}
    # The strict shared join validates this closed observation below before it
    # can enter the final completed result; raw stdout is not a new authority.
    save_proof(M,profile_root/'profile.json',received['stdout'],rows)
   finally:
    if cid is not None:
     actual=inspect(False);state=actual.get('State');need(type(state)is dict and type(state.get('Running'))is bool,'business81_cleanup')
     if state['Running']:
      need(cli(['container','stop','--time','3',cid],seconds=15,maximum=128,enforce=False).strip().decode()==cid,'business81_cleanup')
     state=inspect(False).get('State');need(state.get('Running')is False and state.get('Pid')==0,'business81_cleanup')
     need(cli(['container','rm',cid],seconds=15,maximum=128,enforce=False).strip().decode()==cid,'business81_cleanup')
     need(cli(['container','ls','--all','--no-trunc','--filter','id='+cid,'--format','{{.ID}}'],seconds=15,enforce=False).strip()==b'','business81_cleanup')
     cleaned.append(profile)
   need(image_frame()==initial,'business81_image_changed')
  value=B.join_actual_results(selection,plans,observations,expected_manifest_sha256=manifest_sha)
  need(cleaned==list(PROFILES),'business81_cleanup');check()
  value.update(actual_outer_authentication_and_cleanup_still_required=False,actual_profile_containers_removed=3,
   actual_same_candidate_image_verified=True,source_and_output_finalizers_completed=True,run_directory=str(work),controller_input_signature=controller_signature,
   assertion_approval=APPROVAL_PIN,assertion_reviewed_at=approval['reviewed_at'],
   profile_outputs={row.path.relative_to(work).as_posix():row.binding for row in rows if row.path.is_relative_to(work)and not row.path.is_relative_to(evidence)})
 finally:close_all(rows)
 # No successful receipt exists before every original source, fixture and
 # profile-output descriptor has physically closed and passed named finals.
 return publish_completion(M,work/'completed.json',value,rows)


if __name__=='__main__':
 print(json.dumps({'status':'UNARMED','actual_candidate_test_execution_performed':False,'security_accepted':False}))
