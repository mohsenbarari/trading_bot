"""Public synthetic fixtures only. No Docker/SSH/DB/provider/production data."""
from contextlib import ExitStack
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import ast
import importlib.util
import io
import json
import os
import shlex
import socket
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]


def module(suffix):
    path = ROOT/'deploy/maintenance'/('canonical_app_activation_'+suffix+'.py')
    spec = importlib.util.spec_from_file_location('_public_activation_'+suffix,path)
    value = importlib.util.module_from_spec(spec); spec.loader.exec_module(value); return value


C,S,K = module('contract'),module('site'),module('coordinator')
S.C = C; K.C = C; K.S = S


def fixture(service='app',site='web'):
    iid = 'sha256:'+'a'*64; cid = '1'*64; newcid = '2'*64; net = 'fixture_net'
    scope = {'project':C.SITES[site]['project'],'workdir':C.SITES[site]['workdir'],'compose_version':'2.40.3',
        'docker_version':'28.5.1','environment_file_label':None,
        'public_image_env_delta':{'PYTHON_VERSION':{'old':'3.11.13','new':'3.11.14'},
            'PYTHON_SHA256':{'old':'PUBLIC_OLD','new':'PUBLIC_NEW'}}}
    paths = ['/public/base.yml','/public/normal.yml']; override = '/public/one-image.yml'
    role = 'api' if service == 'app' else service
    spec = {'image':'public_old_image','container_name':'fixture_'+service,'command':['python','normal_'+service+'.py'],
        'environment':{'TRADING_BOT_SERVICE':role,'PUBLIC_BUSINESS_VALUE':'PUBLIC_SAME_VALUE'},
        'volumes':[],'networks':{'default':None},'restart':'always'}
    model = {'name':scope['project'],'services':{service:spec},'networks':{'default':{'name':net}}}
    after_model = deepcopy(model); after_model['services'][service]['image'] = C.IMAGE['image_id']
    before_defaults = {'Cmd':['python'],'Env':['PATH=/usr/local/bin:/usr/bin','LANG=C.UTF-8','GPG_KEY=PUBLIC_GPG',
        'PYTHON_VERSION=3.11.13','PYTHON_SHA256=PUBLIC_OLD'],'Labels':{},'WorkingDir':'/app'}
    after_defaults = deepcopy(before_defaults)
    after_defaults['Env'][-2:] = ['PYTHON_VERSION=3.11.14','PYTHON_SHA256=PUBLIC_NEW']
    after_defaults['Labels'] = dict(zip(C.PROVENANCE_LABELS,(C.IMAGE['source_commit'],C.IMAGE['source_tree'],C.IMAGE['input_signature'])))
    old_image = {'Id':iid,'Config':before_defaults}; new_image = {'Id':C.IMAGE['image_id'],'Config':after_defaults,'RootFS':deepcopy(C.IMAGE['rootfs'])}
    def make(identifier,image,defaults,files,stopped=False):
        labels = {**defaults['Labels'],**{'com.docker.compose.'+key:value for key,value in {
            'project':scope['project'],'service':service,'version':scope['compose_version'],'container-number':'1',
            'oneoff':'False','project.working_dir':scope['workdir'],'project.config_files':','.join(files),
            'image':image,'config-hash':'b'*64,'depends_on':''}.items()}}
        config = {'Image':image if image != iid else spec['image'],'Hostname':identifier[:12],'User':'','WorkingDir':'/app',
            'Cmd':spec['command'],'Entrypoint':None,'Env':[*defaults['Env'],*(k+'='+v for k,v in spec['environment'].items())],
            'Labels':labels,'Volumes':None,'StopSignal':''}
        host = {'Privileged':False,'ReadonlyRootfs':False,'AutoRemove':False,'PidMode':'','UsernsMode':'',
            'CapAdd':None,'CapDrop':None,'SecurityOpt':None,'GroupAdd':None,
            'RestartPolicy':{'Name':'always','MaximumRetryCount':0},'LogConfig':{'Type':'json-file','Config':{}},
            'PortBindings':None,'NetworkMode':net,'Init':None,'IpcMode':'private','CgroupnsMode':'private',
            'Runtime':'runc','ShmSize':67108864,'ConsoleSize':[0,0],'Memory':0,'MemorySwap':0,'PidsLimit':None,
            'NanoCpus':0,'ExtraHosts':None,'Ulimits':None,'MaskedPaths':['/proc/kcore'],'ReadonlyPaths':['/proc/bus'],
            'Binds':None,'Mounts':[],'FutureNeutral':False}
        network = {'NetworkID':'3'*64,'EndpointID':identifier,'MacAddress':'02:42:ac:12:00:02',
            'Gateway':'172.18.0.1','IPAddress':'172.18.0.'+('2' if identifier == cid else '3'),'IPPrefixLen':16,
            'IPv6Gateway':'','GlobalIPv6Address':'','GlobalIPv6PrefixLen':0,'DNSNames':[spec['container_name'],identifier[:12]],
            'Aliases':[spec['container_name'],service],'IPAMConfig':None,'Links':None,'DriverOpts':None}
        if stopped:
            for key in ('NetworkID','EndpointID','MacAddress','Gateway','IPAddress','IPv6Gateway','GlobalIPv6Address'):network[key] = ''
            network.update(IPPrefixLen=0,GlobalIPv6PrefixLen=0,DNSNames=None)
        return {'id':identifier,'name':'/'+spec['container_name'],'image':image,'config':config,'host':host,
            'mounts':[],'networks':{net:network},'running':not stopped,'pid':0 if stopped else 1234,
            'started':'0001-01-01T00:00:00Z' if stopped else '2026-10-25T10:00:00.000000000Z','restarts':0,'paused':False,'restarting':False,'dead':False,'health':None}
    return SimpleNamespace(old=model,new=after_model,scope=scope,paths=paths,override=override,
        old_image=old_image,new_image=new_image,old_row=make(cid,iid,before_defaults,paths),
        stopped=make(newcid,C.IMAGE['image_id'],after_defaults,[*paths,override],True),
        active=make(newcid,C.IMAGE['image_id'],after_defaults,[*paths,override]),make=make)


class FakeBackend:
    def __init__(self,case=None):
        self.f = fixture(); self.site = 'web'; self.scope = self.f.scope; self.old_model,self.new_model = self.f.old,self.f.new
        self.images = {self.f.old_image['Id']:self.f.old_image}; self.new_image = self.f.new_image
        self.paths,self.override,self.control = self.f.paths,self.f.override,{'runtime_env_delta':{}}
        self.rollback_override = '/public/old-images.yml'; self.rollback_model = deepcopy(self.old_model)
        self.rollback_model['services']['app']['image'] = self.f.old_image['Id']
        self.current = {self.f.old_row['id']:deepcopy(self.f.old_row),'9'*64:{'id':'9'*64,'public':'unrelated'}}
        self.actual = deepcopy(self.current); self.role_ids = {'app':self.f.old_row['id']}; self.completed = []
        self.transactions = {}; self.calls = []; self.case = case; self.journal = SimpleNamespace(record=lambda *args:self.calls.append(('journal',*args)))
    def inspect(self,cid):return deepcopy(self.actual[cid])
    def inventory(self):return deepcopy(self.actual)
    def unchanged(self,old=None,new=None):
        if old is None:C.require(C.same(S.comparable_inventory(self.current),S.comparable_inventory(self.actual)),'runtime_changed')
        else:C.other_containers(S.comparable_inventory(self.current),S.comparable_inventory(self.actual),old,new)
        return deepcopy(self.actual)
    def singleton(self,service,excluding=None):self.calls.append(('singleton',service))
    def stop(self,service,cid):
        self.calls.append(('stop',cid))
        if self.case == 'stop_unknown':raise S.Unknown('public_stop_unknown')
        self.actual[cid]['pid'] = 0; self.actual[cid]['running'] = False
        def empty():
            self.calls.append(('empty',cid)); C.require(self.actual.get(cid,{}).get('pid',0) == 0,'not_empty')
        return empty
    def create(self,service,candidate=True):
        self.calls.append(('create',candidate))
        if self.case == 'create_unknown':raise S.Unknown('public_create_unknown')
        oldid = next(k for k,v in self.actual.items() if v.get('name') == self.f.old_row['name']); self.actual.pop(oldid)
        if candidate:value = deepcopy(self.f.stopped)
        else:
            value = self.f.make('4'*64,self.f.old_image['Id'],self.f.old_image['Config'],[*self.paths,self.rollback_override],True)
            value['config']['Image'] = self.f.old_image['Id']
        if self.case == 'config_tamper' and candidate:value['config']['Env'].append('UNEXPECTED=PUBLIC_TAMPER')
        if self.case == 'other_drift':self.actual['9'*64]['public'] = 'changed'
        self.actual[value['id']] = deepcopy(value); return value
    def start(self,service,value):
        self.calls.append(('start',value['id']))
        if self.case == 'start_unknown':raise S.Unknown('public_start_unknown')
        if value['image'] == self.f.new_image['Id']:active = deepcopy(self.f.active)
        else:
            active = self.f.make(value['id'],self.f.old_image['Id'],self.f.old_image['Config'],[*self.paths,self.rollback_override])
            active['config']['Image'] = self.f.old_image['Id']
        self.actual[value['id']] = active; return deepcopy(active)
    def functional(self,service,value):
        self.calls.append(('functional',value['id']))
        if self.case == 'functional_failure' and value['image'] == self.f.new_image['Id']:raise RuntimeError('PUBLIC_UNREADY')


class ActivationTests(unittest.TestCase):
    def setUp(self):
        self.assertEqual((os.geteuid(),os.getegid()),(0,0),'requires genuine Root fixture lane')
        self.stack = ExitStack(); self.addCleanup(self.stack.close)
        for name in ('subprocess.run','subprocess.Popen','os.system','os.popen','socket.socket','socket.create_connection'):
            self.stack.enter_context(patch(name,side_effect=AssertionError('ambient operation denied')))
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix='.public-canonical-activation-',dir=Path.cwd())))
        self.root.chmod(0o700)
    def file(self,name,raw=b'PUBLIC FIXTURE'):
        path = self.root/name; path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        path.write_bytes(raw); path.chmod(0o600); return path
    def test_factory_is_unarmed_and_actual_fixed_ci_intake_is_preserved(self):
        control = C.unarmed_control()
        with self.assertRaisesRegex(RuntimeError,'unarmed'):C.validate_control(control,100)
        self.assertIsNone(control['gates']['host_loads']['bot']); self.assertIsNone(control['gates']['backup_restore'])
        self.assertEqual(C.SMOKE['run_id'],40196208692); self.assertEqual(C.SMOKE['artifact_id'],12599936628)
        self.assertEqual(C.IMAGE['source_commit'],'665220a2b5086597daa4565eef6450a37aaf47f4')
        self.assertEqual(len(C.IMAGE['rootfs']['Layers']),33); self.assertEqual(C.IMAGE['archive_observation']['member_count'],71)
        with self.assertRaisesRegex(RuntimeError,'smoke_artifact'):C.canonical_smoke(b'PUBLIC NOT ACTUAL ARTIFACT')
    def test_strict_json_types_duplicate_nonfinite_and_private_output(self):
        for raw in (b'{"x":1,"x":2}',b'{"x":NaN}',b'{"x":1e999}'):
            with self.subTest(raw=raw),self.assertRaises((RuntimeError,ValueError)):C.document(raw)
        self.assertFalse(C.same(True,1)); self.assertFalse(C.same([1],[True]))
        with self.assertRaisesRegex(RuntimeError,'private_output'):C.encoded({'nested':{'Env':['PUBLIC_SYNTHETIC']}})
    def test_complete_normal_render_configuration_and_generated_network_accounting(self):
        f = fixture(); C.rendered_delta(f.old,f.new,('app',),C.IMAGE['image_id'],{})
        C.configuration_parity(f.old,f.new,'app',f.old_row,f.stopped,f.old_image,f.new_image,f.scope,f.paths,[*f.paths,f.override],{},stopped=True)
        C.configuration_parity(f.old,f.new,'app',f.old_row,f.active,f.old_image,f.new_image,f.scope,f.paths,[*f.paths,f.override],{})
        reverse = deepcopy(f.active); reverse['config']['Env'].reverse()
        C.configuration_parity(f.old,f.new,'app',f.old_row,reverse,f.old_image,f.new_image,f.scope,f.paths,[*f.paths,f.override],{})
    def test_creation_no_dependencies_is_explicit_exact_empty_and_keeps_complete_model(self):
        f=fixture(); deps={'migration':{'condition':'service_completed_successfully'},'redis':{'condition':'service_started'}}
        f.old['services']['app']['depends_on']=deepcopy(deps)
        with self.assertRaisesRegex(RuntimeError,'compose_labels'):
            C.normal_baseline(f.old,'app',f.old_row,f.old_image,f.scope,f.paths)
        C.normal_baseline(f.old,'app',f.old_row,f.old_image,f.scope,f.paths,creation_no_dependencies=True)
        self.assertEqual(f.old['services']['app']['depends_on'],deps)
        labels=f.old_row['config']['Labels']; key='com.docker.compose.depends_on'
        exact='migration:service_completed_successfully:false,redis:service_started:false'
        labels[key]=exact
        C.normal_baseline(f.old,'app',f.old_row,f.old_image,f.scope,f.paths)
        for value in (exact,'redis:service_started:false','foreign:service_started:false',False,0,None):
            labels[key]=value
            with self.subTest(value=value),self.assertRaises(RuntimeError):
                C.normal_baseline(f.old,'app',f.old_row,f.old_image,f.scope,f.paths,creation_no_dependencies=True)
        labels.pop(key)
        with self.assertRaisesRegex(RuntimeError,'compose_labels'):
            C.normal_baseline(f.old,'app',f.old_row,f.old_image,f.scope,f.paths,creation_no_dependencies=True)
        for value in (1,'true',None):
            labels[key]=''
            with self.subTest(mode=value),self.assertRaisesRegex(RuntimeError,'compose_labels'):
                C.normal_baseline(f.old,'app',f.old_row,f.old_image,f.scope,f.paths,creation_no_dependencies=value)

    def test_complete_configuration_parity_accounts_no_deps_without_changing_dependencies(self):
        f=fixture(); deps={'redis':{'condition':'service_started'}}
        for model in (f.old,f.new):model['services']['app']['depends_on']=deepcopy(deps)
        C.configuration_parity(f.old,f.new,'app',f.old_row,f.active,f.old_image,f.new_image,f.scope,f.paths,[*f.paths,f.override],{})
        self.assertEqual(f.old['services']['app']['depends_on'],deps)
        self.assertEqual(f.new['services']['app']['depends_on'],deps)
        f.active['config']['Labels']['com.docker.compose.depends_on']='foreign:service_started:false'
        with self.assertRaisesRegex(RuntimeError,'compose_labels'):
            C.configuration_parity(f.old,f.new,'app',f.old_row,f.active,f.old_image,f.new_image,f.scope,f.paths,[*f.paths,f.override],{})

    def test_independently_admitted_runtime_restart_preserves_historical_model_and_other_guards(self):
        f=fixture(); f.old['services']['app']['restart']='no';original=deepcopy(f.old)
        policy={'Name':'always','MaximumRetryCount':0}
        with self.assertRaisesRegex(RuntimeError,'normal_restart'):
            C.normal_baseline(f.old,'app',f.old_row,f.old_image,f.scope,f.paths)
        C.normal_baseline(f.old,'app',f.old_row,f.old_image,f.scope,f.paths,runtime_restart_policy=policy)
        self.assertEqual(f.old,original);self.assertEqual(policy,{'Name':'always','MaximumRetryCount':0})
        for bad in ({'Name':'always','MaximumRetryCount':False},{'Name':'always','MaximumRetryCount':1},
                {'Name':'no','MaximumRetryCount':0},{'Name':'always','MaximumRetryCount':0,'extra':0},'always',True):
            with self.subTest(policy=bad),self.assertRaisesRegex(RuntimeError,'normal_restart'):
                C.normal_baseline(f.old,'app',f.old_row,f.old_image,f.scope,f.paths,runtime_restart_policy=bad)
        for field,value in (('Cmd',['python','changed.py']),('Env',['PUBLIC_UNEXPECTED=1'])):
            changed=deepcopy(f.old_row);changed['config'][field]=value
            with self.subTest(field=field),self.assertRaises(RuntimeError):
                C.normal_baseline(f.old,'app',changed,f.old_image,f.scope,f.paths,runtime_restart_policy=policy)
        f.old['services']['app']['restart']='on-failure:3'
        with self.assertRaisesRegex(RuntimeError,'normal_restart'):
            C.normal_baseline(f.old,'app',f.old_row,f.old_image,f.scope,f.paths,runtime_restart_policy=policy)

    def test_extra_hosts_normal_map_and_short_forms_preserve_exact_endpoint_and_reject_drift(self):
        f=fixture();spec=f.old['services']['app'];f.old_row['host']['ExtraHosts']=['coin.example:192.0.2.8']
        for form in ({'coin.example':'192.0.2.8'},{'coin.example':['192.0.2.8']},['coin.example=192.0.2.8'],['coin.example:192.0.2.8']):
            spec['extra_hosts']=deepcopy(form);before=deepcopy(spec)
            C.normal_baseline(f.old,'app',f.old_row,f.old_image,f.scope,f.paths)
            self.assertEqual(spec,before)
        for bad in (['coin.example=192.0.2.9'],['other.example=192.0.2.8'],['coin.example=192.0.2.8']*2,
                ['coin.example'],['=192.0.2.8'],['coin.example='],['coin.example=192.0.2.8=extra'],[True],True,
                {'coin.example':False},{'coin.example':[]},['coin.example=192.0.2.8\n']):
            spec['extra_hosts']=bad
            with self.subTest(form=bad),self.assertRaises(RuntimeError):
                C.normal_baseline(f.old,'app',f.old_row,f.old_image,f.scope,f.paths)
        f.old_row['host']['ExtraHosts']=['coin.example:2001:db8::1']
        for form in ({'coin.example':'2001:db8::1'},['coin.example=2001:db8::1'],['coin.example:2001:db8::1']):
            spec['extra_hosts']=form;C.normal_baseline(f.old,'app',f.old_row,f.old_image,f.scope,f.paths)
        # The same flattened Docker string must not hide a different hostname.
        spec['extra_hosts']={'coin.example:2001':'db8::1'}
        with self.assertRaisesRegex(RuntimeError,'normal_hosts'):
            C.normal_baseline(f.old,'app',f.old_row,f.old_image,f.scope,f.paths)

    def test_normal_pull_policy_never_is_modeled_and_all_other_values_refuse(self):
        f = fixture()
        spec = f.old['services']['app']; spec['pull_policy'] = 'never'
        C.normal_baseline(f.old,'app',f.old_row,f.old_image,f.scope,f.paths)
        self.assertEqual(spec['pull_policy'],'never')
        for value in ('always','build','missing','unknown',True,False,None,0):
            with self.subTest(value=value):
                spec['pull_policy'] = value
                with self.assertRaisesRegex(RuntimeError,'normal_pull_policy'):
                    C.normal_baseline(f.old,'app',f.old_row,f.old_image,f.scope,f.paths)

    def test_stop_timeout_requires_exact_typed_image_default_and_no_unmodeled_compose_feature(self):
        f = fixture()
        for timeout in (None,0,30):
            image=deepcopy(f.old_image); value=deepcopy(f.old_row)
            if timeout is not None:image['Config']['StopTimeout']=timeout
            value['config']['StopTimeout']=timeout
            C.normal_baseline(f.old,'app',value,image,f.scope,f.paths)
            changed=deepcopy(value); changed['config']['StopTimeout']=31 if timeout==30 else 30
            with self.subTest(timeout=timeout),self.assertRaisesRegex(RuntimeError,'normal_execution'):
                C.normal_baseline(f.old,'app',changed,image,f.scope,f.paths)
        for timeout in (True,-1,'30'):
            image=deepcopy(f.old_image); image['Config']['StopTimeout']=timeout
            value=deepcopy(f.old_row); value['config']['StopTimeout']=timeout
            with self.subTest(invalid=timeout),self.assertRaisesRegex(RuntimeError,'normal_execution'):
                C.normal_baseline(f.old,'app',value,image,f.scope,f.paths)
        model=deepcopy(f.old); model['services']['app']['stop_grace_period']='30s'
        with self.assertRaisesRegex(RuntimeError,'normal_feature_scope'):
            C.normal_baseline(model,'app',f.old_row,f.old_image,f.scope,f.paths)
    def test_unmodeled_old_stop_timeout_refuses_real_site_admission_before_any_stop_or_create(self):
        control,now=self.control_schema(); f=fixture(); f.old_row['config']['StopTimeout']=30
        f.old['services']['sync_worker']=fixture('sync_worker').old['services']['sync_worker']
        owned=self.root/'public-site'; owned.mkdir(mode=0o700)
        marker=self.file('public-site-marker',b'PUBLIC OPAQUE MARKER')
        control['sites']['web']['fences']['marker']=S.identity(marker.stat())
        scope=control['sites']['web']; mapped={}
        for index,proof in enumerate(scope['compose_files']):
            raw=b'# PUBLIC COMPOSE FIXTURE\n'; path=self.file('public-compose-'+str(index)+'.yml',raw)
            proof.update(bytes=len(raw),sha256=sha256(raw).hexdigest()); mapped[proof['path']]=path
        paths=[r['path'] for r in scope['compose_files']]
        scope['roles']['app']={'container_id':f.old_row['id'],'image_id':f.old_row['image'],
            'started':f.old_row['started'],'restarts':f.old_row['restarts'],'pid':f.old_row['pid']}
        f.old_row['config']['Labels']['com.docker.compose.project.config_files']=','.join(paths)
        real_held=S.Held
        def held(path,*args,**kwargs):return real_held(mapped.get(str(path),path),*args,**kwargs)
        with patch.multiple(S,ROOTS={'web':owned,'bot':owned},MARKER=marker,Held=held), \
                patch.object(S,'HeldPublic',return_value=SimpleNamespace(raw=scope['machine_id'].encode())), \
                patch.object(C,'evidence',return_value={site:C.IMAGE['image_id'] for site in C.SITES}), \
                patch.object(S.Site,'version_check'),patch.object(S.Site,'inventory',return_value={f.old_row['id']:f.old_row}), \
                patch.object(S.Site,'render',return_value=f.old),patch.object(S.Site,'image',return_value=f.old_image), \
                patch.object(S.Site,'functional') as functional,patch.object(S.Site,'stop') as stop, \
                patch.object(S.Site,'create') as create,patch.object(S.Site,'start') as start:
            with self.assertRaisesRegex(RuntimeError,'normal_execution'):
                with ExitStack() as stack:S.Site('web',control,stack)
        stop.assert_not_called();create.assert_not_called();start.assert_not_called();functional.assert_not_called()
        self.assertFalse((owned/'activation.guard').exists()); self.assertFalse((owned/'attempts').exists())
    def test_each_business_host_mount_label_command_and_network_tamper_refuses(self):
        f = fixture()
        cases = [lambda r:r['config']['Env'].append('PUBLIC_EXTRA=x'),lambda r:r['config'].update(Cmd=['python','other.py']),
            lambda r:r['config'].update(User='0'),lambda r:r['host'].update(Privileged=True),
            lambda r:r['host'].update(FutureNonzero=1),lambda r:r['host'].update(CapAdd=['DAC_OVERRIDE']),
            lambda r:r['config']['Labels'].update({'business':'changed'}),lambda r:r['mounts'].append({'Destination':'/app'}),
            lambda r:r['networks']['fixture_net'].update(Gateway='172.19.0.1'),
            lambda r:r['networks']['fixture_net'].update(IPAddress='192.0.2.1'),
            lambda r:r['networks']['fixture_net'].update(Aliases=['other']),lambda r:r['config'].update(Unexpected=False)]
        for change in cases:
            value = deepcopy(f.active); change(value)
            with self.subTest(change=cases.index(change)),self.assertRaises(RuntimeError):
                C.configuration_parity(f.old,f.new,'app',f.old_row,value,f.old_image,f.new_image,f.scope,f.paths,[*f.paths,f.override],{})
        changed = deepcopy(f.new); changed['services']['app']['environment']['PUBLIC_BUSINESS_VALUE'] = 'changed'
        with self.assertRaisesRegex(RuntimeError,'normal_render_delta'):C.rendered_delta(f.old,changed,('app',),C.IMAGE['image_id'],{})
    def test_only_measured_python_defaults_and_provenance_may_change(self):
        f = fixture(); C.image_defaults(f.old_image['Config'],f.new_image['Config'],f.scope['public_image_env_delta'])
        for key in ('PATH','LANG','GPG_KEY'):
            bad = deepcopy(f.new_image['Config']); bad['Env'] = [key+'=PUBLIC_CHANGED' if v.startswith(key+'=') else v for v in bad['Env']]
            with self.subTest(key=key),self.assertRaises(RuntimeError):C.image_defaults(f.old_image['Config'],bad,f.scope['public_image_env_delta'])
        with self.assertRaises(RuntimeError):C.image_defaults(f.old_image['Config'],f.new_image['Config'],{})
    def test_ordered_stop_create_verify_start_and_complete_config_check(self):
        b = FakeBackend(); result = S.replace(b,'app')
        calls = [x[0] for x in b.calls]; self.assertLess(calls.index('stop'),calls.index('create')); self.assertLess(calls.index('create'),calls.index('start'))
        self.assertEqual(result['status'],'active'); self.assertEqual(b.completed,['app'])
        with self.assertRaisesRegex(RuntimeError,'role_order'):S.replace(b,'app')
    def test_bad_stopped_config_never_starts_and_known_owned_rollback_restores_old(self):
        b = FakeBackend('config_tamper')
        with self.assertRaises(S.KnownFailure):S.replace(b,'app')
        self.assertFalse(any(x[0] == 'start' for x in b.calls))
        self.assertEqual(S.rollback(b,'app')['status'],'rolled_back')
        self.assertEqual(b.actual[b.role_ids['app']]['image'],b.f.old_image['Id'])
    def test_functional_failure_rolls_back_only_measured_owned_cid(self):
        b = FakeBackend('functional_failure')
        with self.assertRaises(S.KnownFailure):S.replace(b,'app')
        self.assertEqual(S.rollback(b,'app')['status'],'rolled_back')
        self.assertEqual([x[1] for x in b.calls if x[0] == 'stop'],['1'*64,'2'*64])
    def test_unknown_stop_create_start_and_other_drift_never_grant_rollback(self):
        for case in ('stop_unknown','create_unknown','start_unknown','other_drift'):
            b = FakeBackend(case)
            with self.subTest(case=case),self.assertRaises(S.Unknown):S.replace(b,'app')
            self.assertFalse(any(x[0] == 'create' and x[1] is False for x in b.calls))
    def test_completed_role_rollback_preserves_unrelated_containers(self):
        b = FakeBackend(); S.replace(b,'app'); S.rollback(b,'app')
        self.assertEqual(b.completed,[]); self.assertEqual(b.actual['9'*64],{'id':'9'*64,'public':'unrelated'})
    def test_metadata_only_locks_and_aliases_never_read_permanent_or_secret_payload(self):
        source = self.file('SOURCE',b''); marker = self.file('MARKER',b'PUBLIC OPAQUE MARKER')
        secret = self.file('owned-secret-reference',b'PUBLIC SYNTHETIC ENV NEVER READ'); link = self.root/'normal.env'; link.symlink_to(secret.name)
        proof = {'path':str(link),'target':str(secret),'identity':S.identity(secret.stat()),
            'aliases':[{'path':str(link),'identity':S.identity(link.lstat())}]}
        with patch.object(S.os,'pread',side_effect=AssertionError('opaque payload read denied')):
            S.Held(source,self.stack,expected=S.identity(source.stat()),opaque=True,lock=True)
            held = S.Held(marker,self.stack,expected=S.identity(marker.stat()),opaque=True,lock=True)
            with self.assertRaises(RuntimeError):
                with ExitStack() as local:
                    reference = S.OpaqueReference(proof,local); reference.check(); held.check()
                    secret.chmod(0o644); reference.check()
    def test_real_owned_guard_exclusive_binding_and_only_owned_unlink(self):
        control = {'nonce':'a'*32,'controller_commit':'b'*40}
        path = self.root/'owned.guard'; guard = S.Guard(path,control,self.stack)
        with self.assertRaises(FileExistsError):S.Guard(path,control,self.stack)
        guard.close(); self.assertFalse(path.exists()); guard.check()
    def test_invalid_leaf_metadata_refuses_before_open_or_payload_read(self):
        path = self.file('preopen-public')
        wrong_owner = self.file('foreign-owner')
        os.chown(wrong_owner,1,1)
        self.addCleanup(os.chown,wrong_owner,0,0)
        linked = self.file('hardlinked-public'); os.link(linked,self.root/'other-link')
        fifo = self.root/'public-fifo'; os.mkfifo(fifo,0o600)
        path.chmod(0o666)
        oversized = self.file('oversized-public',b'PUBLIC')
        for leaf,kwargs in ((path,{}),(wrong_owner,{}),(linked,{}),(fifo,{}),(oversized,{'maximum':2})):
            with self.subTest(leaf=leaf.name), patch.object(S.os,'open',side_effect=AssertionError('leaf must not open')), \
                    patch.object(S.os,'pread',side_effect=AssertionError('payload must not read')), self.assertRaises(RuntimeError):
                with ExitStack() as local:S.Held(leaf,local,**kwargs)
    def test_stale_expected_regular_inode_refuses_before_open(self):
        path = self.file('expected-public'); expected = S.identity(path.stat())
        path.rename(self.root/'original-public'); self.file('expected-public')
        with patch.object(S.os,'open',side_effect=AssertionError('stale inode must not open')), \
                patch.object(S.os,'pread',side_effect=AssertionError('payload must not read')), self.assertRaisesRegex(RuntimeError,'held_identity'):
            with ExitStack() as local:S.Held(path,local,expected=expected)
    def test_regular_replacement_between_lstat_and_open_refuses_before_pread(self):
        path = self.file('racing-public'); expected = S.identity(path.stat()); actual_open = S.os.open
        def replacing(value,*args,**kwargs):
            if Path(value) == path:
                path.rename(self.root/'retained-public'); self.file('racing-public',b'PUBLIC REPLACEMENT')
            return actual_open(value,*args,**kwargs)
        with patch.object(S.os,'open',side_effect=replacing), \
                patch.object(S.os,'pread',side_effect=AssertionError('replacement payload must not read')), \
                self.assertRaisesRegex(RuntimeError,'held_identity'):
            with ExitStack() as local:S.Held(path,local,expected=expected)
    def test_primary_error_survives_final_held_metadata_fault(self):
        path = self.file('held-public')
        with self.assertRaisesRegex(RuntimeError,'PRIMARY_PUBLIC'):
            with ExitStack() as stack:
                held = S.Held(path,stack); path.chmod(0o644); raise RuntimeError('PRIMARY_PUBLIC')
        self.assertIsNotNone(held)
    def test_all_five_order_reverse_known_rollback_and_unknown_stops(self):
        calls = []
        class Backend:
            def __init__(self,site,fail=None):self.site,self.fail = site,fail
            def replace(self,role):
                calls.append(('replace',self.site,role))
                if (self.site,role) == self.fail:raise S.KnownFailure('PUBLIC_KNOWN')
            def rollback(self,role):calls.append(('rollback',self.site,role))
            def final(self):calls.append(('final',self.site))
            def recovery(self):calls.append(('recovery',self.site))
        self.assertEqual(K.sequence({site:Backend(site) for site in C.SITES}),'activated')
        self.assertEqual([x[1:] for x in calls if x[0] == 'replace'],list(C.ROLE_ORDER)); calls.clear()
        self.assertEqual(K.sequence({site:Backend(site,('bot','bot')) for site in C.SITES}),'rolled_back')
        self.assertEqual([x[1:] for x in calls if x[0] == 'rollback'],[('bot','bot'),('bot','app'),('web','sync_worker'),('web','app')])
        calls.clear(); values = {site:Backend(site) for site in C.SITES}
        values['web'].replace = lambda role:(_ for _ in ()).throw(S.Unknown('PUBLIC_UNKNOWN'))
        with self.assertRaises(S.Unknown):K.sequence(values)
        self.assertFalse(calls)
    def test_peer_frames_reject_type_nonce_field_and_status_tampering(self):
        control = {'nonce':'a'*32}; good = K.frame(control,1,'active','app','b'*64); K.admit(good,control,1)
        for change in ({'index':True},{'nonce':'c'*32},{'status':'invented'},{'Env':[]},{'container_id':'short'}):
            with self.subTest(change=change),self.assertRaises(RuntimeError):K.admit({**good,**change},control,1)
        with self.assertRaises(S.Unknown):K.admit(K.frame(control,1,'refused_or_unknown'),control,1)
    def test_image_only_override_complete_prefix_and_no_historical_entrypoint(self):
        fake = object.__new__(S.Site); fake.scope = {'workdir':'/public/normal','project':'public',
            'env_files':[{'path':'/public/normal.env','cli':True},{'path':'/public/retained.env','cli':True},{'path':'/public/service.env','cli':False}]}
        fake.paths = ['/public/base.yml','/public/prod.yml','/public/retained-rollback.yml']; fake.override = Path('/public/one-image.yml')
        self.assertEqual(fake.compose(True),['/usr/bin/docker','compose','--project-directory','/public/normal','--project-name','public',
            '--env-file','/public/normal.env','--env-file','/public/retained.env','--file','/public/base.yml','--file','/public/prod.yml',
            '--file','/public/retained-rollback.yml','--file','/public/one-image.yml'])
        for name in C.SOURCE_FILES:
            text = (ROOT/'deploy/maintenance'/name).read_text(); tree = ast.parse(text)
            imports = [n.module or '' for n in ast.walk(tree) if isinstance(n,ast.ImportFrom)]
            self.assertFalse(any('security_consumer' in n or 'private_primary' in n or 'cow' in n for n in imports))
            self.assertNotIn("'--hash'",text); self.assertNotIn('os.environ[',text)
    def test_command_cleanup_attempts_every_fd_and_preserves_primary_failure(self):
        real_close = os.close; closed = []; first = True
        def close(fd):
            nonlocal first
            closed.append(fd); real_close(fd)
            if first:first = False; raise OSError('PUBLIC_CLOSE_FAULT')
        with patch.object(S.os,'close',side_effect=close):
            with self.assertRaisesRegex(RuntimeError,'command_unproven'):
                S.command(['/public/never-executed'],time.monotonic()+1,maximum=64)
        self.assertEqual(len(closed),2)
    def test_terminal_receipt_is_durable_before_guard_close_and_unknown_keeps_guard(self):
        backend = object.__new__(S.Site); backend.control = {'nonce':'a'*32}; backend.site = 'web'
        backend.completed = ['app','sync_worker']; backend.role_ids = {'app':'1'*64,'sync_worker':'2'*64}
        backend.current = {cid:{'image':C.IMAGE['image_id']} for cid in backend.role_ids.values()}
        backend.journal = S.Journal(self.root/'attempt',backend.control); backend.terminal = None
        backend.final = lambda:{'security_accepted':False}; backend.check = lambda:None
        backend.guard = S.Guard(self.root/'guard',{'nonce':'a'*32,'controller_commit':'b'*40},self.stack)
        with self.assertRaisesRegex(RuntimeError,'terminal_not_prepared'):backend.close()
        self.assertTrue((self.root/'guard').exists())
        pin = backend.prepare_terminal('activated'); self.assertTrue(C.hex64(pin))
        self.assertEqual(sha256((backend.journal.path/'terminal.json').read_bytes()).hexdigest(),pin)
        backend.close(); self.assertFalse((self.root/'guard').exists())
    def test_never_started_proof_cannot_be_reused_after_a_start_request(self):
        b = FakeBackend('config_tamper')
        with self.assertRaises(S.KnownFailure):S.replace(b,'app')
        b.transactions['app']['start_requested'] = True
        with self.assertRaises(S.Unknown):S.rollback(b,'app')
        self.assertFalse(any(x[0] == 'create' and x[1] is False for x in b.calls))
    def public_load_receipt(self,site='web'):
        identities = {C.IMAGE['image_id']:{'kind':'Docker_config_identity'},
            C.IMAGE['archive_observation']['manifest_id']:{'kind':'OCI_manifest_to_config',
                'media_type':'application/vnd.oci.image.manifest.v1+json','size':5254},
            'sha256:'+C.IMAGE['archive_observation']['index_sha256']:{'kind':'OCI_index_to_manifest_to_config',
                'media_type':'application/vnd.oci.image.index.v1+json','size':241}}
        return {'schema':'canonical_app_preloaded_image/2','operation':'security-canonical-app-candidate-images-20261025-01',
            'site':'native' if site == 'bot' else 'web','host_machine_id':'a'*32,
            'host':'65.109.216.187' if site == 'bot' else '65.109.220.59',
            'catalog_sha256':C.LOAD_CATALOG,'controller_sha256':C.LOAD_CONTROLLER,'engine_sha256':C.LOAD_ENGINE,
            'health_policy_sha256':C.LOAD_HEALTH_POLICY,'health_adapter_sha256':C.LOAD_HEALTH_ADAPTER,
            'ci_run_id':C.IMAGE['run_id'],'image_artifact_sha256':C.IMAGE['image_zip_sha256'],
            'actual_ci_run_id':C.SMOKE['run_id'],'actual_ci_artifact_sha256':C.SMOKE['zip_sha256'],
            'role':'app','profile':'canonical_app','candidate_config_id':C.IMAGE['image_id'],
            'candidate_local_image_id':C.IMAGE['image_id'],'rootfs':C.IMAGE['rootfs']['Layers'],
            'source_archive_bytes':C.IMAGE['archive_observation']['archive_bytes'],
            'source_archive_sha256':C.IMAGE['archive_observation']['archive_sha256'],
            'source_identities':identities,'source_admission_sha256':'b'*64,'operation_sha256':'c'*64,'nonce':'d'*32,
            'archive_and_config_association_verified':True,'whole_typed_config_compared_in_ram':True,
            'physical_graph_closed':True,'runtime_policy_preserved':True,'serving_app_roles_unchanged':True,
            'serving_roles_activated':[],'serving_validation':'NOT_TESTED','configuration_values_disclosed':False,
            'runtime_health_issues_open':True,'capture_issue_open':True,'security_accepted':False,'runtime_health_observations':[]}
    def test_host_load_requires_actual_new_receipt_contract_and_measured_identity(self):
        for pin,name in ((C.LOAD_CONTROLLER,'canonical_app_candidate_images_v2.py'),
            (C.LOAD_HEALTH_POLICY,'runtime_health_observation_policy.py'),(C.LOAD_HEALTH_ADAPTER,'market_candidate_images_v3.py')):
            self.assertEqual(pin,sha256((ROOT/'deploy/maintenance'/name).read_bytes()).hexdigest())
        for site in C.SITES:
            value = self.public_load_receipt(site); self.assertEqual(C.host_load(value,site,{'machine_id':'a'*32}),C.IMAGE['image_id'])
            for change in ({'schema':'market_preloaded_image/1'},{'actual_ci_run_id':0},{'physical_graph_closed':False},
                {'schema':'canonical_app_preloaded_image/1'},
                {'controller_sha256':'43029071a8e67d55d16a31b413d861a6f5ad446ad1f8e8723d4795e13044b4ae'},
                {'health_policy_sha256':'0'*64},{'health_adapter_sha256':'0'*64},
                {'controller_sha256':'0'*64},{'candidate_local_image_id':'sha256:'+'0'*64},
                {'rootfs':list(reversed(value['rootfs']))},{'security_accepted':True},{'site':'other'}):
                with self.subTest(site=site,change=change),self.assertRaises(RuntimeError):C.host_load({**value,**change},site,{'machine_id':'a'*32})
    def resource_load_receipt(self,site):
        value = self.public_load_receipt(site)
        value.update(schema='canonical_app_preloaded_image/3',controller_sha256=C.LOAD_V3_CONTROLLER,
            resource_contract_sha256=C.LOAD_RESOURCE_CONTRACT)
        if site == 'bot':value['native_resource_policy_unchanged'] = True
        else:value.update(resource_ram_floor_bytes=3021996032,aggregate_client_memory_max_bytes=536870912,
            metadata_stream_allowance_bytes=268435456,actual_tmpfs_payload_write_bytes=0,
            daemon_heap_quota_claimed=False,resource_contract_accepted=False)
        return value
    def test_v3_load_resource_tuple_preserves_exact_v2_and_both_site_policies(self):
        for name,pin in (('canonical_app_candidate_images_v3.py',C.LOAD_V3_CONTROLLER),
            ('canonical_app_resource_contract.py',C.LOAD_RESOURCE_SOURCE)):
            self.assertEqual(sha256((ROOT/'deploy/maintenance'/name).read_bytes()).hexdigest(),pin)
        spec = importlib.util.spec_from_file_location('_public_resource_contract',ROOT/'deploy/maintenance/canonical_app_resource_contract.py')
        resource = importlib.util.module_from_spec(spec); spec.loader.exec_module(resource)
        self.assertEqual(resource.CONTRACT_SHA,C.LOAD_RESOURCE_CONTRACT)
        for site in C.SITES:
            old = self.public_load_receipt(site); new = self.resource_load_receipt(site); before = deepcopy(new)
            for value in (old,new):self.assertEqual(C.host_load(value,site,{'machine_id':'a'*32}),C.IMAGE['image_id'])
            self.assertEqual(new,before)
            for change in ({'schema':'canonical_app_preloaded_image/2'},{'controller_sha256':C.LOAD_CONTROLLER},
                {'schema':'canonical_app_preloaded_image/4'},{'resource_contract_sha256':'0'*64},
                {'source_archive_sha256':'0'*64},{'source_archive_bytes':True},{'whole_typed_config_compared_in_ram':False},
                {'source_identities':{}},{'actual_ci_artifact_sha256':'0'*64},{'rootfs':list(reversed(new['rootfs']))},
                {'candidate_local_image_id':'sha256:'+'0'*64},{'security_accepted':True}):
                with self.subTest(site=site,change=change),self.assertRaises(RuntimeError):C.host_load({**new,**change},site,{'machine_id':'a'*32})
            missing = deepcopy(new); missing.pop('resource_contract_sha256')
            with self.assertRaises(RuntimeError):C.host_load(missing,site,{'machine_id':'a'*32})
    def test_v3_resource_scalars_are_typed_and_cannot_claim_native_or_web_waivers(self):
        web = self.resource_load_receipt('web')
        for key,bad in (('resource_ram_floor_bytes',True),('aggregate_client_memory_max_bytes',536870911),
            ('metadata_stream_allowance_bytes',False),('actual_tmpfs_payload_write_bytes',False),
            ('actual_tmpfs_payload_write_bytes',1),('daemon_heap_quota_claimed',True),('resource_contract_accepted',True),
            ('native_resource_policy_unchanged',True)):
            with self.subTest(key=key),self.assertRaises(RuntimeError):C.host_load({**web,key:bad},'web',{'machine_id':'a'*32})
        for key in ('resource_ram_floor_bytes','aggregate_client_memory_max_bytes','metadata_stream_allowance_bytes',
            'actual_tmpfs_payload_write_bytes','daemon_heap_quota_claimed','resource_contract_accepted'):
            missing = deepcopy(web); missing.pop(key)
            with self.subTest(missing=key),self.assertRaises(RuntimeError):C.host_load(missing,'web',{'machine_id':'a'*32})
        native = self.resource_load_receipt('bot')
        for change in ({'native_resource_policy_unchanged':False},{'native_resource_policy_unchanged':1},
            {'resource_ram_floor_bytes':3021996032},{'daemon_heap_quota_claimed':False}):
            with self.subTest(change=change),self.assertRaises(RuntimeError):C.host_load({**native,**change},'bot',{'machine_id':'a'*32})
        missing = deepcopy(native); missing.pop('native_resource_policy_unchanged')
        with self.assertRaises(RuntimeError):C.host_load(missing,'bot',{'machine_id':'a'*32})
    def test_v3_health_observations_remain_exact_current_load_history(self):
        value = self.resource_load_receipt('bot')
        row = {'schema':'runtime_image_store_health_observation/1','operation':value['operation'],
            'operation_sha256':value['operation_sha256'],'health_policy_sha256':C.LOAD_HEALTH_POLICY,'nonce':value['nonce'],
            'site':'bot','container_id':'1'*64,'image_id':'sha256:'+'2'*64,'pid':123,'started':'2026-10-25T12:00:00Z',
            'restarts':0,'baseline_health':'healthy','observed_health':'unhealthy','observed_at':100,
            'runtime_health_issues_open':True,'security_accepted':False}
        value['runtime_health_observations'] = [row]
        self.assertEqual(C.host_load(value,'bot',{'machine_id':'a'*32}),C.IMAGE['image_id'])
        for change in ({'nonce':'e'*32},{'operation_sha256':'f'*64},{'pid':True},{'observed_health':'starting'},
            {'site':'web'},{'security_accepted':True}):
            changed = deepcopy(value); changed['runtime_health_observations'][0].update(change)
            with self.subTest(change=change),self.assertRaises(RuntimeError):C.host_load(changed,'bot',{'machine_id':'a'*32})
    def test_image_store_observations_remain_bound_history_with_no_serving_health_waiver(self):
        value = self.public_load_receipt('bot')
        row = {'schema':'runtime_image_store_health_observation/1','operation':value['operation'],
            'operation_sha256':value['operation_sha256'],'health_policy_sha256':C.LOAD_HEALTH_POLICY,'nonce':value['nonce'],
            'site':'bot','container_id':'1'*64,'image_id':'sha256:'+'2'*64,'pid':123,'started':'2026-10-25T12:00:00Z',
            'restarts':0,'baseline_health':'healthy','observed_health':'unhealthy','observed_at':100,
            'runtime_health_issues_open':True,'security_accepted':False}
        value['runtime_health_observations'] = [row]
        value['peer_runtime_health_observations'] = [{**row,'site':'web','container_id':'3'*64}]
        before = deepcopy(value)
        self.assertEqual(C.host_load(value,'bot',{'machine_id':'a'*32}),C.IMAGE['image_id']); self.assertEqual(value,before)
        for change in ({'nonce':'e'*32},{'operation_sha256':'f'*64},{'site':'web'},{'health_policy_sha256':'0'*64},
            {'pid':True},{'restarts':False},{'observed_at':True},{'started':'2026-99-25T12:00:00Z'},
            {'observed_health':'starting'},{'observed_health':'healthy'},{'Env':['PRIVATE must refuse']},
            {'container_id':'6a16c8a1b4cdab3b4bcd2a0d4d4998ced7a455cc6c364255e5e0e3dacd9c2752'},
            {'security_accepted':True}):
            changed = deepcopy(value); changed['runtime_health_observations'][0].update(change)
            with self.subTest(change=change),self.assertRaises(RuntimeError):C.host_load(changed,'bot',{'machine_id':'a'*32})
        for rows in (None,[row,row],[{**row,'container_id':'2'*64},row],[row]*129):
            changed = deepcopy(value); changed['runtime_health_observations'] = rows
            with self.subTest(rows=type(rows).__name__),self.assertRaises(RuntimeError):C.host_load(changed,'bot',{'machine_id':'a'*32})
        web = self.public_load_receipt('web'); web['peer_runtime_health_observations'] = [{**row,'site':'web'}]
        with self.assertRaises(RuntimeError):C.host_load(web,'web',{'machine_id':'a'*32})
        # The normal serving health requirement remains strict even though the
        # historical image-store receipt truthfully records an unhealthy role.
        f = fixture(); f.old_row['health'] = 'unhealthy'; backend = S.Site.__new__(S.Site)
        backend.deadline = time.monotonic()-1; backend.inspect = lambda cid:deepcopy(f.old_row)
        backend.singleton = lambda *a,**kw:None; backend.units_check = lambda:None
        with self.assertRaisesRegex(RuntimeError,'normal_role_unready'):backend.functional('app',f.old_row)
    def test_fresh_backup_and_schema_evidence_cannot_be_replaced_by_missing_or_generic_proof(self):
        # Unit schema fixtures are not actual production restore/LOAD receipts.
        # The real fixed CI ZIP is intentionally mocked only in this pure routing test.
        control = {'sites':{s:{'machine_id':'a'*32} for s in C.SITES},'gates':{}}
        base = {'source_commit':C.IMAGE['source_commit'],'image_config_id':C.IMAGE['image_id'],
            'machine_ids':{s:'a'*32 for s in C.SITES},'completed_at':100,'security_accepted':False}
        backups = {**base,'schema':'canonical_app_backup_restore/1','sites':{s:{'database_backup_complete':True,
            'uploads_backup_complete':True,'isolated_database_restore_passed':True,'isolated_uploads_restore_passed':True,
            'durable_receipt_verified':True} for s in C.SITES}}
        schema = {**base,'schema':'canonical_app_schema_compatibility/1','init_db_create_all_acknowledged':True,
            'mandatory_channel_rollout_acknowledged':True,'candidate_on_restored_database_passed':True,
            'old_image_on_same_restored_database_passed':True,'rollback_data_compatible':True,'implicit_migration_permitted':False,
            'database_heads':{s:'ff6c7d8e9f01' for s in C.SITES}}
        bodies = {'ci':b'PUBLIC UNIT ROUTING ONLY','backup':C.encoded(backups),'schema':C.encoded(schema)}
        for site in C.SITES:bodies[site] = C.encoded(self.public_load_receipt(site))
        binding = lambda key:{'name':key,'bytes':len(bodies[key]),'sha256':sha256(bodies[key]).hexdigest()}
        control['gates'] = {'canonical_smoke':binding('ci'),'host_loads':{s:binding(s) for s in C.SITES},
            'backup_restore':binding('backup'),'schema_compatibility':binding('schema')}
        with patch.object(C,'canonical_smoke',side_effect=lambda raw:self.assertEqual(raw,bodies['ci'])):
            values = C.evidence(control,lambda value:bodies[value['name']],100); self.assertEqual(set(values),set(C.SITES))
            for key,bad in (('backup',{**backups,'completed_at':-20000}),('schema',{**schema,'rollback_data_compatible':False}),
                ('schema',{**schema,'implicit_migration_permitted':True}),('schema',{**schema,'old_image_on_same_restored_database_passed':False})):
                original = bodies[key]; bodies[key] = C.encoded(bad)
                with self.subTest(key=key,bad=bad),self.assertRaises(RuntimeError):C.evidence(control,lambda value:bodies[value['name']],100)
                bodies[key] = original
    def test_reader_units_and_arbitrary_other_container_changes_are_not_normalized(self):
        self.assertEqual(len(C.READER_UNITS),8); self.assertIn('coin-private-shadow-legacy-estimator-bridge.timer',C.READER_UNITS)
        b = FakeBackend(); changed = deepcopy(b.current); changed['9'*64]['public'] = 'changed'
        with self.assertRaises(RuntimeError):C.other_containers(b.current,changed,'1'*64)
        f = fixture(); changed = deepcopy(f.active); changed['networks']['fixture_net']['DriverOpts'] = {'other':'changed'}
        with self.assertRaises(RuntimeError):C.network_parity(f.old,'app',f.old_row,changed,stopped=False)
    def control_schema(self):
        now = int(time.time()); control = C.unarmed_control(); env = self.file('synthetic-normal.env',b'PUBLIC_UNIT_ONLY=1\n')
        control.update(nonce='a'*32,controller_commit='b'*40,issued_at=now-1,forward_deadline=now+600,recovery_deadline=now+900,
            source_files={name:{'bytes':1,'sha256':'c'*64} for name in C.SOURCE_FILES})
        for index,site in enumerate(C.SITES):
            fixed = C.SITES[site]; mode = '/docker-compose.yml' if site == 'bot' else '/docker-compose.iran.yml'
            control['sites'][site] = {'machine_id':('d' if site == 'bot' else 'e')*32,'project':fixed['project'],'workdir':fixed['workdir'],
                'compose_files':[{'path':fixed['workdir']+mode,'bytes':1,'sha256':'f'*64},
                    {'path':fixed['workdir']+'/public-normal.yml','bytes':1,'sha256':'f'*64}],
                'env_files':[{'path':str(env),'target':str(env),'identity':S.identity(env.stat()),'aliases':[],'cli':True}],
                'environment_file_label':None,'compose_version':'2.40.3','docker_version':'28.5.1',
                'fences':{name:[1,2,0o100600,0,0,1,0,1,1] for name in (('marker','source') if site == 'bot' else ('marker',))},
                'roles':{role:{'container_id':str(i+1)*64,'image_id':'sha256:'+'a'*64,'started':'2026-10-25T10:00:00Z',
                    'restarts':0,'pid':1234+i} for i,role in enumerate(fixed['roles'])},
                'reader_units':{unit:{'LoadState':'loaded','UnitFileState':'enabled','NeedDaemonReload':'no'} for unit in C.READER_UNITS} if site == 'bot' else {},
                'legacy_units':{'public-old-role.service':{'ActiveState':'inactive','MainPID':'0','ControlPID':'0',
                    'UnitFileState':'disabled','NeedDaemonReload':'no'}},'public_image_env_delta':{}}
        binding = lambda name:{'name':name,'bytes':1,'sha256':'0'*64}
        control['gates'].update(host_loads={s:binding(s+'.json') for s in C.SITES},backup_restore=binding('backup.json'),schema_compatibility=binding('schema.json'))
        control['sites']['bot']['fences']['source'] = deepcopy(C.SOURCE_ID)
        return control,now
    def test_control_schema_has_complete_prefix_roles_units_and_bounded_fresh_authority(self):
        control,now = self.control_schema(); C.validate_control(control,now)
        cases = [lambda x:x['sites']['bot']['reader_units'].pop(next(iter(C.READER_UNITS))),
            lambda x:x['sites']['web']['compose_files'].reverse(),lambda x:x['sites']['bot']['roles'].pop('bot'),
            lambda x:x['sites']['web']['legacy_units']['public-old-role.service'].update(MainPID='123'),
            lambda x:x['gates']['host_loads'].update(bot=None),lambda x:x.update(forward_deadline=now-1),
            lambda x:x.update(runtime_env_delta={'TELEGRAM_DELIVERY_WORKER_ENABLED':'true'}),
            lambda x:x.update(writer_change_permitted=True)]
        for change in cases:
            changed = deepcopy(control); change(changed)
            with self.subTest(case=cases.index(change)),self.assertRaises(RuntimeError):C.validate_control(changed,now)
        C.validate_control(control,control['forward_deadline']+1,recovery=True)
        with self.assertRaises(RuntimeError):C.validate_control(control,control['recovery_deadline'],recovery=True)
    def test_cold_source_and_control_are_held_before_runtime_site_construction(self):
        control,now = self.control_schema(); owned = self.root/'installation'; owned.mkdir(mode=0o700)
        (owned/'controls').mkdir(mode=0o700)
        for name in C.SOURCE_FILES:
            body = (ROOT/'deploy/maintenance'/name).read_bytes(); path = owned/name; path.write_bytes(body); path.chmod(0o600)
            control['source_files'][name] = {'bytes':len(body),'sha256':sha256(body).hexdigest()}
        path = owned/'controls'/(control['nonce']+'.json'); path.write_bytes(C.encoded(control)); path.chmod(0o600)
        with patch.multiple(K,ROOTS={'bot':owned,'web':owned},__file__=str(owned/'canonical_app_activation_coordinator.py'),C=C,S=S):
            with ExitStack() as stack:
                admitted,checks = K.load('bot',control['nonce'],stack); self.assertEqual(admitted,control)
                self.assertTrue(checks)
            target = owned/'canonical_app_activation_site.py'; target.write_bytes(target.read_bytes()+b'\n# PUBLIC TAMPER\n')
            with self.assertRaisesRegex(RuntimeError,'source_pin'):
                with ExitStack() as stack:K.load('bot',control['nonce'],stack)
    def test_original_source_public_yaml_and_public_stable_unit_policy_are_required(self):
        control,now = self.control_schema()
        changes = [lambda x:x['sites']['bot']['fences']['source'].__setitem__(1,1234567),
            lambda x:x['sites']['web']['compose_files'][1].update(path='/public/secret.env'),
            lambda x:x['sites']['bot']['reader_units'][next(iter(C.READER_UNITS))].update(Environment='PUBLIC_NEVER_A_UNIT_PROPERTY'),
            lambda x:x['sites']['bot']['reader_units'][next(iter(C.READER_UNITS))].update(ActiveState='inactive'),
            lambda x:x['sites']['web']['legacy_units']['public-old-role.service'].update(ExecStart='PUBLIC_NEVER_READ')]
        for change in changes:
            value = deepcopy(control); change(value)
            with self.subTest(case=changes.index(change)),self.assertRaises(RuntimeError):C.validate_control(value,now)
        # Stable Reader policy makes no claim about a scheduled oneshot's PID or active state.
        self.assertNotIn('MainPID',C.READER_POLICY_FIELDS); self.assertNotIn('ActiveState',C.READER_POLICY_FIELDS)
    def test_all_actual665_image_copy_targets_and_code_ancestors_cannot_be_source_mounted(self):
        dockerfile = (ROOT/'Dockerfile').read_bytes(); self.assertEqual(sha256(dockerfile).hexdigest(),C.DOCKERFILE_SHA)
        stage = False; copied = set()
        for line in dockerfile.decode().splitlines():
            if line.startswith('FROM '):stage = line.endswith(' AS runtime')
            if not stage or not line.startswith('COPY ') or '--from=' in line:continue
            source,destination = shlex.split(line)[-2:]
            target = ('/app/'+source.rstrip('/').split('/')[-1]) if destination in {'.','./'} else ('/app/'+destination[2:] if destination.startswith('./') else destination)
            copied.add(target.rstrip('/'))
        self.assertEqual(copied,set(C.IMAGE_SOURCE_PATHS))
        f = fixture()
        for target in [*sorted(copied),'/','/app','/usr/local/lib/python3.11','/app/bot/submodule.py']:
            model = deepcopy(f.old); model['services']['app']['volumes'] = [{'type':'bind','source':'/public/never-read','target':target}]
            with self.subTest(target=target),self.assertRaisesRegex(RuntimeError,'source_mount'):C.mounts_model(model,'app')
        for target in ('/app/.env','/app/uploads','/app/audit_trail','/app/runtime/coin-inference'):
            model = deepcopy(f.old); model['services']['app']['volumes'] = [{'type':'bind','source':'/public/normal-data','target':target}]
            self.assertIn(target,C.mounts_model(model,'app'))
    def test_implicit_env_references_are_held_without_inventing_cli_flags(self):
        backend = object.__new__(S.Site); backend.paths = ['/public/base.yml']; backend.scope = {'project':'public',
            'workdir':'/public/workdir','env_files':[{'path':'/public/workdir/.env','cli':False},{'path':'/public/service.env','cli':False}]}
        self.assertNotIn('--env-file',backend.compose())
        backend.scope['env_files'].append({'path':'/public/actual-cli.env','cli':True})
        self.assertEqual(backend.compose().count('--env-file'),1)
        self.assertIn('/public/actual-cli.env',backend.compose()); self.assertNotIn('/public/service.env',backend.compose())
    def test_complete_file_alias_chain_required_without_secret_payload_reads(self):
        target = self.file('secret-target',b'PUBLIC SYNTHETIC NEVER READ')
        middle = self.root/'middle.env'; middle.symlink_to(target.name)
        first = self.root/'normal.env'; first.symlink_to(middle.name)
        aliases = [{'path':str(p),'identity':S.identity(p.lstat())} for p in (first,middle)]
        proof = {'path':str(first),'target':str(target),'identity':S.identity(target.stat()),'aliases':aliases}
        with patch.object(S.os,'pread',side_effect=AssertionError('secret payload denied')):
            with ExitStack() as stack:S.OpaqueReference(proof,stack).check()
            for missing in ([],aliases[:1],aliases[1:]):
                with self.subTest(missing=len(missing)),self.assertRaisesRegex(RuntimeError,'env_alias_inventory'):
                    with ExitStack() as stack:S.OpaqueReference({**proof,'aliases':missing},stack)
            with self.assertRaisesRegex(RuntimeError,'env_alias_inventory'):
                with ExitStack() as stack:S.OpaqueReference({**proof,'aliases':aliases+[aliases[0]]},stack)

    def test_raw_parent_directory_symlink_before_dotdot_cannot_hide_a_different_or_undeclared_target(self):
        for name in ('target','other','other/nested','other/target'):
            (self.root/name).mkdir(mode=0o700)
        target=self.file('target/normal.env',b'PUBLIC DECLARED')
        other=self.file('other/target/normal.env',b'PUBLIC DIFFERENT')
        redirect=self.root/'redirect';redirect.symlink_to(self.root/'other/nested',target_is_directory=True)
        alias=self.root/'normal-alias';alias.symlink_to('redirect/../target/normal.env')
        proof={'path':str(alias),'target':str(target),'identity':S.identity(target.stat()),
            'aliases':[{'path':str(alias),'identity':S.identity(alias.lstat())}]}
        self.assertEqual(alias.resolve(strict=True),other)
        with patch.object(S.os,'pread',side_effect=AssertionError('opaque payload read denied')):
            with ExitStack() as stack,self.assertRaises(RuntimeError):S.OpaqueReference(proof,stack)
            # Equality of the final target alone also cannot admit an undeclared
            # directory-symlink hop discarded by lexical '..' normalization.
            redirect.unlink();redirect.symlink_to(self.root/'other',target_is_directory=True)
            self.assertEqual(alias.resolve(strict=True),target)
            with ExitStack() as stack,self.assertRaises(RuntimeError):S.OpaqueReference(proof,stack)

    def test_safe_relative_dotdot_alias_remains_opaque_and_parent_metadata_stays_bound(self):
        directory=self.root/'normal-aliases';directory.mkdir(mode=0o700)
        target=self.file('normal-relative.env',b'PUBLIC NORMAL REFERENCE')
        alias=directory/'normal.env';alias.symlink_to('../normal-relative.env')
        proof={'path':str(alias),'target':str(target),'identity':S.identity(target.stat()),
            'aliases':[{'path':str(alias),'identity':S.identity(alias.lstat())}]}
        with patch.object(S.os,'pread',side_effect=AssertionError('opaque payload read denied')):
            reference=S.OpaqueReference(proof,self.stack);reference.check()
            directory.chmod(0o750)
            try:
                with self.assertRaises(RuntimeError):reference.check()
            finally:directory.chmod(0o700)
    def test_scope_duration_requires_positive_known_finite_max7200seconds(self):
        for text,total in (('2h',7200),('7200s',7200),('1h 30min',5400),('500ms',.5),('1000000us',1)):
            self.assertEqual(float(C.scope_seconds(text)),total)
        for text in ('infinity','arbitrary','0s','7201s','3h','-1s','nan','1e9s','900','2h\n','1h 999min'):
            with self.subTest(text=text),self.assertRaisesRegex(RuntimeError,'finite_scope'):C.scope_seconds(text)
    def test_public_authority_mismatch_with_same_nonce_refuses_before_any_action(self):
        control = {'nonce':'a'*32,'public_pin':'b'*64}; value = K.frame(control,0,'ready')
        changed = {**control,'public_pin':'c'*64}
        self.assertEqual(value['operation'],C.OPERATION)
        with self.assertRaisesRegex(RuntimeError,'peer_frame'):K.admit(value,changed,0)
        with self.assertRaisesRegex(RuntimeError,'private_output'):C.control_binding({'Env':['PUBLIC_NEVER_HASH_PRIVATE_VALUES']})


if __name__ == '__main__':unittest.main()
