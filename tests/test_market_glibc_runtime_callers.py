"""Explicit runtime call wires: public AST and owned UNIT DTO/FDs only.

No image, package, business81, hosted identity or activation is qualified here.
Only transport/hosted seams and unit GLIBC pins are mocked; the real record,
image, source-interface and actual_business81 bodies execute unchanged.
"""
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch
import ast, inspect, json, os, tempfile, unittest
ROOT=Path(__file__).resolve().parents[1]
def module(relative):
 p=ROOT/relative;m=ModuleType('owned_runtime_wire_'+p.stem);m.__file__=str(p);exec(compile(p.read_bytes(),str(p),'exec'),m.__dict__);return m
REQUIRED={'image_contract':(4,'glibc_runtime'),'receipt_contract':(5,'glibc_runtime'),'controller_parcel_inputs':(4,'glibc_runtime'),'controller_parcel':(None,'glibc_runtime'),'actual_business81':(None,'glibc_runtime'),'O.produce':(None,'glibc_runtime'),'glibc_build_argv':(3,'runtime'),'reopen_glibc_runtime':(1,'record'),'glibc_runtime_record':(0,'value')}
EXPECTED=[('scripts/prepare_market_capture2_image.py', 'image_contract', 'glibc_runtime_record', 'glibc_runtime'), ('scripts/prepare_market_capture2_image.py', 'receipt_contract', 'glibc_runtime_record', 'glibc_runtime'), ('scripts/prepare_market_capture2_image.py', 'actual_business81', 'glibc_runtime_record', 'glibc_runtime'), ('scripts/prepare_market_capture2_image.py', 'actual_business81', 'O.produce', 'glibc_runtime'), ('scripts/prepare_market_capture2_image.py', 'controller_parcel_inputs', 'glibc_runtime_record', 'glibc_runtime'), ('scripts/prepare_market_capture2_image.py', 'controller_parcel', 'controller_parcel_inputs', 'glibc_runtime'), ('scripts/prepare_market_capture2_image.py', 'RuntimeCapsule.record', 'glibc_runtime_record', "{'schema': 'normal_market_glibc_runtime_context/1', 'Root_qualification': dict(GLIBC_ROOT_PIN), 'named_context': document(encoded(GLIBC_NAMED)), 'context_signature': sha256(encoded(GLIBC_NAMED)).hexdigest(), 'custody': self.custody()}"), ('scripts/prepare_market_capture2_image.py', 'reopen_glibc_runtime', 'glibc_runtime_record', "record['controller_inputs']['glibc_runtime']"), ('scripts/prepare_market_capture2_image.py', 'reopen_glibc_runtime', 'receipt_contract', 'value'), ('scripts/prepare_market_capture2_image.py', 'glibc_build_argv', 'glibc_runtime_record', 'runtime'), ('scripts/prepare_market_capture2_image.py', 'normal_build', 'receipt_contract', 'runtime'), ('scripts/prepare_market_capture2_image.py', 'normal_build', 'glibc_build_argv', 'runtime'), ('scripts/prepare_market_capture2_image.py', 'normal_build', 'image_contract', 'runtime'), ('scripts/prepare_market_capture2_image.py', 'normal_build', 'actual_business81', 'runtime'), ('scripts/prepare_market_capture2_image.py', 'normal_build', 'controller_parcel', 'runtime'), ('scripts/prepare_market_capture2_image.py', 'normal_build', 'image_contract', 'runtime'), ('scripts/prepare_market_capture2_image.py', 'finalize', 'reopen_glibc_runtime', 'record'), ('scripts/prepare_market_capture2_image.py', 'finalize', 'receipt_contract', 'runtime'), ('scripts/prepare_market_capture2_image.py', 'finalize.inspect', 'image_contract', 'runtime'), ('scripts/prepare_market_capture2_image.py', 'raw_gate', 'reopen_glibc_runtime', 'record'), ('scripts/prepare_market_capture2_image.py', 'complete_evidence', 'reopen_glibc_runtime', 'record'), ('scripts/prepare_market_capture2_image.py', 'complete_evidence', 'receipt_contract', 'runtime'), ('scripts/prepare_market_capture2_image.py', 'complete_evidence', 'controller_parcel_inputs', 'runtime'), ('scripts/market_business81_outer.py', 'produce', 'M.glibc_runtime_record', 'glibc_runtime'), ('scripts/market_business81_outer.py', 'produce.image_frame', 'M.image_contract', 'glibc_runtime')]
def inventory(sources):
 rows=[]
 for relative,raw in sorted(sources.items()):
  class Visitor(ast.NodeVisitor):
   def __init__(self):self.stack=[]
   def visit_ClassDef(self,n):self.stack.append(n.name);self.generic_visit(n);self.stack.pop()
   def visit_FunctionDef(self,n):self.stack.append(n.name);self.generic_visit(n);self.stack.pop()
   def visit_AsyncFunctionDef(self,n):self.visit_FunctionDef(n)
   def visit_Call(self,n):
    target=ast.unparse(n.func);key=target if target=='O.produce'else n.func.id if isinstance(n.func,ast.Name)else n.func.attr if isinstance(n.func,ast.Attribute)else None
    if key in REQUIRED:
     index,keyword=REQUIRED[key];values=[x.value for x in n.keywords if x.arg==keyword]
     if index is not None and len(n.args)>index:values.append(n.args[index])
     if len(values)!=1 or isinstance(values[0],ast.Constant)and values[0].value is None:raise RuntimeError('missing_required_glibc_context')
     rows.append((relative,'.'.join(self.stack),target,ast.unparse(values[0])))
    self.generic_visit(n)
  Visitor().visit(ast.parse(raw))
 return rows
class ObservedWire(RuntimeError):pass
class RuntimeCallTests(unittest.TestCase):
 def setUp(self):
  self.M=module('scripts/prepare_market_capture2_image.py');self.O=module('scripts/market_business81_outer.py');self.B=module('scripts/market_business81.py');self.F=module('tests/market_security_source_fixtures.py');self.tmp=tempfile.TemporaryDirectory(prefix='.owned-explicit-runtime-wire-',dir=ROOT/'tests');self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
  self.runtime=self.F.owned_glibc_runtime(self,self.M,self.root);self.cap=self.M.RuntimeCapsule(self.root/'glibc-capsule',prior=self.runtime['custody']);self.addCleanup(self.cap.close);self.runtime=self.cap.record()
  self.receipt={'schema':'normal_market_git_component_inputs/1','release_commit':'a'*40,'release_tree':'b'*40,'business_commit':self.B.BUSINESS_COMMIT,'business_tree':self.B.BUSINESS_TREE,'accepted_manifest_sha256':self.M.MANIFEST_SHA}
  self.signature=sha256(self.M.encoded(self.receipt)).hexdigest();self.csig='f'*64;self.image='sha256:'+'1'*64;self.config=self.image_config()
 def image_config(self):
  M=self.M;r=self.receipt;labels={'org.opencontainers.image.revision':r['release_commit'],'io.gold-trade.release.tree':r['release_tree'],'io.gold-trade.release.input-signature':self.signature,'io.gold-trade.release.controller-input-signature':self.csig,'io.gold-trade.business.source-revision':r['business_commit'],'io.gold-trade.business.source-tree':r['business_tree'],'io.gold-trade.business.source-manifest':M.MANIFEST_SHA,'io.gold-trade.glibc.named-context':self.runtime['context_signature'],'io.gold-trade.glibc.Root-qualification':M.GLIBC_ROOT_PIN['sha256']}
  return {'Id':self.image,'Os':'linux','Architecture':'amd64','Config':{'User':'10001:10001','WorkingDir':'/app','Entrypoint':['python','-m','core.market_intelligence.private_pipeline_foundation'],'Cmd':['service','--role','market-processor'],'Labels':labels,'Env':['PUBLIC_UNIT_DEFAULT_ONLY=1']},'RootFS':{'Type':'layers','Layers':['sha256:'+'2'*64]}}
 def frame(self,source,runtime):
  tree=ast.parse(source);fn=next(n for n in ast.walk(tree)if isinstance(n,ast.FunctionDef)and n.name=='image_frame');namespace={'M':self.M,'image':self.image,'receipt':self.receipt,'signature':self.signature,'controller_signature':self.csig,'glibc_runtime':runtime,'need':self.O.need,'cli':lambda *args,**kw:self.M.encoded(self.config)};exec(compile(ast.fix_missing_locations(ast.Module(body=[deepcopy(fn)],type_ignores=[])),self.O.__file__,'exec'),namespace);return namespace['image_frame']()
 def producer_prefix(self):
  tree=ast.parse(Path(self.O.__file__).read_bytes());fn=deepcopy(next(n for n in tree.body if isinstance(n,ast.FunctionDef)and n.name=='produce'));cut=next(i for i,n in enumerate(fn.body)if isinstance(n,ast.Assign)and any(isinstance(t,ast.Name)and t.id=='work'for t in n.targets));fn.body=fn.body[:cut]+[ast.Return(ast.Constant('OWNED_PREFIX_ONLY'))];namespace=dict(self.O.__dict__);exec(compile(ast.fix_missing_locations(ast.Module(body=[fn],type_ignores=[])),self.O.__file__,'exec'),namespace);return namespace['produce']
 def bundle(self):
  M=self.M;directory=self.root/'controller-source';raws={};controllers=[]
  for name in(M.BUSINESS,M.OUTER,M.SELF):
   raw=(ROOT/name).read_bytes();p=directory/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(raw);p.chmod(0o644);raws[name]=raw;controllers.append({'path':name,'bytes':len(raw),'sha256':sha256(raw).hexdigest()})
  B=M.module(raws[M.BUSINESS],directory/M.BUSINESS);self.O=M.module(raws[M.OUTER],directory/M.OUTER);manifest={'image_id':self.image,'release_commit':self.receipt['release_commit'],'two_git_input_signature':self.signature};contents={'suite-manifest.json':B.encode(manifest)};plans={n:{}for n in self.O.PROFILES};paths=M.business81_source_paths(self.root,controllers);return B,raws,controllers,contents,manifest,plans,paths
 def test_closed_all_production_context_callers_and_actual_function_parameter_positions(self):
  sources={str(p.relative_to(ROOT)):p.read_bytes()for p in(ROOT/'scripts').rglob('*.py')}
  self.assertCountEqual(inventory(sources),EXPECTED)
  for name,(index,keyword)in REQUIRED.items():
   if name=='O.produce':self.assertIn(keyword,inspect.signature(self.O.produce).parameters);continue
   parameters=list(inspect.signature(getattr(self.M,name)).parameters)
   if index is not None:self.assertEqual(parameters[index],keyword)
   else:self.assertIn(keyword,parameters)
 def test_semantic_inventory_refuses_omissions_even_when_python_defaultNone_accepts_the_arity(self):
  sources={name:(ROOT/name).read_bytes()for name in('scripts/prepare_market_capture2_image.py','scripts/market_business81_outer.py')};raw=sources['scripts/market_business81_outer.py'];needle=b'M.image_contract(actual,receipt,signature,controller_signature,glibc_runtime)';self.assertEqual(raw.count(needle),1)
  for replacement in(b'M.image_contract(actual,receipt,signature,controller_signature)',b'M.image_contract(actual,receipt,signature,controller_signature,None)'):
   changed={**sources,'scripts/market_business81_outer.py':raw.replace(needle,replacement)}
   with self.assertRaisesRegex(RuntimeError,'^missing_required_glibc_context$'):inventory(changed)
  self.assertIsNone(inspect.signature(self.M.image_contract).parameters['glibc_runtime'].default)
 def test_closed_inventory_refuses_extra_production_caller_or_reconstructed_context(self):
  sources={name:(ROOT/name).read_bytes()for name in('scripts/prepare_market_capture2_image.py','scripts/market_business81_outer.py')};extra={**sources,'scripts/OWNED_EXTRA.py':b'def caller(runtime):\n image_contract(a,b,c,d,runtime)\n'};self.assertNotEqual(sorted(inventory(extra)),sorted(EXPECTED));raw=sources['scripts/market_business81_outer.py'];changed={**sources,'scripts/market_business81_outer.py':raw.replace(b'controller_signature,glibc_runtime)',b'controller_signature,dict(glibc_runtime))')};self.assertNotEqual(sorted(inventory(changed)),sorted(EXPECTED))
 def test_actual_unchanged_image_body_accepts_exact_record_and_rejects_missing_None_wrong_binding(self):
  M=self.M;self.cap.check();self.assertEqual(M.image_contract(self.config,self.receipt,self.signature,self.csig,self.runtime)['image_id'],self.image)
  for runtime in(None,{},{**self.runtime,'context_signature':'0'*64}):
   with self.assertRaisesRegex(RuntimeError,'^glibc_runtime_record$'):M.image_contract(self.config,self.receipt,self.signature,self.csig,runtime)
  changed=deepcopy(self.runtime);next(iter(changed['custody']['files'].values()))['binding']['sha256']='0'*64
  with self.assertRaisesRegex(RuntimeError,'^glibc_runtime_custody$'):M.image_contract(self.config,self.receipt,self.signature,self.csig,changed)
 def test_current_outer_frame_real_image_body_accepts_same_runtime_old_call_omission_refuses(self):
  current=Path(self.O.__file__).read_bytes();frame,inherited=self.frame(current,self.runtime);self.assertEqual(frame['expected_labels']['io.gold-trade.glibc.named-context'],self.runtime['context_signature']);self.assertEqual(inherited,['PUBLIC_UNIT_DEFAULT_ONLY=1'])
  old=current.replace(b'M.image_contract(actual,receipt,signature,controller_signature,glibc_runtime)',b'M.image_contract(actual,receipt,signature,controller_signature)');self.assertNotEqual(current,old)
  with self.assertRaisesRegex(RuntimeError,'^glibc_runtime_record$'):self.frame(old,self.runtime)
 def test_new_actual_and_producer_boundaries_require_explicit_keyword_context(self):
  for function in(self.M.actual_business81,self.O.produce):p=inspect.signature(function).parameters['glibc_runtime'];self.assertEqual(p.kind,inspect.Parameter.KEYWORD_ONLY);self.assertIs(p.default,inspect.Parameter.empty)
  with self.assertRaisesRegex(TypeError,'glibc_runtime'):self.M.actual_business81(self.root,{},[],self.receipt,self.signature,self.csig,self.image,self.root,self.cap.check)
  with self.assertRaisesRegex(TypeError,'glibc_runtime'):self.O.produce(self.M,self.B,root=self.root,repo=ROOT,image=self.image,receipt=self.receipt,signature=self.signature,controller_signature=self.csig,contents={},manifest={},plans={},source_paths={},approval_paths={},guard=self.cap.check)
 def test_actual_entry_wrong_runtime_refuses_before_bundle_or_any_spawn(self):
  M=self.M
  for runtime in(None,{},deepcopy(self.runtime)):
   if runtime not in(None,{}):runtime['custody']['root']=str(self.root/'OWNED_WRONG_ROOT')
   with patch.object(M,'historical81_sources',side_effect=AssertionError('No bundle may be admitted')),patch.object(M.subprocess,'Popen',side_effect=AssertionError('No process')):
    with self.assertRaisesRegex(RuntimeError,'^glibc_runtime_(record|custody)$'):M.actual_business81(self.root,{},[],self.receipt,self.signature,self.csig,self.image,self.root,lambda:None,glibc_runtime=runtime)
 def test_actual_normal_statement_actual_entry_and_actual_producer_prefix_frame_preserve_same_object(self):
  M=self.M;B,raws,controllers,contents,manifest,plans,paths=self.bundle();prefix=self.producer_prefix();observed=[]
  def producer(api,business,**kwargs):
   self.assertIs(kwargs['glibc_runtime'],self.runtime);self.assertIs(api.glibc_runtime_record(kwargs['glibc_runtime'],self.root),self.runtime);self.assertEqual(prefix(api,business,**kwargs),'OWNED_PREFIX_ONLY');frame,_=self.frame(Path(self.O.__file__).read_bytes(),kwargs['glibc_runtime']);observed.append(frame);raise ObservedWire('OWNED_STOP_AFTER_REAL_IMAGE_CONTRACT')
  tree=ast.parse(Path(M.__file__).read_bytes());normal=next(n for n in tree.body if isinstance(n,ast.FunctionDef)and n.name=='normal_build');statement=next(n for n in ast.walk(normal)if isinstance(n,ast.Assign)and any(isinstance(t,ast.Name)and t.id=='actual81'for t in n.targets));namespace={'root':self.root,'raws':raws,'controllers':controllers,'receipt':self.receipt,'signature':self.signature,'controller_signature':self.csig,'image':self.image,'context':self.root/'sealed/release','check':self.cap.check,'runtime':self.runtime,'actual_business81':M.actual_business81}
  with patch.object(M,'historical81_sources',return_value=(B,SimpleNamespace(produce=producer),contents,manifest,plans)),patch.object(M,'hosted',return_value=None),patch.dict(M.os.environ,{'GITHUB_WORKSPACE':str(ROOT)}),patch.object(M.subprocess,'Popen',side_effect=AssertionError('No process')):
   with self.assertRaisesRegex(ObservedWire,'^OWNED_STOP_AFTER_REAL_IMAGE_CONTRACT$'):exec(compile(ast.fix_missing_locations(ast.Module(body=[deepcopy(statement)],type_ignores=[])),M.__file__,'exec'),namespace)
  self.assertEqual(len(observed),1);self.cap.check();self.assertNotIn('actual81',namespace)
 def test_source_preflight_rejects_optional_or_missing_new_context_interface_without_spawns(self):
  M=self.M;B,raws,controllers,*_=self.bundle();api=ModuleType('_owned_interface');api.__dict__.update(M.__dict__);paths=M.business81_source_paths(self.root,controllers);O=M.module(raws[M.OUTER],self.root/'controller-source'/M.OUTER)
  self.assertIsNone(O.source_contract(api,B,paths))
  for bad in(lambda *args,glibc_runtime=None:None,lambda *args:None):
   api.actual_business81=bad
   with patch.object(M.subprocess,'Popen',side_effect=AssertionError('No process')),self.assertRaisesRegex(RuntimeError,'^business81_source$'):O.source_contract(api,B,paths)
if __name__=='__main__':unittest.main(verbosity=2)
