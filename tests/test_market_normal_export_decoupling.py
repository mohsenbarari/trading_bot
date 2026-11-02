"""Real owned TAR/gzip/report checks with only external build observations stubbed."""
from contextlib import ExitStack
from hashlib import sha256
from pathlib import Path
from types import ModuleType,SimpleNamespace
from unittest.mock import patch
import gzip,io,json,os,tarfile,tempfile,unittest

ROOT=Path(__file__).absolute().parents[1]
M=ModuleType('owned_normal_export_decoupling');M.__file__=str(ROOT/'scripts/prepare_market_capture2_image.py')
exec(compile(Path(M.__file__).read_bytes(),M.__file__,'exec'),M.__dict__)

class NormalExportDecouplingTests(unittest.TestCase):
    def fixture(self,*,wrong_report=False,wrong_archive=False):
        temporary=tempfile.TemporaryDirectory(prefix='.owned-normal-export-',dir=Path(__file__).parent);self.addCleanup(temporary.cleanup)
        root=Path(temporary.name);root.chmod(0o700);(root/'reports').mkdir(mode=0o700)
        layers=['sha256:'+'a'*64];config=M.encoded({'rootfs':{'type':'layers','diff_ids':layers}});image='sha256:'+sha256(config).hexdigest()
        if wrong_archive:config=M.encoded({'rootfs':{'type':'layers','diff_ids':['sha256:'+'f'*64]}})
        outgoing=io.BytesIO()
        with tarfile.open(fileobj=outgoing,mode='w')as archive:
            for name,raw in(('manifest.json',M.encoded([{'Config':'config.json','Layers':[]}])),('config.json',config)):
                item=tarfile.TarInfo(name);item.size=len(raw);archive.addfile(item,io.BytesIO(raw))
        archive_raw=outgoing.getvalue();receipt={'schema':'normal_market_git_component_inputs/1','release_commit':'b'*40,'release_tree':'c'*40,'business_commit':'d'*40,'business_tree':'e'*40,'repository_identity':{'host':'github.com','repository':M.REPOSITORY}}
        signature='1'*64;csig='2'*64;controller={'controller_files':[],'normal_cache_files':[]}
        build={'schema':'normal_market_capture2_image_build/1','status':'built_not_accepted','security_accepted':False,'source_inputs':receipt,
            'two_git_input_signature':signature,'controller_input_signature':csig,'image_id':image,'run_id':123,'run_attempt':1,
            'controller_inputs':controller,'actual_source93_completed':True,'actual_sqlite_completed':True,'historical81_business_tests_completed':True,
            'historical81':{'owned_fixture':True},'complete_ci_controller_payload':True,'controller_parcel':{'owned_fixture':True}}
        inputs={'two_git_input_signature':signature,'controller_input_signature':csig,'release':{},'product':{}}
        scan={'Metadata':{'ImageID':'sha256:'+'f'*64 if wrong_report else image},'Results':[{'Vulnerabilities':[{'Severity':'HIGH'}for _ in range(45)]}]}
        sbom={'metadata':{'component':{'type':'container','name':image,'properties':[{'name':'aquasecurity:trivy:ImageID','value':image}]}}}
        for name,value in(('image-build.json',build),('input-manifest.json',inputs),('reports/vulnerabilities.json',scan),('reports/sbom.cdx.json',sbom)):
            path=root/name;path.write_bytes(M.encoded(value));path.chmod(0o600)
        inspected={'Id':image,'RootFS':{'Type':'layers','Layers':layers},'Config':{'User':'10001:10001'}}
        calls=[]
        def command(argv,**kwargs):
            calls.append(argv)
            if argv[len(M.DOCKER):len(M.DOCKER)+2]==['image','inspect']:return M.encoded(inspected)
            self.assertEqual(argv,[*M.DOCKER,'image','save',image]);self.assertEqual(kwargs['maximum'],2*1024**3);self.assertEqual(kwargs['seconds'],600)
            kwargs['guard']();fd=kwargs['target'];offset=0
            while offset<len(archive_raw):offset+=os.write(fd,archive_raw[offset:offset+4096])
            kwargs['stream_complete']({'bytes':len(archive_raw),'sha256':sha256(archive_raw).hexdigest()},M.identity(os.fstat(fd)))
            return None
        def snapshot(*args,**kwargs):return SimpleNamespace(check=lambda:None,inventory=lambda:None,close=lambda:None,named=lambda:None)
        sealer=SimpleNamespace(GitRepository=lambda repo:repo,collect=lambda *args,**kwargs:(receipt,signature,{}))
        return root,receipt,signature,csig,controller,inspected,archive_raw,calls,command,snapshot,sealer

    def invoke(self,fixture):
        root,receipt,signature,csig,controller,inspected,archive_raw,calls,command,snapshot,sealer=fixture
        with ExitStack()as stack:
            for name,value in{'hosted':lambda *args:None,'reopen_glibc_runtime':lambda *args:{},'git_source':lambda *args:b'# OWNED PUBLIC SOURCE',
                'module':lambda *args:sealer,'ContextSnapshot':snapshot,'receipt_contract':lambda *args:(controller,csig),'source_epoch':lambda *args:1792972800,
                'live_main':lambda *args:{'sha':receipt['release_commit']},'image_contract':lambda value,*args:self.assertEqual(value,inspected),'command':command}.items():
                stack.enter_context(patch.object(M,name,value))
            stack.enter_context(patch.object(M.os,'environ',{'GITHUB_SHA':receipt['release_commit'],'GITHUB_RUN_ID':'123','GITHUB_RUN_ATTEMPT':'1'}))
            stack.enter_context(patch.object(M.os,'statvfs',lambda path:SimpleNamespace(f_bavail=2**30,f_frsize=4096)))
            forbidden=stack.enter_context(patch.object(M,'collect_sqlite_inventory',side_effect=AssertionError('experimental collector must not gate ordinary transport')))
            value=M.finalize(root,Path('/OWNED-NOT-OPENED'))
            forbidden.assert_not_called();return value

    def test_actual_owned_export_compression_and_report_bindings_survive_without_research_collector(self):
        fixture=self.fixture();root=fixture[0];before=(root/'reports/vulnerabilities.json').read_bytes();value=self.invoke(fixture)
        compressed=(root/'candidate-image.tar.gz').read_bytes();self.assertEqual(gzip.decompress(compressed),fixture[6])
        self.assertEqual(value['archive']['sha256'],sha256(compressed).hexdigest());self.assertEqual(value['image_id'],fixture[5]['Id'])
        self.assertEqual(value['raw_counts']['HIGH'],45);self.assertEqual((root/'reports/vulnerabilities.json').read_bytes(),before)
        self.assertEqual(value['sqlite_component_inventory'],{'status':'UNASSESSED','collection_performed':False,'source_link_join':'MISSING','security_accepted':False})
        self.assertFalse(value['security_accepted']);self.assertFalse(value['activation_authority']);self.assertFalse((root/'reports/sqlite-component-inventory.json').exists())
        self.assertEqual(json.loads((root/'image-export.json').read_bytes()),value)

    def test_foreign_image_scan_is_still_refused_before_any_archive_is_created(self):
        fixture=self.fixture(wrong_report=True)
        with self.assertRaisesRegex(RuntimeError,'^report_identity$'):self.invoke(fixture)
        self.assertFalse((fixture[0]/'candidate-image.tar').exists())

    def test_wrong_saved_image_configuration_is_still_refused_before_compression(self):
        fixture=self.fixture(wrong_archive=True)
        with self.assertRaisesRegex(RuntimeError,'^archive_config$'):self.invoke(fixture)
        self.assertFalse((fixture[0]/'candidate-image.tar.gz').exists())

if __name__=='__main__':unittest.main()
