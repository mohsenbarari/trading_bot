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


def control_binding(record, b):
    expected = {str(path) for path in (b.MARKER, b.BOTJ, b.SOURCE, STOP)}
    require(set(record['control_files']) == expected, 'control_binding_scope')
    for path, digest in record['control_files'].items():
        require(sha256(b.read(Path(path), maximum=65536)).hexdigest() == digest, 'current_control_bytes_changed')
    require(b.read(STOP, maximum=16) == b'STOP\n', 'automation_not_durably_paused')
    require(not os.path.lexists(b.PENDING), 'two_host_operation_pending')
    for number, path in enumerate(sorted(b.MARKER.parent.glob('production-queue-phase-*.json'))):
        require(number < 1000, 'queue_phase_inventory_bound')
        data = b.decode(b.read(path, maximum=65536))
        require(data.get('schema_version') == 1 and data.get('environment') == 'production' and
                data.get('secrets_disclosed') is False and data.get('status') in TERMINAL, 'pending_queue_phase')


def source_binding(record, c, b):
    for name in c.CONTROL_SOURCES:
        require(sha256(b.read(HERE / name, maximum=65536, private=False)).hexdigest() ==
                record['control_sources'][name], 'release_control_source_changed')
    git = ['/usr/bin/git', '--no-optional-locks', '-C', str(REPO)]
    require(b.command([*git, 'symbolic-ref', '--short', 'HEAD']) == 'main' and
            b.command([*git, 'rev-parse', 'HEAD']) == record['source_commit'] and
            b.command([*git, 'rev-parse', 'HEAD^{tree}']) == record['source_tree'] and
            not b.command([*git, 'status', '--porcelain', '--untracked-files=normal']), 'clean_main_checkout_required')
    # A shared local clone must have its real original remote restored; a local
    # path origin cannot masquerade as publication on the actual main remote.
    remote = b.command([*git, 'remote', 'get-url', 'origin'])
    require(remote in {'https://github.com/mohsenbarari/trading_bot',
                       'https://github.com/mohsenbarari/trading_bot.git',
                       'git@github.com:mohsenbarari/trading_bot',
                       'git@github.com:mohsenbarari/trading_bot.git'}, 'real_public_origin_required')
    require(b.command([*git, 'ls-remote', 'origin', 'refs/heads/main']).split() ==
            [record['source_commit'], 'refs/heads/main'], 'published_main_drift')


def reserve(record, b, stack):
    b.private_parent(b.RESERVATION)
    require(not os.path.lexists(b.RESERVATION), 'existing_private_primary_operation')
    fd, temporary = tempfile.mkstemp(prefix='.security-consumers-', dir=b.RESERVATION.parent)
    stack.callback(os.close, fd)
    stack.callback(lambda: Path(temporary).unlink(missing_ok=True))
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    payload = {'schema': 'security_consumer_release_guard/1', 'operation_id': record['operation_id'],
               'nonce': record['nonce'], 'pid': os.getpid(), 'inode': os.fstat(fd).st_ino,
               'source_commit': record['source_commit']}
    b.write_all(fd, (json.dumps(payload, sort_keys=True) + '\n').encode())
    os.fsync(fd)
    b.publish_noreplace(temporary, b.RESERVATION)
    sync_parent(b.RESERVATION)
    return fd, payload


def sync_parent(path):
    fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try: os.fsync(fd)
    finally: os.close(fd)


def invoke(action, sites, record, evidence, digest, b, lock_fds):
    assets = [{'source': str(HERE / name), 'name': name, 'sha256': record['control_sources'][name]}
              for name in ('security_consumer_contract.py', 'security_consumer_site.py')]
    assets += [{'source': str(HELPER), 'name': HELPER.name, 'sha256': HELPER_SHA},
               {'source': str(evidence), 'name': 'acceptance.json', 'sha256': digest}]
    variables = {'security_action': action, 'security_coordinator_nonce': record['nonce'],
                 'security_acceptance_sha256': digest, 'security_public_assets': assets,
                 'security_public_evidence': [{'name': name, 'source': value['path'], 'sha256': value['sha256']}
                                              for name, value in record['evidence'].items()]}
    canonical = Path('/root/trading-bot/trading_bot/.local')
    environment = {'PATH': str(ANSIBLE.parent) + ':/usr/sbin:/usr/bin:/sbin:/bin', 'LANG': 'C.UTF-8',
                   'ANSIBLE_CONFIG': str(HERE / 'security_consumers_ansible.cfg'),
                   'ANSIBLE_HOME': str(canonical / 'security-maintenance-ansible-home'),
                   'ANSIBLE_LOCAL_TEMP': str(canonical / 'security-maintenance-ansible-tmp')}
    argv = [str(ANSIBLE), '-i', str(HERE / 'security_consumers_inventory.yml'),
            str(HERE / 'security_consumers.yml'), '--limit', ':'.join('security_' + site for site in sites),
            '-e', json.dumps(variables)]
    return b.execute_ansible(argv, environment, record['deadline_utc'], lock_fds=lock_fds)


def run(action, record, path, digest, worker, c, b, confirmation):
    worker.native('bot', c)
    source_binding(record, c, b)
    control_binding(record, b)
    # Check key metadata only; private key content is solely consumed by SSH.
    key = Path('/root/.ssh/id_ed25519_iran')
    info = key.lstat()
    require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and info.st_nlink == 1 and
            stat.S_IMODE(info.st_mode) == 0o600, 'unsafe_existing_ssh_identity')
    with ExitStack() as stack:
        locks = []
        for name in (b.MARKER, b.SOURCE):
            fd = os.open(name, os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK)
            stack.callback(os.close, fd)
            require(b.identity(os.fstat(fd)) == b.identity(name.lstat()), 'coordination_inode_changed')
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            locks.append(fd)
        require(not os.path.lexists(b.RESERVATION), 'existing_private_primary_operation')
        control_binding(record, b)
        if action == 'check':
            require(invoke('check', ('web', 'bot'), record, path, digest, b, locks) == 0, 'consumer_preflight_failed')
            return 'checked'
        require(confirmation == worker.CONFIRMATION, 'explicit_scoped_apply_required')
        reservation_fd, reservation = reserve(record, b, stack)
        locks.append(reservation_fd)
        control_binding(record, b)
        if action == 'stage':
            require(invoke('stage', ('web', 'bot'), record, path, digest, b, locks) == 0, 'public_asset_staging_failed')
            status = 'public_assets_staged'
        else:
            require(datetime.now(timezone.utc) < worker.CUTOFF, 'activation_cutoff_expired')
            require(invoke('check', ('web', 'bot'), record, path, digest, b, locks) == 0, 'consumer_preflight_failed')
            require(invoke('snapshot', ('web', 'bot'), record, path, digest, b, locks) == 0, 'consumer_snapshot_failed')
            attempted = []
            try:
                for site in ('web', 'bot'):
                    control_binding(record, b)
                    attempted.append(site)
                    require(invoke('apply', (site,), record, path, digest, b, locks) == 0, 'consumer_activation_failed')
                require(invoke('verify', ('web', 'bot'), record, path, digest, b, locks) == 0, 'two_site_postcheck_failed')
            except Exception:
                failures = [site for site in reversed(attempted)
                            if invoke('rollback', (site,), record, path, digest, b, locks) != 0]
                require(not failures, 'owned_rollback_failed_reservation_retained')
                status = 'rolled_back'
            else: status = 'completed'
        control_binding(record, b)
        require(b.identity(os.fstat(reservation_fd)) == b.identity(b.RESERVATION.lstat()) and
                b.document(b.RESERVATION) == reservation, 'owned_reservation_changed')
        b.RESERVATION.unlink()
        sync_parent(b.RESERVATION)
        return status


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('check', 'stage', 'apply'), nargs='?', default='check')
    parser.add_argument('--acceptance', type=Path, required=True)
    parser.add_argument('--acceptance-sha256', required=True)
    parser.add_argument('--apply')
    args = parser.parse_args()
    b = module(HELPER, HELPER_SHA)
    raw = b.read(args.acceptance)
    require(re.fullmatch('[a-f0-9]{64}', args.acceptance_sha256) and
            sha256(raw).hexdigest() == args.acceptance_sha256, 'acceptance_bytes_changed')
    acceptance = b.decode(raw)
    c = module(HERE / 'security_consumer_contract.py', acceptance['control_sources']['security_consumer_contract.py'])
    worker = module(HERE / 'security_consumer_site.py', acceptance['control_sources']['security_consumer_site.py'])
    record = worker.artifact(args.acceptance, args.acceptance_sha256, raw=raw)
    status = run(args.action, record, args.acceptance, args.acceptance_sha256, worker, c, b, args.apply)
    print(json.dumps({'status': status, 'operation_id': record['operation_id'], 'nonce': record['nonce'],
                      'capture_writer_dns_changed': False, 'migrations_executed': False, 'env_persisted_or_hashed': False}))
    return 0 if status != 'rolled_back' else 1


if __name__ == '__main__':
    try: raise SystemExit(main())
    except Exception:
        print('{"status":"failed_closed","detail":"security_consumer_release_refused"}')
        raise SystemExit(1)
