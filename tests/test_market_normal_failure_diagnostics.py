"""Owned pure traceback/source-cap controls; no Docker/Git/GH/image actions."""
from pathlib import Path
from hashlib import sha256
from types import ModuleType,SimpleNamespace
import ast,json,unittest

ROOT=Path(__file__).absolute().parents[1]
DRIVER_RAW=(ROOT/'scripts/prepare_market_capture2_image.py').read_bytes()
OUTER_RAW=(ROOT/'scripts/market_business81_outer.py').read_bytes()
WORKFLOW_RAW=(ROOT/'.github/workflows/market-capture2-image.yml').read_bytes()
M=ModuleType('_owned_normal_failure_diagnostics')
M.__file__=str(ROOT/'scripts/prepare_market_capture2_image.py')
exec(compile(DRIVER_RAW,M.__file__,'exec'),M.__dict__)

def captured(filename,kind='AssertionError'):
    namespace={}
    try:
        exec(compile("def public_step():\n private_local='PRIVATE_CANARY'\n raise "+kind+"('PRIVATE_CANARY')\npublic_step()\n",filename,'exec'),namespace)
    except Exception as error:return error
    raise AssertionError('owned failure was not raised')

class NormalFailureDiagnosticsTests(unittest.TestCase):
    def test_builtin_class_and_public_project_position_survive_without_private_values(self):
        error=captured('/OWNED/PRIVATE_CANARY/source/market_sqlite_component_probe.py')
        value=M.closed_failure(error);raw=json.dumps(value)
        self.assertEqual(value['exception_class'],'AssertionError')
        frames=value['diagnostic']['project_frames'];self.assertTrue(frames)
        self.assertTrue(all(r['source']=='scripts/market_sqlite_component_probe.py'for r in frames))
        self.assertTrue(all(type(r['line'])is int and r['line']>0 for r in frames))
        self.assertNotIn('PRIVATE_CANARY',raw);self.assertNotIn('/OWNED/',raw)
        self.assertFalse(value['security_accepted']);self.assertFalse(value['activation_authority'])

    def test_source_io_bootstrap_alias_has_only_its_admitted_relative_path(self):
        error=captured('/OWNED/PRIVATE_CANARY/source/source_io.py','ValueError')
        value=M.failure_diagnostic(error)
        self.assertTrue(value['project_frames'])
        self.assertTrue(all(row['source']=='scripts/security_age132_tool/source_io.py'for row in value['project_frames']))
        self.assertNotIn('PRIVATE_CANARY',json.dumps(value))

    def test_foreign_traceback_paths_args_and_locals_are_never_serialized(self):
        error=captured('/OWNED/PRIVATE_CANARY/private-config.py','AttributeError')
        value=M.failure_diagnostic(error)
        self.assertEqual(value,{'exception_class':'AttributeError','project_frames':[]})
        self.assertNotIn('PRIVATE_CANARY',json.dumps(M.closed_failure(error)))

    def test_group_children_are_bounded_and_keep_public_original_cause(self):
        original=captured('/OWNED/source/prepare_market_capture2_image.py','TypeError')
        grouped=ExceptionGroup('PRIVATE_CANARY',[original]*10)
        value=M.failure_diagnostic(grouped)
        self.assertEqual(value['exception_class'],'ExceptionGroup')
        self.assertEqual(len(value['group_children']),4)
        self.assertTrue(all(v['exception_class']=='TypeError'for v in value['group_children']))
        before=M.OUTER_FAILURE
        try:
            M.OUTER_FAILURE=(grouped,original,())
            public=M.closed_failure(grouped)
            self.assertEqual(public['original_diagnostic']['exception_class'],'TypeError')
            self.assertTrue(public['original_diagnostic']['project_frames'])
            self.assertNotIn('PRIVATE_CANARY',json.dumps(public))
        finally:M.OUTER_FAILURE=before

    def test_driver_purpose_cap_and_exact_outer_hash_join(self):
        self.assertEqual(M.MAX_DRIVER_SOURCE,160*1024)
        self.assertEqual(M.MAX_SOURCE,128*1024)
        self.assertEqual(M.MAX_CONTROLLER_SOURCE,256*1024)
        self.assertLessEqual(len(DRIVER_RAW),M.MAX_DRIVER_SOURCE)
        tree=ast.parse(OUTER_RAW)
        pin=next(ast.literal_eval(n.value)for n in tree.body if isinstance(n,ast.Assign)and any(isinstance(t,ast.Name)and t.id=='NORMAL_PIN'for t in n.targets))
        self.assertEqual(pin,(len(DRIVER_RAW),sha256(DRIVER_RAW).hexdigest()))

    def test_actual_outer_loop_increases_only_normal_driver_source(self):
        tree=ast.parse(OUTER_RAW)
        loop=next(n for n in ast.walk(tree)if isinstance(n,ast.For)and isinstance(n.target,ast.Tuple)and[n.id for n in n.target.elts]==['name','spec']and any(isinstance(c,ast.Attribute)and c.attr=='Held'for c in ast.walk(n)))
        calls=[];scope={'M':SimpleNamespace(Held=lambda path,**kwargs:calls.append((path,kwargs['maximum']))),'rows':[],
                        'source_paths':{'normal':{'path':'driver','binding':{}},'business':{'path':'business','binding':{}},'outer':{'path':'outer','binding':{}}}}
        exec(compile(ast.Module(body=[loop],type_ignores=[]),'<owned actual admission loop>','exec'),scope)
        self.assertEqual(calls,[('driver',163840),('business',131072),('outer',131072)])

    def test_all_workflow_driver_reads_share_the_bounded_160KiB_limit(self):
        text=WORKFLOW_RAW.decode()
        self.assertEqual(text.count('163841'),3)
        self.assertEqual(text.count('163840'),7)
        self.assertNotIn('131073',text)
        self.assertIn('maximum=MAX_DRIVER_SOURCE',DRIVER_RAW.decode())

if __name__=='__main__':unittest.main()
