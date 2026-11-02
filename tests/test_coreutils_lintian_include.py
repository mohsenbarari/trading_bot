"""Owned SOURCE/argument QA; never install any package into the host.

The dpkg-deb child only parses our tiny, script-free local archive.  The ordered
pattern check consumes the exact pinned historical slim policy; it is not evidence
of a genuine dpkg install or of a future artifact/image being admitted.
"""
from fnmatch import fnmatchcase
from hashlib import sha256
from pathlib import Path
import ast, importlib.util, io, json, os, re, selectors, subprocess, tarfile, tempfile, time, unittest

ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/'scripts/prepare_coreutils_acl_os_cache.py'
LEAF='--path-include=/usr/share/lintian/overrides/coreutils'
OLD_SHA='756ce091cdbb61fc8f18a3d159d75ececaa15f38b4846019151483884ce223c2'
POLICY=ROOT/'deploy/maintenance/app_6de_smoke/dpkg-docker-policy.original.txt'
CACHE03_JOIN="   and{key:cli.get('actual_installed_candidate_binaries',{}).get(name,{}).get(key)for key in('bytes','sha256')}==row['candidate_binary'],'archive_contract')"
CACHE04_JOIN="   and type(cli.get('actual_installed_candidate_binaries'))is dict and set(cli['actual_installed_candidate_binaries'])=={'cp','mv','install'}\n   and type(cli['actual_installed_candidate_binaries'][name])is dict\n   and cli['actual_installed_candidate_binaries'][name]=={'path':row['path'],'sha256':row['candidate_binary']['sha256']},'archive_contract')"

def cache03_bytes():
 # Historical reconstruction is allowed only after authenticating the REAL
 # current companion lock through the complete production reader/validator.
 with M.Held()as held:actual=M.load_lock(held)
 if M.LOCK_SHA!='987cb4cea875bcf103c5f85a53b5e990bf646fd55cf3f26b2f832c7d217765b6'or actual['armed']is not True:raise AssertionError('actual Root lock stage required')
 spec=importlib.util.spec_from_file_location('owned_historical_coreutils_fixture',ROOT/'tests/market_security_source_fixtures.py');fixture=importlib.util.module_from_spec(spec);spec.loader.exec_module(fixture)
 raw=fixture.historical_gold1_coreutils_helper(ROOT);armed=b"LOCK_SHA='c9f287289d6bdcec280b0e49bb615f4df01856bc21b84ca2398543b0e57484d6'";historical=b"LOCK_SHA='77c9f3af5db1492c5cabec52c3791c61bc6f44e92ba419ec50bfa52844176803'"
 if raw.count(armed)!=1:raise AssertionError('actual Root lock pin source required')
 # Reverse only the explicitly reviewed closed-diagnostic function/catch.
 # Real companion-lock authentication above precedes historical reconstruction.
 text=raw.decode();tree=ast.parse(text);diagnostic=next(n for n in tree.body if isinstance(n,ast.FunctionDef)and n.name=='failure');segment=ast.get_source_segment(text,diagnostic)
 if sha256(segment.encode()).hexdigest()!='c3128006dac819aec7932337c0c4f3662591604d5be801eb06ec112b1f6cb7a0':raise AssertionError('reviewed closed diagnostics source required')
 text=text.replace(segment+'\n\n','',1);current="  result=failure(error);print(json.dumps(result,sort_keys=True));return 1";prior="  code=error.args[0] if type(error) is RuntimeError and len(error.args)==1 and type(error.args[0]) is str and error.args[0] in CODES else 'unexpected_refusal'\n  result={'status':'refused','code':code,'security_accepted':False};print(json.dumps(result,sort_keys=True));return 1"
 if text.count(current)!=1:raise AssertionError('reviewed closed diagnostic catch required')
 raw=text.replace(current,prior).encode()
 return raw.replace(armed,historical).replace(CACHE04_JOIN.encode(),CACHE03_JOIN.encode())

POLICY_PIN=(3501,'050ba34b74480e1cc00dd774153e729e76553b0af595e0d517fddc95b855d4ce')
spec=importlib.util.spec_from_file_location('coreutils_cache_leaf_source',SOURCE)
M=importlib.util.module_from_spec(spec);spec.loader.exec_module(M)

def tar_bytes(rows):
 out=io.BytesIO()
 with tarfile.open(fileobj=out,mode='w',format=tarfile.USTAR_FORMAT)as archive:
  for name,body in rows:
   info=tarfile.TarInfo(name);info.uid=info.gid=info.mtime=0
   info.uname=info.gname='';info.mode=0o644;info.size=len(body)
   archive.addfile(info,io.BytesIO(body))
 return out.getvalue()

def owned_deb():
 control=tar_bytes([('./control',b'Package: owned-path-policy-fixture\nVersion: 1\nArchitecture: all\nMaintainer: Owned Fixture <fixture@example.invalid>\nDescription: inert source-only path policy fixture\n')])
 payload=tar_bytes([('./usr/share/lintian/overrides/coreutils',b'owned exact leaf\n'),
  ('./usr/share/lintian/overrides/unrelated-package',b'must stay excluded\n'),
  ('./usr/share/lintian/overrides/coreutils.extra',b'must stay excluded\n'),
  ('./usr/share/doc/coreutils/README',b'owned documentation\n')])
 out=bytearray(b'!<arch>\n')
 for name,body in [('debian-binary',b'2.0\n'),('control.tar',control),('data.tar',payload)]:
  out.extend((name+'/').ljust(16).encode()+b'0'.ljust(12)+b'0'.ljust(6)+b'0'.ljust(6)+b'100644'.ljust(8)+str(len(body)).ljust(10).encode()+b'`\n')
  out.extend(body)
  if len(body)%2:out.extend(b'\n')
 return bytes(out),payload

def bounded_parse_deb(path,home):
 # This fixed read-only dpkg-deb operation does not run dpkg's global status
 # logger, package maintainer scripts, triggers, or package installation.
 child=subprocess.Popen(['/usr/bin/dpkg-deb','--fsys-tarfile',str(path)],
  stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,
  close_fds=True,start_new_session=True,
  env={'PATH':'/usr/bin:/bin','LC_ALL':'C','HOME':str(home)})
 parts=[];size=0;end=time.monotonic()+10
 try:
  with selectors.DefaultSelector()as sel:
   os.set_blocking(child.stdout.fileno(),False);sel.register(child.stdout,selectors.EVENT_READ)
   while True:
    left=end-time.monotonic()
    if left<=0:raise AssertionError('owned dpkg-deb deadline')
    if not sel.select(min(.2,left)):continue
    block=os.read(child.stdout.fileno(),min(4096,32768-size+1))
    if not block:break
    if len(block)>32768-size:raise AssertionError('owned dpkg-deb output bound')
    size+=len(block);parts.append(block)
  if child.wait(timeout=3)!=0:raise AssertionError('owned dpkg-deb failed')
 finally:
  if child.poll()is None:child.kill();child.wait(timeout=3)
  child.stdout.close()
 return b''.join(parts)

def admitted(path,rules):
 """Ordered fixed-pattern model only, not a complete dpkg implementation."""
 result=True
 for include,pattern in rules:
  if fnmatchcase(path,pattern):result=include
 return result

def authentic_policy_rules():
 raw=POLICY.read_bytes()
 if (len(raw),sha256(raw).hexdigest())!=POLICY_PIN:raise AssertionError('pinned historical slim policy changed')
 rows=[]
 for line in raw.decode('ascii').splitlines():
  if not line or line.startswith('#'):continue
  kind,pattern=line.split(' ',1)
  if kind not in('path-exclude','path-include')or not pattern.startswith('/usr/share/'):raise AssertionError('policy source shape')
  rows.append((kind=='path-include',pattern))
 return rows

class LintianLeafTests(unittest.TestCase):
 def test_exact_historical_policy_pin_and_genuine_lintian_exclusion(self):
  rows=authentic_policy_rules()
  self.assertEqual(len(rows),18);self.assertEqual(sum(not include for include,_ in rows),9)
  self.assertIn((False,'/usr/share/lintian/overrides/*'),rows)
  self.assertNotIn((False,'/usr/share/lintian/*'),rows)
  self.assertIn((True,'/usr/share/doc/*/copyright'),rows)

 def test_entire_helper_delta_is_one_exact_appended_argument(self):
  raw=cache03_bytes();extra=b"+('--path-include=/usr/share/lintian/overrides/coreutils',)"
  self.assertEqual(raw.count(extra),1)
  raw=raw.replace(b"['acl_delete_def_file_at','acl_set_file_at']",b"['acl_delete_def_file_at','acl_get_file_at','acl_set_file_at']");self.assertEqual(sha256(raw.replace(extra,b'')).hexdigest(),OLD_SHA)

 def test_exact_leaf_only_and_all_existing_includes_preserved(self):
  original=tuple(flag for root in('/usr/share/doc/coreutils','/usr/share/locale','/usr/share/man','/usr/share/info')for flag in('--path-include='+root,'--path-include='+root+'/*'))
  self.assertEqual(M.DOC_INCLUDES,original+(LEAF,))
  self.assertEqual([s for s in M.DOC_INCLUDES if 'lintian'in s],[LEAF])

 def test_allowed_install_has_same_exact_shared_tuple(self):
  path=M.CAPSULE/M.PACKAGE;command=('/usr/bin/dpkg',*M.DOC_INCLUDES,'--install',str(path))
  self.assertIn(command,M.commands(path))
  self.assertEqual([row for row in M.commands(path)if '--install'in row],[command])

 def test_actual_installer_and_allowlist_use_the_shared_tuple(self):
  tree=ast.parse(SOURCE.read_text());funcs={n.name:n for n in tree.body if isinstance(n,ast.FunctionDef)}
  for name in('commands','install_admitted'):
   relevant=[n for n in ast.walk(funcs[name])if isinstance(n,(ast.List,ast.Tuple))and any(isinstance(e,ast.Constant)and e.value=='--install'for e in n.elts)]
   self.assertEqual(len(relevant),1,name)
   self.assertEqual([e.value.id for e in relevant[0].elts if isinstance(e,ast.Starred)and isinstance(e.value,ast.Name)],['DOC_INCLUDES'],name)

 def test_broader_lintian_arguments_are_rejected_before_any_child(self):
  path=M.CAPSULE/M.PACKAGE;allowed=M.commands(path)
  for forbidden in('--path-include=/usr/share/lintian/*','--path-include=/usr/share/lintian/overrides/*','--path-include=/usr/share/lintian/overrides/coreutils*'):
   command=['/usr/bin/dpkg',*M.DOC_INCLUDES[:-1],forbidden,'--install',str(path)]
   with self.assertRaisesRegex(RuntimeError,'^installer_command$'):M.bounded(command,allowed)

 def test_full_payload_verification_is_still_mandatory(self):
  tree=ast.parse(SOURCE.read_text());f=next(n for n in tree.body if isinstance(n,ast.FunctionDef)and n.name=='install_admitted')
  calls=[n.func.id for n in ast.walk(f)if isinstance(n,ast.Call)and isinstance(n.func,ast.Name)]
  self.assertIn('verify_coreutils_payload',calls);self.assertIn('verify_approved_libacl_payload',calls)
  self.assertIn('verify_transition',calls)
  self.assertEqual(calls.count('verify_approved_libacl_payload'),2)

 def test_genuine_readonly_dpkg_deb_fixture_and_exact_policy_leaf(self):
  raw,expected=owned_deb();self.assertLess(len(raw),32768)
  with tempfile.TemporaryDirectory(prefix='owned-coreutils-leaf-')as temp:
   root=Path(temp);path=root/'fixture.deb';path.write_bytes(raw)
   actual=bounded_parse_deb(path,root);self.assertEqual(actual,expected)
   self.assertEqual(path.read_bytes(),raw)
   self.assertEqual({p.name for p in root.iterdir()},{'fixture.deb'})
  with tarfile.open(fileobj=io.BytesIO(actual),mode='r:')as archive:
   rows=archive.getmembers();self.assertTrue(all(x.isfile()and x.uid==x.gid==0 for x in rows))
   paths=['/'+x.name.removeprefix('./')for x in rows]
  exclusion=authentic_policy_rules()
  rules=exclusion+[(True,x.partition('=')[2])for x in M.DOC_INCLUDES]
  leaf='/usr/share/lintian/overrides/coreutils'
  self.assertFalse(admitted(leaf,exclusion));self.assertTrue(admitted(leaf,rules))
  self.assertEqual([p for p in paths if p.startswith('/usr/share/lintian/')and admitted(p,rules)],[leaf])
  self.assertTrue(admitted('/usr/share/doc/coreutils/README',rules))

 def test_rule_order_cannot_be_misrepresented(self):
  leaf='/usr/share/lintian/overrides/coreutils'
  self.assertFalse(admitted(leaf,[(True,leaf),(False,'/usr/share/lintian/overrides/*')]))
  self.assertTrue(admitted(leaf,[(False,'/usr/share/lintian/overrides/*'),(True,leaf)]))

class RuntimeImportContractTests(unittest.TestCase):
 def caller_guard(self):
  tree=ast.parse(SOURCE.read_bytes());fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef)and n.name=='admit_archive');loop=next(n for n in fn.body if isinstance(n,ast.For)and ast.unparse(n.iter)=="abi['callers'].items()")
  self.assertEqual(len(loop.body),1);return compile(ast.Module(body=loop.body,type_ignores=[]),'<actual per-caller ABI archive guard>','exec')
 def fixture(self,name='cp'):
  binary={'bytes':100,'sha256':'a'*64};library={'path':'/usr/lib/x86_64-linux-gnu/libacl.so.1','bytes':100,'sha256':'b'*64};required=['acl_delete_def_file_at','acl_set_file_at']
  row={'path':'/usr/bin/'+name,'candidate_binary':binary,'libacl':library,'dynamic_approved_libacl_not_static_sdk':True,'actual_ldd_acl_target':library['path'],'security_accepted':False,'required_private_at_imports':required,'actual_versioned_acl_bindings':{k:'ACL_2.4'for k in required}}
  return row,{'name':name,'library':library,'lock':{'installed_callers':{name:binary}},'cli':{'actual_installed_candidate_binaries':{n:{'path':'/usr/bin/'+n,'sha256':binary['sha256']}for n in('cp','mv','install')}},'need':M.need}
 def test_only_source_proven_runtime_imports_required_for_each_caller(self):
  raw=(ROOT/'tests/fixtures/coreutils_gold_acl_fd.c').read_bytes();self.assertEqual((len(raw),sha256(raw).hexdigest()),(4689,'8ab8712ac655a8e26cbffad2f72561f0eb487da482329f0daf945427e6aa13d3'))
  runtime=set(re.findall(rb'\b(acl_[A-Za-z0-9_]+_at)\s*\(',raw));self.assertEqual(runtime,{b'acl_set_file_at',b'acl_delete_def_file_at'});self.assertNotIn(b'acl_get_file_at',raw)
  for name in('cp','mv','install'):
   row,env=self.fixture(name);self.assertEqual([v.encode()for v in row['required_private_at_imports']],sorted(runtime));exec(self.caller_guard(),{**env,'row':row})
 def test_missing_extra_configure_GET_or_binding_drift_all_refuse(self):
  row,env=self.fixture();guard=self.caller_guard()
  for required,bindings in [(['acl_set_file_at'],{'acl_set_file_at':'ACL_2.4'}),(['acl_delete_def_file_at'],{'acl_delete_def_file_at':'ACL_2.4'}),(['acl_delete_def_file_at','acl_get_file_at','acl_set_file_at'],{k:'ACL_2.4'for k in('acl_delete_def_file_at','acl_get_file_at','acl_set_file_at')}),(row['required_private_at_imports'],{'acl_set_file_at':'ACL_2.4'}),(row['required_private_at_imports'],{**row['actual_versioned_acl_bindings'],'acl_get_file_at':'ACL_2.4'})]:
   with self.subTest(required=required,bindings=bindings),self.assertRaisesRegex(RuntimeError,'^archive_contract$'):exec(guard,{**env,'row':{**row,'required_private_at_imports':required,'actual_versioned_acl_bindings':bindings}})
 def test_entire_production_change_reverses_to_exact_cache02(self):
  raw=cache03_bytes();old=b"['acl_delete_def_file_at','acl_get_file_at','acl_set_file_at']";new=b"['acl_delete_def_file_at','acl_set_file_at']";self.assertEqual(raw.count(new),1);restored=raw.replace(new,old)
  self.assertEqual((len(restored),sha256(restored).hexdigest()),(36757,'645e0a9378ed989b025d5eea374c91800a149b42265777285766f0b73bae49c5'))

 def actual_proofs(self):
  result={}
  for name,pin in [('ordinary-compatibility.json',(2092,'ed351f313f80dcf3d109fc470f95b0efd0b5223ce921c58b1508a06610daff2c')),('abi-contract.json',(2300,'6d478eb4fea8df234c8aa9d2e9bb0076c3282337884e64eaab9f4f5ebfe7fd26'))]:
   raw=(ROOT/'tests/fixtures'/('coreutils_ci14_'+name)).read_bytes();self.assertEqual((len(raw),sha256(raw).hexdigest()),pin);result[name]=json.loads(raw)
  abi=result['abi-contract.json']['callers'];cli=result['ordinary-compatibility.json']['actual_cli'];installed={name:{'path':row['path'],**row['candidate_binary']}for name,row in abi.items()}
  return abi,cli,{'lock':{'installed_callers':installed},'library':abi['cp']['libacl'],'need':M.need}
 def test_authentic_CI14_CLI_producer_shape_joins_all_three_real_ABI_candidates(self):
  abi,cli,env=self.actual_proofs();guard=self.caller_guard()
  oldtree=ast.parse(cache03_bytes());oldfn=next(n for n in oldtree.body if isinstance(n,ast.FunctionDef)and n.name=='admit_archive');oldloop=next(n for n in oldfn.body if isinstance(n,ast.For)and ast.unparse(n.iter)=="abi['callers'].items()");oldguard=compile(ast.Module(body=oldloop.body,type_ignores=[]),'<cache03 authentic regression>','exec')
  for name,row in abi.items():
   actual=cli['actual_installed_candidate_binaries'][name];self.assertEqual(set(actual),{'path','sha256'});self.assertEqual(actual['path'],row['path']);self.assertEqual(actual['sha256'],row['candidate_binary']['sha256'])
   values={**env,'name':name,'row':row,'cli':cli};exec(guard,values)
   with self.assertRaisesRegex(RuntimeError,'^archive_contract$'):exec(oldguard,values)
 def test_actual_CLI_wrong_path_hash_missing_extra_or_untyped_fields_refuse(self):
  abi,cli,env=self.actual_proofs();guard=self.caller_guard()
  for name,row in abi.items():
   actual=cli['actual_installed_candidate_binaries'][name]
   for bad in({**actual,'path':'/usr/bin/OWNED-wrong'}, {**actual,'sha256':'0'*64}, {'path':actual['path']}, {'sha256':actual['sha256']}, {**actual,'bytes':row['candidate_binary']['bytes']}, {**actual,'unexpected':False}, None, []):
    altered={**cli,'actual_installed_candidate_binaries':{**cli['actual_installed_candidate_binaries'],name:bad}}
    with self.subTest(name=name,bad=bad),self.assertRaisesRegex(RuntimeError,'^archive_contract$'):exec(guard,{**env,'name':name,'row':row,'cli':altered})
  for mapping in(None,[],{k:v for k,v in cli['actual_installed_candidate_binaries'].items()if k!='mv'},{**cli['actual_installed_candidate_binaries'],'unexpected':{}}):
   with self.subTest(mapping=mapping),self.assertRaisesRegex(RuntimeError,'^archive_contract$'):exec(guard,{**env,'name':'cp','row':abi['cp'],'cli':{**cli,'actual_installed_candidate_binaries':mapping}})
 def test_actual_ABI_byte_mismatch_still_refuses_and_complete_cache03_restores(self):
  abi,cli,env=self.actual_proofs();guard=self.caller_guard()
  for name,row in abi.items():
   changed={**row,'candidate_binary':{**row['candidate_binary'],'bytes':row['candidate_binary']['bytes']+1}}
   with self.subTest(name=name),self.assertRaisesRegex(RuntimeError,'^archive_contract$'):exec(guard,{**env,'name':name,'row':changed,'cli':cli})
  current=SOURCE.read_bytes();self.assertEqual(current.count(CACHE04_JOIN.encode()),1);restored=cache03_bytes();self.assertEqual((len(restored),sha256(restored).hexdigest()),(36739,'104329409c2edbf4666f00a752f04a513de49a209443c10b4b247c241ef04c1e'))

if __name__=='__main__':unittest.main()
