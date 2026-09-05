"""Exact-scope, owner-approved Account1 incident hotfix. No data migration."""
import argparse
from contextlib import contextmanager, ExitStack
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import time

OLD = 'a972e38908af199723033fdec24c9af65069c1f3'
NEW = '1f972a48ade3037d8455d574d14a598851ef5640'
OLD_IMAGE = 'sha256:9782e8c792024ecb2407ae37ccb055d3be93640affe9a128bf417b4ddc529ed8'
NEW_IMAGE = 'sha256:c0fd39806381256712719e1ae6be06cba0b8f710934140244ae9398e5e32196f'
PORTABLE = 'c1eaefa0361ca93980a2a7dd692477ef25863b0c85689975763a51c23b9153ea'
ROLE = 'market-capture-account1'
PROJECT = 'market-private-pipeline-primary'
CONTAINER = PROJECT + '-' + ROLE + '-1'
ROOT = Path('/srv/trading-bot/market-pipeline-releases') / OLD
DATA = Path('/srv/trading-bot/market-data-staging-shadow')
STATE = DATA / 'state' / ROLE / ROLE
SESSION = DATA / 'sessions/account1'
MARKER = SESSION / 'authority-container.json'
PARENT_LOCK = Path('/root/secure-envs/trading-bot/queue-cutover-artifacts/production-release.lock')
OPS = Path('/srv/trading-bot/incident-recovery/20260905-account1')
OVERRIDE = OPS / 'account1-hotfix-20260905.override.json'
JOURNAL = OPS / 'handoff.json'

def require(condition, reason):
    if not condition:
        raise RuntimeError(reason)

def utc():
    return datetime.now(timezone.utc).isoformat()

def command(args, timeout=60):
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    require(result.returncode == 0, 'command_failed:' + args[0] + ':' + str(result.returncode))
    return result.stdout

def inspect(target, image=False):
    return json.loads(command(['docker'] + (['image'] if image else []) + ['inspect',target]))[0]

def digest(data):
    return hashlib.sha256(data).hexdigest()

def file_check(path, uid, mode=0o600):
    require(path.resolve() == path, 'symlink_path')
    s = path.lstat()
    require(stat.S_ISREG(s.st_mode) and s.st_nlink == 1 and s.st_uid == uid
            and stat.S_IMODE(s.st_mode) == mode, 'unsafe_file_metadata')
    return s

def atomic(path, value, uid=0, gid=0):
    temporary = path.with_name('.' + path.name + '.' + str(os.getpid()))
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, 'w') as stream:
            os.fchown(stream.fileno(), uid, gid)
            json.dump(value, stream, sort_keys=True)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)

@contextmanager
def held(path, uid):
    before = file_check(path, uid)
    fd = os.open(path, os.O_RDWR | os.O_NOFOLLOW)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        live = os.fstat(fd)
        require((before.st_dev,before.st_ino) == (live.st_dev,live.st_ino), 'lock_inode_drift')
        yield
        require(path.stat().st_ino == live.st_ino, 'lock_replaced')
    finally:
        os.close(fd)

def compose(new=False):
    args = ['docker','compose','-p',PROJECT,'--profile','web','--env-file',str(ROOT/'web.release.env')]
    for name in ('deploy/market-data/compose.yml','deploy/market-data/compose.web.yml',
                 'account1-replay-recovery.override.yml','account1-live-recovery-20260905.yml'):
        args += ['-f',str(ROOT/name)]
    if new:
        args += ['-f',str(OVERRIDE)]
    return args

def validate_config(old, new):
    a, b = old['services'], new['services']
    require(set(a) == set(b), 'service_set_drift')
    for role in a:
        if role != ROLE:
            require(a[role] == b[role], 'unrelated_service_drift')
    expected = json.loads(json.dumps(a[ROLE]))
    expected['image'] = NEW_IMAGE
    expected['environment']['MARKET_PIPELINE_RELEASE_SHA'] = NEW
    expected.setdefault('labels',{})['org.opencontainers.image.revision'] = NEW
    require(expected == b[ROLE], 'unexpected_target_config_drift')
    for key in ('networks','volumes','secrets','configs'):
        require(old.get(key) == new.get(key), 'infrastructure_config_drift')
    require(not b[ROLE]['environment']['MARKET_CAPTURE_BACKFILL_NOT_BEFORE_UTC'], 'replay_reenabled')

def mounts(container):
    return sorted((x['Source'], x['Destination'], x.get('RW')) for x in container['Mounts'])

def bystanders():
    ids = command(['docker','ps','-q']).split()
    rows = json.loads(command(['docker','inspect'] + ids)) if ids else []
    return {r['Id']: (r['Image'],r['State']['StartedAt']) for r in rows if r['Name'] != '/' + CONTAINER}

def owners():
    ids = command(['docker','ps','-q']).split()
    rows = json.loads(command(['docker','inspect'] + ids)) if ids else []
    return [r['Id'] for r in rows if any(m.get('Source') == str(SESSION) for m in r['Mounts'])]

def healthy_live(release, image, old_mounts, minimum_sequence, since):
    c = inspect(CONTAINER)
    require(c['State']['Running'] and c['Image'] == image and c['RestartCount'] == 0, 'runtime_not_stable')
    require(mounts(c) == old_mounts, 'runtime_mount_drift')
    require(owners() == [c['Id']], 'session_owner_overlap')
    h = json.loads((STATE/'health.json').read_text())
    t = datetime.fromisoformat(h['updated_at_utc'].replace('Z','+00:00')).timestamp()
    require(h.get('release_sha') == release and t >= since and time.time()-t < 35, 'heartbeat_stale')
    require(h.get('status') in ('live-ready','live-degraded'), 'capture_not_live')
    require(h.get('capture_sequence',0) > minimum_sequence, 'live_sequence_not_advancing')
    return {'observed_at_utc':utc(), 'container_id':c['Id'], 'restart_count':c['RestartCount'],
            'capture_sequence':h['capture_sequence'], 'capture_status':h['status'],
            'docker_health':c['State'].get('Health',{}).get('Status'),
            'historical_quarantine':sum(v.get('explicit_backfill',{}).get('quarantined',0) for v in h.get('sources',{}).values())}

def wait_live(release, image, old_mounts, seq, since):
    deadline = time.monotonic() + 120
    while True:
        try:
            return healthy_live(release,image,old_mounts,seq,since)
        except (RuntimeError,KeyError,ValueError):
            if time.monotonic() >= deadline:
                raise
            time.sleep(4)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply',action='store_true')
    args = parser.parse_args()
    require(os.geteuid() == 0, 'root_required')
    file_check(OVERRIDE,0)
    require(OPS.resolve() == OPS and stat.S_IMODE(OPS.stat().st_mode) == 0o700, 'operations_path_invalid')
    with held(PARENT_LOCK,0):
        lock_bytes = PARENT_LOCK.read_bytes()
        parent = json.loads(lock_bytes)
        info = PARENT_LOCK.stat()
        require(parent.get('release_sha') == '4ef8e6dcb2d361b763bd8b72dc730c1f978f564a'
                and parent.get('schema') == 'market_pipeline_maintenance_lock/1.0'
                and parent.get('environment') == 'production' and parent.get('host_role') == 'web'
                and parent.get('inode') == info.st_ino and parent.get('device') == info.st_dev, 'parent_binding_drift')
        require(not JOURNAL.exists(), 'journal_exists_no_blind_repeat')
        file_check(MARKER,10001)
        prior = json.loads(MARKER.read_text())
        require(prior == {'authority':'container','authorized_at_utc':'2026-09-03T10:21:22.459009Z',
                         'contract':'market_capture_authority/1.0','release_sha':OLD,'role':ROLE}, 'marker_drift')
        old = inspect(CONTAINER)
        require(old['Image'] == OLD_IMAGE and old['State']['Running'] and old['RestartCount'] == 0, 'prior_runtime_drift')
        require(owners() == [old['Id']], 'prior_owner_overlap')
        target = inspect(NEW_IMAGE, image=True)
        payload = {k:target.get(k) for k in ('Architecture','Config','Created','Os','RootFS')}
        require(digest(json.dumps(payload,sort_keys=True,separators=(',',':'),default=str).encode()) == PORTABLE,
                'image_content_mismatch')
        validate_config(json.loads(command(compose()+['config','--format','json'])),
                        json.loads(command(compose(True)+['config','--format','json'])))
        seq = json.loads((STATE/'health.json').read_text())['capture_sequence']
        untouched = bystanders()
        old_mounts = mounts(old)
        record = {'schema':'account1_scoped_hotfix_handoff/1.0','status':'PREPARED','created_at_utc':utc(),
                  'old_release':OLD,'new_release':NEW,'old_image':OLD_IMAGE,'new_image':NEW_IMAGE,
                  'portable_image_digest':PORTABLE,'parent_lock_sha256':digest(lock_bytes),
                  'prior_marker':prior,'old_container_id':old['Id'],'sequence_before':seq,
                  'data_deleted':False,'product_changed':False,'queue_changed':False,
                  'parent_handoff_changed':False,'replay_certified':False}
        if not args.apply:
            print(json.dumps({'status':'PREFLIGHT_PASS','old_release':OLD,'new_release':NEW}))
            return
        atomic(JOURNAL,record)
        stopped = False
        try:
            command(['docker','stop','--time','30',CONTAINER],timeout=50)
            stopped = True
            with ExitStack() as stack:
                stack.enter_context(held(STATE/'owner.lock',10001))
                stack.enter_context(held(SESSION/'owner.lock',10001))
                require(owners() == [], 'old_owner_not_quiesced')
                require(json.loads(MARKER.read_text()) == prior, 'marker_race')
                record['status'] = 'AUTHORITY_TRANSFER_INTENT'
                record['target_marker'] = {**prior,'release_sha':NEW,'authorized_at_utc':utc()}
                atomic(JOURNAL,record)
                atomic(MARKER,record['target_marker'],10001,10001)
                file_check(MARKER,10001)
                record['status'] = 'AUTHORITY_TRANSFERRED'
                atomic(JOURNAL,record)
            started = time.time()
            command(compose(True)+['up','-d','--no-deps','--no-build','--pull','never',ROLE],timeout=90)
            record['live_probe'] = wait_live(NEW,NEW_IMAGE,old_mounts,seq,started)
            require(bystanders() == untouched, 'unrelated_container_changed')
            require(PARENT_LOCK.read_bytes() == lock_bytes, 'parent_lock_changed')
            require(json.loads(MARKER.read_text()) == record['target_marker'], 'final_marker_drift')
            record['status'] = 'APPLIED_LIVE_HISTORICAL_REVIEW_RETAINED'
            record['completed_at_utc'] = utc()
            atomic(JOURNAL,record)
            print(json.dumps({k:record[k] for k in ('status','new_release','live_probe')}),flush=True)
        except BaseException:
            record['status'] = 'ROLLBACK_REQUIRED'
            atomic(JOURNAL,record)
            if stopped:
                command(['docker','stop','--time','30',CONTAINER],timeout=50)
                with ExitStack() as stack:
                    stack.enter_context(held(STATE/'owner.lock',10001))
                    stack.enter_context(held(SESSION/'owner.lock',10001))
                    require(owners() == [], 'rollback_owner_not_quiesced')
                    current = json.loads(MARKER.read_text())
                    require(current in (prior,record.get('target_marker')), 'rollback_marker_drift')
                    atomic(MARKER,prior,10001,10001)
                seq_now = json.loads((STATE/'health.json').read_text()).get('capture_sequence',seq)
                started = time.time()
                command(compose()+['up','-d','--no-deps','--no-build','--pull','never',ROLE],timeout=90)
                record['rollback_probe'] = wait_live(OLD,OLD_IMAGE,old_mounts,seq_now,started)
                record['status'] = 'ROLLED_BACK_LIVE'
                atomic(JOURNAL,record)
            raise

if __name__ == '__main__':
    main()
