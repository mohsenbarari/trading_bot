#!/usr/bin/env python3
"""Install one exact-SHA-reviewed Antigravity gateway in root-owned storage.

This grants only the reviewed typed operation catalog. It does not claim a fence,
start Antigravity, touch a server, or perform any project Stage mutation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

import jsonschema

if __package__ in {None, ''}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.two_site_agent_context import PROTECTED_SOURCE

INSTALL_ROOT = Path('/usr/local/libexec/two-site-agent-gateway.d')
LAUNCHER = Path('/usr/local/libexec/two-site-agent-gateway')
RECOVERY_LAUNCHER = Path('/usr/local/libexec/two-site-agent-recover')
STATE_PARENT = Path('/var/lib/two-site-agent-gateway')
STATE_ROOT = STATE_PARENT / 'controller'
MANIFEST = STATE_PARENT / 'manifest.json'
JOURNAL = STATE_PARENT / 'install-journal.json'
BRANCH = 'refactor/two-site-architecture'
UUID = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\Z')
SHA40 = re.compile(r'[0-9a-f]{40}\Z')


class InstallError(RuntimeError):
    pass


def run(root: Path, *arguments: str, output_limit: int = 4_000_000) -> bytes:
    result = subprocess.run(
        ['/usr/bin/git', '--no-replace-objects', '-C', str(root), *arguments],
        stdin=subprocess.DEVNULL, capture_output=True, check=False,
        env={'PATH': '/usr/local/bin:/usr/bin:/bin', 'LANG': 'C.UTF-8',
             'GIT_CONFIG_NOSYSTEM': '1', 'GIT_OPTIONAL_LOCKS': '0'},
    )
    if len(result.stdout) + len(result.stderr) > output_limit or result.returncode:
        raise InstallError('Git provenance check failed: ' + arguments[0])
    return result.stdout


def file_sha256(path: Path) -> str:
    value = hashlib.sha256()
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise InstallError('installation input is not a regular file')
        with os.fdopen(descriptor, 'rb', closefd=False) as source:
            while chunk := source.read(1024 * 1024):
                value.update(chunk)
    finally:
        os.close(descriptor)
    return value.hexdigest()


def canonical(path: Path, *, directory: bool = False) -> Path:
    if not path.is_absolute() or path.resolve(strict=True) != path:
        raise InstallError('installation path is not canonical')
    if directory and not path.is_dir():
        raise InstallError('installation workspace is not a directory')
    if not directory and not path.is_file():
        raise InstallError('installation input is not a file')
    return path


def atomic_bytes(path: Path, content: bytes, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
    descriptor, temporary = tempfile.mkstemp(prefix='.' + path.name + '.', dir=path.parent)
    try:
        view = memoryview(content)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise InstallError('atomic installation write made no progress')
            view = view[written:]
        os.fsync(descriptor)
        os.fchmod(descriptor, mode)
        os.close(descriptor)
        descriptor = -1
        os.replace(temporary, path)
        parent = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(parent)
        finally:
            os.close(parent)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def binary(name: str, explicit: Path) -> dict[str, str]:
    if not explicit.is_absolute():
        raise InstallError('required binary path is not absolute: ' + name)
    path = explicit.resolve(strict=True)
    canonical(path)
    metadata = path.stat()
    if (not os.access(path, os.X_OK) or metadata.st_uid != 0
            or metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH)):
        raise InstallError('required binary is not root-owned/non-writable/executable: ' + name)
    return {'path': str(path), 'sha256': file_sha256(path)}


def validate_receipt(root: Path, path: Path, accepted: str) -> bytes:
    canonical(path)
    raw = path.read_bytes()
    if len(raw) > 1_000_000:
        raise InstallError('governance receipt exceeds its budget')
    try:
        receipt = json.loads(raw)
        schema = json.loads((root / 'docs/refactor/two-server/templates/governance-review.schema.json').read_text())
        jsonschema.Draft202012Validator(schema, format_checker=jsonschema.FormatChecker()).validate(receipt)
    except (ValueError, jsonschema.ValidationError) as error:
        raise InstallError('governance receipt is invalid') from error
    if (receipt.get('reviewer_authority') != 'CODEX_FINAL_REVIEWER'
            or receipt.get('receipt_type') != 'EXECUTION_GOVERNANCE_AMENDMENT'
            or receipt.get('verdict') != 'APPROVE'
            or receipt.get('reviewed_head_sha') != accepted
            or receipt.get('decision', {}).get('accepted_commit') != accepted
            or receipt.get('decision', {}).get('stage_completion_granted') is not False
            or receipt.get('decision', {}).get('conditions')
            or receipt.get('scope', {}).get('stage_state_unchanged') is not True
            or receipt.get('scope', {}).get('changed_paths_within_scope') is not True
            or receipt.get('scope', {}).get('unauthorized_external_mutation') != 'NONE'
            or not receipt.get('verification')
            or any(item.get('result') != 'PASS' for item in receipt['verification'])
            or any(item.get('severity') in {'HIGH', 'CRITICAL'} for item in receipt.get('findings', []))):
        raise InstallError('governance receipt does not authorize this exact installation SHA')
    return raw


def expected_hooks() -> dict:
    command = '/usr/local/libexec/two-site-agent-gateway'
    return {'two-site-typed-operation-gate': {
        'PreToolUse': [{'matcher': '*', 'hooks': [
            {'type': 'command', 'command': command + ' pre', 'timeout': 720}]}],
        'PostToolUse': [{'matcher': '*', 'hooks': [
            {'type': 'command', 'command': command + ' post', 'timeout': 2400}]}],
        'Stop': [{'type': 'command', 'command': command + ' stop', 'timeout': 720}],
    }}


def install(root: Path, accepted: str, coordinator: str, receipt_path: Path,
            stage_workspace_root: Path, codex_binary: Path, agy_binary: Path) -> dict:
    if os.geteuid() != 0:
        raise InstallError('root ownership is required for protected installation')
    root = canonical(root, directory=True)
    if not SHA40.fullmatch(accepted) or not UUID.fullmatch(coordinator):
        raise InstallError('accepted SHA or Coordinator UUID is malformed')
    if run(root, 'branch', '--show-current').decode().strip() != BRANCH:
        raise InstallError('installer must run from the integration branch')
    if run(root, 'rev-parse', 'HEAD').decode().strip() != accepted:
        raise InstallError('working HEAD differs from the reviewed SHA')
    if run(root, 'status', '--porcelain=v1', '--untracked-files=all'):
        raise InstallError('reviewed installation requires a fully clean repository')
    if json.loads((root / '.agents/hooks.json').read_text()) != expected_hooks():
        raise InstallError('active hook differs from the reviewed project-only gate')
    runtime_registry = json.loads(
        (root / '.local/two-site-refactor/antigravity-runtime-sessions.json').read_text())
    if (runtime_registry.get('execution_coordinator') != coordinator
            or not re.fullmatch(r'[0-9a-f]{64}', str(runtime_registry.get('cli_sha256', '')))):
        raise InstallError('runtime registry is not bound to this execution Coordinator/CLI')
    receipt = validate_receipt(root, receipt_path, accepted)
    stage_workspace_root = canonical(stage_workspace_root, directory=True)
    if stage_workspace_root == root or stage_workspace_root.is_relative_to(root):
        raise InstallError('Stage workspace root overlaps the integration repository')
    workspace_values = [root, stage_workspace_root]

    source_hashes: dict[str, str] = {}
    version = INSTALL_ROOT / accepted
    recover_completed = False
    if JOURNAL.exists():
        journal = json.loads(JOURNAL.read_text())
        if (journal.get('accepted_sha') != accepted or journal.get('root') != str(root)
                or set(journal) != {'schema_version', 'accepted_sha', 'root'}):
            raise InstallError('another incomplete protected installation requires review')
        if LAUNCHER.exists():
            if (not RECOVERY_LAUNCHER.exists() or not MANIFEST.exists()
                    or json.loads(MANIFEST.read_text()).get('accepted_sha') != accepted):
                raise InstallError('active launcher has an incomplete installation journal')
            recover_completed = True
        else:
            for target in (RECOVERY_LAUNCHER, MANIFEST):
                if target.exists():
                    target.unlink()
            if STATE_ROOT.exists():
                shutil.rmtree(STATE_ROOT)
            if version.exists():
                shutil.rmtree(version)
            JOURNAL.unlink()
    if (not recover_completed
            and (version.exists() or LAUNCHER.exists() or RECOVERY_LAUNCHER.exists() or MANIFEST.exists())):
        raise InstallError('a protected gateway installation already exists')
    if not recover_completed and STATE_ROOT.exists() and any(STATE_ROOT.iterdir()):
        raise InstallError('protected controller state is not empty')
    for relative in PROTECTED_SOURCE:
        source = canonical(root / relative)
        committed = run(root, 'show', accepted + ':' + relative, output_limit=2_500_000)
        actual = source.read_bytes()
        if actual != committed:
            raise InstallError('source is not the exact accepted commit: ' + relative)
        source_hashes[relative] = hashlib.sha256(actual).hexdigest()
    binaries = {
        'git': binary('git', Path('/usr/bin/git')),
        'python': binary('python', Path('/usr/bin/python3')),
        'bwrap': binary('bwrap', Path('/usr/bin/bwrap')),
        'prlimit': binary('prlimit', Path('/usr/bin/prlimit')),
        'codex': binary('codex', codex_binary),
        'agy': binary('agy', agy_binary),
    }
    if binaries['agy']['sha256'] != runtime_registry['cli_sha256']:
        raise InstallError('Antigravity CLI differs from the runtime-bound digest')
    pre_commit = canonical(root / '.git/hooks/pre-commit')
    memory_guard = run(root, 'show', 'main:scripts/check_memory_secrets.py')
    manifest = {
        'schema_version': '1.0', 'root': str(root),
        'stage_workspace_root': str(stage_workspace_root),
        'workspaces': [str(value) for value in workspace_values],
        'coordinator_id': coordinator, 'accepted_sha': accepted,
        'source_sha256': source_hashes,
        'governance_receipt_sha256': hashlib.sha256(receipt).hexdigest(),
        'pre_commit_sha256': file_sha256(pre_commit),
        'main_memory_guard_sha256': hashlib.sha256(memory_guard).hexdigest(),
        'binary_sha256': binaries, 'mode': 'ACTIVE',
    }
    bootstrap_content = (
        'import sys\n'
        f"sys.path.insert(0, {str(version)!r})\n"
        'from scripts.two_site_agent_host import main\nraise SystemExit(main())\n'
    ).encode()
    recovery_bootstrap_content = (
        'import sys\n'
        f"sys.path.insert(0, {str(version)!r})\n"
        'from scripts.two_site_agent_recovery import main\nraise SystemExit(main())\n'
    ).encode()
    recovery_launcher = (
        '#!/bin/sh\nexec /usr/bin/env -i HOME=/root PATH=/usr/local/bin:/usr/bin:/bin '
        'PYTHONDONTWRITEBYTECODE=1 '
        f"{binaries['python']['path']} -I {version / 'recovery_bootstrap.py'} \"$@\"\n"
    ).encode()
    launcher = (
        '#!/bin/sh\nexec /usr/bin/env -i HOME=/root PATH=/usr/local/bin:/usr/bin:/bin '
        'PYTHONDONTWRITEBYTECODE=1 '
        f"{binaries['python']['path']} -I {version / 'gateway_bootstrap.py'} \"$@\"\n"
    ).encode()
    if recover_completed:
        if json.loads(MANIFEST.read_text()) != manifest:
            raise InstallError('completed journal manifest differs from reviewed installation')
        if (STATE_ROOT / 'governance-receipt.json').read_bytes() != receipt:
            raise InstallError('completed journal governance receipt differs from reviewed bytes')
        expected_files = {relative: (root / relative).read_bytes() for relative in PROTECTED_SOURCE}
        expected_files['gateway_bootstrap.py'] = bootstrap_content
        expected_files['recovery_bootstrap.py'] = recovery_bootstrap_content
        if any(not (version / relative).is_file()
                   or (version / relative).read_bytes() != content
                   for relative, content in expected_files.items()):
            raise InstallError('completed journal installed source differs from reviewed bytes')
        if LAUNCHER.read_bytes() != launcher or RECOVERY_LAUNCHER.read_bytes() != recovery_launcher:
            raise InstallError('completed journal launchers differ from reviewed bytes')
        JOURNAL.unlink()
        return manifest

    INSTALL_ROOT.mkdir(parents=True, exist_ok=True, mode=0o755)
    STATE_PARENT.mkdir(parents=True, exist_ok=True, mode=0o700)
    STATE_PARENT.chmod(0o700)
    atomic_bytes(JOURNAL, json.dumps({
        'schema_version': '1.0', 'accepted_sha': accepted, 'root': str(root),
    }, sort_keys=True, separators=(',', ':')).encode() + b'\n', 0o600)
    temporary_version = Path(tempfile.mkdtemp(prefix='.' + accepted + '.', dir=INSTALL_ROOT))
    temporary_state = Path(tempfile.mkdtemp(prefix='.controller.', dir=STATE_PARENT))
    installed_version = False
    installed_state = False
    installed_manifest = False
    installed_recovery = False
    installed_launcher = False
    try:
        for relative in PROTECTED_SOURCE:
            destination = temporary_version / relative
            destination.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
            destination.write_bytes((root / relative).read_bytes())
            destination.chmod(0o555 if destination.suffix in {'.py', '.sh'} else 0o444)
        package = temporary_version / 'scripts/__init__.py'
        package.touch(mode=0o444, exist_ok=False)
        bootstrap = temporary_version / 'gateway_bootstrap.py'
        bootstrap.write_bytes(bootstrap_content)
        bootstrap.chmod(0o444)
        recovery_bootstrap = temporary_version / 'recovery_bootstrap.py'
        recovery_bootstrap.write_bytes(recovery_bootstrap_content)
        recovery_bootstrap.chmod(0o444)
        atomic_bytes(temporary_state / 'governance-receipt.json', receipt, 0o600)
        os.replace(temporary_version, version)
        installed_version = True
        if STATE_ROOT.exists():
            STATE_ROOT.rmdir()
        os.replace(temporary_state, STATE_ROOT)
        installed_state = True
        atomic_bytes(MANIFEST, json.dumps(manifest, sort_keys=True, separators=(',', ':')).encode() + b'\n', 0o600)
        installed_manifest = True
        atomic_bytes(RECOVERY_LAUNCHER, recovery_launcher, 0o550)
        installed_recovery = True
        atomic_bytes(LAUNCHER, launcher, 0o555)
        installed_launcher = True
        JOURNAL.unlink()
    except Exception:
        if temporary_version.exists():
            shutil.rmtree(temporary_version)
        if temporary_state.exists():
            shutil.rmtree(temporary_state)
        if installed_launcher and LAUNCHER.exists():
            LAUNCHER.unlink()
        if installed_recovery and RECOVERY_LAUNCHER.exists():
            RECOVERY_LAUNCHER.unlink()
        if installed_manifest and MANIFEST.exists():
            MANIFEST.unlink()
        if installed_state and STATE_ROOT.exists():
            shutil.rmtree(STATE_ROOT)
        if installed_version and version.exists():
            shutil.rmtree(version)
        raise
    return manifest


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser()
    result.add_argument('--root', type=Path, required=True)
    result.add_argument('--accepted-sha', required=True)
    result.add_argument('--coordinator-id', required=True)
    result.add_argument('--governance-receipt', type=Path, required=True)
    result.add_argument('--stage-workspace-root', type=Path, required=True)
    result.add_argument('--agy-binary', type=Path, required=True)
    result.add_argument('--codex-binary', type=Path, required=True)
    return result


def main() -> int:
    args = parser().parse_args()
    try:
        manifest = install(args.root, args.accepted_sha, args.coordinator_id,
                           args.governance_receipt, args.stage_workspace_root,
                           args.codex_binary, args.agy_binary)
    except InstallError as error:
        print('BLOCKED: ' + str(error))
        return 1
    print('INSTALLED=' + manifest['accepted_sha'])
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
