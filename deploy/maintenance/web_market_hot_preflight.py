#!/usr/bin/env python3
"""Stage two public scripts; only explicit hot action performs a real backup.

The sealed C07 bundle, existing fence payloads, env and keys remain untouched.
"""
from contextlib import ExitStack
from hashlib import sha256
import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import resource
import selectors
import signal
import stat
import subprocess
import time
from types import ModuleType

SOURCE_COMMIT = '43ae1eae96b9bb54b0aaffae53aafcd473c89670'
ROOT = Path('/root/.local/security-market-hot-preflight-20261024-01')
TARGET = Path('/srv/trading-bot/backups/security-market-hot-preflight-20261024-01')
OLD_ROOT = Path('/root/.local/security-web-engine-20261024-task07')
PLAN_SHA = 'f8b4759ea648ec0d8b8528f43c74ecc281e68b2d2fd94431398923c62e0f16c5'
C_SHA = '81b8983cb0f790c95d0e51c3f3225bd60aa5eb8ef5037dd9c20f8dbc549e64b9'
OP = 'security-web-engine-20261023-07'
NONCE = '304b2ec68d304738a43794f5a666b6c8'
OWNER = 2188434
SCRIPTS = {'backup_market_pipeline_archive.py': 'c7695e2e9321aed8d39a008cb76fdf229010021e3b74132ac6dc2f82b7dec527',
           'prepare_market_pipeline_release.py': '8952ae8997101960baa3ae598c57d434dab69f86a7ae12ab72a5480a600b3953'}
CONFIRM = 'RUN-REAL-MARKET-HOT-PREFLIGHT-20261024-01'
ARTIFACT_MAX = 16 * 1024**3


def require(ok, code):
    if not ok:
        raise RuntimeError(code)


def safe(path):
    require(path.is_absolute() and path.resolve(strict=False) == path, 'path_redirected')
    for parent in path.parents:
        value = parent.lstat()
        require(stat.S_ISDIR(value.st_mode) and value.st_uid == 0 and not value.st_mode & 0o022, 'unsafe_parent')


def read(path, maximum=2097152):
    safe(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        require(stat.S_ISREG(before.st_mode) and before.st_uid == 0 and before.st_nlink == 1
                and not before.st_mode & 0o077 and before.st_size <= maximum, 'unsafe_public_or_control_file')
        raw = os.read(fd, maximum + 1)
        identity = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
        require(len(raw) == before.st_size and identity(before) == identity(os.fstat(fd)) == identity(path.lstat()), 'file_changed')
        return raw
    finally:
        os.close(fd)


def controller():
    path = OLD_ROOT / 'source/deploy/maintenance/web_engine_controller.py'
    raw = read(path, 65536)
    require(sha256(raw).hexdigest() == C_SHA, 'sealed_controller_changed')
    module = ModuleType('_exact_sealed_c07_hot_diagnostic')
    module.__file__ = str(path)
    exec(compile(raw, str(path), 'exec'), module.__dict__)
    return module


def stage(sources):
    require(set(sources) == set(SCRIPTS), 'public_source_inventory')
    for name, raw in sources.items():
        require(0 < len(raw) <= 131072 and sha256(raw).hexdigest() == SCRIPTS[name], 'published_public_source_changed')
    safe(ROOT)
    if not os.path.lexists(ROOT):
        ROOT.mkdir(mode=0o700)
        (ROOT / 'source').mkdir(mode=0o700)
        (ROOT / 'source/scripts').mkdir(mode=0o700)
        for name, raw in sources.items():
            fd = os.open(ROOT / 'source/scripts' / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            try:
                view = memoryview(raw)
                while view:
                    count = os.write(fd, view)
                    require(count > 0, 'public_stage_write_stalled')
                    view = view[count:]
                os.fsync(fd)
            finally:
                os.close(fd)
        for folder in (ROOT / 'source/scripts', ROOT / 'source', ROOT, ROOT.parent):
            fd = os.open(folder, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
    require({p.name for p in ROOT.iterdir()} == {'source'}, 'existing_diagnostic_attempt')
    for folder in (ROOT, ROOT / 'source', ROOT / 'source/scripts'):
        safe(folder)
        info = folder.lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and stat.S_IMODE(info.st_mode) == 0o700, 'public_stage_directory_unsafe')
    require({p.name for p in (ROOT / 'source').iterdir()} == {'scripts'}
            and {p.name for p in (ROOT / 'source/scripts').iterdir()} == set(SCRIPTS), 'public_stage_inventory')
    for name, expected in SCRIPTS.items():
        require(sha256(read(ROOT / 'source/scripts' / name, 131072)).hexdigest() == expected, 'staged_source_changed')


def owner_proof(C, plan, fds):
    C.native_contract(plan)
    require(plan['operation_id'] == OP and plan['nonce'] == NONCE, 'wrong_original_plan')
    source, reservation = (C.B.document(path) for path in (C.paths(plan)[1], C.P.RESERVATION))
    require(source == reservation and source['pid'] == OWNER and source['operation_id'] == OP
            and source['nonce'] == NONCE and source['manifest_sha256'] == PLAN_SHA, 'wrong_retained_owner')
    require(not os.path.lexists('/proc/' + str(OWNER)), 'previous_owner_alive')
    audit = C.B.document(C.ROOT / (OP + '.owned-intent.json'))
    previous = C.ROOT / (OP + '.owned-restore.json')
    require(audit['schema'] == 'web_engine_owned_intent/1' and audit['operation_id'] == OP and audit['nonce'] == NONCE
            and audit['manifest_sha256'] == PLAN_SHA and audit['intent_owner_pid'] == OWNER
            and audit['phase'] == 'intent_restored_health_incomplete' and audit['security_transition_completed'] is False
            and sha256(C.B.read(previous)).hexdigest() == audit['previous_restore_audit_sha256'], 'owned_audit_changed')
    originals = C.B.document(previous)['original_fences']
    require(len(originals) == 2 and {r['path'] for r in originals} == {str(C.paths(plan)[1]), str(C.P.RESERVATION)}, 'original_fence_inventory')
    for row in originals:
        value = os.fstat(fds[Path(row['path'])])
        require((value.st_dev, value.st_ino) == (row['device'], row['inode']) and {**row['payload'], 'pid': OWNER} == source,
                'original_fence_changed')
    unit = 'security-web-engine-07-owned-intent-' + NONCE + '.service'
    state = dict(line.split('=', 1) for line in C.B.command(['/usr/bin/systemctl', 'show', unit,
        '--property=ActiveState,MainPID,ExecMainStatus']).splitlines())
    require(state == {'ActiveState': 'failed', 'MainPID': '0', 'ExecMainStatus': '3'}
            and C.scope_empty(Path('/sys/fs/cgroup/system.slice/' + unit)), 'previous_continuation_not_terminal')
    journal = C.journal(plan, PLAN_SHA)
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


def command(plan):
    options = plan['market_backup']
    args = ['/usr/bin/python3', '-I', '-B', str(ROOT / 'source/scripts/backup_market_pipeline_archive.py'), 'create',
        '--env-file', options['env_file'], '--receipt', str(TARGET / 'market-pipeline-backup-receipt.json'),
        '--release-sha', options['release_sha'], '--release-tree', options['release_tree'], '--image-id', options['image_id'],
        '--image-input-signature', options['image_input_signature'], '--bluegreen-source-env']
    args += ['--backup-dir', str(TARGET), '--allow-running-writers', '--confirm', 'create-production-market-pipeline-archive-hot-backup']
    return args


def run_cli(argv, deadline, *, expected_help=False):
    def limits():
        resource.setrlimit(resource.RLIMIT_FSIZE, (ARTIFACT_MAX, ARTIFACT_MAX))
    child = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        start_new_session=True, env={'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LC_ALL': 'C.UTF-8',
                                   'DOCKER_HOST': 'unix:///var/run/docker.sock'}, preexec_fn=limits)
    data = bytearray()
    selector = selectors.DefaultSelector()
    try:
        selector.register(child.stdout, selectors.EVENT_READ)
        while selector.get_map():
            require(time.monotonic() < deadline, 'hot_backup_runtime_exceeded')
            for key, _ in selector.select(timeout=min(1, max(0, deadline - time.monotonic()))):
                chunk = os.read(key.fileobj.fileno(), 65536)
                if not chunk:
                    selector.unregister(key.fileobj)
                else:
                    require(len(data) + len(chunk) <= 2097152, 'hot_backup_diagnostics_exceeded')
                    data.extend(chunk)
        result = child.wait(timeout=max(0.1, min(10, deadline - time.monotonic())))
    finally:
        selector.close()
        try:
            os.killpg(child.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        child.wait(timeout=10)
        child.stdout.close()
    documents = []
    for line in bytes(data).splitlines():
        try:
            value = json.loads(line)
            if isinstance(value, dict):
                documents.append(value)
        except (ValueError, UnicodeDecodeError):
            pass
    value = documents[-1] if documents else {}
    if result != 0:
        code = value.get('reason_code', '')
        raise RuntimeError(code if isinstance(code, str) and re.fullmatch(r'[a-z0-9_]{1,96}', code) else 'real_backup_cli_failed')
    if expected_help:
        require(b'create' in data and b'verify' in data, 'real_backup_cli_help_invalid')
        return {'status': 'real_cli_import_passed', 'backup_performed': False}
    require(value.get('status') == 'pass' and value.get('backup_status') == 'PASS'
            and value.get('database_mutated') is False and value.get('services_started') is False
            and value.get('secrets_disclosed') is False, 'real_backup_receipt_not_passed')
    return {'status': 'pass', 'backup_status': 'PASS', 'database_mutated': False, 'services_started': False, 'secrets_disclosed': False}


def execute(action, sources):
    C = controller()
    plan = C.P.document(OLD_ROOT / 'plan.json', PLAN_SHA)
    with ExitStack() as stack:
        fds = {}
        for path in (C.P.MARKER, C.paths(plan)[1], C.P.RESERVATION):
            before = path.lstat()
            C.B.read(path, maximum=8192)
            fd = os.open(path, os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK)
            stack.callback(os.close, fd)
            require(C.B.identity(before) == C.B.identity(os.fstat(fd)) == C.B.identity(path.lstat()), 'original_fence_inode_changed')
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fds[path] = fd
        owner_proof(C, plan, fds)
        stage(sources)
        require(not os.path.lexists(TARGET), 'existing_hot_backup_target')
        safe(TARGET)
        run_cli(['/usr/bin/python3', '-I', '-B', str(ROOT / 'source/scripts/backup_market_pipeline_archive.py'), '--help'],
                time.monotonic() + 30, expected_help=True)
        if action == 'stage':
            return {'status': 'public_scripts_staged_checked', 'backup_performed': False}
        free = os.statvfs('/')
        require(free.f_bavail * free.f_frsize >= 44 * 1024**3, 'root_free_below_44gib')
        require(TARGET.parent.stat().st_dev == Path('/').stat().st_dev, 'backup_filesystem_changed')
        ids = {r['id'] for r in plan['containers'] if r['name'].endswith('-market-database-1')}
        probe = next(p for p in plan['datastore_probes'] if p['id'] in ids)
        size = C.B.command(['/usr/bin/docker', 'exec', probe['id'], 'psql', '-X', '-At', '-U', probe['user'], '-d', probe['database'],
                            '-c', 'SELECT pg_database_size(current_database())'])
        require(size.isdecimal() and 0 < int(size) <= ARTIFACT_MAX, 'market_restore_capacity_not_bounded')
        audit_path = ROOT / 'attempt.json'
        audit = {'schema': 'security_market_hot_preflight/1', 'source_commit': SOURCE_COMMIT,
                 'original_plan_sha256': PLAN_SHA, 'status': 'running', 'phase': 'create_running', 'pid': os.getpid(),
                 'production_writers_stopped': False, 'completion_unverified': True}
        C.write_json(audit_path, audit, exclusive=True)
        try:
            deadline = time.monotonic() + 1770  # Cleanup fits inside the outer 30-minute bound.
            run_cli(command(plan), deadline)
        except Exception as error:
            audit.update(status='failed', reason_code=static_code(error), completion_unverified=True,
                         restore_cleanup_must_be_proven=True)
            C.write_json(audit_path, audit, exclusive=False)
            raise
        audit.update(status='passed', phase='hot_create_restore_smoke_passed', real_create_restore_cleanup=True,
                     quiesced_upgrade_backup_accepted=False, completion_unverified=False)
        C.write_json(audit_path, audit, exclusive=False)
        return {'status': 'hot_create_restore_smoke_passed', 'quiesced_upgrade_backup_accepted': False,
                'completion_unverified': False, 'database_mutated': False, 'services_started': False, 'secrets_disclosed': False}


def static_code(error):
    value = str(error)
    return value if isinstance(error, RuntimeError) and re.fullmatch(r'[a-z0-9_]{1,96}', value) else 'hot_backup_preflight_refused'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('backup_source')
    parser.add_argument('prepare_source')
    parser.add_argument('action', choices=('stage', 'hot'), nargs='?', default='stage')
    parser.add_argument('--confirmation', default='')
    args = parser.parse_args()
    require(args.action == 'stage' or args.confirmation == CONFIRM, 'explicit_hot_backup_required')
    def expired(*_):
        raise RuntimeError('hot_backup_outer_deadline')
    signal.signal(signal.SIGALRM, expired)
    signal.alarm(1800)
    print(json.dumps(execute(args.action, {'backup_market_pipeline_archive.py': args.backup_source.encode(),
        'prepare_market_pipeline_release.py': args.prepare_source.encode()}), sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(json.dumps({'status': 'fail', 'reason_code': static_code(error), 'secrets_disclosed': False,
                          'completion_unverified': True}, sort_keys=True))
        raise SystemExit(1)
