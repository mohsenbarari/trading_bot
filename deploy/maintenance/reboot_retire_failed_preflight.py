#!/usr/bin/env python3
"""Retire ONE failed task02 before native reservation; never reboot or rearm it.

Default is read-only. Apply archives exact old artifacts with their nonce/hash;
only a subsequent freshly reviewed manifest/nonce may start another attempt.
The old canonical guard, not this security worktree's new guard, proves baseline.
"""
from contextlib import ExitStack
from hashlib import sha256
import argparse
import ctypes
import json
import os
from pathlib import Path
import re
import resource
import shlex
import signal
import stat
import subprocess
import sys
import tempfile
from types import ModuleType

OPERATION = 'security-kernel-reboot-20261023-02'
CANONICAL = Path('/root/trading-bot/trading_bot')
CONTROL = Path('/root/secure-envs/trading-bot/release-control')
MANIFEST = CONTROL / (OPERATION + '.manifest.json')
WEB = Path('/root/.local/security-kernel-reboot-20261023-task02')
MAXIMUM = 8 * 1024 * 1024


def require(ok, code):
    if not ok: raise RuntimeError(code)


def identity(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid,
            info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def parents(path):
    require(path.is_absolute() and path.resolve(strict=False) == path, 'redirected_path')
    for parent in path.parents:
        value = parent.lstat()
        require(stat.S_ISDIR(value.st_mode) and value.st_uid == os.geteuid() and not value.st_mode & 0o022, 'unsafe_parent')


def read(path, maximum=MAXIMUM, private=True):
    parents(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        require(stat.S_ISREG(before.st_mode) and before.st_uid == os.geteuid() and before.st_nlink == 1
                and not before.st_mode & (0o077 if private else 0o022) and before.st_size <= maximum, 'unsafe_file')
        data = bytearray()
        while chunk := os.read(fd, min(65536, maximum + 1 - len(data))):
            data.extend(chunk); require(len(data) <= maximum, 'file_bound')
        require(identity(before) == identity(os.fstat(fd)) == identity(path.lstat()), 'file_changed')
        return bytes(data)
    finally: os.close(fd)


def fsync_parent(path):
    fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try: os.fsync(fd)
    finally: os.close(fd)


def noreplace(source, destination):
    libc = ctypes.CDLL(None, use_errno=True)
    function = libc.renameat2
    function.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    function.restype = ctypes.c_int
    if function(-100, os.fsencode(source), -100, os.fsencode(destination), 1):
        raise OSError(ctypes.get_errno(), 'exclusive_archive_refused')
    fsync_parent(source); fsync_parent(destination)


def publish(path, value):
    parents(path)
    fd, temporary = tempfile.mkstemp(prefix='.failed-preflight-receipt-', dir=path.parent)
    try:
        data = (json.dumps(value, sort_keys=True) + '\n').encode()
        while data:
            count = os.write(fd, data); require(count > 0, 'short_write'); data = data[count:]
        os.fsync(fd)
        noreplace(Path(temporary), path)
    finally:
        os.close(fd)
        Path(temporary).unlink(missing_ok=True)


def prepare_archive(archive, value):
    parents(archive)
    temporary = Path(tempfile.mkdtemp(prefix='.failed-preflight-archive-', dir=archive.parent))
    try:
        publish(temporary / 'retirement.json', value)
        noreplace(temporary, archive)
    finally:
        if temporary.exists():
            # Only this fresh, still unpublished directory and its own receipt.
            (temporary / 'retirement.json').unlink(missing_ok=True)
            temporary.rmdir()


def archive_file(source, destination, digest):
    require(os.path.lexists(source) != os.path.lexists(destination), 'archive_source_or_destination_ambiguous')
    current = source if os.path.lexists(source) else destination
    require(sha256(read(current)).hexdigest() == digest, 'archive_digest_changed')
    if current == source: noreplace(source, destination)
    require(sha256(read(destination)).hexdigest() == digest and not os.path.lexists(source), 'archive_not_verified')


def run(argv, maximum=65536):
    def limits():
        resource.setrlimit(resource.RLIMIT_FSIZE, (maximum, maximum))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    with tempfile.TemporaryFile() as output:
        process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.DEVNULL,
                    start_new_session=True, preexec_fn=limits,
                    env={'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LC_ALL': 'C'})
        try:
            require(process.wait(timeout=60) == 0 and output.tell() <= maximum, 'metadata_command_failed')
            output.seek(0)
            return output.read(maximum).decode().strip()
        finally:
            try: os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError: pass
            process.wait(timeout=10)


def expected(options):
    for name in ('manifest_sha256', 'intent_sha256', 'log_sha256'):
        require(re.fullmatch('[0-9a-f]{64}', getattr(options, name)), 'invalid_digest')
    require(re.fullmatch('[0-9a-f-]{36}', options.nonce) and re.fullmatch('[0-9a-f]{40}', options.source_revision), 'invalid_identity')
    return {'schema': 'security_reboot_failed_preflight_retirement/1', 'operation_id': OPERATION,
            'nonce': options.nonce, 'manifest_sha256': options.manifest_sha256,
            'source_revision': options.source_revision, 'intent_sha256': options.intent_sha256,
            'log_sha256': options.log_sha256, 'reason': 'ansible_pty_denied_before_native_reservation'}


def remote(options):
    value = expected(options)
    unit = 'security-kernel-reboot-task02-' + options.nonce + '.service'
    properties = dict(line.split('=', 1) for line in run(['/usr/bin/systemctl', 'show', unit,
                      '--property=ActiveState,MainPID,ExecMainStatus']).splitlines())
    require(properties == {'ActiveState': 'failed', 'MainPID': '0', 'ExecMainStatus': '1'}, 'web_controller_not_failed_and_dead')
    for name in ('native-result.json', 'controller-result.json', 'credential-cleanup.log'):
        require(not os.path.lexists(WEB / name), 'web_result_or_cleanup_exists')
    archive = WEB / ('failed-preflight-' + options.nonce)
    receipt = archive / 'retirement.json'
    pairs = [('controller-intent.json', options.intent_sha256), ('ansible.log', options.log_sha256),
             ('reboot_manifest.json', options.manifest_sha256)]
    if os.path.lexists(archive):
        info = archive.lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == os.geteuid() and stat.S_IMODE(info.st_mode) == 0o700, 'unsafe_archive')
        require(json.loads(read(receipt)) == value, 'foreign_archive_intent')
    for name, digest in pairs:
        source, target = WEB / name, archive / name
        require(os.path.lexists(source) != os.path.lexists(target), 'web_archive_ambiguous')
        require(sha256(read(source if os.path.lexists(source) else target)).hexdigest() == digest, 'web_artifact_changed')
    intent_path = WEB / 'controller-intent.json'
    intent = json.loads(read(intent_path if intent_path.exists() else archive / intent_path.name))
    require(intent == {'schema': 'security_kernel_reboot_controller_intent/1', 'operation_id': OPERATION,
            'manifest_sha256': options.manifest_sha256, 'nonce': options.nonce,
            'source_revision': options.source_revision, 'status': 'single_attempt_started'}, 'web_intent_identity_changed')
    if options.apply:
        if not os.path.lexists(archive):
            prepare_archive(archive, value)
        # Keep the single-attempt fence until both supporting artifacts are safe.
        for name, digest in pairs[1:] + pairs[:1]: archive_file(WEB / name, archive / name, digest)
    return {**value, 'status': 'archived' if options.apply else 'checked', 'native_mutation_permitted': False}


def web(options, apply):
    code = read(Path(__file__).resolve(), maximum=65536, private=False).decode()
    args = ['/usr/bin/python3', '-I', '-B', '-c', code, '--remote', '--manifest-sha256', options.manifest_sha256,
            '--nonce', options.nonce, '--source-revision', options.source_revision,
            '--intent-sha256', options.intent_sha256, '--log-sha256', options.log_sha256]
    if apply: args.append('--apply')
    result = json.loads(run(['/usr/bin/ssh', '-p', '37067', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
                '-o', 'ForwardAgent=no', '-o', 'UpdateHostKeys=no', '-o', 'ConnectTimeout=10',
                '-o', 'ControlMaster=no', '-S', 'none', 'root@65.109.220.59', shlex.join(args)]))
    require(result == {**expected(options), 'status': 'archived' if apply else 'checked',
                       'native_mutation_permitted': False}, 'remote_proof_changed')
    return result


def canonical_guard(raw, options):
    plan = json.loads(raw)
    path = CANONICAL / 'deploy/maintenance/reboot_guard.py'
    entry = [v for v in plan['files'] if v['path'] == str(path)]
    require(len(entry) == 1 and plan['schema'] == 'security_kernel_reboot/1' and plan['operation_id'] == OPERATION
            and plan['nonce'] == options.nonce and plan['source_revision'] == options.source_revision, 'original_manifest_identity_changed')
    code = read(path, maximum=65536, private=False)
    require(sha256(code).hexdigest() == entry[0]['sha256'] and len(code) == entry[0]['size'], 'canonical_guard_changed')
    guard = ModuleType('_original_canonical_reboot_guard'); guard.__file__ = str(path)
    exec(compile(code, str(path), 'exec'), guard.__dict__)
    return guard, plan


def native_check(guard, plan):
    guard.invariants(plan, before=True)
    require(not any(os.path.lexists(p) for p in (guard.B.RESERVATION, guard.B.PENDING, guard.STATE,
            guard.TOKEN, guard.RESULT, guard.GOLDEN_INHIBITOR)), 'native_operation_started')
    guard.B.runtime(plan, 'complete')


def retire(options):
    value = expected(options)
    archived = CONTROL / (OPERATION + '.failed-preflight-' + options.nonce + '.manifest.json')
    receipt = CONTROL / (OPERATION + '.failed-preflight-' + options.nonce + '.json')
    require(os.path.lexists(MANIFEST) != os.path.lexists(archived), 'native_manifest_archive_ambiguous')
    raw = read(MANIFEST if MANIFEST.exists() else archived)
    require(sha256(raw).hexdigest() == options.manifest_sha256, 'original_manifest_changed')
    guard, plan = canonical_guard(raw, options)
    with ExitStack() as stack:
        guard.lock_current(stack)
        native_check(guard, plan)
        web(options, False)
        if options.apply:
            if os.path.lexists(receipt): require(json.loads(read(receipt)) == value, 'foreign_native_retirement')
            else: publish(receipt, value)
            web(options, True)
            native_check(guard, plan)
            archive_file(MANIFEST, archived, options.manifest_sha256)
            web(options, False)
        return {**value, 'status': 'retired_pre_mutation' if options.apply else 'checked',
                'old_manifest_archived': bool(options.apply), 'reboot_requested': False, 'runtime_changed': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('manifest-sha256', 'nonce', 'source-revision', 'intent-sha256', 'log-sha256'):
        parser.add_argument('--' + name, required=True)
    parser.add_argument('--remote', action='store_true')
    parser.add_argument('--apply', action='store_true')
    options = parser.parse_args()
    try:
        require(os.geteuid() == 0 and Path('/proc/1/exe').readlink().name == 'systemd', 'native_root_required')
        print(json.dumps(remote(options) if options.remote else retire(options), sort_keys=True))
        return 0
    except Exception:
        print('{"status":"refused","code":"retirement_refused"}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
