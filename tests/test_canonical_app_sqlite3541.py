"""Source joins only; genuine frozen Git bytes, no Docker/network/package install."""
from contextlib import ExitStack
from hashlib import sha1,sha256
from pathlib import Path
from types import ModuleType
from unittest.mock import patch
import ast,copy,json,os,subprocess,tempfile,unittest,zipfile
import yaml

SOURCE=Path(__file__).resolve().parents[1]
REPO=Path.cwd().resolve()
P=SOURCE/'scripts/prepare_canonical_app_image.py'
M=ModuleType('_app_canonical3541');M.__file__=str(P);exec(compile(P.read_bytes(),str(P),'exec'),M.__dict__)

class Canonical3541Tests(unittest.TestCase):
    def setUp(self):
        self.stack=ExitStack();self.addCleanup(self.stack.close)
        self.root=Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix='app3541-public-')))
        self.stack.enter_context(patch.object(M,'CANONICAL3541',True))

    def test_recipe_only_changes_explicit_sqlite_selector_and_runtime_glibc_hook(self):
        before=subprocess.check_output(['/usr/bin/git','--no-optional-locks','show',M.BUSINESS_COMMIT+':Dockerfile'],cwd=REPO)
        after=(SOURCE/M.APP_RECIPE).read_bytes()
        adapter={};exec(compile((REPO/'scripts/glibc_runtime_adapter.py').read_bytes(),'glibc_runtime_adapter.py','exec'),adapter)
        recovered=after.replace(adapter['HOOK'],b'',1)
        recovered=recovered.replace(b'sqlite3541-capsule',b'sqlite-wal-capsule').replace(b'"--canonical3541", ',b'')
        # The old cache mount and its container target have different names.
        recovered=recovered.replace(b'target=/sqlite-wal-capsule',b'target=/sqlite-capsule').replace(b'"/sqlite-wal-capsule"',b'"/sqlite-capsule"')
        self.assertEqual(recovered,before)
        self.assertEqual((len(after),sha256(after).hexdigest()),M.APP_RECIPE_PIN)
        self.assertLess(after.index(b'prepare_glibc_security_os_cache.py'),after.index(b'verify_image_trust.py'))

    def test_genuine_frozen_git_context_preserves_every_business_blob(self):
        # The sole not-yet-published operand is the dedicated Source recipe.
        head=subprocess.check_output(['/usr/bin/git','--no-optional-locks','rev-parse','HEAD'],cwd=REPO).decode().strip()
        recipe=(SOURCE/M.APP_RECIPE).read_bytes();blob=sha1(b'blob '+str(len(recipe)).encode()+b'\0'+recipe).hexdigest()
        real_git=M.git;real_read=M.read_file
        def git(repo,*args,**kwargs):
            if args==('rev-parse',head+':'+M.APP_RECIPE):return (blob+'\n').encode()
            return real_git(repo,*args,**kwargs)
        def read(path,*args):
            return real_read(SOURCE/M.APP_RECIPE if Path(path)==REPO/M.APP_RECIPE else path,*args)
        old_umask=os.umask(0o077)
        try:
            with patch.dict(os.environ,{'GITHUB_SHA':head}),patch.object(M,'git',side_effect=git),patch.object(M,'read_file',side_effect=read):
                context,rows=M.frozen_context(self.root,REPO)
        finally:os.umask(old_umask)
        self.assertEqual(context.stat().st_mode&0o777,0o755)
        for path in context.rglob('*'):
            if path.is_dir():self.assertEqual(path.stat().st_mode&0o777,0o755)
        self.assertGreater(len(rows),500)
        changed={r['path'] for r in rows if r['commit']==head}
        self.assertEqual(changed,{'Dockerfile',*M.SECURITY_UPDATES})
        for row in rows:
            raw=(context/row['path']).read_bytes()
            self.assertEqual(sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest(),row['git_blob'])
            self.assertEqual(sha256(raw).hexdigest(),row['sha256'])
        self.assertFalse((context/'.git').exists())
        self.assertEqual((context/'core/market_intelligence/estimator_snapshot_receiver_service.py').read_bytes(),
            subprocess.check_output(['/usr/bin/git','--no-optional-locks','show',M.BUSINESS_COMMIT+':core/market_intelligence/estimator_snapshot_receiver_service.py'],cwd=REPO))

    def test_canonical_inventory_accepts_exact_armed_trixie_and_refuses_unarmed(self):
        names=('app_security_os_packages.lock.json','sqlite3541_packages.lock.json','sqlite3541_package.py',
            'tar_acl_package.lock.json','coreutils_acl_package.lock.json')
        rows=[]
        for name in names:
            path=self.root/'scripts'/name;path.parent.mkdir(exist_ok=True);path.write_bytes((REPO/'scripts'/name).read_bytes());path.chmod(0o600)
            raw,metadata=M.read_file(path,1024**2);rows.append({'path':'scripts/'+name,'identity':metadata,'sha256':sha256(raw).hexdigest()})
        selected=M.public_input_names(self.root,{'files':rows})
        package='pip_packages/sqlite3541-capsule/libsqlite3-0_3.54.1-0+trading1~trixie1_amd64.deb'
        self.assertIn(package,selected);self.assertIn('pip_packages/sqlite3541-capsule/provenance.json',selected)
        self.assertFalse(any('sqlite-wal-capsule' in name for name in selected))
        path=self.root/'scripts/sqlite3541_packages.lock.json';value=json.loads(path.read_bytes());value['armed']=False;path.write_bytes(M.encode(value))
        raw,metadata=M.read_file(path,1024**2)
        row=next(r for r in rows if r['path']=='scripts/sqlite3541_packages.lock.json');row.update(identity=metadata,sha256=sha256(raw).hexdigest())
        with self.assertRaisesRegex(RuntimeError,'lock_unarmed'):M.public_input_names(self.root,{'files':rows})

    def test_canonical_builder_joins_cache_recipe_labels_and_named_context(self):
        repo=self.root/'context';repo.mkdir();(repo/'mini_app_dist').mkdir();(repo/'mini_app_dist/index.html').write_text('public fixture')
        (repo/'Dockerfile').write_bytes((SOURCE/M.APP_RECIPE).read_bytes());(self.root/'reports').mkdir()
        source={'repo':str(REPO),'source_commit':'a'*40,'source_tree':'b'*40,'run_id':'1','run_attempt':'1',
            'files':[r for r in M.input_files(repo) if r['path']=='Dockerfile']}
        calls=[];image='sha256:'+'c'*64
        def command(argv,**kwargs):
            calls.append(argv)
            if 'build' in argv:(self.root/'candidate-image.id').write_text(image)
            return b''
        named={'schema':'glibc84243_exact_runtime_named_context/1','files':M.GLIBC_FILES,'security_accepted':False}
        with patch.object(M,'state',return_value=(repo,source)),patch.object(M,'unchanged'),\
            patch.object(M,'canonical_glibc',return_value=named),patch.object(M,'canonical_external_rows',return_value=[]),\
            patch.object(M,'public_input_names',return_value={'Dockerfile'}),patch.object(M,'command',side_effect=command),\
            patch.object(M,'inspect',return_value={'Config':{},'RootFS':{}}),patch.object(M,'emit'):
            self.assertEqual(M.build(self.root)['image_id'],image)
        sqlite=next(call for call in calls if any('prepare_sqlite_wal_os_cache.py' in arg for arg in call))
        self.assertIn('--canonical3541',sqlite);self.assertIn(str(repo/'pip_packages/sqlite3541-capsule'),sqlite)
        build=calls[-1];self.assertIn('glibc-runtime='+str(self.root/'glibc-capsule'),build)
        self.assertIn('io.gold-trade.business.commit='+M.BUSINESS_COMMIT,build)
        self.assertEqual(build[-3:],['-f',str(repo/'Dockerfile'),str(repo)])
        result=json.loads((self.root/'image-build.json').read_bytes());self.assertEqual(result['sqlite_family'],'canonical3541')
        self.assertEqual(result['glibc_named_context'],named);self.assertFalse(result['security_accepted'])

    def test_actual_existing_app32_adapter_only_rebinds_image_receipt(self):
        driver=(REPO/M.SMOKE_DRIVER).read_bytes();adapter=(REPO/M.SMOKE_ADAPTER).read_bytes()
        self.assertEqual((len(driver),sha256(driver).hexdigest()),M.SMOKE_PIN)
        self.assertEqual((len(adapter),sha256(adapter).hexdigest()),M.SMOKE_ADAPTER_PIN)
        old=M.source_module(driver,REPO/M.SMOKE_DRIVER);api=M.source_module(adapter,REPO/M.SMOKE_ADAPTER)
        image='sha256:'+'c'*64;bound=api.bind_capsule(old.CAPSULE,old.APP['image_config_id'],image)
        self.assertEqual(bound.replace(image,old.APP['image_config_id']),old.CAPSULE)
        self.assertEqual(sum(row[1] for row in old.SELECTORS.values()),24)
        self.assertIn("'expected':8",old.CAPSULE)

    def test_workflow_opt_in_and_actual32_precedes_export_with_original_scans(self):
        data=yaml.safe_load((SOURCE/'.github/workflows/canonical-app-image.yml').read_text())
        trigger=data.get('on',data.get(True));self.assertIs(trigger['workflow_dispatch']['inputs']['canonical3541']['default'],False)
        steps=data['jobs']['candidate']['steps'];indexed={step.get('id'):index for index,step in enumerate(steps)}
        self.assertLess(indexed['build'],indexed['app32']);self.assertLess(indexed['app32'],indexed['finalize'])
        self.assertEqual(steps[indexed['app32']]['if'],'inputs.canonical3541')
        for step in steps:
            text=step.get('run','')
            if "<<'PY'\n" in text:ast.parse(text.split("<<'PY'\n",1)[1].rsplit('\nPY',1)[0])
        scans=[step for step in steps if step.get('uses','').startswith('aquasecurity/trivy-action@')]
        self.assertEqual(len(scans),2)
        self.assertTrue(all(step['with']['image-ref']=='${{ steps.build.outputs.image_id }}' for step in scans))
        self.assertEqual(data['permissions'],{'contents':'read','actions':'read'})

    def test_shared_normalizer_requires_genuine_App_gate_shapes_without_Market81(self):
        # Clearly synthetic App protocol operands; never an actual image proof.
        A=M.source_module((REPO/'scripts/assess_image_security_evidence.py').read_bytes(),REPO/'scripts/assess_image_security_evidence.py')
        R=M.source_module((SOURCE/M.SECURITY_RUNTIME).read_bytes(),SOURCE/M.SECURITY_RUNTIME)
        N=M.source_module((SOURCE/M.SECURITY_NORMALIZER).read_bytes(),SOURCE/M.SECURITY_NORMALIZER)
        H=M.source_module((REPO/'tests/test_assess_image_security_evidence.py').read_bytes(),REPO/'tests/test_assess_image_security_evidence.py')
        p=H.Packet();build=json.loads(p.inputs[H.PRODUCT_NAMES['build']]);build['rootfs']={'Type':'layers','Layers':['sha256:'+'e'*64]}
        with zipfile.ZipFile(REPO/M.SECURITY_SEED)as archive:originals={name:archive.read(name)for name in archive.namelist()}
        for key,name in N.PRODUCT.items():originals[name]=p.inputs[H.PRODUCT_NAMES[key]]
        originals[N.PRODUCT['build']]=A.encoded(build)
        p.scan['Results'][0]['Vulnerabilities']=[];originals['scan.json']=A.encoded(p.scan);originals['sbom.json']=A.encoded(p.sbom)
        originals['sqlite-lock.json']=(REPO/'scripts/sqlite3541_packages.lock.json').read_bytes()
        originals.update({'component-verifier.py':(REPO/M.SECURITY_COMPONENT).read_bytes(),'acl-verifier.py':(REPO/M.SECURITY_ACL).read_bytes(),
            'runtime-collector.py':(SOURCE/M.SECURITY_RUNTIME).read_bytes(),'normalizer.py':(SOURCE/M.SECURITY_NORMALIZER).read_bytes()})
        expected,bundle=R.prepare_request(build,originals['runtime-vector.json'],originals['sqlite-lock.json'],originals['component-verifier.py'],originals['acl-verifier.py'])
        self.assertEqual(bundle['source_commit'],build['source_commit']);self.assertEqual(bundle['run_id'],123)
        runtime={'schema':'normal_market_same_image_runtime_observation/1','status':'OBSERVED_NOT_ASSESSED',
            **{key:bundle[key]for key in('image_id','source_commit','source_tree','input_signature','run_id','run_attempt','vector_sha256')},
            'observed_at':H.NOW,'production_mounts_or_data_used':False,'security_accepted':False,
            'declared_runtime_files_complete':True,'all_declared_file_readers_closed':True,'owned_container_cleanup_proven':True,
            'packages':{row['package']:{'installed_version':row['installed_version'],'source':row['source_package'],'architecture':'amd64'}for row in expected},
            'files':[file for row in expected for file in row['expected_files']],'component_presence':{'owned_synthetic_fixture':True}}
        originals['runtime-observation.json']=A.encoded(runtime);originals['component-original.json']=A.encoded(runtime['component_presence'])
        lock=json.loads(originals['sqlite-lock.json']);profile=lock['profiles']['trixie']
        originals['sqlite-probe.json']=A.encoded({'status':'PASS','sqlite_package':profile['version'],
            'sqlite_system_library':{**profile['installed_library'],'mapped_inode_verified':True},'production_data_accessed':False,'all_cves_attested':False})
        actual={'schema':'app_image_ci_smoke/1','image_config_id':build['image_id'],'status':'passed',
            'runtime':{'expected':8,'run':8,'passed':8,'failures':[]},
            'components':{'expected':24,'run':24,'passed':24,'failures':[],'errors':0,'skipped':0},
            'migration':{'invocation_attempted':True,'child_completed':True,'return_code':0},'runtime_cleanup_failures':[],
            'app_source_masked':False,'production_config_guards_active':True,'real_provider_used':False,'security_accepted':False,'full_matrix_accepted':False}
        factual={'schema':'canonical_app_actual_unchanged32/1','image_id':build['image_id'],'assertions_changed':False,
            'driver':{'bytes':M.SMOKE_PIN[0],'sha256':M.SMOKE_PIN[1]},'adapter':{'bytes':M.SMOKE_ADAPTER_PIN[0],'sha256':M.SMOKE_ADAPTER_PIN[1]},'actual':actual,'security_accepted':False}
        originals['factual-evidence.json']=A.encoded(factual)
        originals['app-cleanup.json']=A.encoded({'owned_cleanup_complete':True,'closed_causes':[],'primary_failure_retained':False})
        originals['image-export.json']=A.encoded({'schema':'canonical_app_image_export/1','status':'exported_not_accepted',
            'image_id':build['image_id'],'image_config_id':build['image_id'],'source_commit':build['source_commit'],'source_tree':build['source_tree'],
            'input_signature':build['input_signature'],'raw_counts':{key:0 for key in('UNKNOWN','LOW','MEDIUM','HIGH','CRITICAL')},
            'reports':{'vulnerabilities.json':N.pin(originals['scan.json']),'sbom.cdx.json':N.pin(originals['sbom.json'])},
            'security_accepted':False,'full_config_compared_in_ram':True,'rootfs':build['rootfs'],'activation_performed':False,
            'native_abi_checks':{'completed_with_build':True},'inputs':{name:N.pin(originals[key])for name,key in
                (('source-identity.json',N.PRODUCT['source']),('input-manifest.json',N.PRODUCT['manifest']),('image-build.json',N.PRODUCT['build']))}})
        originals['current-review.json']=A.encoded({'schema':'normal_market_current_security_review/1','official_sources_reviewed':True,
            'old_probe_or_gate_retimestamps':False,'security_accepted':False,'reviewed_at':H.NOW,'valid_until':H.NOW+86400,'advisory_ids':list(A.SOURCES)})
        prepared=N.prepare(A,R,originals,now=H.NOW)
        result=A.assess(prepared['lock_raw'],originals['scan.json'],originals['sbom.json'],prepared['inputs'],now=H.NOW,finalizer=lambda:True)
        self.assertEqual(result['status'],'PASS');self.assertEqual(result['product_profile'],'canonical_app32')
        gates={A.document(prepared['inputs'][name])['observations']['scope']for name in A.document(prepared['lock_raw'])['required_gates']}
        self.assertEqual(gates,set(A.PROFILE_GATES['canonical_app32']));self.assertNotIn('normal_market_81_unchanged',gates)
        for field,value,code in [('factual-evidence.json',{**factual,'actual':{**actual,'runtime':{'expected':8,'run':7,'passed':7,'failures':[]}}},'normal_app_unchanged32_gate'),
            ('app-cleanup.json',{'owned_cleanup_complete':False},'normal_app_cleanup_gate')]:
            bad=dict(originals);bad[field]=A.encoded(value)
            with self.assertRaisesRegex(A.Refused,code):N.prepare(A,R,bad,now=H.NOW)
        bad=copy.deepcopy(build);bad['historical81']={}
        with self.assertRaisesRegex(RuntimeError,'runtime_app_product'):
            R.prepare_request(bad,originals['runtime-vector.json'],originals['sqlite-lock.json'],originals['component-verifier.py'],originals['acl-verifier.py'])

if __name__=='__main__':unittest.main()
