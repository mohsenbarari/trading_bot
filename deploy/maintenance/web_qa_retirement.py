#!/usr/bin/env python3
"""Retire only ten user-authorized obsolete QA containers; no deletion."""
from contextlib import ExitStack, contextmanager
from hashlib import sha256
import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import signal
import stat
import time
from types import ModuleType

HOT_SHA = 'cb3e03503a0bddae0d7bd7d31f615606d2645b0f6f9b10dfa7468fef108995aa'
PROJECT = 'three-site-dc32d90392b0-webapp-fi'
IDS = {
 '5a77d25b37bddbc28c36f7f3e4ddcad7da15ce609e191462d1ac584c525c5ec0': 'webapp_fi_effects',
 'f82b0e9cad3219b3549e7350d023a6eaa4938570523a7cf563e8e4aeaa7b0384': 'webapp_fi_api',
 '26dd7428182e66afd2ce28c85fc78f30f61a2ea323b2fe21b3f7a69892acff95': 'webapp_fi_blobs',
 '061af20a47a4d604f1950da7a27a67f77411b376eaa46899cf5e005f5244c870': 'webapp_fi_dr_delivery',
 '0b1c1ac348b0cc804c04ef1b0a421d161ce1fe262ff43666ba8a9a0bdceec61d': 'webapp_fi_writer_control',
 'ab19a2dca24f4b4de6c5f3e87566af1c3956948b94e3fc2b99579e56a20eb535': 'webapp_fi_dr_projection',
 '6baa54ddf92657de1fd063eefa45084472e66438ff2a4859fbd277fe161bdcdf': 'webapp_fi_dr_receiver',
 'b7f75f523fbbc8783b9affed1cac9b5f030f045036e89670204fe6fee15f77a6': 'webapp_fi_db',
 '596ea9221bb8a471f6718e0df0a82e14b9d7b8ab1299aedbc3a73d8b6511f7fe': 'webapp_fi_dr_tls',
 'e36fc1f59172ad0a60fdd14209379ef1a153467a4642fccf4ef267ca6997f87e': 'webapp_fi_redis',
}
CONFIRM = 'RETIRE-EXACT-TEN-OBSOLETE-QA-CONTAINERS-20261024'
SCOPE_END = '2026-10-24T07:10:00Z'
KNOWN_MARKET_FLAPS = {
 'f2dbc15b9c9a2d14ef9669d1f6de37096161469a50c47bf6609f0383886b3a27': '/market-private-pipeline-primary-market-processor-1',
 'f0f5ddc3efe1a55cc8a6f800ad59d1f41556a4f896cdc8ed7d20864c9d360e2a': '/market-private-pipeline-primary-estimator-snapshot-receiver-1',
}


def require(ok, code):
    if not ok:
        raise RuntimeError(code)


def reviewed_hot(raw):
    raw = raw.encode()
    require(0 < len(raw) <= 65536 and sha256(raw).hexdigest() == HOT_SHA, 'reviewed_hot_source_changed')
    module = ModuleType('_reviewed_hot_ownership_proof')
    exec(compile(raw, '<reviewed-hot-proof>', 'exec'), module.__dict__)
    return module


def scope_remaining(C, minimum):
    end = C.datetime.fromisoformat(SCOPE_END.replace('Z', '+00:00'))
    remaining = (end - C.datetime.now(C.timezone.utc)).total_seconds()
    require(remaining > 0 and remaining >= minimum, 'independent_qa_scope_window_exhausted')
    return end


def scope_owner_proof(C, H, plan, fds):
    # This new user-authorized, no-production-downtime task has its own clock.
    # Do not call/patch the expired old transaction's native_contract or alter
    # its 07:00 deadline/manifest. Independently prove the same native host.
    scope_remaining(C, 0)
    require(os.geteuid() == 0 and Path('/proc/1/exe').readlink().name == 'systemd', 'native_root_required')
    require(not any(Path(v).exists() for v in ('/.dockerenv', '/run/.containerenv', '/run/systemd/container')), 'container_refused')
    for ns in ('pid', 'mnt', 'user'):
        require(os.readlink('/proc/self/ns/' + ns) == os.readlink('/proc/1/ns/' + ns), 'namespace_mismatch')
    distro = dict(line.split('=', 1) for line in Path('/etc/os-release').read_text().splitlines() if '=' in line)
    require(distro.get('ID', '').strip('"') == 'ubuntu' and distro.get('VERSION_ID', '').strip('"') == '26.04', 'distro_mismatch')
    require(Path('/etc/machine-id').read_text().strip() == plan['machine_id'], 'host_changed')
    require(plan['operation_id'] == H.OP and plan['nonce'] == H.NONCE, 'wrong_original_plan')
    source, reservation = (C.B.document(path) for path in (C.paths(plan)[1], C.P.RESERVATION))
    require(source == reservation and source['pid'] == H.OWNER and source['schema'] == 'web_engine_transaction/1'
            and source['operation_id'] == H.OP and source['nonce'] == H.NONCE and source['manifest_sha256'] == H.PLAN_SHA,
            'wrong_retained_owner')
    require(not os.path.lexists('/proc/' + str(H.OWNER)), 'previous_owner_alive')
    audit = C.B.document(C.ROOT / (H.OP + '.owned-intent.json'))
    previous = C.ROOT / (H.OP + '.owned-restore.json')
    require(audit['schema'] == 'web_engine_owned_intent/1' and audit['operation_id'] == H.OP and audit['nonce'] == H.NONCE
            and audit['manifest_sha256'] == H.PLAN_SHA and audit['intent_owner_pid'] == H.OWNER
            and audit['phase'] == 'intent_restored_health_incomplete' and audit['security_transition_completed'] is False
            and sha256(C.B.read(previous)).hexdigest() == audit['previous_restore_audit_sha256'], 'owned_audit_changed')
    originals = C.B.document(previous)['original_fences']
    require(len(originals) == 2 and {r['path'] for r in originals} == {str(C.paths(plan)[1]), str(C.P.RESERVATION)}, 'original_fence_inventory')
    for row in originals:
        value = os.fstat(fds[Path(row['path'])])
        require((value.st_dev, value.st_ino) == (row['device'], row['inode'])
                and {**row['payload'], 'pid': H.OWNER} == source, 'original_fence_changed')
    unit = 'security-web-engine-07-owned-intent-' + H.NONCE + '.service'
    state = dict(line.split('=', 1) for line in C.B.command(['/usr/bin/systemctl', 'show', unit,
        '--property=ActiveState,MainPID,ExecMainStatus']).splitlines())
    require(state == {'ActiveState': 'failed', 'MainPID': '0', 'ExecMainStatus': '3'}
            and C.scope_empty(Path('/sys/fs/cgroup/system.slice/' + unit)), 'previous_continuation_not_terminal')
    journal = C.journal(plan, H.PLAN_SHA)
    require(journal['phase'] == 'writers_quiesced' and journal['pid'] == 2110733, 'original_journal_changed')
    require(not os.path.lexists(C.P.PENDING) and not any(os.path.lexists(p) for p in plan['absent_control_paths']), 'foreign_operation')
    marker = plan['marker']
    require(C.P.file_digest(C.P.MARKER, maximum=8192, private=True) == (marker['size'], marker['sha256'])
            and (C.P.MARKER.stat().st_dev, C.P.MARKER.stat().st_ino) == (marker['device'], marker['inode']), 'marker_changed')
    env = Path(plan['market_backup']['env_file'])
    C.B.private_parent(env)
    info = env.lstat()
    require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and info.st_nlink == 1 and not info.st_mode & 0o077
            and list(C.B.identity(info)) == plan['market_backup']['env_file_identity'], 'env_metadata_changed')
    C.market_anchor_contract(plan)


@contextmanager
def pinned(C):
    import dbus
    bus = dbus.SystemBus(private=True)
    held = []
    try:
        manager = dbus.Interface(bus.get_object('org.freedesktop.systemd1', '/org/freedesktop/systemd1'),
                                 'org.freedesktop.systemd1.Manager')
        for name in sorted(C.UNITS | set(C.ENGINES)):
            manager.RefUnit(name, timeout=5)
            held.append(name)
            obj = manager.GetUnit(name, timeout=5)
            props = dbus.Interface(bus.get_object('org.freedesktop.systemd1', obj), 'org.freedesktop.DBus.Properties')
            require(str(props.Get('org.freedesktop.systemd1.Unit', 'LoadState', timeout=5)) == 'loaded', 'native_unit_unloaded')
        yield
    finally:
        for name in reversed(held):
            try:
                manager.UnrefUnit(name, timeout=5)
            except Exception:
                pass
        bus.close()


def hot_finished(C, H):
    unit = 'security-market-hot-preflight-20261024-01.service'
    state = dict(line.split('=', 1) for line in C.B.command(['/usr/bin/systemctl', 'show', unit,
        '--property=LoadState,ActiveState,MainPID']).splitlines())
    require(state.get('MainPID') == '0' and state.get('ActiveState') in {'inactive', 'failed'}, 'hot_backup_still_active')
    require(C.scope_empty(Path('/sys/fs/cgroup/system.slice/' + unit)), 'hot_backup_descendants_present')
    # Exact Docker inventory below excludes every leftover restore container.
    # Volume inventory also refuses any labelled restore volume left by HOT.
    names = C.B.command(['/usr/bin/docker', 'volume', 'ls', '--filter', 'label=io.gold-trade.market-backup-run', '-q']).splitlines()
    require(not names, 'owned_restore_volume_cleanup_unverified')
    if os.path.lexists(H.TARGET / 'market-pipeline-backup-journal.json'):
        journal = C.B.document(H.TARGET / 'market-pipeline-backup-journal.json')
        resources = journal.get('restore_resources')
        if resources:
            require(set(resources) == {'container', 'volume', 'label'}, 'hot_resource_binding_invalid')
            require(re.fullmatch(r'market_pipeline_restore_[0-9a-f]{16}', resources['container'])
                    and resources['volume'] == resources['container'] + '_data', 'hot_resource_name_invalid')
            for kind in ('container', 'volume'):
                observed = C.B.command(['/usr/bin/docker', kind, 'ls', '-q', '--filter', 'name=' + resources[kind]]) if kind == 'volume' else C.B.command(
                    ['/usr/bin/docker', 'ps', '-aq', '--filter', 'name=^/' + resources[kind] + '$'])
                require(not observed, 'hot_owned_resource_still_present')


def exact_scope(plan):
    require(len(plan['containers']) == 27 and len(IDS) == 10, 'original_inventory_changed')
    selected = [row for row in plan['containers'] if row['id'] in IDS]
    require(len(selected) == 10 and all(row['project'] == PROJECT and row['name'] == '/' + PROJECT + '-' + IDS[row['id']] + '-1'
            for row in selected), 'approved_qa_identity_changed')
    stores = set(plan['datastore_ids']) & set(IDS)
    require(len(stores) == 2 and {IDS[ident] for ident in stores} == {'webapp_fi_db', 'webapp_fi_redis'}, 'approved_qa_datastores_changed')
    require(sum(row['running'] and row['id'] not in IDS for row in plan['containers']) == 16, 'retained_running_scope_changed')
    names = {row['id']: row['name'] for row in plan['containers']}
    require(all(names.get(ident) == name for ident, name in KNOWN_MARKET_FLAPS.items()), 'known_market_incident_identity_changed')
    return selected, stores


def inventory(C, plan, *, retired):
    expected = {row['id']: row for row in plan['containers']}
    require(set(C.B.command(['/usr/bin/docker', 'ps', '-aq', '--no-trunc']).splitlines()) == set(expected), 'container_inventory_changed')
    degraded = []
    for ident, before in expected.items():
        row = json.loads(C.B.command(['/usr/bin/docker', 'inspect', '--format', C.INSPECT, ident]))
        require(all(row[k] == before[k] for k in ('id', 'name', 'image', 'project', 'network_mode', 'port_bindings'))
                and sorted(row['network_names']) == sorted(before['network_names'])
                and C.P.canonical_mounts(row['mounts']) == C.P.canonical_mounts(before['mounts']), 'container_identity_changed')
        policy = {'Name': 'no', 'MaximumRetryCount': 0} if retired and ident in IDS else before['restart_policy']
        require(row['restart_policy'] == policy, 'retirement_policy_changed')
        if ident not in IDS:
            require(row['running'] == before['running'], 'production_running_intent_changed')
            if row['running'] and row['health'] not in {None, 'healthy'}:
                degraded.append(ident)
        elif retired:
            require(not row['running'], 'approved_qa_still_running')
            require(C.B.command(['/usr/bin/docker', 'inspect', '--format', '{{json .State.Restarting}}', ident]) == 'false', 'approved_qa_still_restarting')
    return degraded


def preserved(C, selected):
    volumes = {}
    actual_mounts = []
    for row in selected:
        mounts = json.loads(C.B.command(['/usr/bin/docker', 'inspect', '--format', '{{json .Mounts}}', row['id']]))
        require(C.P.canonical_mounts(mounts) == C.P.canonical_mounts(row['mounts']), 'qa_mount_identity_changed')
        actual_mounts.extend(mounts)
        for mount in mounts:
            if mount['Type'] == 'volume':
                name = mount.get('Name')
                require(isinstance(name, str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,200}', name), 'qa_volume_name_invalid')
                require(name not in volumes or volumes[name] == mount['Source'], 'qa_volume_source_alias')
                volumes[name] = mount['Source']
    require(len(volumes) == 4, 'qa_volume_inventory_changed')
    volume_records = {}
    for name in sorted(volumes):
        record = json.loads(C.B.command(['/usr/bin/docker', 'volume', 'inspect', '--format',
            '{"name":{{json .Name}},"driver":{{json .Driver}},"mountpoint":{{json .Mountpoint}},"labels":{{json .Labels}}}', name]))
        require(record['name'] == name and record['mountpoint'] == volumes[name], 'qa_volume_binding_changed')
        info = Path(record['mountpoint']).stat()
        volume_records[name] = {'record': record, 'device': info.st_dev, 'inode': info.st_ino}
    secrets = {}
    for mount in actual_mounts:
        if mount['Type'] == 'bind':
            path = Path(mount['Source'])
            value = path.lstat()
            require(not path.is_symlink(), 'qa_secret_metadata_unsafe')
            if stat.S_ISREG(value.st_mode):
                secrets[str(path)] = list(C.B.identity(value))
    for image in {row['image'] for row in selected}:
        require(C.B.command(['/usr/bin/docker', 'image', 'inspect', '--format', '{{.Id}}', image]) == image, 'qa_image_missing')
    return {'volumes': volume_records, 'secret_metadata': secrets}


def production(C, plan):
    stores = [probe for probe in plan['datastore_probes'] if probe['id'] not in IDS]
    require(len(stores) == 6, 'retained_datastores_changed')
    C.old_datastores_and_api_ready({**plan, 'datastore_probes': stores, 'health_probes': []})
    for probe in plan['health_probes']:
        require(probe['id'] not in IDS, 'production_probe_points_to_retired_qa')
        require(C.B.command(['/usr/bin/docker', 'exec', probe['id'], '/usr/local/bin/python', '-I', '-B', '-c',
            C.B.CONTAINER_HTTP_PROBE, probe['url']]) == '200', 'production_api_not_ready')
    for unit in [*plan['units'], *plan['engine_intent']]:
        require(C.B.command(['/usr/bin/systemctl', 'show', unit['name'], '-p', 'UnitFileState', '--value']) == unit['enabled'], 'native_enablement_changed')
        if unit.get('kind') != 'oneshot':
            state = C.B.command(['/usr/bin/systemctl', 'show', unit['name'], '-p', 'ActiveState', '--value'])
            require(state in {'active', 'inactive', 'failed'} and (state == 'active') == unit['active'],
                    'native_intent_changed')
    snapshot = {}
    for row in plan['containers']:
        if row['id'] in IDS or not row['running']:
            continue
        value = json.loads(C.B.command(['/usr/bin/docker', 'inspect', '--format',
            '{"started":{{json .State.StartedAt}},"restarts":{{json .RestartCount}},"running":{{json .State.Running}},"restarting":{{json .State.Restarting}}}', row['id']]))
        require(value.get('running') is True and value.get('restarting') is False
                and type(value.get('restarts')) is int and value['restarts'] >= 0
                and isinstance(value.get('started'), str) and value['started'], 'retained_application_not_stable')
        snapshot[row['id']] = (value['started'], value['restarts'])
    require(len(snapshot) == 16, 'retained_application_count_changed')
    return snapshot


def stable_production(C, plan):
    initial = production(C, plan)
    degraded = []
    for _ in range(3):
        time.sleep(10)
        require(production(C, plan) == initial, 'retained_application_restarted')
        degraded = inventory(C, plan, retired=True)
        require(set(degraded) <= set(KNOWN_MARKET_FLAPS), 'new_production_health_degradation')
    return degraded


def no_backup_exec(C, plan, *, retired):
    for probe in plan['datastore_probes']:
        if retired and probe['id'] in IDS:
            continue
        value = json.loads(C.B.command(['/usr/bin/docker', 'inspect', '--format', '{{json .ExecIDs}}', probe['id']]))
        require(value in (None, []), 'datastore_exec_still_active')
        if probe['kind'] == 'postgres':
            sql = "SELECT count(*) FROM pg_stat_activity WHERE pid<>pg_backend_pid() AND application_name IN ('pg_dump','pg_restore')"
            require(C.B.command(['/usr/bin/docker', 'exec', probe['id'], 'psql', '-X', '-At', '-U', probe['user'], '-d', probe['database'],
                '-c', sql]) == '0', 'backup_backend_still_active')


def retire(C, selected, stores):
    for row in selected:
        C.B.command(['/usr/bin/docker', 'update', '--restart=no', row['id']])
    apps = sorted(row['id'] for row in selected if row['id'] not in stores)
    require(len(apps) == 8, 'qa_application_scope_changed')
    for group in (apps, sorted(stores)):
        C.bounded(['/usr/bin/docker', 'stop', '--timeout', '45', *group], timeout=90)
        for ident in group:
            require(C.B.command(['/usr/bin/docker', 'inspect', '--format', '{{json .State.Running}}', ident]) == 'false', 'qa_stop_not_complete')


def execute(C, H, action):
    plan = C.P.document(H.OLD_ROOT / 'plan.json', H.PLAN_SHA)
    selected, stores = exact_scope(plan)
    audit_path = C.ROOT / (H.OP + '.qa-retirement.json')
    require(not os.path.lexists(audit_path), 'qa_retirement_already_attempted')
    with ExitStack() as stack, pinned(C):
        fds = {}
        for path in (C.P.MARKER, C.paths(plan)[1], C.P.RESERVATION):
            before = path.lstat()
            C.B.read(path, maximum=8192)
            fd = os.open(path, os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK)
            stack.callback(os.close, fd)
            require(C.B.identity(before) == C.B.identity(os.fstat(fd)) == C.B.identity(path.lstat()), 'original_fence_inode_changed')
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fds[path] = fd
        scope_owner_proof(C, H, plan, fds)
        scope_remaining(C, 300)
        hot_finished(C, H)
        no_backup_exec(C, plan, retired=False)
        degraded_before = inventory(C, plan, retired=False)
        require(set(degraded_before) <= set(KNOWN_MARKET_FLAPS), 'unexpected_preexisting_production_degradation')
        production(C, plan)
        retained = preserved(C, selected)
        evidence_paths = [C.paths(plan)[0], C.ROOT / (H.OP + '.owned-restore.json'), C.ROOT / (H.OP + '.owned-intent.json')]
        evidence = [{'path': str(path), 'raw_json': C.B.read(path, maximum=65536).decode()} for path in evidence_paths]
        for row in evidence:
            row['sha256'] = sha256(row['raw_json'].encode()).hexdigest()
        if action == 'check':
            return {'status': 'exact_qa_retirement_checked', 'runtime_changed': False}
        audit = {'schema': 'web_qa_retirement/1', 'authorization_scope': 'user_authorized_exact_ten_obsolete_qa_20261024',
            'original_operation_id': H.OP, 'original_nonce': H.NONCE, 'original_plan_sha256': H.PLAN_SHA,
            'original_deadline_utc': plan['deadline_utc'], 'retirement_scope_deadline_utc': SCOPE_END,
            'phase': 'retiring_qa', 'pid': os.getpid(), 'retired_ids': sorted(IDS), 'preserved_evidence': evidence,
            'preserved_resources': retained, 'security_transition_completed': False, 'all_prior_runtime_healthy': False,
            'degraded_production_health_ids_before': degraded_before,
            'known_preexisting_market_health_flaps': KNOWN_MARKET_FLAPS,
            'cleanup_review_due_utc': (C.datetime.now(C.timezone.utc) + C.timedelta(days=10)).isoformat().replace('+00:00', 'Z')}
        C.write_json(audit_path, audit, exclusive=True)
        retire(C, selected, stores)
        degraded = stable_production(C, plan)
        require(preserved(C, selected) == retained, 'qa_retained_resource_changed')
        for row in evidence:
            require(C.B.read(Path(row['path']), maximum=65536).decode() == row['raw_json'], 'original_evidence_changed')
        # The original failed07 journal stays byte-for-byte intact. This NEW
        # scope audit supersedes it without claiming old all27 health/security.
        scope_owner_proof(C, H, plan, fds)
        hot_finished(C, H)
        no_backup_exec(C, plan, retired=True)
        require(not C.B.command(['/usr/bin/ss', '-H', '-ltn', 'sport = :8443']), 'retired_qa_port_still_listening')
        audit.update(phase='aborted_after_authorized_qa_retirement', retired_count=10, retained_count=17,
                     retained_running_count=16, retained_datastore_count=6, degraded_production_health_ids=degraded,
                     data_deleted=False, old_manifest_changed=False, fences_release_status='pending')
        C.write_json(audit_path, audit, exclusive=False)
        owned = [(path, os.fstat(fds[path]).st_dev, os.fstat(fds[path]).st_ino)
                 for path in (C.P.RESERVATION, C.paths(plan)[1])]
        C.release_owned_fences(owned)
        audit['fences_release_status'] = 'released'
        C.write_json(audit_path, audit, exclusive=False)
        return {'status': audit['phase'], 'qa_retirement_completed': True, 'security_transition_completed': False,
                'all_prior_runtime_healthy': False, 'degraded_production_health_count': len(degraded), 'data_deleted': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('reviewed_hot_source')
    parser.add_argument('action', choices=('check', 'retire'), nargs='?', default='check')
    parser.add_argument('--confirmation', default='')
    args = parser.parse_args()
    require(args.action == 'check' or args.confirmation == CONFIRM, 'explicit_exact_qa_retirement_required')
    H = reviewed_hot(args.reviewed_hot_source)
    C = H.controller()
    plan = C.P.document(H.OLD_ROOT / 'plan.json', H.PLAN_SHA)
    deadline = scope_remaining(C, 300)
    def expired(*_):
        raise RuntimeError('qa_retirement_deadline')
    signal.signal(signal.SIGALRM, expired)
    signal.alarm(min(600, int((deadline - C.datetime.now(C.timezone.utc)).total_seconds()) - 5))
    print(json.dumps(execute(C, H, args.action), sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        code = str(error) if isinstance(error, RuntimeError) and re.fullmatch(r'[a-z0-9_]{1,96}', str(error)) else 'qa_retirement_refused'
        print(json.dumps({'status': 'refused', 'reason_code': code, 'completion_unverified': True}, sort_keys=True))
        raise SystemExit(1)
