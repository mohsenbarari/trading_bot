#!/usr/bin/env python3
"""One finite hot Market backup, independent restore and cipher-only offhost copy."""
from contextlib import ExitStack
from datetime import datetime, timezone
from hashlib import sha256
import argparse
import fcntl
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
import subprocess
import sys
import time
from types import ModuleType

OP = 'security-web-market-hot3-20261024-01'
STAGE = Path('/root/.local') / OP
WORK = Path('/srv/trading-bot') / OP
MANAGED = Path('/srv/trading-bot/market-pipeline-releases/security-backup-managed-20261024-04')
TRANSPORT = Path('/root/.ssh/security-web-market-hot3-20261024-01/transport.json')
MANAGED_RECEIPT_SHA = '305de6c1fc3a1997e53f91c689f83d038c37383d27b15d6d4571281dfb233e9c'
MANAGED_EVIDENCE_SHA = '2e13ad0997eacd9ec8cf1f6cd6dc22494b3f81b0216a4ec684559f28e4520f3c'
RESERVE, WAL_TEMP, CIPHER_ALLOWANCE = 8 * 1024**3, 4 * 1024**3, 4096
CLEANUP_SECONDS = 120
PINS = {
    'web_engine_preflight.py': 'cbadbc1085dd5e351510ed719a9e6c779523c4d644f53198d4f9c0c1d7750679',
    'web_market_managed_config.py': 'b9bedbe70b449e1d21e3c6472c414cd69f8c8c11c5cafafc46b0b887fc886fba',
    'prepare_market_pipeline_primary_release.py': '898f4c824b2587bdc4ba039f4c78a9d69c901245ddca11a466bdfec60938c3d6',
    'prepare_market_pipeline_release.py': '6355aafa369b3abd652ff251a19fa9e329a8104bf9781ba197b1dc5e113677d5',
    'backup_market_pipeline_archive.py': '5193672e73c5a724ac95ce50ee42fa2c3d1450a92a4531011d3b9ab9ca121342',
    'crypt_market_pipeline_backup.py': '5a6f237eea9b042b1e51e0de6ea5dba827f66bb3707f667ce50f7955ee03a300',
    'web_market_hot3_offhost.py': 'a9a7824cbdd66a476be126f8b4aba2d9d19eceb451353b0fceb3848c5c3b96a8',
}


class Refusal(RuntimeError):
    pass


def require(ok, code):
    if not ok:
        raise Refusal(code)


def identity(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid,
            info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def read_public(path, maximum=131072, digest=None):
    require(path.is_absolute() and path.resolve(strict=True) == path, 'hot3_public_path')
    for parent in path.parents:
        info = parent.lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022, 'hot3_public_parent')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        before = os.fstat(fd)
        require(stat.S_ISREG(before.st_mode) and before.st_uid == 0 and before.st_nlink == 1
                and stat.S_IMODE(before.st_mode) == 0o600 and 0 < before.st_size <= maximum, 'hot3_public_metadata')
        raw = os.pread(fd, maximum + 1, 0)
        require(len(raw) == before.st_size and identity(before) == identity(os.fstat(fd)) == identity(path.lstat()), 'hot3_public_changed')
        require(digest is None or sha256(raw).hexdigest() == digest, 'hot3_public_digest')
        return raw
    finally:
        os.close(fd)


def document(path, digest=None):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, 'hot3_duplicate_key'); result[key] = value
        return result
    result = json.loads(read_public(path, digest=digest), object_pairs_hook=unique)
    require(type(result) is dict, 'hot3_public_document')
    return result


def write_json(path, payload, *, replace=False):
    raw = (json.dumps(payload, sort_keys=True, separators=(',', ':')) + '\n').encode()
    require(len(raw) <= 131072, 'hot3_control_bound')
    if os.path.lexists(path):
        require(replace and document(path).get('intent_sha256') == payload.get('intent_sha256'), 'hot3_control_exists')
    temporary = path.parent / ('.' + path.name + '.' + str(os.getpid()) + '.tmp')
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw); stream.flush(); os.fsync(stream.fileno())
        if replace:
            os.replace(temporary, path)
        else:
            os.link(temporary, path, follow_symlinks=False); temporary.unlink()
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try: os.fsync(directory)
        finally: os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)
    return sha256(raw).hexdigest()


class Clock:
    def __init__(self, text, *, maximum=7200):
        require(type(text) is str and re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z', text), 'hot3_deadline_invalid')
        self.deadline = datetime.fromisoformat(text.replace('Z', '+00:00'))
        self.started = time.monotonic()
        self.budget = (self.deadline - datetime.now(timezone.utc)).total_seconds()
        require(0 < self.budget <= maximum, 'hot3_deadline_invalid')

    def remaining(self):
        return min((self.deadline-datetime.now(timezone.utc)).total_seconds(), self.budget-(time.monotonic()-self.started))

    def check(self):
        require(self.remaining() > 0, 'hot3_deadline_expired')


def native(arguments, *, seconds=10, maximum=65536, stdin=None):
    child = subprocess.Popen(arguments, stdin=subprocess.DEVNULL if stdin is None else stdin,
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, start_new_session=True,
        env={'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LC_ALL': 'C.UTF-8', 'DOCKER_HOST': 'unix:///var/run/docker.sock'})
    deadline = time.monotonic() + seconds; output = bytearray()
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(child.stdout, selectors.EVENT_READ)
            while selector.get_map():
                require(time.monotonic() < deadline, 'hot3_command_timeout')
                for key, _ in selector.select(.1):
                    block = os.read(key.fileobj.fileno(), min(65536, maximum+1-len(output)))
                    if not block: selector.unregister(key.fileobj)
                    else:
                        output.extend(block); require(len(output) <= maximum, 'hot3_command_output_bound')
            require(child.wait(timeout=max(.01, deadline-time.monotonic())) == 0, 'hot3_command_failed')
        return bytes(output)
    finally:
        try: os.killpg(child.pid, signal.SIGKILL)
        except ProcessLookupError: pass
        child.wait(timeout=5); child.stdout.close()


def load_modules(sources):
    require(set(sources) == set(PINS), 'hot3_recipe_inventory')
    for name, raw in sources.items():
        require(0 < len(raw) <= 131072 and sha256(raw).hexdigest() == PINS[name], 'hot3_recipe_changed')
    require(not any(name == 'scripts' or name.startswith('scripts.') for name in sys.modules), 'hot3_namespace_occupied')
    package = ModuleType('scripts'); package.__path__ = []; sys.modules['scripts'] = package
    modules = {}
    for name in ('web_engine_preflight.py', 'prepare_market_pipeline_release.py', 'backup_market_pipeline_archive.py',
                 'prepare_market_pipeline_primary_release.py', 'crypt_market_pipeline_backup.py',
                 'web_market_managed_config.py', 'web_market_hot3_offhost.py'):
        fullname = 'scripts.' + name.removesuffix('.py')
        module = ModuleType(fullname); module.__file__ = str(STAGE/'source'/name); module.__package__ = 'scripts'
        sys.modules[fullname] = module; exec(compile(sources[name], str(STAGE/'source'/name), 'exec'), module.__dict__)
        modules[name] = module
    return modules


def modules_from_stage():
    return load_modules({name: read_public(STAGE/'source'/name, digest=digest) for name, digest in PINS.items()})


def roles(modules):
    return tuple(modules[name] for name in ('web_engine_preflight.py', 'backup_market_pipeline_archive.py',
        'prepare_market_pipeline_primary_release.py', 'web_market_managed_config.py', 'crypt_market_pipeline_backup.py'))


def check_marker(P, H, fd, expected=None):
    info = os.fstat(fd)
    require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and info.st_nlink == 1
            and stat.S_IMODE(info.st_mode) == 0o600 and (info.st_dev, info.st_ino) == (H.MARKER_DEVICE, H.MARKER_INODE)
            and identity(info) == identity(H.MARKER.lstat())
            and (expected is None or list(identity(info)) == expected), 'hot3_marker_changed')
    require(sha256(os.pread(fd, 8193, 0)).hexdigest() == H.MARKER_SHA, 'hot3_marker_changed')
    # A surviving scope must own the inherited file description, not a newly
    # opened pathname which could silently drop the original serialization.
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)


def resource_binding(nonce):
    return {'container': 'market_pipeline_restore_' + nonce, 'volume': 'market_pipeline_restore_' + nonce + '_data',
            'label': 'market-pipeline-backup-' + nonce}


def phase(intent_sha, name, *, daemon_deadline=None, daemon_complete=False, **evidence):
    return write_json(STAGE/'phase.json', {'intent_sha256': intent_sha, 'phase': name,
        'daemon_deadline_epoch_seconds': daemon_deadline, 'daemon_requests_complete': daemon_complete, **evidence}, replace=True)


def free_bytes(path):
    value = os.statvfs(path); return value.f_bavail * value.f_frsize


def capacity_guard(paths):
    require(all(free_bytes(path) >= RESERVE for path in paths), 'hot3_disk_reserve_breached')


def capacity_required(dump_cap, database_bytes, *, same_filesystem):
    require(type(dump_cap) is int and dump_cap > 0 and type(database_bytes) is int and database_bytes > 0, 'hot3_capacity_invalid')
    restore = 2 * database_bytes + WAL_TEMP
    return (dump_cap + restore + RESERVE,) if same_filesystem else (dump_cap + RESERVE, restore + RESERVE)


def web_dump_budget(native_cap, database_bytes, available, *, same_filesystem):
    require(type(native_cap) is int and native_cap > 0 and type(database_bytes) is int and database_bytes > 0,
            'hot3_capacity_invalid')
    restore = 2*database_bytes + WAL_TEMP
    if same_filesystem:
        affordable = available[0] - restore - RESERVE
    else:
        require(available[1] >= restore+RESERVE, 'hot3_web_capacity_insufficient')
        affordable = available[0] - RESERVE
    result = min(native_cap, 64*1024**3, affordable)
    require(result > 0, 'hot3_web_capacity_insufficient')
    return result


def transport(P, old_plan, digest, deadline):
    require(type(digest) is str and re.fullmatch('[a-f0-9]{64}', digest), 'hot3_transport_digest')
    value = P.document(TRANSPORT, digest)
    require(value.get('schema') == 'security_web_market_hot3_transport/1' and value.get('operation_id') == OP,
            'hot3_transport_scope')
    require(value.get('identity_file') == '/root/.ssh/security-web-market-hot3-20261024-01/id_ed25519'
            and value.get('known_hosts_file') == old_plan['offhost']['known_hosts_file']
            and value.get('known_hosts_sha256') == old_plan['offhost']['known_hosts_sha256']
            and value.get('source_host') == '65.109.220.59' and value.get('destination_host') == '65.109.216.187'
            and type(value.get('port')) is int and value['port'] == 22 and value.get('user') == 'root', 'hot3_transport_scope')
    expiry = datetime.fromisoformat(value['expires_at_utc'].replace('Z','+00:00'))
    require(deadline <= expiry, 'hot3_transport_expiry')
    P.safe_parent(Path(value['identity_file']))
    info = Path(value['identity_file']).lstat()
    require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and info.st_nlink == 1 and stat.S_IMODE(info.st_mode) == 0o600
            and type(value.get('identity_metadata')) is list and len(value['identity_metadata']) == 9
            and all(type(item) is int for item in value['identity_metadata'])
            and list(identity(info)) == value['identity_metadata'], 'hot3_transport_key_metadata')
    return value


class Runtime:
    def __init__(self, intent, intent_sha, modules):
        self.intent, self.intent_sha, self.modules = intent, intent_sha, modules
        self.P, self.B, self.R, self.H, self.C = roles(modules)

    def command(self, arguments, **options):
        remaining = datetime.fromisoformat(self.intent['deadline_utc'].replace('Z', '+00:00')).timestamp()+CLEANUP_SECONDS-time.time()
        require(remaining > 0, 'hot3_cleanup_deadline_expired')
        options['seconds'] = min(options.get('seconds', 10), max(.01, remaining))
        return native(arguments, **options)

    def offhost(self, mode, *, size=None, digest=None, stream=None):
        remote = self.intent['offhost']
        for key in ('identity_file', 'known_hosts_file'):
            self.P.safe_parent(Path(remote[key])); info = Path(remote[key]).lstat()
            require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and info.st_nlink == 1
                    and stat.S_IMODE(info.st_mode) == 0o600 and info.st_size <= 16384, 'hot3_ssh_metadata')
        require(list(identity(Path(remote['identity_file']).lstat())) == remote['identity_metadata'], 'hot3_transport_key_metadata')
        self.P.document(TRANSPORT, self.intent['transport_receipt_sha256'])
        require(datetime.fromisoformat(self.intent['deadline_utc'].replace('Z','+00:00'))
                <= datetime.fromisoformat(remote['expires_at_utc'].replace('Z','+00:00')), 'hot3_transport_expiry')
        require(self.P.file_digest(remote['known_hosts_file'], maximum=16384, private=True)[1] == remote['known_hosts_sha256'], 'hot3_ssh_host_pin')
        program = read_public(STAGE/'source/web_market_hot3_offhost.py', digest=PINS['web_market_hot3_offhost.py']).decode()
        remote_arguments = ['/usr/bin/python3', '-I', '-B', '-c', program, mode, '--operation', OP,
            '--nonce', self.intent['nonce'], '--manifest-sha256', self.intent_sha, '--deadline-utc', self.intent['deadline_utc']]
        if mode != 'capacity': remote_arguments += ['--cipher-size', str(size), '--cipher-sha256', digest]
        arguments = ['/usr/bin/ssh', '-F', '/dev/null', '-i', remote['identity_file'], '-p', '22',
            '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes', '-o', 'UpdateHostKeys=no', '-o', 'ForwardAgent=no',
            '-o', 'ConnectTimeout=10', '-o', 'ControlMaster=no', '-S', 'none',
            '-o', 'UserKnownHostsFile='+remote['known_hosts_file'], 'root@65.109.216.187', shlex.join(remote_arguments)]
        value = json.loads(self.command(arguments, seconds=min(900, max(1, int(self.clock.remaining()))), stdin=stream))
        require(list(identity(Path(remote['identity_file']).lstat())) == remote['identity_metadata'], 'hot3_transport_key_metadata')
        self.P.document(TRANSPORT, self.intent['transport_receipt_sha256'])
        require(self.P.file_digest(remote['known_hosts_file'], maximum=16384, private=True)[1] == remote['known_hosts_sha256'], 'hot3_ssh_host_pin')
        require(type(value) is dict and value.get('nonce') == self.intent['nonce']
                and value.get('manifest_sha256') == self.intent_sha and value.get('operation_id') == OP
                and value.get('deadline_utc') == self.intent['deadline_utc']
                and value.get('status') == {'capacity':'capacity_verified', 'receive':'received', 'verify':'verified'}[mode], 'hot3_offhost_binding')
        return value

    def scope(self, kind, *, stop=False):
        unit = self.intent['scopes'][kind]
        value = self.command(['/usr/bin/systemctl', 'show', unit,
            '--property=LoadState,Description,Transient,ActiveState,ControlGroup,KillMode']).decode()
        fields = dict(line.split('=', 1) for line in value.splitlines())
        expected = 'market-hot3:' + self.intent['nonce'] + ':' + self.intent_sha + ':' + kind
        if fields.get('LoadState') == 'not-found':
            require(not os.path.lexists('/sys/fs/cgroup/system.slice/'+unit), 'hot3_scope_descendants')
            return True
        require(fields.get('Description') == expected and fields.get('Transient') == 'yes'
                and fields.get('KillMode') == 'control-group'
                and fields.get('ControlGroup') in {'', '/system.slice/'+unit}, 'hot3_foreign_scope')
        if stop and fields.get('ActiveState') not in {'inactive', 'failed'}:
            self.command(['/usr/bin/systemctl', 'stop', unit])
        group = Path('/sys/fs/cgroup/system.slice') / unit
        if group.exists():
            events = (group/'cgroup.events').read_text()
            require(len(events) <= 4096, 'hot3_cgroup_events_bound')
            return dict(line.split() for line in events.splitlines()).get('populated') == '0'
        return fields.get('ActiveState') in {'inactive', 'failed'}

    def pg_rows(self):
        source = self.intent['database']
        template = '{"id":{{json .Id}},"image":{{json .Image}}}'
        actual = json.loads(self.command(['/usr/bin/docker', 'inspect', '--format', template, source['container_id']]))
        require(actual == {'id': source['container_id'], 'image': source['image']}, 'hot3_source_container_changed')
        sql = ("SET statement_timeout='5000ms'; SELECT COALESCE(json_agg(json_build_object('pid',pid,"
            "'backend_start',backend_start,'database',datname,'user',usename,'application',application_name))::text,'[]') "
            "FROM pg_catalog.pg_stat_activity WHERE application_name='"+self.intent['pg_application_name']+"' "
            "AND datname='"+source['database']+"' AND usename='"+source['user']+"'")
        raw = self.command(['/usr/bin/docker', 'exec', source['container_id'], 'psql', '-X', '-q', '-v', 'ON_ERROR_STOP=1',
            '-At', '-U', source['user'], '-d', source['database'], '-c', sql])
        rows = json.loads(raw)
        require(type(rows) is list and len(rows) <= 8, 'hot3_pg_inventory')
        for row in rows:
            require(type(row) is dict and set(row) == {'pid', 'backend_start', 'database', 'user', 'application'}
                    and type(row['pid']) is int and row['pid'] > 0
                    and row['database'] == source['database'] and row['user'] == source['user']
                    and row['application'] == self.intent['pg_application_name']
                    and type(row['backend_start']) is str, 'hot3_pg_owner')
            started = datetime.fromisoformat(row['backend_start'].replace('Z', '+00:00'))
            require(started >= datetime.fromisoformat(self.intent['created_at_utc'].replace('Z', '+00:00')), 'hot3_pg_owner')
        return rows

    def stop_pg(self, rows):
        source = self.intent['database']
        for row in rows:
            require(type(row.get('pid')) is int and row['pid'] > 0
                    and row.get('application') == self.intent['pg_application_name']
                    and row.get('database') == source['database'] and row.get('user') == source['user'], 'hot3_pg_owner')
            started = datetime.fromisoformat(row['backend_start'].replace('Z', '+00:00')).isoformat()
            sql = ("SET statement_timeout='5000ms'; SELECT pg_terminate_backend(pid) FROM pg_catalog.pg_stat_activity "
                "WHERE pid="+str(row['pid'])+" AND backend_start='"+started+"'::timestamptz "
                "AND application_name='"+self.intent['pg_application_name']+"' AND datname='"+source['database']+"' AND usename='"+source['user']+"'")
            self.command(['/usr/bin/docker', 'exec', source['container_id'], 'psql', '-X', '-q', '-v', 'ON_ERROR_STOP=1',
                '-At', '-U', source['user'], '-d', source['database'], '-c', sql])

    def restore_resources(self, *, remove=False):
        absent = True
        for binding in self.intent['restores']:
            for kind, key, template in (
                ('container', 'container', '{"id":{{json .Id}},"name":{{json .Name}},"owner":{{json (index .Config.Labels "io.gold-trade.market-backup-run")}}}'),
                ('volume', 'volume', '{"name":{{json .Name}},"owner":{{json (index .Labels "io.gold-trade.market-backup-run")}}}')):
                name = binding[key]
                arguments = (['/usr/bin/docker', 'container', 'ls', '--all', '--no-trunc', '--filter', 'name=^/'+name+'$', '--format', '{{.ID}}']
                             if kind == 'container' else ['/usr/bin/docker', 'volume', 'ls', '--filter', 'name=^'+name+'$', '--format', '{{.Name}}'])
                found = self.command(arguments).decode().splitlines()
                require(len(found) <= 1, 'hot3_restore_inventory')
                if not found: continue
                absent = False
                actual = json.loads(self.command(['/usr/bin/docker', kind, 'inspect', '--format', template, found[0]]))
                require(actual.get('name') == ('/' if kind == 'container' else '')+name
                        and actual.get('owner') == binding['label'], 'hot3_restore_owner')
                if kind == 'container':
                    require(actual.get('id') == found[0] and re.fullmatch('[a-f0-9]{64}', found[0]), 'hot3_restore_owner')
                else: require(found[0] == name, 'hot3_restore_owner')
                if remove:
                    arguments = (['/usr/bin/docker', 'rm', '--force', actual['id']] if kind == 'container'
                                 else ['/usr/bin/docker', 'volume', 'rm', name])
                    self.command(arguments)
        return absent


def launch_scope(intent, intent_sha, kind, marker, own_source):
    unit = intent['scopes'][kind]
    require(not os.path.lexists('/sys/fs/cgroup/system.slice/'+unit), 'hot3_prior_scope')
    description = 'market-hot3:'+intent['nonce']+':'+intent_sha+':'+kind
    bootstrap = ('import hashlib,sys;raw=bytes.fromhex(sys.argv.pop(1));expected=sys.argv.pop(1);'
        'assert hashlib.sha256(raw).hexdigest()==expected;exec(compile(raw,"<reviewed-hot3>","exec"),{"__name__":"__main__"})')
    seconds = math.ceil(datetime.fromisoformat(intent['deadline_utc'].replace('Z', '+00:00')).timestamp()-time.time())
    if kind == 'watchdog': seconds += CLEANUP_SECONDS+10
    require(seconds > 0, 'hot3_deadline_expired')
    arguments = ['/usr/bin/systemd-run', '--scope', '--system', '--quiet', '--no-ask-password', '--expand-environment=no',
        '--unit='+unit, '--description='+description, '--property=KillMode=control-group', '--property=SendSIGKILL=yes',
        '--property=TimeoutStopSec=10s', '--property=RuntimeMaxSec='+str(seconds), '--',
        '/usr/bin/python3', '-I', '-B', '-c', bootstrap, own_source.hex(), sha256(own_source).hexdigest(),
        kind, '--intent-sha256', intent_sha, '--marker-fd', str(marker)]
    return subprocess.Popen(arguments, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        pass_fds=(marker,), start_new_session=True,
        env={'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LC_ALL': 'C.UTF-8'})


def supervisor_failure(rt, marker, watch):
    # Persist before any stop request, which itself may time out or be lost.
    write_json(STAGE/'supervisor-failed.json', {'intent_sha256': rt.intent_sha, 'daemon_outcome_unknown': True,
        'worker_launch_requested': (STAGE/'worker-launch-requested.json').exists()})
    try:
        rt.scope('worker', stop=True)
    except Exception:
        write_json(STAGE/'supervisor-stop-unconfirmed.json', {'intent_sha256': rt.intent_sha, 'stop_rpc_confirmed': False})
    if watch.poll() is not None and not (STAGE/'cleanup.json').exists():
        watchdog(rt, marker)


def watchdog(rt, marker, *, pause=time.sleep):
    intent, digest = rt.intent, rt.intent_sha
    deadline = datetime.fromisoformat(intent['deadline_utc'].replace('Z', '+00:00')).timestamp()
    monotonic_end = time.monotonic() + max(0, deadline+CLEANUP_SECONDS-time.time())
    check_marker(rt.P, rt.H, marker, intent['marker_identity'])
    if not (STAGE/'watchdog-ready.json').exists():
        write_json(STAGE/'watchdog-ready.json', {'intent_sha256': digest, 'marker_retained': True, 'pid': os.getpid()})
    else:
        require(document(STAGE/'watchdog-ready.json').get('intent_sha256') == digest, 'hot3_watchdog_binding')
    cancelled, clean, observed, ownership_refused, creation_unknown = False, False, [], False, False
    last_daemon_deadline = deadline
    errors = 0
    foreign = {'hot3_foreign_scope', 'hot3_restore_owner', 'hot3_restore_inventory', 'hot3_pg_owner',
               'hot3_source_container_changed', 'hot3_marker_changed', 'hot3_phase_binding', 'hot3_worker_binding'}
    def attempt(callback):
        nonlocal cancelled, ownership_refused, errors
        try:
            return callback()
        except Exception as error:
            cancelled = True; errors += 1
            code = str(error)
            if code in foreign: ownership_refused = True
            reason = code if code in foreign or code in {'hot3_command_timeout','hot3_command_failed','hot3_command_output_bound'} else 'hot3_metadata_or_cleanup_unknown'
            write_json(STAGE/'watchdog-errors.json', {'intent_sha256': digest, 'errors': errors,
                'reason_code': reason, 'ownership_refused': ownership_refused}, replace=True)
            return None
    while time.time() < deadline + CLEANUP_SECONDS and time.monotonic() < monotonic_end:
        attempt(lambda: check_marker(rt.P, rt.H, marker, intent['marker_identity']))
        state = attempt(lambda: document(STAGE/'phase.json')) if (STAGE/'phase.json').exists() else {}
        state = state or {}
        if state and state.get('intent_sha256') != digest:
            attempt(lambda: require(False, 'hot3_phase_binding')); state = {}
        stage_deadline = state.get('daemon_deadline_epoch_seconds')
        if type(stage_deadline) is int:
            if stage_deadline > deadline:
                attempt(lambda: require(False, 'hot3_phase_binding')); state = {}
            else: last_daemon_deadline = stage_deadline
        done = attempt(lambda: document(STAGE/'worker-complete.json')) if (STAGE/'worker-complete.json').exists() else None
        if done is not None and done.get('intent_sha256') != digest:
            attempt(lambda: require(False, 'hot3_worker_binding')); done = None
        supervisor_failed = None
        if (STAGE/'supervisor-failed.json').exists():
            supervisor_failed = attempt(lambda: document(STAGE/'supervisor-failed.json'))
            if supervisor_failed is None or supervisor_failed.get('intent_sha256') != digest:
                attempt(lambda: require(False, 'hot3_worker_binding'))
            cancelled = True
        if done is not None and done.get('status') != 'PASS': cancelled = True
        if time.time() >= deadline: cancelled = True
        attempt(lambda: capacity_guard([WORK, Path('/var/lib/docker')]))
        rows = attempt(rt.pg_rows)
        changed = False
        for row in rows or []:
            if row not in observed: observed.append(row); changed = True
        if changed:
            write_json(STAGE/'observed-pg-backends.json', {'intent_sha256': digest, 'backends': observed}, replace=True)
        if done is not None and done.get('status') == 'PASS' and not cancelled:
            if rows == [] and attempt(rt.restore_resources) is True and attempt(lambda: rt.scope('worker')) is True:
                clean = True; break
        if cancelled:
            if state.get('phase') in {'create','independent-restore'} and state.get('daemon_requests_complete') is not True:
                creation_unknown = True
            if not state and (STAGE/'worker-launch-requested.json').exists(): creation_unknown = True
            attempt(lambda: rt.scope('worker', stop=True))
            if rows is not None: attempt(lambda: rt.stop_pg(rows))
            attempt(lambda: rt.restore_resources(remove=True))
            # A killed client is not RPC cancellation. Keep the inherited M
            # and watch for late requests until their absolute guard expires.
            safe_cutoff = last_daemon_deadline
            if (done is not None or supervisor_failed is not None) and state.get('daemon_requests_complete') is True:
                safe_cutoff = 0
            if (supervisor_failed is not None and supervisor_failed.get('worker_launch_requested') is False
                    and not (STAGE/'worker-launch-requested.json').exists()):
                safe_cutoff = 0
            if (not ownership_refused and not creation_unknown and time.time() >= safe_cutoff and attempt(rt.pg_rows) == []
                    and attempt(rt.restore_resources) is True and attempt(lambda: rt.scope('worker')) is True):
                clean = True; break
        pause(.5)
    if creation_unknown:
        write_json(STAGE/'cleanup-unresolved.json', {'intent_sha256': digest,
            'reason_code':'hot3_daemon_creation_outcome_unknown','daemon_request_completion_proven':False,
            'automatic_retry_or_adoption_permitted':False,'manual_recovery_required':True})
        raise Refusal('hot3_daemon_creation_outcome_unknown')
    require(clean, 'hot3_cleanup_deadline_unproven')
    write_json(STAGE/'cleanup.json', {'intent_sha256': digest, 'all_owned_resources_absent': True,
        'worker_succeeded': not cancelled, 'original_marker_retained': True, 'pending_requests_adopted': False})
    return not cancelled


def limits(B, intent, clock, deadline, checkpoint, *, dump_cap=None):
    return B.BackupExecutionLimits(metadata_timeout_seconds=60, dump_timeout_seconds=1800,
        restore_timeout_seconds=1800, dump_max_bytes=intent['dump_max_bytes'] if dump_cap is None else dump_cap, minimum_free_bytes=RESERVE,
        pg_application_name=intent['pg_application_name'], restore_cpus=2, restore_memory_bytes=2*1024**3,
        restore_pids_limit=256, daemon_deadline_epoch_seconds=min(int(clock.deadline.timestamp()), deadline), checkpoint=checkpoint)


def worker(rt, marker):
    P, B, R, H, C = rt.P, rt.B, rt.R, rt.H, rt.C
    intent, digest = rt.intent, rt.intent_sha
    clock = rt.clock = Clock(intent['deadline_utc'])
    def checkpoint():
        clock.check(); check_marker(P, H, marker, intent['marker_identity']); capacity_guard([WORK, Path('/var/lib/docker')])
    phase(digest, 'preflight')
    plan, parking, identities = H.controls(P)
    check_marker(P, H, marker, intent['marker_identity'])
    require(H.public_profile(P, clock, plan, parking) == P.document(MANAGED/'public-source-evidence.json', MANAGED_EVIDENCE_SHA), 'hot3_source_profile_changed')
    P.document(MANAGED/'web-managed-receipt.json', MANAGED_RECEIPT_SHA)
    normal = dict(rendered_env=H.ENV, web_env=MANAGED/'web.release.env', receipt=MANAGED/'web-managed-receipt.json',
        config_release_sha=H.CONFIGURATION['release_sha'], config_release_tree=H.CONFIGURATION['release_tree'],
        source_image_id=H.ANCHOR, source_image_release_sha=H.PROVENANCE['release_sha'],
        source_image_release_tree=H.PROVENANCE['release_tree'], source_image_input_signature=H.PROVENANCE['image_input_signature'],
        source_evidence_sha256=MANAGED_EVIDENCE_SHA)
    common = dict(env_file=normal['web_env'], release_sha=H.CONFIGURATION['release_sha'],
        release_tree=H.CONFIGURATION['release_tree'], image_id=H.ANCHOR,
        image_input_signature=H.PROVENANCE['image_input_signature'], allow_target_identity_mismatch=False,
        allow_private_primary_source=True)
    with B.BoundRoleEnv(H.ENV) as original, B.BoundRoleEnv(normal['web_env']) as managed:
        R.verify_web_metadata(**normal)
        values = B.validate_release_env(common['env_file'], release_sha=common['release_sha'], image_id=common['image_id'],
            allow_target_identity_mismatch=False, allow_private_primary_source=True)
        require(values.get('MARKET_POSTGRES_USER', 'market_data') == intent['database']['user']
                and values.get('MARKET_POSTGRES_DB', 'market_archive') == intent['database']['database'], 'hot3_database_role_changed')
        metadata_deadline = min(int(clock.deadline.timestamp()), int(time.time())+180)
        phase(digest, 'source-metadata', daemon_deadline=metadata_deadline)
        source = B.inspect_source_database(values, require_quiesce=False,
            execution_limits=limits(B, intent, clock, metadata_deadline, checkpoint))
        del values
        require(source['container_id'] == intent['database']['container_id'], 'hot3_database_changed')
        same = WORK.stat().st_dev == Path('/var/lib/docker').stat().st_dev
        available = [free_bytes(WORK)] if same else [free_bytes(WORK), free_bytes(Path('/var/lib/docker'))]
        dump_cap = web_dump_budget(intent['dump_max_bytes'], source['database_size_bytes'], available, same_filesystem=same)
        needed = capacity_required(dump_cap, source['database_size_bytes'], same_filesystem=same)
        require(all(free >= required for free, required in zip(available, needed)), 'hot3_web_capacity_insufficient')
        backup_root = WORK/'archive'; backup_root.mkdir(mode=0o700)
        receipt = backup_root/'market-pipeline-backup-receipt.json'
        create_deadline = min(int(clock.deadline.timestamp()), int(time.time())+3600)
        phase(digest, 'create', daemon_deadline=create_deadline, source_database_bytes=source['database_size_bytes'],
              free_bytes=available, required_bytes=list(needed), effective_dump_max_bytes=dump_cap)
        created = B.create_backup(backup_dir=backup_root, receipt=receipt, allow_running_writers=True,
            execution_limits=limits(B, intent, clock, create_deadline, checkpoint, dump_cap=dump_cap), restore_resource_binding=intent['restores'][0], **common)
        require(created['status'] == 'PASS' and created['backup_mode'] == 'hot' and created['restore_smoke']['cleanup_status'] == 'PASS', 'hot3_create_not_verified')
        require(rt.restore_resources() and not rt.pg_rows(), 'hot3_create_cleanup_unproven')
        receipt_sha, journal_sha = B.file_digest(receipt), B.file_digest(B._journal_path(backup_root))
        verify_deadline = min(int(clock.deadline.timestamp()), int(time.time())+1800)
        phase(digest, 'independent-restore', daemon_deadline=verify_deadline, create_receipt_sha256=receipt_sha, journal_sha256=journal_sha)
        verified = B.verify_hot_receipt(receipt=receipt, receipt_sha256=receipt_sha, journal_sha256=journal_sha,
            maximum_age_seconds=7200, execution_limits=limits(B, intent, clock, verify_deadline, checkpoint, dump_cap=dump_cap),
            restore_resource_binding=intent['restores'][1], **common)
        require(verified['fresh_restore_verified'] is True and verified['eligible_for_new_deployment'] is False
                and rt.restore_resources() and not rt.pg_rows(), 'hot3_independent_cleanup_unproven')
        verify_sha = write_json(STAGE/'independent-restore.json', {'intent_sha256': digest, **verified})
        phase(digest, 'encrypt', daemon_complete=True, independent_restore_sha256=verify_sha)
        checkpoint()
        artifact = Path(created['backup']['path'])
        require(free_bytes(WORK) >= created['backup']['size_bytes']+CIPHER_ALLOWANCE+RESERVE, 'hot3_cipher_capacity_insufficient')
        cipher_root = WORK/'encrypted'; cipher_root.mkdir(mode=0o700)
        cipher, encryption_receipt = cipher_root/'market-pipeline-backup.enc', cipher_root/'encryption.json'
        key = Path(plan['backup_key_file']); P.safe_parent(key); key_before = identity(key.lstat())
        require(stat.S_ISREG(key_before[2]) and key_before[3] == 0 and key_before[5] == 1 and stat.S_IMODE(key_before[2]) == 0o600, 'hot3_key_metadata')
        resource.setrlimit(resource.RLIMIT_FSIZE, (created['backup']['size_bytes']+CIPHER_ALLOWANCE,)*2)
        with B._pinned_backup_artifact(artifact, created['backup']):
            encrypted = C.encrypt(source=artifact, destination=cipher, key_file=key, receipt=encryption_receipt)
            C.verify(artifact=cipher, key_file=key, receipt=encryption_receipt,
                expected_plaintext_sha256=created['backup']['sha256'], expected_plaintext_size_bytes=created['backup']['size_bytes'])
        require(identity(key.lstat()) == key_before and encrypted['plaintext_sha256'] == created['backup']['sha256']
                and encrypted['plaintext_size_bytes'] == created['backup']['size_bytes'], 'hot3_encryption_reconciliation')
        checkpoint(); require(encrypted['ciphertext_size_bytes'] <= intent['max_cipher_bytes'], 'hot3_native_cipher_does_not_fit')
        current_capacity = rt.offhost('capacity')
        require(current_capacity['max_cipher_bytes'] >= encrypted['ciphertext_size_bytes'], 'hot3_native_capacity_changed')
        phase(digest, 'offhost', daemon_complete=True, ciphertext_size_bytes=encrypted['ciphertext_size_bytes'], ciphertext_sha256=encrypted['ciphertext_sha256'])
        with B._pinned_backup_artifact(cipher, {'size_bytes': encrypted['ciphertext_size_bytes'], 'sha256': encrypted['ciphertext_sha256']}) as descriptor:
            with os.fdopen(os.dup(descriptor), 'rb') as stream:
                stream.seek(0); received = rt.offhost('receive', size=encrypted['ciphertext_size_bytes'], digest=encrypted['ciphertext_sha256'], stream=stream)
            remote = rt.offhost('verify', size=encrypted['ciphertext_size_bytes'], digest=encrypted['ciphertext_sha256'])
        for value in (received, remote):
            require(value.get('fsync_completed') is True and value.get('plaintext_materialized_offhost') is False
                    and value.get('ciphertext_size_bytes') == encrypted['ciphertext_size_bytes']
                    and value.get('ciphertext_sha256') == encrypted['ciphertext_sha256'], 'hot3_offhost_durability_unproven')
        R.verify_web_metadata(**normal); original.check(); managed.check()
        require(H.public_profile(P, clock, plan, parking) == P.document(MANAGED/'public-source-evidence.json', MANAGED_EVIDENCE_SHA), 'hot3_source_profile_changed')
        H.boundaries(P, clock, plan, identities, marker, tuple(intent['marker_identity']))
        checkpoint()
        result = {'intent_sha256': digest, 'phase': 'hot_backup_offhost_verified_not_accepted',
            'create_receipt_sha256': receipt_sha, 'create_journal_sha256': journal_sha,
            'independent_restore_sha256': verify_sha, 'encryption_receipt_sha256': B.file_digest(encryption_receipt),
            'ciphertext_sha256': encrypted['ciphertext_sha256'], 'ciphertext_size_bytes': encrypted['ciphertext_size_bytes'],
            'offhost': remote, 'managed_receipt_sha256': MANAGED_RECEIPT_SHA, 'public_source_evidence_sha256': MANAGED_EVIDENCE_SHA,
            'configuration_source': H.CONFIGURATION, 'source_image': H.PROVENANCE,
            'security_accepted': False, 'deployment_eligible': False, 'production_services_changed': False,
            'plaintext_materialized_offhost': False, 'secret_or_session_diagnostics_read': False}
        write_json(STAGE/'result.json', result)
        phase(digest, 'worker-verified', daemon_complete=True)
        return result


def execute(args, sources, own_source):
    clock = Clock(args.deadline_utc)
    require(os.geteuid() == 0 and re.fullmatch('[a-f0-9]{32}', args.nonce)
            and args.nonce[:16] != args.nonce[16:], 'hot3_owner_invalid')
    modules = load_modules(sources); P, B, R, H, C = roles(modules)
    require(not os.path.lexists(STAGE) and not os.path.lexists(WORK), 'hot3_partial_operation_exists')
    P.safe_parent(STAGE); P.safe_parent(WORK)
    STAGE.mkdir(mode=0o700); (STAGE/'source').mkdir(mode=0o700)
    for name, raw in {**sources, 'producer.py': own_source}.items():
        fd = os.open(STAGE/'source'/name, os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'wb') as output: output.write(raw); output.flush(); os.fsync(output.fileno())
    plan, parking, controls = H.controls(P)
    require(Path('/etc/machine-id').read_text().strip() == plan['machine_id'], 'hot3_wrong_host')
    marker = os.open(H.MARKER, os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC)
    watch = worker_process = rt = None
    try:
        until = time.monotonic()+5
        while True:
            try: fcntl.flock(marker, fcntl.LOCK_EX|fcntl.LOCK_NB); break
            except BlockingIOError:
                clock.check(); require(time.monotonic() < until, 'hot3_marker_busy'); time.sleep(.1)
        check_marker(P, H, marker)
        H.boundaries(P, clock, plan, controls, marker, identity(os.fstat(marker)))
        require(H.public_profile(P, clock, plan, parking) == P.document(MANAGED/'public-source-evidence.json', MANAGED_EVIDENCE_SHA), 'hot3_source_profile_changed')
        P.document(MANAGED/'web-managed-receipt.json', MANAGED_RECEIPT_SHA)
        database = [row for row in plan['containers'] if row['project'] == 'market-private-pipeline-primary' and row['name'].endswith('-market-database-1')]
        require(len(database) == 1, 'hot3_database_scope')
        probes = [row for row in plan['datastore_probes'] if row['name'] == database[0]['name'] and row['kind'] == 'postgres']
        require(len(probes) == 1 and all(re.fullmatch('[a-z_][a-z0-9_]{0,62}', probes[0][key]) for key in ('user', 'database')), 'hot3_database_probe_scope')
        intent = {'schema': 'security_web_market_hot3_intent/1', 'operation_id': OP, 'nonce': args.nonce,
            'created_at_utc': datetime.now(timezone.utc).isoformat().replace('+00:00','Z'), 'deadline_utc': args.deadline_utc,
            'marker_identity': list(identity(os.fstat(marker))),
            'offhost': transport(P, plan, args.transport_sha256, clock.deadline), 'transport_receipt_sha256': args.transport_sha256,
            'database': {'container_id': database[0]['id'], 'image': database[0]['image'], 'user': probes[0]['user'], 'database': probes[0]['database']},
            'pg_application_name': 'hot3-dump-'+args.nonce, 'restores': [resource_binding(args.nonce[:16]), resource_binding(args.nonce[16:])],
            'scopes': {kind: 'security-market-hot3-'+args.nonce+'-'+kind+'.scope' for kind in ('worker','watchdog')},
            'managed_receipt_sha256': MANAGED_RECEIPT_SHA, 'public_source_evidence_sha256': MANAGED_EVIDENCE_SHA,
            'recipe_sha256': {**PINS, 'producer.py': sha256(own_source).hexdigest()}}
        rt = Runtime(intent, MANAGED_RECEIPT_SHA, modules); rt.clock = clock
        capacity = rt.offhost('capacity')
        require(type(capacity.get('max_cipher_bytes')) is int and capacity['max_cipher_bytes'] > CIPHER_ALLOWANCE, 'hot3_native_capacity_insufficient')
        intent.update(max_cipher_bytes=capacity['max_cipher_bytes'], dump_max_bytes=min(64*1024**3,capacity['max_cipher_bytes']-CIPHER_ALLOWANCE))
        digest = write_json(STAGE/'intent.json', intent)
        rt.intent_sha = digest
        require(rt.offhost('capacity')['max_cipher_bytes'] >= intent['max_cipher_bytes'], 'hot3_native_capacity_changed')
        WORK.mkdir(mode=0o700)
        watch = launch_scope(intent, digest, 'watchdog', marker, own_source)
        ready_until = time.monotonic()+15
        while not (STAGE/'watchdog-ready.json').exists():
            require(watch.poll() is None and time.monotonic() < ready_until, 'hot3_watchdog_not_armed'); time.sleep(.1)
        require(document(STAGE/'watchdog-ready.json').get('intent_sha256') == digest, 'hot3_watchdog_binding')
        write_json(STAGE/'worker-launch-requested.json', {'intent_sha256': digest, 'worker_launch_requested': True})
        worker_process = launch_scope(intent, digest, 'worker', marker, own_source)
        while worker_process.poll() is None:
            clock.check()
            if watch.poll() is not None:
                require(watch.returncode == 0 and document(STAGE/'cleanup.json').get('worker_succeeded') is True,
                        'hot3_watchdog_died')
            time.sleep(.25)
        require(worker_process.returncode == 0, 'hot3_worker_failed')
        cleanup_until = time.monotonic()+CLEANUP_SECONDS
        while watch.poll() is None:
            require(time.monotonic() < cleanup_until, 'hot3_cleanup_wait_expired'); time.sleep(.25)
        require(watch.returncode == 0 and document(STAGE/'cleanup.json') == {
            'intent_sha256': digest, 'all_owned_resources_absent': True, 'worker_succeeded': True,
            'original_marker_retained': True, 'pending_requests_adopted': False}, 'hot3_cleanup_not_verified')
        result = document(STAGE/'result.json')
        require(result.get('intent_sha256') == digest and result.get('security_accepted') is False, 'hot3_result_binding')
        write_json(STAGE/'complete.json', {'intent_sha256': digest, 'result_sha256': sha256(read_public(STAGE/'result.json')).hexdigest(),
            'cleanup_sha256': sha256(read_public(STAGE/'cleanup.json')).hexdigest(), 'phase': 'hot_backup_offhost_verified_not_accepted',
            'security_accepted': False, 'deployment_eligible': False, 'production_services_changed': False})
        return {'status': 'PASS', 'hot_backup_verified': True, 'offhost_durability_verified': True,
                'security_accepted': False, 'deployment_eligible': False, 'production_services_changed': False}
    except BaseException:
        if watch is not None and rt is not None:
            # An intact supervisor covers an unexpectedly killed watchdog.
            # A surviving watchdog covers supervisor/client/worker death.
            supervisor_failure(rt, marker, watch)
        raise
    finally:
        # Do not unlock a shared file description: the independent watchdog
        # retains M until all bounded daemon work and cleanup are resolved.
        os.close(marker)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('run', 'worker', 'watchdog'))
    parser.add_argument('--deadline-utc'); parser.add_argument('--nonce')
    parser.add_argument('--transport-sha256')
    parser.add_argument('--intent-sha256'); parser.add_argument('--marker-fd', type=int)
    parser.add_argument('--own-source'); parser.add_argument('--own-sha256')
    for name in PINS: parser.add_argument('--'+name.removesuffix('.py').replace('_','-'))
    args = parser.parse_args(argv)
    os.umask(0o077)
    try:
        if args.mode == 'run':
            require(type(args.own_source) is str and sha256(args.own_source.encode()).hexdigest() == args.own_sha256, 'hot3_own_source_changed')
            process_clock = Clock(args.deadline_utc)
            signal.signal(signal.SIGALRM, lambda *_: (_ for _ in ()).throw(Refusal('hot3_process_deadline_expired')))
            signal.alarm(math.ceil(process_clock.remaining())+CLEANUP_SECONDS+15)
            sources = {name: getattr(args,name.removesuffix('.py')).encode() for name in PINS}
            result = execute(args, sources, args.own_source.encode())
            print(json.dumps(result, sort_keys=True)); return 0
        require(type(args.intent_sha256) is str and re.fullmatch('[a-f0-9]{64}', args.intent_sha256)
                and type(args.marker_fd) is int and args.marker_fd >= 3, 'hot3_internal_binding')
        intent = document(STAGE/'intent.json', args.intent_sha256)
        modules = modules_from_stage(); rt = Runtime(intent, args.intent_sha256, modules)
        internal_deadline = intent['deadline_utc']
        if args.mode == 'watchdog':
            internal_deadline = datetime.fromtimestamp(datetime.fromisoformat(internal_deadline.replace('Z','+00:00')).timestamp()+CLEANUP_SECONDS,
                timezone.utc).isoformat().replace('+00:00','Z')
        rt.clock = Clock(internal_deadline, maximum=7200+CLEANUP_SECONDS)
        check_marker(rt.P, rt.H, args.marker_fd, intent['marker_identity'])
        if args.mode == 'watchdog': return 0 if watchdog(rt, args.marker_fd) else 1
        try:
            worker(rt, args.marker_fd)
            write_json(STAGE/'worker-complete.json', {'intent_sha256': args.intent_sha256, 'status': 'PASS'})
            return 0
        except BaseException:
            write_json(STAGE/'worker-complete.json', {'intent_sha256': args.intent_sha256, 'status': 'FAIL', 'daemon_outcome_unknown': True})
            return 1
    except BaseException:
        if args.mode == 'run':
            print('{"status":"FAIL","reason_code":"hot3_refused_pending_evidence_retained","security_accepted":false,"deployment_eligible":false,"production_services_changed":false}')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
