"""Genuine owned public fixtures. No service, client, backup or private inputs."""
from contextlib import ExitStack
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from types import ModuleType,SimpleNamespace
from unittest.mock import patch
import ast,json,os,re,stat,tempfile,time,unittest

ROOT=Path(__file__).resolve().parents[1]
DENIED=('subprocess.run','subprocess.Popen','os.system','os.popen','os.fork','os.kill','os.killpg',
    'socket.socket','socket.create_connection','socket.getaddrinfo','urllib.request.urlopen','ctypes.CDLL',
    'resource.setrlimit','signal.alarm','signal.signal')

def module():
    path=ROOT/'deploy/maintenance/normal_datastore_activation.py'
    value=ModuleType('_public_datastore_activation_fixture');value.__file__=str(path)
    exec(compile(path.read_bytes(),str(path),'exec'),value.__dict__);return value

with ExitStack() as importing:
    for target in DENIED:importing.enter_context(patch(target,side_effect=AssertionError('ambient denied')))
    M=module()

class DatastoreTests(unittest.TestCase):
    def test_datastore_stop_uses_the_one_shared_retained_cgroup_proof(self):
        row={'public':'owned datastore operand'};returned=lambda:None
        with patch.object(self.S,'bind_cgroup',return_value=returned) as shared:
            self.assertIs(M.bind_cgroup(SimpleNamespace(S=self.S,stack=self.stack),row),returned)
            shared.assert_called_once_with(self.stack,row)
    def setUp(self):
        self.assertEqual((os.geteuid(),os.getegid()),(0,0))
        self.stack=ExitStack();self.addCleanup(self.stack.close)
        for name in DENIED:self.stack.enter_context(patch(name,side_effect=AssertionError('ambient denied')))
        self.root=Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix='.normal-datastore-',dir=ROOT)))
        self.root.chmod(0o700);self.C,self.S,self.checks=M.primitives(self.stack)
        self.S.C=self.C;self.now=int(time.time())
        source=ast.parse((ROOT/'tests/test_canonical_app_activation.py').read_bytes())
        node=next(n for n in source.body if isinstance(n,ast.FunctionDef) and n.name=='fixture')
        namespace={'C':self.C,'SimpleNamespace':SimpleNamespace,'deepcopy':deepcopy}
        exec(compile(ast.Module(body=[node],type_ignores=[]),'public_operand_builder','exec'),namespace)
        self.builder=namespace['fixture']
    def private(self,path,raw,mode=0o600):
        path.parent.mkdir(parents=True,exist_ok=True,mode=0o700);path.write_bytes(raw);path.chmod(mode);return path
    def case(self,role='native-postgres'):
        f=self.builder();service=M.SERVICE[role];profile=M.ROLES[role][1]
        for model in (f.old,f.new):
            model['services'][service]=model['services'].pop('app');spec=model['services'][service]
            spec['container_name']='fixture_'+service;spec['command']=['postgres'] if profile=='postgres' else ['redis-server']
            spec['environment']={'PUBLIC_BUSINESS_VALUE':'PUBLIC_SAME'}
            spec['profiles']=['foundation-template','web'] if role=='web-market-postgres' else []
        f.scope.update(container_name='fixture_'+service,profiles=f.old['services'][service]['profiles'],env_files=[],secret_files=[],public_sources=[],
            image_env_delta={},data_bindings=[])
        version='PG_VERSION' if profile=='postgres' else 'REDIS_VERSION'
        defaults={'Cmd':['postgres'] if profile=='postgres' else ['redis-server'],'Entrypoint':None,
            'Env':['PATH=/usr/bin',version+'='+M.VERSIONS[profile]],'WorkingDir':'/data','User':'','Volumes':None,'Labels':{}}
        f.old_image['Config']=deepcopy(defaults);f.new_image['Config']=deepcopy(defaults)
        for row in (f.old_row,f.active,f.stopped):
            row['name']='/fixture_'+service;cfg=row['config'];cfg['User']='';cfg['WorkingDir']='/data'
            cfg['Cmd']=deepcopy(defaults['Cmd']);cfg['Entrypoint']=None;cfg['Env']=[*defaults['Env'],'PUBLIC_BUSINESS_VALUE=PUBLIC_SAME']
            cfg['Labels']={k:v for k,v in cfg['Labels'].items() if k.startswith('com.docker.compose.')}
            cfg['Labels']['com.docker.compose.service']=service;row['health']=None
            for network in row['networks'].values():
                network['Aliases']=['fixture_'+service,service]
                network['DNSNames']=['fixture_'+service,row['id'][:12]] if row['running'] else None
        f.scope['image_env_delta']={};return f
    def control(self,role='native-postgres'):
        C=self.C;value=M.unarmed_control();scope=self.case(role).scope
        scope.pop('public_image_env_delta')
        env=self.private(self.root/'normal.env',b'PUBLIC UNIT FIXTURE')
        data=self.root/'data';data.mkdir(mode=0o700)
        profile=M.ROLES[role][1]
        scope.update(project='fixture',workdir=str(self.root),container_name='fixture_'+M.SERVICE[role],
            compose_files=[{'path':str(self.root/'normal.yml'),'bytes':1,'sha256':'c'*64}],
            env_files=[{'path':str(env),'target':str(env),'identity':self.S.identity(env.stat()),'aliases':[],'cli':True}],
            secret_files=[],public_sources=[],release='d'*40,compose_version='2.40.3',docker_version='28.5.1',environment_file_label=None,
            data_bindings=[{'path':str(data),'target':'/var/lib/postgresql/data' if profile=='postgres' else '/data','identity':self.S.identity(data.stat())[:5]}],
            baseline={'container_id':'1'*64,'image_id':'sha256:'+'a'*64,'pid':1234,'started':'2026-10-25T00:00:00Z','restarts':0})
        if role!='web-market-postgres':
            scope.update(project=C.SITES[M.ROLES[role][0]]['project'],workdir=C.SITES[M.ROLES[role][0]]['workdir'])
        value.update(role=role,nonce='a'*32,controller_commit='b'*40,issued_at=self.now-1,forward_deadline=self.now+300,recovery_deadline=self.now+600,scope=scope,
            adapter_source={'name':'normal_datastore_activation.py','bytes':1,'sha256':'c'*64})
        for site in ('bot','web'):
            value['sites'][site]={'machine_id':'d'*32,'fences':{'marker':[1,2,0o100600,0,0,1,0,1,1]},
                'reader_units':{name:{'LoadState':'loaded','UnitFileState':'enabled','NeedDaemonReload':'no'} for name in C.READER_UNITS} if site=='bot' else {},
                'legacy_units':{'public-old.service':{'ActiveState':'inactive','MainPID':'0','ControlPID':'0','UnitFileState':'disabled','NeedDaemonReload':'no'}}}
        value['sites']['bot']['fences']['source']=deepcopy(M.SOURCE_ID)
        value['gates']={name:{'name':M.PRODUCER if name=='functional_producer' else name+'.json','bytes':1,'sha256':'c'*64} for name in value['gates']}
        return value
    def test_shared_rollback_forwards_explicit_no_dependencies_mode(self):
        role='native-postgres';f=self.case(role);service=M.SERVICE[role];labels=f.old_row['config']['Labels'];key='com.docker.compose.depends_on'
        f.old['services'][service]['depends_on']={'redis':{'condition':'service_started'}}
        M.baseline(self.C,f.old,role,f.old_row,f.old_image,f.scope,f.paths,creation_no_dependencies=True)
        labels[key]='redis:service_started:false'
        with self.assertRaisesRegex(RuntimeError,'compose_labels'):
            M.baseline(self.C,f.old,role,f.old_row,f.old_image,f.scope,f.paths,creation_no_dependencies=True)
        M.baseline(self.C,f.old,role,f.old_row,f.old_image,f.scope,f.paths)
        labels.pop(key)
        with self.assertRaisesRegex(RuntimeError,'compose_labels'):
            M.baseline(self.C,f.old,role,f.old_row,f.old_image,f.scope,f.paths,creation_no_dependencies=True)

    def test_fixed_five_roles_and_unarmed_factory_never_requests_commands(self):
        self.assertEqual(len(M.ROLES),5);self.assertEqual(sum(profile=='postgres' for _,profile in M.ROLES.values()),3)
        with self.assertRaisesRegex(RuntimeError,'unarmed'):M.DatastoreBackend(M.unarmed_control(),self.stack)
    def test_each_role_complete_control_and_every_missing_gate_refuses(self):
        for role in M.ROLES:
            value=self.control(role);M.validate_control(self.C,value,self.now)
            for gate in value['gates']:
                changed=deepcopy(value);changed['gates'][gate]=None
                with self.subTest(role=role,gate=gate),self.assertRaises(RuntimeError):M.validate_control(self.C,changed,self.now)
            # Each iteration owns a separate, genuine directory.
            (self.root/'data').rmdir()
    def test_control_cannot_enable_environment_migration_or_writer_changes(self):
        value=self.control()
        for mutate in (lambda v:v.update(runtime_env_delta={'POSTGRES_DB':'other'}),lambda v:v.update(writer_change_permitted=True),
            lambda v:v['scope'].update(profiles=['unknown']),lambda v:v['sites']['bot']['fences']['source'].__setitem__(6,1),
            lambda v:v['scope']['data_bindings'][0]['identity'].__setitem__(3,True),
            lambda v:v['scope'].update(project='shadow'),lambda v:v['scope'].update(workdir='/other-release')):
            changed=deepcopy(value);mutate(changed)
            with self.assertRaises(RuntimeError):M.validate_control(self.C,changed,self.now)
    def test_all_five_datastore_replace_labels_preserve_explicit_names_and_implicit_service_number(self):
        for role in M.ROLES:
            for implicit in (False,True):
                f=self.case(role);service=M.SERVICE[role];spec=f.old['services'][service]
                if implicit:
                    spec.pop('container_name');actual=f.scope['project']+'-'+service+'-1'
                    f.scope['container_name']=actual;f.old_row['name']='/'+actual
                    for net in f.old_row['networks'].values():net['Aliases']=[actual,service];net['DNSNames']=[actual,f.old_row['id'][:12]]
                    label=service+'-1'
                else:label=actual=spec['container_name']
                labels=f.old_row['config']['Labels'];labels['com.docker.compose.replace']=label;saved=deepcopy(f)
                with self.subTest(role=role,implicit=implicit):
                    M.baseline(self.C,f.old,role,f.old_row,f.old_image,f.scope,f.paths,replacement=True)
                    self.assertEqual(f.__dict__,saved.__dict__);self.assertEqual(f.old_row['name'],'/'+actual)
                    with self.assertRaisesRegex(RuntimeError,'compose_labels'):
                        M.baseline(self.C,f.old,role,f.old_row,f.old_image,f.scope,f.paths,replacement=False)
                    for wrong in (role+'-1',f.old_row['id'],actual if implicit else service+'-1',''):
                        labels['com.docker.compose.replace']=wrong
                        with self.subTest(wrong=wrong),self.assertRaisesRegex(RuntimeError,'compose_labels'):
                            M.baseline(self.C,f.old,role,f.old_row,f.old_image,f.scope,f.paths,replacement=True)
                    if implicit:self.assertNotIn('container_name',spec)

    def test_datastore_null_and_missing_commands_inherit_without_empty_or_runtime_drift_waivers(self):
        for role in M.ROLES:
            for key,field in (('command','Cmd'),('entrypoint','Entrypoint')):
                for setting in ('missing','null','empty','explicit'):
                    f=self.case(role);spec=f.old['services'][M.SERVICE[role]];defaults=f.old_image['Config']
                    defaults[field]=['public-image-default',field]
                    if setting=='missing':spec.pop(key,None)
                    else:spec[key]={'null':None,'empty':[],'explicit':['public-override',field]}[setting]
                    expected=defaults[field] if setting in ('missing','null') else spec[key]
                    f.old_row['config'][field]=deepcopy(expected);before=deepcopy((spec,defaults,f.old_row))
                    with self.subTest(role=role,key=key,setting=setting):
                        M.baseline(self.C,f.old,role,f.old_row,f.old_image,f.scope,f.paths)
                        self.assertEqual((spec,defaults,f.old_row),before)
                        for changed in (None,[],['public-runtime-drift']):
                            if changed==expected:continue
                            f.old_row['config'][field]=changed
                            with self.assertRaisesRegex(RuntimeError,'normal_execution'):
                                M.baseline(self.C,f.old,role,f.old_row,f.old_image,f.scope,f.paths)
        for key in ('command','entrypoint'):
            for bad in (False,0,'',{},[False],['public',1]):
                f=self.case();f.old['services']['db'][key]=bad
                with self.subTest(key=key,bad=bad),self.assertRaisesRegex(RuntimeError,'normal_command'):
                    M.baseline(self.C,f.old,'native-postgres',f.old_row,f.old_image,f.scope,f.paths)

    def test_all_five_normal_model_baselines_and_complete_parity(self):
        for role in M.ROLES:
            f=self.case(role);M.baseline(self.C,f.old,role,f.old_row,f.old_image,f.scope,f.paths)
            M.parity(self.C,f.old,f.new,role,f.old_row,f.active,f.old_image,f.new_image,f.scope,f.paths,[*f.paths,f.override],{})
    def test_all_five_datastore_memory_byte_strings_and_default_swap_preserve_raw_complete_parity(self):
        for role in M.ROLES:
            for explicit_swap in (True,False):
                f=self.case(role);service=M.SERVICE[role]
                for model in (f.old,f.new):
                    spec=model['services'][service];spec['mem_limit']='96m'
                    if explicit_swap:spec['memswap_limit']='192m'
                    else:spec.pop('memswap_limit',None)
                for row in (f.old_row,f.stopped,f.active):row['host'].update(Memory=96*1024**2,MemorySwap=192*1024**2)
                saved=deepcopy(f);M.baseline(self.C,f.old,role,f.old_row,f.old_image,f.scope,f.paths)
                M.parity(self.C,f.old,f.new,role,f.old_row,f.active,f.old_image,f.new_image,f.scope,f.paths,[*f.paths,f.override],{})
                self.assertEqual(f.__dict__,saved.__dict__)
    def test_datastore_memory_refuses_malformed_overflow_actual_drift_and_unsupported_deploy(self):
        for key in ('mem_limit','memswap_limit'):
            for bad in (True,False,1.0,float('inf'),None,-1,'-1','','1mb',' 1m','1e9','0.1b',2**63,'9'*65):
                f=self.case();f.old['services']['db'][key]=bad
                with self.subTest(key=key,bad=bad),self.assertRaises(RuntimeError):M.baseline(self.C,f.old,'native-postgres',f.old_row,f.old_image,f.scope,f.paths)
        with self.assertRaisesRegex(RuntimeError,'normal_resource'):M.normal_memory_spec({'mem_limit':2**62})
        for field in ('Memory','MemorySwap'):
            f=self.case();f.old['services']['db'].update(mem_limit='96m',memswap_limit='192m');f.old_row['host'].update(Memory=96*1024**2,MemorySwap=192*1024**2);f.old_row['host'][field]+=1
            with self.subTest(field=field),self.assertRaisesRegex(RuntimeError,'normal_resource'):M.baseline(self.C,f.old,'native-postgres',f.old_row,f.old_image,f.scope,f.paths)
        f=self.case();f.old['services']['db']['deploy']={'resources':{'limits':{'memory':'1g'}}}
        with self.assertRaisesRegex(RuntimeError,'normal_features'):M.baseline(self.C,f.old,'native-postgres',f.old_row,f.old_image,f.scope,f.paths)
    def test_public_market_postgres_inherited_profiles_keep_only_web_CLI_and_exact_rendered_pair(self):
        base=(ROOT/'deploy/market-data/compose.yml').read_text()
        web=(ROOT/'deploy/market-data/compose.web.yml').read_text()
        self.assertIn('  market-postgres-base:\n',base)
        self.assertIn('    profiles: [foundation-template]',re.search(r'(?ms)^  market-postgres-base:\n(.*?)(?=^  \S|\Z)',base).group(1))
        child=re.search(r'(?ms)^  market-database:\n(.*?)(?=^  \S|\Z)',web).group(1)
        self.assertIn('      file: compose.yml',child);self.assertIn('      service: market-postgres-base',child)
        self.assertIn('    profiles: [web]',child)
        f=self.case('web-market-postgres');spec=f.old['services']['market-database']
        for profiles in (['foundation-template','web'],['web','foundation-template']):
            spec['profiles']=profiles
            M.baseline(self.C,f.old,'web-market-postgres',f.old_row,f.old_image,f.scope,f.paths)
        for profiles in (['web'],['foundation-template'],['web','web'],['web','other'],['web','foundation-template',True],None,'web'):
            spec['profiles']=profiles
            with self.subTest(profiles=profiles),self.assertRaisesRegex(RuntimeError,'normal_features'):
                M.baseline(self.C,f.old,'web-market-postgres',f.old_row,f.old_image,f.scope,f.paths)
        for role in M.ROLES:
            b=object.__new__(M.DatastoreBackend);b.role=role;b.scope=self.case(role).scope;b.paths=[]
            b.scope['workdir']=str(self.root);b.scope['project']='fixture';b.scope['env_files']=[]
            argv=b.compose();actual=[argv[i+1] for i,v in enumerate(argv) if v=='--profile']
            self.assertEqual(actual,['web'] if role=='web-market-postgres' else [])
        value=self.control('web-market-postgres');M.validate_control(self.C,value,self.now)
        for profiles in (['web'],['web','foundation-template'],['foundation-template','web','web']):
            value['scope']['profiles']=profiles
            with self.assertRaisesRegex(RuntimeError,'normal_profiles'):M.validate_control(self.C,value,self.now)

    def test_explicit_public_vendor_minor_metadata_changes_preserve_business_environment(self):
        f=self.case();f.old_image['Config']['Env'][-1]='PG_VERSION=15.18';f.old_row['config']['Env'][1]='PG_VERSION=15.18'
        f.scope['image_env_delta']={'PG_VERSION':{'old':'15.18','new':'15.19'}}
        M.parity(self.C,f.old,f.new,'native-postgres',f.old_row,f.active,f.old_image,f.new_image,f.scope,f.paths,[*f.paths,f.override],{})
        for bad in ({},{'POSTGRES_PASSWORD':{'old':'PUBLIC','new':'OTHER'}},{'PG_VERSION':{'old':'14.9','new':'15.19'}}):
            with self.assertRaises(RuntimeError):M.image_defaults(self.C,f.old_image['Config'],f.new_image['Config'],bad,'postgres')
        wrong=deepcopy(f.active);wrong['config']['Env'][-1]='PUBLIC_BUSINESS_VALUE=CHANGED'
        with self.assertRaises(RuntimeError):M.parity(self.C,f.old,f.new,'native-postgres',f.old_row,wrong,f.old_image,f.new_image,f.scope,f.paths,[*f.paths,f.override],{})
    def test_config_host_mount_network_and_stop_timeout_drift_refuse(self):
        f=self.case()
        for field,key,value in (('config','User','999:999'),('config','StopTimeout',30),('host','Privileged',True),('host','Memory',99)):
            wrong=deepcopy(f.old_row);wrong[field][key]=value
            with self.subTest(field=key),self.assertRaises(RuntimeError):M.baseline(self.C,f.old,'native-postgres',wrong,f.old_image,f.scope,f.paths)
        wrong=deepcopy(f.active);wrong['networks']['fixture_net']['DriverOpts']={'unknown':'changed'}
        with self.assertRaises(RuntimeError):M.parity(self.C,f.old,f.new,'native-postgres',f.old_row,wrong,f.old_image,f.new_image,f.scope,f.paths,[*f.paths,f.override],{})
    def test_data_reference_allows_real_writes_without_reading_and_refuses_inode_or_mode_change(self):
        data=self.root/'data';data.mkdir(mode=0o700);proof={'path':str(data),'target':'/data','identity':self.S.identity(data.stat())[:5]}
        with patch.object(os,'pread',side_effect=AssertionError('no payload reads')),patch.object(os,'scandir',side_effect=AssertionError('no data inventory')):
            reference=M.DataReference(proof,self.C,self.S,self.stack);reference.check()
            self.private(data/'public-writer-output',b'PUBLIC');reference.check()
            data.chmod(0o750)
            try:
                with self.assertRaises(RuntimeError):reference.check()
            finally:data.chmod(0o700)
            moved=self.root/'old-data';data.rename(moved);data.mkdir(mode=0o700)
            try:
                with self.assertRaises(RuntimeError):reference.check()
            finally:data.rmdir();moved.rename(data)
    def test_data_directory_real_postgres_uid_is_held_with_metadata_only_descriptor(self):
        data=self.root/'postgres-owned-data';data.mkdir(mode=0o700);os.chown(data,70,70)
        self.assertEqual((data.stat().st_uid,data.stat().st_gid),(70,70))
        proof={'path':str(data),'target':'/var/lib/postgresql/data','identity':self.S.identity(data.stat())[:5]}
        try:
            with ExitStack() as held,patch.object(os,'pread',side_effect=AssertionError('data payload forbidden')):
                reference=M.DataReference(proof,self.C,self.S,held);reference.check()
                with self.assertRaises(OSError):os.read(reference.fd,1)
        finally:
            # Restore ownership only after the real UID70 FD's final check.
            os.chown(data,0,0)
    def test_secret_alias_hidden_directory_hop_and_permanent_payload_reads_refuse(self):
        parent=self.root/'public-secret';parent.mkdir(mode=0o700);target=self.private(parent/'value',b'PUBLIC',0o440)
        alias=self.root/'normal-secret';alias.symlink_to('public-secret/value')
        proof={'path':str(alias),'target':str(target),'identity':self.S.identity(target.stat()),'aliases':[{'path':str(alias),'identity':self.S.identity(alias.lstat())}]}
        with patch.object(os,'pread',side_effect=AssertionError('no secret payload')):
            with ExitStack() as stack:M.SecretReference(proof,self.S,stack).check()
            directory=self.root/'redirect';directory.symlink_to(parent,target_is_directory=True)
            alias.unlink();alias.symlink_to('redirect/../public-secret/value');proof['aliases'][0]['identity']=self.S.identity(alias.lstat())
            with ExitStack() as stack,self.assertRaises(RuntimeError):M.SecretReference(proof,self.S,stack)
    def test_normal_producer_requires_source_defined_callbacks_and_no_default_flags(self):
        for raw in (b'verify_gate=True',b'from builtins import print as functional\nverify_gate=owners=fences=functional'):
            with self.assertRaises(RuntimeError):M.normal_producer(raw,self.root/M.PRODUCER)
        raw=b'def verify_gate(*a):return False\ndef owners(*a):return {}\ndef functional(*a):return {}\ndef fences(*a):return {}'
        producer=M.normal_producer(raw,self.root/M.PRODUCER);self.assertIs(producer.verify_gate(None),False)
    def backend(self,health=None):
        b=object.__new__(M.DatastoreBackend);b.C=self.C;b.S=self.S;b.role='native-postgres';b.site='bot';b.scope=self.case().scope
        b.control=self.control();b.control_sha256='e'*64;b.compatibility={'old_server_version':'15.18','rollback_data_compatible':True,'implicit_migration_permitted':False}
        b.candidate=self.case().active['image'];b.check=lambda:None;b.singleton=lambda *a,**kw:None
        value=deepcopy(self.case().active);value['health']=health;b.measured=value
        b.inspect=lambda cid:deepcopy(b.measured)
        def ready(role,backend,row):
            return {'schema':'normal_datastore_functional/1','operation':M.OPERATION,'control_sha256':b.control_sha256,'nonce':b.control['nonce'],
                'role':role,'profile':'postgres','site':'bot','container_id':row['id'],'image_id':row['image'],'pid':row['pid'],
                'started':row['started'],'restarts':row['restarts'],'server_version':'15.19','status':'ready','authenticated':True,
                'persistence_ready':True,'stateful_restore_gate_bound':True,'security_accepted':False}
        b.producer=SimpleNamespace(functional=ready,verify_gate=lambda *a:True);return b,value
    def test_authenticated_normal_functional_proof_required_without_health_waiver(self):
        b,row=self.backend();b.functional(b.role,row)
        ready=b.producer.functional
        for change in ({'authenticated':False},{'persistence_ready':False},{'stateful_restore_gate_bound':False},{'server_version':'14.9'},{'Env':['PRIVATE']}):
            b.producer.functional=lambda *a,change=change:{**ready(*a),**change}
            with self.subTest(change=change),self.assertRaises(RuntimeError):b.functional(b.role,row)
    def test_normal_producer_waits_for_existing_health_then_fresh_pid_and_health_stay_strict(self):
        b,row=self.backend('starting');row['config']['Healthcheck']={'Test':['CMD','normal-check']};b.measured=deepcopy(row)
        ready=b.producer.functional
        def start_ready(*a):b.measured['health']='healthy';return ready(*a)
        b.producer.functional=start_ready;b.functional(b.role,row)
        for field,value in (('health','unhealthy'),('pid',9876)):
            def changed(*a,field=field,value=value):b.measured[field]=value;return ready(*a)
            b.producer.functional=changed
            with self.assertRaises(RuntimeError):b.functional(b.role,row)
            b.measured=deepcopy(row)
    def test_rollback_requires_known_failure_and_explicit_compatible_stateful_data(self):
        b,row=self.backend();b.transactions={b.role:{}};b.rollback_authorized=False;b.evidence=lambda binding:b''
        with self.assertRaises(RuntimeError):b.rollback()
        b.rollback_authorized=True;b.compatibility['rollback_data_compatible']=False
        with self.assertRaises(RuntimeError):b.rollback()
        b.compatibility['rollback_data_compatible']=True;b.compatibility['implicit_migration_permitted']=True
        with self.assertRaises(RuntimeError):b.rollback()

    def evidence_case(self,role='native-postgres',legacy=False):
        # These are explicit schema-routing fixtures, NOT actual LOAD/restore
        # receipts. No production gate or serving authority is manufactured.
        b=object.__new__(M.DatastoreBackend);b.C=self.C;b.S=self.S;b.stack=self.stack;b.checks=[]
        b.role=role;b.site=M.ROLES[role][0];b.control=self.control(role);b.scope=b.control['scope'];b.root=self.root/'public-evidence'
        (b.root/'evidence').mkdir(parents=True,mode=0o700)
        catalog=(ROOT/'deploy/maintenance/datastore_candidate_images_v2.json').read_bytes()
        self.private(b.root/'evidence/datastore_candidate_images.json',catalog)
        spec=next(r for r in json.loads(catalog)['profiles'] if r['profile']==M.ROLES[role][1]);profile=spec['profile']
        admitted={'schema':'market_candidate_archive/1','profile':profile,'candidate_config_id':spec['candidate_config_id'],
            'rootfs':spec['candidate_rootfs'],'archive_sha256':'b'*64,'source_identities':{spec['candidate_config_id']:{'kind':'Docker_config_identity'}},'security_accepted':False,
            'archive_bytes':spec['archive']['uncompressed_bytes'],'expanded_layer_bytes':1234,
            'prospective_import_bytes':2*spec['archive']['uncompressed_bytes']+2468,'configuration_values_disclosed':False,
            'tar_identity':[1,2,0o100600,0,0,1,spec['archive']['uncompressed_bytes'],1,1]}
        operation={'schema':'datastore_image_store_control/2' if legacy else 'datastore_image_store_control/3',
            'operation':'security-datastore-candidate-images-20261025-03','worker_sha256':M.LEGACY_LOAD_SHA if legacy else M.LOAD_WORKER_SHA,
            'profile':profile,'phase':'load-native' if b.site=='bot' else 'load-web','nonce':'f'*32,'admission_sha256':sha256(self.C.encoded(admitted)).hexdigest()}
        if not legacy:operation['health_policy_sha256']=M.HEALTH_POLICY
        receipt={'schema':'datastore_preloaded_image/2' if legacy else 'datastore_preloaded_image/3','operation':operation['operation'],
            'controller_sha256':operation['worker_sha256'],'engine_sha256':'1041cc2d04100e2f6aebb95dca30c2339de41342b350ef44522c05277e9c146e',
            'nonce':operation['nonce'],'profile':profile,'role':profile,'catalog_sha256':M.CATALOG_SHA,'site':'native' if b.site=='bot' else 'web',
            'host_machine_id':b.control['sites'][b.site]['machine_id'],'ci_run_id':spec['ci_run_id'],'image_artifact_sha256':spec['image_artifact']['digest'][7:],
            'candidate_config_id':spec['candidate_config_id'],'rootfs':spec['candidate_rootfs'],'operation_sha256':sha256(self.C.encoded(operation)).hexdigest(),
            'source_admission_sha256':sha256(self.C.encoded(admitted)).hexdigest(),'archive_and_config_association_verified':True,'whole_typed_config_compared_in_ram':True,
            'configuration_values_disclosed':False,'security_accepted':False,'serving_roles_activated':[],'serving_validation':'NOT_TESTED',
            'source_archive_sha256':admitted['archive_sha256'],'candidate_local_image_id':spec['candidate_config_id']}
        if not legacy:receipt.update(health_policy_sha256=M.HEALTH_POLICY,health_adapter_sha256=M.HEALTH_ADAPTER,runtime_health_issues_open=True,runtime_health_observations=[])
        proofs={}
        for key,schema in (('backup_restore','normal_datastore_backup_restore/1'),('stateful_compatibility','normal_datastore_stateful_compatibility/1'),('normal_prefix','normal_datastore_prefix/1')):
            proofs[key]={'schema':schema,'role':role,'profile':profile,'machine_id':b.control['sites'][b.site]['machine_id'],
                'candidate_config_id':spec['candidate_config_id'],'old_image_id':b.scope['baseline']['image_id'],'data_bindings':b.scope['data_bindings'],
                'completed_at':self.now,'security_accepted':False}
        proofs['backup_restore'].update(backup_complete=True,isolated_restore_passed=True,durable_receipt_verified=True)
        proofs['stateful_compatibility'].update(candidate_on_fresh_restore_passed=True,old_on_candidate_written_clone_passed=True,
            persistence_restart_passed=True,rollback_data_compatible=True,implicit_migration_permitted=False,
            candidate_server_version=M.VERSIONS[profile],old_server_version='15.18' if profile=='postgres' else '7.4.10')
        records={'archive_admission':admitted,'load_control':operation,'load_receipt':receipt,**proofs}
        def consume(link=True):
            if link:
                admission_pin=sha256(self.C.encoded(records['archive_admission'])).hexdigest()
                records['load_control']['admission_sha256']=admission_pin
                records['load_receipt']['source_admission_sha256']=admission_pin
                records['load_receipt']['operation_sha256']=sha256(self.C.encoded(records['load_control'])).hexdigest()
            for key,value in records.items():
                raw=self.C.encoded(value);name=key+'.json';self.private(b.root/'evidence'/name,raw)
                b.control['gates'][key]={'name':name,'bytes':len(raw),'sha256':sha256(raw).hexdigest()}
            with ExitStack() as stack:
                b.stack=stack;b.checks=[];return b.load_evidence()
        return b,spec,records,consume

    def test_actual_vendor_source_and_original_or_new_receipt_formats_route_without_relabeling(self):
        for legacy in (False,True):
            self.root=self.root/str(legacy);self.root.mkdir(mode=0o700)
            b,spec,records,consume=self.evidence_case('web-postgres',legacy)
            before=deepcopy(records);self.assertEqual(consume()[0],spec['candidate_config_id']);self.assertEqual(records,before)
            records['load_receipt']['schema']='datastore_preloaded_image/3' if legacy else 'datastore_preloaded_image/2'
            with self.assertRaises(RuntimeError):consume()

    def test_restore_and_candidate_to_old_written_format_proofs_are_mandatory_before_handoff(self):
        b,spec,records,consume=self.evidence_case();consume()
        for gate,key,bad in (('backup_restore','isolated_restore_passed',False),('backup_restore','backup_complete',False),
            ('stateful_compatibility','old_on_candidate_written_clone_passed',False),('stateful_compatibility','candidate_on_fresh_restore_passed',False),
            ('stateful_compatibility','persistence_restart_passed',False),('stateful_compatibility','rollback_data_compatible',False),
            ('stateful_compatibility','implicit_migration_permitted',True),('stateful_compatibility','old_server_version','14.9'),
            ('stateful_compatibility','candidate_server_version','16.0'),('normal_prefix','completed_at',self.now-20000)):
            old=records[gate][key];records[gate][key]=bad
            try:
                with self.subTest(gate=gate,key=key),self.assertRaises(RuntimeError):consume()
            finally:records[gate][key]=old

    def test_loaded_image_rootfs_policy_source_and_machine_provenance_must_all_match(self):
        b,spec,records,consume=self.evidence_case()
        for gate,key,bad in (('load_receipt','host_machine_id','0'*32),('load_receipt','rootfs',[]),('load_receipt','candidate_local_image_id','sha256:'+'0'*64),
            ('load_receipt','health_policy_sha256','0'*64),('load_receipt','health_adapter_sha256','0'*64),('load_receipt','security_accepted',True),
            ('load_control','worker_sha256',M.LEGACY_LOAD_SHA),('archive_admission','candidate_config_id','sha256:'+'0'*64),
            ('archive_admission','archive_bytes',1),('archive_admission','tar_identity',[1,2,0o100600,0,0,1,1,1,1])):
            old=records[gate][key];records[gate][key]=bad
            try:
                with self.subTest(gate=gate,key=key),self.assertRaises(RuntimeError):consume()
            finally:records[gate][key]=old
        records['load_receipt']['source_admission_sha256']='0'*64
        with self.assertRaises(RuntimeError):consume(False)
    def test_verified_stateful_gate_metadata_cannot_be_mutated_or_age_out_before_commands(self):
        b,spec,records,consume=self.evidence_case();consume();sealed_check=b.checks[-1];sealed_check()
        b.compatibility['rollback_data_compatible']=False
        with self.assertRaisesRegex(RuntimeError,'stateful_evidence_changed'):sealed_check()
        b.compatibility['rollback_data_compatible']=True;sealed_check()
        b.deadline=time.monotonic()+300;b.checks=[];b.guard=None
        b.normal_proofs['backup_restore']['completed_at']=int(time.time())-14401
        with self.assertRaisesRegex(RuntimeError,'stateful_evidence_expired'):b.check()
    def transaction_backend(self,fault=None):
        domain=SimpleNamespace(**{k:getattr(self.C,k) for k in ('MAX_JSON','require','same','document','public','encoded','absolute','hex64','environment','unordered_host','network_parity','other_containers')})
        domain.metadata=self.C.metadata;domain.OPERATION=M.OPERATION;domain.ROLE_ORDER=(('web','web-postgres'),)
        domain.normal_baseline=lambda *a,**kw:M.baseline(self.C,*a,**kw)
        domain.network_parity=lambda model,role,*a,**kw:self.C.network_parity(model,M.SERVICE[role],*a,**kw)
        domain.configuration_parity=lambda *a,**kw:M.parity(self.C,*a,**kw);self.S.C=domain
        f=self.case('web-postgres');b=object.__new__(M.DatastoreBackend);b.C=self.C;b.S=self.S;b.stack=self.stack;b.role='web-postgres';b.site='web'
        b.root=self.root/'owned-site';b.root.mkdir(mode=0o700);(b.root/'attempts').mkdir(mode=0o700)
        b.scope=f.scope;b.paths=f.paths;b.control={'nonce':'a'*32,'controller_commit':'b'*40,'runtime_env_delta':{},'recovery_deadline':int(time.time())+600}
        b.control_sha256='c'*64;b.deadline=time.monotonic()+300;b.checks=self.checks;b.guard=None;b.journal=None;b.terminal=None;b.rollback_authorized=False
        b.original={f.old_row['id']:deepcopy(f.old_row)};b.current=deepcopy(b.original);b.measured=deepcopy(b.original)
        b.role_ids={b.role:f.old_row['id']};b.images={f.old_image['Id']:f.old_image};b.new_image=f.new_image;b.candidate=f.new_image['Id']
        b.old_model=f.old;b.new_model=f.new;b.rollback_model=deepcopy(f.old);b.rollback_model['services']['db']['image']=f.old_image['Id']
        b.completed=[];b.transactions={};b.override=b.root/'attempts'/b.control['nonce']/'image-only.yml';b.rollback_override=b.override.with_name('old-image.yml');b.calls=[]
        b.inventory=lambda:deepcopy(b.measured);b.inspect=lambda cid:deepcopy(b.measured[cid]);b.units_check=lambda:None
        b.scope['release']='d'*40
        b.render=lambda override=None,**kw:deepcopy(b.new_model if override==b.override else b.rollback_model)
        b.cgroup=lambda row:(lambda:b.calls.append(('empty',row['id'])))
        def owner(role,backend,excluding):return {'schema':'normal_datastore_owner/1','role':role,'excluded_container_id':excluding,
            'single_owner':True,'legacy_owners_absent':True,'data_mount_singleton':True,'security_accepted':False}
        def functional(role,backend,row):
            b.calls.append(('functional',row['id']))
            return {'schema':'normal_datastore_functional/1','operation':M.OPERATION,'control_sha256':b.control_sha256,'nonce':b.control['nonce'],
                'role':role,'profile':'postgres','site':'web','container_id':row['id'],'image_id':row['image'],
                'pid':row['pid'],'started':row['started'],'restarts':row['restarts'],'server_version':'15.19',
                'status':'wrong' if fault=='functional' and row['image']==b.candidate else 'ready',
                'authenticated':True,'persistence_ready':True,'stateful_restore_gate_bound':True,'security_accepted':False}
        def fences(role,backend):return {'schema':'normal_datastore_live_peer_fences/1','operation':M.OPERATION,'control_sha256':b.control_sha256,
            'nonce':b.control['nonce'],'role':role,'site':b.site,'native_source_held':True,'both_markers_held':True,
            'pending_release_absent':True,'guard_owner_exact':True,'reader_units_unchanged':True,
            'other_host_runtime_unchanged':True,'legacy_owners_absent':True,'security_accepted':False}
        b.producer=SimpleNamespace(owners=owner,functional=functional,fences=fences,verify_gate=lambda *a:True)
        b.compatibility={'old_server_version':'15.19','rollback_data_compatible':True,'implicit_migration_permitted':False};b.evidence=lambda binding:b''
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

    def test_real_owned_journal_guard_and_unchanged_stopped_first_primitives(self):
        b=self.transaction_backend();b.begin();result=b.replace();self.assertEqual(result['status'],'active')
        events=[r[0] for r in b.calls]
        self.assertLess(events.index('stop'),events.index('empty'));self.assertLess(events.index('empty'),events.index('create'))
        self.assertLess(events.index('create'),events.index('start'));self.assertEqual(events.count('start'),1)
        self.assertTrue((b.root/'activation.guard').exists());self.assertEqual(b.completed,['web-postgres'])
        rows=[json.loads(p.read_bytes()) for p in b.journal.path.glob('[0-9]*.json')]
        self.assertTrue(rows and all(r['schema']=='normal_datastore_activation_step/1' and r['service']=='web-postgres' for r in rows))
        proof=b.prepare_terminal();self.assertTrue(proof['guard_retained']);self.assertFalse(proof['cross_site_closure_proven'])
        self.assertFalse(hasattr(b,'close'));self.assertEqual(proof['terminal_sha256'],sha256((b.journal.path/'terminal.json').read_bytes()).hexdigest())

    def test_known_configuration_failure_can_recreate_old_only_with_explicit_stateful_compatibility(self):
        b=self.transaction_backend('config');b.begin()
        with self.assertRaises(b.S.KnownFailure):b.replace()
        b.compatibility['rollback_data_compatible']=False
        with self.assertRaises(RuntimeError):b.rollback()
        self.assertEqual([x for x in b.calls if x[0]=='create'],[('create',True)])
        b.compatibility['rollback_data_compatible']=True
        self.assertEqual(b.rollback()['status'],'rolled_back');self.assertFalse(b.rollback_authorized)
        with self.assertRaises(RuntimeError):b.rollback()

    def test_unknown_stop_or_start_never_grants_datastore_rollback_or_guard_close(self):
        for fault in ('unknown_stop','unknown_start'):
            self.root=self.root/fault;self.root.mkdir(mode=0o700);b=self.transaction_backend(fault);b.begin()
            with self.assertRaises(b.S.Unknown):b.replace()
            with self.assertRaises(RuntimeError):b.rollback()
            self.assertTrue((b.root/'activation.guard').exists());self.assertNotIn(('create',False),b.calls)
    def test_live_both_host_fence_reader_and_other_runtime_proofs_cannot_be_flags_or_replayed(self):
        b=self.transaction_backend();original=b.producer.fences
        for change in ({'nonce':'0'*32},{'native_source_held':False},{'both_markers_held':False},
            {'reader_units_unchanged':False},{'other_host_runtime_unchanged':False},{'legacy_owners_absent':False}):
            b.producer.fences=lambda *a,change=change:{**original(*a),**change}
            with self.subTest(change=change),self.assertRaises(RuntimeError):b.begin()
            self.assertIsNone(b.guard);self.assertEqual(b.calls,[])

    def test_library_has_no_appsite_cli_restore_migration_or_unsealed_dependencies(self):
        raw=Path(M.__file__).read_bytes();tree=ast.parse(raw)
        self.assertNotIn(b'if __name__',raw);self.assertNotIn(b'Site.__init__',raw);self.assertNotIn(b'Site.__new__',raw)
        for n in ast.walk(tree):
            if isinstance(n,ast.Attribute):self.assertNotIn(n.attr,{'rmtree','unlink','remove','rename'})
        self.assertEqual(sha256((ROOT/'deploy/maintenance/canonical_app_activation_contract.py').read_bytes()).hexdigest(),M.BASE_CONTRACT_SHA)
        self.assertEqual(sha256((ROOT/'deploy/maintenance/canonical_app_activation_site.py').read_bytes()).hexdigest(),M.BASE_UTILITIES_SHA)

if __name__=='__main__':unittest.main()
