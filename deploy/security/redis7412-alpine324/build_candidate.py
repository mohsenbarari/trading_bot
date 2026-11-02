"""One hosted-CI supported Redis variant. No deploy or old producer mutation."""
from collections import Counter
from datetime import datetime,timezone
from hashlib import sha256
from pathlib import Path
import argparse,json,os,re,resource,signal,subprocess,time

MAX_LOG=32*1024**2;MAX_IMAGE=512*1024**2;MAX_GZIP=256*1024**2
BASE='alpine:3.24.1@sha256:79ff19e9084a00eece421b2523fb93e22d730e2c0e525905de047e848e56d95f'
ENTRYPOINT='f8122ddc0eb2ae6ca7762725999f08b2ee7a6975da54e9061feee88f6d4ab2bb'
SOURCE_SHA='324ab63d55c607639b419f547b213c62ae39ff50e09870e450a11c272567b84f'
REPO='mohsenbarari/trading_bot';ROOT_RECIPE='deploy/security/redis7412-alpine324/'

def need(ok,code):
 if not ok:raise RuntimeError(code)
def read(path,maximum):
 need(path.is_file()and not path.is_symlink()and 0<path.stat().st_size<=maximum,'input_file_bound');return path.read_bytes()
def pin(path,maximum):
 need(path.is_file()and not path.is_symlink()and 0<path.stat().st_size<=maximum,'output_file_bound')
 h=sha256();count=0
 with path.open('rb')as f:
  for raw in iter(lambda:f.read(65536),b''):count+=len(raw);need(count<=maximum,'output_file_bound');h.update(raw)
 return {'bytes':count,'sha256':h.hexdigest()}
def save(path,value):
 raw=(json.dumps(value,sort_keys=True,indent=2)+'\n').encode();need(len(raw)<=4*1024**2,'receipt_bound')
 with path.open('xb')as f:f.write(raw);f.flush();os.fsync(f.fileno())
def runtime_contract(text):
 rows=text.splitlines();need(len(rows)>=6,'runtime_contract')
 version,release,uid,gid,entry=rows[:5]
 need(re.search(r'\bv=7[.]4[.]12\b',version)is not None and re.fullmatch(r'3[.]24[.][0-9]+',release)and(uid,gid)==('999','1000')and entry==ENTRYPOINT,'runtime_contract')
 return {'server_version':version,'alpine_release':release,'redis_uid':999,'redis_gid':1000,'entrypoint_sha256':entry,'volume':'/data','working_dir':'/data'}
def assess(raw,sbom,image,started):
 need(type(raw)is dict and raw.get('Metadata',{}).get('ImageID')==image,'scan_image')
 osmeta=raw['Metadata'].get('OS',{});need(osmeta.get('Family')=='alpine'and type(osmeta.get('Name'))is str and re.fullmatch(r'3[.]24[.][0-9]+',osmeta['Name'])and osmeta.get('EOSL',False)is False,'supported_OS')
 created=datetime.fromisoformat(raw['CreatedAt'].replace('Z','+00:00')).timestamp();need(started<=created<=time.time()+60,'fresh_scan')
 results=raw.get('Results');need(type(results)is list,'scan_results');counts=Counter()
 for result in results:
  need(type(result)is dict,'scan_results');rows=result.get('Vulnerabilities')or[];need(type(rows)is list,'scan_results')
  for row in rows:need(type(row)is dict and row.get('Severity')in{'UNKNOWN','LOW','MEDIUM','HIGH','CRITICAL'},'scan_severity');counts[row['Severity']]+=1
 component=sbom.get('metadata',{}).get('component',{});need(sbom.get('bomFormat')=='CycloneDX'and component.get('type')=='container'and component.get('name')==image,'sbom_image')
 props=component.get('properties',[]);need(type(props)is list and[p.get('value')for p in props if p.get('name')=='aquasecurity:trivy:ImageID']==[image],'sbom_image')
 need(counts['HIGH']==counts['CRITICAL']==0,'unresolved_HIGH_CRITICAL')
 return {level:counts[level]for level in('UNKNOWN','LOW','MEDIUM','HIGH','CRITICAL')}

def run(argv,log,*,seconds=120,maximum=MAX_LOG,stderr=None):
 """Ordinary file-backed CLI; kernel bounds every child-written file up front."""
 def limits():
  hard=resource.getrlimit(resource.RLIMIT_FSIZE)[1];bound=maximum if hard==resource.RLIM_INFINITY else min(maximum,hard);resource.setrlimit(resource.RLIMIT_FSIZE,(bound,bound));resource.setrlimit(resource.RLIMIT_CORE,(0,0))
 env={'PATH':'/usr/local/bin:/usr/bin:/bin','HOME':os.environ['HOME'],'DOCKER_HOST':'unix:///var/run/docker.sock','LC_ALL':'C.UTF-8','DOCKER_CONFIG':os.environ['REDIS_CANDIDATE_DOCKER_CONFIG']}
 with log.open('xb')as out:
  err=stderr.open('xb')if stderr is not None else None
  try:
   p=subprocess.Popen(argv,stdin=subprocess.DEVNULL,stdout=out,stderr=err if err is not None else subprocess.STDOUT,env=env,start_new_session=True,preexec_fn=limits)
   try:need(p.wait(timeout=seconds)==0,'command_nonzero:'+log.name)
   except subprocess.TimeoutExpired:
    os.killpg(p.pid,signal.SIGKILL);p.wait(timeout=10);raise RuntimeError('command_timeout:'+log.name)from None
  finally:
   if err is not None:err.close()
  out.flush();os.fsync(out.fileno())
 need(log.stat().st_size<maximum,'command_output_bound')

def inputs(root):
 need(os.geteuid()==1001 and os.environ.get('GITHUB_ACTIONS')=='true'and os.environ.get('RUNNER_ENVIRONMENT')=='github-hosted'and os.environ.get('GITHUB_REPOSITORY')==REPO and os.environ.get('GITHUB_REF')=='refs/heads/main','hosted_Main_only')
 need(root.is_absolute()and root.resolve(strict=True)==root and root.is_relative_to(Path(os.environ['RUNNER_TEMP']).resolve(strict=True))and root!=Path(os.environ['RUNNER_TEMP']).resolve(strict=True),'CI_root')
 value=json.loads(read(root/'source-files.json',65536));need(value['commit']==os.environ['GITHUB_SHA']and value['run_id']==int(os.environ['GITHUB_RUN_ID'])and value['run_attempt']==int(os.environ['GITHUB_RUN_ATTEMPT']),'CI_source_identity')
 for row in value['files']:
  path=root/row['local'];need(pin(path,65536)=={k:row[k]for k in('bytes','sha256')},'CI_source_changed')
 dockerfile=read(root/'context/Dockerfile',65536).decode();need(dockerfile.startswith('FROM '+BASE+'\n')and 'RUN apk upgrade --no-cache'in dockerfile and SOURCE_SHA in dockerfile,'recipe_source')
 need(sha256(read(root/'context/docker-entrypoint.sh',4096)).hexdigest()==ENTRYPOINT,'recipe_entrypoint')
 os.environ['REDIS_CANDIDATE_DOCKER_CONFIG']=str(root/'empty-docker-config')
 return value

def build(root):
 source=inputs(root);report=root/'reports';report.mkdir();(root/'empty-docker-config').mkdir(mode=0o700)
 started=time.time();run(['/usr/bin/docker','build','--platform','linux/amd64','--pull','--iidfile',str(root/'image-id'),'--label','org.opencontainers.image.revision='+source['commit'],'--label','io.gold-trade.security.recipe=redis7412-alpine324','--file',str(root/'context/Dockerfile'),str(root/'context')],report/'build.log',seconds=1200)
 image=read(root/'image-id',128).decode().strip();need(re.fullmatch('sha256:[a-f0-9]{64}',image)is not None,'image_identity')
 run(['/usr/bin/docker','image','inspect',image],report/'image.json');row=json.loads(read(report/'image.json',65536));need(type(row)is list and len(row)==1,'image_identity');row=row[0]
 need(row['Id']==image and row.get('Os')=='linux'and row.get('Architecture')=='amd64'and row['Config'].get('Labels',{}).get('org.opencontainers.image.revision')==source['commit'],'image_identity')
 need(row['Config'].get('Entrypoint')==['docker-entrypoint.sh']and row['Config'].get('Cmd')==['redis-server']and row['Config'].get('WorkingDir')=='/data'and row['Config'].get('Volumes')=={'/data':{}},'image_runtime_contract')
 name='redis7412-version-'+source['run_id'].__str__()+'-'+source['run_attempt'].__str__()
 # A short public no-network metadata probe. The actual source-data tests run
 # later on isolated copies of each site's genuine backup, never in this job.
 script='set -eu; test -x /usr/local/bin/docker-entrypoint.sh; redis-server --version; cat /etc/alpine-release; id -u redis; id -g redis; sha256sum /usr/local/bin/docker-entrypoint.sh | cut -d " " -f1; apk info -v'
 primary=None
 try:run(['/usr/bin/docker','run','--rm','--name',name,'--network','none','--memory','256m','--memory-swap','256m','--cpus','1','--pids-limit','64','--entrypoint','/bin/sh',image,'-ec',script],report/'runtime.log',seconds=30,stderr=report/'runtime-stderr.log')
 except BaseException as error:primary=error;raise
 finally:
  # Own fixed fresh CI name only; --rm normally already removed its anonymous
  # /data volume. This cleanup never selects by a broad Docker predicate.
  try:
   target=report/'probe-remaining.log'
   run(['/usr/bin/docker','container','ls','-aq','--filter','name=^/'+name+'$'],target,seconds=15,maximum=1024)
   if target.read_bytes().strip():run(['/usr/bin/docker','rm','--force','--volumes',name],report/'probe-cleanup.log',seconds=30)
  except BaseException as cleanup:
   if primary is not None:raise primary from cleanup
   raise
 text=read(report/'runtime.log',65536).decode();contract=runtime_contract(text);save(report/'runtime-contract.json',contract)
 (report/'apk-packages.txt').write_text('\n'.join(text.splitlines()[5:])+'\n')
 save(root/'build.json',{'schema':'redis7412_supported_alpine_build/1','image_id':image,'started_at':started,'source':source,'runtime':contract,'security_accepted':False,'production_activated':False})
 with Path(os.environ['GITHUB_OUTPUT']).open('a')as f:f.write('image_id='+image+'\n')
 print('Built supported Redis7.4.12 image; security and deployment acceptance remain false.')

def finalize(root):
 source=inputs(root);report=root/'reports';built=json.loads(read(root/'build.json',4*1024**2));need(built['source']==source,'CI_source_changed');image=built['image_id']
 raw=json.loads(read(report/'vulnerabilities.json',16*1024**2));sbom=json.loads(read(report/'sbom.cdx.json',16*1024**2));counts=assess(raw,sbom,image,built['started_at'])
 # No receipt-size limit is inherited into these ordinary large-file commands.
 archive=root/'candidate-image.tar';compressed=root/'candidate-image.tar.gz'
 run(['/usr/bin/docker','image','save','--output',str(archive),image],report/'export.log',seconds=300,maximum=MAX_IMAGE)
 need(archive.stat().st_size<MAX_IMAGE,'image_output_bound');before=pin(archive,MAX_IMAGE)
 run(['/usr/bin/gzip','--no-name','--stdout',str(archive)],compressed,seconds=300,maximum=MAX_GZIP,stderr=report/'gzip.log');after=pin(compressed,MAX_GZIP)
 save(root/'image-export.json',{'schema':'redis7412_supported_alpine_candidate/1','status':'exported_not_accepted','image_id':image,'release_commit':source['commit'],'run_id':source['run_id'],'run_attempt':source['run_attempt'],'source':source,'runtime':built['runtime'],'raw_counts':counts,'OS_supported_Alpine324':True,'reports':{name:pin(report/name,16*1024**2)for name in('vulnerabilities.json','sbom.cdx.json','image.json','runtime-contract.json','apk-packages.txt')},'archive':{'name':compressed.name,'format':'docker-archive+gzip',**after,'uncompressed_bytes':before['bytes'],'uncompressed_sha256':before['sha256']},'real_site_data_tested':False,'security_accepted':False,'production_activated':False})
 archive.unlink();print('Same-image raw scan/SBOM and supportedOS passed; genuine candidate exported, not activated.')

def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('phase',choices=('build','finalize'));p.add_argument('--root',type=Path,required=True);a=p.parse_args();os.umask(0o077)
 {'build':build,'finalize':finalize}[a.phase](a.root)

if __name__=='__main__':main()
