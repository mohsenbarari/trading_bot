"""Finite reversible parking of eleven retained legacy containers; default check."""
from contextlib import ExitStack
from datetime import datetime,timezone
from hashlib import sha256
from pathlib import Path
from types import ModuleType
import argparse,base64,fcntl,json,os,re,resource,signal,stat,tempfile,time

OP='security-legacy-parking-20261025-01'
MAIN=Path('/root/trading-bot/trading_bot');HERE=Path(__file__).resolve().parent
HELPER_SHA='fa2bf85cd50378bb999a7d8aabbd965a174722605c943c59ce6da56f68a74901'
INVENTORY_SHA='e6baf5f30a2eed60af42bfdda5afe766f6524132d1763a7f98deb2a1c8a37a10'
CONFIG_SHA='6d8b88bb029e6603d4a055e0883c6c5284bb07ae645edc793104231834e5ee03'
STOP=MAIN/'.local/two-site-refactor/integration/.local/two-site-refactor/autopilot/STOP'
TOKEN=Path('/run/trading-bot/market-capture-legacy-authority-enabled')
KNOWN_HEALTH={'f2dbc15b9c9a2d14ef9669d1f6de37096161469a50c47bf6609f0383886b3a27','f0f5ddc3efe1a55cc8a6f800ad59d1f41556a4f896cdc8ed7d20864c9d360e2a'}
TARGETS={
 '54387f4d51f36c3c49495c88a0641ae67f44855fbb2424d980d62393ef2f063c':('bot','/trading_bot_staging-foreign_app-1','trading_bot_staging'),
 'cf08eee60689dcc07cb5995b52dbec7683d8f5365b4c2bbae0aef32e17cec465':('bot','/trading_bot_staging-foreign_sync_worker-1','trading_bot_staging'),
 '7beb657410211b951ee669770fb405764f9ce078b6f9c5d017b8528dac273994':('bot','/trading_bot_staging-bot-1','trading_bot_staging'),
 'a3456a6d2233d254e0cba270a106ba481f858ed9920c1719ab9d8d27321c0e80':('bot','/trading_bot_staging-bot_executor-1','trading_bot_staging'),
 '757abbc44c82400e521d7cc78cabab83440d7fd3128920d3a707b78e45107347':('bot','/trading_bot_staging-db-1','trading_bot_staging'),
 'c95cef46e04c47b23a6e779b5ef3dbb284ca251369ab6cf9ee69df1226d02053':('bot','/trading_bot_staging-redis-1','trading_bot_staging'),
 'e56e357ebd9aa31f8379904dd12b0ae5ca3b02ae2b116132ee6c78e5e6f0733c':('web','/trading_bot_staging_iran-app-1','trading_bot_staging_iran'),
 'a113f73fe1be1cfc778e6f75014db639b286629196930ebe6d331e40f4fd14ee':('web','/trading_bot_staging_iran-sync_worker-1','trading_bot_staging_iran'),
 'd013bebcfbaf09e64bc5fe6c4b8a35e5460cb5adaf49ea187150741c60c85b26':('web','/trading_bot_staging_iran-db-1','trading_bot_staging_iran'),
 '3dac9af2abc551e1f8b3df6ebee4cf60e07c6b6051b95244c80e03e21a9ecb72':('web','/trading_bot_staging_iran-redis-1','trading_bot_staging_iran'),
 '707155a1c3d71bb4b18c41fc2673ce01af3ce269bd2ba24bfbd0cee826cbf9f5':('web','/three-site-727c1f337b75-webapp-fi-webapp_fi_db-1','three-site-727c1f337b75-webapp-fi')}
WRITERS=('cf08','a345','7beb','5438','a113','e56e')
ORDER=tuple(next(i for i in TARGETS if i.startswith(prefix)) for prefix in (*WRITERS,'757a','c95c','d013','3dac','7071'))
NO={'Name':'no','MaximumRetryCount':0}
# Names select candidates for classification; none alone grants an exemption.
READER_FAMILIES={
 'coin-intelligence-staging-market-input-bridge':(
  'deploy/coin_intelligence/systemd/coin-intelligence-staging-market-input-bridge.service',
  'deploy/coin_intelligence/systemd/coin-intelligence-staging-market-input-bridge.timer',
  'scripts/bridge_staging_market_inputs.py'),
 'coin-intelligence-staging-snapshot-publish':(
  'deploy/coin_intelligence/systemd/coin-intelligence-staging-snapshot-publish.service.template',
  'deploy/coin_intelligence/systemd/coin-intelligence-staging-snapshot-publish.timer',
  'deploy/coin_intelligence/systemd/coin-intelligence-staging-snapshot-publish.service.d/host-python-toman.conf',
  'deploy/coin_intelligence/systemd/coin-intelligence-staging-snapshot-publish.service.d/host-python-toman.conf.template',
  'scripts/install_staging_coin_inference_snapshot_publisher.sh',
  'scripts/publish_coin_intelligence_snapshot.py'),
 'coin-intelligence-staging-snapshot-relay':(
  'scripts/install_staging_coin_inference_snapshot_relay.sh',
  'scripts/relay_staging_coin_inference_snapshot.py'),
 'coin-private-shadow-legacy-estimator-bridge':(
  'deploy/coin_intelligence/systemd/coin-private-shadow-legacy-estimator-bridge.service.template',
  'deploy/coin_intelligence/systemd/coin-private-shadow-legacy-estimator-bridge.timer',
  'scripts/install_private_shadow_legacy_estimator_bridge.sh',
  'scripts/run_private_shadow_legacy_estimator_bridge.py')}
READER_SYSTEMD=Path('/etc/systemd/system');READER_RUNTIME=Path('/run/systemd/system')
READER_UNITS=frozenset(base+suffix for base in READER_FAMILIES for suffix in ('.service','.timer'))
READER_COMMON_SOURCES={'deploy/maintenance/boot_mount_gates.yml','deploy/maintenance/boot_mount_inventory.yml'}
READER_PROPERTIES='Id,LoadState,ActiveState,UnitFileState,Type,MainPID,Job,NeedDaemonReload,RefuseManualStart,TriggeredBy,Triggers,FragmentPath,DropInPaths,Transient'
CONFIRM='PARK-ONLY-ELEVEN-RETAIN-CIDS-DATA-IMAGES-20261025-01'
PHASE='public_input_admission'
class Refusal(RuntimeError):pass
TRUSTED=(Refusal,)
def need(ok,code):
    if not ok:raise Refusal(code)
def ident(s):return(s.st_dev,s.st_ino,s.st_mode,s.st_uid,s.st_gid,s.st_nlink,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
def encoded(v):return(json.dumps(v,sort_keys=True,separators=(',',':'))+'\n').encode()
def equal(a,b):
    if type(a) is not type(b):return False
    if type(a) is dict:return set(a)==set(b) and all(equal(a[k],b[k]) for k in a)
    if type(a) is list:return len(a)==len(b) and all(equal(x,y) for x,y in zip(a,b))
    return a==b
def utc(v):
    need(type(v) is str,'utc_shape');value=datetime.fromisoformat(v.replace('Z','+00:00'));need(value.tzinfo is not None and value.utcoffset().total_seconds()==0,'utc_shape');return value
def held(stack,path,maximum,pin=None):
    need(path.is_absolute() and path.resolve(strict=True)==path,'public_path');fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK);stack.callback(os.close,fd);s=os.fstat(fd);before=ident(s)
    need(stat.S_ISREG(s.st_mode) and s.st_uid==s.st_gid==0 and s.st_nlink==1 and not s.st_mode&0o022 and 0<s.st_size<=maximum,'public_metadata');raw=os.pread(fd,s.st_size+1,0)
    def check():need(before==ident(os.fstat(fd))==ident(path.lstat()) and os.pread(fd,s.st_size+1,0)==raw,'public_changed')
    need(len(raw)==s.st_size and (pin is None or sha256(raw).hexdigest()==pin),'public_sha');check();return raw,check
def load(stack,path,pin):
    raw,check=held(stack,path,131072,pin);m=ModuleType('_sealed_parking_primitives');m.__file__=str(path);exec(compile(raw,str(path),'exec'),m.__dict__);return m,check
def write(b,path,value,exclusive=True,expected=None):
    def public(v):
        if type(v) is dict:
            need(not {'Env','Config','HostConfig','environment'}&set(v),'private_values_not_persistable')
            for k,item in v.items():
                if k in {'legacypark_FINAL','security_accepted','deployment_eligible','data_deletion_authorized'}:need(item is False,'final_acceptance_forbidden')
                public(item)
        elif type(v) is list:
            for item in v:public(item)
    public(value);raw=encoded(value);need(len(raw)<=2*1024**2,'public_bound');b.private_parent(path);fd,tmp=tempfile.mkstemp(prefix='.legacy-public-',dir=path.parent)
    try:
        b.write_all(fd,raw);os.fsync(fd)
        if exclusive:b.publish_noreplace(tmp,path)
        else:
            need(expected is not None and ident(path.lstat())==expected,'mutable_control_changed')
            with ExitStack() as stack:
                previous,check=held(stack,path,2*1024**2);old=b.decode(previous);need(old.get('schema')==value.get('schema') and old.get('nonce')==value.get('nonce') and old.get('id')==value.get('id'),'mutable_control_owner');check();need(ident(path.lstat())==expected,'mutable_control_changed');os.replace(tmp,path)
        parent=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
        try:os.fsync(parent)
        finally:os.close(parent)
        return ident(path.lstat())
    finally:os.close(fd);Path(tmp).unlink(missing_ok=True)
def workspace(site,nonce):return (MAIN/'.local' if site=='bot' else Path('/srv/trading-bot'))/(OP+'-'+nonce)
def checked_context(window,profile,app,now,*,action=None):
    need(type(window) is dict and window.get('schema')=='security_legacy_parking_window/1' and window.get('operation_id')==OP and window.get('action') in {'park','restore'} and type(window.get('nonce')) is str and re.fullmatch('[a-f0-9]{32}',window['nonce']),'parking_authority')
    start,end=utc(window['not_before_utc']),utc(window['deadline_utc']);need(start<=now<end and 0<(end-start).total_seconds()<=900,'parking_window_closed')
    need(type(profile) is dict and profile.get('schema')=='security_legacy_parking_profile/1' and profile.get('legacypark_FINAL') is False and profile.get('security_accepted') is False and set(profile.get('sites',{}))=={'bot','web'} and 0<=(start-utc(profile['created_at_utc'])).total_seconds()<=300,'fresh_post_app_profile_required')
    need(app.get('schema')=='security_consumer_controller_result/2' and app.get('status')=='five_targets_verified_global_gates_pending' and app.get('action')=='apply' and all(app.get(k) is True for k in ('fence_cleanup_confirmed','source_mutex_preserved','reservation_removed')) and all(app.get(k) is False for k in ('security_accepted','global_security_eligible','raw_gate_passed','raw_gate_waived')),'actual_app_handoff_required')
    need(app.get('operation_id')=='security-consumers-20261024-02' and type(app.get('nonce')) is str and re.fullmatch('[a-f0-9]{32}',app['nonce']) and type(app.get('source_commit')) is str and re.fullmatch('[a-f0-9]{40}',app['source_commit']) and profile.get('app_controller_nonce')==app['nonce'] and profile.get('app_controller_source_commit')==app['source_commit'],'post_app_profile_binding')
    need(all(window.get(k) is False for k in ('legacypark_FINAL','security_accepted','data_deletion_authorized')) and type(window.get('permissions')) is dict and all(type(v) is bool for v in window['permissions'].values()) and window['permissions']=={'restart_policy_only':True,'stop_or_owned_start_only':True,'remove':False,'volumes':False,'images':False,'qa':False,'active_market':False},'parking_permission_scope')
    controls=window.get('controls');need(type(controls) is dict and set(controls)=={'script','playbook','helper','inventory','config'} and all(type(v) is str and re.fullmatch('[a-f0-9]{64}',v) for v in controls.values()) and controls['helper']==HELPER_SHA and controls['inventory']==INVENTORY_SHA and controls['config']==CONFIG_SHA,'parking_control_pins')
    for site,data in profile['sites'].items():
        rows=data['runtime'];need(type(rows) is list and len(rows)==(15 if site=='bot' else 27) and len({r['id'] for r in rows})==len(rows),'parking_profile_inventory')
        fields={'id','name','image','project','service','running','paused','restarting','restart_policy','mounts','network_mode','networks','ports','pid_mode','privileged','read_only','health','exit_code','oom_killed','error_empty'}
        need(all(type(r) is dict and set(r)==fields and type(r['id']) is str and re.fullmatch('[a-f0-9]{64}',r['id']) and type(r['image']) is str and re.fullmatch('sha256:[a-f0-9]{64}',r['image']) and all(type(r[k]) is bool for k in ('running','paused','restarting','privileged','read_only','oom_killed','error_empty')) and type(r['exit_code']) is int and type(r['mounts']) is list and len(r['mounts'])<=64 and type(r['networks']) is dict and len(r['networks'])<=32 and type(r['restart_policy']) is dict and set(r['restart_policy'])=={'Name','MaximumRetryCount'} and type(r['restart_policy']['MaximumRetryCount']) is int for r in rows),'parking_public_runtime_shape')
        need(sum(r['running'] is True for r in rows)==((15 if site=='bot' else 16)-(0 if window['action']=='park' else 6 if site=='bot' else 5)),'parking_running_intent')
        checked_readers(data.get('production_readers'),site)
        need(type(data.get('machine_id')) is str and re.fullmatch('[a-f0-9]{32}',data['machine_id']) and type(data.get('drivers')) is dict and len(data['drivers'])<=128,'parking_site_metadata')
        roots=data.get('store_roots');wanted={'/srv/trading-bot/production-data/docker-data'} if site=='bot' else {'/var/lib/docker','/var/lib/containerd'}
        need(type(roots) is list and len(roots)==len(wanted) and {r['path'] for r in roots}==wanted and all(type(r.get('identity')) is list and len(r['identity'])==5 and all(type(n) is int for n in r['identity']) and type(r.get('uuid')) is str and re.fullmatch('[a-f0-9-]{36}',r['uuid']) for r in roots),'parking_store_roots')
        ids={r['id']:r for r in rows};selected={i for i,v in TARGETS.items() if v[0]==site};need(selected<=set(ids),'parking_profile_targets')
        need(type(data['marker_identity']) is list and len(data['marker_identity'])==9 and all(type(n) is int for n in data['marker_identity']) and data['marker_identity'][2:6]==[0o100600,0,0,1],'parking_marker_identity')
        if site=='bot':need(type(data['source_mutex_identity']) is list and len(data['source_mutex_identity'])==9 and all(type(n) is int for n in data['source_mutex_identity']) and data['source_mutex_identity'][2:7]==[0o100600,0,0,1,0],'parking_source_mutex_identity')
        qa=data.get('qa_ids');need(type(qa) is list and all(type(i) is str and re.fullmatch('[a-f0-9]{64}',i) for i in qa) and len(qa)==len(set(qa)),'parking_qa_identity_shape')
        if site=='bot':need(qa==[],'parking_native_qa_scope')
        else:need(data['source_mutex_identity'] is None and len(qa)==10 and not set(qa)&selected,'parking_web_source_or_qa_scope')
        for i in selected:
            r=ids[i];need((r['name'],r['project'])==TARGETS[i][1:] and r['running'] is (window['action']=='park') and r['paused'] is False and r['restarting'] is False,'parking_target_identity')
            need(r['restart_policy']==NO if window['action']=='restore' else r['restart_policy']['Name'] in {'always','unless-stopped'} and r['restart_policy']['MaximumRetryCount']==0,'parking_target_policy')
        for i in data.get('qa_ids',[]):need(i in ids and ids[i]['running'] is False and equal(ids[i]['restart_policy'],NO),'parking_qa_changed')
        serving={'/trading_bot_app','/trading_bot_sync_worker'}|({'/trading_bot_bot'} if site=='bot' else set());by_name={r['name']:r for r in rows};candidate='sha256:8da3e404d01cc239606d1c79a30c4701c5a2ef2d103787b68b9ec221ae723672' if site=='bot' else 'sha256:c6106a78b1ef830103fddf351635028773eb0efa59281449a3a9f062ec946e7c'
        need(serving<=set(by_name) and all(by_name[n]['running'] is True and by_name[n]['image']==candidate and by_name[n]['id'] not in TARGETS for n in serving),'actual_post_app_serving_images_required')
    return end

def runtime(b):
    ids=b.command(['/usr/bin/docker','container','ls','--all','--quiet','--no-trunc']).splitlines();need(0<len(ids)<=64 and len(ids)==len(set(ids)) and all(re.fullmatch('[a-f0-9]{64}',i) for i in ids),'runtime_inventory')
    fmt='{"id":{{json .Id}},"name":{{json .Name}},"image":{{json .Image}},"project":{{json (index .Config.Labels "com.docker.compose.project")}},"service":{{json (index .Config.Labels "com.docker.compose.service")}},"running":{{json .State.Running}},"paused":{{json .State.Paused}},"restarting":{{json .State.Restarting}},"restart_policy":{{json .HostConfig.RestartPolicy}},"mounts":{{json .Mounts}},"network_mode":{{json .HostConfig.NetworkMode}},"networks":{{json .NetworkSettings.Networks}},"ports":{{json .HostConfig.PortBindings}},"pid_mode":{{json .HostConfig.PidMode}},"privileged":{{json .HostConfig.Privileged}},"read_only":{{json .HostConfig.ReadonlyRootfs}},"health":{{with index .State "Health"}}{{json .Status}}{{else}}null{{end}},"exit_code":{{json .State.ExitCode}},"oom_killed":{{json .State.OOMKilled}},"error_empty":{{json (eq .State.Error "")}}}'
    rows=[b.decode(line.encode()) for line in b.command(['/usr/bin/docker','container','inspect','--format',fmt,*sorted(ids)]).splitlines()];need(len(rows)==len(ids) and {r['id'] for r in rows}==set(ids),'runtime_inventory')
    for row in rows:
        need(type(row['running']) is bool and row['paused'] is False and row['restarting'] is False and row['health'] in (None,'healthy','unhealthy','starting'),'runtime_state')
        row['mounts']=sorted(row['mounts'],key=lambda m:m['Destination']);row['ports']=row['ports'] or {}
        need(type(row['networks']) is dict,'runtime_networks')
        row['networks']={name:{k:v for k,v in network.items() if k in {'IPAMConfig','Links','Aliases','DriverOpts','GwPriority'}} for name,network in row['networks'].items()}
    return sorted(rows,key=lambda r:r['id'])
def phase_expected(profile,site,projection):
    need(type(projection) is dict and set(projection)<=set(TARGETS),'parking_projection')
    rows=json.loads(json.dumps(profile['sites'][site]['runtime']))
    for row in rows:
        if row['id'] in projection:
            value=projection[row['id']];need(type(value) is dict and set(value)=={'running','restart_policy'} and type(value['running']) is bool,'parking_projection');row.update(value)
    return rows
def matches_runtime(actual,expected):
    need(len(actual)==len(expected) and {r['id'] for r in actual}=={r['id'] for r in expected},'parking_inventory_changed');by_id={r['id']:r for r in actual};incidents=[]
    for old in expected:
        row=by_id[old['id']];skip={'exit_code','oom_killed','error_empty','health'}
        need(equal({k:v for k,v in row.items() if k not in skip},{k:v for k,v in old.items() if k not in skip}),'parking_runtime_changed')
        if row['running'] and row['id'] not in TARGETS and row['health'] not in (None,'healthy'):
            need(row['id'] in KNOWN_HEALTH,'parking_unexpected_peer_health');incidents.append(row['id'])
    return sorted(incidents)
def checked_readers(bindings,site):
    need(type(bindings) is dict and set(bindings)==(READER_UNITS if site=='bot' else set()),'production_reader_bindings_required')
    for name,binding in bindings.items():
        need(type(binding) is dict and set(binding)=={'classification','public_files','policy'},'production_reader_binding_fields')
        base,suffix=name.rsplit('.',1);proof=binding['classification']
        need(type(proof) is dict and set(proof)=={'schema','unit','role','source_commit','versioned_sources','root_public_source_equivalence_reviewed','legacy_container_driver','credential_values_present'}
             and proof['schema']=='security_production_reader_source_classification/1' and proof['unit']==name
             and proof['role']=='production_model_projection' and proof['root_public_source_equivalence_reviewed'] is True
             and proof['legacy_container_driver'] is False and proof['credential_values_present'] is False
             and type(proof['source_commit']) is str and re.fullmatch('[a-f0-9]{40}',proof['source_commit']),'production_reader_source_classification')
        sources=proof['versioned_sources'];allowed=set(READER_FAMILIES[base])|READER_COMMON_SOURCES
        need(type(sources) is dict and set(sources)==allowed and all(type(v) is str and re.fullmatch('[a-f0-9]{64}',v) for v in sources.values()),'production_reader_versioned_sources')
        policy=binding['policy'];service=suffix=='service';peer=base+('.timer' if service else '.service')
        required={'Id':name,'LoadState':'loaded','UnitFileState':'static' if service else 'enabled',
            'Type':'oneshot' if service else '', 'NeedDaemonReload':'no','RefuseManualStart':'no',
            'TriggeredBy':peer if service else '', 'Triggers':'' if service else peer,
            'FragmentPath':str(READER_SYSTEMD/name),'Transient':'no'}
        need(type(policy) is dict and set(policy)==set(required)|{'DropInPaths'} and all(policy[k]==v for k,v in required.items())
             and type(policy['DropInPaths']) is str,'production_reader_policy_binding')
        drops=policy['DropInPaths'].split();files=binding['public_files']
        need(len(drops)==len(set(drops))<=16 and type(files) is dict and set(files)=={policy['FragmentPath'],*drops},'production_reader_public_file_inventory')
        for path,bound in files.items():
            parent=Path(path).parent
            need(path==policy['FragmentPath'] or parent in {READER_SYSTEMD/(name+'.d'),READER_RUNTIME/(name+'.d')},'production_reader_public_file_scope')
            need(type(bound) is dict and set(bound)=={'identity','sha256','public_bytes_b64','versioned_source'}
                 and bound['versioned_source'] in allowed and type(bound['sha256']) is str and re.fullmatch('[a-f0-9]{64}',bound['sha256'])
                 and type(bound['identity']) is list and len(bound['identity'])==9 and all(type(v) is int for v in bound['identity'])
                 and bound['identity'][0]>0 and bound['identity'][1]>0 and stat.S_ISREG(bound['identity'][2])
                 and not bound['identity'][2]&0o022 and bound['identity'][3:6]==[0,0,1]
                 and 0<bound['identity'][6]<=65536 and type(bound['public_bytes_b64']) is str and len(bound['public_bytes_b64'])<=87384,'production_reader_public_file_binding')
            raw=base64.b64decode(bound['public_bytes_b64'],validate=True)
            need(len(raw)==bound['identity'][6] and sha256(raw).hexdigest()==bound['sha256'],'production_reader_expected_public_bytes')
    return bindings

def reader_policy(value,name,binding):
    service=name.endswith('.service');static={key:value.get(key,'') for key in binding['policy']}
    need(equal(static,binding['policy']),'production_reader_loaded_policy_changed')
    if service:
        need(value.get('ActiveState') in {'inactive','active','activating','deactivating'}
             and type(value.get('MainPID')) is str and re.fullmatch('[0-9]{1,20}',value['MainPID'])
             and type(value.get('Job')) is str and (value['Job']=='' or re.fullmatch('[0-9]{1,20}',value['Job'])),'production_reader_oneshot_state')
        state={'ActiveState':'NATURAL_ONESHOT_TRANSITION'}
    else:
        need(value.get('ActiveState')=='active' and value.get('MainPID','0')=='0' and value.get('Job') in {'','0'},'production_reader_timer_state')
        state={'ActiveState':'active','MainPID':'0','Job':'0'}
    return {**static,**state}

def reader_driver(b,name,binding):
    def shown():
        raw=b.command(['/usr/bin/systemctl','show',name,'--property='+READER_PROPERTIES]);need(len(raw)<=65536,'production_reader_show_bound')
        fields={}
        for line in raw.splitlines():
            if not line:continue
            key,sep,value=line.partition('=');need(sep and key not in fields,'production_reader_show_fields');fields[key]=value
        need(set(fields)<=set(READER_PROPERTIES.split(',')),'production_reader_show_fields')
        return reader_policy(fields,name,binding)
    before=shown();checks=[]
    with ExitStack() as stack:
        for path,bound in binding['public_files'].items():
            path=Path(path);need(list(ident(path.lstat()))==bound['identity'],'production_reader_public_metadata_changed')
            # Expected bytes were admitted as PUBLIC by Root from versioned
            # sources before this phase. Never hash changed/unknown actual bytes.
            expected=base64.b64decode(bound['public_bytes_b64'],validate=True)
            raw,check=held(stack,path,65536);need(raw==expected,'production_reader_public_bytes_changed');checks.append(check)
        after=shown();need(equal(before,after),'production_reader_changed_during_check')
        for check in checks:check()
    return {**after,'classification':binding['classification'],
        'verified_public_files':{path:{'identity':item['identity'],'sha256':item['sha256']} for path,item in binding['public_files'].items()}}

def drivers(b,nonce,site=None,readers=None):
    readers=checked_readers(readers,site) if site in {'bot','web'} else {}
    collectors={'coin-capture.service','market-channel-capture.service'} if site=='web' else set();names=set(collectors)|set(readers)
    for verb in ('list-unit-files','list-units'):
        raw=b.command(['/usr/bin/systemctl',verb,'--all','--no-legend','--no-pager','--plain']);need(len(raw)<=2*1024**2,'driver_inventory_bound')
        for line in raw.splitlines():
            parts=line.split();name=next((s for s in parts[:2] if re.fullmatch(r'[A-Za-z0-9_.@:-]+\.(?:service|timer)',s)),None)
            if name and any(v in name.lower() for v in ('staging','deploy','autopilot','legacy','three-site','cutover')) and name!=OP+'-'+nonce+'.service':names.add(name)
    need(len(names)<=128,'driver_inventory_bound');result={}
    for name in sorted(names):
        if name in readers:
            result[name]=reader_driver(b,name,readers[name]);continue
        raw=b.command(['/usr/bin/systemctl','show',name,'--property=LoadState,ActiveState,UnitFileState,MainPID,Job,NeedDaemonReload,RefuseManualStart,TriggeredBy,DropInPaths'])
        value=dict(line.split('=',1) for line in raw.splitlines());need(value.get('ActiveState') in {'inactive','failed'} and value.get('MainPID')=='0' and value.get('Job') in {'','0'} and value.get('NeedDaemonReload')=='no' and set(value.get('TriggeredBy','').split())<=names and (value.get('UnitFileState') in {'disabled','masked','static','not-found','indirect'} or value.get('RefuseManualStart')=='yes'),'legacy_deploy_driver_not_fenced')
        if name in collectors:
            need(value.get('LoadState')=='loaded' and value.get('UnitFileState')=='disabled' and value.get('RefuseManualStart')=='yes','web_legacy_capture_guard_changed')
            persistent=Path('/etc/systemd/system')/(name+'.d')/'90-security-20261024-web-boot.conf';runtime_guard=Path('/run/systemd/system')/(name+'.d')/'market-container-authority.conf';paths=set(value.get('DropInPaths','').split());need(str(persistent) in paths and paths<={str(persistent),str(runtime_guard)},'web_legacy_guard_inventory')
            body=b'[Unit]\nConditionPathExists=/run/trading-bot/market-capture-legacy-authority-enabled\nRefuseManualStart=yes\n';header=b'# Owned Web boot guards 2026-10-24; exact-content cleanup only.\n';files={}
            for path in (persistent,runtime_guard):
                if str(path) in paths:
                    wanted=header+body if path==persistent else body;need(b.read(path,maximum=8192,private=False)==wanted,'web_legacy_guard_bytes_changed');files[str(path)]={'identity':list(ident(path.lstat())),'sha256':sha256(wanted).hexdigest()}
            value['verified_public_guards']=files
        result[name]=value
    return result
def snapshot(site,profile,projection,b,nonce):
    data=profile['sites'][site];need(b.read(Path('/etc/machine-id'),maximum=128,private=False).decode().strip()==data['machine_id'],'parking_host_identity')
    need(not os.path.lexists(b.PENDING),'parking_pending_release')
    if site=='bot':need(b.read(STOP,maximum=16,private=False)==b'STOP\n','parking_automation_not_stopped')
    else:need(not os.path.lexists(TOKEN),'legacy_capture_authority_present')
    actual=runtime(b);incidents=matches_runtime(actual,phase_expected(profile,site,projection));need(equal(drivers(b,nonce,site,data['production_readers']),data['drivers']),'legacy_deploy_driver_changed')
    for root in data['store_roots']:
        path=Path(root['path']);s=path.lstat();need(path in ({Path('/srv/trading-bot/production-data/docker-data')} if site=='bot' else {Path('/var/lib/docker'),Path('/var/lib/containerd')}) and path.resolve(strict=True)==path and stat.S_ISDIR(s.st_mode) and list(ident(s)[:5])==root['identity'] and b.command(['/usr/bin/findmnt','-n','-o','UUID','-T',str(path)])==root['uuid'],'parking_store_root_changed')
    return actual,incidents

def mutex(stack,path,expected):
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK);stack.callback(os.close,fd);need(list(ident(os.fstat(fd)))==expected==list(ident(path.lstat())),'permanent_mutex_changed');fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
    def check():need(list(ident(os.fstat(fd)))==expected==list(ident(path.lstat())),'permanent_mutex_changed')
    check();return fd,check
def guard(stack,b,path,payload):
    b.private_parent(path);need(not os.path.lexists(path),'existing_guard_not_adopted');fd,tmp=tempfile.mkstemp(prefix='.legacy-guard-',dir=path.parent);stack.callback(os.close,fd)
    published=False
    try:
        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB);payload={**payload,'inode':os.fstat(fd).st_ino,'pid':os.getpid()};raw=encoded(payload);b.write_all(fd,raw);os.fsync(fd);b.publish_noreplace(tmp,path);before=ident(os.fstat(fd));published=True
        parent=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
        try:os.fsync(parent)
        finally:os.close(parent)
    except BaseException:
        if published and before==ident(os.fstat(fd))==ident(path.lstat()) and os.pread(fd,len(raw)+1,0)==raw:path.unlink()
        raise
    finally:Path(tmp).unlink(missing_ok=True)
    def check():need(before==ident(os.fstat(fd))==ident(path.lstat()) and os.pread(fd,len(raw)+1,0)==raw,'owned_guard_changed')
    check();return fd,check
def remove_guard(path,check):
    check();path.unlink();parent=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:os.fsync(parent)
    finally:os.close(parent)
def coordinator_owner(window,profile,window_sha,b):
    value=b.document(b.RESERVATION);need(value.get('schema')=='security_legacy_parking_guard/1' and value.get('nonce')==window['nonce'] and value.get('operation_id')==OP and value.get('window_sha256')==window_sha and type(value.get('pid')) is int and value['pid']>1 and value.get('inode')==b.RESERVATION.lstat().st_ino,'parking_owner_missing')
    held=set()
    for line in b.command(['/usr/bin/cat','/proc/locks']).splitlines():
        fields=line.split()
        if len(fields)>=8 and fields[1:4]==['FLOCK','ADVISORY','WRITE']:
            major,minor,inode=fields[5].split(':');held.add((int(fields[4]),os.makedev(int(major,16),int(minor,16)),int(inode)))
    for path in (b.MARKER,b.SOURCE,b.RESERVATION):
        s=path.lstat();need((value['pid'],s.st_dev,s.st_ino) in held,'parking_owner_flocks_missing')
    need(list(ident(b.SOURCE.lstat()))==profile['sites']['bot']['source_mutex_identity'] and b.SOURCE.lstat().st_size==0,'parking_source_mutex_changed')
def mutation(b,argv,deadline,fd):
    saved=[os.dup(1),os.dup(2)];null=os.open('/dev/null',os.O_WRONLY)
    try:
        os.dup2(null,1);os.dup2(null,2)
        status=b.execute_ansible(argv,{'PATH':'/usr/sbin:/usr/bin:/sbin:/bin','LC_ALL':'C.UTF-8','DOCKER_HOST':'unix:///var/run/docker.sock'},deadline,lock_fds=fd if type(fd) is tuple else (fd,))
    finally:
        os.dup2(saved[0],1);os.dup2(saved[1],2)
        for item in (*saved,null):os.close(item)
    need(status==0,'parking_rpc_outcome_unknown')
def restore_parent(parent,profile):
    need(type(parent) is dict and parent.get('schema')=='security_legacy_parking_result/1' and parent.get('operation_id')==OP and parent.get('action')=='park' and parent.get('status')=='reversibly_parked' and parent.get('canonical_reservation_removed') is True and parent.get('legacypark_FINAL') is False and parent.get('security_accepted') is False and set(parent.get('original_target_intents',{}))==set(TARGETS),'completed_owned_parking_required')
    intents=parent['original_target_intents']
    for i,old in intents.items():
        site=TARGETS[i][0];current=next(r for r in profile['sites'][site]['runtime'] if r['id']==i)
        need(old['id']==i and old['running'] is True and old['restart_policy']['Name'] in {'always','unless-stopped'} and equal({k:v for k,v in old.items() if k not in {'running','restart_policy','health','exit_code','oom_killed','error_empty'}},{k:v for k,v in current.items() if k not in {'running','restart_policy','health','exit_code','oom_killed','error_empty'}}),'owned_restore_intent_changed')
    return intents
def datastore_ready(identification,b,deadline):
    pg=identification in {i for i in TARGETS if i.startswith(('757a','d013','7071'))};redis=identification in {i for i in TARGETS if i.startswith(('c95c','3dac'))}
    if not (pg or redis):return False
    end=min(time.monotonic()+45,time.monotonic()+(utc(deadline)-datetime.now(timezone.utc)).total_seconds())
    while time.monotonic()<end:
        try:
            value=b.command(['/usr/bin/docker','exec',identification,*(['pg_isready','-q'] if pg else ['redis-cli','ping'])])
            if pg or value=='PONG':return True
        except b.Refusal:pass
        time.sleep(.5)
    raise Refusal('legacy_restored_datastore_not_ready')
def target_order(window):return ORDER if window['action']=='park' else tuple(reversed(ORDER))
def checked_projection(window,projection,parent):
    need(type(projection) is dict and set(projection)==set(target_order(window)[:len(projection)]),'parking_target_order')
    for identification,value in projection.items():
        desired={'running':window['action']=='restore','restart_policy':NO if window['action']=='park' else parent[identification]['restart_policy']}
        need(equal(value,desired),'parking_projection_intent')
    return projection
def target_step(site,identification,window,profile,app,projection,b,root,fd,checks,*,parent=None,before_rpc=lambda:None):
    global PHASE
    need(identification in TARGETS and TARGETS[identification][0]==site,'parking_target_scope');checked_context(window,profile,app,datetime.now(timezone.utc));need((utc(window['deadline_utc'])-datetime.now(timezone.utc)).total_seconds()>=90,'parking_rpc_budget')
    expected=phase_expected(profile,site,projection);snapshot(site,profile,projection,b,window['nonce'])
    old=next(r for r in profile['sites'][site]['runtime'] if r['id']==identification);desired=NO if window['action']=='park' else parent[identification]['restart_policy'];running=window['action']=='restore'
    journal=root/('intent-'+identification+'.json');journal_binding=write(b,journal,{'schema':'security_legacy_parking_target_intent/1','nonce':window['nonce'],'id':identification,'action':window['action'],'phase':'restart_policy_request_unknown','original':old,'legacypark_FINAL':False,'security_accepted':False})
    for check in checks:check()
    PHASE='target_restart_policy';policy=desired['Name'];before_rpc();mutation(b,['/usr/bin/docker','update','--restart='+policy,identification],window['deadline_utc'],fd)
    halfway={**projection,identification:{'running':old['running'],'restart_policy':desired}};snapshot(site,profile,halfway,b,window['nonce'])
    write(b,journal,{'schema':'security_legacy_parking_target_intent/1','nonce':window['nonce'],'id':identification,'action':window['action'],'phase':'lifecycle_request_unknown','original':old,'legacypark_FINAL':False,'security_accepted':False},exclusive=False,expected=journal_binding)
    for check in checks:check()
    PHASE='target_start' if running else 'target_stop';before_rpc();mutation(b,['/usr/bin/docker',*(['start',identification] if running else ['stop','--time','60',identification])],window['deadline_utc'],fd)
    ready=datastore_ready(identification,b,window['deadline_utc']) if running else False
    wanted={**projection,identification:{'running':running,'restart_policy':desired}};rows,incidents=snapshot(site,profile,wanted,b,window['nonce']);current=next(r for r in rows if r['id']==identification)
    for check in checks:check()
    result={'schema':'security_legacy_parking_target_result/1','nonce':window['nonce'],'id':identification,'action':window['action'],'status':'known_target_verified','projection':wanted[identification],'clean_exit_observed':not running and type(current['exit_code']) is int and current['exit_code']==0 and current['oom_killed'] is False and current['error_empty'] is True,'datastore_listening_verified':ready,'business_transactions_tested':False,'known_health_open_ids':incidents,'health_waived':False,'data_backup_or_restore_proven':False,'legacypark_FINAL':False,'security_accepted':False}
    write(b,root/('result-'+identification+'.json'),result);return result
def inputs(args,stack):
    global TRUSTED
    need(args.primitives==(HERE/'validate_docker_daemon_maintenance.py' if args.role=='worker' else MAIN/'scripts/validate_docker_daemon_maintenance.py') and args.window.name=='legacy-window.json' and args.profile.name=='legacy-profile.json' and args.app_result.name in {'controller-result.json','app-controller-result.json'},'public_input_scope')
    b,check=load(stack,args.primitives,HELPER_SHA);TRUSTED=(Refusal,b.Refusal);checks=[check];raw,check=held(stack,args.window,2*1024**2,args.window_sha256);checks.append(check);window=b.decode(raw)
    raw,check=held(stack,args.profile,2*1024**2,args.profile_sha256);checks.append(check);profile=b.decode(raw)
    raw,check=held(stack,args.app_result,131072,args.app_sha256);checks.append(check);app=b.decode(raw)
    need(window.get('profile_sha256')==args.profile_sha256 and window.get('app_result_sha256')==args.app_sha256,'parking_input_chain')
    end=checked_context(window,profile,app,datetime.now(timezone.utc));signal.alarm(max(1,int((end-datetime.now(timezone.utc)).total_seconds())))
    _,check=held(stack,Path(__file__).resolve(),131072,window['controls']['script']);checks.append(check);parent=None
    if window['action']=='restore':
        need(args.parent is not None and args.parent_sha256 is not None and window.get('parent_park_sha256')==args.parent_sha256,'parking_parent_binding');raw,check=held(stack,args.parent,2*1024**2,args.parent_sha256);checks.append(check);parent=restore_parent(json.loads(raw),profile)
    return window,profile,app,b,checks,parent
def worker(args):
    need(os.geteuid()==0,'native_root_required')
    with ExitStack() as stack:
        window,profile,app,b,checks,parent=inputs(args,stack);root=workspace(args.site,window['nonce']);need(HERE==args.directory==root and root.resolve(strict=True)==root,'parking_worker_scope');projection=checked_projection(window,b.decode(args.projection.encode()),parent)
        need(args.mode in {'check','target'},'parking_worker_mode');seal=b.document(root/'public-stage.json');need(seal.get('schema')=='security_legacy_parking_stage/1' and seal.get('nonce')==window['nonce'] and seal.get('source_sha256')==window['controls']['script'] and seal.get('window_sha256')==args.window_sha256 and seal.get('profile_sha256')==args.profile_sha256 and seal.get('app_sha256')==args.app_sha256,'incomplete_public_stage_not_adopted')
        if args.mode=='check':
            rows,incidents=snapshot(args.site,profile,projection,b,window['nonce']);return {'status':'checked','known_health_open_ids':incidents,'legacypark_FINAL':False,'security_accepted':False}
        need(args.confirm==CONFIRM and args.target in TARGETS,'explicit_parking_scope_required');order=target_order(window);need(set(projection)==set(order[:order.index(args.target)]),'parking_target_order');inherited=[]
        if args.site=='bot':coordinator_owner(window,profile,args.window_sha256,b)
        else:
            need(not os.path.lexists(b.SOURCE) and not os.path.lexists(b.RESERVATION),'web_old_fences_must_remain_absent');marker,check=mutex(stack,b.MARKER,profile['sites']['web']['marker_identity']);checks.append(check);inherited.append(marker)
        lock=root/'site-operation.lock';fd,check=guard(stack,b,lock,{'schema':'security_legacy_parking_site_guard/1','operation_id':OP,'nonce':window['nonce']});checks.append(check)
        # Unknown lifecycle/policy RPCs never reach this exact cleanup. A retained
        # guard is inspected manually; no restart/stop request is replayed.
        state={'requested':False}
        def requested():state['requested']=True
        try:result=target_step(args.site,args.target,window,profile,app,projection,b,root,tuple([fd,*inherited]),checks,parent=parent,before_rpc=requested)
        except BaseException as error:
            code=reason(error)
            try:write(b,root/('failure-'+args.target+'.json'),{'schema':'security_legacy_parking_failure/1','nonce':window['nonce'],'id':args.target,'phase':PHASE,'code':code,'rpc_requested':state['requested'],'rpc_retry_allowed':False,'daemon_cancellation_proven':False,'legacypark_FINAL':False,'security_accepted':False})
            except Exception:pass
            if not state['requested']:remove_guard(lock,check)
            raise
        remove_guard(lock,check);return result

def invoke(args,mode,sites,window,projection,b,locks,*,target=None):
    nonce=window['nonce'];assets=[{'name':'security_legacy_parking.py','path':str(HERE/'security_legacy_parking.py'),'sha256':window['controls']['script']},{'name':'validate_docker_daemon_maintenance.py','path':str(args.primitives),'sha256':HELPER_SHA}]
    for name,path,pin in (('legacy-window.json',args.window,args.window_sha256),('legacy-profile.json',args.profile,args.profile_sha256),('app-controller-result.json',args.app_result,args.app_sha256)):
        assets.append({'name':name,'path':str(path),'sha256':pin})
    if args.parent is not None:assets.append({'name':'parent-parking.json','path':str(args.parent),'sha256':args.parent_sha256})
    values={'legacy_action':mode,'legacy_nonce':nonce,'security_coordinator_nonce':nonce,'legacy_assets':assets,'legacy_target':target or '', 'legacy_projection':json.dumps(projection,sort_keys=True),'legacy_window_sha256':args.window_sha256,'legacy_profile_sha256':args.profile_sha256,'legacy_app_sha256':args.app_sha256,'legacy_parent_sha256':args.parent_sha256 or '', 'legacy_result_destination':str(workspace('bot',nonce)/'results'/((sites[0]+'-'+target+'.json') if target else 'unused.json'))}
    binary=MAIN/'.local/security-maintenance-ansible-venv/bin/ansible-playbook';env={'PATH':str(binary.parent)+':/usr/sbin:/usr/bin:/sbin:/bin','LANG':'C.UTF-8','ANSIBLE_CONFIG':str(HERE/'security_legacy_parking_ansible.cfg'),'ANSIBLE_HOME':str(MAIN/'.local/security-maintenance-ansible-home'),'ANSIBLE_LOCAL_TEMP':str(MAIN/'.local/security-maintenance-ansible-tmp')}
    return b.execute_ansible([str(binary),'-i',str(HERE/'security_consumers_inventory.yml'),str(HERE/'security_legacy_parking.yml'),'--limit',':'.join('security_'+s for s in sites),'-e',json.dumps(values)],env,window['deadline_utc'],lock_fds=locks)
def coordinate(args):
    need(os.geteuid()==0 and HERE==MAIN/'deploy/maintenance','published_main_controls_required')
    with ExitStack() as stack:
        window,profile,app,b,checks,parent=inputs(args,stack);nonce=window['nonce'];root=workspace('bot',nonce)
        for name,pin in (('security_legacy_parking.yml',window['controls']['playbook']),('security_consumers_inventory.yml',INVENTORY_SHA),('security_legacy_parking_ansible.cfg',CONFIG_SHA)):
            _,check=held(stack,HERE/name,131072,pin);checks.append(check)
        marker,check=mutex(stack,b.MARKER,profile['sites']['bot']['marker_identity']);checks.append(check);source,check=mutex(stack,b.SOURCE,profile['sites']['bot']['source_mutex_identity']);checks.append(check)
        need(not os.path.lexists(b.RESERVATION) and not os.path.lexists(b.PENDING),'other_operation_present');need(b.read(STOP,maximum=16,private=False)==b'STOP\n','parking_automation_not_stopped')
        locks=[marker,source];projection={}
        if args.mode=='check':
            need(invoke(args,'check',('bot','web'),window,projection,b,locks)==0,'parking_preflight_failed');return {'status':'checked','legacypark_FINAL':False,'security_accepted':False}
        need(args.confirm==CONFIRM,'explicit_parking_scope_required');reservation,guard_check=guard(stack,b,b.RESERVATION,{'schema':'security_legacy_parking_guard/1','operation_id':OP,'nonce':nonce,'window_sha256':args.window_sha256});locks.append(reservation);checks.append(guard_check);requested=False
        try:
            if args.mode=='stage':
                need(invoke(args,'stage',('bot','web'),window,projection,b,locks)==0,'public_parking_staging_failed');status='public_assets_staged';results=[]
            else:
                staged=b.document(root/'controller-stage-result.json');need(staged.get('schema')=='security_legacy_parking_result/1' and staged.get('nonce')==nonce and staged.get('status')=='public_assets_staged' and staged.get('profile_sha256')==args.profile_sha256 and staged.get('window_sha256')==args.window_sha256 and staged.get('canonical_reservation_removed') is True,'incomplete_public_stage_not_adopted')
                need(args.mode=='apply' and not os.path.lexists(root/'controller-journal.json') and not os.path.lexists(root/'controller-result.json'),'parking_attempt_not_adopted');results=[]
                need(invoke(args,'check',('bot','web'),window,projection,b,locks)==0,'parking_preflight_failed')
                journal_binding=None
                for identification in target_order(window):
                    checked_context(window,profile,app,datetime.now(timezone.utc))
                    for check in checks:check()
                    need(invoke(args,'check',('bot','web'),window,projection,b,locks)==0,'parking_peer_preflight_failed')
                    site=TARGETS[identification][0];need(not os.path.lexists(root/'results'/(site+'-'+identification+'.json')),'existing_target_result_not_adopted')
                    journal_binding=write(b,root/'controller-journal.json',{'schema':'security_legacy_parking_journal/1','nonce':nonce,'phase':'target_rpc_outcome_not_yet_known','target':identification,'projection':projection,'legacypark_FINAL':False,'security_accepted':False},exclusive=journal_binding is None,expected=journal_binding)
                    requested=True;need(invoke(args,'target',(site,),window,projection,b,locks,target=identification)==0,'parking_target_outcome_unknown')
                    value=b.document(root/'results'/(site+'-'+identification+'.json'));need(value.get('schema')=='security_legacy_parking_target_result/1' and value.get('nonce')==nonce and value.get('id')==identification and value.get('action')==window['action'] and value.get('status')=='known_target_verified' and value.get('legacypark_FINAL') is False and value.get('security_accepted') is False,'parking_target_result_binding')
                    desired={'running':window['action']=='restore','restart_policy':NO if window['action']=='park' else parent[identification]['restart_policy']};need(equal(value.get('projection'),desired),'parking_target_result_projection');projection[identification]=desired;results.append(value)
                need(invoke(args,'check',('bot','web'),window,projection,b,locks)==0,'parking_final_peer_check_failed');status='reversibly_parked' if window['action']=='park' else 'owned_intent_restored'
            for check in checks:check()
            remove_guard(b.RESERVATION,guard_check)
            result={'schema':'security_legacy_parking_result/1','operation_id':OP,'nonce':nonce,'action':window['action'],'status':status,'profile_sha256':args.profile_sha256,'app_result_sha256':args.app_sha256,'window_sha256':args.window_sha256,'projection':projection,'targets':results,'original_target_intents':{r['id']:r for s in profile['sites'].values() for r in s['runtime'] if r['id'] in TARGETS},'canonical_reservation_removed':True,'permanent_source_mutex_preserved':True,'web_c07_fences_recreated':False,'known_driver_scope':'canonical_deployment_locks_stop_and_observed_systemd','arbitrary_external_root_automation_absence_proven':False,'clean_stopped_backup_restore_coverage_proven':False,'data_deletion_authorized':False,'legacypark_FINAL':False,'security_accepted':False}
            # Receipt is last, still under both permanent mutexes. A failed
            # publication never authorizes another action or auto rollback.
            try:write(b,root/('controller-stage-result.json' if args.mode=='stage' else 'controller-result.json'),result)
            except Exception:return {**result,'status':'known_outcome_receipt_pending','receipt_written':False}
            return result
        except BaseException:
            # Public stage/preflight can clean only its exact freshly owned
            # reservation. A submitted target RPC keeps the durable fence.
            if not requested and os.path.lexists(b.RESERVATION):remove_guard(b.RESERVATION,guard_check)
            raise

def reason(error):
    return error.args[0].replace('-','_') if type(error) in TRUSTED and len(error.args)==1 and type(error.args[0]) is str and re.fullmatch('[a-z][a-z0-9_-]{0,79}',error.args[0]) else 'parking_incomplete_or_unknown'
def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--role',choices=('coordinate','worker'),default='coordinate');parser.add_argument('--mode',choices=('check','stage','apply','target'),default='check');parser.add_argument('--site',choices=('bot','web'));parser.add_argument('--target');parser.add_argument('--projection',default='{}');parser.add_argument('--directory',type=Path);parser.add_argument('--window',type=Path,required=True);parser.add_argument('--window-sha256',required=True);parser.add_argument('--profile',type=Path,required=True);parser.add_argument('--profile-sha256',required=True);parser.add_argument('--app-result',type=Path,required=True);parser.add_argument('--app-sha256',required=True);parser.add_argument('--primitives',type=Path,required=True);parser.add_argument('--parent',type=Path);parser.add_argument('--parent-sha256');parser.add_argument('--confirm');args=parser.parse_args()
    resource.setrlimit(resource.RLIMIT_CORE,(0,0));os.umask(0o077)
    try:result=worker(args) if args.role=='worker' else coordinate(args);print(json.dumps({'status':result['status'],'legacypark_FINAL':False,'security_accepted':False},sort_keys=True));return 1 if result['status']=='known_outcome_receipt_pending' else 0
    except Exception as error:
        print(json.dumps({'status':'incomplete_or_unknown','phase':PHASE,'code':reason(error),'rpc_retry_allowed':False,'legacypark_FINAL':False,'security_accepted':False},sort_keys=True));return 1
if __name__=='__main__':raise SystemExit(main())
