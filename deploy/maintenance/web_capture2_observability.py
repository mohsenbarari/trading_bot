#!/usr/bin/env python3
"""Finite, explicit normal-Compose capture2 diagnostic rollout; default is read-only."""
from contextlib import ExitStack, redirect_stdout
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import argparse
import base64
import fcntl
import io
import json
import os
from pathlib import Path
import re
import signal
import stat
import time
from types import ModuleType

PARK_SHA = '83650494adc141234c447f8020f11b0cce4a2a8e419a00ae1141b29e92c4a928'
QA_SHA = '9703ddf97439fd1578a4137ce3ad0e38d48c2c1acf0cf891b588527494982272'
HOT_SHA = 'cb3e03503a0bddae0d7bd7d31f615606d2645b0f6f9b10dfa7468fef108995aa'
PARITY_SHA = '6a7f502d85ecd58a49f1aa4209674c40a4a21b0b4d198429e43b5f49af6605b2'
PARK_AUDIT_SHA = 'd9ddd4d3c1c5771f20014d1d986bae720e82439ff07a1869653c1174ea465e9e'
PUBLIC_FILES = {'compose.yml':'38a9191e4f55799f1f8193b88136993e01758c1d1dcac6e8fbff03ed731fec5b',
                'compose.web.yml':'25a794f6f9f4303c96adb69b35eeb19af86d153fb57f0ff87307f72797937cd5'}
OP = 'security-capture2-observability-20261024-04'
PREVIOUS_OP = 'security-capture2-observability-20261024-02'
PREVIOUS_AUDIT_SHA = 'ea4c02596b1a96eb6ee4fecfb0d8fc15686ce3056a84a31a4b95e5f321029246'
TASK_ROOT = Path('/root/.local/' + OP + '-activation')
CONFIRM = 'ACTIVATE-ONLY-CAPTURE2-SAFE-ORIGIN-DIAGNOSTIC-20261024-04'
SESSION = '/srv/trading-bot/market-data-staging-shadow/sessions/account2'
TOKEN = Path('/run/trading-bot/market-capture-legacy-authority-enabled')
COLLECTORS = ('coin-capture.service','market-channel-capture.service')
GUARD_BODY = b'[Unit]\nConditionPathExists=/run/trading-bot/market-capture-legacy-authority-enabled\nRefuseManualStart=yes\n'
GUARD_HEADER = b'# Owned Web boot guards 2026-10-24; exact-content cleanup only.\n'
INSPECT = ('{"id":{{json .Id}},"name":{{json .Name}},"image":{{json .Image}},'
           '"config":{{json .Config}},"host":{{json .HostConfig}},"mounts":{{json .Mounts}},'
           '"networks":{{json .NetworkSettings.Networks}},"running":{{json .State.Running}},'
           '"restarting":{{json .State.Restarting}},"pid":{{json .State.Pid}},'
           '"started":{{json .State.StartedAt}},"restarts":{{json .RestartCount}}}')

def require(ok, code):
    if not ok:
        raise RuntimeError(code)

def reviewed(raw, digest, name, initial=None):
    data = raw.encode()
    require(0 < len(data) <= 65536 and sha256(data).hexdigest() == digest, 'reviewed_capture_source_changed')
    module = ModuleType(name)
    module.__dict__.update(initial or {})
    exec(compile(data,'<'+name+'>','exec'),module.__dict__)
    return module

def code(error):
    value = str(error) if isinstance(error, RuntimeError) else ''
    return value if re.fullmatch(r'[a-z0-9_]{1,96}',value) else 'capture_diagnostic_operation_failed'

class Clock:
    def __init__(self):
        self.started = time.monotonic()
        self.rollback = False
        self.deadline_utc = datetime.now(timezone.utc) + timedelta(seconds=900)

    def seconds(self, maximum):
        remaining = self.started + (900 if self.rollback else 540) - time.monotonic()
        require(remaining > 0,'capture_repair_clock_expired')
        return min(maximum,remaining)

def command(V, clock, argv, seconds=30):
    result, output, error = V.bounded(argv,seconds=clock.seconds(seconds))
    require(result == 0,'trusted_capture_command_failed')
    return output

def snapshot(V, clock, *, allow_absent_target=False):
    ids = command(V,clock,[V.DOCKER,'ps','-aq','--no-trunc']).decode().splitlines()
    require(len(ids) == len(set(ids)) and len(ids) in ({26,27} if allow_absent_target else {27})
            and all(re.fullmatch(r'[0-9a-f]{64}',v) for v in ids),'capture_inventory_changed')
    # Never print, hash, serialize or persist the private configuration buffers.
    return {ident:json.loads(command(V,clock,[V.DOCKER,'inspect','--format',INSPECT,ident])) for ident in ids}

def same_session_owner(rows, allowed):
    wanted = Path(SESSION)
    for ident,row in rows.items():
        if ident == allowed or not (row['running'] or row['restarting']):
            continue
        require((row['config'].get('Labels') or {}).get('com.docker.compose.service') != 'market-capture-account2','duplicate_capture_role')
        for mount in row['mounts']:
            source = Path(mount['Source'])
            require(source.is_absolute() and str(source) == os.path.normpath(str(source)),'noncanonical_container_mount')
            require(not (source == wanted or source in wanted.parents or wanted in source.parents),'other_session_mount_owner')

def legacy(C):
    import dbus
    require(not os.path.lexists(TOKEN),'legacy_authority_token_present')
    bus = dbus.SystemBus(private=True)
    result = {'persistent_boot_guard_verified':True}
    held = []
    try:
        manager = dbus.Interface(bus.get_object('org.freedesktop.systemd1','/org/freedesktop/systemd1'),'org.freedesktop.systemd1.Manager')
        for name in COLLECTORS:
            manager.RefUnit(name,timeout=5)
            held.append(name)
            obj = manager.GetUnit(name,timeout=5)
            props = dbus.Interface(bus.get_object('org.freedesktop.systemd1',obj),'org.freedesktop.DBus.Properties')
            unit = lambda key: props.Get('org.freedesktop.systemd1.Unit',key,timeout=5)
            require(str(unit('LoadState')) == 'loaded' and str(unit('UnitFileState')) == 'disabled'
                    and str(unit('ActiveState')) in {'inactive','failed'} and bool(unit('RefuseManualStart'))
                    and not bool(unit('NeedDaemonReload')),'legacy_collector_guard_changed')
            require(any(list(row[:4]) == ['ConditionPathExists',False,False,str(TOKEN)] for row in unit('Conditions')),'legacy_condition_missing')
            persistent = Path('/etc/systemd/system')/(name+'.d')/'90-security-20261024-web-boot.conf'
            runtime = Path('/run/systemd/system')/(name+'.d')/'market-container-authority.conf'
            files = {}
            require(C.B.read(runtime,maximum=8192,private=False) == GUARD_BODY,'runtime_legacy_inhibitor_changed')
            files[str(runtime)] = list(C.B.identity(runtime.lstat()))
            if os.path.lexists(persistent):
                require(C.B.read(persistent,maximum=8192,private=False) == GUARD_HEADER+GUARD_BODY,'persistent_legacy_inhibitor_changed')
                files[str(persistent)] = list(C.B.identity(persistent.lstat()))
            else:
                result['persistent_boot_guard_verified'] = False
            require(set(map(str,unit('DropInPaths'))) == set(files),'legacy_dropin_inventory_changed')
            result[name] = {'active':str(unit('ActiveState')),'files':files}
    finally:
        for name in reversed(held):
            try:
                manager.UnrefUnit(name,timeout=5)
            except Exception:
                pass
        bus.close()
    return result

def compose(V, *tail, rollback=False):
    argv = [V.DOCKER,'compose','--project-name',V.PROJECT,'--project-directory',str(V.ROOT),
            '--env-file',str(V.ROLE_ENV),'-f',str(V.ROOT/'compose.yml'),'-f',str(V.ROOT/'compose.web.yml'),
            '-f',str(V.ROOT/'capture2-image-only.yml')]
    if rollback:
        argv.extend(['-f',str(TASK_ROOT/'rollback.yml')])
    return [*argv,'--profile','web',*tail,V.SERVICE]

def target(rows, V):
    selected = [row for row in rows.values() if row['name'] == '/'+V.PROJECT+'-'+V.SERVICE+'-1']
    require(len(selected) == 1,'capture_target_not_unique')
    row = selected[0]
    require(row['image'] in {V.BASE,V.CANDIDATE} and row['config']['Labels'].get('com.docker.compose.project') == V.PROJECT
            and row['config']['Labels'].get('com.docker.compose.service') == V.SERVICE,'capture_target_scope_changed')
    require(any(m['Source'] == SESSION and m['Destination'] == '/var/lib/market-data/session' and m['Type'] == 'bind' and m['RW'] for m in row['mounts']),'capture_session_mount_changed')
    return row

STATE_FIELDS = frozenset({'pid','started','restarts','running','restarting'})

def canonical_mount_records(records):
    require(isinstance(records,list) and all(isinstance(row,dict) and isinstance(row.get('Destination'),str)
            and row['Destination'].startswith('/') for row in records),'mount_records_invalid')
    require(len({row['Destination'] for row in records}) == len(records),'duplicate_mount_destination')
    # Docker serializes its MountPoints map into this list in arbitrary order.
    # Retain every field/value of every full record; normalize only list order.
    return sorted(records,key=lambda row:row['Destination'])

def other_runtime(before, after, original, replacement=None, *, known_incidents=frozenset()):
    expected = set(before)-{original}
    require(set(after)-({replacement} if replacement else {original}) == expected,'other_container_inventory_changed')
    for ident in expected:
        old,new = ({**row,'mounts':canonical_mount_records(row['mounts'])} for row in (before[ident],after[ident]))
        if ident in known_incidents:
            require({k:v for k,v in old.items() if k not in STATE_FIELDS} == {k:v for k,v in new.items() if k not in STATE_FIELDS},'other_container_runtime_changed')
        else:
            require(old == new,'other_container_runtime_changed')

def incident_deltas(before, after, identifiers):
    return [{'id':ident,'incident_status':'open','before':{k:before[ident][k] for k in sorted(STATE_FIELDS)},
             'after':{k:after[ident][k] for k in sorted(STATE_FIELDS)}} for ident in sorted(identifiers)
            if any(before[ident][k] != after[ident][k] for k in STATE_FIELDS)]

def current_incident_profile(plan,V):
    selected = [row for row in plan['containers'] if row['id'] == V.CID]
    require(len(selected) == 1 and selected[0]['name'] == '/'+V.PROJECT+'-'+V.SERVICE+'-1'
            and selected[0]['image'] == V.BASE,'original_capture_anchor_invalid')
    # This is a typed CURRENT inspection profile, never the historical plan.
    # Only this capture identity changes; original document/SHA remains intact.
    return {**plan,'schema':'capture2_current_inventory/1','operation_id':OP,
        'original_operation_id':plan['operation_id'],
        'containers':[{**row,'id':V.AFTER_CAPTURE02_ID} if row['id'] == V.CID else row for row in plan['containers']]}

def capture_readiness(C,P,R,H,profile):
    # This operation never stops/changes a datastore or takes a backup. Normal
    # healthcheck ExecIDs are permitted; owned HOT activity is still excluded.
    R.hot_finished(C,H)
    P.inventory(C,R,profile,parked=True)
    P.web_datastores_native(C,R,profile)

def post_parity(V,before,after,desired, *, replaced=None):
    require(after['id'] != before['id'] and after['image'] == desired,'capture_replacement_identity_invalid')
    old_cfg,new_cfg = before['config'],after['config']
    require(set(old_cfg) == set(new_cfg),'capture_config_schema_changed')
    require(V.environment(old_cfg['Env']) == V.environment(new_cfg['Env']),'capture_private_environment_changed')
    skipped = {'Env','Image','Hostname','Labels'}
    require({k:v for k,v in old_cfg.items() if k not in skipped} == {k:v for k,v in new_cfg.items() if k not in skipped},'capture_application_config_changed')
    require(new_cfg['Hostname'] == after['id'][:12] and new_cfg['Image'] == desired,'capture_replacement_default_changed')
    added = {'io.gold-trade.observability.base-image':V.BASE,'io.gold-trade.observability.source-sha256':V.PATCHED_SOURCE,
             'io.gold-trade.observability.status':'diagnostic_not_security_acceptance'} if desired == V.CANDIDATE else {}
    labels = lambda row: {k:v for k,v in row.items() if not k.startswith('com.docker.compose.')}
    require(labels(new_cfg['Labels']) == {**labels(old_cfg['Labels']),**added},'capture_application_labels_changed')
    old_engine = {k:v for k,v in old_cfg['Labels'].items() if k.startswith('com.docker.compose.')}
    new_engine = {k:v for k,v in new_cfg['Labels'].items() if k.startswith('com.docker.compose.')}
    replace = 'com.docker.compose.replace'
    require(new_engine.get('com.docker.compose.version') == '2.40.3','capture_compose_version_unverified')
    if replace in old_engine:
        require(old_engine.get('com.docker.compose.version') == '2.40.3'
                and old_engine.pop(replace) == V.SERVICE+'-1','capture_prior_replace_provenance_invalid')
    if replace in new_engine:
        require(replaced is not None and new_engine.pop(replace) == V.SERVICE+'-1','capture_replace_provenance_invalid')
    changed = {'com.docker.compose.config-hash','com.docker.compose.image','com.docker.compose.project.config_files',
               'com.docker.compose.project.working_dir','com.docker.compose.project.environment_file','com.docker.compose.version'}
    require(set(new_engine) == set(old_engine) and {k:v for k,v in new_engine.items() if k not in changed} == {k:v for k,v in old_engine.items() if k not in changed},'capture_compose_identity_changed')
    paths = [V.ROOT/'compose.yml',V.ROOT/'compose.web.yml',V.ROOT/'capture2-image-only.yml']
    if desired == V.BASE:
        paths.append(TASK_ROOT/'rollback.yml')
    require(new_engine['com.docker.compose.image'] == desired
            and new_engine['com.docker.compose.project.config_files'] == ','.join(map(str,paths))
            and new_engine['com.docker.compose.project.working_dir'] == str(V.ROOT)
            and new_engine['com.docker.compose.project.environment_file'] == str(V.ROLE_ENV)
            and re.fullmatch(r'[0-9a-f]{64}',new_engine['com.docker.compose.config-hash'])
            and re.fullmatch(r'\d+\.\d+\.\d+(?:[-+][A-Za-z0-9._-]+)?',new_engine['com.docker.compose.version']),'capture_new_provenance_invalid')
    require(before['host'] == after['host'],'capture_host_configuration_changed')
    require(canonical_mount_records(before['mounts']) == canonical_mount_records(after['mounts']),'capture_mounts_changed')
    require(set(before['networks']) == set(after['networks']),'capture_network_membership_changed')
    for name,old in before['networks'].items():
        new = after['networks'][name]
        dynamic = {'EndpointID','IPAddress','MacAddress','GlobalIPv6Address','DNSNames'}
        require(set(old) == set(new) and {k:v for k,v in old.items() if k not in dynamic} == {k:v for k,v in new.items() if k not in dynamic},'capture_network_configuration_changed')
        require(not old.get('IPAMConfig') and not new.get('IPAMConfig'),'capture_static_network_unreviewed')
        for row,ident in [(old,before['id']),(new,after['id'])]:
            dns = row.get('DNSNames')
            require(dns is None or set(dns) == set(row['Aliases'])|{ident[:12]},'capture_network_dns_changed')

def stop_and_start(C,V,clock,before,desired, *, known_incidents=frozenset(),diagnostic_sink=None):
    baseline_id = target(before,V)['id']
    current = snapshot(V,clock,allow_absent_target=desired==V.BASE)
    if len(current) == 26:
        require(desired == V.BASE and set(current) == set(before)-{baseline_id},'absent_capture_rollback_scope_invalid')
        stopped = current
        replaced = None
        other_runtime(before,stopped,baseline_id,known_incidents=known_incidents)
    else:
        old = target(current,V)
        replaced = old['id']
        command(V,clock,compose(V,'stop','--timeout','30'),seconds=60)
        stopped = snapshot(V,clock)
        stopped_target = target(stopped,V)
        require(stopped_target['id'] == old['id'] and not stopped_target['running'] and not stopped_target['restarting']
                and stopped_target['pid'] == 0,'old_session_owner_not_stopped')
        other_runtime(before,stopped,baseline_id,old['id'],known_incidents=known_incidents)
    same_session_owner(stopped,None)
    legacy(C)
    command(V,clock,compose(V,'up','--detach','--no-deps','--no-build','--pull','never','--force-recreate',rollback=desired==V.BASE),seconds=180)
    after = snapshot(V,clock)
    new = target(after,V)
    if desired == V.CANDIDATE and diagnostic_sink is not None:
        require(new['image'] == V.CANDIDATE,'candidate_diagnostic_image_invalid')
        diagnostic_sink(new['id'],poll_diagnostics(V,clock,new['id']))
    other_runtime(before,after,baseline_id,new['id'],known_incidents=known_incidents)
    post_parity(V,before[baseline_id],new,desired,replaced=replaced)
    same_session_owner(after,new['id'])
    require(command(V,clock,[V.DOCKER,'image','inspect','--format','{{.Id}}',V.BASE]).decode().strip() == V.BASE,'rollback_image_missing')
    return new,after

def diagnostics(V,clock,ident):
    # Both Docker streams remain bounded RAM. Only this exact static schema can
    # leave the process; no raw log line, error message, trace or provider text.
    status,out,err = V.bounded([V.DOCKER,'logs','--tail','60',ident],seconds=clock.seconds(15),maximum=262144)
    require(status == 0,'capture_diagnostic_log_read_failed')
    types = {'OSError','ValueError','FileNotFoundError','PermissionError','NotADirectoryError','IsADirectoryError','FileExistsError',
        'TimeoutError','ConnectionError','ConnectionResetError','ConnectionRefusedError','ConnectionAbortedError','BrokenPipeError',
        'BlockingIOError','InterruptedError','UnicodeDecodeError','Error','DatabaseError','OperationalError','IntegrityError',
        'ProgrammingError','InterfaceError','DataError','NotSupportedError','OtherDependencyError'}
    origins = {'pipeline_foundation','private_capture','capture_service','telegram_capture','telethon_sqlite_session',
               'telethon_client','telethon_auth','telethon_connection','other_runtime','trace_depth_limit'}
    found = []
    for raw in (out+err).splitlines():
        if len(raw) > 384:
            continue
        try:
            row = json.loads(raw)
        except (ValueError,UnicodeError):
            continue
        if (isinstance(row,dict) and set(row) == {'status','reason_code','error_type','origin','origin_line'}
            and row['status'] == 'fail' and row['reason_code'] == 'runtime_dependency_failure'
            and isinstance(row['error_type'],str) and row['error_type'] in types
            and isinstance(row['origin'],str) and row['origin'] in origins and type(row['origin_line']) is int
            and 0 <= row['origin_line'] <= 1000000 and row not in found):
            found.append(row)
    return found[:4]

def poll_diagnostics(V,clock,ident):
    deadline = time.monotonic()+clock.seconds(60)
    while True:
        found = diagnostics(V,clock,ident)
        if found or time.monotonic() >= deadline:
            return found
        time.sleep(min(2,max(0,deadline-time.monotonic())))

def execute(P,R,H,V,action):
    clock = Clock()
    C = H.controller()
    plan = C.P.document(H.OLD_ROOT/'plan.json',H.PLAN_SHA)
    parking_path = C.ROOT/(H.OP+'.qa-parking.json')
    parking_raw = C.B.read(parking_path,maximum=65536)
    parking = json.loads(parking_raw)
    require(sha256(parking_raw).hexdigest() == PARK_AUDIT_SHA and parking['phase'] == 'qa_parked_production_incident_open'
            and parking['qa_parking_verified'] is True and parking['old_fences_retained'] is True
            and parking['parked_ids'] == sorted(R.IDS) and parking['security_transition_completed'] is False,'authentic_parking_required')
    unit = 'security-web-qa-parking-20261024-01.service'
    state = dict(line.split('=',1) for line in C.B.command(['/usr/bin/systemctl','show',unit,'--property=ActiveState,MainPID,ExecMainStatus']).splitlines())
    require(state == {'ActiveState':'failed','MainPID':'0','ExecMainStatus':'3'} and C.scope_empty(Path('/sys/fs/cgroup/system.slice')/unit),'parking_owner_not_terminal')
    audit_path = C.ROOT/(OP+'.activation.json')
    require(not os.path.lexists(audit_path) and not os.path.lexists(TASK_ROOT),'capture_activation_already_attempted')
    with ExitStack() as stack,R.pinned(C):
        fds,originals = {},{}
        for path in (C.P.MARKER,C.paths(plan)[1],C.P.RESERVATION):
            raw = C.B.read(path,maximum=8192)
            prior = path.lstat()
            fd = os.open(path,os.O_RDWR|os.O_NOFOLLOW|os.O_NONBLOCK)
            stack.callback(os.close,fd)
            require(C.B.identity(prior) == C.B.identity(os.fstat(fd)) == C.B.identity(path.lstat()),'capture_fence_inode_changed')
            fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            fds[path],originals[path] = fd,raw
        P.owner_proof(C,H,plan,fds)
        previous_path = C.ROOT/(PREVIOUS_OP+'.activation.json')
        previous_raw = C.B.read(previous_path,maximum=65536)
        previous = json.loads(previous_raw)
        require(sha256(previous_raw).hexdigest() == PREVIOUS_AUDIT_SHA
                and previous['schema'] == 'capture2_observability_activation/1' and previous['operation_id'] == PREVIOUS_OP
                and previous['phase'] == 'rollback_unverified_incident_open'
                and previous['reason_code'] == previous['rollback_reason_code'] == 'capture_replace_provenance_invalid'
                and previous['old_capture_id'] == V.CID and previous['base_image'] == V.BASE
                and previous['candidate_image'] == V.CANDIDATE and previous['old_fences_retained'] is True
                and previous['security_transition_completed'] is False,'previous_capture_attempt_changed')
        previous_unit = PREVIOUS_OP+'.service'
        previous_state = dict(line.split('=',1) for line in C.B.command(['/usr/bin/systemctl','show',previous_unit,'--property=ActiveState,MainPID,ExecMainStatus']).splitlines())
        require(previous_state == {'ActiveState':'failed','MainPID':'0','ExecMainStatus':'3'}
                and C.scope_empty(Path('/sys/fs/cgroup/system.slice')/previous_unit),'previous_capture_owner_not_terminal')
        refused03 = 'security-capture2-observability-20261024-03'
        refused_state = dict(line.split('=',1) for line in C.B.command(['/usr/bin/systemctl','show',refused03+'.service','--property=ActiveState,MainPID,ExecMainStatus']).splitlines())
        require(refused_state == {'ActiveState':'failed','MainPID':'0','ExecMainStatus':'1'}
                and C.scope_empty(Path('/sys/fs/cgroup/system.slice')/(refused03+'.service'))
                and not os.path.lexists(C.ROOT/(refused03+'.activation.json'))
                and not os.path.lexists(Path('/root/.local')/(refused03+'-activation')),'refused03_not_proven_readonly')
        require(command(V,clock,[V.DOCKER,'compose','version','--short']).decode().strip() == '2.40.3+ds1-0ubuntu1','actual_compose_version_unverified')
        profile = current_incident_profile(plan,V)
        capture_readiness(C,P,R,H,profile)
        native = legacy(C)
        old_evidence = {path:C.B.read(path,maximum=65536) for path in (C.paths(plan)[0],C.ROOT/(H.OP+'.owned-restore.json'),C.ROOT/(H.OP+'.owned-intent.json'),parking_path,previous_path)}
        output = io.StringIO()
        with redirect_stdout(output):
            checked = V.main(existing=True,incident_profile='after_capture02')
        require(checked == 0 and json.loads(output.getvalue())['full_runtime_parity'] is True,'capture_complete_preflight_failed')
        before = snapshot(V,clock)
        known_incidents = frozenset(R.KNOWN_MARKET_FLAPS)
        require(len(known_incidents) == 2 and known_incidents <= set(before) and not known_incidents & (set(R.IDS)|{V.CID}),'known_incident_binding_invalid')
        baseline_id = V.AFTER_CAPTURE02_ID
        require(target(before,V)['id'] == baseline_id and before[baseline_id]['image'] == V.BASE
                and all(row['image'] != V.CANDIDATE for row in before.values()),'current_postrollback_capture_required')
        same_session_owner(before,baseline_id)
        if action == 'check':
            return {'status':'capture_diagnostic_checked','runtime_changed':False,'private_values_emitted':False}
        TASK_ROOT.mkdir(mode=0o700)
        rollback = ('services:\n  '+V.SERVICE+':\n    image: '+V.BASE+'\n').encode()
        fd = os.open(TASK_ROOT/'rollback.yml',os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
        try:
            require(os.write(fd,rollback) == len(rollback),'rollback_public_stage_incomplete')
            os.fsync(fd)
        finally:
            os.close(fd)
        audit = {'schema':'capture2_observability_activation/1','operation_id':OP,'pid':os.getpid(),
            'deadline_utc':clock.deadline_utc.isoformat(),'old_capture_id':baseline_id,'base_image':V.BASE,'candidate_image':V.CANDIDATE,
            'original_c07_capture_id':V.CID,'capture_identity_delta':{'original_id':V.CID,'current_before_id':baseline_id,'reason':'owned02_base_rollback'},
            'source_sha256':V.PATCHED_SOURCE,'public_runtime_revision':V.PUBLIC_REVISION,'parking_audit_sha256':PARK_AUDIT_SHA,
            'previous_operation_id':PREVIOUS_OP,'previous_audit_sha256':PREVIOUS_AUDIT_SHA,
            'prior03_readonly_refusal_proven':True,
            'original_operation_id':H.OP,'original_plan_sha256':H.PLAN_SHA,'old_fences_retained':True,
            'original_manifest_changed':False,'security_transition_completed':False,'phase':'admitted',
            'persistent_boot_guard_verified':native['persistent_boot_guard_verified'],'web_reboot_authorized':False,
            'cleanup_review_due_utc':(datetime.now(timezone.utc)+timedelta(days=10)).isoformat()}
        C.write_json(audit_path,audit,exclusive=True)
        def save_diagnostic(ident,records):
            audit.update(phase='candidate_diagnostic_collected_before_acceptance',observed_candidate_id=ident,diagnostics=records,
                diagnostic_status='fixed_origin_captured' if records else 'diagnostic_not_yet_available',candidate_configuration_accepted=False)
            C.write_json(audit_path,audit,exclusive=False)
        try:
            new,after = stop_and_start(C,V,clock,before,V.CANDIDATE,known_incidents=known_incidents,diagnostic_sink=save_diagnostic)
            require(legacy(C) == native,'legacy_guard_metadata_changed')
            P.web_datastores_native(C,R,profile)
            records = audit['diagnostics']
            after = snapshot(V,clock)
            final = target(after,V)
            require(final['id'] == new['id'],'capture_replaced_during_observation')
            other_runtime(before,after,baseline_id,new['id'],known_incidents=known_incidents)
            post_parity(V,before[baseline_id],final,V.CANDIDATE,replaced=baseline_id)
            same_session_owner(after,new['id'])
            require(legacy(C) == native,'legacy_guard_metadata_changed')
            for path,raw in originals.items():
                require(C.B.read(path,maximum=8192) == raw and C.B.identity(os.fstat(fds[path])) == C.B.identity(path.lstat()),'capture_original_fence_changed')
            require(all(C.B.read(path,maximum=65536) == raw for path,raw in old_evidence.items()),'capture_original_evidence_changed')
            P.owner_proof(C,H,plan,fds)
            audit.update(phase='diagnostic_image_active_incident_open',new_capture_id=new['id'],diagnostics=records,
                diagnostic_status='fixed_origin_captured' if records else 'diagnostic_not_yet_available',
                known_market_incident_ids=sorted(known_incidents),known_market_state_deltas=incident_deltas(before,after,known_incidents),
                capture_role_original_anchor_intentionally_superseded=True,all_other_26_configuration_unchanged=True,
                capture_functional_repair_accepted=False,candidate_configuration_accepted=True,completion_unverified=True)
        except Exception as error:
            audit.update(phase='rollback_pending',reason_code=code(error),completion_unverified=True)
            C.write_json(audit_path,audit,exclusive=False)
            clock.rollback = True
            try:
                restored,after = stop_and_start(C,V,clock,before,V.BASE,known_incidents=known_incidents)
                require(legacy(C) == native,'legacy_guard_metadata_changed')
                audit.update(phase='rolled_back_capture_incident_open',rollback_capture_id=restored['id'],rollback_image_verified=True,
                    known_market_incident_ids=sorted(known_incidents),known_market_state_deltas=incident_deltas(before,after,known_incidents),
                    capture_role_original_anchor_intentionally_superseded=restored['id'] != V.CID)
            except Exception as rollback_error:
                audit.update(phase='rollback_unverified_incident_open',rollback_reason_code=code(rollback_error),rollback_image_verified=False)
        C.write_json(audit_path,audit,exclusive=False)
        return {key:audit[key] for key in ('phase','security_transition_completed','old_fences_retained','completion_unverified')}

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('parking_source','qa_source','hot_source','parity_source'):
        parser.add_argument(name)
    parser.add_argument('action',choices=('check','activate'),nargs='?',default='check')
    parser.add_argument('--confirmation',default='')
    args = parser.parse_args()
    require(args.action == 'check' or args.confirmation == CONFIRM,'explicit_capture_diagnostic_required')
    P = reviewed(args.parking_source,PARK_SHA,'reviewed_parking')
    R = reviewed(args.qa_source,QA_SHA,'reviewed_qa')
    H = reviewed(args.hot_source,HOT_SHA,'reviewed_hot')
    public = {}
    for name,digest in PUBLIC_FILES.items():
        raw = H.read(Path('/root/.local/security-capture2-compose-parity-20261024-04')/name,65536)
        require(sha256(raw).hexdigest() == digest,'public_compose_source_changed')
        public[name] = base64.b64encode(raw).decode()
    V = reviewed(args.parity_source,PARITY_SHA,'reviewed_capture_parity',{'PUBLIC_COMPOSE':public})
    def expired(*_):
        raise RuntimeError('capture_repair_hard_deadline')
    signal.signal(signal.SIGALRM,expired)
    signal.alarm(900)
    result = execute(P,R,H,V,args.action)
    print(json.dumps(result,sort_keys=True))
    return 3 if result.get('completion_unverified') else 0

if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as error:
        print(json.dumps({'status':'capture_diagnostic_refused','reason_code':code(error),'completion_unverified':True}))
        raise SystemExit(1)
