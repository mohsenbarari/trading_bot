#!/usr/bin/env python3
"""One-window image-only coordinator, not a replacement deployment framework.

Default check cannot recreate containers. The new reservation coordinates the
existing authority/source mutexes; historical Market receipts remain unchanged.
"""
import argparse
from contextlib import ExitStack
from datetime import datetime, timezone
import fcntl
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import resource
import stat
import tempfile
from types import ModuleType

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
HELPER = REPO / 'scripts/validate_docker_daemon_maintenance.py'
HELPER_SHA = 'fa2bf85cd50378bb999a7d8aabbd965a174722605c943c59ce6da56f68a74901'
ANSIBLE = Path('/root/trading-bot/trading_bot/.local/security-maintenance-ansible-venv/bin/ansible-playbook')
STOP = Path('/root/trading-bot/trading_bot/.local/two-site-refactor/integration/.local/two-site-refactor/autopilot/STOP')
TERMINAL = {'applied', 'redeployed', 'rolled_back', 'failed_recovered', 'interrupted_recovered'}


def require(ok, code):
    if not ok: raise RuntimeError(code)

def module(path, expected):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        require(stat.S_ISREG(before.st_mode) and before.st_uid == os.geteuid() and before.st_nlink == 1 and
                not before.st_mode & 0o022 and 0 < before.st_size <= 65536, 'unsafe_public_source')
        raw = os.read(fd, 65537)
        identity = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mode, s.st_mtime_ns, s.st_ctime_ns)
        require(len(raw) == before.st_size and identity(before) == identity(os.fstat(fd)) == identity(path.lstat()) and
                sha256(raw).hexdigest() == expected, 'public_source_changed')
    finally: os.close(fd)
    result = ModuleType('_security_release_' + path.stem)
    result.__file__ = str(path)
    exec(compile(raw, str(path), 'exec'), result.__dict__)
    return result

def source_binding(record, c, b, worker=None):
    require(worker is not None and REPO == Path('/root/trading-bot/trading_bot'), 'canonical_successor_checkout_required')
    for name in c.CONTROL_SOURCES:
        require(sha256(b.read(HERE / name, maximum=65536, private=False)).hexdigest() ==
                record['control_sources'][name], 'release_control_source_changed')
    git = ['/usr/bin/git', '--no-optional-locks', '-C', str(REPO)]
    require(b.command([*git, 'symbolic-ref', '--short', 'HEAD']) == 'main' and
            b.command([*git, 'rev-parse', 'HEAD']) == record['source_commit'] and
            b.command([*git, 'rev-parse', 'HEAD^{tree}']) == record['source_tree'], 'published_main_checkout_required')
    exception = c.USER_DOCUMENT_EXCEPTION
    require(record.get('checkout_exception') == exception, 'successor_checkout_exception')
    status = worker.private_json([*git, 'status', '--porcelain=v1', '-z', '--untracked-files=normal'],
                                 maximum=65536, raw_output=True)
    require(status == (exception['status'] + ' ' + exception['path'] + '\0').encode(),
            'unapproved_checkout_change')
    require(sha256(b.read(REPO / exception['path'], maximum=131072, private=False)).hexdigest() == exception['sha256'],
            'preserved_user_document_changed')
    for relative in [*('deploy/maintenance/' + name for name in sorted(c.CONTROL_SOURCES)),
                     'docker-compose.yml', 'docker-compose.iran.yml']:
        actual = b.read(REPO / relative, maximum=131072, private=False)
        committed = worker.private_json([*git, 'cat-file', 'blob', record['source_commit'] + ':' + relative],
                                        maximum=131072, raw_output=True)
        require(actual == committed, 'consumed_public_source_not_committed')
    # A shared local clone must have its real original remote restored; a local
    # path origin cannot masquerade as publication on the actual main remote.
    remote = b.command([*git, 'remote', 'get-url', 'origin'])
    require(remote in {'https://github.com/mohsenbarari/trading_bot',
                       'https://github.com/mohsenbarari/trading_bot.git',
                       'git@github.com:mohsenbarari/trading_bot',
                       'git@github.com:mohsenbarari/trading_bot.git'}, 'real_public_origin_required')
    require(b.command([*git, 'ls-remote', 'origin', 'refs/heads/main']).split() ==
            [record['source_commit'], 'refs/heads/main'], 'published_main_drift')
    return {'schema': 'security_consumer_checkout/2', 'source_commit': record['source_commit'],
            'source_tree': record['source_tree'], 'controls_committed_and_published': True,
            'preserved_user_document': {**exception, 'verified': True},
            'user_document_modified': False, 'private_environment_read': False}

def control_binding(record, b, owned=()):
    expected = {str(b.MARKER), str(b.BOTJ), str(STOP)}
    require(set(record['control_files']) == expected, 'control_binding_scope')
    for name, digest in record['control_files'].items():
        path = Path(name)
        require(list(b.identity(path.lstat())) == record['control_identities'][name]
                and sha256(b.read(path, maximum=65536, private=path != STOP)).hexdigest() == digest,
                'current_control_bytes_or_identity_changed')
    require(b.read(STOP, maximum=16, private=False) == b'STOP\n', 'automation_not_durably_paused')
    require(not os.path.lexists(b.PENDING), 'two_host_operation_pending')
    require(list(b.identity(b.SOURCE.lstat())) == record['source_mutex'], 'persistent_source_mutex_changed')
    if not any(path == b.RESERVATION for path, *_ in owned):
        require(not os.path.lexists(b.RESERVATION), 'existing_private_primary_operation')
    for path, fd, payload, identity in owned:
        require(b.identity(os.fstat(fd)) == identity == b.identity(path.lstat())
                and (payload is None and path == b.SOURCE and list(identity) == record['source_mutex']
                     or payload is not None and path == b.RESERVATION and b.document(path) == payload), 'owned_fence_changed')
    for number, path in enumerate(sorted(b.MARKER.parent.glob('production-queue-phase-*.json'))):
        require(number < 1000, 'queue_phase_inventory_bound')
        data = b.decode(b.read(path, maximum=65536))
        require(data.get('schema_version') == 1 and data.get('environment') == 'production'
                and data.get('secrets_disclosed') is False and data.get('status') in TERMINAL, 'pending_queue_phase')


def sync_parent(path):
    fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try: os.fsync(fd)
    finally: os.close(fd)


def persistent_source(record, b, stack):
    fd = os.open(b.SOURCE, os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK)
    stack.callback(os.close, fd)
    require(list(b.identity(os.fstat(fd))) == record['source_mutex'] == list(b.identity(b.SOURCE.lstat())),
            'persistent_source_mutex_changed')
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    require(list(b.identity(os.fstat(fd))) == record['source_mutex'] == list(b.identity(b.SOURCE.lstat())),
            'persistent_source_mutex_changed')
    return b.SOURCE, fd, None, b.identity(os.fstat(fd))


def fresh_reservation(record, digest, b, stack):
    path = b.RESERVATION
    b.private_parent(path)
    require(not os.path.lexists(path), 'existing_private_primary_operation')
    fd, temporary = tempfile.mkstemp(prefix='.security-consumers-', dir=path.parent)
    stack.callback(os.close, fd)
    stack.callback(lambda: Path(temporary).unlink(missing_ok=True))
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    payload = {'schema': 'security_consumer_release_guard/2',
        'operation_id': record['operation_id'], 'nonce': record['nonce'], 'pid': os.getpid(),
        'inode': os.fstat(fd).st_ino, 'source_commit': record['source_commit'], 'window_sha256': digest,
        'source_mutex': record['source_mutex']}
    b.write_all(fd, (json.dumps(payload, sort_keys=True) + '\n').encode());os.fsync(fd)
    b.publish_noreplace(temporary, path);sync_parent(path)
    return path, fd, payload, b.identity(os.fstat(fd))


def sealed_controls(record, worker):
    root = worker.workspace('bot', record)
    for name, digest in record['control_sources'].items():
        require(sha256(worker.read(root/name, maximum=131072, public=True)).hexdigest() == digest,
                'staged_control_source_changed')
    return root


def invoke(action, sites, record, evidence, digest, b, lock_fds, *, worker, service=None, projection=None, staged=True):
    source = sealed_controls(record, worker) if staged else HERE
    assets = [{'source': str(HERE/name), 'name': name, 'sha256': value}
              for name, value in record['control_sources'].items()]
    assets += [{'source': str(HELPER), 'name': HELPER.name, 'sha256': HELPER_SHA},
               {'source': str(evidence), 'name': 'window.json', 'sha256': digest}]
    destination = worker.workspace('bot', record)/'results'/((sites[0]+'-'+action+'-'+service+'.json') if service else 'unused.json')
    variables = {'security_action': action, 'security_coordinator_nonce': record['nonce'],
        'security_acceptance_sha256': digest, 'security_public_assets': assets,
        'security_public_evidence': [{'name': name, 'source': value['path'], 'sha256': value['sha256']}
                                    for name, value in record['evidence'].items()],
        'security_service': service or '', 'security_projection_json': json.dumps(projection or {}, sort_keys=True),
        'security_result_destination': str(destination)}
    canonical = Path('/root/trading-bot/trading_bot/.local')
    environment = {'PATH': str(ANSIBLE.parent)+':/usr/sbin:/usr/bin:/sbin:/bin', 'LANG': 'C.UTF-8',
        'ANSIBLE_CONFIG': str(source/'security_consumers_ansible.cfg'),
        'ANSIBLE_HOME': str(canonical/'security-maintenance-ansible-home'),
        'ANSIBLE_LOCAL_TEMP': str(canonical/'security-maintenance-ansible-tmp')}
    argv = [str(ANSIBLE), '-i', str(source/'security_consumers_inventory.yml'), str(source/'security_consumers.yml'),
            '--limit', ':'.join('security_'+site for site in sites), '-e', json.dumps(variables)]
    absolute = record['recovery']['deadline_utc'] if action in {'rollback', 'recovered_verify'} else record['deadline_utc']
    return b.execute_ansible(argv, environment, absolute, lock_fds=lock_fds)


def target_result(action, site, service, record, worker, c):
    path = worker.workspace('bot', record)/'results'/(site+'-'+action+'-'+service+'.json')
    value = json.loads(worker.read(path))
    require(value.get('schema') == 'security_consumer_target_result/2'
            and value.get('operation_id') == c.SUCCESSOR_OPERATION and value.get('nonce') == record['nonce']
            and value.get('source_commit') == record['source_commit'] and value.get('site') == site
            and value.get('service') == service and value.get('source_image_id') == c.CANDIDATE
            and all(value.get(k) is False for k in ('global_security_eligible', 'security_accepted', 'raw_gate_passed', 'raw_gate_waived')),
            'target_result_binding')
    require(value.get('status') in {'verified', 'rolled_back', 'rolled_back_after_postcheck_failure',
            'write_outcome_unknown', 'rollback_failed'}, 'target_result_status')
    require(value.get('image') in {record['images'][site], c.OLD_IMAGES[site]}, 'target_result_image')
    if value['status'] == 'verified':require(value['image'] == record['images'][site], 'target_result_image')
    if value['status'] in {'rolled_back', 'rolled_back_after_postcheck_failure'}:
        require(value['image'] == c.OLD_IMAGES[site], 'target_result_image')
    require(action != 'target' or value['status'] != 'rolled_back', 'target_result_status')
    require(value.get('id') is None and value['status'] == 'write_outcome_unknown'
            or type(value.get('id')) is str and re.fullmatch('[a-f0-9]{64}', value['id']), 'target_result_identifier')
    return value


def run(action, record, path, digest, worker, c, b, confirmation):
    c.assert_window(record, datetime.now(timezone.utc))
    worker.native('bot', c, record)
    checkout = source_binding(record, c, b, worker)
    control_binding(record, b)
    key = Path('/root/.ssh/id_ed25519_iran');info = key.lstat()
    require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and info.st_nlink == 1
            and stat.S_IMODE(info.st_mode) == 0o600, 'unsafe_existing_ssh_identity')
    with ExitStack() as stack:
        marker = os.open(b.MARKER, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        stack.callback(os.close, marker);fcntl.flock(marker, fcntl.LOCK_EX | fcntl.LOCK_NB)
        require(list(b.identity(os.fstat(marker))) == record['control_identities'][str(b.MARKER)]
                == list(b.identity(b.MARKER.lstat())), 'coordination_inode_changed')
        control_binding(record, b)
        source = persistent_source(record, b, stack)
        control_binding(record, b, (source,))
        locks = [marker, source[1]]
        if action == 'check':
            require(invoke('check', ('web', 'bot'), record, path, digest, b, locks, worker=worker) == 0,
                    'consumer_preflight_failed')
            return {'status': 'checked', 'checkout': checkout, 'global_security_eligible': False}
        require(confirmation == worker.CONFIRMATION, 'explicit_scoped_apply_required')
        root = worker.workspace('bot', record)
        result_path = root/('controller-stage-result.json' if action == 'stage' else 'controller-result.json')
        require(not os.path.lexists(result_path) and
                (action == 'stage' or not os.path.lexists(root/'controller-journal.json')),
                'existing_controller_attempt_not_adopted')
        reservation = fresh_reservation(record, digest, b, stack)
        owned = (source, reservation);locks += [reservation[1]]
        control_binding(record, b, owned)
        failure = None
        if action == 'stage':
            require(invoke('stage', ('web', 'bot'), record, path, digest, b, locks,
                           worker=worker, staged=False) == 0, 'public_asset_staging_failed')
            sealed_controls(record, worker)
            outcome = 'public_assets_staged'
        else:
            c.assert_window(record, datetime.now(timezone.utc), activating=True)
            require(invoke('check', ('web', 'bot'), record, path, digest, b, locks, worker=worker) == 0,
                    'consumer_preflight_failed')
            require(invoke('snapshot', ('web', 'bot'), record, path, digest, b, locks, worker=worker) == 0,
                    'consumer_snapshot_failed')
            projection = {};completed = [];write_pending = False
            journal = worker.workspace('bot', record)/'controller-journal.json'
            def save(phase):
                worker.write(b, journal, {'schema': 'security_consumer_controller_journal/2',
                    'operation_id': c.SUCCESSOR_OPERATION, 'nonce': record['nonce'], 'window_sha256': digest,
                    'source_commit': record['source_commit'], 'phase': phase, 'projection': projection,
                    'completed': [list(row) for row in completed], 'first_failure': failure,
                    'global_security_eligible': False, 'security_accepted': False}, exclusive=not journal.exists())
            save('preflight_complete')
            try:
                for site, service in c.ROLE_ORDER:
                    c.assert_window(record, datetime.now(timezone.utc), activating=True)
                    control_binding(record, b, owned)
                    source_binding(record, c, b, worker)
                    save('writing_'+site+'_'+service)
                    write_pending = True
                    require(invoke('target', (site,), record, path, digest, b, locks, worker=worker,
                                   service=service, projection=projection) == 0, 'target_worker_outcome_unknown')
                    result = target_result('target', site, service, record, worker, c)
                    require(result['status'] not in {'write_outcome_unknown', 'rollback_failed'},
                            'target_daemon_outcome_unknown_fences_retained')
                    write_pending = False
                    projection[site+'/'+service] = {'id': result['id'], 'image': result['image']}
                    if result['status'] != 'verified':
                        failure = {'site': site, 'service': service, 'stage': result['first_failure_stage'],
                                   'code': result['first_failure_code']};save('known_target_rolled_back')
                        break
                    completed.append((site, service));save('target_verified')
                else:
                    c.assert_window(record, datetime.now(timezone.utc))
                    require(invoke('verify', ('web', 'bot'), record, path, digest, b, locks,
                                   worker=worker, projection=projection) == 0, 'five_target_finalcheck_failed')
                    outcome = 'five_targets_verified_global_gates_pending';save('verified_pending_fence_cleanup')
                if failure is None and len(completed) == 5:
                    pass
                else:
                    raise RuntimeError('known_target_postcheck_failed')
            except Exception as error:
                # Only exact results proving known completed writes are recoverable.
                # Unknown requests keep canonical fences; observations never become cancellation proof.
                if write_pending:
                    failure = failure or {'stage': 'daemon_write', 'code': 'target_outcome_unknown'}
                    save('outcome_unknown_fences_retained');raise
                failure = failure or {'stage': 'forward_or_finalcheck', 'code': 'forward_guard_failed'}
                save('recovering_known_completed_targets')
                for site, service in reversed(completed):
                    c.assert_window(record, datetime.now(timezone.utc), recovering=True)
                    control_binding(record, b, owned)
                    require(invoke('rollback', (site,), record, path, digest, b, locks, worker=worker,
                                   service=service, projection=projection) == 0, 'owned_rollback_outcome_unknown')
                    result = target_result('rollback', site, service, record, worker, c)
                    require(result['status'] == 'rolled_back', 'owned_rollback_failed_fences_retained')
                    projection[site+'/'+service] = {'id': result['id'], 'image': result['image']};save('owned_target_restored')
                require(invoke('recovered_verify', ('web', 'bot'), record, path, digest, b, locks,
                               worker=worker, projection=projection) == 0, 'owned_recovery_postcheck_failed')
                outcome = 'rolled_back';save('recovered_pending_fence_cleanup')
        control_binding(record, b, owned)
        # Only the exact fresh reservation is removed. Both persistent mutexes
        # retain their original inode and bytes until their held FDs are closed.
        for target, fd, payload, identity in (reservation,):
            require(b.identity(os.fstat(fd)) == identity == b.identity(target.lstat())
                    and b.document(target) == payload, 'owned_fence_changed')
            target.unlink();sync_parent(target)
        require(list(b.identity(os.fstat(marker))) == record['control_identities'][str(b.MARKER)]
                == list(b.identity(b.MARKER.lstat())), 'coordination_inode_changed')
        require(list(b.identity(os.fstat(source[1]))) == record['source_mutex']
                == list(b.identity(b.SOURCE.lstat())), 'persistent_source_mutex_changed')
        result = {'schema': 'security_consumer_controller_result/2', 'operation_id': record['operation_id'],
            'nonce': record['nonce'], 'source_commit': record['source_commit'], 'source_tree': record['source_tree'],
            'window_sha256': digest, 'action': action, 'status': outcome, 'first_failure': failure,
            'fence_cleanup_confirmed': True, 'source_mutex_preserved': True, 'reservation_removed': True,
            'global_security_eligible': False,
            'security_accepted': False, 'raw_gate_passed': False, 'raw_gate_waived': False}
        try:
            # Still under M and SOURCE; the exact owned reservation is absent. This is
            # the last write: failure cannot trigger another handoff or rollback.
            worker.write(b, result_path, result, exclusive=True)
        except Exception:
            return {**result, 'status': 'known_outcome_final_receipt_pending', 'verified_outcome': outcome,
                'controller_result_written': False, 'receipt_failure_code': 'final_result_publication_failed',
                'checkout': checkout}
        return {**result, 'controller_result_written': True, 'checkout': checkout}


def main():
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0));os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('check', 'stage', 'apply'), nargs='?', default='check')
    parser.add_argument('--acceptance', type=Path, required=True)
    parser.add_argument('--acceptance-sha256', required=True)
    parser.add_argument('--apply')
    args = parser.parse_args()
    b = module(HELPER, HELPER_SHA)
    raw = b.read(args.acceptance)
    require(re.fullmatch('[a-f0-9]{64}', args.acceptance_sha256)
            and sha256(raw).hexdigest() == args.acceptance_sha256, 'acceptance_bytes_changed')
    acceptance = b.decode(raw)
    c = module(HERE/'security_consumer_contract.py', acceptance['control_sources']['security_consumer_contract.py'])
    worker = module(HERE/'security_consumer_site.py', acceptance['control_sources']['security_consumer_site.py'])
    record = worker.artifact(args.acceptance, args.acceptance_sha256, raw=raw)
    result = run(args.action, record, args.acceptance, args.acceptance_sha256, worker, c, b, args.apply)
    print(json.dumps({'operation_id': record['operation_id'], 'nonce': record['nonce'], **result}, sort_keys=True))
    return 1 if result['status'] in {'rolled_back', 'known_outcome_final_receipt_pending'} else 0


if __name__ == '__main__':
    try:raise SystemExit(main())
    except Exception:
        print('{"status":"failed_closed","detail":"security_consumer_successor_refused"}')
        raise SystemExit(1)
