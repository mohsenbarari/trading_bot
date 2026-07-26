#!/usr/bin/env python3
"""Bootstrap key-only SSH on disposable Full Matrix Arvan hosts.

The provider currently creates Ubuntu images with an expired one-time
password even when an init script was supplied.  This tool changes that
password only when PAM requires it, persists the replacement only while it is
needed, installs the controller key, disables password login, verifies key
authentication, and then removes the password from the owner-only state.

The tool is idempotent and has no delete operation.
"""

from __future__ import annotations

import argparse
import base64
from datetime import datetime, timezone
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import stat
import subprocess
import sys
from typing import Any

import pexpect


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.provision_arvan_full_matrix_destructive_hosts import (  # noqa: E402
    CONTROL_IP,
    KNOWN_HOSTS_FILE,
    PRIVATE_KEY_FILE,
    PUBLIC_KEY_FILE,
    ROLE_ORDER,
    STATE_FILE,
    DestructiveProvisionError,
    _atomic_state,
    _safe_existing_state,
)
from scripts.provision_arvan_witness_recovery_vps import (  # noqa: E402
    validate_public_key,
)


SSH_TIMEOUT_SECONDS = 20
KEY_LINE = re.compile(
    r"(?P<host>[0-9.]+) ssh-ed25519 (?P<key>[A-Za-z0-9+/]+={0,3})\Z"
)


class BootstrapError(DestructiveProvisionError):
    """Disposable host bootstrap failed closed."""


def _require_private_file(path: Path, *, mode: int | None = None) -> None:
    metadata = path.lstat()
    if (
        path.is_symlink()
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or metadata.st_nlink != 1
    ):
        raise BootstrapError(f"unsafe private file: {path}")
    if mode is not None and stat.S_IMODE(metadata.st_mode) != mode:
        raise BootstrapError(f"private file mode differs from {mode:04o}: {path}")


def _state() -> dict[str, Any]:
    value = _safe_existing_state()
    if value is None or value.get("status") != "active":
        raise BootstrapError("destructive host state is not active")
    hosts = value.get("hosts")
    if not isinstance(hosts, dict) or set(hosts) != set(ROLE_ORDER):
        raise BootstrapError("destructive host state has an invalid role set")
    for role in ROLE_ORDER:
        item = hosts[role]
        if not isinstance(item, dict) or item.get("role") != role:
            raise BootstrapError(f"destructive state role is invalid: {role}")
        try:
            address = ipaddress.ip_address(str(item["public_ip"]))
        except (KeyError, ValueError) as exc:
            raise BootstrapError(f"destructive role IP is invalid: {role}") from exc
        if address.version != 4 or address.is_private or address.is_loopback:
            raise BootstrapError(f"destructive role IP is unsafe: {role}")
    return value


def _known_hosts_lines() -> list[str]:
    if not KNOWN_HOSTS_FILE.exists():
        return []
    _require_private_file(KNOWN_HOSTS_FILE, mode=0o600)
    try:
        lines = KNOWN_HOSTS_FILE.read_text(encoding="ascii").splitlines()
    except UnicodeDecodeError as exc:
        raise BootstrapError("known_hosts is not ASCII") from exc
    for line in lines:
        match = KEY_LINE.fullmatch(line)
        if match is None:
            raise BootstrapError("known_hosts contains an unsupported entry")
        try:
            base64.b64decode(match.group("key"), validate=True)
        except ValueError as exc:
            raise BootstrapError("known_hosts contains an invalid key") from exc
    return lines


def _atomic_known_hosts(lines: list[str]) -> None:
    KNOWN_HOSTS_FILE.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if KNOWN_HOSTS_FILE.parent.is_symlink() or KNOWN_HOSTS_FILE.is_symlink():
        raise BootstrapError("known_hosts path is unsafe")
    raw = ("\n".join(lines) + "\n").encode("ascii")
    temporary = KNOWN_HOSTS_FILE.with_name(
        f".{KNOWN_HOSTS_FILE.name}.{os.getpid()}.tmp"
    )
    descriptor = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC,
        0o600,
    )
    try:
        offset = 0
        while offset < len(raw):
            written = os.write(descriptor, raw[offset:])
            if written <= 0:
                raise BootstrapError("short known_hosts write")
            offset += written
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.replace(temporary, KNOWN_HOSTS_FILE)
    os.chmod(KNOWN_HOSTS_FILE, 0o600)


def ensure_host_key(ip: str) -> str:
    lines = _known_hosts_lines()
    existing = [line for line in lines if line.startswith(f"{ip} ")]
    if len(existing) > 1:
        raise BootstrapError(f"duplicate SSH host key: {ip}")
    if existing:
        return existing[0]
    result = subprocess.run(
        ["ssh-keyscan", "-T", "10", "-t", "ed25519", ip],
        text=True,
        capture_output=True,
        check=False,
        timeout=15,
    )
    candidates = [
        line.strip()
        for line in result.stdout.splitlines()
        if line and not line.startswith("#")
    ]
    if result.returncode != 0 or len(candidates) != 1:
        raise BootstrapError(f"cannot acquire exact SSH host key: {ip}")
    match = KEY_LINE.fullmatch(candidates[0])
    if match is None or match.group("host") != ip:
        raise BootstrapError(f"SSH host key response is invalid: {ip}")
    try:
        decoded = base64.b64decode(match.group("key"), validate=True)
    except ValueError as exc:
        raise BootstrapError(f"SSH host key is invalid: {ip}") from exc
    if len(decoded) < 32:
        raise BootstrapError(f"SSH host key is too short: {ip}")
    lines.append(candidates[0])
    _atomic_known_hosts(lines)
    return candidates[0]


def _ssh_base(
    ip: str,
    *,
    password_only: bool,
    force_tty: bool = False,
) -> list[str]:
    command = [
        "ssh",
        "-o",
        "BatchMode=no" if password_only else "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=yes",
        "-o",
        f"UserKnownHostsFile={KNOWN_HOSTS_FILE}",
        "-o",
        f"ConnectTimeout={SSH_TIMEOUT_SECONDS}",
        "-o",
        "IdentitiesOnly=yes",
    ]
    if password_only:
        if force_tty:
            command.append("-tt")
        command.extend(
            [
                "-o",
                "PubkeyAuthentication=no",
                "-o",
                "PreferredAuthentications=password",
            ]
        )
    else:
        command.extend(["-i", str(PRIVATE_KEY_FILE)])
    command.append(f"ubuntu@{ip}")
    return command


def key_auth_works(ip: str) -> bool:
    result = subprocess.run(
        [*_ssh_base(ip, password_only=False), "printf KEY_AUTH_OK"],
        text=True,
        capture_output=True,
        check=False,
        timeout=SSH_TIMEOUT_SECONDS + 5,
    )
    return result.returncode == 0 and result.stdout == "KEY_AUTH_OK"


def _password_auth_or_rotate(ip: str, password: str) -> str:
    if not password or len(password) > 1024 or "\n" in password:
        raise BootstrapError("bootstrap password is unavailable or unsafe")
    child = pexpect.spawn(
        _ssh_base(ip, password_only=True, force_tty=True)[0],
        [
            *_ssh_base(ip, password_only=True, force_tty=True)[1:],
            "printf PASSWORD_AUTH_OK",
        ],
        encoding="utf-8",
        timeout=SSH_TIMEOUT_SECONDS,
        echo=False,
    )
    try:
        index = child.expect(
            [
                r"(?i)password:",
                "PASSWORD_AUTH_OK",
                r"(?i)permission denied",
                pexpect.EOF,
            ]
        )
        if index == 1:
            return password
        if index != 0:
            raise BootstrapError(f"password authentication failed: {ip}")
        child.sendline(password)
        index = child.expect(
            [
                "PASSWORD_AUTH_OK",
                r"Current password:",
                r"(?i)permission denied",
                pexpect.EOF,
            ]
        )
        if index == 0:
            child.expect(pexpect.EOF)
            return password
        if index != 1:
            raise BootstrapError(f"bootstrap password was rejected: {ip}")
        replacement = secrets.token_urlsafe(36)
        child.sendline(password)
        child.expect(r"New password:")
        child.sendline(replacement)
        child.expect(r"Retype new password:")
        child.sendline(replacement)
        child.expect(r"password updated successfully", timeout=30)
        child.expect(pexpect.EOF, timeout=30)
        return replacement
    except (pexpect.TIMEOUT, pexpect.EOF) as exc:
        raise BootstrapError(f"password bootstrap dialogue failed: {ip}") from exc
    finally:
        child.close(force=True)


def _remote_bootstrap_script(public_key: str, role: str) -> str:
    if role not in ROLE_ORDER:
        raise BootstrapError("bootstrap role is invalid")
    key = base64.b64encode((public_key + "\n").encode()).decode("ascii")
    role_value = base64.b64encode((role + "\n").encode()).decode("ascii")
    return f"""set -Eeuo pipefail
umask 077
install -d -m 0700 /root/.ssh /home/ubuntu/.ssh
printf '%s' '{key}' | base64 -d >/root/.ssh/authorized_keys
install -o ubuntu -g ubuntu -m 0600 /root/.ssh/authorized_keys /home/ubuntu/.ssh/authorized_keys
chown root:root /root/.ssh /root/.ssh/authorized_keys
chown ubuntu:ubuntu /home/ubuntu/.ssh /home/ubuntu/.ssh/authorized_keys
install -d -m 0755 /etc/ssh/sshd_config.d
cat >/etc/ssh/sshd_config.d/00-full-matrix-destructive.conf <<'EOF'
PubkeyAuthentication yes
PermitRootLogin prohibit-password
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitEmptyPasswords no
EOF
sshd -t
systemctl reload ssh || systemctl reload sshd
printf '%s' '{role_value}' | base64 -d >/etc/full-matrix-destructive-role
chmod 0644 /etc/full-matrix-destructive-role
install -d -m 0700 /var/lib/trading-bot-full-matrix
touch /var/lib/trading-bot-full-matrix/bootstrap-complete
"""


def _bootstrap_with_password(
    ip: str,
    password: str,
    public_key: str,
    role: str,
) -> None:
    environment = os.environ.copy()
    environment["SSHPASS"] = password
    result = subprocess.run(
        [
            "sshpass",
            "-e",
            *_ssh_base(ip, password_only=True),
            "sudo -n /bin/bash -s",
        ],
        input=_remote_bootstrap_script(public_key, role),
        text=True,
        capture_output=True,
        check=False,
        timeout=60,
        env=environment,
    )
    if result.returncode != 0:
        raise BootstrapError(f"remote bootstrap failed: {role}")


def _persist_password(
    state: dict[str, Any],
    role: str,
    password: str,
) -> None:
    state["hosts"][role]["bootstrap_password"] = password
    state["updated_at"] = datetime.now(timezone.utc).isoformat()
    _atomic_state(state)


def bootstrap_role(state: dict[str, Any], role: str, public_key: str) -> None:
    item = state["hosts"][role]
    ip = str(item["public_ip"])
    ensure_host_key(ip)
    if not key_auth_works(ip):
        password = _password_auth_or_rotate(
            ip,
            str(item.get("bootstrap_password") or ""),
        )
        _persist_password(state, role, password)
        _bootstrap_with_password(ip, password, public_key, role)
    if not key_auth_works(ip):
        raise BootstrapError(f"key authentication verification failed: {role}")
    item.pop("bootstrap_password", None)
    item["ssh_bootstrap"] = "key-only"
    item["ssh_user"] = "ubuntu"
    item["ssh_control_cidr"] = CONTROL_IP
    item["bootstrapped_at"] = datetime.now(timezone.utc).isoformat()
    state["updated_at"] = datetime.now(timezone.utc).isoformat()
    _atomic_state(state)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument(
        "--role",
        action="append",
        choices=ROLE_ORDER,
        dest="roles",
    )
    args = parser.parse_args(argv)
    state = _state()
    public_key = validate_public_key(PUBLIC_KEY_FILE)
    _require_private_file(PRIVATE_KEY_FILE)
    roles = tuple(args.roles or ROLE_ORDER)
    if not args.apply:
        print(
            json.dumps(
                {
                    "status": "dry_run",
                    "apply": False,
                    "roles": list(roles),
                    "delete_operation_available": False,
                    "state_file": str(STATE_FILE),
                    "known_hosts_file": str(KNOWN_HOSTS_FILE),
                },
                sort_keys=True,
            )
        )
        return 0
    completed: list[str] = []
    for role in roles:
        bootstrap_role(state, role, public_key)
        completed.append(role)
    print(
        json.dumps(
            {
                "status": "bootstrapped",
                "apply": True,
                "roles": completed,
                "ssh_mode": "key-only",
                "bootstrap_passwords_retained": False,
                "delete_operation_available": False,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except DestructiveProvisionError as exc:
        print(
            json.dumps(
                {"status": "failed", "error": str(exc)},
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        raise SystemExit(1)
