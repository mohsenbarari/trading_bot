"""Owned source/FD and authentic PUBLIC evidence tests; no Docker/CI proof.

Protocol fixtures below are explicitly synthetic and never image acceptance.
The actual32 assertions, Docker daemon and genuine hosted UID1001 remain gates.
"""
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from types import ModuleType
from unittest.mock import patch
import ast,copy,errno,fcntl,json,os,stat,tarfile,tempfile,time,unittest

HERE=Path(__file__).absolute().parent

def load(path):
    raw=path.read_bytes();module=ModuleType('owned_'+path.stem)
    module.__file__=str(path);exec(compile(raw,str(path),'exec'),module.__dict__)
    return module

Q=load(HERE/'controller.py');S=load(HERE/'seal_and_run.py')
MANIFEST=json.loads((HERE/'source-manifest.json').read_bytes())
ROOT=HERE.parents[1]

def dependency(name):
    path=HERE/'dependencies'/name
    if not path.exists():
        row=next(r for r in MANIFEST['files']if r['name']==name)
        # Installed new cohort resides at deploy/maintenance/app_gold2_smoke.
        path=HERE.parents[2]/row['git_path']
    return path

P=load(dependency('producer02_controller.py'))
B=load(dependency('prepare_canonical_app_image.py'))
OLD=load(dependency('run_app_image_ci_smoke.py'))
E=load(dependency('export_controller.py'))
A=load(dependency('archive_inventory.py'));I=load(dependency('inventory.py'))
C=load(dependency('compose_security_os_candidate.py'))
INTAKE=Q.checked_document((HERE/'intake.json').read_bytes(),Q.INTAKE_PIN)

class Owned(unittest.TestCase):
    def test_complete_source_dag_modes_bounds_and_actual_reused_paths(self):
        Q.validate_inputs(INTAKE,MANIFEST)
        self.assertEqual(len(MANIFEST['files']),15)
        self.assertEqual(MANIFEST['total_bytes'],519336)
        for row in MANIFEST['files']:
            with self.subTest(name=row['name']):
                p=dependency(row['name']);raw=p.read_bytes()
                self.assertEqual((len(raw),sha256(raw).hexdigest()),Q.PINS[row['name']])
                self.assertEqual(stat.S_IMODE(p.stat().st_mode),0o644)
                self.assertLessEqual(len(raw),131072);ast.parse(raw)
                self.assertEqual(S.SOURCE_PATHS[row['name']],row['git_path'])
                self.assertEqual(S.SOURCES[row['name']],Q.PINS[row['name']])
        for name in ('controller.py','intake.json','source-manifest.json'):
            raw=(HERE/name).read_bytes()
            self.assertEqual(S.SOURCES[name],(len(raw),sha256(raw).hexdigest()))
        self.assertEqual(len(S.SOURCES),18)
        self.assertTrue(all(S.SOURCE_PATHS[n].startswith('deploy/maintenance/app_gold2_smoke/')for n in ('controller.py','intake.json','source-manifest.json')))

    def test_default_unarmed_without_source_or_host_operation(self):
        with patch.object(Q,'hosted',side_effect=AssertionError('OWNED_NO_HOST')):
            self.assertEqual(Q.main(['--root','/OWNED_NOT_OPENED']),0)
        with patch.object(S,'host',side_effect=AssertionError('OWNED_NO_HOST')):
            self.assertEqual(S.main([]),0)
        self.assertFalse(S.EXECUTION_ARMED)
        self.assertFalse(P.ARMED);self.assertIsNone(P.CONTROL)

    def test_actual_public_evidence_zip_and_current_builder_admission(self):
        path=HERE/'fixtures/public-evidence.zip';raw=path.read_bytes();control=INTAKE['control']
        self.assertEqual((len(raw),sha256(raw).hexdigest()),(control['evidence_zip_bytes'],control['evidence_zip_sha256']))
        evidence=P.read_zip(BytesIO(raw),Q.EVIDENCE_NAMES)
        result=Q.admit_evidence(evidence,INTAKE,B)
        self.assertEqual(result,INTAKE['evidence_members'])
        exported=(HERE/'fixtures/image-export.json').read_bytes()
        self.assertEqual((len(exported),sha256(exported).hexdigest()),(INTAKE['export']['bytes'],INTAKE['export']['sha256']))
        archive=P.export_record(Q.document(exported),control,result)
        self.assertEqual(archive['uncompressed_sha256'],control['archive_observation']['archive_sha256'])
        self.assertEqual(control['raw_counts']['HIGH'],44)
        self.assertEqual(Q.BUILDER_PIN,'403e95c4f59a4f22c6154f5ebddfd8d6f77b1b3a9c84fff4f7ab84a10ae698f3')
        self.assertNotEqual(Q.BUILDER_PIN,P.BUILDER_PIN)
        self.assertFalse(P.ARMED);self.assertIsNone(P.CONTROL)

    def test_evidence_and_intake_mutations_refuse_before_use(self):
        raw=(HERE/'fixtures/public-evidence.zip').read_bytes()
        files=P.read_zip(BytesIO(raw),Q.EVIDENCE_NAMES)
        for name in ('source-identity.json','image-build.json','input-manifest.json','reports/sbom.cdx.json','reports/vulnerabilities.json'):
            with self.subTest(member=name):
                changed=dict(files);changed[name]=changed[name]+b' '
                with self.assertRaisesRegex(RuntimeError,'evidence_identity'):
                    Q.admit_evidence(changed,INTAKE,B)
        for field,value in (('image_id','sha256:'+'0'*64),('source_commit','1'*40),('input_signature','2'*64)):
            value_doc=copy.deepcopy(INTAKE);value_doc['control'][field]=value
            with self.subTest(field=field),self.assertRaises(RuntimeError):
                Q.admit_evidence(files,value_doc,B)
        changed=copy.deepcopy(INTAKE);changed['sbom']['sha256']='0'*64
        with self.assertRaisesRegex(RuntimeError,'source_pin'):
            Q.checked_document(Q.encoded(changed),Q.INTAKE_PIN)
        with self.assertRaises(RuntimeError):Q.document(b'{"x":1,"x":2}')

    def test_root_measurement_is_provenance_not_host_filesystem_authority(self):
        raw=(HERE/'fixtures/Root-archive-receipt.json').read_bytes()
        self.assertEqual((len(raw),sha256(raw).hexdigest()),(INTAKE['Root_physical_receipt']['bytes'],INTAKE['Root_physical_receipt']['sha256']))
        receipt=json.loads(raw);proof=receipt['proof']
        self.assertEqual({k:proof[k]for k in INTAKE['control']['archive_observation']},INTAKE['control']['archive_observation'])
        self.assertEqual(len(proof['rootfs_layers']),37);self.assertEqual(proof['member_count'],78)
        self.assertNotIn('full9',Q.encoded(INTAKE).decode())
        self.assertNotIn('output_device',INTAKE)

    def test_real_owned_flat_tar_retains_hardlinks_and_unassessed_without_exclusions(self):
        with tempfile.TemporaryDirectory(prefix='owned-flat-',dir=HERE)as td:
            path=Path(td)/'owned.tar'
            files={'var/lib/dpkg/status':b'Package: owned-elf\nStatus: install ok installed\nVersion: 1\nArchitecture: amd64\n\n',
                   'var/lib/dpkg/info/owned-elf.list':b'/usr/bin/owned\n/proof/owned\n/usr/bin/owned-hard\n',
                   'usr/bin/owned':b'\x7fELF'+bytes(60),'proof/owned':b'\x7fELF'+bytes(60)}
            with tarfile.open(path,'w',format=tarfile.USTAR_FORMAT)as archive:
                for name,raw in files.items():
                    item=tarfile.TarInfo(name);item.mode=0o644;item.size=len(raw);archive.addfile(item,BytesIO(raw))
                item=tarfile.TarInfo('usr/bin/owned-hard');item.type=tarfile.LNKTYPE;item.linkname='usr/bin/owned';archive.addfile(item)
            path.chmod(0o600);raw=path.read_bytes();digest=sha256(raw).hexdigest();sbom=b'{"components":[]}'
            with path.open('rb')as stream:
                receipt={'schema':'same_image_never_started_export/1','container_id':'3'*64,'image_id':INTAKE['control']['image_id'],
                    'source_commit':INTAKE['control']['source_commit'],'before':{'OWNED_PROTOCOL_ONLY':True},'after':{'OWNED_PROTOCOL_ONLY':True},
                    'archive':{'bytes':len(raw),'sha256':digest,'full9':list(A.identity(os.fstat(stream.fileno())))},
                    'actual_export_client_returncode':0,'container_started':False,'private_mounts_used':False,'security_accepted':False}
                report=A.collect(I,None,stream,len(raw),image_id=receipt['image_id'],source_commit=receipt['source_commit'],
                    sbom_raw=sbom,sbom_sha256=sha256(sbom).hexdigest(),verifier_sha256=Q.PINS['archive_inventory.py'][1],
                    export_sha256=digest,container_receipt=receipt,check=lambda:None,end=time.monotonic()+10)
                os.fstat(stream.fileno());self.assertFalse(stream.closed)
            rows=report['inventory']['elf_files']
            self.assertEqual({r['path']for r in rows},{'/usr/bin/owned','/proof/owned','/usr/bin/owned-hard'})
            self.assertTrue(all(r['elf_parse_status']=='UNASSESSED_UNSUPPORTED_METADATA'for r in rows))
            self.assertEqual(report['inventory']['runtime_paths_excluded'],[])
            self.assertFalse(report['elf_metadata_inventory_complete'])

    def test_measured835_gate_never_trims_or_drops_unassessed(self):
        # Synthetic protocol result only; these rows prove no actual image fact.
        rows=[{'path':'/OWNED_PROTOCOL_ONLY/'+str(i),'elf_parse_status':'UNASSESSED_UNSUPPORTED_METADATA'}for i in range(835)]
        report={'schema':'image_acl_archive_inventory/1','image_id':INTAKE['control']['image_id'],'source_commit':INTAKE['control']['source_commit'],
            'sbom_sha256':INTAKE['sbom']['sha256'],'scope':'all_regular_ELF_in_authenticated_flat_image_TAR_without_path_exclusions',
            'inspected_code_executed':False,'archive_extracted':False,'whole_image_acl_closed':False,'security_accepted':False,
            'inventory':{'runtime_paths_excluded':[],'elf_files':rows}}
        self.assertIs(Q.inventory_gate(report,report['image_id'],INTAKE),report)
        self.assertIs(report['inventory']['elf_files'],rows)
        rows.append({'path':'/OWNED_PROTOCOL_ONLY/835','elf_parse_status':'UNASSESSED_UNSUPPORTED_METADATA'})
        with self.assertRaisesRegex(RuntimeError,'inventory_cardinality_changed'):Q.inventory_gate(report,report['image_id'],INTAKE)
        self.assertEqual(len(rows),836);rows.pop();report['inventory']['runtime_paths_excluded']=['/proof']
        with self.assertRaisesRegex(RuntimeError,'inventory_receipt'):Q.inventory_gate(report,report['image_id'],INTAKE)

    def test_actual_capsule_and_strict32_receipt_no_historical_global_rewrite(self):
        old=OLD.CAPSULE;image=INTAKE['control']['image_id']
        bound=P.bind_capsule(old,OLD.APP['image_config_id'],image)
        self.assertEqual(bound.replace("'image_config_id':'"+image+"'","'image_config_id':'"+OLD.APP['image_config_id']+"'",1),old)
        self.assertEqual(sha256(old.encode()).hexdigest(),'c2de3f79c7c4acfceab4d5f9e504b4d623ff820e744c2824b35769442b46ed56')
        self.assertEqual(sum(v[1]for v in OLD.SELECTORS.values()),24)
        value={'schema':'app_image_ci_smoke/1','status':'passed','image_config_id':image,
            'runtime':{'expected':8,'run':8,'passed':8,'failures':[]},
            'components':{'expected':24,'run':24,'passed':24,'failures':[],'errors':0,'skipped':0},
            'migration':{'invocation_attempted':True,'child_completed':True,'return_code':0},
            'runtime_cleanup_failures':[],'production_config_guards_active':True,'app_source_masked':False,'real_provider_used':False,
            'security_accepted':False,'full_matrix_accepted':False}
        self.assertIs(P.validate_result(value,image,OLD),value)
        for section,key,bad in (('runtime','passed',7),('components','skipped',1),('components','errors',1),('migration','return_code',1)):
            changed=copy.deepcopy(value);changed[section][key]=bad
            with self.subTest(field=key),self.assertRaises(RuntimeError):P.validate_result(changed,image,OLD)
        self.assertEqual(OLD.CAPSULE,old);self.assertFalse(P.ARMED);self.assertIsNone(P.CONTROL)

    def test_actual_source_call_graph_and_never_started_wire(self):
        tree=ast.parse((HERE/'controller.py').read_bytes());execute=next(n for n in tree.body if isinstance(n,ast.FunctionDef)and n.name=='execute')
        calls=[n.func.attr for n in ast.walk(execute)if isinstance(n,ast.Call)and isinstance(n.func,ast.Attribute)]
        self.assertIn('admit_app',calls);self.assertIn('whole_image_inventory',calls);self.assertIn('run_smoke',calls)
        self.assertNotIn('execute',calls);self.assertNotIn('native_payload',calls)
        self.assertEqual(P.CONTROL,None)
        args=E.create_args(OLD,'OWNED_PROTOCOL_ONLY',INTAKE['control']['image_id'])
        self.assertIn('--pull=never',args);self.assertIn('--network=none',args);self.assertIn('--read-only',args)
        self.assertIn('--cap-drop=ALL',args);self.assertEqual(args[-2:],[INTAKE['control']['image_id'],'--inventory-never-started'])
        self.assertNotIn('start',args)
        actual=ast.get_source_segment(dependency('export_controller.py').read_text(),next(n for n in ast.parse(dependency('export_controller.py').read_bytes()).body if isinstance(n,ast.FunctionDef)and n.name=='whole_image_inventory'))
        self.assertLess(actual.index("'preinspect'"),actual.index("'export'"))
        self.assertLess(actual.index("'export'"),actual.index("'postinspect'"))
        self.assertLess(actual.index('before==after'),actual.index('A.collect'))

    def test_real_source_flags_and_one_shot_physical_close(self):
        with tempfile.TemporaryDirectory(prefix='owned-held-',dir=HERE)as td:
            p=Path(td)/'source.py';p.write_bytes(b'VALUE=1\n');p.chmod(0o644)
            item=S.Held(p,(8,sha256(p.read_bytes()).hexdigest()),owner=os.geteuid(),mode=0o644);fd=item.original_fd
            fcntl.fcntl(fd,fcntl.F_SETFD,0)
            with self.assertRaisesRegex(RuntimeError,'sealed_changed'):item.close()
            self.assertIsNone(item.fd)
            with self.assertRaises(OSError)as caught:os.fstat(fd)
            self.assertEqual(caught.exception.errno,errno.EBADF)

    def test_real_partial_constructor_kept_and_retired(self):
        with tempfile.TemporaryDirectory(prefix='owned-prefix-',dir=HERE)as td:
            p=Path(td)/'source.py';p.write_bytes(b'VALUE=1\n');p.chmod(0o644)
            item=S.Held.__new__(S.Held);published=[item]
            with self.assertRaisesRegex(RuntimeError,'sealed_pin'):
                item.__init__(p,(8,'0'*64),owner=os.geteuid(),mode=0o644)
            fd=item.original_fd;os.fstat(fd);self.assertIs(published[0],item)
            S.finalize(published);self.assertIsNone(item.fd)
            with self.assertRaises(OSError):os.fstat(fd)

    def test_unknown_close_is_retained_and_never_retried(self):
        with tempfile.TemporaryDirectory(prefix='owned-close-',dir=HERE)as td:
            p=Path(td)/'source.py';p.write_bytes(b'VALUE=1\n');p.chmod(0o644)
            item=S.Held(p,(8,sha256(p.read_bytes()).hexdigest()),owner=os.geteuid(),mode=0o644);fd=item.original_fd
            try:
                with patch.object(S.os,'close',side_effect=OSError(errno.EIO,'OWNED_UNKNOWN_CLOSE'))as close:
                    with self.assertRaises(OSError):item.close()
                    self.assertEqual(item.fd,fd);os.fstat(fd)
                    with self.assertRaisesRegex(RuntimeError,'sealed_close_unknown'):item.close()
                    self.assertEqual(close.call_count,1)
            finally:os.close(fd)

    def test_original_deadline_after_real_close_and_all_finalizer_failures(self):
        with tempfile.TemporaryDirectory(prefix='owned-finals-',dir=HERE)as td:
            items=[];fds=[]
            for n in range(2):
                p=Path(td)/str(n);p.write_bytes(b'X');p.chmod(0o644)
                item=S.Held(p,(1,sha256(b'X').hexdigest()),owner=os.geteuid(),mode=0o644);items.append(item);fds.append(item.fd)
            def clock():
                for fd in fds:
                    with self.assertRaises(OSError):os.fstat(fd)
                raise RuntimeError('original_deadline')
            with self.assertRaises(S.FinalizationFailure)as caught:S.finalize(items,clocks=clock)
            self.assertEqual(caught.exception.failures[-1]['code'],'original_deadline')
            sources=Q.ObservationSources(lambda e:Q.closed_error(e,Q.CODES));events=[]
            for n in range(2):
                def close(index=n):events.append(index);raise RuntimeError('OWNED_PRIVATE_TEXT')
                sources.callback(close)
            sources.close();self.assertEqual(events,[1,0]);self.assertEqual(len(sources.failures),2)
            self.assertNotIn('OWNED_PRIVATE_TEXT',json.dumps(sources.failures))

    @unittest.skipUnless(hasattr(os,'fork'),'requires owned fork')
    def test_actual_fd_zero_source_is_closed(self):
        with tempfile.TemporaryDirectory(prefix='owned-fd0-',dir=HERE)as td:
            p=Path(td)/'source.py';p.write_bytes(b'X');p.chmod(0o644)
            pid=os.fork()
            if pid==0:
                code=1
                try:
                    os.close(0);item=S.Held(p,(1,sha256(b'X').hexdigest()),owner=os.geteuid(),mode=0o644)
                    assert item.fd==0;item.close()
                    try:os.fstat(0)
                    except OSError as error:assert error.errno==errno.EBADF
                    else:raise AssertionError('OWNED_FD0_LEAK')
                    code=0
                finally:os._exit(code)
            _,status=os.waitpid(pid,0);self.assertEqual(os.waitstatus_to_exitcode(status),0)

    def test_current_host_admission_is_not_relaxed_for_local_root(self):
        if os.geteuid()==0:
            with self.assertRaisesRegex(RuntimeError,'hosted_ci_only'):Q.hosted(Path('/OWNED_NOT_OPENED'))
            with self.assertRaisesRegex(RuntimeError,'hosted_ci_only'):S.host(Path('/OWNED_NOT_OPENED'))

    def test_real_bootstrap_reader_closes_after_original_controller_refuses_local_root(self):
        if os.geteuid()!=0:return
        # Only the new outer host predicate is bypassed for this owned negative.
        # The actual old.Controller must still refuse Root before any Docker I/O.
        with tempfile.TemporaryDirectory(prefix='owned-inner-prefix-',dir=HERE)as td:
            root=Path(td);(root/'source').mkdir();(root/'reports').mkdir()
            path=root/'source/run_app_image_ci_smoke.py';path.write_bytes(dependency(path.name).read_bytes());path.chmod(0o600)
            keeper=C.ProofKeeper(root);opened=[];real_open=os.open
            def open_owned(operand,*args,**kwargs):
                fd=real_open(operand,*args,**kwargs)
                if Path(operand)==path:opened.append(fd)
                return fd
            with patch.object(Q,'hosted',return_value=None),patch.object(Q.os,'open',side_effect=open_owned):
                self.assertEqual(Q.main(['--root',str(root),'--execute-reviewed-gold2-proof','--confirm',Q.CONFIRM],outer_keeper=keeper),1)
            self.assertEqual(len(opened),1)
            for fd in opened:
                with self.assertRaises(OSError)as caught:os.fstat(fd)
                self.assertEqual(caught.exception.errno,errno.EBADF)
            self.assertEqual(Q.LAST_FAILURE_RECORD['primary_failure']['code'],'hosted_ci_only')
            self.assertTrue(Q.LAST_FAILURE_RECORD['inner_sources_physically_closed'])
            self.assertEqual(Q.LAST_FAILURE_RECORD['inner_finalizer_failures'],[])
            keeper.close()

    def test_new_workflow_is_only_explicit_successor_and_joins_sealer_pin(self):
        path=HERE/'canonical-app-gold2-runtime-smoke.yml'
        if not path.exists():path=HERE.parents[2]/'.github/workflows'/path.name
        raw=path.read_text()
        self.assertIn('default: false',raw)
        self.assertIn("github.ref == 'refs/heads/main' && inputs.run_proof",raw)
        self.assertIn('--source-sha256 '+sha256((HERE/'seal_and_run.py').read_bytes()).hexdigest(),raw)
        self.assertIn('deploy/maintenance/app_gold2_smoke/seal_and_run.py',raw)
        self.assertNotIn('app_6de_smoke/seal_and_run.py',raw)
        self.assertNotIn('app_r2_observe02/seal_and_run.py',raw)
        self.assertIn("if: always() && steps.run.outputs.root != ''",raw)

if __name__=='__main__':unittest.main(verbosity=2)
