"""Owned public files/inert direct children only; no Git/GH/Docker/image actions."""
from contextlib import redirect_stdout
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from types import ModuleType,SimpleNamespace
from unittest.mock import patch
import ast,io,json,os,signal,subprocess,sys,tempfile,time,unittest
REPO=Path(__file__).resolve().parents[1]
HERE=REPO/'deploy/maintenance/ba938_smoke'
TEST_ROOT=Path(__file__).resolve().parent

def load(leaf):
    p=HERE/leaf;m=ModuleType('_owned_'+leaf.replace('.','_'));m.__file__=str(p)
    exec(compile(p.read_bytes(),str(p),'exec'),m.__dict__);return m
C=load('controller.py');B=load('compose_security_os_candidate.py');S=load('seal_and_run.py')
O=load('run_app_image_ci_smoke.py')

# Independent source fingerprints replace all private local-history dependencies.
BASE_CONTROLLER_FUNCTIONS={'need': '1fe2d0598daa28e10782c55504f1d1a40060524468f76ee970ef1c29b6ede1b4', 'encode': 'fa5e1399e78169699b840aefb865faba7d2b161e6f9b1537d4bfc49f1b3bcb6c', 'document': '8dc4e2e46abcea25168a65806bbd9d6543995ada656d527e7b3c9588212714d4', 'same': '8b7dc11bea006780516dc01157a92cd8b9a2741e5f855181ba886d1db170c871', 'validate_control': 'a22f7c4592baeb5a8be15d2efab271982139d7bf5c4af5635eebeaa0adf911d2', 'hosted': '29c1813f41ec82c2a43451cdff0733e711cf810c0329490d3988960567207fd2', 'source_module': 'c4c7e384d31959858bc7f8cd9ecf15e407f6c7e014050affe94b304578734d58', 'canonical_legacy_configs': '797b13408141c52de952ed8658a34e31fe24edb107fd8a8d82d49b349d8e8023', 'admit_evidence': '14d12ba0b7c6e26e12ed7a9aa808e369d2386e71cd735eb5e9f53a22bf11bf6a', 'read_zip': 'ab2d2670aa674971391c79710edcf111ffdc4fc24a76e5775c7604237b5d7ac7', 'export_record': '71d802a2323b0f2d197ae233eb14f640393706af3bca205d5a9ada565186c1c1', 'archive_config': 'ef4c93f75165bde394f75981d4eef437421b87e47a38ff4b111ed44ca33c922d', 'validate_archive': 'ff70480effd0274d92cdd6f694abf0a040c23f157a45806aec5aba2ab3797b8a', 'admit_app': '8651cbe303f7787d364c4473369f92d51d54ee4fbb69b9bbe2111e6b4ac099b4', 'bind_capsule': 'f34ee05272550229e3e2715c0cb947dfee5558a3b420f0bf2f0e7782da84db33', 'validate_result': 'ea0f9300ab80c5e1da9e7069dd87db59750a5dae5306539c9978907c8e1a4d45', 'presence_args': 'f0570daa53d2aa88a27b13d30c4b1ca88a4365f04aa1ccae992fa939fb0fdc8f', 'presence': '79a12ec21e2eae60aa607c2b314b3e8956468ba1538a86b3886c5eca49b679a1', 'native_payload': '42bc8db07d3e81201b5e3e3c80bd98347c9aef3a7d8b4e360d75dff78c2c8387', 'run_smoke': 'c8b23e4735f06b27215519f9d41a6b061e7f138bd1ded2a6e6cec43e6e22c425', 'finish_cleanup': '29329adbd2a1181f5cee3e0c9759164864e9ff9085f815a259d75a08ec2f9a35'}
BASE_CONTROLLER_NAMES=('need', 'encode', 'document', 'same', 'validate_control', 'hosted', 'source_module', 'canonical_legacy_configs', 'admit_evidence', 'read_zip', 'export_record', 'archive_config', 'validate_archive', 'admit_app', 'bind_capsule', 'validate_result', 'presence_args', 'presence', 'native_payload', 'run_smoke', 'execute', 'finish_cleanup', 'main')
BASE_HELPER_FUNCTIONS={'require': '429775561c8d379d92ba188e15be588d530ce8ab079c8bccb57e85fd2bc41820', 'read': 'd67e16de92ec81284f9997b60ce2a4cfad2421ce05e145bc4812eb55fb66ae8d', 'encoded': 'd6ed43f00252a1549b5a99a15ca4b57db2b4e12568928944aad8ccd5fa82363b', 'public_error': '74f2f4b9fa8413fbdfeafcf16016e46453d96782044c9eee6210cada03b14851', 'phase_state': 'ec69155f5b38ee775fd31c52fed101afee50342ebd0712ce68f8e21c30a948a3', 'save_phase': '0c55395d50c4809f934fcfacfa7de21867898a935a275e74d605d62bb3393fa6', 'phase': '00f21ea7978d976b19a811e79f364bf679251786420da2eb7d99920b1036d81b', 'failed_phase': '8248d19e3373470529703c4d8f841ebfa0e407ebdc3ee2a7275a470784885e75', 'stream_digest': 'c3f069e37a5f2f06bd26d7d39314ced8da000ca35dfa36b3c20eaed439333f84', 'admit_packages': 'f237dd9a1ba048c4bd065a49be29c9eefc508d61394e85982aa6cd11b8793fae', 'admit_test_proof': 'c7d12832291cdfe7aadd334ac5aeb78e5468db0564cb498dd812e9d9bb9294dc', 'fetch_tests': '1b707f57924e48473dd27d632cabca938e9cef0ae0b451cbacaec6b40929414e', 'dockerfile_for': 'd412381898f5030aea54e15419635c54a9cf0d08b74a598bcdabba2ec1d5f333', 'inspect_builder': 'cd8c6b80ef4dc0b750702dd70de1ecc7fd1febea9cf091a00d38c55546420a5c', 'parse_builder': '0d7d9ba7b33f38c13ce21a3ff595064e59218df1437d9246b3ab7e50810751f5', 'image_record': '4e09efb50c7b0a1e48f044ad37fc2d152c00575ea8e3e90605c163b76b5668d7', 'archive_config': 'bc2d5822acefb625e70a13243122506229b56696ed7741c37724b061e192ec7a', 'compare_images': 'adcd86fa04119a66e78456dd366dccb8047bc427b2d461b96f4db82fc9ab62c2', 'compare_inventory': '2e15782777df57a3516889416c6bea536672678ece4253257aca6c74dfe4cf1f', 'docker': 'afbb98b3478b9663345c30a09a01bf2b19f979ad8e416fd57fad0e57023bb24c', 'base_alias_plan': 'e9d19f5d58030c154df9cee51200e65bf4cae3974bacffc066454b809334f3fd', 'cleanup': '9fc1359047476ae922cf160abf9210ec46dcd95c36069120fd23b9c6a7f5fb89', 'cleanup_isolated': '8ee3d027be91aa2c35788d6a2d1f1070bda466c69579625265ad30c142825c5d', 'isolated': '1db1dab715fa456973e8abe8024c58aca1aeb6b019f7159e938fae8fb75faf61', 'validate_component': 'e84d64a7390d7952da892802e9b40922f4eafe85f07bb1fffd995ef53623a9a2', 'evidence_hashes': 'd53ecfa7a60e497ff2b088fc1e48a457c33e949e84dd9d49ecf3cbfb6f08b5bc', 'validate_acl_receipt': 'dd03d381f13216d89aeb15a8fea93d3b53d92e420626c01bc8979f293525753c', 'acl_consumer_probe': '77bd166c041d6fd1b403d01e00ef94952afb647fa38b34db72a4b246b4102839', 'load_sealed': '4fea754bd9621dbedb555b69adbca8bd40427355e80052d4600c027f5c0fa5cd', 'ci_root': '5b815fd6dca7ebb445bb9323fac3d227dd1f37dddeb271403553f4cd7f921680', 'compose': 'e22d8db4559f3aff1e1c7c24e3a9dc4df9ca77b3b6b8337f94c2c13e0b939d09', 'validate_reports': '758c81559f7babad74991bbf81dcda4f739c7ce9c95fb73ca86c8f2c35dace97', 'finalize': '4b0fb6df89889786f6a22e685d6fba5cacdf87c27a9d076b2dddc8c1593abcf2', 'main': '0fe5b917004dec2fc6671adce12f3d6be1b57d22028e91abbbba614ac2f62c39'}
BASE_HELPER_NAMES=('require', 'pinned', 'read', 'write', 'encoded', 'public_error', 'phase_state', 'save_phase', 'phase', 'failed_phase', 'stream_digest', 'command', 'fetch_artifact', 'admit_packages', 'admit_test_proof', 'fetch_tests', 'dockerfile_for', 'inspect_builder', 'parse_builder', 'image_record', 'archive_config', 'compare_images', 'compare_inventory', 'docker', 'base_alias_plan', 'cleanup', 'cleanup_isolated', 'isolated', 'validate_component', 'evidence_hashes', 'validate_acl_receipt', 'acl_consumer_probe', 'load_sealed', 'ci_root', 'compose', 'validate_reports', 'export_image', 'finalize', 'main')

def function_fingerprints(leaf):
    text=(HERE/leaf).read_text()
    return {n.name:sha256(ast.get_source_segment(text,n).encode()).hexdigest()for n in ast.parse(text).body if isinstance(n,(ast.FunctionDef,ast.ClassDef))}


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory(prefix='.owned-ba938-',dir=TEST_ROOT);self.addCleanup(tmp.cleanup);self.root=Path(tmp.name)
    def test_default_controller_and_wrapper_cannot_read_host_or_run_proof(self):
        for m,args in ((C,['--root','/UNOPENED']), (S,[])):
            with self.subTest(module=m.__name__),patch.object(m,'hosted' if m is C else'run',side_effect=AssertionError('host denied')),redirect_stdout(io.StringIO())as stream:
                self.assertEqual(m.main(args),0)
            self.assertEqual(json.loads(stream.getvalue())['status'],'UNARMED')
    def test_actual19_expectation_only_arming_field_changes_and_source_pin_remains(self):
        raw=(HERE/'native-expectation.source19.UNARMED.json').read_bytes()
        self.assertEqual((len(raw),sha256(raw).hexdigest()),(34994,'2cf0e5410381cd0e0303611e72949daed67e4fd12d7913de2912775f53df51cb'))
        original=json.loads(raw);armed=json.loads((HERE/'native-expectation.json').read_bytes())
        self.assertEqual({k for k in original if original[k]!=armed[k]},{'armed'});self.assertIs(original['armed'],False);self.assertIs(armed['armed'],True)
        self.assertEqual((len(armed['packages']),armed['native_file_count'],armed['alias_count']),(19,154,28));self.assertEqual(armed['missing_package_sources'],[])
        self.assertFalse(armed['actual_installed_payload_measured']);self.assertFalse(armed['security_accepted'])
        self.assertEqual(sha256((HERE/'native-expectation.json').read_bytes()).hexdigest(),C.NATIVE_PAYLOAD_EXPECTATION_SHA256)
    def test_original_frozen_source_cohort_and_runtime32_functions_are_unchanged(self):
        actual=function_fingerprints('controller.py')
        self.assertEqual({name:actual[name]for name in BASE_CONTROLLER_FUNCTIONS},BASE_CONTROLLER_FUNCTIONS)
        self.assertEqual(set(actual),set(BASE_CONTROLLER_NAMES)|{'inventory_document','inventory_args','inventory_container','whole_image_inventory','inventory_inspect_template','inventory_cli'})
        self.assertEqual(actual['inventory_args'],'d3fb65741e52a95a2064755b169cf09bd5701ec473e36749cb5606af26f8010f')
        self.assertEqual(actual['inventory_container'],'dc1e07511f092302a4bb9fadcb75d278bd76843f20f1f7f125b419c800298e6a')
        tree=ast.parse((HERE/'controller.py').read_bytes())
        factory=next(node for node in tree.body if isinstance(node,ast.FunctionDef)and node.name=='inventory_inspect_template')
        expression=deepcopy(factory.body[0].value);changes=[]
        for node in ast.walk(expression):
            if isinstance(node,ast.Constant)and type(node.value)is str and '{{json (index .HostConfig "Tmpfs")}}'in node.value:
                changes.append(True);node.value=node.value.replace('{{json (index .HostConfig "Tmpfs")}}','{{json .HostConfig.Tmpfs}}')
        self.assertEqual(changes,[True])
        self.assertEqual(sha256(ast.dump(expression).encode()).hexdigest(),'1f3d4e5e40b7d1ac275d317d29593ae7948414e42876b17a29c49397880674cc')
        self.assertEqual(sum(value[1]for value in O.SELECTORS.values()),24)
        self.assertEqual(C.CONTROL['image_id'],'sha256:ba938a7dd1ab1a0d9ebacdae768861a45276ba7caacead40ed319fa8e1bef218')
        C.validate_control(C.CONTROL)
    def test_only_cleanup_and_proof_lifetime_functions_change_in_executed_helper(self):
        actual=function_fingerprints('compose_security_os_candidate.py')
        self.assertEqual({name:actual[name]for name in BASE_HELPER_FUNCTIONS},BASE_HELPER_FUNCTIONS)
        self.assertEqual(set(actual),set(BASE_HELPER_NAMES)|{'cleanup_owned_child','_proof_identity','_ProofParents','_ProofFile','ProofKeeper'})
        self.assertNotIn('child.poll()', (HERE/'compose_security_os_candidate.py').read_text())
        # This full-file normalization proves the CAPSULE/all32 assertions and
        # every old wrapper global/function remain byte-identical, not just AST.
        raw=(HERE/'run_app_image_ci_smoke.py').read_bytes();actualpin=O.HELPERS['compose_security_os_candidate.py'][1].encode()
        self.assertEqual(raw.count(actualpin),1)
        normalized=raw.replace(actualpin,b'10ccbe216fef04e5f7a7ce2ad31ac30eca7eeae090dfaaf8074fa3cc5c789d44',1)
        self.assertEqual(sha256(normalized).hexdigest(),'09221619f6929df66f2622de8911913e8bf3770c96001ae5e06d77a9851abb62')
        self.assertEqual(actualpin.decode(),sha256((HERE/'compose_security_os_candidate.py').read_bytes()).hexdigest())
    def command(self,code,seconds=2):return B.command([sys.executable,'-I','-S','-B','-c',code],seconds=seconds,maximum=128)
    def test_real_successful_and_nonzero_reaped_command_never_signalled(self):
        with patch.object(B.os,'killpg',side_effect=AssertionError('postreap signal denied')):
            self.assertEqual(self.command("print('PUBLIC')"),b'PUBLIC\n')
            with self.assertRaisesRegex(RuntimeError,'command_failed'):self.command("print('PUBLIC');raise SystemExit(7)")
    def wrapped_spawn(self,kind,*,stream=False):
        real=subprocess.Popen;children=[]
        class Wrapped:
            def __init__(self,child):self.child=child;self.first=True
            def __getattr__(self,name):return getattr(self.child,name)
            def wait(self,*args,**kw):
                if self.first:
                    self.first=False
                    if kind=='interrupted':self.child.wait(*args,**kw);raise RuntimeError('owned_after_reap')
                    if kind=='timeout':return self.child.wait(timeout=.02)
                return self.child.wait(*args,**kw)
        def spawn(argv,*args,**kw):
            if stream:
                self.assertEqual(argv[:4],['gh','api','--method','GET']);self.assertIn('/actions/artifacts/123/zip',argv[-1])
                self.assertEqual(kw['env']['GH_TOKEN'],'PUBLIC-OWNED-FIXTURE')
                code='import time;time.sleep(2)'if kind=='timeout'else"import sys;sys.stdout.buffer.write(b'PUBLIC-ARTIFACT');"+('raise SystemExit(7)'if kind=='nonzero'else'')
                argv=[sys.executable,'-I','-S','-B','-c',code]
            child=real(argv,*args,**kw);children.append(child);return Wrapped(child)if kind in('interrupted','timeout')else child
        return spawn,children
    def test_interrupted_real_reap_command_never_signalled(self):
        spawn,children=self.wrapped_spawn('interrupted')
        with patch.object(B.subprocess,'Popen',spawn),patch.object(B.os,'killpg',side_effect=AssertionError('reaped group denied')),self.assertRaisesRegex(RuntimeError,'owned_after_reap'):self.command("print('PUBLIC')")
        self.assertEqual(children[0].returncode,0)
    def test_actual_live_timeout_still_proves_kernel_ownership_then_kills_and_reaps(self):
        spawn,children=self.wrapped_spawn('normal');real_waitid=os.waitid;real_killpg=os.killpg;signals=[]
        def killpg(pid,sig):
            self.assertEqual(pid,children[0].pid);self.assertIsNone(children[0].returncode)
            real_waitid(os.P_PID,pid,os.WEXITED|os.WNOHANG|os.WNOWAIT);signals.append((pid,sig));return real_killpg(pid,sig)
        with patch.object(B.subprocess,'Popen',spawn),patch.object(B.os,'killpg',killpg),self.assertRaisesRegex(RuntimeError,'command_timeout'):self.command('import time;time.sleep(2)',seconds=.02)
        self.assertEqual(signals,[(children[0].pid,signal.SIGKILL)]);self.assertIsNotNone(children[0].returncode)
    def fetch(self,kind):
        spawn,children=self.wrapped_spawn(kind,stream=True);raw=b'PUBLIC-ARTIFACT';target=self.root/('artifact-'+kind)
        with patch.object(B.subprocess,'Popen',spawn),patch.object(B.os,'environ',{'GH_TOKEN':'PUBLIC-OWNED-FIXTURE'}):
            B.fetch_artifact(123,target,len(raw),sha256(raw).hexdigest())
        return target,children
    def test_real_owned_artifact_child_success_nonzero_and_interrupted_reap_no_signal(self):
        with patch.object(B.os,'killpg',side_effect=AssertionError('reaped group denied')):
            target,children=self.fetch('normal');self.assertEqual(target.read_bytes(),b'PUBLIC-ARTIFACT');self.assertEqual(children[0].returncode,0)
            with self.assertRaisesRegex(RuntimeError,'artifact_download_failed'):self.fetch('nonzero')
            with self.assertRaisesRegex(RuntimeError,'owned_after_reap'):self.fetch('interrupted')
    def test_real_owned_artifact_timeout_kills_and_reaps_only_current_child(self):
        spawn,children=self.wrapped_spawn('timeout',stream=True);signals=[];real=os.killpg
        def killpg(pid,sig):
            self.assertEqual(pid,children[0].pid);self.assertIsNone(children[0].returncode);os.waitid(os.P_PID,pid,os.WEXITED|os.WNOHANG|os.WNOWAIT);signals.append(pid);return real(pid,sig)
        with patch.object(B.subprocess,'Popen',spawn),patch.object(B.os,'killpg',killpg),patch.object(B.os,'environ',{'GH_TOKEN':'PUBLIC-OWNED-FIXTURE'}),self.assertRaises(subprocess.TimeoutExpired):
            B.fetch_artifact(123,self.root/'timeout-artifact',16,sha256(b'PUBLIC-ARTIFACT').hexdigest())
        self.assertEqual(signals,[children[0].pid]);self.assertIsNotNone(children[0].returncode)
    def test_whole_inventory_exact_source_and_isolation_argv(self):
        raw=(HERE/'inventory.py').read_bytes();self.assertEqual((len(raw),sha256(raw).hexdigest()),(27258,C.INVENTORY_SHA256))
        args=C.inventory_args(O,'OWNED-NAME',C.CONTROL['image_id'],Path('/OWNED-PROOF'),os.getegid(),C.CONTROL)
        self.assertIn('--network=none',args);self.assertIn('--read-only',args);self.assertIn('--cap-drop=ALL',args);self.assertIn('--user=0:0',args)
        self.assertEqual(args[args.index('/proof/inventory.py')-3:args.index('/proof/inventory.py')],['-I','-S','-B'])
        mounts=[args[i+1]for i,v in enumerate(args)if v=='--mount'];self.assertEqual(mounts,['type=bind,source=/OWNED-PROOF,target=/proof,readonly'])
        self.assertNotIn('fsize=2097153:2097153',args)
    def container(self):
        return{'name':'/OWNED-NAME','image':C.CONTROL['image_id'],'owner':O.owner(),'readonly':True,'privileged':False,'user':'0:0','network':'none','networks':{},
            'caps_add':None,'caps_drop':['ALL'],'security':['no-new-privileges'],'memory':512*1024**2,'memory_swap':512*1024**2,'nano_cpus':10**9,'pids':64,'tmpfs':None,
            'mounts':[{'Type':'bind','Source':'/OWNED-PROOF','Destination':'/proof','RW':False}],'running':False,'paused':False,'pid':0}
    def test_actual_inspection_contract_refuses_foreign_mount_network_privilege_and_pid_types(self):
        row=self.container();C.inventory_container(row,O,'OWNED-NAME',C.CONTROL['image_id'],Path('/OWNED-PROOF'))
        for key,value in [('readonly',False),('privileged',True),('user','10001:10001'),('network','bridge'),('networks',{'bridge':{}}),('caps_add',['CAP_SYS_ADMIN']),('caps_drop',[]),('memory',128),('pid',False),('running',True),('tmpfs',{'/app':'rw'})]:
            with self.subTest(key=key),self.assertRaises(RuntimeError):C.inventory_container({**row,key:value},O,'OWNED-NAME',C.CONTROL['image_id'],Path('/OWNED-PROOF'))
        bad=deepcopy(row);bad['mounts'].append({'Type':'bind','Source':'/PRIVATE','Destination':'/private','RW':False})
        with self.assertRaises(RuntimeError):C.inventory_container(bad,O,'OWNED-NAME',C.CONTROL['image_id'],Path('/OWNED-PROOF'))
    def test_whole_inventory_parser_rejects_duplicate_nonfinite_and_before_output_bound(self):
        for raw in(b'{"a":1,"a":2}',b'{"a":1e999}',b'{"a":NaN}',b'X'*(C.MAX_ACL_REPORT+1)):
            with self.subTest(size=len(raw)),self.assertRaises(RuntimeError):C.inventory_document(raw)
    def test_inventory_precedes_every_other_installed_image_probe_after_graph_load(self):
        tree=ast.parse((HERE/'controller.py').read_bytes());fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef)and n.name=='execute')
        calls=[n.func.id for n in ast.walk(fn)if isinstance(n,ast.Call)and isinstance(n.func,ast.Name)]
        # The top-level calls are ordered by their concrete source positions.
        nodes=sorted((n for n in ast.walk(fn)if isinstance(n,ast.Call)and isinstance(n.func,ast.Name)and n.func.id in('admit_app','whole_image_inventory','presence','native_payload','run_smoke')),key=lambda n:n.lineno)
        self.assertEqual([n.func.id for n in nodes],['admit_app','whole_image_inventory','presence','native_payload','run_smoke'])
    def test_wrapper_holds_every_declared_source_pin_and_bound_originals(self):
        self.assertEqual(len(S.SOURCES),18)
        for leaf,pin in S.SOURCES.items():
            raw=(HERE/leaf).read_bytes();self.assertEqual((len(raw),sha256(raw).hexdigest()),pin)
        self.assertEqual(C.OLD_PIN,S.SOURCES['run_app_image_ci_smoke.py'][1]);self.assertEqual(C.BUILDER_PIN,S.SOURCES['prepare_canonical_app_image.py'][1])
    def test_real_late_outer_source_close_cannot_change_already_closed_report(self):
        source=self.root/'source';report=self.root/'report';source.write_bytes(b'OWNED SOURCE');report.write_bytes(b'OWNED REPORT');source.chmod(0o600);report.chmod(0o600)
        held=[S.Held(p,None,owner=os.geteuid(),mode=0o600)for p in(source,report)];source_inode=(source.stat().st_dev,source.stat().st_ino);real=os.close;changed=[]
        def close(fd):
            info=os.fstat(fd);real(fd)
            if(info.st_dev,info.st_ino)==source_inode and not changed:changed.append(True);report.write_bytes(b'OWNED LATE REPORT CHANGE')
        with patch.object(S.os,'close',close),self.assertRaisesRegex(RuntimeError,'sealed_finalization'):S.finalize(held)
        self.assertTrue(changed);self.assertTrue(all(item.fd is None for item in held))
    def test_real_staged_parent_change_rejected_before_any_payload_read(self):
        parent=self.root/'parent';parent.mkdir();path=parent/'file';path.write_bytes(b'OWNED');path.chmod(0o600)
        item=S.Held(path,None,owner=os.geteuid(),mode=0o600);parent.rename(self.root/'retained');parent.mkdir();path.write_bytes(b'OTHER');path.chmod(0o600)
        try:
            with self.assertRaisesRegex(RuntimeError,'sealed_changed'):item.check()
        finally:os.close(item.fd);item.fd=None


if __name__=='__main__':unittest.main()
