#!/usr/bin/env python3
"""Read-only normal backup validation; stage only two pinned public scripts."""
from contextlib import ExitStack
from hashlib import sha256
import argparse
import fcntl
import importlib
import json
import os
from pathlib import Path
import stat
import sys
import time

ROOT = Path('/root/.local/security-backup-env-probe-20261024-01')
PLAN = Path('/root/.local/security-web-engine-20261024-task07/plan.json')
PLAN_SHA = 'f8b4759ea648ec0d8b8528f43c74ecc281e68b2d2fd94431398923c62e0f16c5'
MARKER = Path('/root/secure-envs/trading-bot/queue-cutover-artifacts/production-release.lock')
SOURCE = Path('/root/secure-envs/trading-bot/release-control/security-web-engine-20261023-07.source.lock')
RESERVATION = SOURCE.parent/'private-primary-controller.lock'
ENV = Path('/srv/trading-bot/market-pipeline-releases/f3be9ae2c4e9df0da31abaa41e91f327eb9573a2/web.release.env')
PINS = {'backup_market_pipeline_archive.py':'d6f3baa40a01e6205fc3c50c36a539a862dd3ccdc1310091cce16315ac4971cf',
        'prepare_market_pipeline_release.py':'8952ae8997101960baa3ae598c57d434dab69f86a7ae12ab72a5480a600b3953'}
CODES = frozenset({'backup_source_release_sha_invalid','backup_source_image_identity_invalid'})

def require(ok,code):
    if not ok:
        raise RuntimeError(code)

def identity(value):
    return value.st_dev,value.st_ino,value.st_size,value.st_mtime_ns,value.st_ctime_ns

def metadata(path):
    require(path.is_absolute() and path.resolve(strict=True) == path,'probe_path_unsafe')
    for parent in path.parents:
        value=parent.lstat()
        require(stat.S_ISDIR(value.st_mode) and value.st_uid == 0 and not value.st_mode & 0o022,'probe_parent_unsafe')
    value=path.lstat()
    require(stat.S_ISREG(value.st_mode) and value.st_uid == 0 and value.st_nlink == 1
            and stat.S_IMODE(value.st_mode) == 0o600,'probe_file_unsafe')
    return identity(value)

def read_public_or_control(path,maximum):
    before=metadata(path)
    require(before[2] <= maximum,'probe_input_bound')
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    try:
        raw=os.read(fd,maximum+1)
        require(len(raw) == before[2] and before == identity(os.fstat(fd)) == metadata(path),'probe_input_changed')
        return raw
    finally:
        os.close(fd)

def stage(sources):
    require(set(sources) == set(PINS),'probe_public_inventory_invalid')
    for name,data in sources.items():
        require(0 < len(data) <= 131072 and sha256(data).hexdigest() == PINS[name],'probe_public_source_changed')
    require(ROOT.resolve(strict=False) == ROOT,'probe_public_parent_unsafe')
    for parent in ROOT.parents:
        value=parent.lstat()
        require(stat.S_ISDIR(value.st_mode) and value.st_uid == 0 and not value.st_mode & 0o022,'probe_public_parent_unsafe')
    created=not os.path.lexists(ROOT)
    if created:
        ROOT.mkdir(mode=0o700)
        (ROOT/'source').mkdir(mode=0o700)
        (ROOT/'source/scripts').mkdir(mode=0o700)
        for name,data in {**sources,'__init__.py':b''}.items():
            fd=os.open(ROOT/'source/scripts'/name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
            try:
                view=memoryview(data)
                while view:
                    count=os.write(fd,view);require(count > 0,'probe_stage_write_stalled');view=view[count:]
                os.fsync(fd)
            finally:
                os.close(fd)
    for directory in (ROOT,ROOT/'source',ROOT/'source/scripts'):
        value=directory.lstat()
        require(directory.resolve(strict=True) == directory and stat.S_ISDIR(value.st_mode)
                and value.st_uid == 0 and stat.S_IMODE(value.st_mode) == 0o700,'probe_public_parent_unsafe')
    require({p.name for p in (ROOT/'source/scripts').iterdir()} == set(PINS)|{'__init__.py'},'probe_public_inventory_changed')
    for name,data in {**sources,'__init__.py':b''}.items():
        require(read_public_or_control(ROOT/'source/scripts'/name,131072) == data,'probe_staged_source_changed')
    return created

def marker_lock(fd):
    deadline=time.monotonic()+5
    while True:
        try:
            fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            return
        except BlockingIOError:
            remaining=deadline-time.monotonic();require(remaining > 0,'probe_marker_busy')
            time.sleep(min(.1,remaining))

def normal_validation(backup,release,env,options):
    # The existing trusted function alone consumes the protected input. Never
    # inspect/print/hash/copy its returned mapping or individual values.
    try:
        values=backup.validate_release_env(env,release_sha=options['release_sha'],image_id=options['image_id'],
                                           allow_target_identity_mismatch=True)
        del values
        return {'validation_passed':True,'reason_code':None}
    except (backup.BackupError,release.ReleaseContractError) as error:
        value=str(error)
        return {'validation_passed':False,'reason_code':value if value in CODES else 'backup_env_other_contract_refusal'}

def probe(sources):
    require(os.geteuid() == 0,'native_root_required')
    raw=read_public_or_control(PLAN,262144)
    require(sha256(raw).hexdigest() == PLAN_SHA,'probe_original_plan_changed')
    plan=json.loads(raw);options=plan['market_backup']
    require(options['env_file'] == str(ENV),'probe_env_binding_changed')
    staged=stage(sources)
    with ExitStack() as stack:
        owners=[]
        for path in (MARKER,SOURCE,RESERVATION):
            present=os.path.lexists(path)
            if path != MARKER and not present:
                continue
            before=metadata(path);payload=read_public_or_control(path,8192)
            fd=os.open(path,os.O_RDWR|os.O_NOFOLLOW|os.O_NONBLOCK);stack.callback(os.close,fd)
            marker_lock(fd) if path == MARKER else fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            require(before == identity(os.fstat(fd)) == metadata(path)
                    and read_public_or_control(path,8192) == payload,'probe_fence_changed')
            if path == MARKER:
                require(sha256(payload).hexdigest() == plan['marker']['sha256']
                        and before[:2] == (plan['marker']['device'],plan['marker']['inode']),'probe_marker_changed')
            else:
                owner=json.loads(payload)
                require(owner['schema'] == 'web_engine_transaction/1' and owner['operation_id'] == plan['operation_id'] and owner['nonce'] == plan['nonce']
                        and owner['manifest_sha256'] == PLAN_SHA and owner['pid'] == 2188434
                        and not os.path.lexists('/proc/2188434'),'probe_foreign_or_live_owner')
                owners.append(owner)
        require(os.path.lexists(SOURCE) == os.path.lexists(RESERVATION),'probe_partial_fence_release')
        require(not owners or len(owners) == 2 and owners[0] == owners[1],'probe_fence_payload_mismatch')
        env_before=metadata(ENV)
        require(0 < env_before[2] <= 65536,'probe_env_metadata_bound')
        value=ENV.lstat()
        require(list((value.st_dev,value.st_ino,value.st_mode,value.st_uid,value.st_gid,value.st_nlink,
                      value.st_size,value.st_mtime_ns,value.st_ctime_ns)) == options['env_file_identity'],'probe_env_metadata_binding_changed')
        require('scripts' not in sys.modules,'probe_import_namespace_occupied')
        sys.path.insert(0,str(ROOT/'source'))
        backup=importlib.import_module('scripts.backup_market_pipeline_archive')
        release=importlib.import_module('scripts.prepare_market_pipeline_release')
        result=normal_validation(backup,release,ENV,options)
        require(metadata(ENV) == env_before,'probe_env_metadata_changed')
    return {**result,'protected_env_values_disclosed':False,'protected_env_content_hashed':False,
            'backup_created':False,'runtime_changed':False,'public_scripts_staged':staged}

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('backup_source');parser.add_argument('release_source')
    args=parser.parse_args()
    result=probe({'backup_market_pipeline_archive.py':args.backup_source.encode(),
                  'prepare_market_pipeline_release.py':args.release_source.encode()})
    print(json.dumps(result,sort_keys=True))
    return 0

if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception:
        print(json.dumps({'validation_passed':False,'reason_code':'backup_env_probe_refused',
                          'protected_env_values_disclosed':False,'backup_created':False,'runtime_changed':False}))
        raise SystemExit(1)
