#!/usr/bin/env python3
"""Finish only the interrupted, still-fenced C07 restore; default is read-only.

Restore DB/API, original policies/native intent, then complete health. Failed
health is recorded honestly and retains every fence, never accepted as success.
"""
from contextlib import ExitStack
from hashlib import sha256
import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import signal
import stat
import sys
import time
from types import ModuleType

PRIOR_SHA = 'a3ef1b1e06de1e2585668186d1b6bb63b74c943b1cfd706b4c62fc21602517b0'
PRIOR_PID = 2147364
FIRST_PID = 2110733
BLOB_ID = '26dd7428182e66afd2ce28c85fc78f30f61a2ea323b2fe21b3f7a69892acff95'
TIMERS = {'coin-intelligence-capture-shadow-external.timer', 'coin-intelligence-capture-shadow-input.timer',
          'market-pipeline-watchdog.timer', 'trading-bot-sync-health-sampler.timer'}
CONFIRM = 'FINISH-OWNED-WEB-ENGINE-07-INTENT'


def require(ok, code):
    if not ok:
        raise RuntimeError(code)


def prior_source(raw):
    raw = raw.encode()
    require(0 < len(raw) <= 65536 and sha256(raw).hexdigest() == PRIOR_SHA, 'prior_source_changed')
    module = ModuleType('_prior_reviewed_owned_restore')
    exec(compile(raw, '<sealed-prior-owned-restore>', 'exec'), module.__dict__)
    return module


def prior_dead(C, M):
    name = 'security-web-engine-07-owned-restore-' + M.NONCE + '.service'
    state = dict(line.split('=', 1) for line in C.B.command(['/usr/bin/systemctl', 'show', name,
        '--property=ActiveState,MainPID,ExecMainStatus']).splitlines())
    require(state == {'ActiveState': 'failed', 'MainPID': '0', 'ExecMainStatus': '1'}, 'previous_restore_not_failed_exited')
    require(not os.path.lexists('/proc/' + str(PRIOR_PID)), 'previous_restore_pid_alive')
    require(C.scope_empty(Path('/sys/fs/cgroup/system.slice/' + name)), 'previous_restore_descendants_present')


def db_api(C, plan):
    C.old_datastores_and_api_ready({**plan, 'health_probes': []})
    for probe in plan['health_probes']:
        require(C.B.command(['/usr/bin/docker', 'exec', probe['id'], '/usr/local/bin/python', '-I', '-B', '-c',
                             C.B.CONTAINER_HTTP_PROBE, probe['url']]) == '200', 'original_api_not_ready')


def transitional_runtime(C, plan):
    """Exact identities, mounts, networks and no-restart state; DB/API first.

    Intermediate application health can depend on the four paused timers.
    No unhealthy application is accepted by the final baseline/health gate.
    """
    expected = {row['id']: row for row in plan['containers']}
    require(len(expected) == 27 and set(C.B.command(['/usr/bin/docker', 'ps', '-aq', '--no-trunc']).splitlines()) == set(expected),
            'container_inventory_changed')
    C.P.store_contract(json.loads(C.B.command(['/usr/bin/docker', 'info', '--format',
        '{"root":{{json .DockerRootDir}},"driver":{{json .Driver}},"driver_status":{{json .DriverStatus}},"live_restore":{{json .LiveRestoreEnabled}}}'])))
    require(json.loads(C.B.command(['/usr/bin/docker', 'info', '--format',
        '{"driver":{{json .CgroupDriver}},"version":{{json .CgroupVersion}}}'])) == {'driver': 'systemd', 'version': '2'},
        'cgroup_runtime_changed')
    running = set()
    for ident, before in expected.items():
        row = json.loads(C.B.command(['/usr/bin/docker', 'inspect', '--format', C.INSPECT, ident]))
        require(all(row[key] == before[key] for key in ('id', 'name', 'image', 'project'))
                and C.P.canonical_mounts(row['mounts']) == C.P.canonical_mounts(before['mounts'])
                and all(row[key] == before[key] for key in ('network_mode', 'port_bindings'))
                and sorted(row['network_names']) == sorted(before['network_names']), 'container_identity_changed')
        require(row['restart_policy'] == {'Name': 'no', 'MaximumRetryCount': 0}, 'original_restore_policy_changed')
        require(row['running'] == before['running'] or (ident == BLOB_ID and before['running'] and not row['running']),
                'unexpected_stopped_application')
        if row['running']:
            running.add(ident)
        if ident in plan['datastore_ids']:
            require(row['running'] and row['health'] in {None, 'healthy'}, 'datastore_unhealthy')
    require(len(plan['datastore_ids']) == 8 and sum(row['running'] for row in expected.values()) == 26
            and BLOB_ID in expected and expected[BLOB_ID]['name'] == '/three-site-dc32d90392b0-webapp-fi-webapp_fi_blobs-1',
            'original_restore_scope_changed')
    base = ['/usr/bin/ctr', '--address', '/run/containerd/containerd.sock']
    namespaces = C.B.command(base + ['namespaces', 'list', '--quiet']).splitlines()
    require(sorted(namespaces) == ['moby', 'moby_history'], 'containerd_namespace_changed')
    for namespace in namespaces:
        tasks = C.B.command(base + ['--namespace', namespace, 'tasks', 'list', '--quiet']).splitlines()
        require(len(tasks) == len(set(tasks)) and set(tasks) == (running if namespace == 'moby' else set()), 'unexpected_containerd_tasks')
    db_api(C, plan)


def metadata_proof(C, M, plan, bus, source, reservation, old, fds):
    C.native_contract(plan)
    C.control_parent_contract(plan)
    C.remaining_contract(plan, 300)
    require(plan['operation_id'] == M.OP and plan['nonce'] == M.NONCE and plan['source_root'] == str(M.ROOT / 'source'), 'wrong_operation')
    journal = C.journal(plan, M.DIGEST)
    require(journal['phase'] == 'writers_quiesced' and journal['pid'] == FIRST_PID, 'original_journal_changed')
    require(old['schema'] == 'web_engine_owned_restore/1' and old['operation_id'] == M.OP and old['nonce'] == M.NONCE
            and old['manifest_sha256'] == M.DIGEST and old['source_sha256'] == M.SOURCE_SHA
            and old['previous_owner_pid'] == FIRST_PID and old['restore_owner_pid'] == PRIOR_PID and old['status'] == 'restoring',
            'previous_restore_audit_changed')
    require(source == reservation and source['pid'] == PRIOR_PID and source['schema'] == 'web_engine_transaction/1'
            and source['operation_id'] == M.OP and source['nonce'] == M.NONCE and source['manifest_sha256'] == M.DIGEST,
            'previous_restore_fence_changed')
    originals = old['original_fences']
    require(len(originals) == 2 and {row['path'] for row in originals} == {str(C.paths(plan)[1]), str(C.P.RESERVATION)}, 'original_fence_inventory')
    for row in originals:
        info = os.fstat(fds[Path(row['path'])])
        require((info.st_dev, info.st_ino) == (row['device'], row['inode']) and
                {**row['payload'], 'pid': PRIOR_PID} == source and row['payload']['pid'] == FIRST_PID, 'original_fence_binding_changed')
    prior_dead(C, M)
    M.descendants_gone(C, plan)
    M.jobs_idle(C, bus)
    require({row['name'] for row in plan['units'] if row['active']} == TIMERS
            and all(row['kind'] == 'timer' for row in plan['units'] if row['active']), 'original_native_intent_changed')
    require(not os.path.lexists(C.P.PENDING) and not any(os.path.lexists(path) for path in plan['absent_control_paths']), 'foreign_operation')
    require(C.package_versions(C.P.OLD_PACKAGES) == {name: pin[0] for name, pin in C.P.OLD_PACKAGES.items()}, 'package_boundary_crossed')
    require(not any(os.path.lexists(path) for path in ('/etc/docker/daemon.json', '/etc/containerd/config.toml')), 'vendor_install_started')
    marker = plan['marker']
    require(C.P.file_digest(C.P.MARKER, maximum=8192, private=True) == (marker['size'], marker['sha256'])
            and (C.P.MARKER.lstat().st_dev, C.P.MARKER.lstat().st_ino) == (marker['device'], marker['inode']), 'marker_changed')
    for entry in plan['files']:
        require(C.P.file_digest(entry['path'], maximum=2097152, private=entry.get('private', True))
                == (entry['size'], entry['sha256']), 'sealed_file_changed')
    for unit in plan['units']:
        require(sha256(C.B.read(unit['fragment_path'], private=False)).hexdigest() == unit['fragment_sha256'], 'unit_fragment_changed')
        require(C.B.command(['/usr/bin/systemctl', 'show', unit['name'], '-p', 'DropInPaths', '--value']).split()
                == [entry['path'] for entry in unit['dropins']], 'unit_dropins_changed')
        for entry in unit['dropins']:
            require(sha256(C.B.read(entry['path'], private=False)).hexdigest() == entry['sha256'], 'unit_dropin_changed')
    for path in (plan['market_backup']['env_file'], plan['backup_key_file']):
        info = Path(path).lstat()
        C.B.private_parent(Path(path))
        require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and info.st_nlink == 1 and not info.st_mode & 0o077
                and info.st_size <= 1048576, 'private_input_metadata_changed')
    require(list(C.B.identity(Path(plan['market_backup']['env_file']).lstat())) == plan['market_backup']['env_file_identity'], 'env_metadata_changed')
    for entry in plan['store_roots']:
        C.store_root_contract(entry)
    C.backup_contract(plan)
    C.market_anchor_invariant(plan, M.DIGEST, owned=True)
    M.no_dump_or_restore(C, plan)
    transitional_runtime(C, plan)
    return journal


def restore_intent(C, plan):
    db_api(C, plan)
    for row in plan['containers']:
        policy = row['restart_policy']
        label = policy['Name'] + (':' + str(policy['MaximumRetryCount'])
            if policy['Name'] == 'on-failure' and policy['MaximumRetryCount'] > 0 else '')
        C.B.command(['/usr/bin/docker', 'update', '--restart=' + label, row['id']])
    for name in sorted(TIMERS):
        C.B.command(['/usr/bin/systemctl', 'start', name])
    row = json.loads(C.B.command(['/usr/bin/docker', 'inspect', '--format', '{{json .State.Running}}', BLOB_ID]))
    if row is False:
        C.B.command(['/usr/bin/docker', 'start', BLOB_ID])


def intent_proof(C, plan):
    for row in plan['containers']:
        observed = json.loads(C.B.command(['/usr/bin/docker', 'inspect', '--format', '{{json .HostConfig.RestartPolicy}}', row['id']]))
        require(observed == row['restart_policy'], 'original_policy_not_restored')
    for unit in plan['units']:
        require(C.B.command(['/usr/bin/systemctl', 'show', unit['name'], '-p', 'UnitFileState', '--value']) == unit['enabled'],
                'original_unit_enablement_changed')
        if unit['kind'] != 'oneshot':
            require((C.B.command(['/usr/bin/systemctl', 'show', unit['name'], '-p', 'ActiveState', '--value']) == 'active')
                    == unit['active'], 'original_native_intent_not_restored')
    for unit in plan['engine_intent']:
        require(C.B.command(['/usr/bin/systemctl', 'show', unit['name'], '-p', 'UnitFileState', '--value']) == unit['enabled']
                and (C.B.command(['/usr/bin/systemctl', 'show', unit['name'], '-p', 'ActiveState', '--value']) == 'active') == unit['active'],
                'original_engine_intent_changed')


def stable_snapshot(C, plan):
    result = {}
    for row in plan['containers']:
        if not row['running']:
            continue
        value = json.loads(C.B.command(['/usr/bin/docker', 'inspect', '--format',
            '{"started":{{json .State.StartedAt}},"restarts":{{json .RestartCount}},"running":{{json .State.Running}},"restarting":{{json .State.Restarting}}}', row['id']]))
        if not (value.get('running') is True and value.get('restarting') is False
                and isinstance(value.get('started'), str) and value['started']
                and type(value.get('restarts')) is int and value['restarts'] >= 0):
            raise C.P.Refusal('original_application_restarting')
        result[row['id']] = (value['started'], value['restarts'])
    require(len(result) == 26, 'original_running_scope_changed')
    return result


def final_health(C, M, plan):
    # Five minutes is a finite upper bound, not a guarantee or a waiver. Leave
    # at least 45 seconds before the original immutable outer deadline.
    deadline = C.remaining_contract(plan, 60)
    end = min(time.monotonic() + 300, time.monotonic() + (deadline - C.datetime.now(C.timezone.utc)).total_seconds() - 45)
    stable_since = None
    snapshot = None
    while True:
        try:
            C.old_datastores_and_api_ready(plan)
            C.invariant_contract(plan, M.DIGEST, owned=True)
            C.runtime_contract(plan, 'baseline')
            current = stable_snapshot(C, plan)
            now = time.monotonic()
            if snapshot != current:
                snapshot, stable_since = current, now
            elif stable_since is not None and now - stable_since >= 30:
                return True
        except (C.P.Refusal, C.B.Refusal):
            stable_since, snapshot = None, None
        if time.monotonic() >= end:
            return False
        time.sleep(5)


def execute(C, M, action):
    plan = C.P.document(M.ROOT / 'plan.json', M.DIGEST)
    old_path = C.ROOT / (M.OP + '.owned-restore.json')
    audit_path = C.ROOT / (M.OP + '.owned-intent.json')
    require(not os.path.lexists(audit_path), 'owned_intent_already_attempted')
    old = C.B.document(old_path)
    old_sha = sha256(C.B.read(old_path)).hexdigest()
    with ExitStack() as stack, M.pinned(C) as bus:
        fds = {}
        for path in (C.P.MARKER, C.paths(plan)[1], C.P.RESERVATION):
            before = path.lstat()
            C.B.read(path, maximum=8192)
            fd = os.open(path, os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK)
            stack.callback(os.close, fd)
            require(C.B.identity(before) == C.B.identity(os.fstat(fd)) == C.B.identity(path.lstat()), 'fence_inode_changed')
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fds[path] = fd
        source = C.B.document(C.paths(plan)[1])
        reservation = C.B.document(C.P.RESERVATION)
        journal = metadata_proof(C, M, plan, bus, source, reservation, old, fds)
        require(sha256(C.B.read(old_path)).hexdigest() == old_sha, 'previous_restore_audit_changed')
        if action == 'check':
            return {'status': 'owned_intent_checked', 'runtime_changed': False}
        audit = {'schema': 'web_engine_owned_intent/1', 'operation_id': M.OP, 'nonce': M.NONCE, 'manifest_sha256': M.DIGEST,
                 'previous_restore_audit_sha256': old_sha, 'previous_owner_pid': PRIOR_PID, 'intent_owner_pid': os.getpid(),
                 'phase': 'restoring_intent', 'security_transition_completed': False}
        C.write_json(audit_path, audit, exclusive=True)
        for path in (C.paths(plan)[1], C.P.RESERVATION):
            M.rewrite_owner(C, fds[path], path, source)
        C.invariant_contract(plan, M.DIGEST, owned=True)
        restore_intent(C, plan)
        intent_proof(C, plan)
        audit['phase'] = 'intent_restored_health_pending'
        C.write_json(audit_path, audit, exclusive=False)
        healthy = final_health(C, M, plan)
        require(sha256(C.B.read(old_path)).hexdigest() == old_sha and C.journal(plan, M.DIGEST) == journal, 'original_evidence_changed')
        if not healthy:
            audit['phase'] = 'intent_restored_health_incomplete'
            C.write_json(audit_path, audit, exclusive=False)
            return {'status': audit['phase'], 'security_transition_completed': False, 'completion_unverified': True}
        journal.update(phase='aborted_restored', aborted_from='writers_quiesced', security_transition_completed=False,
                       owned_restore_pid=PRIOR_PID, owned_intent_pid=os.getpid())
        C.write_json(C.paths(plan)[0], journal, exclusive=False)
        # Source first, global reservation LAST; M and prior restore audit stay.
        owned = [(path, os.fstat(fds[path]).st_dev, os.fstat(fds[path]).st_ino)
                 for path in (C.P.RESERVATION, C.paths(plan)[1])]
        C.release_owned_fences(owned)
        audit['phase'] = 'aborted_restored'
        C.write_json(audit_path, audit, exclusive=False)
        return {'status': 'aborted_restored', 'security_transition_completed': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('prior_helper_source')
    parser.add_argument('action', choices=('check', 'restore_intent'), nargs='?', default='check')
    parser.add_argument('--confirmation', default='')
    args = parser.parse_args()
    require(args.action == 'check' or args.confirmation == CONFIRM, 'explicit_owned_intent_required')
    M = prior_source(args.prior_helper_source)
    C = M.controller()
    plan = C.P.document(M.ROOT / 'plan.json', M.DIGEST)
    deadline = C.remaining_contract(plan, 300)
    seconds = min(900, int((deadline - C.datetime.now(C.timezone.utc)).total_seconds()) - 5)
    def expired(*_):
        raise RuntimeError('owned_intent_deadline')
    signal.signal(signal.SIGALRM, expired)
    signal.alarm(seconds)
    result = execute(C, M, args.action)
    print(json.dumps(result, sort_keys=True))
    return 3 if result.get('completion_unverified') else 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as error:
        code = str(error) if isinstance(error, RuntimeError) and re.fullmatch(r'[a-z0-9_]{1,96}', str(error)) else 'owned_intent_refused'
        print(json.dumps({'status': 'refused', 'code': code, 'completion_unverified': True}, sort_keys=True))
        raise SystemExit(1)
