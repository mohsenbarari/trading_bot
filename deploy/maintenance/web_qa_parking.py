#!/usr/bin/env python3
"""Park only ten obsolete QA containers; retain old fences and open incidents."""
from contextlib import ExitStack
from hashlib import sha256
import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import selectors
import signal
import stat
import subprocess
import time
from types import ModuleType

QA_SOURCE_SHA = '9703ddf97439fd1578a4137ce3ad0e38d48c2c1acf0cf891b588527494982272'
HOT_SOURCE_SHA = 'cb3e03503a0bddae0d7bd7d31f615606d2645b0f6f9b10dfa7468fef108995aa'
CONFIRM = 'PARK-EXACT-TEN-OBSOLETE-QA-KEEP-INCIDENT-OPEN-20261024'
PARK_SCOPE = 'user_authorized_no_production_downtime_qa_parking_20261024'
CAPTURE_ID = '9870311065b13646767dfad1e48214d6a231e3898e1f248bb9559edd8926966c'
QA_PARENT_MOUNT = Path('/srv/trading-bot-three-site-staging-data')
QA_PARENT_UUID = 'e7f47458-ffc1-4181-81e1-58c6fc4519a2'
QA_BACKING_PATHS = frozenset(QA_PARENT_MOUNT / 'three-site-dc32d90392b0/webapp_fi' / name
                             for name in ('audit', 'postgres', 'redis', 'uploads'))


def require(ok, code):
    if not ok:
        raise RuntimeError(code)


def reviewed(raw, expected, name):
    data = raw.encode()
    require(0 < len(data) <= 65536 and sha256(data).hexdigest() == expected, 'reviewed_parking_source_changed')
    module = ModuleType(name)
    exec(compile(data, '<' + name + '>', 'exec'), module.__dict__)
    return module


def owner_proof(C, H, plan, fds):
    # This new user-authorized, no-production-downtime task has its own clock.
    # Do not call/patch the expired old transaction's native_contract or alter
    # its 07:00 deadline/manifest. Independently prove the same native host.
    require(C.datetime.now(C.timezone.utc).date().isoformat() == '2026-10-24', 'parking_scope_day_expired')
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

def inventory(C, R, plan, *, parked):
    expected = {row['id']: row for row in plan['containers']}
    require(set(C.B.command(['/usr/bin/docker', 'ps', '-aq', '--no-trunc']).splitlines()) == set(expected), 'parking_inventory_changed')
    incidents = []
    for ident, before in expected.items():
        row = json.loads(C.B.command(['/usr/bin/docker', 'inspect', '--format', C.INSPECT, ident]))
        require(all(row[key] == before[key] for key in ('id', 'name', 'image', 'project', 'network_mode', 'port_bindings'))
                and sorted(row['network_names']) == sorted(before['network_names'])
                and C.P.canonical_mounts(row['mounts']) == C.P.canonical_mounts(before['mounts']), 'parking_identity_changed')
        policy = {'Name': 'no', 'MaximumRetryCount': 0} if parked and ident in R.IDS else before['restart_policy']
        require(row['restart_policy'] == policy, 'parking_policy_changed')
        state = json.loads(C.B.command(['/usr/bin/docker', 'inspect', '--format',
            '{"restarting":{{json .State.Restarting}},"status":{{json .State.Status}},"started":{{json .State.StartedAt}},"restarts":{{json .RestartCount}}}', ident]))
        require(type(state.get('restarting')) is bool and type(state.get('restarts')) is int, 'parking_state_metadata_invalid')
        if ident in R.IDS:
            if parked:
                require(not row['running'] and not state['restarting'], 'qa_not_parked')
        else:
            if row['running'] != before['running'] or state['restarting'] or (row['running'] and row['health'] not in {None, 'healthy'}):
                incidents.append({'id': ident, 'name': row['name'], 'running': row['running'], 'health': row['health'],
                                  'restarting': state['restarting'], 'restart_count': state['restarts'], 'started_at': state['started']})
    return incidents


def trusted_docker():
    binary = Path('/usr/bin/docker').resolve(strict=True)
    value = binary.stat()
    require(stat.S_ISREG(value.st_mode) and value.st_uid == 0 and not value.st_mode & 0o022, 'private_inspect_binary_unsafe')


def memory_private_inspect(ident):
    # The older B.command helper deliberately uses a temporary output file.
    # Private Config/HostConfig must instead remain in this bounded pipe/memory.
    require(re.fullmatch(r'[0-9a-f]{64}', ident), 'private_inspect_id_invalid')
    trusted_docker()
    child = subprocess.Popen(['/usr/bin/docker', 'inspect', '--format',
        '{"config":{{json .Config}},"host_config":{{json .HostConfig}}}', ident],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, start_new_session=True,
        env={'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LC_ALL': 'C.UTF-8', 'DOCKER_HOST': 'unix:///var/run/docker.sock'})
    selector = None
    raw = bytearray()
    deadline = time.monotonic() + 25
    try:
        selector = selectors.DefaultSelector()
        selector.register(child.stdout, selectors.EVENT_READ)
        while selector.get_map():
            require(time.monotonic() < deadline, 'private_inspect_timeout')
            for key, _ in selector.select(timeout=0.5):
                chunk = os.read(key.fileobj.fileno(), 65536)
                if not chunk:
                    selector.unregister(key.fileobj)
                else:
                    require(len(raw) + len(chunk) <= 2097152, 'private_inspect_output_bound')
                    raw.extend(chunk)
        require(child.wait(timeout=max(0.1, min(5, deadline - time.monotonic()))) == 0, 'private_inspect_failed')
    finally:
        if selector is not None:
            selector.close()
        if child.poll() is None:
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        child.wait(timeout=5)
        child.stdout.close()
    value = json.loads(raw)
    require(isinstance(value, dict) and set(value) == {'config', 'host_config'}
            and all(isinstance(item, dict) for item in value.values()), 'private_inspect_schema_invalid')
    return value


def private_configuration(C, R, plan):
    # Private values remain only inside this trusted process. Never serialize,
    # print, hash, copy or persist these Config/HostConfig comparison buffers.
    result = {}
    for row in plan['containers']:
        if row['id'] not in R.IDS:
            result[row['id']] = memory_private_inspect(row['id'])
    require(len(result) == 17, 'retained_configuration_scope_changed')
    return result


def stable_directory(C, path):
    require(path.is_absolute() and path.resolve(strict=True) == path, 'qa_backing_redirected')
    # Data directories can legitimately belong to the PostgreSQL/app UID.
    # Require canonical directories without imposing secret-file root ownership.
    for parent in path.parents:
        require(stat.S_ISDIR(parent.lstat().st_mode) and not parent.is_symlink(), 'qa_backing_parent_unsafe')
    value = path.lstat()
    require(stat.S_ISDIR(value.st_mode) and not path.is_symlink(), 'qa_backing_not_directory')
    return {'path': str(path), 'device': value.st_dev, 'inode': value.st_ino,
            'uid': value.st_uid, 'gid': value.st_gid, 'mode': stat.S_IMODE(value.st_mode)}


def parent_mount(C):
    # -M/--mountpoint proves this exact attached mount, never an underlying
    # root filesystem reached through findmnt -T after a missing attachment.
    require(C.B.command(['/usr/bin/findmnt', '-n', '-o', 'TARGET', '--mountpoint', str(QA_PARENT_MOUNT)])
            == str(QA_PARENT_MOUNT), 'qa_parent_mount_missing')
    require(C.B.command(['/usr/bin/findmnt', '-n', '-o', 'UUID', '--mountpoint', str(QA_PARENT_MOUNT)])
            == QA_PARENT_UUID, 'qa_parent_mount_changed')
    return {**stable_directory(C, QA_PARENT_MOUNT), 'uuid': QA_PARENT_UUID}


def preserved_resources(C, selected):
    """Preserve backing directories, not a local-driver bind's transient _data.

    Stopping the final user unmounts the bind at Docker's Mountpoint. Its
    visible inode/device can then change legitimately. Options.device remains
    the durable data location and must retain its identity on the attached FS.
    """
    volume_sources = {}
    actual_mounts = []
    for row in selected:
        mounts = json.loads(C.B.command(['/usr/bin/docker', 'inspect', '--format', '{{json .Mounts}}', row['id']]))
        require(C.P.canonical_mounts(mounts) == C.P.canonical_mounts(row['mounts']), 'qa_mount_identity_changed')
        actual_mounts.extend(mounts)
        for mount in mounts:
            if mount['Type'] == 'volume':
                name = mount.get('Name')
                require(isinstance(name, str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,200}', name), 'qa_volume_name_invalid')
                require(name not in volume_sources or volume_sources[name] == mount['Source'], 'qa_volume_source_alias')
                volume_sources[name] = mount['Source']
    require(len(volume_sources) == 4, 'qa_volume_inventory_changed')
    records, bind_paths, dependency = {}, set(), None
    for name, source in sorted(volume_sources.items()):
        record = json.loads(C.B.command(['/usr/bin/docker', 'volume', 'inspect', '--format',
            '{"name":{{json .Name}},"driver":{{json .Driver}},"mountpoint":{{json .Mountpoint}},"options":{{json .Options}},"labels":{{json .Labels}}}', name]))
        require(record['name'] == name and record['mountpoint'] == source, 'qa_volume_binding_changed')
        require(record['driver'] == 'local', 'qa_volume_driver_unsupported')
        mountpoint = Path(record['mountpoint'])
        require(mountpoint.is_absolute() and mountpoint.resolve(strict=True) == mountpoint
                and stat.S_ISDIR(mountpoint.lstat().st_mode), 'qa_volume_mountpoint_redirected')
        options = record['options']
        if options in (None, {}):
            backing, kind = mountpoint, 'local_directory'
        else:
            require(isinstance(options, dict) and set(options) == {'type', 'o', 'device'}
                    and options['type'] == 'none' and options['o'] == 'bind', 'qa_volume_options_unsupported')
            require(isinstance(options['device'], str), 'qa_bind_device_invalid')
            backing, kind = Path(options['device']), 'local_bind'
            require(backing in QA_BACKING_PATHS and str(backing) == options['device'], 'qa_bind_device_unapproved')
            require(backing not in bind_paths, 'qa_bind_device_duplicate')
            bind_paths.add(backing)
            if dependency is None:
                dependency = parent_mount(C)
        identity = stable_directory(C, backing)
        if kind == 'local_bind':
            require(identity['device'] == dependency['device'], 'qa_backing_filesystem_changed')
            observed_target = C.B.command(['/usr/bin/findmnt', '-n', '-o', 'TARGET', '-T', str(mountpoint)])
            if observed_target == str(mountpoint):
                mounted = mountpoint.lstat()
                require((mounted.st_dev, mounted.st_ino) == (identity['device'], identity['inode']), 'qa_mounted_backing_mismatch')
        records[name] = {'record': record, 'backing_kind': kind, 'backing_identity': identity}
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
    return {'volumes': records, 'secret_metadata': secrets, 'parent_mount': dependency}


def web_datastores_native(C, R, plan):
    probes = [probe for probe in plan['datastore_probes'] if probe['id'] not in R.IDS]
    require(len(probes) == 6, 'parking_retained_datastores_changed')
    C.old_datastores_and_api_ready({**plan, 'datastore_probes': probes, 'health_probes': []})
    for probe in plan['health_probes']:
        require(probe['id'] not in R.IDS, 'parking_api_probe_is_qa')
        require(C.B.command(['/usr/bin/docker', 'exec', probe['id'], '/usr/local/bin/python', '-I', '-B', '-c',
            C.B.CONTAINER_HTTP_PROBE, probe['url']]) == '200', 'parking_production_api_not_ready')
    for unit in [*plan['units'], *plan['engine_intent']]:
        require(C.B.command(['/usr/bin/systemctl', 'show', unit['name'], '-p', 'UnitFileState', '--value']) == unit['enabled'],
                'parking_native_enablement_changed')
        if unit.get('kind') != 'oneshot':
            state = C.B.command(['/usr/bin/systemctl', 'show', unit['name'], '-p', 'ActiveState', '--value'])
            require(state in {'active', 'inactive', 'failed'} and (state == 'active') == unit['active'], 'parking_native_intent_changed')


def static_code(error):
    value = str(error)
    return value if isinstance(error, RuntimeError) and re.fullmatch(r'[a-z0-9_]{1,96}', value) else 'parking_verification_failed'


def execute(C, H, R, action, deadline):
    plan = C.P.document(H.OLD_ROOT / 'plan.json', H.PLAN_SHA)
    selected, stores = R.exact_scope(plan)
    capture = next(row for row in plan['containers'] if row['id'] == CAPTURE_ID)
    require(capture['name'] == '/market-private-pipeline-primary-market-capture-account2-1', 'capture_incident_identity_changed')
    path = C.ROOT / (H.OP + '.qa-parking.json')
    require(not os.path.lexists(path) and not os.path.lexists(C.ROOT / (H.OP + '.qa-retirement.json')), 'parking_or_retirement_already_attempted')
    with ExitStack() as stack, R.pinned(C):
        fds = {}
        original_payloads = {}
        for target in (C.P.MARKER, C.paths(plan)[1], C.P.RESERVATION):
            before = target.lstat()
            original_payloads[target] = C.B.read(target, maximum=8192)
            fd = os.open(target, os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK)
            stack.callback(os.close, fd)
            require(C.B.identity(before) == C.B.identity(os.fstat(fd)) == C.B.identity(target.lstat()), 'parking_fence_inode_changed')
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fds[target] = fd
        owner_proof(C, H, plan, fds)
        R.hot_finished(C, H)
        R.no_backup_exec(C, plan, retired=False)
        require(C.B.command(['/usr/bin/systemctl', 'show', 'security-web-qa-retirement-20261024-01.service',
                            '-p', 'LoadState', '--value']) == 'not-found', 'old_qa_retirement_unit_exists')
        before_incidents = inventory(C, R, plan, parked=False)
        web_datastores_native(C, R, plan)
        private_before = private_configuration(C, R, plan)
        resources = preserved_resources(C, selected)
        originals = [C.paths(plan)[0], C.ROOT / (H.OP + '.owned-restore.json'), C.ROOT / (H.OP + '.owned-intent.json')]
        evidence = [{'path': str(item), 'raw_json': C.B.read(item, maximum=65536).decode()} for item in originals]
        for item in evidence:
            item['sha256'] = sha256(item['raw_json'].encode()).hexdigest()
        if action == 'check':
            return {'status': 'qa_parking_checked', 'runtime_changed': False, 'production_incident_count': len(before_incidents)}
        audit = {'schema': 'web_qa_parking/1', 'authorization_scope': PARK_SCOPE, 'original_operation_id': H.OP,
            'original_nonce': H.NONCE, 'original_plan_sha256': H.PLAN_SHA, 'original_deadline_utc': plan['deadline_utc'],
            'parking_deadline_utc': deadline.isoformat().replace('+00:00', 'Z'), 'parking_pid': os.getpid(), 'phase': 'parking_qa',
            'parked_ids': sorted(R.IDS), 'preserved_evidence': evidence, 'preserved_resources': resources,
            'production_incidents_before': before_incidents,
            'known_preexisting_incident_ids': sorted([CAPTURE_ID, *R.KNOWN_MARKET_FLAPS]),
            'security_transition_completed': False, 'all_prior_runtime_healthy': False, 'old_fences_retained': True,
            'cleanup_review_due_utc': (C.datetime.now(C.timezone.utc) + C.timedelta(days=10)).isoformat().replace('+00:00', 'Z')}
        C.write_json(path, audit, exclusive=True)
        try:
            R.retire(C, selected, stores)
            after_incidents = inventory(C, R, plan, parked=True)
            require(private_configuration(C, R, plan) == private_before, 'retained_private_configuration_changed')
            del private_before
            require(preserved_resources(C, selected) == resources, 'parked_resource_changed')
            require(not C.B.command(['/usr/bin/ss', '-H', '-ltn', 'sport = :8443']), 'parked_qa_port_still_listening')
            health_ok, health_code = True, None
            try:
                web_datastores_native(C, R, plan)
            except Exception as error:
                health_ok, health_code = False, static_code(error)
            for item in evidence:
                require(C.B.read(Path(item['path']), maximum=65536).decode() == item['raw_json'], 'original_parking_evidence_changed')
            for target, raw in original_payloads.items():
                require(C.B.read(target, maximum=8192) == raw
                        and C.B.identity(os.fstat(fds[target])) == C.B.identity(target.lstat()), 'parking_fence_changed')
            owner_proof(C, H, plan, fds)
            # A healthy instant is not a reviewed closure of the known capture
            # and Market incidents. Parking has no incident-acceptance path.
            production_complete = False
            audit.update(phase='qa_parked_production_incident_open', parked_count=10, retained_count=17,
                         retained_configuration_parity_checked=True, required_web_datastores_native_passed=health_ok,
                         required_web_datastores_native_code=health_code, production_incidents_after=after_incidents,
                         data_deleted=False, old_manifest_changed=False, public_8443_closed=True, qa_parking_verified=True,
                         production_health_complete=production_complete, completion_unverified=not production_complete)
            C.write_json(path, audit, exclusive=False)
            # Parking never releases either original fence and never claims
            # capture repair, old-all27 health or security acceptance.
            return {'status': audit['phase'], 'qa_parked': True, 'old_fences_retained': True, 'security_transition_completed': False,
                    'all_prior_runtime_healthy': False, 'required_web_datastores_native_passed': health_ok,
                    'qa_parking_verified': True, 'production_health_complete': production_complete,
                    'production_incident_count': len(after_incidents), 'completion_unverified': not production_complete}
        except Exception as error:
            audit.update(phase='parking_incomplete', completion_unverified=True, reason_code=static_code(error))
            C.write_json(path, audit, exclusive=False)
            raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('reviewed_qa_source')
    parser.add_argument('reviewed_hot_source')
    parser.add_argument('action', choices=('check', 'park'), nargs='?', default='check')
    parser.add_argument('--confirmation', default='')
    args = parser.parse_args()
    require(args.action == 'check' or args.confirmation == CONFIRM, 'explicit_exact_qa_parking_required')
    R = reviewed(args.reviewed_qa_source, QA_SOURCE_SHA, 'reviewed_qa_primitives')
    H = reviewed(args.reviewed_hot_source, HOT_SOURCE_SHA, 'reviewed_hot_primitives')
    C = H.controller()
    deadline = C.datetime.now(C.timezone.utc) + C.timedelta(seconds=600)
    def expired(*_):
        raise RuntimeError('qa_parking_runtime_exceeded')
    signal.signal(signal.SIGALRM, expired)
    signal.alarm(600)
    result = execute(C, H, R, args.action, deadline)
    print(json.dumps(result, sort_keys=True))
    return 3 if result.get('completion_unverified') else 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as error:
        print(json.dumps({'status': 'refused', 'reason_code': static_code(error), 'completion_unverified': True}, sort_keys=True))
        raise SystemExit(1)
