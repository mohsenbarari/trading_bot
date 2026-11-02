"""Closed-source and owned public-byte checks; no Docker, API or Host action."""
from contextlib import redirect_stdout
from hashlib import sha1,sha256
from pathlib import Path
from types import ModuleType
from unittest.mock import patch
import ast,io,json,os,subprocess,tempfile,unittest
import yaml

SOURCE=Path(__file__).resolve().parents[1];ROOT=Path.cwd().resolve()
P=SOURCE/'scripts/prepare_canonical_app_image.py';M=ModuleType('_owned_native_profile');M.__file__=str(P)
exec(compile(P.read_bytes(),str(P),'exec'),M.__dict__)

class NativeProfileTests(unittest.TestCase):
    def setUp(self):
        self.original=(M.APP_PROFILE,M.BUSINESS_COMMIT,M.BUSINESS_TREE,M.CANONICAL3541)
        self.addCleanup(self.restore)
        temporary=tempfile.TemporaryDirectory(prefix='native-profile-public-');self.addCleanup(temporary.cleanup);self.root=Path(temporary.name)

    def restore(self):M.APP_PROFILE,M.BUSINESS_COMMIT,M.BUSINESS_TREE,M.CANONICAL3541=self.original

    def test_closed_profiles_and_security_scope_cannot_select_Main_business_or_frontend_changes(self):
        self.assertEqual(set(M.APP_PROFILES),{'web','native'});M.select_app_profile('native')
        self.assertEqual((M.BUSINESS_COMMIT,M.BUSINESS_TREE),('ba1f7616ade57558f51bb3a7fbb903243a33aab4','5d2ff35831696a3886372e4388f2a769a3594a8a'))
        self.assertEqual(len(M.security_updates()),len(set(M.security_updates())))
        self.assertEqual({name for name in M.security_updates()if name.startswith('frontend/src/')},{'frontend/src/composables/useWebSocket.ts'})
        self.assertFalse(any(name.startswith('core/market_intelligence/')for name in M.security_updates()))
        self.assertIn('core/jwt_codec.py',M.security_updates());self.assertIn('core/services/chat_upload_session_service.py',M.security_updates())
        for invalid in('main','HEAD','ba1f7616ade57558f51bb3a7fbb903243a33aab4','web/../../main'):
            with self.assertRaisesRegex(RuntimeError,'^app_profile$'):M.select_app_profile(invalid)
        M.select_app_profile('web');self.assertEqual(M.BUSINESS_COMMIT,'4df2fed29859c0b82367666521f1a345f269f58a');self.assertEqual(M.security_updates(),M.SECURITY_UPDATES)

    def test_genuine_native_Git_context_preserves_every_nonsecurity_blob_including_all_frontend_and_Market(self):
        M.select_app_profile('native');head=subprocess.check_output(['/usr/bin/git','--no-optional-locks','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
        old_umask=os.umask(0o077)
        try:
            with patch.dict(os.environ,{'GITHUB_SHA':head}):context,rows=M.frozen_context(self.root,ROOT)
        finally:os.umask(old_umask)
        changed={row['path']for row in rows if row['commit']==head}
        self.assertEqual(changed,{'Dockerfile',*M.security_updates()})
        for row in rows:
            raw=(context/row['path']).read_bytes()
            self.assertEqual(sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest(),row['git_blob'])
            if row['path'].startswith(('frontend/src/','core/market_intelligence/'))and row['path']not in M.security_updates():self.assertEqual(row['commit'],M.BUSINESS_COMMIT)
        for name in('frontend/src/components/JalaliDatePicker.vue','frontend/src/components/profile/ProfileTradeHistory.vue','frontend/src/utils/currentUser.ts'):
            actual=(context/name).read_bytes();old=subprocess.check_output(['/usr/bin/git','--no-optional-locks','show',M.BUSINESS_COMMIT+':'+name],cwd=ROOT)
            newer=subprocess.check_output(['/usr/bin/git','--no-optional-locks','show',M.APP_PROFILES['web'][0]+':'+name],cwd=ROOT)
            self.assertEqual(actual,old);self.assertNotEqual(actual,newer)
        for path in context.rglob('*'):
            if path.is_dir():self.assertEqual(path.stat().st_mode&0o777,0o755)

    def test_actual_source_probe_checks_copied_public_bytes_and_detects_same_path_drift(self):
        tree=ast.parse(P.read_bytes());probe=next(node.value for node in ast.walk(tree)if isinstance(node,ast.Constant)
            and type(node.value)is str and node.value.startswith('import hashlib,json,os,stat\n'))
        path=self.root/'public.py';path.write_bytes(b'OWNED PUBLIC SOURCE\n');raw=path.read_bytes()
        vector=M.encode([{'path':'/app/api/public.py','bytes':len(raw),'sha256':sha256(raw).hexdigest()}])
        real_open=os.open;real_lstat=os.lstat
        def opened(name,*args,**kwargs):
            self.assertEqual(name,'/app/api/public.py');return real_open(path,*args,**kwargs)
        def named(name,*args,**kwargs):
            self.assertEqual(name,'/app/api/public.py');return real_lstat(path,*args,**kwargs)
        def source_file(name,*args,**kwargs):self.assertEqual(name,'/proof/source-inputs.json');return io.BytesIO(vector)
        with patch('builtins.open',side_effect=source_file),patch.object(os,'open',side_effect=opened),patch.object(os,'lstat',side_effect=named),redirect_stdout(io.StringIO())as output:
            exec(compile(probe,'<owned-copied-source-probe>','exec'),{'__name__':'_owned_fixture'})
        result=json.loads(output.getvalue());self.assertEqual(result['status'],'PASS');self.assertEqual(result['verified_files'],1)
        self.assertEqual(result['vector_sha256'],sha256(vector).hexdigest())
        path.write_bytes(b'CHANGED PUBLIC SOURCE\n')
        with patch('builtins.open',side_effect=source_file),patch.object(os,'open',side_effect=opened),patch.object(os,'lstat',side_effect=named),self.assertRaises(AssertionError):
            exec(compile(probe,'<owned-source-drift>','exec'),{'__name__':'_owned_fixture'})

    def test_workflow_carries_closed_profile_to_all_real_phases_and_default_Web_is_preserved(self):
        data=yaml.safe_load((SOURCE/'.github/workflows/canonical-app-image.yml').read_text());trigger=data.get('on',data.get(True))
        selector=trigger['workflow_dispatch']['inputs']['app_profile'];self.assertEqual(selector['options'],['web','native']);self.assertEqual(selector['default'],'web')
        self.assertIn('canonical-app-image-evidence-native',data['concurrency']['group'])
        phases=[]
        for step in data['jobs']['candidate']['steps']:
            text=step.get('run','')
            if "<<'PY'\n" not in text:continue
            code=text.split("<<'PY'\n",1)[1].rsplit('\nPY',1)[0];ast.parse(code)
            self.assertIn("['--app-profile',os.environ['APP_PROFILE']]",code)
            self.assertEqual(step['env']['APP_PROFILE'],'${{ inputs.app_profile }}');phases.append(code)
        self.assertEqual(len(phases),5)

    def test_a_Web_phase_cannot_adopt_prepared_Native_source_identity(self):
        (self.root/'app-context').mkdir();M.select_app_profile('web');M.CANONICAL3541=True
        native=M.APP_PROFILES['native']
        value={'schema':'canonical_app_source/1','repo':str(ROOT),'source_commit':'a'*40,'source_tree':'b'*40,
            'run_id':'1','run_attempt':'1','canonical3541':True,'app_profile':'native','business_commit':native[0],'business_tree':native[1],
            'context_repo':str(self.root/'app-context')}
        M.write_new(self.root/'source-identity.json',value)
        with patch.dict(os.environ,{'GITHUB_RUN_ID':'1','GITHUB_RUN_ATTEMPT':'1'}),patch.object(M,'hosted'),patch.object(M,'git_identity'),self.assertRaisesRegex(RuntimeError,'^state$'):
            M.state(self.root)

if __name__=='__main__':unittest.main()
