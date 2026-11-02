#!/usr/bin/env python3
"""One separately admitted Web kernel reboot; check is read-only by default."""
from contextlib import ExitStack
from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import resource
import shlex
import signal
import stat
import subprocess
import tempfile
from types import ModuleType

OP = 'security-web-kernel-reboot-20261024-01'
OWNER = 0
ROOT = Path('/root/.local') / OP
SOURCE = ROOT / 'source'
WINDOW = ROOT / 'window.json'
CONTROL = Path('/root/secure-envs/trading-bot/release-control')
RESERVATION = CONTROL / 'private-primary-controller.lock'
SOURCE_LOCK = CONTROL / (OP + '.source.lock')
STATE = ROOT / 'request.json'
RESULT = ROOT / 'postboot-result.json'
COMPLETE = ROOT / 'complete.json'
OLD_KERNEL, TARGET = '7.0.0-27-generic', '7.0.0-38-generic'
BOOT_ROOT = Path('/boot')
GRUB_DEFAULT = Path('/etc/default/grub')
GRUB_CFG = BOOT_ROOT/'grub/grub.cfg'
GRUB_ENV = BOOT_ROOT/'grub/grubenv'
BOOT_SHA = '08963b564e4925ab415359b20e75a73d8302c543706e5d8477c1602ebd506c50'
CONFIRMATION = 'ONE-WEB-KERNEL-REBOOT-20261024-01'
SOURCE_NAMES = {'web_kernel_reboot_guard.py', 'web_kernel_reboot.yml', 'web_boot_guard.py'}
HTTP_PROBE = ('import sys,urllib.request; '
              'r=urllib.request.urlopen(sys.argv[1],timeout=5); '
              'print(r.status); r.close()')


class Refusal(RuntimeError):
    pass


TRUSTED_ERRORS = {Refusal}


def safe_reason(error):
    value = str(error)
    return value if type(error) in TRUSTED_ERRORS and re.fullmatch('[a-z][a-z0-9_]{0,79}', value) else 'web_kernel_reboot_refused'


def require(ok, code):
    if not ok:
        raise Refusal(code)


def identity(info):
    return [info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid,
            info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns]


def read(path, maximum=2097152, private=False):
    path = Path(path)
    require(path.is_absolute() and path.resolve(strict=True) == path, 'kernel_control_path')
    for parent in path.parents:
        info = parent.lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid in {0, OWNER} and not info.st_mode & 0o022,
                'kernel_control_parent')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        before = os.fstat(fd)
        require(stat.S_ISREG(before.st_mode) and before.st_uid == OWNER and before.st_nlink == 1
                and 0 < before.st_size <= maximum and not before.st_mode & (0o077 if private else 0o022),
                'kernel_control_metadata')
        raw = os.read(fd, before.st_size + 1)
        require(len(raw) == before.st_size and identity(before) == identity(os.fstat(fd)) == identity(path.lstat()),
                'kernel_control_changed')
        return raw
    finally:
        os.close(fd)


def parse_document(raw):
    def unique(pairs):
        value = {}
        for key, item in pairs:
            require(key not in value, 'kernel_duplicate_key')
            value[key] = item
        return value
    try:
        value = json.loads(raw, object_pairs_hook=unique,
                           parse_constant=lambda _: require(False, 'kernel_json_constant'))
    except (ValueError, UnicodeError, RecursionError):
        raise Refusal('kernel_control_json') from None
    require(type(value) is dict, 'kernel_control_shape')
    return value


def document(path, digest=None):
    raw = read(path, private=True)
    require(digest is None or sha256(raw).hexdigest() == digest, 'kernel_control_digest')
    return parse_document(raw)


def seal(path, digest, maximum, private=False):
    before = identity(Path(path).lstat())
    raw = read(path, maximum, private)
    require(identity(Path(path).lstat()) == before and sha256(raw).hexdigest() == digest, 'kernel_control_changed')
    return raw, {'identity': before, 'sha256': digest}


def timestamp(value):
    require(type(value) is str and re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z', value),
            'kernel_window_time')
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        raise Refusal('kernel_window_time') from None


def boot_id():
    # Fixed public procfs identity; unlike ordinary controls its st_size is zero.
    with Path('/proc/sys/kernel/random/boot_id').open('rb') as stream:
        raw = stream.read(129)
    require(re.fullmatch(rb'[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}\n?', raw), 'kernel_boot_id')
    return raw.decode('ascii').strip()


def sync_parent(path):
    fd = os.open(Path(path).parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def write_all(fd, raw):
    while raw:
        count = os.write(fd, raw)
        require(count > 0, 'kernel_short_write')
        raw = raw[count:]


def publish(path, value):
    """Exclusive public control only; never overwrite or adopt an old attempt."""
    path = Path(path)
    raw = (json.dumps(value, sort_keys=True) + '\n').encode()
    fd, temporary = tempfile.mkstemp(prefix='.web-kernel-', dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        write_all(fd, raw)
        os.fsync(fd)
        os.link(temporary, path, follow_symlinks=False)
        Path(temporary).unlink()
        sync_parent(path)
    finally:
        os.close(fd)
        Path(temporary).unlink(missing_ok=True)
    return sha256(raw).hexdigest()


class Runtime:
    def __init__(self, digest):
        require(type(digest) is str and re.fullmatch('[a-f0-9]{64}', digest), 'kernel_window_digest')
        self.digest = digest
        window_raw, binding = seal(WINDOW, digest, 2097152, True)
        self.window = parse_document(window_raw)
        self.authority_files = {str(WINDOW): binding}
        self.authority_fds = {}
        value = self.window
        flags = {'reboot_allowed': True, 'kernel_packages_allowed': False, 'engine_packages_allowed': False,
                 'container_recreation_allowed': False, 'writer_dns_changes_allowed': False,
                 'capture_owner_changes_allowed': False, 'automatic_downgrade_allowed': False}
        keys = {'schema', 'operation_id', 'nonce', 'machine_id', 'before_boot_id', 'not_before_utc', 'deadline_utc',
                'target_kernel', 'completed_manifest_sha256', 'completed_journal_sha256', 'completed_cleanup_sha256',
                'approved_sources_sha256', *flags}
        require(set(value) == keys and value['schema'] == 'web_kernel_reboot_window/1'
                and value['operation_id'] == OP and value['target_kernel'] == TARGET
                and type(value['nonce']) is str and re.fullmatch('[a-f0-9]{32}', value['nonce'])
                and type(value['machine_id']) is str and re.fullmatch('[a-f0-9]{32}', value['machine_id'])
                and type(value['before_boot_id']) is str
                and re.fullmatch('[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}', value['before_boot_id'])
                and all(value[k] is v for k, v in flags.items()), 'kernel_window_scope')
        for key in ('completed_manifest_sha256', 'completed_journal_sha256', 'completed_cleanup_sha256'):
            require(type(value[key]) is str and re.fullmatch('[a-f0-9]{64}', value[key]), 'kernel_completion_pin')
        self.before, self.deadline = timestamp(value['not_before_utc']), timestamp(value['deadline_utc'])
        require(0 < (self.deadline - self.before).total_seconds() <= 3600, 'kernel_window_expanded')
        self.remaining()
        pins = value['approved_sources_sha256']
        require(type(pins) is dict and set(pins) == SOURCE_NAMES
                and all(type(v) is str and re.fullmatch('[a-f0-9]{64}', v) for v in pins.values())
                and pins['web_boot_guard.py'] == BOOT_SHA, 'kernel_source_inventory')
        buffers = {}
        for name, pin in pins.items():
            buffers[name], binding = seal(SOURCE / name, pin, 131072)
            self.authority_files[str(SOURCE / name)] = binding
        source = buffers['web_boot_guard.py']
        self.B = ModuleType('_sealed_current08_web_boot_guard')
        exec(compile(source, str(SOURCE / 'web_boot_guard.py'), 'exec'), self.B.__dict__)
        TRUSTED_ERRORS.add(self.B.BootGuardRefusal)
        require(value['nonce'] != self.B.NONCE, 'kernel_nonce_not_fresh')
        # Compose the reviewed read-only probes with this operation's finite
        # command runner; no absence/ownership admission rule is overridden.
        self.B.run = self.command

    def bind_authority(self, stack):
        for path, expected in self.authority_files.items():
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
            stack.callback(os.close, fd)
            require(self.B.equal(identity(os.fstat(fd)), expected['identity'])
                    and self.B.equal(identity(Path(path).lstat()), expected['identity']), 'kernel_authority_changed')
            self.authority_fds[path] = fd
        self.check_authority()

    def check_authority(self):
        self.remaining()
        require(set(self.authority_fds) == set(self.authority_files), 'kernel_authority_unbound')
        for path, expected in self.authority_files.items():
            fd = self.authority_fds[path]
            require(self.B.equal(identity(os.fstat(fd)), expected['identity'])
                    and self.B.equal(identity(Path(path).lstat()), expected['identity']), 'kernel_authority_changed')
            size = expected['identity'][6]
            data = os.pread(fd, size+1, 0)
            require(len(data) == size and sha256(data).hexdigest() == expected['sha256']
                    and self.B.equal(identity(os.fstat(fd)), expected['identity'])
                    and self.B.equal(identity(Path(path).lstat()), expected['identity']), 'kernel_authority_changed')

    def remaining(self, reserve=0):
        now = datetime.now(timezone.utc)
        left = (self.deadline - now).total_seconds()
        require(self.before <= now and left > reserve, 'kernel_window_expired_or_not_started')
        return left

    def command(self, argv):
        maximum = 2097152
        timeout = min(15, self.remaining())
        def limits():
            resource.setrlimit(resource.RLIMIT_FSIZE, (maximum, maximum))
            resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        with tempfile.TemporaryFile() as output:
            child = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.DEVNULL,
                    env={'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LC_ALL': 'C'},
                    start_new_session=True, preexec_fn=limits)
            try:
                code = child.wait(timeout=timeout)
            except BaseException:
                # Do not enter Popen.__exit__'s unbounded final wait when the
                # operating system cannot acknowledge child termination.
                try:
                    os.killpg(child.pid, signal.SIGKILL)
                    child.wait(timeout=5)
                except (ProcessLookupError, subprocess.TimeoutExpired):
                    pass
                raise Refusal('kernel_command_outcome_unknown') from None
            require(code == 0, 'kernel_command_failed')
            output.seek(0)
            raw = output.read(maximum + 1)
        require(len(raw) < maximum, 'kernel_command_bound')
        self.remaining()
        return raw.decode('utf-8').strip()

    def host(self, *, after):
        self.remaining()
        self.B.native()
        require(read('/etc/machine-id', 128).decode().strip() == self.window['machine_id'], 'kernel_machine_changed')
        changed = boot_id() != self.window['before_boot_id']
        require(changed is after, 'kernel_boot_identity_mismatch')
        require(self.command(['/usr/bin/uname', '-r']) == (TARGET if after else OLD_KERNEL), 'kernel_active_version')

    def kernel(self):
        require(self.command(['/usr/bin/dpkg-query', '-W', '-f=${db:Status-Status}', 'linux-image-' + TARGET]) == 'installed',
                'kernel_target_not_installed')
        values = {}
        defaults = read(GRUB_DEFAULT, 65536)
        for line in defaults.decode().splitlines():
            stripped = line.strip()
            if stripped.startswith(('GRUB_DEFAULT=', 'GRUB_SAVEDEFAULT=')):
                key, value = stripped.split('=', 1)
                require(key not in values, 'kernel_grub_ambiguous')
                values[key] = shlex.split(value)
        require(values.get('GRUB_DEFAULT') == ['0'] and 'GRUB_SAVEDEFAULT' not in values, 'kernel_grub_default')
        cfg = read(GRUB_CFG)
        entries = re.findall(r'^\s*(linux|linuxefi|initrd|initrdefi)\s+([^\n]+)', cfg.decode(), re.MULTILINE)
        require(len(entries) >= 2 and entries[0][0] in {'linux', 'linuxefi'}
                and shlex.split(entries[0][1])[0] == '/boot/vmlinuz-' + TARGET
                and entries[1][0] in {'initrd', 'initrdefi'}
                and shlex.split(entries[1][1]) == ['/boot/initrd.img-' + TARGET], 'kernel_grub_first_entry')
        environment = self.command(['/usr/bin/grub-editenv', str(GRUB_ENV), 'list'])
        require(not any(row.startswith('next_entry=') for row in environment.splitlines()), 'kernel_grub_next_entry')
        artifacts = {}
        for kernel in (OLD_KERNEL, TARGET):
            for prefix in ('vmlinuz-', 'initrd.img-'):
                path = BOOT_ROOT / (prefix + kernel)
                require(path.resolve(strict=True) == path, 'kernel_artifact_redirected')
                info = path.lstat()
                require(stat.S_ISREG(info.st_mode) and info.st_uid == OWNER and info.st_nlink == 1
                        and 0 < info.st_size <= 256*1024**2 and not info.st_mode & 0o022, 'kernel_artifact_metadata')
                artifacts[str(path)] = identity(info)
        return {'grub_default_sha256': sha256(defaults).hexdigest(),
                'grub_cfg_sha256': sha256(cfg).hexdigest(), 'artifacts': artifacts}

    def completion(self):
        value = self.window
        return self.B.completed(value['completed_manifest_sha256'], value['completed_journal_sha256'],
                                value['completed_cleanup_sha256'])

    def snapshots(self, plan):
        B = self.B
        paths = [B.WORK/'plan.json', B.CONTROL/(B.OP+'.journal.json'), B.WORK/'window.json',
                 Path('/srv/trading-bot')/(B.OP+'-'+B.NONCE)/'watch-cleanup.json',
                 B.WORK/'source/deploy/maintenance/web_engine_current_controller.py',
                 B.WORK/'source/deploy/maintenance/web_engine_current_profile.py',
                 *[B.WORK/'evidence'/(name+'.json') for name in plan['current_profile']['source_control_sha256']],
                 *[B.SYSTEMD/(unit+'.d')/B.NAME for unit in B.desired()]]
        result = {}
        for path in paths:
            before = identity(path.lstat())
            raw = read(path)
            require(identity(path.lstat()) == before, 'kernel_control_changed')
            result[str(path)] = {'sha256': sha256(raw).hexdigest(), 'identity': before}
        return result

    def probes(self, plan):
        profile = plan['current_profile']
        probes = profile['datastore_probes']
        require(len(probes) == len({p['id'] for p in probes}) == 6, 'kernel_datastore_scope')
        for probe in probes:
            require(probe['kind'] in {'redis', 'postgres'}, 'kernel_datastore_scope')
            argv = ['redis-cli', 'ping'] if probe['kind'] == 'redis' else ['pg_isready', '-t', '5', '-U', probe['user'], '-d', probe['database']]
            output = self.command(self.B.DOCKER + ['exec', probe['id'], *argv])
            require(probe['kind'] != 'redis' or output == 'PONG', 'kernel_datastore_not_ready')
        old = document(self.B.WORK/'evidence/c07_plan.json', profile['source_control_sha256']['c07_plan'])
        require(self.B.equal(plan['health_probes'], old['health_probes']) and plan['health_probes'], 'kernel_api_scope')
        for probe in plan['health_probes']:
            require(self.command(self.B.DOCKER + ['exec', probe['id'], '/usr/local/bin/python', '-I', '-B', '-c',
                                                  HTTP_PROBE, probe['url']]) == '200', 'kernel_api_not_ready')
        for unit in plan['engine_intent']:
            values = self.command(['/usr/bin/systemctl', 'show', unit['name'], '-p', 'UnitFileState', '-p', 'ActiveState'])
            actual = dict(row.split('=', 1) for row in values.splitlines())
            require(actual['UnitFileState'] == unit['enabled'] and (actual['ActiveState'] == 'active') == unit['active'],
                    'kernel_engine_intent')
        return {'datastores_verified': 6, 'api_probes_verified': len(plan['health_probes'])}

    def readiness(self, plan, bus, *, after):
        self.B.current_profile(plan)
        self.B.primary(bus)
        self.B.entries('setup', True, bus)
        observed = plan
        if after:
            observed = {**plan, 'units': deepcopy(plan['units'])}
            for row in observed['units']:
                if row['name'] not in self.B.COLLECTORS:
                    continue
                ephemeral = str(self.B.RUNTIME/(row['name']+'.d')/'market-container-authority.conf')
                removed = [v for v in row['dropins'] if v['path'] == ephemeral and not os.path.lexists(ephemeral)]
                require(all(v['sha256'] == sha256(self.B.BODY.encode()).hexdigest() for v in removed),
                        'kernel_ephemeral_inhibitor_changed')
                row['dropins'] = [v for v in row['dropins'] if v not in removed]
        closure = self.B.runtime(observed, bus)
        return {**closure, **self.probes(plan)}

    def no_foreign_maintenance(self):
        B = self.B
        absent = (B.PENDING, B.CONTROL/(B.OP+'.source.lock'), B.CONTROL/'security-web-engine-20261023-07.source.lock',
                  B.CONTROL.parent/'.production-runtime-source.lock',
                  B.CONTROL.parent/'market-pipeline-cutover/bot-legacy-handoff-4ef8e6dc.json')
        require(not any(os.path.lexists(p) for p in absent), 'kernel_foreign_maintenance')

    def state(self):
        value = document(STATE)
        require(value.get('schema') == 'web_kernel_reboot_request/1' and value.get('operation_id') == OP
                and value.get('nonce') == self.window['nonce'] and value.get('window_sha256') == self.digest
                and value.get('before_boot_id') == self.window['before_boot_id']
                and value.get('phase') == 'reboot_requested_outcome_unknown', 'kernel_request_binding')
        return value

    def own_fences(self, state, stack):
        require(type(state['fences']) is list and [r['path'] for r in state['fences']] == [str(SOURCE_LOCK), str(RESERVATION)],
                'kernel_fence_inventory')
        for row in state['fences']:
            path = Path(row['path'])
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK); stack.callback(os.close, fd)
            before = os.fstat(fd)
            require(self.B.equal(identity(before), row['identity']) and stat.S_ISREG(before.st_mode)
                    and before.st_uid == OWNER and before.st_nlink == 1 and stat.S_IMODE(before.st_mode) == 0o600
                    and 0 < before.st_size <= 8192
                    and self.B.equal(identity(before), identity(path.lstat())), 'kernel_fence_changed')
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            raw = os.pread(fd, before.st_size+1, 0)
            require(len(raw) == before.st_size and sha256(raw).hexdigest() == row['sha256']
                    and self.B.equal(identity(os.fstat(fd)), identity(before))
                    and self.B.equal(identity(before), identity(path.lstat())), 'kernel_fence_changed')
            value = parse_document(raw)
            require(set(value) == {'schema', 'operation_id', 'nonce', 'window_sha256', 'before_boot_id', 'owner_pid'}
                    and value['schema'] == 'web_kernel_reboot_owner/1' and value['operation_id'] == OP
                    and value['nonce'] == self.window['nonce'] and value['window_sha256'] == self.digest
                    and value['before_boot_id'] == self.window['before_boot_id']
                    and type(value['owner_pid']) is int and value['owner_pid'] > 0, 'kernel_owner_binding')
        return state['fences']

    def prepared_after(self, stack, bus):
        self.host(after=True)
        self.no_foreign_maintenance()
        state = self.state(); self.own_fences(state, stack)
        require(self.B.equal(self.authority_files, state['authority_files']), 'kernel_authority_changed')
        self.check_authority()
        plan = document(self.B.WORK/'plan.json', self.window['completed_manifest_sha256'])
        require(self.B.equal(self.snapshots(plan), state['completed_files'])
                and self.B.equal(self.kernel(), state['kernel_selection']), 'kernel_completed_evidence_changed')
        marker = plan['marker']
        require(self.B.equal(identity(self.B.MARKER.lstat()), state['marker_identity'])
                and sha256(read(self.B.MARKER, 8192, True)).hexdigest() == marker['sha256'], 'kernel_marker_changed')
        return state, self.readiness(plan, bus, after=True)


def create_fences(rt, stack):
    require(not any(os.path.lexists(p) for p in (SOURCE_LOCK, RESERVATION, STATE, RESULT, COMPLETE)), 'kernel_attempt_exists')
    rows, owned = [], []
    try:
        for path in (SOURCE_LOCK, RESERVATION):
            raw = (json.dumps({'schema': 'web_kernel_reboot_owner/1', 'operation_id': OP,
                'nonce': rt.window['nonce'], 'window_sha256': rt.digest,
                'before_boot_id': rt.window['before_boot_id'], 'owner_pid': os.getpid()}, sort_keys=True)+'\n').encode()
            fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            stack.callback(os.close, fd)
            owned.append((path, fd, raw))
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            write_all(fd, raw); os.fsync(fd); sync_parent(path)
            rows.append({'path': str(path), 'identity': identity(os.fstat(fd)), 'sha256': sha256(raw).hexdigest()})
        return rows
    except BaseException:
        # Initialization only, before request publication or a reboot RPC.
        for path, fd, expected in reversed(owned):
            if not os.path.lexists(path):
                continue
            info = os.fstat(fd)
            if identity(path.lstat()) == identity(info) and info.st_uid == OWNER and info.st_nlink == 1 \
                    and stat.S_IMODE(info.st_mode) == 0o600 and 0 <= info.st_size <= len(expected) \
                    and os.pread(fd, len(expected)+1, 0) == expected[:info.st_size]:
                path.unlink(); sync_parent(path)
        raise


def wait_shutdown(rt):
    signal.alarm(min(60, max(1, int(rt.remaining()))))
    try:
        signal.pause()
        raise Refusal('kernel_shutdown_unproven')
    finally:
        signal.alarm(0)


def execute(action, digest):
    require(action in {'check', 'request', 'post-check', 'finalize'}, 'kernel_action')
    rt = Runtime(digest)
    with ExitStack() as stack:
        rt.bind_authority(stack)
        stack.enter_context(rt.B.locked_marker())
        bus = stack.enter_context(rt.B.pinned())
        if action in {'check', 'request'}:
            rt.host(after=False)
            require(not any(os.path.lexists(p) for p in (SOURCE_LOCK, RESERVATION, STATE, RESULT, COMPLETE)), 'kernel_attempt_exists')
            plan = rt.completion()
            ready = rt.readiness(plan, bus, after=False)
            selection = rt.kernel()
            if action == 'check':
                return {'status': 'checked', 'reboot_requested': False, 'configuration_changed': False,
                        'target_kernel': TARGET, 'readiness': ready}
            rt.remaining(1500)
            files = rt.snapshots(plan)
            rt.check_authority()
            fences = create_fences(rt, stack)
            # Durable request precedes the one normal reboot RPC. No missing
            # response, timeout or absent process is treated as cancellation.
            try:
                publish(STATE, {'schema': 'web_kernel_reboot_request/1', 'operation_id': OP,
                    'nonce': rt.window['nonce'], 'window_sha256': digest, 'before_boot_id': rt.window['before_boot_id'],
                    'phase': 'reboot_requested_outcome_unknown', 'fences': fences, 'completed_files': files,
                    'kernel_selection': selection, 'marker_identity': identity(rt.B.MARKER.lstat()),
                    'authority_files': rt.authority_files,
                    'requested_at_utc': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')})
            except BaseException:
                if not os.path.lexists(STATE):
                    for row in fences:
                        path = Path(row['path'])
                        require(rt.B.equal(identity(path.lstat()), row['identity'])
                                and sha256(read(path, 8192, True)).hexdigest() == row['sha256'], 'kernel_fence_changed')
                        path.unlink(); sync_parent(path)
                raise
            rt.remaining(600)
            rt.check_authority()
            rt.command(['/usr/bin/systemctl', 'reboot'])
            wait_shutdown(rt)
            raise Refusal('kernel_shutdown_unproven')
        state, ready = rt.prepared_after(stack, bus)
        if action == 'post-check':
            return {'status': 'postboot_checked', 'reboot_requested': False, 'readiness': ready}
        require(not os.path.lexists(RESULT) and not os.path.lexists(COMPLETE), 'kernel_result_exists')
        rt.check_authority()
        result_sha = publish(RESULT, {'schema': 'web_kernel_reboot_postboot/1', 'operation_id': OP,
            'nonce': rt.window['nonce'], 'window_sha256': digest, 'before_boot_id': rt.window['before_boot_id'],
            'after_boot_id': boot_id(), 'active_kernel': TARGET, 'status': 'postboot_verified', 'readiness': ready,
            'application_image_changed': False, 'writer_dns_changes': False, 'capture_owner_changes': False,
            'packages_changed': False, 'security_accepted': False, 'automatic_reboot_retry': False})
        for row in state['fences']:
            rt.check_authority()
            path = Path(row['path'])
            require(rt.B.equal(identity(path.lstat()), row['identity'])
                    and sha256(read(path, 8192, True)).hexdigest() == row['sha256'], 'kernel_fence_changed')
            path.unlink(); sync_parent(path)
        rt.check_authority()
        publish(COMPLETE, {'schema': 'web_kernel_reboot_complete/1', 'operation_id': OP,
            'nonce': rt.window['nonce'], 'window_sha256': digest, 'postboot_result_sha256': result_sha,
            'status': 'completed', 'active_kernel': TARGET, 'owned_temporary_fences_released': True,
            'original_marker_retained': True, 'security_accepted': False})
        return {'status': 'completed', 'active_kernel': TARGET, 'readiness': ready, 'security_accepted': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--action', choices=('check', 'request', 'post-check', 'finalize'), default='check')
    parser.add_argument('--window-sha256', required=True)
    parser.add_argument('--confirmation', default='')
    args = parser.parse_args()
    require(args.action in {'check', 'post-check'} or args.confirmation == CONFIRMATION, 'kernel_explicit_apply')
    print(json.dumps(execute(args.action, args.window_sha256), sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(json.dumps({'status': 'refused', 'code': safe_reason(error), 'automatic_reboot_retry': False}))
        raise SystemExit(1)
