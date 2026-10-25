#!/usr/bin/env python3
"""Finite fixed-image staging and LOAD. No datastore/container activation API."""
from contextlib import ExitStack
from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
import argparse
import json
import math
import os
from pathlib import Path
import re
import resource
import selectors
import shlex
import signal
import stat
import struct
import subprocess
import time
from types import ModuleType, SimpleNamespace

HERE = Path(__file__).resolve().parent
OPERATION = 'security-datastore-candidate-20261025-01'
CONFIRM = 'DATASTORE-CANDIDATE-IMAGES-ONLY-20261025-01'
ROOTS = {'bot': Path('/srv/trading-bot/production-data') / OPERATION,
         'web': Path('/srv/trading-bot') / OPERATION}
WEB_TOOLS = Path('/srv/trading-bot/security-datastore-candidate-tools-20261025-01')
DOCKER = ['/usr/bin/docker', '--host=unix:///var/run/docker.sock']
SSH = ['/usr/bin/ssh', '-i', '/root/.ssh/id_ed25519_iran', '-p', '37067', '-o', 'BatchMode=yes',
       '-o', 'IdentitiesOnly=yes', '-o', 'StrictHostKeyChecking=yes',
       '-o', 'UserKnownHostsFile=/root/.ssh/known_hosts', '-o', 'ForwardAgent=no',
       '-o', 'UpdateHostKeys=no', '-o', 'ConnectTimeout=10', '-o', 'ControlMaster=no', '-S', 'none',
       'root@65.109.220.59']
STREAM_HELPER_SHA = '2243eb34db8fe19107448a3933450123ab2838ed589b5c5608a0c93b40025302'
MAX_JSON, RESERVE, ROOT_RESERVE, MARGIN = 2*1024**2, 8*1024**3, 2*1024**3, 64*1024**2
OS_ROOT, OS_TMP = Path('/'), Path('/tmp')
PHASE = 'entry'
CONTRACT = None
SOURCE_LOCK = Path('/root/secure-envs/trading-bot/.production-runtime-source.lock')
MARKER = Path('/root/secure-envs/trading-bot/queue-cutover-artifacts/production-release.lock')
RESERVATION = Path('/root/secure-envs/trading-bot/release-control/private-primary-controller.lock')
OPEN_HEALTH = {'f2dbc15b9c9a2d14ef9669d1f6de37096161469a50c47bf6609f0383886b3a27',
               'f0f5ddc3efe1a55cc8a6f800ad59d1f41556a4f896cdc8ed7d20864c9d360e2a'}

# The remote entrypoint is compiled from the same pinned retained byte buffer.
BOOTSTRAP = r'''
from hashlib import sha256
from pathlib import Path
import os,stat,sys
path=Path(sys.argv[1]);pin=sys.argv[2];args=sys.argv[3:]
fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
try:
 s=os.fstat(fd);ident=lambda s:(s.st_dev,s.st_ino,s.st_mode,s.st_uid,s.st_gid,s.st_nlink,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
 assert path.is_absolute() and path.resolve(strict=True)==path and stat.S_ISREG(s.st_mode) and s.st_uid==s.st_gid==0 and s.st_nlink==1 and stat.S_IMODE(s.st_mode)==0o600 and 0<s.st_size<=131072
 raw=os.pread(fd,s.st_size+1,0);assert len(raw)==s.st_size and sha256(raw).hexdigest()==pin
 def check():
  assert ident(s)==ident(os.fstat(fd))==ident(path.lstat()) and sha256(os.pread(fd,s.st_size+1,0)).hexdigest()==pin
 check();code=compile(raw,str(path),'exec');check();sys.argv=[str(path),*args]
 try:exec(code,{'__name__':'__main__','__file__':str(path),'__package__':None,'__cached__':None})
 finally:check()
finally:os.close(fd)
'''


def need(ok, code):
    if not ok: raise RuntimeError(code)


def encode(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False) + '\n').encode()


def seal(path, pin, stack):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK); stack.callback(os.close, fd)
    s = os.fstat(fd)
    ident = lambda s: (s.st_dev, s.st_ino, s.st_mode, s.st_uid, s.st_gid, s.st_nlink, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
    need(path.resolve(strict=True) == path and stat.S_ISREG(s.st_mode) and s.st_uid == s.st_gid == 0
         and s.st_nlink == 1 and stat.S_IMODE(s.st_mode) == 0o600 and 0 < s.st_size <= 131072, 'source_metadata')
    raw = os.pread(fd, s.st_size + 1, 0)
    def check():
        need(ident(s) == ident(os.fstat(fd)) == ident(path.lstat())
             and sha256(os.pread(fd, s.st_size + 1, 0)).hexdigest() == pin, 'source_changed')
    need(len(raw) == s.st_size and sha256(raw).hexdigest() == pin, 'source_sha256'); check()
    m = ModuleType('_sealed_stage_' + path.stem); m.__file__ = str(path)
    exec(compile(raw, str(path), 'exec'), m.__dict__)
    return m, check


def control_buffer(path, pin, stack):
    need(path.is_absolute() and path.parent == HERE/'controls' and re.fullmatch(r'[a-f0-9]{32}\.json', path.name)
         and path.resolve(strict=True) == path, 'operation_path')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK); stack.callback(os.close, fd)
    s = os.fstat(fd)
    ident = lambda s: (s.st_dev, s.st_ino, s.st_mode, s.st_uid, s.st_gid, s.st_nlink, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
    need(stat.S_ISREG(s.st_mode) and s.st_uid == s.st_gid == 0 and s.st_nlink == 1
         and stat.S_IMODE(s.st_mode) == 0o600 and 0 < s.st_size <= MAX_JSON, 'operation_metadata')
    raw = os.pread(fd, s.st_size + 1, 0)
    def check():
        need(ident(s) == ident(os.fstat(fd)) == ident(path.lstat())
             and sha256(os.pread(fd, s.st_size + 1, 0)).hexdigest() == pin, 'operation_changed')
    need(len(raw) == s.st_size and sha256(raw).hexdigest() == pin, 'operation_sha256'); check()
    return raw, check


def control(raw, pin, phase, site, contract):
    need(sha256(raw).hexdigest() == pin, 'operation_sha256')
    value = contract.document(raw)
    fields = {'schema', 'operation_id', 'profile', 'phase', 'nonce', 'issued_at_utc', 'deadline_utc',
              'source_sha256', 'baselines', 'evidence_path', 'source_nonce', 'source_admission',
              'web_loaded', 'fences', 'security_accepted', 'activation_allowed'}
    need(type(value) is dict and set(value) == fields and value['schema'] == 'datastore_candidate_operation/1'
         and value['operation_id'] == OPERATION and value['profile'] in contract.PROFILES
         and value['phase'] in {'stage-web', 'load-web', 'load-native'}
         and re.fullmatch('[a-f0-9]{32}', value['nonce']) and value['security_accepted'] is False
         and value['activation_allowed'] is False, 'operation_scope')
    expected = {('bot', 'stage-web'): 'stage-web', ('web', 'receive-web'): 'stage-web',
                ('bot', 'load-web'): 'load-web', ('web', 'load-web'): 'load-web',
                ('bot', 'load-native'): 'load-native', ('web', 'stream-web'): 'load-native'}
    need(expected.get((site, phase)) == value['phase'], 'phase_scope')
    now = datetime.now(timezone.utc)
    dates = []
    for key in ('issued_at_utc', 'deadline_utc'):
        need(type(value[key]) is str and re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z', value[key]), 'operation_clock')
        dates.append(datetime.fromisoformat(value[key].replace('Z', '+00:00')))
    need(dates[0] <= now < dates[1] and 0 < (dates[1] - dates[0]).total_seconds() <= 900, 'operation_expired')
    need(type(value['source_sha256']) is dict and set(value['source_sha256']) == {
         'datastore_candidate_contract.py', 'datastore_candidate_stage.py', 'datastore_candidate_launch.py',
         *contract.HELPERS, 'load_native8da.py'} and all(type(x) is str and contract.HEX.fullmatch(x)
         for x in value['source_sha256'].values()), 'source_scope')
    need(all(value['source_sha256'][k] == v for k, v in contract.HELPERS.items())
         and value['source_sha256']['load_native8da.py'] == STREAM_HELPER_SHA, 'helper_pin')
    need(type(value['baselines']) is dict and set(value['baselines']) == {'bot', 'web'}, 'baseline_bindings')
    for binding in value['baselines'].values():
        need(type(binding) is dict and set(binding) == {'path', 'sha256'}
             and type(binding['path']) is str and Path(binding['path']).is_absolute()
             and type(binding['sha256']) is str and contract.HEX.fullmatch(binding['sha256']), 'baseline_binding')
    if value['phase'] == 'stage-web':
        need(value['source_nonce'] is None and value['source_admission'] is None and value['web_loaded'] is None, 'stage_source_scope')
    else:
        need(type(value['source_nonce']) is str and re.fullmatch('[a-f0-9]{32}', value['source_nonce'])
             and value['source_nonce'] != value['nonce'], 'admitted_source_required')
        public_binding(value['source_admission'], contract)
        if value['phase'] == 'load-web': need(value['web_loaded'] is None, 'web_loaded_required')
        else: public_binding(value['web_loaded'], contract)
    return value, time.monotonic() + (dates[1] - now).total_seconds()


def public_binding(value, contract):
    need(type(value) is dict and set(value) == {'paths', 'sha256'} and type(value['paths']) is dict
         and set(value['paths']) == {'bot', 'web'} and all(type(p) is str and Path(p).is_absolute()
         for p in value['paths'].values()) and type(value['sha256']) is str
         and contract.HEX.fullmatch(value['sha256']), 'public_site_binding')


def public(value):
    if type(value) is dict:
        need(not {'Env', 'Config', 'HostConfig', 'environment', 'resolved_environment'} & set(value), 'private_output_refused')
        for child in value.values(): public(child)
    elif type(value) is list:
        for child in value: public(child)


def checkpoint(ctx, *, verify_locks=False):
    need(time.monotonic() < ctx.end, 'deadline')
    for check in ctx.checks: check()
    if ctx.site == 'bot' and verify_locks:
        record = ctx.C.document(ctx.C.read(RESERVATION))
        need(record.get('schema') == 'datastore_candidate_reservation/1' and record.get('nonce') == ctx.op['nonce']
             and record.get('operation_sha256') == ctx.pin and type(record.get('pid')) is int
             and record['pid'] == os.getpid(), 'native_fences')
        locks = command(ctx, ['/usr/bin/cat', '/proc/locks'], checking=False).decode().splitlines()
        held = set()
        for line in locks:
            fields = line.split()
            if len(fields) >= 8 and fields[1:4] == ['FLOCK', 'ADVISORY', 'WRITE']:
                major, minor, inode = fields[5].split(':')
                held.add((int(fields[4]), os.makedev(int(major, 16), int(minor, 16)), int(inode)))
        for name in (MARKER, SOURCE_LOCK, RESERVATION):
            s = name.lstat(); need((os.getpid(), s.st_dev, s.st_ino) in held, 'native_fences')
        need(list(ctx.C.identity(SOURCE_LOCK.lstat())) == ctx.op['fences']['source_mutex']
             and SOURCE_LOCK.lstat().st_size == 0, 'source_mutex_changed')


def scope(ctx):
    with Path('/proc/self/cgroup').open('rb') as source: raw = source.read(65537)
    need(len(raw) < 65536 and raw.startswith(b'0::/') and b'\n' not in raw.strip()
         and raw.decode().strip().endswith('/security-datastore-stage-' + ctx.op['nonce'] + '-' + ctx.site + '.scope'), 'scope_required')
    root = Path('/sys/fs/cgroup') / raw.decode().strip()[4:]
    def value(name):
        with (root / name).open('rb') as f: raw = f.read(1025)
        need(len(raw) <= 1024, 'scope_bound'); return raw.decode().strip()
    quota, period = value('cpu.max').split()
    need(value('memory.max') == str(2*1024**3) and value('memory.swap.max') == '0'
         and value('pids.max') == '64' and quota != 'max' and 0 < int(quota) <= int(period), 'scope_required')
    unit = 'security-datastore-stage-' + ctx.op['nonce'] + '-' + ctx.site + '.scope'
    properties = command(ctx, ['/usr/bin/systemctl', 'show', unit,
        '--property=RuntimeMaxUSec,TimeoutStopUSec,KillMode,SendSIGKILL']).decode()
    fields = dict(line.split('=', 1) for line in properties.splitlines())
    need(set(fields) == {'RuntimeMaxUSec', 'TimeoutStopUSec', 'KillMode', 'SendSIGKILL'}
         and duration_usec(fields['RuntimeMaxUSec']) == 900000000
         and duration_usec(fields['TimeoutStopUSec']) == 5000000
         and fields['KillMode'] == 'control-group' and fields['SendSIGKILL'] == 'yes', 'scope_lifetime_required')


def duration_usec(value):
    need(type(value) is str and re.fullmatch(r'\d+(?:\.\d{1,6})?(?:us|ms|s|min|h)(?: \d+(?:\.\d{1,6})?(?:us|ms|s|min|h))*', value), 'scope_duration')
    units = {'us': 1, 'ms': 1000, 's': 1000000, 'min': 60000000, 'h': 3600000000}
    return sum(Decimal(number) * units[unit] for number, unit in re.findall(r'(\d+(?:\.\d{1,6})?)(us|ms|s|min|h)', value))


def command(ctx, argv, *, checking=True, maximum=MAX_JSON):
    if checking: checkpoint(ctx)
    out, err = os.memfd_create('datastore-public-output'), os.memfd_create('datastore-private-error')
    try:
        child = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=out, stderr=err,
            env={'PATH': '/usr/bin:/bin', 'LC_ALL': 'C', 'DOCKER_CONFIG': str(ctx.docker_config)},
            preexec_fn=ctx.N.bounds)
        try: code = child.wait(timeout=max(.01, min(20, ctx.end - time.monotonic())))
        except subprocess.TimeoutExpired:
            ctx.N.stop(child); raise RuntimeError('public_command_timeout') from None
        need(type(code) is int and code == 0 and os.fstat(out).st_size <= maximum and os.fstat(err).st_size <= maximum, 'public_command_failed')
        return os.pread(out, maximum + 1, 0)
    finally: os.close(out); os.close(err)


def root_temp_storage(*, expected_uid=0, expected_gid=0):
    """Observe public root/tmp metadata; never claim private daemon Env absence."""
    rows = []
    for path in (OS_ROOT, OS_TMP):
        info = path.lstat()
        need(path.resolve(strict=True) == path and stat.S_ISDIR(info.st_mode)
             and info.st_uid == expected_uid and info.st_gid == expected_gid
             and (stat.S_IMODE(info.st_mode) == 0o1777 if path == OS_TMP
                  else not info.st_mode & 0o022), 'native_root_temp_metadata')
        rows.append({'path': str(path), 'identity': [info.st_dev, info.st_ino,
            info.st_mode, info.st_uid, info.st_gid]})
    need(rows[0]['identity'][0] == rows[1]['identity'][0], 'native_root_temp_filesystem')
    return rows


def root_capacity(ctx):
    fs = os.statvfs(OS_ROOT); free = fs.f_bavail * fs.f_frsize
    budget = getattr(ctx, 'root_fallback_budget', 0)
    need(type(budget) is int and budget >= 0, 'native_root_capacity_pressure')
    if budget:
        need(ctx.site == 'bot' and ctx.C.same(root_temp_storage(), ctx.root_fallback_storage),
             'native_root_temp_changed')
    need(free >= ROOT_RESERVE + MARGIN + MAX_JSON + budget,
         'native_root_capacity_pressure')
    return free


def reserve_native_load(ctx, proof):
    """Keep the full import fallback budget on root as well as the data store."""
    need(ctx.site == 'bot' and all(type(proof[k]) is int and proof[k] > 0
         for k in ('archive_bytes', 'expanded_layer_bytes')), 'native_root_capacity_pressure')
    ctx.root_fallback_storage = root_temp_storage()
    ctx.root_fallback_budget = 2*proof['archive_bytes'] + 2*proof['expanded_layer_bytes']
    capacity(ctx, ctx.root_fallback_budget + MARGIN)
    return {'prospective_root_fallback_bytes': ctx.root_fallback_budget,
        'minimum_root_free_bytes': ROOT_RESERVE + MARGIN + MAX_JSON + ctx.root_fallback_budget,
        'root_and_temp': ctx.root_fallback_storage,
        'runtime_override_absence_proven': False, 'allocator_quota_asserted': False}


def capacity(ctx, additional=0):
    checkpoint(ctx)
    base = ROOTS[ctx.site].parent
    expected = 2064 if ctx.site == 'bot' else 2049
    need(base.stat().st_dev == expected and os.statvfs(base).f_frsize == 4096, 'storage_device')
    free = os.statvfs(base).f_bavail * 4096
    root_free = root_capacity(ctx)
    need(type(additional) is int and additional >= 0 and free >= RESERVE + MARGIN + additional,
         'capacity_floor')
    return {'data_free_bytes': free, 'root_free_bytes': root_free}


def publish(ctx, name, value):
    need(re.fullmatch(r'[a-z][a-z0-9-]*\.json', name), 'output_name')
    public(value); raw = encode(value); need(len(raw) <= MAX_JSON, 'output_bound')
    capacity(ctx, len(raw)); out = ctx.C.Output(ctx.work / name, MAX_JSON)
    try:
        for offset in range(0, len(raw), 65536): out.write(raw[offset:offset+65536])
        out.finish()
    finally: out.close()
    fd = os.open(ctx.work, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try: os.fsync(fd)
    finally: os.close(fd)
    return sha256(raw).hexdigest()


def baseline(ctx):
    binding = ctx.op['baselines'][ctx.site]
    raw = ctx.C.read(Path(binding['path']), pin=binding['sha256']); value = ctx.C.document(raw); public(value)
    fields = {'schema', 'site', 'host', 'port', 'machine_id', 'kernel', 'docker_store', 'data_roots', 'mount',
              'containers', 'image_map', 'app_consumers_completion_sha256', 'legacy_parking_receipt_sha256'}
    need(type(value) is dict and set(value) == fields and value['schema'] == 'datastore_candidate_public_baseline/1'
         and value['site'] == ctx.site and value['host'] == ('65.109.216.187' if ctx.site == 'bot' else '65.109.220.59')
         and value['port'] == (22 if ctx.site == 'bot' else 37067)
         and all(type(value[k]) is str and ctx.C.HEX.fullmatch(value[k]) for k in ('app_consumers_completion_sha256', 'legacy_parking_receipt_sha256')),
         'fresh_post_app_legacy_baseline_required')
    rows = value['containers']; count, running = (15, 9) if ctx.site == 'bot' else (27, 11)
    need(type(rows) is list and len(rows) == len({r['id'] for r in rows}) == count
         and sum(r['running'] is True for r in rows) == running, 'baseline_inventory')
    need(value['machine_id'] == public_read(Path('/etc/machine-id'), 128).decode().strip()
         and value['kernel'] == os.uname().release, 'host_identity')
    return value


def image_map(ctx):
    template = '{"id":{{json .ID}},"repository":{{json .Repository}},"tag":{{json .Tag}},"digest":{{json .Digest}}}'
    rows = [ctx.C.document(v) for v in command(ctx, [*DOCKER, 'image', 'ls', '--all', '--no-trunc', '--digests', '--format', template]).splitlines()]
    need(len(rows) <= 512 and all(set(r) == {'id', 'repository', 'tag', 'digest'}
         and ctx.C.DIGEST.fullmatch(r['id']) and all(type(v) is str and len(v) <= 1024 for v in r.values()) for r in rows), 'image_map')
    return sorted(rows, key=encode)


def observe(ctx):
    expected = ctx.baseline
    store = ctx.C.document(command(ctx, [*DOCKER, 'info', '--format', '{"root":{{json .DockerRootDir}},"driver":{{json .Driver}},"server":{{json .ServerVersion}}}']))
    need(ctx.C.same(store, expected['docker_store']), 'docker_store_changed')
    mount = ctx.C.document(command(ctx, ['/usr/bin/findmnt', '--json', '--target', str(ROOTS[ctx.site].parent),
                                         '--output', 'TARGET,SOURCE,FSTYPE,UUID']))
    need(ctx.C.same(mount, expected['mount']), 'storage_mount_changed')
    roots = []
    for item in expected['data_roots']:
        path = Path(item['path']); s = path.lstat()
        need(path.resolve(strict=True) == path and stat.S_ISDIR(s.st_mode) and s.st_uid == s.st_gid == 0
             and not s.st_mode & 0o022 and list(ctx.C.identity(s)[:5]) == item['identity'], 'data_root_changed')
        roots.append(item)
    ids = command(ctx, [*DOCKER, 'container', 'ls', '--all', '--quiet', '--no-trunc']).decode().splitlines()
    need(all(re.fullmatch('[a-f0-9]{64}', value) for value in ids)
         and set(ids) == {r['id'] for r in expected['containers']} and len(ids) == len(set(ids)), 'container_inventory_changed')
    fmt = '{"id":{{json .Id}},"name":{{json .Name}},"image":{{json .Image}},"running":{{json .State.Running}},"paused":{{json .State.Paused}},"restarting":{{json .State.Restarting}},"restart_policy":{{json .HostConfig.RestartPolicy}},"mounts":{{json .Mounts}},"ports":{{json .HostConfig.PortBindings}},"networks":{{json .NetworkSettings.Networks}},"pid_mode":{{json .HostConfig.PidMode}},"privileged":{{json .HostConfig.Privileged}},"read_only":{{json .HostConfig.ReadonlyRootfs}},"health":{{with index .State "Health"}}{{json .Status}}{{else}}null{{end}}}'
    rows = [ctx.C.document(v) for v in command(ctx, [*DOCKER, 'container', 'inspect', '--format', fmt, *sorted(ids)]).splitlines()]
    need(len(rows) == len(ids), 'container_projection')
    def projected(rows):
        result = []
        for row in sorted(rows, key=lambda r: r['id']):
            need(type(row['running']) is bool and type(row['paused']) is bool and type(row['restarting']) is bool
                 and not row['paused'] and not row['restarting'], 'container_state')
            value = dict(row)
            if row['id'] in OPEN_HEALTH:
                need(row['running'] and row['health'] in {'healthy', 'unhealthy', 'starting', None}, 'known_health_scope')
                value['health'] = 'KNOWN_OPEN'
            elif row['running']: need(row['health'] in {'healthy', None}, 'new_health_failure')
            result.append(value)
        return result
    need(ctx.C.same(projected(rows), projected(expected['containers'])), 'container_public_policy_changed')
    return {'docker_store': store, 'mount': mount, 'data_roots': roots, 'containers': projected(rows)}


def read_admission(ctx):
    binding = ctx.op['source_admission']; raw = ctx.C.read(Path(binding['paths'][ctx.site]), pin=binding['sha256'])
    value = ctx.C.document(raw); public(value)
    need(value['schema'] == 'datastore_candidate_web_admission/1' and value['status'] == 'archive_admitted_not_loaded'
         and value['profile'] == ctx.op['profile'] and value['nonce'] == ctx.op['source_nonce']
         and value['image_zip_sha256'] == ctx.spec['image_zip_sha256'] and value['image_zip_bytes'] == ctx.spec['image_zip_bytes']
         and value['docker_load_attempted'] is False and value['security_accepted'] is False
         and value['configuration_values_disclosed'] is False, 'source_admission')
    proof = value['archive_binding']
    need(proof['config_id'] == ctx.spec['config_id'] and proof['rootfs_layers'] == ctx.spec['layers']
         and proof['physical_graph_closed'] is True and proof['repo_tags_empty'] is True
         and proof['daemon_identity_measured'] is False and proof['archive_bytes'] == ctx.spec['archive']['uncompressed_bytes']
         and ctx.C.HEX.fullmatch(proof['archive_sha256']), 'source_archive_binding')
    return value


def source_tar(ctx, stack):
    admission = read_admission(ctx)
    path = ROOTS['web'] / (ctx.op['profile'] + '-' + ctx.op['source_nonce']) / 'candidate-image.tar'
    owner = getattr(ctx, 'retained_source_stack', stack)
    fd, size, check = owner.enter_context(ctx.C.held(path, admission['archive_binding']['archive_bytes'], size=admission['archive_binding']['archive_bytes']))
    need(list(ctx.C.identity(os.fstat(fd))) == admission['tar_binding']['identity']
         and ctx.C.digest_fd(fd, size, ctx.end) == admission['archive_binding']['archive_sha256'], 'source_tar_changed')
    ctx.checks.append(check)
    if owner is stack:
        # Standalone phase callers detach only this registration before held()
        # performs its own final identity check and closes the actual FD.
        stack.callback(ctx.checks.remove, check)
    return fd, size, admission


def observe_loaded(ctx, proof, actual):
    need(actual in proof['source_identities'], 'loaded_identity_unknown')
    # Never mention Descriptor in the direct Config branch (classic API lacks it).
    fmt = '{"id":{{json .Id}},"os":{{json .Os}},"architecture":{{json .Architecture}},"rootfs":{{json .RootFS}},"tags":{{json .RepoTags}},"config":{{json .Config}}}'
    value = ctx.C.document(command(ctx, [*DOCKER, 'image', 'inspect', '--format', fmt, actual]))
    need(set(value) == {'id', 'os', 'architecture', 'rootfs', 'tags', 'config'} and value['id'] == actual
         and value['os'] == 'linux' and value['architecture'] == 'amd64' and value['tags'] in (None, [])
         and ctx.C.same(value['rootfs'], {'Type': 'layers', 'Layers': ctx.spec['layers']})
         and ctx.C.same(ctx.C.container_config(value['config']), ctx.C.container_config(ctx.data['image']['Config'])), 'loaded_config_or_rootfs')
    identity = proof['source_identities'][actual]
    if actual != ctx.spec['config_id']:
        descriptor = ctx.C.document(command(ctx, [*DOCKER, 'image', 'inspect', '--format', '{{with index . "Descriptor"}}{{json .}}{{else}}null{{end}}', actual]))
        need(type(descriptor) is dict and descriptor.get('digest') == actual
             and descriptor.get('mediaType') == identity['media_type'] and type(descriptor.get('size')) is int
             and descriptor['size'] == identity['size'], 'loaded_descriptor_required')
    binding = {'schema': 'runtime_image_archive_binding/1', 'kind': identity['kind'],
        'docker_image_id': actual, 'scanner_config_id': ctx.spec['config_id'], 'config_sha256': ctx.spec['config_id'][7:],
        'rootfs_layers': ctx.spec['layers'], 'archive_sha256': proof['archive_sha256'],
        'archive_bytes': proof['archive_bytes'], 'physical_graph_closed': True,
        'security_accepted': False, 'findings_waived': False}
    if 'manifest_id' in proof: binding['manifest_sha256'] = proof['manifest_id'][7:]
    if identity['kind'] == 'OCI_index_to_manifest_to_config':
        binding['index_binding'] = {'root_index_sha256': actual[7:],
            'selected_manifest_sha256': proof['manifest_id'][7:], 'statement_semantics_validated': False,
            'signatures_verified': False, 'provenance_trusted': False}
    return {'host': ctx.baseline['host'], 'port': ctx.baseline['port'], 'image_loaded': True,
        'stable_before_after': True, 'daemon_image_id': actual, 'configuration_values_disclosed': False,
        'binding': binding}


def measured(ctx, proof, before, images):
    after = observe(ctx); need(ctx.C.same(before, after), 'runtime_changed_during_load')
    current = image_map(ctx); new = {r['id'] for r in current} - {r['id'] for r in images}
    need(len(new) == 1 and next(iter(new)) in proof['source_identities'], 'new_image_identity')
    actual = next(iter(new))
    need(ctx.C.same([r for r in current if r['id'] != actual], images), 'existing_image_or_tag_changed')
    leaf = observe_loaded(ctx, proof, actual); capacity(ctx)
    return leaf, after


def remote(ctx, phase):
    path = WEB_TOOLS / 'datastore_candidate_stage.py'
    argv = ['/usr/bin/systemd-run', '--scope', '--quiet', '--collect',
        '--unit=security-datastore-stage-' + ctx.op['nonce'] + '-web', '--property=CPUQuota=100%',
        '--property=MemoryMax=2G', '--property=MemorySwapMax=0', '--property=TasksMax=64',
        '--property=RuntimeMaxSec=900s', '--property=TimeoutStopSec=5s',
        '--property=KillMode=control-group', '--property=SendSIGKILL=yes',
        '/usr/bin/python3', '-I', '-B', '-c', BOOTSTRAP, str(path), ctx.op['source_sha256'][path.name],
        '--site', 'web', '--phase', phase, '--control', str(WEB_TOOLS / 'controls' / (ctx.op['nonce'] + '.json')),
        '--control-sha256', ctx.pin, '--apply', CONFIRM]
    return [*SSH, shlex.join(argv)]


def spawn(ctx, argv, *, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=None, normal_auth=False):
    checkpoint(ctx, verify_locks=True)
    if ctx.site == 'bot': ctx.on_rpc()
    if stderr is None: stderr = ctx.error_fd
    return subprocess.Popen(argv, stdin=stdin, stdout=stdout, stderr=stderr, bufsize=0,
        env=None if normal_auth else {'PATH': '/usr/bin:/bin', 'LC_ALL': 'C', 'DOCKER_CONFIG': str(ctx.docker_config)},
        preexec_fn=ctx.N.bounds)


def wait_success(ctx, child):
    try:
        if getattr(ctx, 'root_fallback_budget', 0):
            while True:
                capacity(ctx)
                left = ctx.end - time.monotonic()
                if left <= 0: raise subprocess.TimeoutExpired('owned_load_client', 0)
                try:
                    code = child.wait(timeout=min(.25, left)); break
                except subprocess.TimeoutExpired:
                    continue
        else:
            code = child.wait(timeout=max(.01, ctx.end - time.monotonic()))
    except subprocess.TimeoutExpired:
        ctx.N.stop(child); raise RuntimeError('client_outcome_unknown') from None
    need(type(code) is int and code == 0 and os.fstat(ctx.error_fd).st_size <= MAX_JSON
         and os.fstat(ctx.output_fd).st_size <= MAX_JSON, 'client_outcome_unknown')


def frame(ctx, value):
    public(value); raw = encode(value); need(len(raw) <= MAX_JSON, 'frame_bound')
    ctx.N.write_all(1, struct.pack('!I', len(raw)) + raw, ctx.end, lambda: checkpoint(ctx))


def receive_web(ctx):
    before = observe(ctx); images = image_map(ctx)
    need(ctx.C.same(images, ctx.baseline['image_map']), 'initial_image_map_changed')
    capacity(ctx, ctx.spec['image_zip_bytes'] + ctx.spec['archive']['size_bytes'] + ctx.spec['archive']['uncompressed_bytes'] + MAX_JSON)
    out = ctx.C.Output(ctx.work / 'artifact.zip', ctx.spec['image_zip_bytes'])
    try:
        os.set_blocking(0, False); total = 0
        while total < ctx.spec['image_zip_bytes']:
            raw = ctx.N.read_n(0, min(65536, ctx.spec['image_zip_bytes'] - total), ctx.end, lambda: capacity(ctx, ctx.spec['image_zip_bytes'] - total + ctx.spec['archive']['size_bytes'] + ctx.spec['archive']['uncompressed_bytes'] + MAX_JSON))
            out.write(raw); total += len(raw)
        need(ctx.N.read_n_or_eof(0, ctx.end, lambda: checkpoint(ctx)) == b'', 'artifact_stream_extra')
        out.finish()
        proof, tar_binding = ctx.C.unpack_image(out.fd, total, ctx.work, ctx.data, ctx.T, ctx.M,
                                               ctx.end, lambda amount: capacity(ctx, amount))
        out.check(); after = observe(ctx)
        need(ctx.C.same(before, after) and ctx.C.same(image_map(ctx), images), 'admission_runtime_changed')
        value = {'schema': 'datastore_candidate_web_admission/1', 'status': 'archive_admitted_not_loaded',
            'profile': ctx.op['profile'], 'nonce': ctx.op['nonce'], 'operation_sha256': ctx.pin,
            'image_zip_sha256': ctx.spec['image_zip_sha256'], 'image_zip_bytes': total,
            'archive_binding': proof, 'tar_binding': tar_binding, 'runtime_before': before, 'runtime_after': after,
            'docker_load_attempted': False, 'configuration_values_disclosed': False, 'security_accepted': False}
        pin = publish(ctx, 'admission.json', value)
        return {'status': value['status'], 'receipt_sha256': pin, 'security_accepted': False}
    finally: out.close()


def load_web(ctx):
    with ExitStack() as stack:
        fd, size, admission = source_tar(ctx, stack); before = observe(ctx); images = image_map(ctx)
        proof = admission['archive_binding']
        need(ctx.C.same(images, ctx.baseline['image_map']) and not ({r['id'] for r in images} & set(proof['source_identities'])), 'candidate_preexisting_or_map_changed')
        capacity(ctx, 2*size + 2*proof['expanded_layer_bytes'] + MARGIN)
        publish(ctx, 'load-request.json', {'schema': 'datastore_candidate_load_request/1', 'nonce': ctx.op['nonce'],
            'profile': ctx.op['profile'], 'operation_sha256': ctx.pin, 'status': 'daemon_outcome_unknown',
            'source_archive_sha256': proof['archive_sha256'], 'retry_allowed': False, 'daemon_cancellation_proven': False,
            'security_accepted': False})
        ctx.load_requested = True
        child = spawn(ctx, [*DOCKER, 'image', 'load', '--quiet'], stdin=fd, stdout=ctx.output_fd)
        try: wait_success(ctx, child)
        finally: ctx.N.stop(child)
        need(ctx.C.digest_fd(fd, size, ctx.end) == proof['archive_sha256'], 'source_post_load_sha')
        leaf, after = measured(ctx, proof, before, images)
        value = {'schema': 'datastore_candidate_site_loaded/1', 'status': 'loaded_measured_not_accepted',
            'profile': ctx.op['profile'], 'nonce': ctx.op['nonce'], 'loaded_site': leaf,
            'source_admission_sha256': ctx.op['source_admission']['sha256'], 'runtime_before': before,
            'runtime_after': after, 'configuration_values_disclosed': False, 'security_accepted': False}
        pin = publish(ctx, 'loaded-site.json', value)
        return {'status': value['status'], 'receipt_sha256': pin, 'security_accepted': False}


def stream_web(ctx):
    with ExitStack() as stack:
        fd, size, admission = source_tar(ctx, stack); before = observe(ctx); images = image_map(ctx)
        need(ctx.C.same(images, ctx.baseline['image_map']), 'source_image_map_changed')
        loaded = ctx.C.document(ctx.C.read(Path(ctx.op['web_loaded']['paths'][ctx.site]), pin=ctx.op['web_loaded']['sha256']))
        need(loaded['schema'] == 'datastore_candidate_site_loaded/1' and loaded['status'] == 'loaded_measured_not_accepted'
             and loaded['profile'] == ctx.op['profile'] and loaded['security_accepted'] is False, 'web_loaded_receipt')
        leaf = observe_loaded(ctx, admission['archive_binding'], loaded['loaded_site']['daemon_image_id'])
        need(ctx.C.same(leaf, loaded['loaded_site']), 'web_loaded_identity_changed')
        header = {'schema': 'datastore_candidate_source_stream/1', 'nonce': ctx.op['nonce'],
            'profile': ctx.op['profile'], 'operation_sha256': ctx.pin, 'archive_binding': admission['archive_binding'],
            'source_tar_identity': list(ctx.C.identity(os.fstat(fd))), 'web_loaded_site': leaf,
            'web_loaded_receipt_sha256': ctx.op['web_loaded']['sha256'], 'configuration_values_disclosed': False,
            'security_accepted': False}
        publish(ctx, 'stream-started.json', header); os.set_blocking(1, False); frame(ctx, header)
        offset, value = 0, sha256()
        while offset < size:
            checkpoint(ctx); raw = os.pread(fd, min(65536, size - offset), offset); need(raw, 'source_short')
            ctx.N.write_all(1, raw, ctx.end, lambda: checkpoint(ctx)); value.update(raw); offset += len(raw)
        need(value.hexdigest() == admission['archive_binding']['archive_sha256']
             and ctx.C.same(before, observe(ctx)) and ctx.C.same(images, image_map(ctx)), 'stream_source_changed')
        for check in ctx.checks: check()
        frame(ctx, {'schema': 'datastore_candidate_source_complete/1', 'nonce': ctx.op['nonce'],
            'operation_sha256': ctx.pin, 'archive_sha256': value.hexdigest(), 'archive_bytes': size,
            'source_tar_identity': header['source_tar_identity'], 'source_fd_held_and_hash_verified': True,
            'web_public_profile_and_image_map_stable': True, 'configuration_values_disclosed': False,
            'security_accepted': False})


def transfer_web(ctx):
    before = observe(ctx); images = image_map(ctx)
    need(ctx.C.same(images, ctx.baseline['image_map']), 'native_image_map_changed')
    publish(ctx, 'transfer-request.json', {'schema': 'datastore_candidate_transfer_request/1',
        'nonce': ctx.op['nonce'], 'profile': ctx.op['profile'], 'artifact_id': ctx.spec['image_artifact_id'],
        'status': 'remote_stage_outcome_unknown', 'native_archive_file_created': False, 'security_accepted': False})
    source = target = None
    try:
        source = spawn(ctx, ['/usr/bin/gh', 'api', 'repos/mohsenbarari/trading_bot/actions/artifacts/'
                            + str(ctx.spec['image_artifact_id']) + '/zip'], normal_auth=True)
        target = spawn(ctx, remote(ctx, 'receive-web'), stdin=subprocess.PIPE, stdout=ctx.output_fd)
        total, value = 0, sha256(); os.set_blocking(source.stdout.fileno(), False); os.set_blocking(target.stdin.fileno(), False)
        while total < ctx.spec['image_zip_bytes']:
            raw = ctx.N.read_n(source.stdout.fileno(), min(65536, ctx.spec['image_zip_bytes'] - total), ctx.end, lambda: capacity(ctx))
            ctx.N.write_all(target.stdin.fileno(), raw, ctx.end, lambda: capacity(ctx)); value.update(raw); total += len(raw)
        need(ctx.N.read_n_or_eof(source.stdout.fileno(), ctx.end, lambda: checkpoint(ctx)) == b''
             and value.hexdigest() == ctx.spec['image_zip_sha256'], 'download_size_or_sha')
        target.stdin.close(); wait_success(ctx, source); wait_success(ctx, target)
        need(ctx.C.same(before, observe(ctx)) and ctx.C.same(images, image_map(ctx)), 'native_changed_during_transfer')
        result = ctx.C.document(os.pread(ctx.output_fd, MAX_JSON + 1, 0))
        need(result.get('status') == 'archive_admitted_not_loaded' and ctx.C.HEX.fullmatch(result['receipt_sha256'])
             and result.get('security_accepted') is False, 'web_stage_result')
        publish(ctx, 'web-stage-result.json', result); return result
    finally:
        for child in (source, target):
            if child is not None:
                ctx.N.stop(child)
                for stream in (child.stdin, child.stdout):
                    if stream is not None: stream.close()


def request_web_load(ctx):
    before, images = observe(ctx), image_map(ctx)
    need(ctx.C.same(images, ctx.baseline['image_map']), 'native_image_map_changed')
    publish(ctx, 'web-load-request.json', {'schema': 'datastore_candidate_remote_load_request/1',
        'nonce': ctx.op['nonce'], 'profile': ctx.op['profile'], 'operation_sha256': ctx.pin,
        'status': 'remote_daemon_outcome_unknown', 'retry_allowed': False,
        'daemon_cancellation_proven': False, 'security_accepted': False})
    child = spawn(ctx, remote(ctx, 'load-web'), stdout=ctx.output_fd)
    try: wait_success(ctx, child)
    finally: ctx.N.stop(child)
    need(ctx.C.same(before, observe(ctx)) and ctx.C.same(images, image_map(ctx)), 'native_changed_during_web_load')
    result = ctx.C.document(os.pread(ctx.output_fd, MAX_JSON + 1, 0))
    need(result.get('status') == 'loaded_measured_not_accepted' and result.get('security_accepted') is False
         and ctx.C.HEX.fullmatch(result['receipt_sha256']), 'web_load_result')
    publish(ctx, 'web-load-result.json', result); return result


def load_native(ctx):
    before = observe(ctx); images = image_map(ctx); admission = read_admission(ctx); proof = admission['archive_binding']
    need(ctx.C.same(images, ctx.baseline['image_map']) and not ({r['id'] for r in images} & set(proof['source_identities'])), 'candidate_preexisting_or_map_changed')
    # Reuse only the sealed old loader's public-unit inspection and byte I/O.
    # No old execution/profile/dependency entrypoint or module global is changed.
    daemon = ctx.N.public_daemon(SimpleNamespace(command=lambda argv, end: command(ctx, argv), read=public_read), ctx.end)
    root_fallback = reserve_native_load(ctx, proof)
    source = child = None
    try:
        source = spawn(ctx, remote(ctx, 'stream-web')); os.set_blocking(source.stdout.fileno(), False)
        header = ctx.N.read_frame(source.stdout.fileno(), ctx.end, lambda: capacity(ctx), ctx.C)
        known_web = ctx.C.document(ctx.C.read(Path(ctx.op['web_loaded']['paths'][ctx.site]), pin=ctx.op['web_loaded']['sha256']))
        need(header['schema'] == 'datastore_candidate_source_stream/1' and header['nonce'] == ctx.op['nonce']
             and header['profile'] == ctx.op['profile'] and header['operation_sha256'] == ctx.pin
             and ctx.C.same(header['archive_binding'], proof) and header['web_loaded_receipt_sha256'] == ctx.op['web_loaded']['sha256']
             and header['configuration_values_disclosed'] is False and header['security_accepted'] is False
             and known_web['schema'] == 'datastore_candidate_site_loaded/1' and known_web['status'] == 'loaded_measured_not_accepted'
             and known_web['profile'] == ctx.op['profile'] and known_web['security_accepted'] is False
             and ctx.C.same(header['web_loaded_site'], known_web['loaded_site'])
             and ctx.C.same(header['source_tar_identity'], admission['tar_binding']['identity']), 'source_header')
        publish(ctx, 'load-request.json', {'schema': 'datastore_candidate_load_request/1', 'nonce': ctx.op['nonce'],
            'profile': ctx.op['profile'], 'operation_sha256': ctx.pin, 'status': 'daemon_outcome_unknown',
            'source_archive_sha256': proof['archive_sha256'], 'retry_allowed': False, 'daemon_cancellation_proven': False,
            'source_header': header, 'public_default_temp_contract': daemon,
            'root_fallback': root_fallback, 'security_accepted': False})
        ctx.load_requested = True
        child = spawn(ctx, [*DOCKER, 'image', 'load', '--quiet'], stdin=subprocess.PIPE, stdout=ctx.output_fd)
        os.set_blocking(child.stdin.fileno(), False); total, value = 0, sha256()
        while total < proof['archive_bytes']:
            raw = ctx.N.read_n(source.stdout.fileno(), min(65536, proof['archive_bytes'] - total), ctx.end, lambda: capacity(ctx))
            ctx.N.write_all(child.stdin.fileno(), raw, ctx.end, lambda: capacity(ctx)); total += len(raw); value.update(raw)
        footer = ctx.N.read_frame(source.stdout.fileno(), ctx.end, lambda: capacity(ctx), ctx.C)
        expected = {'schema': 'datastore_candidate_source_complete/1', 'nonce': ctx.op['nonce'],
            'operation_sha256': ctx.pin, 'archive_sha256': proof['archive_sha256'], 'archive_bytes': proof['archive_bytes'],
            'source_tar_identity': header['source_tar_identity'], 'source_fd_held_and_hash_verified': True,
            'web_public_profile_and_image_map_stable': True, 'configuration_values_disclosed': False, 'security_accepted': False}
        need(value.hexdigest() == proof['archive_sha256'] and ctx.C.same(footer, expected)
             and ctx.N.read_n_or_eof(source.stdout.fileno(), ctx.end, lambda: capacity(ctx)) == b'', 'source_footer')
        child.stdin.close(); wait_success(ctx, source); wait_success(ctx, child)
        publish(ctx, 'clients-observed.json', {'schema': 'datastore_candidate_load_clients/1', 'ssh_exit_code': 0,
            'docker_exit_code': 0, 'both_clients_reaped': True, 'stream_bytes': total,
            'stream_sha256': value.hexdigest(), 'source_footer': footer, 'security_accepted': False})
        leaf, after = measured(ctx, proof, before, images)
        value = {'schema': 'datastore_candidate_loaded/1', 'status': 'both_sites_loaded_measured_not_accepted',
            'profile': ctx.op['profile'], 'config_id': ctx.spec['config_id'], 'rootfs_layers': ctx.spec['layers'],
            'source_admission_sha256': ctx.op['source_admission']['sha256'], 'archive_sha256': proof['archive_sha256'],
            'archive_bytes': proof['archive_bytes'], 'sites': {'bot': leaf, 'web': header['web_loaded_site']},
            'runtime_before': before, 'runtime_after': after, 'root_fallback': root_fallback,
            'configuration_values_disclosed': False,
            'datastore_activation_performed': False, 'security_accepted': False}
        pin = publish(ctx, 'loaded-candidate.json', value)
        return {'status': value['status'], 'receipt_sha256': pin, 'security_accepted': False}
    finally:
        for process in (source, child):
            if process is not None:
                ctx.N.stop(process)
                for stream in (process.stdin, process.stdout):
                    if stream is not None: stream.close()


def public_read(path, maximum):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd); need(stat.S_ISREG(before.st_mode) and before.st_uid == 0
             and before.st_nlink == 1 and not before.st_mode & 0o022 and 0 < before.st_size <= maximum, 'public_unit_metadata')
        raw = os.pread(fd, before.st_size + 1, 0)
        ident = lambda s: (s.st_dev, s.st_ino, s.st_mode, s.st_uid, s.st_gid, s.st_nlink, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
        need(len(raw) == before.st_size and ident(before) == ident(os.fstat(fd)) == ident(Path(path).lstat()), 'public_unit_changed')
        return raw
    finally: os.close(fd)


def execute(args, *, reservation_owned=False, fence_check=None, on_rpc=None):
    global PHASE, CONTRACT
    need(os.geteuid() == 0 and args.apply == CONFIRM and args.site in ROOTS, 'root_scope')
    with ExitStack() as stack:
        raw, first_check = control_buffer(Path(args.control), args.control_sha256, stack)
        def unique(rows):
            result = {}
            for key, value in rows:
                need(key not in result, 'operation_duplicate_key'); result[key] = value
            return result
        preliminary = json.loads(raw, object_pairs_hook=unique, parse_constant=lambda _: need(False, 'operation_constant'))
        C, check = seal(HERE / 'datastore_candidate_contract.py', preliminary['source_sha256']['datastore_candidate_contract.py'], stack)
        CONTRACT = C
        op, end = control(raw, args.control_sha256, args.phase, args.site, C)
        need(Path(args.control).stem == op['nonce'], 'operation_path')
        _, _, control_check = stack.enter_context(C.held(Path(args.control), MAX_JSON, args.control_sha256))
        need(args.site != 'bot' or reservation_owned is True and callable(fence_check) and callable(on_rpc), 'native_launcher_required')
        checks = [check, control_check, first_check]
        if args.site == 'bot': checks.append(fence_check)
        _, own_check = seal(HERE / 'datastore_candidate_stage.py', op['source_sha256']['datastore_candidate_stage.py'], stack); checks.append(own_check)
        modules = {}
        for name in (*C.HELPERS, 'load_native8da.py'):
            modules[name], source_check = seal(HERE / name, op['source_sha256'][name], stack); checks.append(source_check)
        signal.alarm(max(1, math.ceil(end - time.monotonic())))
        root = ROOTS[args.site]
        need(root.resolve(strict=True) == root and root.lstat().st_uid == root.lstat().st_gid == 0
             and stat.S_IMODE(root.lstat().st_mode) == 0o700, 'stage_root')
        work = root / (op['profile'] + '-' + op['nonce']); os.mkdir(work, 0o700)
        docker_config = work / 'docker-config'; os.mkdir(docker_config, 0o700)
        out, err = os.memfd_create('datastore-client-output'), os.memfd_create('datastore-client-error')
        stack.callback(os.close, out); stack.callback(os.close, err)
        ctx = SimpleNamespace(C=C, N=modules['load_native8da.py'], T=modules['runtime_image_archive_transport.py'],
            M=modules['admit_security_component_image_reuse.py'], op=op, pin=args.control_sha256, site=args.site,
            work=work, docker_config=docker_config, end=end, checks=checks, output_fd=out, error_fd=err,
            load_requested=False, on_rpc=on_rpc, spec=C.PROFILES[op['profile']],
            retained_source_stack=stack)
        scope(ctx); ctx.baseline = baseline(ctx); checkpoint(ctx, verify_locks=True); capacity(ctx)
        ctx.data = C.evidence(C.read(Path(op['evidence_path']), maximum=MAX_JSON, pin=ctx.spec['evidence_sha256']), ctx.spec)
        PHASE = args.phase
        if args.site == 'bot' and args.phase == 'stage-web': result = transfer_web(ctx)
        elif args.site == 'bot' and args.phase == 'load-web': result = request_web_load(ctx)
        elif args.site == 'bot' and args.phase == 'load-native': result = load_native(ctx)
        elif args.site == 'web' and args.phase == 'receive-web': result = receive_web(ctx)
        elif args.site == 'web' and args.phase == 'load-web': result = load_web(ctx)
        elif args.site == 'web' and args.phase == 'stream-web': stream_web(ctx); return None
        else: raise RuntimeError('phase_scope')
        checkpoint(ctx, verify_locks=True); return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--site', choices=ROOTS, required=True); p.add_argument('--phase', required=True)
    p.add_argument('--control', required=True); p.add_argument('--control-sha256', required=True); p.add_argument('--apply', required=True)
    args = p.parse_args()
    try:
        result = execute(args)
        if result is not None: print(json.dumps(result, sort_keys=True))
        return 0
    except Exception as error:
        code = CONTRACT.error_code(error) if CONTRACT is not None else 'entry_not_verified'
        print(json.dumps({'status': 'incomplete', 'phase': PHASE, 'code': code,
                          'retry_load_allowed': False, 'daemon_cancellation_proven': False, 'security_accepted': False}), file=__import__('sys').stderr)
        return 1


if __name__ == '__main__': raise SystemExit(main())
