"""Root-owned synthetic normal Market fixtures; no serving or private inputs."""
from contextlib import ExitStack
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from types import ModuleType,SimpleNamespace
from unittest.mock import patch
import ast,gzip,io,json,os,re,tarfile,tempfile,time,tracemalloc,unittest

ROOT=Path(__file__).resolve().parents[1]
DENIED=('subprocess.Popen','subprocess.run','os.system','os.popen','os.fork','os.kill','os.killpg',
    'socket.socket','socket.create_connection','socket.getaddrinfo','urllib.request.urlopen','ctypes.CDLL','ctypes.PyDLL',
    'resource.setrlimit','signal.signal','signal.alarm')

def load():
    path=ROOT/'deploy/maintenance/normal_market_activation.py';out=ModuleType('_normal_market_public_fixture');out.__file__=str(path)
    exec(compile(path.read_bytes(),str(path),'exec'),out.__dict__);return out

with ExitStack() as importing:
    for target in DENIED:importing.enter_context(patch(target,side_effect=AssertionError('ambient denied')))
    M=load()

class NormalMarketTests(unittest.TestCase):
    def setUp(self):
        self.assertEqual((os.geteuid(),os.getegid()),(0,0));self.stack=ExitStack();self.addCleanup(self.stack.close)
        for target in DENIED:self.stack.enter_context(patch(target,side_effect=AssertionError('ambient denied')))
        self.root=Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix='.normal-market-',dir=ROOT)))
        self.root.chmod(0o700);self.C,self.S,self.checks=M.primitives(self.stack)
        self.C0=self.C;self.now=int(time.time())
        self.S.C=SimpleNamespace(**{k:getattr(self.C,k) for k in ('MAX_JSON','require','same','document','public','encoded','absolute','hex64','environment','unordered_host','network_parity','other_containers')})
        self.S.C.metadata=self.C.metadata;self.S.C.OPERATION=M.OPERATION;self.S.C.ROLE_ORDER=(('web','market-processor'),)
        self.S.C.normal_baseline=lambda *a,**kw:M.baseline(self.C,*a,**kw)
        self.S.C.configuration_parity=lambda *a,**kw:M.parity(self.C,*a,**kw)
        # Reuse only the existing public operand builder, not its tests/backend.
        source=ast.parse((ROOT/'tests/test_canonical_app_activation.py').read_bytes())
        node=next(n for n in source.body if isinstance(n,ast.FunctionDef) and n.name=='fixture')
        ns={'C':self.C,'SimpleNamespace':SimpleNamespace,'deepcopy':deepcopy}
        exec(compile(ast.Module(body=[node],type_ignores=[]),'public_operand_builder','exec'),ns)
        self.builder=ns['fixture']
    def private(self,path,raw,mode=0o600):
        path.parent.mkdir(parents=True,exist_ok=True,mode=0o700);path.write_bytes(raw);path.chmod(mode);return path
    def case(self):
        f=self.builder();role='market-processor';old=f.old['services'].pop('app');f.old['services'][role]=old
        old.update(container_name='public_market_processor',command=['service','--role',role],profiles=['foundation-template','web'],user='10001:10001',read_only=True,
            cap_drop=['ALL'],security_opt=['no-new-privileges:true'],init=True,pids_limit=128,mem_limit=384*1024**2,
            memswap_limit=768*1024**2,cpus=1.0,shm_size=128*1024**2,tmpfs=['/tmp:size=16m,mode=1777,noexec,nosuid,nodev'],
            secrets=[{'source':'public_secret','target':'public_secret'}],healthcheck={'test':['CMD','public-normal-health',role],'interval':'10s','timeout':'3s','retries':3})
        old['environment']={'MARKET_PIPELINE_RELEASE_SHA':'a'*40,'PUBLIC_BUSINESS_VALUE':'UNCHANGED'}
        secret=str(self.root/'public-secret');self.private(Path(secret),b'OPAQUE SYNTHETIC SECRET')
        f.old['secrets']={'public_secret':{'name':'public_secret','file':secret}}
        f.scope.update(container_name=old['container_name'],env_files=[],profiles=['foundation-template','web'],release='a'*40,public_sources=[],secret_files=[{'path':secret,'target':secret,'aliases':[],
            'identity':self.S.identity(Path(secret).stat()),'cli':False}])
        defaults=deepcopy(f.old_image['Config']);defaults.update(User='10001:10001',ArgsEscaped=True)
        f.old_image['Config']=defaults;f.old_image['RootFS']={'Type':'layers','Layers':['sha256:'+'e'*64]}
        f.new_image['Config']=deepcopy(defaults);f.new_image['Id']='sha256:'+'d'*64
        f.new_image['RootFS']={'Type':'layers','Layers':['sha256:'+'e'*64,'sha256:'+'f'*64]}
        f.new=deepcopy(f.old);f.new['services'][role]['image']=f.new_image['Id']
        for row,model,defaults,image in ((f.old_row,f.old,defaults,f.old_image['Id']),
                (f.stopped,f.new,defaults,f.new_image['Id']),(f.active,f.new,defaults,f.new_image['Id'])):
            row.update(name='/'+old['container_name'],image=image)
            cfg=row['config'];cfg.update(Image=model['services'][role]['image'],Cmd=old['command'],User='10001:10001',ArgsEscaped=True,
                Env=[*defaults['Env'],*(k+'='+v for k,v in old['environment'].items())],Healthcheck=self.C.healthcheck(old['healthcheck']))
            labels=cfg['Labels'];labels.update({'com.docker.compose.service':role,'com.docker.compose.image':image})
            for key in self.C.PROVENANCE_LABELS:labels.pop(key,None)
            host=row['host'];host.update(ReadonlyRootfs=True,CapDrop=['ALL'],SecurityOpt=['no-new-privileges:true'],Init=True,
                PidsLimit=128,Memory=384*1024**2,MemorySwap=768*1024**2,NanoCpus=10**9,ShmSize=128*1024**2,
                Tmpfs={'/tmp':'size=16m,mode=1777,noexec,nosuid,nodev'})
            row['mounts']=[{'Type':'bind','Source':secret,'Destination':'/run/secrets/public_secret','RW':False,'Propagation':'rprivate'}]
            for network in row['networks'].values():
                network['Aliases']=[old['container_name'],role]
                if network.get('DNSNames') is not None:network['DNSNames']=[old['container_name'],row['id'][:12]]
            if row is not f.stopped:row['health']='healthy'
        return f

    def test_every_public_role_declares_exact_template_and_site_profiles(self):
        base=(ROOT/'deploy/market-data/compose.yml').read_text()
        block=re.search(r'^  market-runtime-base:\n(.*?)(?=^  [^ ]|\Z)',base,re.M|re.S).group(1)
        self.assertRegex(block,r'profiles: \[foundation-template\]')
        original,_=self.control()
        for role,(site,_) in M.ROLES.items():
            source=(ROOT/'deploy/market-data'/('compose.'+site+'.yml')).read_text()
            block=re.search(r'^  '+re.escape(role)+r':\n(.*?)(?=^  [^ ]|\Z)',source,re.M|re.S).group(1)
            self.assertRegex(block,r'extends:\n(?:[^\n]*\n)*?      service: market-runtime-base')
            self.assertRegex(block,r'profiles: \['+site+r'\]')
            value=deepcopy(original);value['role']=role;value['scope']['profiles']=['foundation-template',site]
            if role=='market-capture-account2':
                value['scope']['public_sources']=[{'kind':'image-baked','target':path,'bytes':size,'sha256':pin,
                    'proof':{'name':'synthetic-source-proof.json','bytes':1,'sha256':'c'*64}}
                    for path,(size,pin) in M.F79_SOURCES.items()]
            M.validate_control(self.C,value,self.now)

    def test_declared_profile_guard_rejects_missing_duplicate_wrong_order_and_unknown(self):
        original,_=self.control()
        for profiles in (['web'],['web','foundation-template'],['foundation-template','web','web'],['foundation-template','other'],['foundation-template','web','other'],'web'):
            value=deepcopy(original);value['scope']['profiles']=profiles
            with self.assertRaisesRegex(RuntimeError,'normal_profiles'):M.validate_control(self.C,value,self.now)

    def test_every_role_cli_enables_only_site_never_the_template_profile(self):
        for role,(site,_) in M.ROLES.items():
            backend=M.MarketBackend.__new__(M.MarketBackend);backend.role=role
            backend.scope={'workdir':'/public','project':'public','profiles':['foundation-template',site],'env_files':[]}
            backend.paths=['/public/base.yml','/public/normal.yml']
            argv=backend.compose();self.assertEqual(argv.count('--profile'),1)
            self.assertEqual(argv[argv.index('--profile')+1],site);self.assertNotIn('foundation-template',argv)

    def test_rendered_profile_order_is_cosmetic_but_typed_exact_members_stay_strict(self):
        f=self.case();role='market-processor';rendered=deepcopy(f.old)
        rendered['services'][role]['profiles']=['web','foundation-template']
        M.baseline(self.C,rendered,role,f.old_row,f.old_image,f.scope,f.paths)
        self.assertEqual(f.scope['profiles'],['foundation-template','web'])
        self.assertEqual(rendered['services'][role]['profiles'],['web','foundation-template'])
        for profiles in (['web'],['foundation-template','unknown'],['foundation-template','web','web'],['foundation-template',True],{'web':True}):
            changed=deepcopy(rendered);changed['services'][role]['profiles']=profiles
            with self.assertRaisesRegex(RuntimeError,'normal_features'):
                M.baseline(self.C,changed,role,f.old_row,f.old_image,f.scope,f.paths)

    def control(self):
        C=self.C;value=M.unarmed_control();f=self.case();now=self.now
        value.update(nonce='a'*32,controller_commit='b'*40,role='market-processor',issued_at=now-1,forward_deadline=now+300,recovery_deadline=now+600)
        def binding(name,raw=b'PUBLIC FIXTURE'):return {'name':name,'bytes':len(raw),'sha256':sha256(raw).hexdigest()}
        own=Path(M.__file__).read_bytes();value['adapter_source']=binding('normal_market_activation.py',own)
        value['gates']={k:binding(M.PRODUCER if k=='functional_producer' else k+'.json') for k in value['gates']}
        marker=self.private(self.root/'marker',b'OPAQUE MARKER');ident=self.S.identity(marker.stat())
        value['sites']={site:{'machine_id':'a'*32,'fences':{'marker':ident,**({'source':M.SOURCE_ID} if site=='bot' else {})},
            'reader_units':{name:{'LoadState':'loaded','UnitFileState':'enabled','NeedDaemonReload':'no'} for name in C.READER_UNITS} if site=='bot' else {},
            'legacy_units':{'public-retired.service':{'ActiveState':'inactive','MainPID':'0','ControlPID':'0','UnitFileState':'disabled','NeedDaemonReload':'no'}}} for site in ('bot','web')}
        ref=self.private(self.root/'normal.env',b'OPAQUE ENV');f.scope.update(project='public-market',workdir='/public/measured-release',
            compose_files=[{'path':'/public/measured-release/base.yml','bytes':3,'sha256':'1'*64},{'path':'/public/measured-release/normal.yml','bytes':4,'sha256':'2'*64}],
            env_files=[{'path':str(ref),'target':str(ref),'identity':self.S.identity(ref.stat()),'aliases':[],'cli':False}],
            baseline={'container_id':f.old_row['id'],'image_id':f.old_row['image'],'pid':f.old_row['pid'],'started':f.old_row['started'],'restarts':0})
        value['scope']={k:f.scope[k] for k in ('project','workdir','container_name','compose_files','profiles','env_files','secret_files','public_sources','compose_version',
            'docker_version','environment_file_label','release','baseline')};return value,f

    def test_shared_rollback_forwards_explicit_no_dependencies_mode(self):
        f=self.case();role='market-processor';labels=f.old_row['config']['Labels'];key='com.docker.compose.depends_on'
        f.old['services'][role]['depends_on']={'redis':{'condition':'service_started'}}
        self.S.C.normal_baseline(f.old,role,f.old_row,f.old_image,f.scope,f.paths,creation_no_dependencies=True)
        labels[key]='redis:service_started:false'
        with self.assertRaisesRegex(RuntimeError,'compose_labels'):
            self.S.C.normal_baseline(f.old,role,f.old_row,f.old_image,f.scope,f.paths,creation_no_dependencies=True)
        M.baseline(self.C,f.old,role,f.old_row,f.old_image,f.scope,f.paths)
        labels.pop(key)
        with self.assertRaisesRegex(RuntimeError,'compose_labels'):
            self.S.C.normal_baseline(f.old,role,f.old_row,f.old_image,f.scope,f.paths,creation_no_dependencies=True)

    def test_factory_missing_measurements_unarmed_before_any_command_or_output(self):
        value=M.unarmed_control();self.assertTrue(all(v is None for v in value['gates'].values()))
        with patch.object(M.MarketBackend,'cmd') as cmd,self.assertRaises(RuntimeError):M.MarketBackend(value,self.stack)
        cmd.assert_not_called();self.assertFalse(any(self.root.rglob('activation.guard')))
    def test_per_role_prefix_profiles_typed_control_and_all_missing_gates_fail_closed(self):
        value,_=self.control();self.assertIs(M.validate_control(self.C,value,self.now),value)
        for key in value['gates']:
            wrong=deepcopy(value);wrong['gates'][key]=None
            with self.subTest(key=key),self.assertRaises(RuntimeError):M.validate_control(self.C,wrong,self.now)
        for change in ({'profiles':['bot']},{'compose_files':[]},{'release':'fake'},{'env_files':[]}):
            wrong=deepcopy(value);wrong['scope'].update(change)
            with self.assertRaises(RuntimeError):M.validate_control(self.C,wrong,self.now)
        wrong=deepcopy(value);wrong['scope']['compose_files'][0]['path']='/private/.env'
        with self.assertRaises(RuntimeError):M.validate_control(self.C,wrong,self.now)
    def test_f79_both_exact_public_overrides_required_no_diagnostic_source_substitution(self):
        value,_=self.control();value['role']='market-capture-account2'
        value['scope']['public_sources']=[{'kind':'image-baked','target':target,'bytes':proof[0],'sha256':proof[1],
            'proof':{'name':'baked-sources.json','bytes':12,'sha256':'e'*64}} for target,proof in M.F79_SOURCES.items()]
        M.validate_control(self.C,value,self.now)
        for bad in ([],value['scope']['public_sources'][:1],[*value['scope']['public_sources'],value['scope']['public_sources'][0]]):
            wrong=deepcopy(value);wrong['scope']['public_sources']=bad
            with self.assertRaises(RuntimeError):M.validate_control(self.C,wrong,self.now)
        wrong=deepcopy(value);wrong['scope']['public_sources'][0].update(bytes=132113,sha256='0'*64)
        with self.assertRaises(RuntimeError):M.validate_control(self.C,wrong,self.now)
        wrong=deepcopy(value);wrong['scope']['public_sources'][0].update(kind='bind',path='/public/not-a-mount.py')
        with self.assertRaises(RuntimeError):M.validate_control(self.C,wrong,self.now)
        wrong=deepcopy(value);wrong['scope']['public_sources'][1]['proof']['sha256']='0'*64
        with self.assertRaises(RuntimeError):M.validate_control(self.C,wrong,self.now)

    def source_tar(self,rows,*,pax=None,mode=0o644,uid=0,gid=0):
        out=io.BytesIO()
        with tarfile.open(fileobj=out,mode='w',format=tarfile.PAX_FORMAT,pax_headers=pax) as archive:
            for name,body,kind,extra in rows:
                item=tarfile.TarInfo(name);item.type=kind;item.mode=0o755 if kind==tarfile.DIRTYPE and mode==0o644 else mode
                item.size=len(body);item.pax_headers=extra;item.uid=uid;item.gid=gid
                archive.addfile(item,io.BytesIO(body))
        return out.getvalue()

    def source_rows(self):
        return [(p[1:],b'PUBLIC_FIXED_SOURCE_'+str(i).encode(),tarfile.REGTYPE,{}) for i,p in enumerate(M.F79_SOURCES)]

    def test_two_fixed_source_resolver_applies_whiteouts_before_same_layer_files_and_opaque_parents(self):
        rows=self.source_rows();base,_,_=M.source_layer(io.BytesIO(self.source_tar(rows)),{},M.F79_SOURCES,time.monotonic()+30)
        target=rows[0][0];parent,leaf=target.rsplit('/',1);whiteout=(parent+'/.wh.'+leaf,b'',tarfile.REGTYPE,{})
        for replacements in ([whiteout,rows[0]],[rows[0],whiteout]):
            state,_,_=M.source_layer(io.BytesIO(self.source_tar(replacements)),base,M.F79_SOURCES,time.monotonic()+30)
            self.assertEqual(state,base)
        state,_,_=M.source_layer(io.BytesIO(self.source_tar([whiteout])),base,M.F79_SOURCES,time.monotonic()+30)
        self.assertIsNone(state[target]);self.assertEqual(state[rows[1][0]],base[rows[1][0]])
        opaque=(parent+'/.wh..wh..opq',b'',tarfile.REGTYPE,{})
        state,_,_=M.source_layer(io.BytesIO(self.source_tar([rows[0],opaque])),base,M.F79_SOURCES,time.monotonic()+30)
        self.assertEqual(state[target],base[target]);self.assertIsNone(state[rows[1][0]])
        rooted,_,_=M.source_layer(io.BytesIO(self.source_tar([('.',b'',tarfile.DIRTYPE,{}),*rows])),{},M.F79_SOURCES,time.monotonic()+30)
        state,_,_=M.source_layer(io.BytesIO(self.source_tar([('.wh..wh..opq',b'',tarfile.REGTYPE,{})])),rooted,
            M.F79_SOURCES,time.monotonic()+30)
        self.assertEqual(state[''],rooted['']);self.assertTrue(all(state[row[0]] is None for row in rows))

    def test_fixed_source_resolver_rejects_links_sparse_ancestor_replacement_and_path_aliases(self):
        rows=self.source_rows();target=rows[0][0]
        for name,kind,extra in ((target,tarfile.SYMTYPE,{}),(target,tarfile.LNKTYPE,{}),
                ('app/core',tarfile.SYMTYPE,{}),('app/core',tarfile.REGTYPE,{}),
                (target,tarfile.GNUTYPE_SPARSE,{}),('../'+target,tarfile.REGTYPE,{}),
                ('/'+target,tarfile.REGTYPE,{}),(target,tarfile.REGTYPE,{'GNU.sparse.map':'0,1'}),
                ('app/core/.wh...',tarfile.REGTYPE,{}),('app/core/.wh..',tarfile.REGTYPE,{})):
            raw=self.source_tar([(name,b'',kind,extra)])
            with self.subTest(name=name,kind=kind),self.assertRaises(RuntimeError):
                M.source_layer(io.BytesIO(raw),{},M.F79_SOURCES,time.monotonic()+30)

    def test_outside_hardlink_or_symlink_alias_to_source_or_ancestor_refuses_but_busybox_links_remain_allowed(self):
        rows=self.source_rows();base,_,_=M.source_layer(io.BytesIO(self.source_tar(rows)),{},M.F79_SOURCES,time.monotonic()+30)
        for kind,link in ((tarfile.LNKTYPE,rows[0][0]),(tarfile.LNKTYPE,'app/core'),
                (tarfile.LNKTYPE,'/'+rows[1][0]),(tarfile.SYMTYPE,'../../'+rows[0][0]),
                (tarfile.SYMTYPE,'/app'),(tarfile.SYMTYPE,'/')):
            alias=('public/elsewhere/alias',b'',kind,{'linkpath':link})
            rewrite=('public/elsewhere/alias',b'CHANGED_THROUGH_ALIAS',tarfile.REGTYPE,{})
            with self.subTest(kind=kind,link=link),self.assertRaisesRegex(RuntimeError,'source_alias'):
                M.source_layer(io.BytesIO(self.source_tar([alias,rewrite])),base,M.F79_SOURCES,time.monotonic()+30)
        unrelated=[('bin/awk',b'',tarfile.LNKTYPE,{'linkpath':'bin/busybox'}),
                   ('usr/bin/awk',b'',tarfile.SYMTYPE,{'linkpath':'../../bin/busybox'})]
        state,_,_=M.source_layer(io.BytesIO(self.source_tar(unrelated)),base,M.F79_SOURCES,time.monotonic()+30)
        self.assertEqual(state,base)

    def test_fixed_source_resolver_handles_bounded_pax_and_gnu_long_names_without_extraction(self):
        rows=self.source_rows();target,body,kind,_=rows[0]
        raw=self.source_tar([('ignored-name',body,kind,{'path':target,'SCHILY.xattr.user.public':'public'})],pax={'gname':'public'})
        state,count,digest=M.source_layer(io.BytesIO(raw),{},M.F79_SOURCES,time.monotonic()+30)
        self.assertEqual((count,digest),(len(raw),sha256(raw).hexdigest()))
        self.assertEqual(state[target]['sha256'],sha256(body).hexdigest())
        name='public/'+'n'*160;out=io.BytesIO()
        with tarfile.open(fileobj=out,mode='w',format=tarfile.GNU_FORMAT) as archive:
            item=tarfile.TarInfo(name);archive.addfile(item)
        M.source_layer(io.BytesIO(out.getvalue()),{},M.F79_SOURCES,time.monotonic()+30)
        old=self.source_tar([(target,body,kind,{'uid':'00010001','gid':'10001'})])
        new=self.source_tar([(target,body,kind,{})],uid=10001,gid=10001)
        a,_,_=M.source_layer(io.BytesIO(old),{},M.F79_SOURCES,time.monotonic()+30)
        b,_,_=M.source_layer(io.BytesIO(new),a,M.F79_SOURCES,time.monotonic()+30)
        self.assertEqual(a[target]['metadata'],b[target]['metadata'])

    def test_fixed_source_resolver_bounds_physical_reads_headers_pax_expansion_and_tail(self):
        class Reader(io.BytesIO):
            def read(self,n):
                self.maximum=max(getattr(self,'maximum',0),n);return super().read(n)
        raw=self.source_tar(self.source_rows());source=Reader(raw)
        M.source_layer(source,{},M.F79_SOURCES,time.monotonic()+30);self.assertLessEqual(source.maximum,65536)
        for invalid in (raw[:511],raw[:-1024]+b'PRIVATE_TAIL',raw[:512]+bytes(1024)):
            with self.subTest(size=len(invalid)),self.assertRaises(RuntimeError):
                M.source_layer(io.BytesIO(invalid),{},M.F79_SOURCES,time.monotonic()+30)
        item=tarfile.TarInfo('pax');item.type=tarfile.XHDTYPE;item.size=1024**2+1
        source=Reader(item.tobuf()+bytes(1024))
        with self.assertRaises(RuntimeError):M.source_layer(source,{},M.F79_SOURCES,time.monotonic()+30)
        self.assertEqual(source.tell(),512)
        with self.assertRaises(RuntimeError):M.source_layer(io.BytesIO(raw),{},M.F79_SOURCES,time.monotonic()-1)
        with self.assertRaises(RuntimeError):M.source_layer(io.BytesIO(raw),{},M.F79_SOURCES,time.monotonic()+30,1024)

    def test_real_header_only_types_cannot_hide_later_source_headers_via_raw_or_pax_size(self):
        rows=self.source_rows();base=self.source_tar(rows);target=rows[0][0]
        changed=tarfile.TarInfo(target);changed.size=512;changed.mode=0o644
        hidden=changed.tobuf(format=tarfile.USTAR_FORMAT)+bytes(512)+bytes(1024)
        expected={p:(len(row[1]),sha256(row[1]).hexdigest()) for p,row in zip(M.F79_SOURCES,rows)}
        for kind in (tarfile.DIRTYPE,tarfile.SYMTYPE,tarfile.LNKTYPE,tarfile.CHRTYPE,tarfile.BLKTYPE,tarfile.FIFOTYPE):
            for pax in (False,True):
                item=tarfile.TarInfo('outside/header-only');item.type=kind;item.linkname='bin/busybox'
                item.size=0 if pax else 512
                if pax:item.pax_headers={'size':'512'}
                layer=item.tobuf(format=tarfile.PAX_FORMAT if pax else tarfile.USTAR_FORMAT)+hidden
                path,raw,data,D,T,moby=self.source_archive([base,layer]);fd=os.open(path,os.O_RDONLY);self.stack.callback(os.close,fd)
                before=D.identity(os.fstat(fd))
                with self.subTest(kind=kind,pax=pax),patch.object(M,'F79_SOURCES',expected),self.assertRaisesRegex(RuntimeError,'source_layer_type'):
                    M.baked_archive_sources(fd,len(raw),time.monotonic()+30,data,D,T,moby,data['spec']['layers'][:1],before,
                        self.source_association(sha256(raw).hexdigest(),data['spec']['config_id'][7:]),
                        lambda:M.require(D.same(before,D.identity(path.lstat())),'source_archive_identity'))

    def test_unrelated_whiteout_paths_do_not_accumulate_in_resolver_memory(self):
        raw=self.source_tar([('elsewhere/.wh.'+'x'*500+str(i),b'',tarfile.REGTYPE,{}) for i in range(5000)])
        stream=io.BytesIO(raw);tracemalloc.start()
        try:
            state,_,_=M.source_layer(stream,{},M.F79_SOURCES,time.monotonic()+30)
            _,peak=tracemalloc.get_traced_memory()
        finally:tracemalloc.stop()
        self.assertEqual(state,{});self.assertLess(peak,1024**2)

    def test_closed_source_observation_protocol_binds_fixed_public_files_and_exact_lifecycle(self):
        calls=[];paths=sorted(M.F79_SOURCES);cid='a'*64
        runtime={'id':cid,'image':M.F79_IMAGE,'running':True,'restarting':False,'pid':123,
            'started':'2026-10-25T00:00:00Z','restarts':1}
        def query(argv):
            calls.append(argv)
            if argv[:2]==['container','inspect']:return json.dumps(runtime).encode()
            self.assertEqual(argv[:6],['exec',cid,'/usr/local/bin/python','-I','-B','-c'])
            ast.parse(argv[-1]);self.assertNotIn('os.environ',argv[-1]);self.assertNotIn('import core',argv[-1])
            return json.dumps({'schema':'f79_public_baked_code/1','configuration_values_disclosed':False,
                'files':{p:{'bytes':n,'sha256':h,'identity':[1,i+1,0o100644,10001,10001,1,n,1,1]}
                    for i,(p,(n,h)) in enumerate(M.F79_SOURCES.items())}}).encode()
        value=M.public_source_observation(query,cid,M.F79_IMAGE)
        self.assertEqual([c[:2] for c in calls],[['container','inspect'],['exec',cid],['container','inspect']])
        self.assertFalse(value['security_accepted']);self.assertEqual(value['runtime_before'],runtime)
        def changed(argv):
            raw=query(argv)
            if len(calls)>=6 and argv[:2]==['container','inspect']:
                d=json.loads(raw);d['pid']+=1;return json.dumps(d).encode()
            return raw
        with self.assertRaisesRegex(RuntimeError,'source_container'):M.public_source_observation(changed,cid,M.F79_IMAGE)
        with self.assertRaisesRegex(RuntimeError,'public_source_observation'):
            M.public_source_observation(lambda _:b'PRIVATE_RAW_OUTPUT_NOT_ALLOWED',cid,M.F79_IMAGE)

    def test_owned_public_code_routine_executes_real_held_fds_and_metadata_without_uid_simulation(self):
        from contextlib import redirect_stdout
        sources={};paths=[]
        for index in range(2):
            body=b'EXPLICIT_PUBLIC_CODE_'+str(index).encode();p=self.private(self.root/('code-'+str(index)+'.py'),body,0o644)
            os.chown(p,10001,10001);paths.append(p);sources[str(p)]=(len(body),sha256(body).hexdigest())
        runtime={'id':'a'*64,'image':M.F79_IMAGE,'running':True,'restarting':False,'pid':123,
            'started':'2026-10-25T00:00:00Z','restarts':0}
        def query(argv):
            if argv[:2]==['container','inspect']:return json.dumps(runtime).encode()
            output=io.StringIO()
            with redirect_stdout(output):exec(compile(argv[-1],'<owned-public-code-routine>','exec'),{})
            return output.getvalue().encode()
        with patch.object(M,'F79_SOURCES',sources):
            value=M.public_source_observation(query,'a'*64,M.F79_IMAGE)
            for path in paths:self.assertEqual(value['files'][str(path)]['identity'],self.S.identity(path.lstat()))
            # CAP_CHOWN-only Root cannot link another UID's read-only file
            # under fs.protected_hardlinks. Build the real alias while owned,
            # then restore the actual image UID/GID before testing refusal.
            os.chown(paths[0],0,0)
            os.link(paths[0],self.root/'alias')
            os.chown(paths[0],10001,10001)
            with self.assertRaises(AssertionError):M.public_source_observation(query,'a'*64,M.F79_IMAGE)

    def source_archive(self,bodies,*,compressed=False,extra=False):
        def cold(name,path):
            ns=ModuleType(name);exec(compile(path.read_bytes(),str(path),'exec'),ns.__dict__);return ns
        directory=ROOT/'deploy/maintenance'
        D=cold('_source_graph_D',directory/'datastore_candidate_contract.py')
        T=cold('_source_graph_T',directory/'runtime_image_archive_transport.py')
        moby=cold('_source_graph_moby',ROOT/'scripts/admit_security_component_image_reuse.py')
        layers=['sha256:'+sha256(body).hexdigest() for body in bodies]
        config={'architecture':'amd64','os':'linux','created':'2026-10-25T00:00:00Z',
            'history':[{'created_by':'public source fixture'} for _ in bodies],
            'rootfs':{'type':'layers','diff_ids':layers},'config':{'Cmd':['python'],'User':'10001'}}
        cfg=D.encode(config);cid='sha256:'+sha256(cfg).hexdigest();stored=[gzip.compress(b,mtime=0) if compressed else b for b in bodies]
        name=lambda body:'blobs/sha256/'+sha256(body).hexdigest()
        descriptors=[{'mediaType':'application/vnd.oci.image.layer.v1.tar'+('+gzip' if compressed else ''),
            'size':len(body),'digest':'sha256:'+sha256(body).hexdigest()} for body in stored]
        manifest=D.encode({'schemaVersion':2,'mediaType':D.OCI_MANIFEST,'config':{'mediaType':'application/vnd.oci.image.config.v1+json',
            'digest':cid,'size':len(cfg)},'layers':descriptors})
        index=D.encode({'schemaVersion':2,'mediaType':D.OCI_INDEX,'manifests':[{'mediaType':D.OCI_MANIFEST,
            'digest':'sha256:'+sha256(manifest).hexdigest(),'size':len(manifest)}]})
        rows=[(name(cfg),cfg),(name(manifest),manifest),('index.json',index),('oci-layout',D.encode({'imageLayoutVersion':'1.0.0'})),
            ('manifest.json',D.encode([{'Config':name(cfg),'Layers':[name(b) for b in stored],'RepoTags':None}]))]
        rows.extend(dict((name(b),b) for b in stored).items());rows.extend(D.legacy_configs(config,moby).items())
        if extra:rows.append(('unclosed',b'not admitted'))
        raw=self.source_tar([(n,b,tarfile.REGTYPE,{}) for n,b in rows]);path=self.private(self.root/'public-source-archive.tar',raw)
        data={'spec':{'profile':'f79cdda21c19','config_id':cid,'layers':layers,'archive':{'uncompressed_bytes':len(raw)}},
            'image':{'Config':config['config'],'Comment':'','Author':'','DockerVersion':''}}
        return path,raw,data,D,T,moby

    def source_association(self,archive,config):
        return {'catalog_sha256':M.CATALOG_SHA,'load_receipt_sha256':'1'*64,'load_control_sha256':'2'*64,
            'source_admission_sha256':'3'*64,'producer_sha256':'4'*64,'archive_sha256':archive,'candidate_config_sha256':config}

    def test_baked_load_association_requires_original_control_and_closed_measured_receipt(self):
        # Synthetic history operands for this parser only; never a LOAD result
        # producer. The physical graph is exercised independently below.
        catalog=(ROOT/'deploy/maintenance/market_candidate_images.json').read_bytes()
        self.assertEqual(sha256(catalog).hexdigest(),M.CATALOG_SHA)
        spec=next(p for p in json.loads(catalog)['profiles'] if p['profile']=='f79cdda21c19')
        admitted={'schema':'market_candidate_archive/1','profile':spec['profile'],
            'candidate_config_id':spec['candidate_config_id'],'rootfs':spec['candidate_rootfs'],
            'archive_sha256':spec['tar_sha256'],'archive_bytes':spec['archive']['uncompressed_bytes'],
            'source_identities':{spec['candidate_config_id']:{'kind':'Docker_config_identity'}},
            'security_accepted':False,'configuration_values_disclosed':False}
        encode=lambda v:(json.dumps(v,sort_keys=True,separators=(',',':'))+'\n').encode()
        ar=encode(admitted)
        control={'operation':'security-market-candidate-images-20261025-06','schema':'market_image_store_control/2',
            'worker_sha256':M.LOAD_WORKER_SHA,'profile':spec['profile'],'phase':'load-web','nonce':'a'*32,
            'admission_sha256':sha256(ar).hexdigest()};cr=encode(control)
        receipt={'schema':'market_preloaded_image/1','profile':spec['profile'],'role':spec['role'],'site':'web',
            'host_machine_id':'a'*32,'catalog_sha256':M.CATALOG_SHA,'candidate_config_id':spec['candidate_config_id'],
            'rootfs':spec['candidate_rootfs'],'ci_run_id':spec['ci_run_id'],
            'image_artifact_sha256':spec['image_artifact']['digest'][7:],'operation_sha256':sha256(cr).hexdigest(),
            'source_admission_sha256':sha256(ar).hexdigest(),'archive_and_config_association_verified':True,
            'whole_typed_config_compared_in_ram':True,'configuration_values_disclosed':False,'security_accepted':False,
            'serving_roles_activated':[],'candidate_local_image_id':spec['candidate_config_id'],
            'source_archive_sha256':spec['tar_sha256']};rr=encode(receipt)
        _,_,association=M.baked_load_evidence(self.C.document,spec,rr,cr,ar,'b'*64,'a'*32)
        self.assertEqual(association['load_receipt_sha256'],sha256(rr).hexdigest())
        self.assertEqual(association['archive_sha256'],spec['tar_sha256'])
        for mutate in (lambda v:v.update(operation_sha256='0'*64),lambda v:v.update(whole_typed_config_compared_in_ram=1),
                lambda v:v.update(security_accepted=True),lambda v:v.update(candidate_local_image_id='sha256:'+'0'*64),
                lambda v:v.update(schema='market_preloaded_image/2'),lambda v:v.update(rootfs=list(reversed(v['rootfs'])))):
            bad=deepcopy(receipt);mutate(bad)
            with self.assertRaises(RuntimeError):M.baked_load_evidence(self.C.document,spec,encode(bad),cr,ar,'b'*64,'a'*32)
        bad=deepcopy(control);bad['worker_sha256']='0'*64
        with self.assertRaisesRegex(RuntimeError,'baked_source_load'):
            M.baked_load_evidence(self.C.document,spec,rr,encode(bad),ar,'b'*64,'a'*32)

    def test_real_closed_archive_resolves_prefix_and_candidate_sources_including_gzip_and_logical_repeats(self):
        rows=self.source_rows();base=self.source_tar([*rows,('bin/busybox',b'PUBLIC_BUSYBOX_FIXTURE',tarfile.REGTYPE,{})])
        last=self.source_tar([('bin/awk',b'',tarfile.LNKTYPE,{'linkpath':'bin/busybox'})])
        expected={p:(len(row[1]),sha256(row[1]).hexdigest()) for p,row in zip(M.F79_SOURCES,rows)}
        for compressed in (False,True):
            path,raw,data,D,T,moby=self.source_archive([base,last,last],compressed=compressed)
            fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW);self.stack.callback(os.close,fd);before=D.identity(os.fstat(fd))
            with patch.object(M,'F79_SOURCES',expected):
                result=M.baked_archive_sources(fd,len(raw),time.monotonic()+30,data,D,T,moby,
                    data['spec']['layers'][:1],before,self.source_association(sha256(raw).hexdigest(),data['spec']['config_id'][7:]),lambda:M.require(D.same(before,D.identity(path.lstat())),'source_archive_identity'))
                M.baked_proof(result,data['spec']['layers'][:1],data['spec']['config_id'],data['spec']['layers'],
                    sha256(raw).hexdigest(),before,self.source_association(sha256(raw).hexdigest(),data['spec']['config_id'][7:]))
                for mutate in (lambda v:v.update(physical_graph_closed=1),lambda v:v.update(activation_authority=True),
                        lambda v:v['association'].update(load_receipt_sha256='0'*64),
                        lambda v:v['files'][next(iter(v['files']))].update(metadata_unchanged_in_ram=1)):
                    bad=deepcopy(result);mutate(bad)
                    with self.assertRaises(RuntimeError):M.baked_proof(bad,data['spec']['layers'][:1],data['spec']['config_id'],
                        data['spec']['layers'],sha256(raw).hexdigest(),before,
                        self.source_association(sha256(raw).hexdigest(),data['spec']['config_id'][7:]))
            self.assertTrue(result['physical_graph_closed']);self.assertFalse(result['activation_authority'])
            self.assertEqual(set(result['files']),set(M.F79_SOURCES));self.assertNotIn('PUBLIC_FIXED_SOURCE',json.dumps(result))

    def test_closed_graph_precedes_resolution_and_changed_or_late_archive_never_produces_source_proof(self):
        rows=self.source_rows();base=self.source_tar(rows);last=self.source_tar([])
        expected={p:(len(row[1]),sha256(row[1]).hexdigest()) for p,row in zip(M.F79_SOURCES,rows)}
        path,raw,data,D,T,moby=self.source_archive([base,last],extra=True);fd=os.open(path,os.O_RDONLY);self.stack.callback(os.close,fd);before=D.identity(os.fstat(fd))
        with patch.object(M,'source_layer') as scan,self.assertRaises(RuntimeError):
            M.baked_archive_sources(fd,len(raw),time.monotonic()+30,data,D,T,moby,data['spec']['layers'][:1],before,self.source_association(sha256(raw).hexdigest(),data['spec']['config_id'][7:]),lambda:M.require(D.same(before,D.identity(path.lstat())),'source_archive_identity'))
        scan.assert_not_called()
        changed=self.source_tar([(rows[0][0],b'CHANGED_SOURCE',tarfile.REGTYPE,{})])
        for bodies in ([base,changed],[self.source_tar(rows[:1]),self.source_tar(rows[1:])],
                [base,self.source_tar(rows,mode=0)]):
            path,raw,data,D,T,moby=self.source_archive(bodies);fd=os.open(path,os.O_RDONLY);self.stack.callback(os.close,fd);before=D.identity(os.fstat(fd))
            with patch.object(M,'F79_SOURCES',expected),self.assertRaisesRegex(RuntimeError,'f79_baked_source_changed'):
                M.baked_archive_sources(fd,len(raw),time.monotonic()+30,data,D,T,moby,data['spec']['layers'][:1],before,self.source_association(sha256(raw).hexdigest(),data['spec']['config_id'][7:]),lambda:M.require(D.same(before,D.identity(path.lstat())),'source_archive_identity'))
        ancestors=[('app',b'',tarfile.DIRTYPE,{}),('app/core',b'',tarfile.DIRTYPE,{}),
            ('app/core/market_intelligence',b'',tarfile.DIRTYPE,{})]
        dir_base=self.source_tar([*ancestors,*rows]);chmod=self.source_tar([ancestors[1]],mode=0)
        path,raw,data,D,T,moby=self.source_archive([dir_base,chmod]);fd=os.open(path,os.O_RDONLY);self.stack.callback(os.close,fd)
        before=D.identity(os.fstat(fd))
        with patch.object(M,'F79_SOURCES',expected),self.assertRaisesRegex(RuntimeError,'f79_baked_ancestor_changed'):
            M.baked_archive_sources(fd,len(raw),time.monotonic()+30,data,D,T,moby,data['spec']['layers'][:1],before,
                self.source_association(sha256(raw).hexdigest(),data['spec']['config_id'][7:]),
                lambda:M.require(D.same(before,D.identity(path.lstat())),'source_archive_identity'))
        alias=self.source_tar([('outside/alias',b'',tarfile.LNKTYPE,{'linkpath':rows[0][0]}),
            ('outside/alias',b'MODIFIED_SOURCE_VIA_HARDLINK',tarfile.REGTYPE,{})])
        path,raw,data,D,T,moby=self.source_archive([base,alias]);fd=os.open(path,os.O_RDONLY);self.stack.callback(os.close,fd)
        before=D.identity(os.fstat(fd))
        with patch.object(M,'F79_SOURCES',expected),self.assertRaisesRegex(RuntimeError,'source_alias'):
            M.baked_archive_sources(fd,len(raw),time.monotonic()+30,data,D,T,moby,data['spec']['layers'][:1],before,
                self.source_association(sha256(raw).hexdigest(),data['spec']['config_id'][7:]),
                lambda:M.require(D.same(before,D.identity(path.lstat())),'source_archive_identity'))
        path,raw,data,D,T,moby=self.source_archive([base,last]);fd=os.open(path,os.O_RDONLY);self.stack.callback(os.close,fd)
        before=D.identity(os.fstat(fd));real=M.source_layer
        def late(*a,**kw):
            value=real(*a,**kw);body=bytearray(path.read_bytes());body[-1]=1;path.write_bytes(body);return value
        with patch.object(M,'F79_SOURCES',expected),patch.object(M,'source_layer',side_effect=late),self.assertRaises(RuntimeError):
            M.baked_archive_sources(fd,len(raw),time.monotonic()+30,data,D,T,moby,data['spec']['layers'][:1],before,self.source_association(sha256(raw).hexdigest(),data['spec']['config_id'][7:]),lambda:M.require(D.same(before,D.identity(path.lstat())),'source_archive_identity'))

    def test_real_two_layer_root_directory_metadata_must_match_prefix_and_candidate(self):
        rows=self.source_rows();root=('.',b'',tarfile.DIRTYPE,{})
        expected={p:(len(row[1]),sha256(row[1]).hexdigest()) for p,row in zip(M.F79_SOURCES,rows)}
        base=self.source_tar([root,*rows])
        cases=[(self.source_tar([root]),True),(self.source_tar([root],mode=0),False),
            (self.source_tar([root],uid=10001),False),(self.source_tar([root],gid=10001),False),
            (self.source_tar([('.',b'',tarfile.DIRTYPE,{'SCHILY.xattr.user.public':'changed'})]),False)]
        for last,accepted in cases:
            path,raw,data,D,T,moby=self.source_archive([base,last]);fd=os.open(path,os.O_RDONLY);self.stack.callback(os.close,fd)
            before=D.identity(os.fstat(fd))
            with self.subTest(accepted=accepted),patch.object(M,'F79_SOURCES',expected):
                args=(fd,len(raw),time.monotonic()+30,data,D,T,moby,data['spec']['layers'][:1],before,
                    self.source_association(sha256(raw).hexdigest(),data['spec']['config_id'][7:]),
                    lambda:M.require(D.same(before,D.identity(path.lstat())),'source_archive_identity'))
                if accepted:self.assertTrue(M.baked_archive_sources(*args)['ancestor_metadata_unchanged_in_ram'])
                else:
                    with self.assertRaisesRegex(RuntimeError,'f79_baked_ancestor_changed'):M.baked_archive_sources(*args)
    def test_market_baseline_and_complete_parity_support_secrets_tmpfs_shm_argsescaped_without_mutating_operands(self):
        f=self.case();saved=deepcopy(f)
        M.baseline(self.C,f.old,'market-processor',f.old_row,f.old_image,f.scope,f.paths)
        for row,stopped in ((f.stopped,True),(f.active,False)):
            self.assertTrue(M.parity(self.C,f.old,f.new,'market-processor',f.old_row,row,f.old_image,f.new_image,f.scope,f.paths,[*f.paths,f.override],{},stopped=stopped))
        self.assertEqual(f.__dict__,saved.__dict__)
    def test_each_business_config_host_secret_tmpfs_resource_or_network_drift_refuses(self):
        for change in (lambda r:r['config']['Env'].append('BUSINESS=CHANGED'),lambda r:r['config'].update(ArgsEscaped=False),
                lambda r:r['config'].update(StopTimeout=30),lambda r:r['host'].update(ShmSize=64*1024**2),
                lambda r:r['host']['Tmpfs'].update({'/tmp':'size=1m'}),lambda r:r['mounts'][0].update(RW=True),
                lambda r:r['mounts'][0].update(Source='/public/other'),lambda r:r['host'].update(Memory=1),
                lambda r:r['host'].update(UnknownNonzero=True),lambda r:r['networks']['fixture_net'].update(Gateway='192.0.2.1')):
            f=self.case();change(f.active)
            with self.subTest(change=repr(change)),self.assertRaises(RuntimeError):
                M.parity(self.C,f.old,f.new,'market-processor',f.old_row,f.active,f.old_image,f.new_image,f.scope,f.paths,[*f.paths,f.override],{})
    def test_new_source_mount_or_secret_reference_and_image_config_delta_refuse(self):
        f=self.case();f.new_image['Config']['Env'].append('NEW_DEFAULT=NO')
        with self.assertRaisesRegex(RuntimeError,'image_security_config_delta'):M.parity(self.C,f.old,f.new,'market-processor',f.old_row,f.active,f.old_image,f.new_image,f.scope,f.paths,[*f.paths,f.override],{})
        f=self.case();f.old['services']['market-processor']['volumes']=[{'type':'bind','source':'/public/x','target':'/app/core/evil.py','read_only':True}]
        with self.assertRaises(RuntimeError):M.expected_mounts(self.C,f.old,'market-processor',f.scope)
        f=self.case();f.old['secrets']['public_secret']['file']='/unadmitted/secret'
        with self.assertRaisesRegex(RuntimeError,'unbound_secret'):M.expected_mounts(self.C,f.old,'market-processor',f.scope)
    def test_receiver_normal_fixed_9443_host_binding_and_exposed_port_facts_are_exact(self):
        f=self.case();spec=f.old['services']['market-processor'];spec['ports']=[{'target':9443,'published':'9443','protocol':'tcp','host_ip':'127.0.0.1','mode':'ingress'}]
        f.old_row['host']['PortBindings']={'9443/tcp':[{'HostIp':'127.0.0.1','HostPort':'9443'}]};f.old_row['config']['ExposedPorts']={'9443/tcp':{}}
        M.baseline(self.C,f.old,'market-processor',f.old_row,f.old_image,f.scope,f.paths)
        for binding in ({'HostIp':'0.0.0.0','HostPort':'9443'},{'HostIp':'127.0.0.1','HostPort':'9444'}):
            wrong=deepcopy(f.old_row);wrong['host']['PortBindings']={'9443/tcp':[binding]}
            with self.assertRaisesRegex(RuntimeError,'normal_ports'):M.baseline(self.C,f.old,'market-processor',wrong,f.old_image,f.scope,f.paths)
        wrong=deepcopy(f.old_row);wrong['config']['ExposedPorts']={'9443/udp':{}}
        with self.assertRaisesRegex(RuntimeError,'normal_ports'):M.baseline(self.C,f.old,'market-processor',wrong,f.old_image,f.scope,f.paths)
    def test_opaque_permanent_and_secret_references_real_fds_never_read_payload(self):
        value,_=self.control();marker=self.root/'marker';ref=value['scope']['secret_files'][0];pread=os.pread
        def no_payload(fd,count,offset):
            self.fail('opaque reference payload was read')
        with patch.object(os,'pread',side_effect=no_payload),ExitStack() as stack:
            held=self.S.Held(marker,stack,expected=self.S.identity(marker.stat()),opaque=True,lock=True);held.check()
            opaque=self.S.OpaqueReference(ref,stack);opaque.check()
        for mode in (0o644,0o660,0o700):
            ident=self.S.identity(Path(ref['target']).stat());ident[2]=stat_mode=0o100000|mode
            with self.subTest(mode=mode),self.assertRaises(RuntimeError):M.secret_metadata(ident)
    def test_compose_exact_per_role_prefix_profiles_and_only_declared_cli_env(self):
        value,_=self.control();backend=object.__new__(M.MarketBackend);backend.role=value['role'];backend.scope=value['scope'];backend.paths=[r['path'] for r in value['scope']['compose_files']]
        backend.override=Path('/public/image-only.yml');backend.rollback_override=Path('/public/old-image.yml')
        command=backend.compose(extra=True);self.assertIn('--profile',command);self.assertNotIn('--env-file',command)
        backend.scope['env_files'][0]['cli']=True;command=backend.compose(rollback=True)
        self.assertEqual(command.count('--env-file'),1);self.assertEqual(command[-1],'/public/old-image.yml')
        self.assertEqual([command[i+1] for i,s in enumerate(command) if s=='--file'],[*backend.paths,'/public/old-image.yml'])
    def test_normal_functional_health_cannot_be_absent_starting_or_unhealthy_and_never_uses_imagestore_policy(self):
        f=self.case();backend=object.__new__(M.MarketBackend);backend.C=self.C;backend.S=self.S;backend.role='market-processor';backend.scope=f.scope
        backend.check=lambda:None;backend.singleton=lambda *a,**kw:None;called=[]
        backend.producer=SimpleNamespace(functional=lambda *a:called.append(True))
        for health in (None,'starting','unhealthy'):
            row=deepcopy(f.active);row['health']=health;backend.inspect=lambda cid:deepcopy(row)
            with self.assertRaisesRegex(RuntimeError,'normal_role_unready'):backend.functional(backend.role,row)
        self.assertEqual(called,[True,True,True])
    def test_source_library_has_no_appsite_historical_controllers_or_entrypoint_and_is_pinned(self):
        raw=Path(M.__file__).read_bytes();tree=ast.parse(raw)
        self.assertNotIn(b'Site.__init__',raw);self.assertNotIn(b'Site.__new__',raw)
        for n in ast.walk(tree):
            if isinstance(n,ast.ClassDef):self.assertFalse(any(isinstance(b,ast.Attribute) and b.attr=='Site' for b in n.bases))
        self.assertNotIn(b'if __name__',raw)
        for name,pin in (('canonical_app_activation_contract.py',M.BASE_CONTRACT_SHA),('canonical_app_activation_site.py',M.BASE_UTILITIES_SHA)):
            self.assertEqual(sha256((ROOT/'deploy/maintenance'/name).read_bytes()).hexdigest(),pin)

    def backend(self,fault=None):
        f=self.case();b=object.__new__(M.MarketBackend);b.C=self.C;b.S=self.S;b.stack=self.stack;b.role='market-processor';b.site='web'
        b.root=self.root/'owned-site';b.root.mkdir(mode=0o700);(b.root/'attempts').mkdir(mode=0o700)
        b.scope=f.scope;b.paths=f.paths;b.control={'nonce':'a'*32,'controller_commit':'b'*40,'runtime_env_delta':{}}
        b.control_sha256='c'*64;b.deadline=time.monotonic()+300;b.checks=self.checks;b.guard=None;b.journal=None;b.terminal=None;b.rollback_authorized=False
        b.original={f.old_row['id']:deepcopy(f.old_row)};b.current=deepcopy(b.original);b.measured=deepcopy(b.original)
        b.role_ids={b.role:f.old_row['id']};b.images={f.old_image['Id']:f.old_image};b.new_image=f.new_image;b.candidate=f.new_image['Id']
        b.old_model=f.old;b.new_model=f.new;b.rollback_model=deepcopy(f.old);b.rollback_model['services'][b.role]['image']=f.old_image['Id']
        b.completed=[];b.transactions={};b.override=b.root/'attempts'/b.control['nonce']/'image-only.yml';b.rollback_override=b.override.with_name('old-image.yml');b.calls=[]
        b.inventory=lambda:deepcopy(b.measured);b.inspect=lambda cid:deepcopy(b.measured[cid]);b.units_check=lambda:None
        b.render=lambda override=None,**kw:deepcopy(b.new_model if override==b.override else b.rollback_model)
        b.cgroup=lambda row:(lambda:b.calls.append(('empty',row['id'])))
        def owner(role,backend,excluding):return {'schema':'normal_market_owner/1','role':role,'excluded_container_id':excluding,
            'single_owner':True,'legacy_owners_absent':True,'capture_cgroup_singleton':True,'security_accepted':False}
        def functional(role,backend,row):
            b.calls.append(('functional',row['id']))
            return {'schema':'normal_market_functional/1','role':role,'container_id':row['id'],'image_id':row['image'],
                'pid':row['pid'],'started':row['started'],'restarts':row['restarts'],'release':f.scope['release'],
                'status':'wrong' if fault=='functional' and row['image']==b.candidate else 'ready',
                'normal_transport_verified':True,'durable_progress_fresh':True,'owner_lock_held':True,'security_accepted':False}
        def fences(role,backend):return {'schema':'normal_market_live_peer_fences/1','operation':M.OPERATION,'control_sha256':b.control_sha256,
            'nonce':b.control['nonce'],'role':role,'site':b.site,'native_source_held':True,'both_markers_held':True,
            'pending_release_absent':True,'guard_owner_exact':True,'reader_units_unchanged':True,
            'other_host_runtime_unchanged':True,'legacy_owners_absent':True,'security_accepted':False}
        b.producer=SimpleNamespace(owners=owner,functional=functional,fences=fences)
        def cmd(argv,**kw):
            if 'up' in argv:
                self.assertIn('--no-start',argv);self.assertIn('--no-deps',argv);self.assertIn('--no-build',argv);self.assertIn('--pull',argv)
                candidate=str(b.override) in argv;cid='2'*64 if candidate else '3'*64;row=deepcopy(f.stopped)
                image=b.candidate if candidate else f.old_image['Id'];row.update(id=cid,image=image)
                row['config'].update(Image=image,Hostname=cid[:12]);row['config']['Labels'].update({'com.docker.compose.image':image,
                    'com.docker.compose.project.config_files':','.join([*f.paths,str(b.override if candidate else b.rollback_override)])})
                if candidate and fault=='config':row['config']['Cmd']=['WRONG']
                b.calls.append(('create',candidate));b.measured={cid:row};return b''
            if 'ps' in argv:return (next(iter(b.measured))+'\n').encode()
            self.fail('unexpected normal command')
        b.cmd=cmd
        def docker(*argv,**kw):
            action,cid=argv[0],argv[-1];b.calls.append((action,cid));row=b.measured[cid]
            if action=='stop':
                if fault=='unknown_stop':raise b.S.Unknown('stop_outcome_unknown')
                row.update(running=False,pid=0)
            elif action=='start':
                row.update(running=True,pid=4567,started='2026-10-25T12:00:00Z',health='healthy')
                row['networks']=deepcopy(f.active['networks'])
                for network in row['networks'].values():network['DNSNames']=[f.scope['container_name'],cid[:12]]
                if fault=='unknown_start':raise b.S.Unknown('start_outcome_unknown')
            else:self.fail('unexpected Docker command')
            return b''
        b.docker=docker;return b

    def test_actual_market_journal_and_reused_stopped_first_transaction_exact_order(self):
        b=self.backend();b.begin();result=b.replace();self.assertEqual(result['status'],'active')
        actions=[r[0] for r in b.calls];self.assertLess(actions.index('stop'),actions.index('empty'))
        self.assertLess(actions.index('empty'),actions.index('create'));self.assertLess(actions.index('create'),actions.index('start'))
        self.assertEqual(actions.count('start'),1);self.assertEqual(b.completed,['market-processor'])
        steps=[json.loads(p.read_bytes()) for p in sorted(b.journal.path.glob('[0-9]*.json'))]
        self.assertTrue(all(r['schema']=='normal_market_activation_step/1' and r['service']=='market-processor' for r in steps))
        self.assertTrue((b.root/'activation.guard').exists())
    def test_known_stopped_config_failure_can_rollback_once_with_old_image_and_empty_proof(self):
        b=self.backend('config');b.begin()
        with self.assertRaises(b.S.KnownFailure):b.replace()
        self.assertTrue(b.rollback_authorized);self.assertFalse(any(r[0]=='start' for r in b.calls))
        result=b.rollback();self.assertEqual(result['status'],'rolled_back');self.assertEqual(b.inspect(result['container_id'])['image'],b.scope.get('old_image',next(iter(b.images))))
        self.assertEqual([r for r in b.calls if r[0]=='create'],[('create',True),('create',False)])
        self.assertFalse(b.rollback_authorized)
        with self.assertRaisesRegex(RuntimeError,'rollback_unproved'):b.rollback()
    def test_actual_functional_rejection_can_normal_rollback_but_unknown_stop_or_start_never_can(self):
        b=self.backend('functional');b.begin()
        with self.assertRaises(b.S.KnownFailure):b.replace()
        self.assertEqual(b.rollback()['status'],'rolled_back')
        # Each other fixture is a separate owned namespace; unknown is never
        # adopted or converted into normal rollback by a new nonce.
        for fault in ('unknown_stop','unknown_start'):
            self.root=self.root/fault;self.root.mkdir(mode=0o700);b=self.backend(fault);b.begin()
            with self.assertRaises(b.S.Unknown):b.replace()
            with self.assertRaisesRegex(RuntimeError,'rollback_unproved'):b.rollback()
            self.assertTrue((b.root/'activation.guard').exists());self.assertNotIn(('create',False),b.calls)
    def test_terminal_durable_before_any_closure_and_library_does_not_grant_peer_close_authority(self):
        b=self.backend();b.begin();b.replace();terminal=b.prepare_terminal()
        self.assertIs(terminal['guard_retained'],True);self.assertIs(terminal['cross_site_closure_proven'],False)
        self.assertEqual(terminal['terminal_sha256'],sha256((b.journal.path/'terminal.json').read_bytes()).hexdigest())
        self.assertTrue((b.root/'activation.guard').exists());b.guard.check()
        self.assertFalse(hasattr(b,'close'));self.assertIs(terminal['full_matrix_accepted'],False)
    def test_source_admitted_normal_producer_is_required_exact_namespace_not_flags_or_imported_callbacks(self):
        raw=b'def verify_gate(*args): return None\ndef owners(*args): return None\ndef functional(*args): return None\ndef fences(*args): return None\n'
        path=self.private(self.root/M.PRODUCER,raw)
        held=self.S.Held(path,self.stack,pin=sha256(raw).hexdigest());producer=M.normal_producer(held.raw,path)
        self.assertIs(producer.functional.__globals__,producer.__dict__)
        for bad in (b'verify_gate=owners=functional=fences=True\n',b'from json import dumps as verify_gate\nowners=functional=fences=verify_gate\n',
                raw.replace(b'def fences',b'def missing_fences')):
            with self.assertRaises(RuntimeError):M.normal_producer(bad,path)
        with self.assertRaises(RuntimeError):M.normal_producer(raw,path.with_name('arbitrary.py'))
    def test_lost_or_replayed_live_peer_source_marker_proof_refuses_before_guard_stop_create(self):
        b=self.backend();proof=b.producer.fences(b.role,b)
        for change in ({'native_source_held':False},{'both_markers_held':False},{'pending_release_absent':False},
                {'nonce':'f'*32},{'control_sha256':'f'*64},{'security_accepted':True},{'private_configuration':'FORBIDDEN'}):
            b.producer.fences=lambda *a,change=change:{**proof,**change}
            with self.subTest(change=change),self.assertRaisesRegex(RuntimeError,'normal_live_fences_unproved'):b.begin()
            self.assertEqual(b.calls,[]);self.assertIsNone(b.guard);self.assertFalse((b.root/'activation.guard').exists())
    def test_lifecycle_delegates_to_the_one_shared_retained_cgroup_proof(self):
        row={"public":"owned lifecycle operand"};returned=lambda:None;backend=SimpleNamespace(S=self.S,stack=self.stack)
        with patch.object(self.S,"bind_cgroup",return_value=returned) as shared:
            self.assertIs(M.bind_cgroup(backend,row),returned);shared.assert_called_once_with(self.stack,row)
        site=SimpleNamespace(stack=self.stack)
        with patch.object(self.S,"bind_cgroup",return_value=returned) as shared:
            self.assertIs(self.S.Site.cgroup(site,row),returned);shared.assert_called_once_with(self.stack,row)
    def test_actual_cold_public_utility_copy_drift_preserves_primary_and_refuses_late_success(self):
        directory=self.root/'sealed';directory.mkdir(mode=0o700)
        own=self.private(directory/'normal_market_activation.py',Path(M.__file__).read_bytes(),0o644)
        for name in ('canonical_app_activation_contract.py','canonical_app_activation_site.py'):
            self.private(directory/name,(ROOT/'deploy/maintenance'/name).read_bytes(),0o644)
        private=ModuleType('_owned_cold_market');private.__file__=str(own)
        exec(compile(own.read_bytes(),str(own),'exec'),private.__dict__)
        with self.assertRaisesRegex(RuntimeError,'source_changed'):
            with ExitStack() as stack:
                _,_,checks=private.primitives(stack)
                (directory/'canonical_app_activation_site.py').write_bytes(b'PUBLIC MUTATION')
                with self.assertRaisesRegex(RuntimeError,'source_changed'):checks[-1]()
    def test_real_root_group_read_secret_and_alias_chain_are_opaque_and_mode_drift_refuses(self):
        parent=self.root/'public-secret-dir';parent.mkdir(mode=0o750);os.chown(parent,0,10001)
        target=self.private(parent/'secret',b'PUBLIC SYNTHETIC SECRET',0o440);os.chown(target,0,10001)
        alias=parent/'reference';alias.symlink_to('secret')
        proof={'path':str(alias),'target':str(target),'identity':self.S.identity(target.stat()),'cli':False,
            'aliases':[{'path':str(alias),'identity':self.S.identity(alias.lstat())}]}
        def no_payload(*args):self.fail('secret payload was read')
        with patch.object(os,'pread',side_effect=no_payload),ExitStack() as stack:
            held=M.SecretReference(proof,self.S,stack);held.check()
        with self.assertRaisesRegex(RuntimeError,'secret_changed'):
            with ExitStack() as stack:
                held=M.SecretReference(proof,self.S,stack);target.chmod(0o640)
                with self.assertRaisesRegex(RuntimeError,'secret_changed'):held.check()
    def test_undeclared_directory_symlink_before_dotdot_cannot_redirect_normal_secret(self):
        target=self.private(self.root/'t/secret',b'PUBLIC DECLARED')
        alternate=self.private(self.root/'other/t/secret',b'PUBLIC OTHER')
        (self.root/'other/nested').mkdir(mode=0o700);(self.root/'redirect').symlink_to('other/nested',target_is_directory=True)
        alias=self.root/'alias';alias.symlink_to('redirect/../t/secret')
        self.assertEqual(alias.resolve(),alternate)
        proof={'path':str(alias),'target':str(target),'identity':self.S.identity(target.stat()),'cli':False,
            'aliases':[{'path':str(alias),'identity':self.S.identity(alias.lstat())}]}
        with ExitStack() as stack,self.assertRaises(RuntimeError):M.SecretReference(proof,self.S,stack)
        # Even an undeclared directory alias which happens to reach the same
        # target is unsupported. Plain canonical '..' is safe and explicit.
        alias.unlink();alias.symlink_to('redirect/../../t/secret');proof['aliases'][0]['identity']=self.S.identity(alias.lstat())
        with ExitStack() as stack,self.assertRaises(RuntimeError):M.SecretReference(proof,self.S,stack)
        alias.unlink();(self.root/'plain').mkdir(mode=0o700);alias.symlink_to('plain/../t/secret');proof['aliases'][0]['identity']=self.S.identity(alias.lstat())
        with ExitStack() as stack:
            held=M.SecretReference(proof,self.S,stack);held.check()
    def test_normal_producer_can_wait_starting_to_healthy_then_fresh_inspect_still_binds_same_lifecycle(self):
        b=self.backend();value=deepcopy(next(iter(b.measured.values())));value['health']='starting';b.measured={value['id']:deepcopy(value)}
        original=b.producer.functional
        def becomes_ready(role,backend,row):
            b.measured[row['id']]['health']='healthy';return original(role,backend,row)
        b.producer.functional=becomes_ready;b.functional(b.role,value)
        self.assertEqual(b.measured[value['id']]['health'],'healthy')
        def silently_restarts(role,backend,row):
            b.measured[row['id']]['pid']+=1;return original(role,backend,row)
        b.producer.functional=silently_restarts
        with self.assertRaisesRegex(RuntimeError,'normal_functional_identity'):b.functional(b.role,value)

if __name__=='__main__':unittest.main()
