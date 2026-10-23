#!/usr/bin/env python3
"""Durable Web-only single-attempt controller; never read a private key's bytes."""
import argparse
from datetime import datetime, timezone
from hashlib import sha256
import importlib.metadata
import json
import os
from pathlib import Path
import re
import resource
import stat
import subprocess
import sys

ROOT = Path('/root/.local/security-kernel-reboot-20261023-task02')
KEY = '/root/.ssh/security-controller-20261023-host-patch-02/id_ed25519'
PIN = '/root/.ssh/security-controller-20261023-host-patch-02/known_hosts'
OPERATION = 'security-kernel-reboot-20261023-02'
NATIVE_MANIFEST = '/root/secure-envs/trading-bot/release-control/' + OPERATION + '.manifest.json'
CUTOFF = datetime(2026, 10, 24, 0, 30, tzinfo=timezone.utc)
DEADLINE = datetime(2026, 10, 24, 1, 30, tzinfo=timezone.utc)
ASSETS = {'reboot_controller.py', 'reboot_external.yml', 'reboot_inventory.yml', 'reboot_ansible.cfg',
          'reboot_requirements.txt', 'reboot_wheels.yml'}


def require(ok, code):
    if not ok: raise RuntimeError(code)


def read(path, maximum=2 * 1024 * 1024):
    require(path.parent == ROOT, 'foreign_controller_file')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        require(stat.S_ISREG(before.st_mode) and before.st_uid == os.geteuid() and before.st_nlink == 1 and
                not before.st_mode & 0o077 and before.st_size <= maximum, 'unsafe_controller_file')
        raw = os.read(fd, maximum + 1)
        after = os.fstat(fd)
        same = lambda value: (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns)
        require(len(raw) <= maximum and same(before) == same(after) == same(path.lstat()), 'controller_file_changed')
        return raw
    finally: os.close(fd)


def publish(name, payload):
    path = ROOT / name
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        raw = (json.dumps(payload, sort_keys=True) + '\n').encode()
        while raw:
            count = os.write(fd, raw)
            require(count > 0, 'controller_short_write')
            raw = raw[count:]
        os.fsync(fd)
    finally: os.close(fd)
    directory = os.open(ROOT, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try: os.fsync(directory)
    finally: os.close(directory)


def execute(argv, environment, log_name, seconds):
    require(datetime.now(timezone.utc) < DEADLINE, 'outer_window_expired')
    limit = 8 * 1024 * 1024
    fd = os.open(ROOT / log_name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    def bounds():
        resource.setrlimit(resource.RLIMIT_FSIZE, (limit, limit))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    try:
        with subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=fd, stderr=fd, env=environment,
                              start_new_session=True, preexec_fn=bounds) as child:
            try: return child.wait(timeout=min(seconds, max(1, (DEADLINE - datetime.now(timezone.utc)).total_seconds())))
            except subprocess.TimeoutExpired:
                os.killpg(child.pid, 15)
                try: child.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    os.killpg(child.pid, 9)
                    child.wait(timeout=10)
                raise RuntimeError('controller_subprocess_timeout') from None
    finally:
        os.fsync(fd)
        os.close(fd)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', required=True)
    parser.add_argument('--manifest-sha256', required=True)
    parser.add_argument('--nonce', required=True)
    parser.add_argument('--source-revision', required=True)
    args = parser.parse_args()
    require(args.apply == 'ONE-BOT-KERNEL-REBOOT-20261023', 'explicit_controller_apply_required')
    require(os.geteuid() == 0 and Path('/proc/1/exe').readlink().name == 'systemd', 'native_web_root_required')
    require(not any(Path(path).exists() for path in ('/.dockerenv', '/run/.containerenv')), 'container_refused')
    for path in (ROOT.parent, ROOT):
        info = path.lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and not info.st_mode & (0o077 if path == ROOT else 0o022), 'unsafe_controller_directory')
    require(datetime.now(timezone.utc) < CUTOFF, 'mutation_cutoff')
    manifest_raw = read(ROOT / 'reboot_manifest.json')
    manifest = json.loads(manifest_raw)
    digest = sha256(manifest_raw).hexdigest()
    require(digest == args.manifest_sha256 and manifest['nonce'] == args.nonce and
            manifest['source_revision'] == args.source_revision, 'controller_launch_binding_mismatch')
    require(manifest['schema'] == 'security_kernel_reboot/1' and manifest['operation_id'] == OPERATION and
            manifest['target_kernel'] == '6.8.0-147-generic' and re.fullmatch('[0-9a-f-]{36}', manifest['nonce']),
            'controller_manifest_scope')
    ready = json.loads(read(ROOT / 'controller_ready.json'))
    require(ready['source_revision'] == manifest['source_revision'] and ready['strict_reverse_ssh'] is True and
            ready['status'] == 'ready' and ready['task_id'] == 'host-patch-02', 'controller_not_ready')
    require(sys.version_info[:3] == (3, 14, 4) and
            ready['web_machine_id'] == Path('/etc/machine-id').read_text().strip() != manifest['machine_id'] and
            ready['web_boot_id'] == Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
            'controller_not_independent_native_web')
    for path in (KEY, PIN):
        info = os.lstat(path)  # Metadata only; NEVER open/read/hash the private key.
        require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and info.st_nlink == 1 and
                stat.S_IMODE(info.st_mode) == 0o600, 'unsafe_controller_ssh_metadata')
    require(importlib.metadata.version('ansible-core') == '2.21.5', 'controller_ansible_version')
    assets = json.loads(read(ROOT / 'reboot_assets.json'))
    require(set(assets) == ASSETS, 'controller_asset_scope')
    for name, expected in assets.items():
        require(sha256(read(ROOT / name)).hexdigest() == expected, 'controller_asset_changed')
    environment = {'PATH': str(ROOT / 'venv/bin') + ':/usr/sbin:/usr/bin:/sbin:/bin', 'LANG': 'C.UTF-8',
                   'ANSIBLE_CONFIG': str(ROOT / 'reboot_ansible.cfg'), 'ANSIBLE_HOME': str(ROOT / 'ansible-home'),
                   'ANSIBLE_LOCAL_TEMP': str(ROOT / 'ansible-tmp')}
    variables = {'reboot_apply': True, 'reboot_manifest': NATIVE_MANIFEST, 'reboot_manifest_sha256': digest}
    command = [str(ROOT / 'venv/bin/ansible-playbook'), '-i', str(ROOT / 'reboot_inventory.yml'),
               str(ROOT / 'reboot_external.yml'), '-e', json.dumps(variables)]
    # Exercise the real Ansible SSH/SFTP/command-module lane in this exact
    # durable runner's sandbox before any single-attempt/reboot intent exists.
    require(execute(command + ['--tags', 'controller_preflight'], environment, 'preflight.log', 180) == 0,
            'external_command_preflight_failed_no_reboot_intent')
    publish('controller-intent.json', {'schema': 'security_kernel_reboot_controller_intent/1',
            'operation_id': OPERATION, 'manifest_sha256': digest, 'nonce': manifest['nonce'],
            'source_revision': manifest['source_revision'], 'status': 'single_attempt_started'})
    status = execute(command, environment, 'ansible.log', 3200)
    require(status == 0, 'external_ansible_failed_no_automatic_reboot_retry')
    result = json.loads(read(ROOT / 'native-result.json'))
    require(result['status'] == 'completed' and result['manifest_sha256'] == digest and
            result['nonce'] == manifest['nonce'] and result['active_kernel'] == '6.8.0-147-generic' and
            result['before_boot_id'] == manifest['before_boot_id'] and result['after_boot_id'] != manifest['before_boot_id'] and
            result['containers_verified'] == 15 and result['marker_journal_bytes_unchanged'] is True and
            result['authority_changed'] is False and result['application_image_changed'] is False,
            'native_completed_receipt_invalid')
    # This is the FINAL SSH request: native cleanup revokes this exact key and
    # removes Web's own key files while this already-authenticated stream ends.
    command = ('/usr/bin/python3 -I -B /root/trading-bot/trading_bot/deploy/maintenance/reboot_guard.py '
               'cleanup-controller --manifest ' + NATIVE_MANIFEST + ' --manifest-sha256 ' + digest + ' --apply')
    cleanup = execute(['/usr/bin/ssh', '-F', '/dev/null', '-i', KEY, '-p', '22', '-o', 'BatchMode=yes',
                       '-o', 'IdentitiesOnly=yes', '-o', 'ForwardAgent=no', '-o', 'StrictHostKeyChecking=yes',
                       '-o', 'GlobalKnownHostsFile=/dev/null', '-o', 'UserKnownHostsFile=' + PIN,
                       '-o', 'HostKeyAlgorithms=ssh-ed25519', '-o', 'ConnectTimeout=10',
                       'root@65.109.216.187', command], environment, 'credential-cleanup.log', 240)
    require(cleanup == 0 and not os.path.lexists(KEY) and not os.path.lexists(PIN), 'temporary_controller_cleanup_incomplete')
    publish('controller-result.json', {'schema': 'security_kernel_reboot_controller_result/1',
            'operation_id': OPERATION, 'manifest_sha256': digest, 'nonce': manifest['nonce'],
            'status': 'completed', 'active_kernel': result['active_kernel'], 'temporary_credential_cleanup': 'passed',
            'authority_changed': False, 'private_key_contents_transferred': False,
            'recorded_at_utc': datetime.now(timezone.utc).isoformat()})
    return 0


if __name__ == '__main__':
    try: raise SystemExit(main())
    except RuntimeError as error:
        print(json.dumps({'status': 'failed_closed', 'code': str(error)}), file=sys.stderr)
        raise SystemExit(1)
