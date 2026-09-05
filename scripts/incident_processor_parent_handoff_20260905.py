"""Exact-scope processor hotfix handoff; never mutates Market data directly."""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import time

OLD = 'f3be9ae2c4e9df0da31abaa41e91f327eb9573a2'
NEW = '9c613d39a14f7e42782fc40a50db87e709d735d7'
OLD_IMAGE = 'sha256:100bce844698e3a5a94bd5d7d6927e65f576bd5070faf15c9aff8b4025ec7e24'
NEW_IMAGE = 'sha256:b873801dca5983c1ac97068b14baa755c2031d438cf447d393cbdb4c749faa0a'
PORTABLE = 'ae3ec92e4e5f50ce00bf671a7a94e5c51a8568c41c342d2a26458c4596177b3e'
PARENT_RELEASE = '4ef8e6dcb2d361b763bd8b72dc730c1f978f564a'
ROLE = 'market-processor'
PROJECT = 'market-private-pipeline-primary'
CONTAINER = PROJECT + '-' + ROLE + '-1'
ROOT = Path('/srv/trading-bot/market-pipeline-releases') / OLD
STATE_ROOT = Path('/srv/trading-bot/market-data-staging-shadow/state/market-processor')
STATE = STATE_ROOT / ROLE
OWNER_LOCK = STATE / 'owner.lock'
PARENT_LOCK = Path('/root/secure-envs/trading-bot/queue-cutover-artifacts/production-release.lock')
OPS = Path('/srv/trading-bot/incident-recovery/20260905-processor')
OVERRIDE = OPS / 'processor-parent-hotfix-20260905.override.json'
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

def atomic(path, value):
    temporary = path.with_name('.' + path.name + '.' + str(os.getpid()))
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, 'w') as stream:
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
                 'processor-backlog-recovery.override.yml'):
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

def mounts(container):
    return sorted((x['Source'],x['Destination'],x.get('RW')) for x in container['Mounts'])

def bystanders():
    ids = command(['docker','ps','-q']).split()
    rows = json.loads(command(['docker','inspect']+ids)) if ids else []
    return {r['Id']:(r['Image'],r['State']['StartedAt']) for r in rows if r['Name']!='/'+CONTAINER}

def owners():
    ids = command(['docker','ps','-q']).split()
    rows = json.loads(command(['docker','inspect']+ids)) if ids else []
    return [r['Id'] for r in rows if any(m.get('Source')==str(STATE_ROOT) for m in r['Mounts'])]

def healthy(release,image,old_mounts,since):
    c=inspect(CONTAINER)
    require(c['State']['Running'] and c['Image']==image and c['RestartCount']==0,'runtime_not_stable')
    require(mounts(c)==old_mounts,'runtime_mount_drift')
    require(owners()==[c['Id']],'processor_owner_overlap')
    h=json.loads((STATE/'health.json').read_text())
    t=datetime.fromisoformat(h['updated_at_utc'].replace('Z','+00:00')).timestamp()
    require(h.get('release_sha')==release and t>=since and time.time()-t<60,'heartbeat_stale')
    require(h.get('status')=='live-shadow-ready','processor_not_ready')
    counters=h.get('counters',{})
    require(counters.get('archive_rejected')==0 and counters.get('research_contexts_unavailable')==0,
            'processor_export_rejected')
    return {'observed_at_utc':utc(),'container_id':c['Id'],'restart_count':0,
            'docker_health':c['State'].get('Health',{}).get('Status'),
            'updated_at_utc':h['updated_at_utc'],'archive_published':counters.get('archive_published'),
            'archive_rejected':0,'research_contexts_unavailable':0}

def wait_healthy(release,image,old_mounts,since):
    deadline=time.monotonic()+180
    last=None
    while time.monotonic()<deadline:
        try:
            last=healthy(release,image,old_mounts,since)
            if last['docker_health']=='healthy':
                return last
        except (RuntimeError,KeyError,ValueError) as exc:
            last=type(exc).__name__
        time.sleep(5)
    raise RuntimeError('processor_health_timeout:'+str(last))

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--apply',action='store_true')
    args=parser.parse_args()
    require(os.geteuid()==0,'root_required')
    file_check(OVERRIDE,0)
    require(OPS.resolve()==OPS and stat.S_IMODE(OPS.stat().st_mode)==0o700,'operations_path_invalid')
    with held(PARENT_LOCK,0):
        lock_bytes=PARENT_LOCK.read_bytes()
        parent=json.loads(lock_bytes)
        info=PARENT_LOCK.stat()
        require(parent.get('schema')=='market_pipeline_maintenance_lock/1.0'
                and parent.get('environment')=='production' and parent.get('host_role')=='web'
                and parent.get('release_sha')==PARENT_RELEASE and parent.get('inode')==info.st_ino
                and parent.get('device')==info.st_dev,'parent_binding_drift')
        require(not JOURNAL.exists(),'journal_exists_no_blind_repeat')
        old=inspect(CONTAINER)
        require(old['Image']==OLD_IMAGE and old['State']['Running'] and old['RestartCount']==0,'prior_runtime_drift')
        require(owners()==[old['Id']],'prior_owner_overlap')
        target=inspect(NEW_IMAGE,image=True)
        payload={k:target.get(k) for k in ('Architecture','Config','Created','Os','RootFS')}
        require(digest(json.dumps(payload,sort_keys=True,separators=(',',':'),default=str).encode())==PORTABLE,
                'image_content_mismatch')
        validate_config(json.loads(command(compose()+['config','--format','json'])),
                        json.loads(command(compose(True)+['config','--format','json'])))
        old_mounts=mounts(old)
        untouched=bystanders()
        record={'schema':'market_processor_scoped_hotfix_handoff/1.0','status':'PREPARED',
                'created_at_utc':utc(),'old_release':OLD,'new_release':NEW,'old_image':OLD_IMAGE,
                'new_image':NEW_IMAGE,'portable_image_digest':PORTABLE,
                'parent_lock_sha256':digest(lock_bytes),'old_container_id':old['Id'],
                'data_deleted':False,'product_changed':False,'queue_changed':False,
                'capture_changed':False,'parent_handoff_changed':False}
        if not args.apply:
            print(json.dumps({'status':'PREFLIGHT_PASS','old_release':OLD,'new_release':NEW}))
            return
        atomic(JOURNAL,record)
        stopped=False
        try:
            command(['docker','stop','--timeout','30',CONTAINER],timeout=50)
            stopped=True
            with held(OWNER_LOCK,10001):
                require(owners()==[],'old_owner_not_quiesced')
                record['status']='OWNER_QUIESCED'
                atomic(JOURNAL,record)
            started=time.time()
            command(compose(True)+['up','-d','--no-deps','--no-build','--pull','never',ROLE],timeout=90)
            record['live_probe']=wait_healthy(NEW,NEW_IMAGE,old_mounts,started)
            require(bystanders()==untouched,'unrelated_container_changed')
            require(PARENT_LOCK.read_bytes()==lock_bytes,'parent_lock_changed')
            record['status']='APPLIED_LIVE'
            record['completed_at_utc']=utc()
            atomic(JOURNAL,record)
            print(json.dumps({k:record[k] for k in ('status','new_release','live_probe')}),flush=True)
        except BaseException:
            record['status']='ROLLBACK_REQUIRED'
            atomic(JOURNAL,record)
            if stopped:
                command(['docker','stop','--timeout','30',CONTAINER],timeout=50)
                with held(OWNER_LOCK,10001):
                    require(owners()==[],'rollback_owner_not_quiesced')
                started=time.time()
                command(compose()+['up','-d','--no-deps','--no-build','--pull','never',ROLE],timeout=90)
                record['rollback_probe']=wait_healthy(OLD,OLD_IMAGE,old_mounts,started)
                record['status']='ROLLED_BACK_LIVE'
                atomic(JOURNAL,record)
            raise

if __name__=='__main__':
    main()
