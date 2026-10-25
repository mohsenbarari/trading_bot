"""Public synthetic archives and orchestration; no Docker/network/real config."""
from contextlib import ExitStack, redirect_stdout
from copy import deepcopy
from hashlib import sha256
import ast
import importlib.util
import inspect
import io
import json
import os
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import tarfile
import unittest
import warnings
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
DENIED = ('subprocess.run','subprocess.Popen','os.system','os.popen','os.fork','os.kill','os.killpg',
          'socket.socket','socket.create_connection','socket.getaddrinfo','urllib.request.urlopen',
          'resource.setrlimit','signal.signal','signal.alarm')


def fence(stack):
    for target in DENIED: stack.enter_context(patch(target,side_effect=AssertionError('ambient operation denied')))


def load(name, relative, pin=None):
    path = ROOT/relative; raw = path.read_bytes()
    if pin is not None: assert sha256(raw).hexdigest() == pin
    spec = importlib.util.spec_from_file_location(name,path); module = importlib.util.module_from_spec(spec)
    exec(compile(raw,str(path),'exec'),module.__dict__); return module


with ExitStack() as loading:
    fence(loading)
    H = load('_canonical_smoke_fixture','scripts/run_canonical_app_ci_smoke.py')
    OLD = load('_old_app_fixture','scripts/run_app_image_ci_smoke.py',H.OLD_PIN)
    B = load('_builder_fixture','scripts/prepare_canonical_app_image.py',H.BUILDER_PIN)
    D = load('_canonical_contract_fixture',*OLD.HELPERS['datastore_candidate_contract.py'])
    T = load('_canonical_transport_fixture',*OLD.HELPERS['runtime_image_archive_transport.py'])
    L = load('_canonical_moby_fixture',*OLD.HELPERS['admit_security_component_image_reuse.py'])


def digest(raw): return 'sha256:'+sha256(raw).hexdigest()
def blob(raw): return 'blobs/sha256/'+sha256(raw).hexdigest()


class CanonicalSmokeTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack(); self.addCleanup(self.stack.close); fence(self.stack)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix='canonical-public-')))
        self.root.chmod(0o700); self.stack.enter_context(patch.dict(os.environ,{'APP_ENV_FILE':'config/unit-test.env.example',
            'GITHUB_RUN_ID':'12345','GITHUB_RUN_ATTEMPT':'1'},clear=True))
        self.contract = load('_private_canonical_contract',*OLD.HELPERS['datastore_candidate_contract.py'])
        self.contract.legacy_configs = lambda config,moby: H.canonical_legacy_configs(config,moby,self.contract)
        self.ctx = SimpleNamespace(D=self.contract,T=T,L=L,end=time.monotonic()+30)

    def tar(self, rows):
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream,mode='w',format=tarfile.USTAR_FORMAT) as archive:
            for name,raw in rows:
                item = tarfile.TarInfo(name)
                if raw is None:item.type = tarfile.DIRTYPE; item.mode = 0o755; archive.addfile(item)
                else:item.size = len(raw); archive.addfile(item,io.BytesIO(raw))
        return stream.getvalue()

    def archive(self, damage=None):
        bodies = [('PUBLIC layer '+str(i)).encode() for i in range(31)]
        ordered = bodies+[bodies[0],bodies[0]]  # Actual shape: 33 logical, 31 physical layers.
        config = {'architecture':'amd64','os':'linux','created':'2026-10-25T01:02:03.120000000Z','history':[],
            'comment':'ordinary public builder comment','author':'public fixture author','docker_version':'28.5.1',
            'container':'public historical builder container','container_config':{'Cmd':['public historical command']},
            'rootfs':{'type':'layers','diff_ids':[digest(raw) for raw in ordered]},
            'config':{'Env':['PUBLIC_FIXTURE=synthetic'],'Cmd':['python3'],'WorkingDir':'/app','Labels':{
                'org.opencontainers.image.revision':H.CONTROL['source_commit'],'io.gold-trade.release.tree':H.CONTROL['source_tree'],
                'io.gold-trade.release.input-signature':H.CONTROL['input_signature']}}}
        raw_config = H.encode(config)
        descriptors = [{'mediaType':'application/vnd.oci.image.layer.v1.tar','digest':digest(raw),'size':len(raw)} for raw in ordered]
        if damage == 'order': descriptors.reverse()
        raw_manifest = H.encode({'schemaVersion':2,'mediaType':D.OCI_MANIFEST,'config':{
            'mediaType':'application/vnd.oci.image.config.v1+json','digest':digest(raw_config),'size':len(raw_config)},'layers':descriptors})
        raw_index = H.encode({'schemaVersion':2,'mediaType':D.OCI_INDEX,'manifests':[{'mediaType':D.OCI_MANIFEST,'digest':digest(raw_manifest),'size':len(raw_manifest)}]})
        legacy = H.canonical_legacy_configs(config,L,D)
        rows = [('blobs',None),('blobs/sha256',None),(blob(raw_config),raw_config),(blob(raw_manifest),raw_manifest),('index.json',raw_index),
            ('oci-layout',H.encode({'imageLayoutVersion':'1.0.0'})),('manifest.json',H.encode([{'Config':blob(raw_config),
                'Layers':[blob(raw) for raw in ordered],'RepoTags':None}]))]+[(blob(raw),raw) for raw in bodies]+list(legacy.items())
        if damage == 'missing': rows = [row for row in rows if row[0] != blob(bodies[0])]
        if damage == 'duplicate': rows.append(rows[0])
        if damage == 'extra': rows.append(('blobs/sha256/'+'f'*64,b'PUBLIC unknown extra'))
        if damage == 'layer': rows = [(name,b'X'+raw[1:] if name == blob(bodies[0]) else raw) for name,raw in rows]
        if damage == 'legacy': rows = [(name,raw+b' ' if name == next(iter(legacy)) else raw) for name,raw in rows]
        raw = self.tar(rows)
        if damage == 'tail': raw += b'PUBLIC not a zero trailer'
        control = deepcopy(H.CONTROL); control['image_id'] = digest(raw_config); control['rootfs'] = {'Type':'layers','Layers':config['rootfs']['diff_ids']}
        control['archive_observation'] = {'archive_bytes':len(raw),'archive_sha256':sha256(raw).hexdigest(),
            'expanded_layer_bytes':sum(map(len,ordered)),'member_count':len(rows),'manifest_id':digest(raw_manifest),
            'index_sha256':sha256(raw_index).hexdigest(),'legacy_compatibility_configs':33}
        return raw,control,config

    def receipt(self):
        return {'schema':'app_image_ci_smoke/1','status':'passed','image_config_id':H.CONTROL['image_id'],
            'runtime':{'expected':8,'run':8,'passed':8,'failures':[]},
            'components':{'expected':24,'run':24,'passed':24,'failures':[],'errors':0,'skipped':0},
            'migration':{'invocation_attempted':True,'child_completed':True,'return_code':0},'runtime_cleanup_failures':[],
            'production_config_guards_active':True,'app_source_masked':False,'real_provider_used':False,
            'security_accepted':False,'full_matrix_accepted':False}

    def evidence(self):
        control = deepcopy(H.CONTROL); control['raw_counts'] = {'UNKNOWN':1,'LOW':0,'MEDIUM':0,'HIGH':1,'CRITICAL':0}
        rows = [{'path':'Dockerfile','sha256':'a'*64,'bytes':17}]; signature = B.foreign_signature(rows,[]); control['input_signature'] = signature
        common = {'source_commit':control['source_commit'],'source_tree':control['source_tree'],'security_accepted':False}
        run = {'run_id':str(control['run_id']),'run_attempt':str(control['run_attempt'])}
        values = {'source-identity.json':{**common,**run,'schema':'canonical_app_source/1','repo':'/public/checkout','files':[]},
            'input-manifest.json':{**common,'schema':'canonical_app_inputs/1','files':rows,'empty_directories':[],'input_signature':signature},
            'image-build.json':{**common,**run,'schema':'canonical_app_image_build/1','image_id':control['image_id'],'rootfs':control['rootfs'],
                'input_signature':signature,'platform':'linux/amd64','activation_performed':False,'market_roles_built':False},
            'prepare.json':{'status':'prepared','security_accepted':False},
            'finalize.json':{'schema':'canonical_app_finalize/1','status':'exported_not_accepted','security_accepted':False},
            'reports/vulnerabilities.json':{'Metadata':{'ImageID':control['image_id']},'Results':[{'Vulnerabilities':[{'Severity':'HIGH'},{'Severity':'UNKNOWN'}]}]},
            'reports/sbom.cdx.json':{'metadata':{'component':{'type':'container','name':control['image_id'],
                'properties':[{'name':'aquasecurity:trivy:ImageID','value':control['image_id']}]}}}}
        return {name:H.encode(value) for name,value in values.items()},control

    def test_missing_actual_observation_refuses_and_actual_release_identity_is_distinct_from_controller(self):
        missing = deepcopy(H.CONTROL); missing['archive_observation'] = None
        with self.assertRaisesRegex(RuntimeError,'actual_archive_observation_missing'): H.validate_control(missing)
        value = deepcopy(H.CONTROL)
        self.assertIs(H.validate_control(value),value)
        for key,new in (('source_commit','d'*40),('image_artifact_id',True),('run_attempt',True)):
            changed = deepcopy(value); changed[key] = new
            with self.assertRaises(RuntimeError): H.validate_control(changed)
        self.assertNotEqual(H.CONTROL['source_commit'],os.environ.get('GITHUB_SHA'))

    def test_armed_control_binds_only_actual_web_measurement_without_claiming_admission(self):
        observed = {'archive_bytes':452719104,'archive_sha256':'e10090370762baf289dd55457f648b5c78b05e8e398c724397336b1d89006c67',
            'expanded_layer_bytes':452635136,'member_count':71,
            'manifest_id':'sha256:2f609ac53add114037016e54482486cfb455e41b1d79c6625c2772b07bb81908',
            'index_sha256':'fa9481689c7165671348103769538e9e17b522f53b3ae51d5b38bbd3aedc022f','legacy_compatibility_configs':33}
        self.assertEqual(H.CONTROL['archive_observation'],observed)
        for key,new in (('member_count',70),('expanded_layer_bytes',452635135),('archive_sha256','f'*64),('legacy_compatibility_configs',0)):
            value = deepcopy(H.CONTROL); value['archive_observation'][key] = new
            with self.subTest(key=key),self.assertRaises(RuntimeError):H.validate_control(value)

    def test_genuine_unadmitted_process_refuses_before_any_source_or_command(self):
        before = (os.getuid(),os.geteuid(),os.getgid(),os.getegid()); output = io.StringIO()
        with patch.object(H,'source_module',side_effect=AssertionError('must refuse before source read')),redirect_stdout(output):
            self.assertEqual(H.main(['--root',str(self.root)]),1)
        self.assertEqual(before,(os.getuid(),os.geteuid(),os.getgid(),os.getegid()))
        value = json.loads(output.getvalue()); self.assertEqual(value['closed_code'],'hosted_ci_only')
        self.assertFalse(value['security_accepted']); self.assertFalse(value['full_matrix_accepted'])

    def test_json_never_admits_duplicate_keys_or_nonfinite_numbers(self):
        for raw in (b'{"a":1,"a":2}',b'{"a":NaN}',b'{"a":Infinity}',b'{"a":1e9999}',b'{"a":-1e9999}'):
            with self.subTest(raw=raw),self.assertRaises(RuntimeError): H.document(raw)
        self.assertFalse(H.same(True,1)); self.assertFalse(H.same(1,1.0))

    def test_new_serializer_preserves_every_old_statement_except_128_limit(self):
        before = ast.parse(inspect.getsource(D.legacy_configs)).body[0]
        after = ast.parse(inspect.getsource(H.canonical_legacy_configs)).body[0]
        after.body.pop(0)  # New explanatory docstring only.
        class Normalize(ast.NodeTransformer):
            def visit_Attribute(self,node):
                if isinstance(node.value,ast.Name) and node.value.id == 'contract' and node.attr in {'container_config','DIGEST'}:
                    return ast.copy_location(ast.Name(id=node.attr,ctx=ast.Load()),node)
                return self.generic_visit(node)
            def visit_Constant(self,node):
                return ast.copy_location(ast.Constant(value=32),node) if type(node.value) is int and node.value == 128 else node
        normal = Normalize().visit(after)
        self.assertEqual(ast.dump(ast.Module(body=before.body,type_ignores=[])),ast.dump(ast.Module(body=normal.body,type_ignores=[])))
        raw,_,config = self.archive(); short = deepcopy(config); short['rootfs']['diff_ids'] = short['rootfs']['diff_ids'][:2]
        self.assertEqual(H.canonical_legacy_configs(short,L,D),D.legacy_configs(short,L))
        self.assertEqual(D.legacy_configs.__name__,'legacy_configs')

    def test_actual_33_layer_shape_with_repeated_logical_layer_is_closed_and_all_metadata_preserved(self):
        raw,control,config = self.archive(); actual,proof = H.validate_archive(io.BytesIO(raw),len(raw),self.ctx,control)
        self.assertTrue(H.same(actual,config)); self.assertTrue(proof['physical_graph_closed'])
        self.assertEqual(proof['member_count'],71); self.assertEqual(proof['legacy_compatibility_configs'],33)
        self.assertEqual(len(proof['rootfs_layers']),33); self.assertFalse(proof['daemon_identity_measured'])
        final = list(H.canonical_legacy_configs(config,L,D).values())[-1]; parsed = json.loads(final)
        self.assertEqual(parsed['created'],'2026-10-25T01:02:03.12Z')
        for key in ('comment','author','docker_version','container'): self.assertEqual(parsed[key],config[key])
        self.assertEqual(parsed['container_config'],D.container_config(config['container_config']))

    def test_full_tar_parser_refuses_graph_tampering_without_relaxing_old_module(self):
        for damage in ('order','missing','duplicate','extra','layer','legacy','tail'):
            raw,control,_ = self.archive(damage)
            with self.subTest(damage=damage),self.assertRaises((RuntimeError,ValueError)):
                H.validate_archive(io.BytesIO(raw),len(raw),self.ctx,control)
        raw,control,config = self.archive()
        for mutate in (lambda c:c.update(image_id='sha256:'+'f'*64),lambda c:c['rootfs']['Layers'].reverse(),
                       lambda c:c['archive_observation'].update(member_count=69),lambda c:c['archive_observation'].update(expanded_layer_bytes=True)):
            changed = deepcopy(control); mutate(changed)
            with self.assertRaises(RuntimeError): H.validate_archive(io.BytesIO(raw),len(raw),self.ctx,changed)
        with self.assertRaises(RuntimeError): D.legacy_configs(config,L)  # Sealed old limit stays32.

    def test_evidence_recomputes_ordinary_signature_and_binds_raw_reports_without_a_waiver(self):
        raw,control = self.evidence(); facts = H.admit_evidence(raw,control,B)
        self.assertEqual(set(facts),H.EVIDENCE_NAMES)
        self.assertEqual(facts['reports/vulnerabilities.json']['sha256'],sha256(raw['reports/vulnerabilities.json']).hexdigest())
        self.assertEqual(control['raw_counts']['HIGH'],1)  # Findings remain visible and unaccepted.
        for name,mutate in (
            ('source-identity.json',lambda v:v.update(source_commit='f'*40)),
            ('image-build.json',lambda v:v.update(run_id='1')),
            ('input-manifest.json',lambda v:v['files'][0].update(sha256='b'*64)),
            ('reports/vulnerabilities.json',lambda v:v['Metadata'].update(ImageID='sha256:'+'f'*64)),
            ('reports/vulnerabilities.json',lambda v:v['Results'][0]['Vulnerabilities'].pop()),
            ('reports/sbom.cdx.json',lambda v:v['metadata']['component']['properties'].append({'name':'aquasecurity:trivy:ImageID','value':control['image_id']})),
            ('finalize.json',lambda v:v.update(security_accepted=True))):
            changed = deepcopy(raw); value = json.loads(changed[name]); mutate(value); changed[name] = H.encode(value)
            with self.subTest(name=name),self.assertRaises(RuntimeError): H.admit_evidence(changed,control,B)

    def test_export_is_bound_to_exact_authentic_evidence_and_physical_observation(self):
        raw,control = self.evidence(); _,archive_control,_ = self.archive(); control['archive_observation'] = archive_control['archive_observation']
        facts = H.admit_evidence(raw,control,B)
        exported = {'schema':'canonical_app_image_export/1','status':'exported_not_accepted','image_id':control['image_id'],
            'image_config_id':control['image_id'],'rootfs':control['rootfs'],'source_commit':control['source_commit'],'source_tree':control['source_tree'],
            'run_id':str(control['run_id']),'run_attempt':str(control['run_attempt']),'input_signature':control['input_signature'],'platform':'linux/amd64',
            'full_config_compared_in_ram':True,'physical_archive_admission_complete':False,'signatures_verified':False,
            'activation_performed':False,'security_accepted':False,'raw_counts':control['raw_counts'],
            'inputs':{n:facts[n] for n in ('source-identity.json','input-manifest.json','image-build.json')},
            'reports':{n:facts['reports/'+n] for n in ('vulnerabilities.json','sbom.cdx.json')},
            'archive':{'format':'docker-archive+gzip','bytes':100,'sha256':'a'*64,'uncompressed_bytes':control['archive_observation']['archive_bytes'],
                       'uncompressed_sha256':control['archive_observation']['archive_sha256']}}
        self.assertIs(H.export_record(exported,control,facts),exported['archive'])
        for mutate in (lambda v:v['archive'].update(bytes=True),lambda v:v.update(signatures_verified=True),
                       lambda v:v['reports']['sbom.cdx.json'].update(sha256='f'*64),lambda v:v['archive'].update(uncompressed_bytes=512)):
            changed = deepcopy(exported); mutate(changed)
            with self.assertRaises(RuntimeError): H.export_record(changed,control,facts)

    def test_zip_inventory_refuses_duplicates_traversal_links_extra_and_oversized_before_read(self):
        names = {'source.json'}
        for entries in ([('source.json',b'{}'),('source.json',b'{}')],[('../source.json',b'{}')],
                        [('source.json',b'{}'),('extra',b'{}')],[('source.json',b'x'*33)]):
            raw = io.BytesIO()
            with warnings.catch_warnings(),zipfile.ZipFile(raw,'w') as archive:
                warnings.simplefilter('ignore',UserWarning)
                for name,body in entries: archive.writestr(name,body)
            raw.seek(0)
            with self.assertRaises(RuntimeError): H.read_zip(raw,names,32)
        raw = io.BytesIO()
        with zipfile.ZipFile(raw,'w') as archive:
            info = zipfile.ZipInfo('source.json'); info.external_attr = 0o120777 << 16; archive.writestr(info,b'{}')
        raw.seek(0)
        with self.assertRaises(RuntimeError): H.read_zip(raw,names,32)

    def test_source_is_pinned_before_compile_and_held_through_final_check(self):
        source = self.root/'source'; source.mkdir(mode=0o700)
        path = source/'public.py'; raw = b'VALUE = "PUBLIC synthetic"\n'; path.write_bytes(raw); path.chmod(0o600)
        with ExitStack() as held:
            module,check = H.source_module(self.root,held,'public.py',sha256(raw).hexdigest()); self.assertEqual(module.VALUE,'PUBLIC synthetic')
            path.write_bytes(raw+b'# changed\n')
            with self.assertRaises(RuntimeError): check()
        with self.assertRaises(RuntimeError),patch('builtins.compile',side_effect=AssertionError('must not compile')):
            H.source_module(self.root,self.stack,'public.py','f'*64)
        path.unlink(); outside = self.root/'outside'; outside.write_bytes(raw); outside.chmod(0o600); path.symlink_to(outside)
        with self.assertRaises(OSError): H.source_module(self.root,self.stack,'public.py',sha256(raw).hexdigest())

    def test_capsule_has_only_one_truthful_metadata_substitution_and_full_reverse_proof(self):
        before = OLD.CAPSULE; old_id = OLD.APP['image_config_id']; image = H.CONTROL['image_id']
        after = H.bind_capsule(before,old_id,image)
        self.assertEqual(after.replace("'image_config_id':'"+image+"'","'image_config_id':'"+old_id+"'",1),before)
        self.assertEqual(before.count(image),0); ast.parse(after)
        for bad in (before+"\n# 'image_config_id':'"+old_id+"'",before.replace(old_id,'f'*64)):
            with self.assertRaises(RuntimeError): H.bind_capsule(bad,old_id,image)
        self.assertEqual(sha256((ROOT/'scripts/run_app_image_ci_smoke.py').read_bytes()).hexdigest(),H.OLD_PIN)

    def test_all32_typed_counters_migration_guards_and_cleanup_are_mandatory(self):
        value = self.receipt(); self.assertIs(H.validate_result(value,H.CONTROL['image_id'],OLD),value)
        for mutate in (lambda v:v['runtime'].update(run=True),lambda v:v['components'].update(passed=23),
                       lambda v:v['components'].update(skipped=1),lambda v:v['migration'].update(return_code=False),
                       lambda v:v['migration'].update(child_completed=False),lambda v:v.update(runtime_cleanup_failures=['failed']),
                       lambda v:v.update(production_config_guards_active=False),lambda v:v.update(app_source_masked=True),
                       lambda v:v.update(real_provider_used=True),lambda v:v.update(security_accepted=True),
                       lambda v:v.update(image_config_id=OLD.APP['image_config_id'])):
            changed = deepcopy(value); mutate(changed)
            with self.assertRaises(RuntimeError): H.validate_result(changed,H.CONTROL['image_id'],OLD)

    def test_cleanup_double_fault_retains_primary_error_and_attempts_cleanup_and_receipt(self):
        events = []; original = RuntimeError('archive_observation'); errors = None
        def cleanup(_): events.append('cleanup'); return ['owned_container_cleanup_unproven']
        def report(path,value):
            events.append('cleanup_report'); self.assertTrue(value['primary_failure_retained'])
            raise OSError(28,'PUBLIC synthetic full filesystem')
        ctx = SimpleNamespace(root=self.root,write=report); old = SimpleNamespace(cleanup=cleanup)
        try:
            try: raise original
            finally: errors = H.finish_cleanup(ctx,old,True)
        except RuntimeError as caught: self.assertIs(caught,original)
        self.assertEqual(events,['cleanup','cleanup_report'])
        self.assertEqual(errors,['cleanup_report_unproven','owned_container_cleanup_unproven'])
        with self.assertRaisesRegex(RuntimeError,'^cleanup_report_unproven$'):
            H.finish_cleanup(SimpleNamespace(root=self.root,write=lambda *_:(_ for _ in ()).throw(OSError(28,'PUBLIC'))),
                SimpleNamespace(cleanup=lambda _:[]),False)
        with self.assertRaisesRegex(RuntimeError,'^owned_cleanup_unproven$'):
            H.finish_cleanup(SimpleNamespace(root=self.root,write=lambda *_:None),
                SimpleNamespace(cleanup=lambda _:['owned_container_cleanup_unproven']),False)

    def test_presence_uses_same_image_readonly_networkless_caps_none_and_public_proof_only(self):
        filename = next(iter(H.VERIFIERS)); pin = H.VERIFIERS[filename][0]; image = H.CONTROL['image_id']
        args = H.presence_args(OLD,None,'app-ci-presence-12345-1',image,self.root,filename,pin,H.CONTROL)
        for value in ('--network=none','--read-only','--cap-drop=ALL','--security-opt=no-new-privileges','--pull=never'):
            self.assertIn(value,args)
        self.assertEqual(args[args.index('--mount')+1],'type=bind,source='+str(self.root)+',target=/proof,readonly')
        self.assertEqual(args.count('--mount'),1); self.assertNotIn('--env',args)
        self.assertIn(H.CONTROL['source_commit'],args); self.assertIn(image,args); self.assertIn('/proof/'+filename,args)
        self.assertNotIn('privileged',' '.join(args))

    def test_both_fresh_presence_receipts_bind_same_daemon_image_and_release_source(self):
        source = self.root/'source'; source.mkdir(mode=0o700)
        for filename,(pin,_,_) in H.VERIFIERS.items():
            raw = (ROOT/'scripts'/filename).read_bytes(); self.assertEqual(sha256(raw).hexdigest(),pin)
            path = source/filename; path.write_bytes(raw); path.chmod(0o600)
        image = 'sha256:'+'d'*64  # Valid measured daemon manifest, distinct from config identity.
        reports = {}; commands = []; values = {}; counter = 0
        def write_file(path,raw,mode):
            with path.open('xb') as output: output.write(raw)
            path.chmod(mode)
        ctx = SimpleNamespace(root=self.root,stack=self.stack,checks=[],created=[],C=SimpleNamespace(write=write_file),
            write=lambda path,value:reports.update({path.name:value}))
        def docker(args,**kwargs):
            nonlocal counter
            commands.append(args)
            if args[0] == 'create':
                filename = next(name for name in H.VERIFIERS if '/proof/'+name in args)
                pin,schema,_ = H.VERIFIERS[filename]; counter += 1; cid = str(counter)*64
                values[cid] = {'schema':schema,'image_id':image,'source_commit':H.CONTROL['source_commit'],
                    'verifier_sha256':pin,'production_mounts_or_data_used':False,'scanner_findings_modified':False,'security_accepted':False}
                return cid.encode()
            if args[:2] == ['container','inspect']:
                return H.encode([{'Type':'bind','Source':str(self.root/'presence-proof'),'Destination':'/proof','RW':False}])
            if args[0] == 'start': return H.encode(values[args[-1]])
            if args[0] == 'inspect': return b'0'
            self.fail('unexpected fixture command')
        ctx.docker = docker; H.presence(ctx,OLD,image,H.CONTROL)
        self.assertEqual(len(reports),2); self.assertEqual(len(ctx.created),2); self.assertEqual(len(ctx.checks),2)
        for value in reports.values():
            self.assertEqual(value['image_id'],image); self.assertEqual(value['source_commit'],H.CONTROL['source_commit'])
            self.assertFalse(value['security_accepted'])
        for check in ctx.checks: check()
        self.assertEqual(len([a for a in commands if a[0] == 'start']),2)

    def test_new_orchestration_executes_old_fixture_contract_without_any_old_app_entrypoint(self):
        value = self.receipt(); commands = []; writes = {}; proof = self.root/'proof'; proof.mkdir()
        ctx = SimpleNamespace(root=self.root,created=[],network=None,M=SimpleNamespace(same_json_value=H.same),
            C=SimpleNamespace(read=lambda *a:H.encode(value)),write=lambda path,v:writes.update({path.name:v}))
        def docker(args,**kwargs):
            commands.append((args,kwargs))
            if args[0] == 'create': return (str(sum(x[0][0] == 'create' for x in commands))*64).encode()
            if args[:2] == ['container','inspect']:
                return H.encode({'mounts':[{'Type':'bind','Destination':'/proof','Source':str(proof),'RW':False}],
                    'tmpfs':{'/tmp':'rw,nosuid,nodev,noexec,size=128m,mode=1777'}})
            if 'pg_isready' in args: return b'accepting'
            if 'redis-cli' in args: return b'PONG'
            if args[0] == 'inspect': return b'0'
            return b''
        ctx.docker = docker
        with patch.object(OLD,'admit_dependency',side_effect=lambda _,p:'sha256:'+('a' if p == 'postgres' else 'b')*64), \
             patch.object(OLD,'prepare_proof',return_value=proof), \
             patch.object(OLD,'execute',side_effect=AssertionError('old entrypoint denied')), \
             patch.object(OLD,'admit_app',side_effect=AssertionError('old admission denied')), \
             patch.object(OLD,'validate_result',side_effect=AssertionError('old validator denied')):
            self.assertEqual(H.run_smoke(ctx,OLD,H.CONTROL['image_id'],H.CONTROL),value)
        app = [args for args,_ in commands if args[0] == 'create'][-1]
        self.assertIn('--cap-drop=ALL',app); self.assertNotIn('--cap-add',app)
        env = [app[i+1] for i,x in enumerate(app) if x == '--env']
        database = 'coin_intelligence_ci_'+sha256(OLD.owner().encode()).hexdigest()[:12]
        self.assertEqual(set(env),{'APP_ENV_FILE=/proof/runtime.env'}|{k+'='+v for k,v in OLD.fixture_env(database).items()})
        self.assertNotIn('/app', [part for part in app if part.startswith('type=bind')][0])
        self.assertEqual(writes['actual-smoke.json'],value)

    def test_workflow_runs_only_reviewed_security_branch_paths_and_preserves_evidence_on_failure(self):
        text = (ROOT/'.github/workflows/canonical-app-runtime-smoke.yml').read_text()
        self.assertIn("github.repository == 'mohsenbarari/trading_bot'",text)
        self.assertIn("github.ref == 'refs/heads/security/remediation-20261023' && (github.event_name == 'push' || inputs.run_smoke)",text)
        trigger = text.split('  push:\n',1)[1].split('  workflow_dispatch:',1)[0]
        self.assertEqual([line.strip()[2:] for line in trigger.splitlines() if line.strip().startswith('- ')],
            ['security/remediation-20261023','scripts/run_canonical_app_ci_smoke.py','tests/test_canonical_app_ci_smoke.py',
             '.github/workflows/canonical-app-runtime-smoke.yml'])
        self.assertNotIn('refs/heads/main',text); self.assertIn('ref: ${{ github.sha }}',text)
        self.assertIn('persist-credentials: false',text); self.assertIn('actions: read',text)
        self.assertIn('CONTROLLER_SHA: '+sha256((ROOT/'scripts/run_canonical_app_ci_smoke.py').read_bytes()).hexdigest(),text)
        self.assertIn('CONTROLLER_TEST_SHA: '+sha256(Path(__file__).read_bytes()).hexdigest(),text)
        self.assertEqual(text.count('GH_TOKEN:'),1); self.assertIn("if: always() && steps.run.outputs.root != ''",text)
        self.assertIn('/evidence/public-evidence.zip',text); self.assertIn('cancel-in-progress: false',text)
        self.assertNotIn('workflow_run:',text); self.assertNotIn('pull_request:',text); self.assertNotIn('sudo',text)
        scripts = text.split("python3 -I -B - <<'PY'\n")[1:]
        self.assertEqual(len(scripts),2)
        admission = '\n'.join(line[10:] for line in scripts[0].split('\n          PY',1)[0].splitlines()); ast.parse(admission)
        self.assertIn('module.validate_control(module.CONTROL)',admission)
        self.assertIn("'tests.test_canonical_app_ci_smoke'",admission)
        self.assertNotIn('GH_TOKEN',admission)
        self.assertLess(text.index('Admit the armed exact intake'),text.index('GH_TOKEN:'))
        code = scripts[1].split('\n          PY',1)[0]
        code = '\n'.join(line[10:] for line in code.splitlines()); ast.parse(code)
        self.assertIn("capture(['git','--no-optional-locks','cat-file','blob',operand]",code)
        self.assertIn("exec(compile(controller",code); self.assertIn('for check in checks:check()',code)


if __name__ == '__main__': unittest.main()
