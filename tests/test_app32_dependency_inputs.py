"""Synthetic public transport tests; no daemon, network or operational data."""
from contextlib import ExitStack,contextmanager
from hashlib import sha256
import gzip
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch
import zipfile

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('app32_inputs',ROOT/'scripts/app32_dependency_inputs.py')
M=importlib.util.module_from_spec(spec);spec.loader.exec_module(M)

class InputsTests(unittest.TestCase):
    def setUp(self):
        self.stack=ExitStack();self.addCleanup(self.stack.close)
        self.root=Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.specs={}
        for profile in('postgres','redis'):
            self.specs[profile]={'profile':profile,'run_id':123,'image_artifact_id':456,
                'image_zip_bytes':789,'image_zip_sha256':'a'*64,
                'evidence_bytes':len(profile),'evidence_sha256':sha256(profile.encode()).hexdigest(),
                'config_id':'sha256:'+'b'*64,'layers':['sha256:'+'c'*64],
                'archive':{'uncompressed_bytes':7}}
        self.ctx=SimpleNamespace(D=SimpleNamespace(PROFILES=self.specs),end=time.monotonic()+60)

    def row(self,profile,payload=b'public image graph'):
        raw=payload;compressed=gzip.compress(raw,mtime=0)
        archive={'format':'docker-archive+gzip','name':'candidate-image.tar.gz',
            'size_bytes':len(compressed),'sha256':sha256(compressed).hexdigest(),'uncompressed_bytes':len(raw)}
        description=M.descriptor(profile,self.specs[profile],archive)
        out=io.BytesIO()
        with zipfile.ZipFile(out,'w')as z:
            z.writestr('transport.json',M.encode(description));z.writestr('candidate-image.tar.gz',compressed)
        body=out.getvalue()
        return {'image':{'asset_id':1 if profile=='postgres'else 3,'name':profile+'-image.zip',**M.pin(body)},
            'evidence':{'asset_id':2 if profile=='postgres'else 4,'name':profile+'-evidence.zip',
                'bytes':len(profile),'sha256':sha256(profile.encode()).hexdigest()},'descriptor':description},body

    def lock(self):
        return {'schema':'app32_closed_public_dependencies/1','release':{'id':5,'tag':M.TAG,'target':M.TARGET},
            'fixtures':{p:self.row(p)[0]for p in('postgres','redis')}}

    def test_closed_inventory_retains_old_immutable_operands_and_honest_new_transport(self):
        lock=self.lock();self.assertEqual(M.inventory(M.encode(lock),None,self.ctx),lock)
        for p,row in lock['fixtures'].items():
            self.assertFalse(row['descriptor']['original_artifact_recreated'])
            self.assertEqual(row['descriptor']['image_config_id'],self.specs[p]['config_id'])
            self.assertEqual(row['descriptor']['rootfs_layers'],self.specs[p]['layers'])

    def test_inventory_rejects_unknown_profile_duplicate_asset_and_changed_config(self):
        for mutate in(lambda x:x['fixtures'].update(extra=x['fixtures']['redis']),
            lambda x:x['fixtures']['redis']['image'].update(asset_id=1),
            lambda x:x['fixtures']['redis']['descriptor'].update(image_config_id='sha256:'+'d'*64),
            lambda x:x['release'].update(target='f'*40),
            lambda x:x['fixtures']['redis']['image'].update(bytes=True)):
            lock=self.lock();mutate(lock)
            with self.assertRaisesRegex(RuntimeError,'^app32_inventory$'):M.inventory(M.encode(lock),None,self.ctx)
        with self.assertRaisesRegex(RuntimeError,'^app32_inventory$'):M.document(b'{"x":1,"x":2}')

    @contextmanager
    def pinned(self,path,maximum):
        with path.open('rb')as stream:
            self.assertLessEqual(path.stat().st_size,maximum);yield stream,path.stat().st_size

    def admission_context(self):
        self.ctx.C=SimpleNamespace(read=lambda p,n:p.read_bytes(),pinned=self.pinned,
            stream_digest=lambda s,n:sha256(s.read(n)).hexdigest())
        self.ctx.capacity=Mock();self.ctx.check=Mock();self.ctx.T=object();self.ctx.L=object()
        def evidence(raw,spec):
            self.assertEqual(sha256(raw).hexdigest(),spec['evidence_sha256'])
            return {'spec':spec,'image':{'Config':{'public':'unchanged'}}}
        def validate(stream,size,end,data,transport,moby):
            self.assertEqual(stream.read(),b'public image graph')
            self.assertEqual(data['spec']['archive']['uncompressed_bytes'],size)
            self.assertEqual(data['spec']['config_id'],'sha256:'+'b'*64)
            return {'physical_graph_closed':True,'source_identities':{'public':{}}}
        self.ctx.D.evidence=evidence;self.ctx.D.validate_archive=Mock(side_effect=validate)
        self.ctx.P=SimpleNamespace(decode_gzip=lambda source,size,path,expanded:
            path.write_bytes(gzip.decompress(source.read(size))))
        return self.ctx

    def test_new_envelope_admission_calls_unchanged_archive_validator_with_original_config(self):
        ctx=self.admission_context();row,raw=self.row('postgres')
        path=self.root/'image.zip';path.write_bytes(raw);evidence=self.root/'evidence.zip';evidence.write_bytes(b'postgres')
        tar,data,proof=M.admit(ctx,None,'postgres',self.root/'admission',path,evidence,row)
        self.assertEqual(tar.read_bytes(),b'public image graph');self.assertTrue(proof['physical_graph_closed'])
        self.assertIs(data['spec'],self.specs['postgres']);ctx.capacity.assert_called_once()
        self.assertEqual(ctx.D.validate_archive.call_count,1)

    def test_corrupted_zip_rejected_before_archive_or_daemon_work(self):
        ctx=self.admission_context();row,raw=self.row('postgres')
        path=self.root/'image.zip';path.write_bytes(bytes([raw[0]^1])+raw[1:]);evidence=self.root/'evidence.zip';evidence.write_bytes(b'postgres')
        with self.assertRaisesRegex(RuntimeError,'^app32_transport_pin$'):
            M.admit(ctx,None,'postgres',self.root/'admission',path,evidence,row)
        ctx.D.validate_archive.assert_not_called()

    def test_transport_checks_original_whole_guards_once_before_and_after_copy(self):
        ctx=self.admission_context();row,raw=self.row('postgres')
        path=self.root/'image.zip';path.write_bytes(raw);evidence=self.root/'evidence.zip';evidence.write_bytes(b'postgres')
        folder=self.root/'admission';states=[]
        def guard():
            compressed=folder/'candidate-image.tar.gz'
            states.append(compressed.stat().st_size if compressed.exists()else None)
        ctx.check=Mock(side_effect=guard)
        M.admit(ctx,None,'postgres',folder,path,evidence,row)
        self.assertEqual(states,[None,row['descriptor']['archive']['size_bytes']])
        self.assertEqual(ctx.check.call_count,2)

    def test_multiple_transport_chunks_keep_two_whole_guards_and_per_read_deadline_checks(self):
        ctx=self.admission_context()
        payload=b''.join(sha256(str(i).encode()).digest()for i in range(8192))
        row,raw=self.row('postgres',payload);self.assertGreater(row['descriptor']['archive']['size_bytes'],3*65536)
        path=self.root/'image.zip';path.write_bytes(raw);evidence=self.root/'evidence.zip';evidence.write_bytes(b'postgres')
        ctx.D.validate_archive=Mock(return_value={'physical_graph_closed':True})
        ctx.end=100
        with patch.object(M.time,'monotonic',return_value=1)as clock:
            tar,_,_=M.admit(ctx,None,'postgres',self.root/'admission',path,evidence,row)
        self.assertEqual(tar.read_bytes(),payload);self.assertEqual(ctx.check.call_count,2)
        chunks=(row['descriptor']['archive']['size_bytes']+65535)//65536
        self.assertEqual(clock.call_count,2*(chunks+1))

    def test_transport_checks_original_deadline_before_each_read_without_mutating_it(self):
        ctx=self.admission_context();row,raw=self.row('postgres')
        path=self.root/'image.zip';path.write_bytes(raw);evidence=self.root/'evidence.zip';evidence.write_bytes(b'postgres')
        ctx.end=1234.5
        with patch.object(M.time,'monotonic',return_value=1234.5),self.assertRaisesRegex(RuntimeError,'^app32_transport_deadline$'):
            M.admit(ctx,None,'postgres',self.root/'admission',path,evidence,row)
        self.assertEqual(ctx.end,1234.5)
        self.assertEqual((self.root/'admission/candidate-image.tar.gz').stat().st_size,0)
        ctx.D.validate_archive.assert_not_called()

    def test_prepare_fetches_exactly_four_retained_assets_then_authenticates_both_graphs(self):
        ctx=self.admission_context();lock=self.lock();bodies={}
        for p,row in lock['fixtures'].items():
            bodies[row['image']['asset_id']]=self.row(p)[1];bodies[row['evidence']['asset_id']]=p.encode()
        metadata={**lock['release'],'tag_name':M.TAG,'target_commitish':M.TARGET,'draft':False,'prerelease':True,'assets':[
            {'id':a['asset_id'],'name':a['name'],'size':a['bytes'],'digest':'sha256:'+a['sha256'],'state':'uploaded',
             'url':'https://api.github.com/repos/'+M.REPO+'/releases/assets/'+str(a['asset_id'])}
            for row in lock['fixtures'].values()for a in(row['image'],row['evidence'])]}
        calls=[]
        def command(argv,**kwargs):
            calls.append(argv)
            if 'target'in kwargs:os.write(kwargs['target'],bodies[int(argv[-1].split('/')[-1])]);return
            return M.encode(metadata)
        (self.root/'reports').mkdir()
        M.prepare(self.root,None,ctx,lock,command=command,environment={'GH_TOKEN':'synthetic-never-log'},
            write=lambda p,v:p.write_bytes(M.encode(v)),digest=lambda p,n:M.pin(p.read_bytes()))
        self.assertEqual(len(calls),5);self.assertEqual(ctx.D.validate_archive.call_count,2)
        receipt=json.loads((self.root/'reports/app32-dependencies.json').read_bytes())
        self.assertTrue(receipt['network_complete_before_build'])
        self.assertFalse(receipt['security_accepted']);self.assertFalse(list(self.root.rglob('*.tar')))

    def test_offline_binding_refuses_network_and_restores_only_new_module_instance(self):
        ctx=SimpleNamespace(command=Mock(return_value=b'public'),download=Mock())
        old=SimpleNamespace(DOCKER=['docker','--host=fixed'],admit_dependency=Mock())
        original_command=ctx.command;original_download=ctx.download;original_admit=old.admit_dependency
        with ExitStack()as stack:
            M.bind(ctx,old,self.root,self.lock(),stack)
            self.assertEqual(ctx.command(['docker','--host=fixed','ps']),b'public')
            for argv,kwargs in [(['gh','api'],{}),(['docker','--host=fixed','ps'],{'auth':True})]:
                with self.assertRaisesRegex(RuntimeError,'^app32_offline_transport$'):ctx.command(argv,**kwargs)
            with self.assertRaisesRegex(RuntimeError,'^app32_offline_transport$'):ctx.download('anything')
        self.assertIs(ctx.command,original_command);self.assertIs(ctx.download,original_download)
        self.assertIs(old.admit_dependency,original_admit)

    def test_real_builder_places_dependency_admission_before_any_vendor_or_build(self):
        source=(ROOT/'scripts/prepare_canonical_app_image.py').read_text()
        prepare=source[source.index('def prepare(root,repo):'):source.index('def inspect(root,image,source,signature):')]
        self.assertLess(prepare.index('canonical_app32_prepare('),prepare.index('canonical_vendor_originals('))
        self.assertIn('inputs.bind(ctx,old,root,lock,stack)',source)
        self.assertIn("APP32_LOCK,SMOKE_DRIVER",source)
        self.assertIn("and identity(os.fstat(fd))==identity(path.lstat())==row['identity']",source)

if __name__=='__main__':unittest.main()
