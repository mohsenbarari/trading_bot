"""Source and synthetic metadata checks only; never builds, pulls or starts Docker."""
from copy import deepcopy
from datetime import datetime,timezone
from hashlib import sha256
import ast,importlib.util,os,resource,sys,tempfile,time,unittest
from pathlib import Path
from unittest.mock import patch
import yaml
ROOT=Path(__file__).resolve().parents[1];AREA=ROOT/'deploy/security/redis7412-alpine324'
spec=importlib.util.spec_from_file_location('owned_redis7412_recipe',AREA/'build_candidate.py');M=importlib.util.module_from_spec(spec);spec.loader.exec_module(M)
IID='sha256:'+'1'*64

def reports():
 stamp=datetime.now(timezone.utc).isoformat()
 raw={'CreatedAt':stamp,'Metadata':{'ImageID':IID,'OS':{'Family':'alpine','Name':'3.24.1'}},'Results':[{'Vulnerabilities':[]}]}
 sbom={'bomFormat':'CycloneDX','metadata':{'component':{'type':'container','name':IID,'properties':[{'name':'aquasecurity:trivy:ImageID','value':IID}]}}}
 return raw,sbom

class Tests(unittest.TestCase):
 def test_pinned_base_source_checksum_and_same_entrypoint_are_preserved(self):
  dockerfile=(AREA/'Dockerfile').read_text();self.assertTrue(dockerfile.startswith('FROM '+M.BASE+'\n'));self.assertEqual(dockerfile.count('RUN apk upgrade --no-cache'),1)
  self.assertIn('ARG REDIS_DOWNLOAD_SHA='+M.SOURCE_SHA,dockerfile);self.assertIn('echo "$REDIS_DOWNLOAD_SHA *redis.tar.gz" | sha256sum -c -',dockerfile)
  self.assertIn('export BUILD_TLS=yes',dockerfile);self.assertIn('addgroup -S -g 1000 redis',dockerfile);self.assertIn('adduser -S -G redis -u 999 redis',dockerfile)
  self.assertEqual(sha256((AREA/'docker-entrypoint.sh').read_bytes()).hexdigest(),M.ENTRYPOINT);self.assertTrue((AREA/'docker-entrypoint.sh').stat().st_mode&0o111);self.assertIn('test -x /usr/local/bin/docker-entrypoint.sh',(AREA/'build_candidate.py').read_text())
  restored=dockerfile.replace(M.SETPRIV_COMPAT,'',1).replace('FROM '+M.BASE+'\n','FROM alpine:3.21\n',1).replace('\n# Current signed Alpine3.24 security revisions; Redis sources/flags stay official.\nRUN apk upgrade --no-cache\n','',1)
  recipe=__import__('json').loads((AREA/'recipe.json').read_bytes());self.assertEqual(sha256(restored.encode()).hexdigest(),recipe['original_Dockerfile']['sha256'])
 def test_runtime_metadata_requires_actual_supported_OS_exact_redis_and_uid_gid(self):
  rows=['Redis server v=7.4.12 sha=00000000:0 malloc=jemalloc bits=64','3.24.1','999','1000',M.ENTRYPOINT,'musl-1.2.6-r3']
  self.assertEqual(M.runtime_contract('\n'.join(rows))['redis_gid'],1000)
  for index,bad in[(0,'Redis server v=7.4.11'),(1,'3.21.7'),(2,'0'),(3,'999'),(4,'0'*64)]:
   changed=list(rows);changed[index]=bad
   with self.subTest(index=index),self.assertRaisesRegex(RuntimeError,'runtime_contract'):M.runtime_contract('\n'.join(changed))
 def test_actual_same_image_raw_and_SBOM_bindings_and_omitempty_EOSL_are_valid(self):
  raw,sbom=reports();self.assertEqual(M.assess(raw,sbom,IID,time.time()-60)['HIGH'],0)
  raw['Metadata']['OS']['EOSL']=False;M.assess(raw,sbom,IID,time.time()-60)
  raw['Results'][0]['Vulnerabilities']=[{'Severity':'LOW'}];self.assertEqual(M.assess(raw,sbom,IID,time.time()-60)['LOW'],1)
 def test_EOL_cross_image_stale_or_high_critical_cannot_export(self):
  for kind in('EOL','old_OS','image','stale','HIGH','CRITICAL','SBOM','SBOM_property'):
   raw,sbom=reports()
   if kind=='EOL':raw['Metadata']['OS']['EOSL']=True
   elif kind=='old_OS':raw['Metadata']['OS']['Name']='3.21.7'
   elif kind=='image':raw['Metadata']['ImageID']='sha256:'+'2'*64
   elif kind=='stale':raw['CreatedAt']='2000-01-01T00:00:00Z'
   elif kind in('HIGH','CRITICAL'):raw['Results'][0]['Vulnerabilities']=[{'Severity':kind}]
   elif kind=='SBOM':sbom['metadata']['component']['name']='other'
   else:sbom['metadata']['component']['properties']=[]
   with self.subTest(kind=kind),self.assertRaises(RuntimeError):M.assess(raw,sbom,IID,time.time()-60)
 def test_real_child_file_bound_prevents_unbounded_CLI_output(self):
  with tempfile.TemporaryDirectory(dir=ROOT/'tests')as folder:
   root=Path(folder)
   with patch.dict(os.environ,{'HOME':folder,'REDIS_CANDIDATE_DOCKER_CONFIG':folder}),self.assertRaisesRegex(RuntimeError,'command_nonzero|command_output_bound'):
    M.run([sys.executable,'-c','import os; os.write(1,b"x"*8192)'],root/'bounded.log',maximum=4096,seconds=5)
   self.assertLessEqual((root/'bounded.log').stat().st_size,4096)
 def test_large_file_command_uses_its_declared_limit(self):
  with tempfile.TemporaryDirectory(dir=ROOT/'tests')as folder:
   root=Path(folder)
   with patch.dict(os.environ,{'HOME':folder,'REDIS_CANDIDATE_DOCKER_CONFIG':folder}):M.run([sys.executable,'-c','import resource; print(resource.getrlimit(resource.RLIMIT_FSIZE)[0])'],root/'limit.log',maximum=1048576)
   self.assertEqual((root/'limit.log').read_text().strip(),'1048576')
 def test_workflow_main_immutable_checkout_source_only_context_and_pinned_scanner(self):
  value=yaml.safe_load((ROOT/'.github/workflows/redis7412-supported-alpine.yml').read_text());job=value['jobs']['redis7412'];self.assertEqual(job['if'],"github.ref == 'refs/heads/main'")
  checkout=job['steps'][0];self.assertEqual(checkout['with']['ref'],'${{ github.sha }}');self.assertFalse(checkout['with']['persist-credentials'])
  seal=job['steps'][1]['run'];self.assertIn("(root/'empty-trivy.yaml').write_text('{}\\n')",seal);self.assertIn("commit+':'+prefix+name",seal);self.assertIn(".chmod(0o755 if name=='docker-entrypoint.sh' else 0o600)",seal)
  code=seal.split("<<'PY'\n",1)[1].rsplit('\nPY',1)[0];compile(code,'workflow-source','exec');tree=ast.parse(code);patterns=[n.value for n in ast.walk(tree)if isinstance(n,ast.Constant)and isinstance(n.value,bytes)and b'[0-9]'in n.value];self.assertTrue(any(__import__('re').fullmatch(pattern,b'4352\n')for pattern in patterns));loop=next(n for n in tree.body if isinstance(n,ast.For));self.assertEqual(ast.literal_eval(loop.iter),[('Dockerfile','context/Dockerfile'),('docker-entrypoint.sh','context/docker-entrypoint.sh'),('recipe.json','controller/recipe.json'),('build_candidate.py','controller/build_candidate.py')])
  scans=[s for s in job['steps']if s.get('id')in('scan','sbom')];self.assertEqual(len(scans),2)
  self.assertTrue(all(s['uses']=='aquasecurity/trivy-action@ed142fd0673e97e23eac54620cfb913e5ce36c25'for s in scans))
  self.assertEqual(scans[0]['with']['exit-code'],'0');self.assertEqual(scans[0]['with']['ignore-unfixed'],'false')
 def test_host_execution_is_refused_before_any_Docker_call(self):
  with patch.object(M.os,'geteuid',return_value=0),patch.object(M,'run',side_effect=AssertionError('Docker must not run')):
   with self.assertRaisesRegex(RuntimeError,'hosted_Main_only'):M.inputs(Path('/UNUSED'))

 def startup_fixture(self,root,*,bad_uid=False,bad_cleanup=False,missing_entrypoint=False,bad_shutdown=False):
  # Inert CLI wire only. Real authenticated normal-entrypoint execution is
  # required by the hosted build before any scanner/export step can run.
  source={'commit':'a'*40,'run_id':123,'run_attempt':1};cid='c'*64;calls=[]
  def command(argv,log,**kwargs):
   calls.append(argv);name=log.name;value=''
   if name=='startup-create.log':value=cid
   elif name=='startup-config.log':
    value=__import__('json').dumps({'id':cid,'image':IID,'entrypoint':['docker-entrypoint.sh'],
     'cmd':['redis-server','--save','','--appendonly','no','--requirepass',M.PUBLIC_AUTH],'user':''})
   elif name.startswith('startup-ping-'):
    if missing_entrypoint:
     log.write_text('');raise RuntimeError('command_nonzero:'+name)
    value='PONG'
   elif name=='startup-unauthenticated.log':value='UNAUTHENTICATED_REFUSED'
   elif name=='startup-process-identity.log':value='0 0 0 0\n1000 1000 1000 1000'if bad_uid else'999 999 999 999\n1000 1000 1000 1000'
   elif name=='startup-set.log':value='OK'
   elif name=='startup-get.log':value='public-synthetic-fixture'
   elif name=='startup-del.log':value='1'
   elif name=='startup-dbsize.log':value='0'
   elif name=='startup-cleanup-before.log':value=cid
   elif name=='startup-cleanup-identity.log':
    value=__import__('json').dumps({'id':cid,'name':'/redis7412-startup-123-1','image':IID,'owner':'FOREIGN'if bad_cleanup else'source-commit'})
    value=value.replace('source-commit',source['commit'])
   elif name=='startup-exit.log':value='1'if bad_shutdown else'0'
   log.write_text(value+'\n'if value else'')
  return source,calls,command

 def test_normal_entrypoint_gate_requires_real_server_identity_auth_and_owned_shutdown(self):
  with tempfile.TemporaryDirectory()as folder:
   root=Path(folder);(root/'reports').mkdir();source,calls,command=self.startup_fixture(root)
   with patch.object(M,'run',side_effect=command):result=M.normal_entrypoint_startup(root,IID,source)
   self.assertEqual(result['status'],'PASS');self.assertEqual(result['actual_server_uid'],999);self.assertEqual(result['actual_server_gid'],1000)
   self.assertTrue(result['authenticated_ping']and result['unauthenticated_refused']and result['synthetic_set_get_delete']and result['owned_cleanup_proven'])
   self.assertEqual(result['actual_server_shutdown_exit'],0)
   create=next(row for row in calls if row[1]=='create');self.assertNotIn('--entrypoint',create);self.assertIn('redis-server',create)
   self.assertIn(['stop','--timeout','5','c'*64],[row[1:]for row in calls]);self.assertIn(['rm','--force','--volumes','c'*64],[row[1:]for row in calls])
   self.assertFalse(result['production_data_used']);self.assertFalse(result['security_accepted'])

 def test_wrong_actual_server_UID_refuses_and_cleanup_is_still_required(self):
  with tempfile.TemporaryDirectory()as folder:
   root=Path(folder);(root/'reports').mkdir();source,calls,command=self.startup_fixture(root,bad_uid=True)
   with patch.object(M,'run',side_effect=command),self.assertRaisesRegex(RuntimeError,'normal_startup_process_identity'):M.normal_entrypoint_startup(root,IID,source)
   self.assertFalse((root/'reports/normal-entrypoint-startup.json').exists())
   cleanup=__import__('json').loads((root/'reports/normal-entrypoint-cleanup.json').read_bytes());self.assertTrue(cleanup['owned_cleanup_proven']);self.assertTrue(cleanup['primary_failure_retained'])
   self.assertTrue(any(row[1:3]==['rm','--force']for row in calls))

 def test_original_missing_setpriv_startup_cannot_pass_version_only_metadata(self):
  with tempfile.TemporaryDirectory()as folder:
   root=Path(folder);(root/'reports').mkdir();source,calls,command=self.startup_fixture(root,missing_entrypoint=True)
   with patch.object(M,'run',side_effect=command),patch.object(M.time,'sleep'),self.assertRaisesRegex(RuntimeError,'normal_startup_not_ready'):M.normal_entrypoint_startup(root,IID,source)
   self.assertFalse((root/'reports/normal-entrypoint-startup.json').exists());self.assertTrue(any(row[1:3]==['rm','--force']for row in calls))

 def test_actual_nonzero_server_shutdown_cannot_publish_clean_startup_PASS(self):
  with tempfile.TemporaryDirectory()as folder:
   root=Path(folder);(root/'reports').mkdir();source,calls,command=self.startup_fixture(root,bad_shutdown=True)
   with patch.object(M,'run',side_effect=command),self.assertRaisesRegex(RuntimeError,'normal_startup_shutdown_nonzero'):M.normal_entrypoint_startup(root,IID,source)
   self.assertFalse((root/'reports/normal-entrypoint-startup.json').exists());self.assertTrue(any(row[1:3]==['rm','--force']for row in calls))

 def test_foreign_cleanup_identity_is_never_removed_and_primary_error_survives(self):
  with tempfile.TemporaryDirectory()as folder:
   root=Path(folder);(root/'reports').mkdir();source,calls,command=self.startup_fixture(root,bad_uid=True,bad_cleanup=True)
   with patch.object(M,'run',side_effect=command),self.assertRaises(BaseExceptionGroup)as raised:M.normal_entrypoint_startup(root,IID,source)
   self.assertEqual(len(raised.exception.exceptions),2);self.assertFalse(any(row[1]=='rm'for row in calls))
   self.assertFalse(__import__('json').loads((root/'reports/normal-entrypoint-cleanup.json').read_bytes())['owned_cleanup_proven'])

 def test_exact_owned_symlink_is_the_only_additional_official_recipe_delta(self):
  dockerfile=(AREA/'Dockerfile').read_text();recipe=__import__('json').loads((AREA/'recipe.json').read_text());M.recipe_contract(dockerfile,recipe)
  self.assertIn('test -x /bin/setpriv; test ! -e /usr/bin/setpriv; ln -s /bin/setpriv /usr/bin/setpriv',dockerfile)
  with self.assertRaisesRegex(RuntimeError,'recipe_delta'):M.recipe_contract(dockerfile.replace(M.SETPRIV_COMPAT,''),recipe)
  altered=dockerfile+'\nRUN echo unreviewed\n';changed=deepcopy(recipe);changed['Dockerfile']={'bytes':len(altered.encode()),'sha256':sha256(altered.encode()).hexdigest()}
  with self.assertRaisesRegex(RuntimeError,'official_recipe_changed'):M.recipe_contract(altered,changed)

 def test_hosted_build_requires_authenticated_startup_before_scans_and_export(self):
  tree=ast.parse((AREA/'build_candidate.py').read_text());build=next(node for node in tree.body if isinstance(node,ast.FunctionDef)and node.name=='build')
  start=next(node.lineno for node in ast.walk(build)if isinstance(node,ast.Call)and isinstance(node.func,ast.Name)and node.func.id=='normal_entrypoint_startup')
  saved=next(node.lineno for node in ast.walk(build)if isinstance(node,ast.Call)and isinstance(node.func,ast.Name)and node.func.id=='save'and any(isinstance(value,ast.Constant)and value.value=='build.json'for value in ast.walk(node)))
  self.assertLess(start,saved)
  finalize=next(node for node in tree.body if isinstance(node,ast.FunctionDef)and node.name=='finalize')
  text=ast.get_source_segment((AREA/'build_candidate.py').read_text(),finalize);self.assertIn("'normal_startup_required'",text)
  self.assertLess(text.index("'normal_startup_required'"),text.index('counts=assess('))
  steps=yaml.safe_load((ROOT/'.github/workflows/redis7412-supported-alpine.yml').read_text())['jobs']['redis7412']['steps'];ids=[row.get('id')for row in steps]
  self.assertLess(ids.index('build'),ids.index('scan'));self.assertLess(ids.index('build'),ids.index('export'))

if __name__=='__main__':unittest.main()
