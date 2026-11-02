"""Labelled public protocol fixtures; no API, image, package install or Host action."""
from contextlib import redirect_stdout
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from types import ModuleType
from unittest.mock import patch
import io,json,os,tempfile,unittest

ROOT=Path.cwd().resolve();SOURCE=Path(__file__).resolve().parents[1]
P=SOURCE/'scripts/prepare_canonical_app_image.py'
M=ModuleType('_owned_app_prepare_fix');M.__file__=str(P);exec(compile(P.read_bytes(),str(P),'exec'),M.__dict__)
POLICY=ModuleType('_owned_existing_retained_policy');POLICY.__file__=str(ROOT/'scripts/prepare_ci_security_caches.py')
exec(compile(Path(POLICY.__file__).read_bytes(),POLICY.__file__,'exec'),POLICY.__dict__)

class PrepareAssetsTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory(prefix='app-prepare-public-');self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name);self.context=self.root/'context';(self.context/'scripts').mkdir(parents=True)
        self.payloads={name:('OWNED SYNTHETIC PUBLIC ZIP '+name).encode()for name in('util','ncurses','acl-tar')}
        self.lock=json.loads((ROOT/'scripts/app_security_os_packages.lock.json').read_bytes())
        for name,raw in self.payloads.items():self.lock['artifacts'][name].update(bytes=len(raw),sha256=sha256(raw).hexdigest())
        (self.context/'scripts/app_security_os_packages.lock.json').write_bytes(M.encode(self.lock))
        self.metadata={'id':POLICY.RELEASE_ID,'tag_name':POLICY.RELEASE_TAG,'target_commitish':POLICY.RELEASE_TARGET,
            'draft':False,'prerelease':True,'html_url':'https://github.com/'+POLICY.REPO+'/releases/tag/'+POLICY.RELEASE_TAG,'assets':[]}
        for name,identifier in POLICY.ASSET_IDS.items():
            spec=self.lock['artifacts'].get(name,{'bytes':1,'sha256':'f'*64})
            self.metadata['assets'].append({'id':identifier,'name':'app-os-'+name+'.zip','size':spec['bytes'],
                'digest':'sha256:'+spec['sha256'],'state':'uploaded',
                'url':'https://api.github.com/repos/'+POLICY.REPO+'/releases/assets/'+str(identifier)})
        self.calls=[]

    def command(self,argv,**kwargs):
        self.calls.append((argv,kwargs));endpoint=argv[-1]
        if endpoint.endswith('/releases/'+str(POLICY.RELEASE_ID)):return M.encode(self.metadata)
        name=next(name for name,identifier in POLICY.ASSET_IDS.items()if endpoint.endswith('/releases/assets/'+str(identifier)))
        self.assertIn('Accept: application/octet-stream',argv)
        self.assertEqual(kwargs['maximum'],self.lock['artifacts'][name]['bytes']);return self.payloads[name]

    def run_assets(self):
        with patch.dict(os.environ,{'GH_TOKEN':'PUBLIC_FIXTURE_TOKEN'}),patch.object(M,'command',side_effect=self.command):
            return M.canonical_vendor_originals(self.root,self.context,ROOT)

    def test_exact_retained_release_metadata_and_octet_stream_downloads_share_existing_pins(self):
        arguments=self.run_assets();self.assertEqual(len(self.calls),4)
        self.assertIn('Accept: application/vnd.github+json',self.calls[0][0])
        self.assertFalse(any('actions/artifacts' in arg for call,_ in self.calls for arg in call))
        for name,raw in self.payloads.items():
            path=self.root/'security-originals'/(name+'.zip');self.assertEqual(path.read_bytes(),raw)
            self.assertEqual(path.stat().st_mode&0o777,0o600);self.assertIn('--'+name+'-zip',arguments);self.assertIn(str(path),arguments)
        self.assertEqual(M.SUBPHASE,'vendor_cache_prepare')

    def test_wrong_release_or_asset_metadata_refuses_before_payload_download(self):
        for mutation,code in [('release','vendor_release_metadata'),('id_type','vendor_release_metadata'),('asset','vendor_asset_metadata')]:
            with self.subTest(mutation=mutation):
                original=deepcopy(self.metadata);self.calls=[]
                if mutation=='release':self.metadata['target_commitish']='0'*40
                elif mutation=='id_type':self.metadata['id']=float(POLICY.RELEASE_ID)
                else:next(row for row in self.metadata['assets']if row['id']==POLICY.ASSET_IDS['util'])['digest']='sha256:'+'0'*64
                with self.assertRaisesRegex(RuntimeError,'^'+code+'$'):self.run_assets()
                self.assertEqual(len(self.calls),1);self.assertFalse(any((self.root/'security-originals').glob('*.zip')))
                self.metadata=original

    def test_payload_drift_refuses_without_publishing_the_unknown_ZIP(self):
        self.payloads['util']=b'CHANGED PUBLIC FIXTURE'
        with self.assertRaisesRegex(RuntimeError,'^vendor_asset_pin$'):self.run_assets()
        self.assertFalse((self.root/'security-originals/util.zip').exists());self.assertEqual(len(self.calls),2)

    def test_controlled_original_fetcher_code_is_visible_and_stale_Git_exit_or_private_args_are_not(self):
        (self.root/'reports').mkdir()
        for failure,wanted in [('artifact_download_failed','artifact_download_failed'),('PRIVATE_SENTINEL_NEVER_PRINT','refused')]:
            with self.subTest(failure=failure):
                report=self.root/'reports/builder-failure.json'
                if report.exists():report.unlink()
                output=io.StringIO()
                with redirect_stdout(output),patch.object(M,'hosted'),patch.object(M,'prepare',side_effect=RuntimeError(failure)),patch.object(M,'COMMAND_RETURN_CODE',0):
                    self.assertEqual(M.main(['prepare','--root',str(self.root),'--repo',str(ROOT),'--canonical3541']),1)
                value=json.loads(output.getvalue());self.assertEqual(value['code'],wanted);self.assertIsNone(value['command_return_code'])
                self.assertNotIn('PRIVATE_SENTINEL',output.getvalue());self.assertFalse(value['security_accepted'])

if __name__=='__main__':unittest.main()
