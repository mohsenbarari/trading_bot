#!/usr/bin/env python3
"""Install the local fenced WA-IR dark listener and its local TLS pair.

This helper deliberately has no DNS, Arvan, Object Storage, SSH, or Certbot
invocation capability.  It accepts only a certificate and key that Certbot has
already issued locally on WA-IR, copies them into the fixed local listener TLS
root, and renders the pinned dark (external 503) Nginx template.  It never
changes public routing.  On a local Nginx validation or reload failure it
restores the prior site, enabled-link, and TLS files before returning failure.

``--certbot-deploy-hook`` is intentionally narrower: Certbot supplies its
renewed-lineage environment, which must exactly equal the configured one.  It
refreshes only the local TLS pair and validates/reloads the existing local
Nginx configuration; it does not render or replace any listener site.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile
from typing import Callable, Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TEMPLATE = REPO_ROOT / "deploy/production/nginx-webapp-ir-standby-dark-https.conf.template"
SERVER_NAME = "coin.gold-trade.ir"
CERTIFICATE_NAME = "fullchain.pem"
KEY_NAME = "privkey.pem"
SITE_NAME = "trading-bot"
RECEIPT_SCHEMA = "gold-trade-wa-ir-dark-listener-v1"
MAX_CONFIG_BYTES = 16 * 1024
MAX_CERTIFICATE_BYTES = 4 * 1024 * 1024
MAX_SITE_BYTES = 1024 * 1024
MAX_BINARY_BYTES = 64 * 1024 * 1024
SAFE_PATH = re.compile(r"^/[A-Za-z0-9._/-]+$")
PLACEHOLDER = re.compile(r"__[A-Z0-9_]+__")

CONFIG_KEYS = frozenset(
    {
        "WA_IR_DARK_LISTENER_SERVER_NAME",
        "WA_IR_DARK_LISTENER_CERTBOT_ROOT",
        "WA_IR_DARK_LISTENER_CERTBOT_LINEAGE",
        "WA_IR_DARK_LISTENER_TLS_ROOT",
        "WA_IR_DARK_LISTENER_CERTIFICATE_PATH",
        "WA_IR_DARK_LISTENER_CERTIFICATE_KEY_PATH",
        "WA_IR_DARK_LISTENER_SITE_PATH",
        "WA_IR_DARK_LISTENER_ENABLED_PATH",
        "WA_IR_DARK_LISTENER_RECEIPT_PATH",
    }
)


class DarkListenerError(RuntimeError):
    """Raised when the local-only listener transaction cannot proceed."""


CommandRunner = Callable[[Sequence[str]], subprocess.CompletedProcess[str]]


@dataclass(frozen=True)
class DarkListenerConfig:
    server_name: str
    certbot_root: Path
    certbot_lineage: Path
    tls_root: Path
    certificate_path: Path
    certificate_key_path: Path
    site_path: Path
    enabled_path: Path
    receipt_path: Path


@dataclass(frozen=True)
class FileSnapshot:
    exists: bool
    payload: bytes | None
    mode: int | None


def _require_root() -> None:
    if os.geteuid() != 0:
        raise DarkListenerError("this command must run as root")


def _safe_absolute_path(value: str, *, label: str) -> Path:
    if not SAFE_PATH.fullmatch(value) or "//" in value:
        raise DarkListenerError(f"{label} must be a safe absolute path")
    path = Path(value)
    if not path.is_absolute() or any(part in {".", ".."} for part in path.parts):
        raise DarkListenerError(f"{label} must be a safe absolute path")
    return path


def _require_root_owned_directory(path: Path, *, label: str, private: bool) -> Path:
    if not path.is_absolute():
        raise DarkListenerError(f"{label} must be absolute")
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise DarkListenerError(f"{label} does not exist") from exc
    disallowed = 0o077 if private else 0o022
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != 0
        or metadata.st_mode & disallowed
        or path.resolve(strict=True) != path
    ):
        qualifier = "root-owned and private" if private else "root-owned and not group/world writable"
        raise DarkListenerError(f"{label} must be {qualifier}")
    return path


def _secure_read_regular_file(path: Path, *, label: str, maximum: int, private: bool) -> bytes:
    try:
        before = path.lstat()
    except OSError as exc:
        raise DarkListenerError(f"{label} does not exist") from exc
    disallowed = 0o077 if private else 0o022
    if (
        not stat.S_ISREG(before.st_mode)
        or stat.S_ISLNK(before.st_mode)
        or before.st_uid != 0
        or before.st_mode & disallowed
        or before.st_nlink != 1
        or before.st_size > maximum
    ):
        qualifier = "root-only" if private else "root-owned and not group/world writable"
        raise DarkListenerError(f"{label} must be a {qualifier} regular file")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise DarkListenerError(f"cannot securely open {label}") from exc
    try:
        after = os.fstat(descriptor)
        if (
            not stat.S_ISREG(after.st_mode)
            or after.st_uid != 0
            or after.st_mode & disallowed
            or after.st_nlink != 1
            or after.st_size > maximum
            or after.st_ino != before.st_ino
            or after.st_dev != before.st_dev
        ):
            raise DarkListenerError(f"{label} changed while being opened")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, min(65536, maximum + 1))
            if not chunk:
                break
            total += len(chunk)
            if total > maximum:
                raise DarkListenerError(f"{label} exceeds its size limit")
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _require_root_owned_regular_file(
    path: Path,
    *,
    label: str,
    private: bool,
    maximum: int = MAX_SITE_BYTES,
) -> Path:
    _secure_read_regular_file(path, label=label, maximum=maximum, private=private)
    if path.resolve(strict=True) != path:
        raise DarkListenerError(f"{label} must not use a symlink")
    return path


def _read_config_values(path: Path) -> dict[str, str]:
    raw = _secure_read_regular_file(path, label="dark-listener config", maximum=MAX_CONFIG_BYTES, private=True)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise DarkListenerError("dark-listener config is not UTF-8") from exc
    values: dict[str, str] = {}
    for number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise DarkListenerError(f"dark-listener config line {number} is not KEY=VALUE")
        key, value = line.split("=", 1)
        if key != key.strip() or value != value.strip() or not key or not value:
            raise DarkListenerError(f"dark-listener config line {number} is malformed")
        if key in values:
            raise DarkListenerError(f"dark-listener config duplicates {key}")
        values[key] = value
    if set(values) != CONFIG_KEYS:
        missing = sorted(CONFIG_KEYS - set(values))
        unexpected = sorted(set(values) - CONFIG_KEYS)
        details: list[str] = []
        if missing:
            details.append("missing " + ", ".join(missing))
        if unexpected:
            details.append("unexpected " + ", ".join(unexpected))
        raise DarkListenerError("dark-listener config keys are invalid: " + "; ".join(details))
    return values


def _require_within(path: Path, root: Path, *, label: str, error: str) -> None:
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise DarkListenerError(error.format(label=label)) from exc


def _validate_site_state(site_path: Path, enabled_path: Path) -> None:
    try:
        site_metadata = site_path.lstat()
    except FileNotFoundError:
        site_metadata = None
    except OSError as exc:
        raise DarkListenerError("cannot inspect dark-listener site path") from exc
    if site_metadata is not None:
        _require_root_owned_regular_file(site_path, label="current dark-listener site", private=False)

    try:
        enabled_metadata = enabled_path.lstat()
    except FileNotFoundError:
        return
    except OSError as exc:
        raise DarkListenerError("cannot inspect dark-listener enabled path") from exc
    if not stat.S_ISLNK(enabled_metadata.st_mode):
        raise DarkListenerError("dark-listener enabled path must be a symlink when it exists")
    try:
        if enabled_path.resolve(strict=True) != site_path.resolve(strict=True):
            raise DarkListenerError("dark-listener enabled path must target the configured site")
    except FileNotFoundError as exc:
        raise DarkListenerError("dark-listener enabled path has a broken target") from exc


def load_dark_listener_config(path: Path) -> DarkListenerConfig:
    values = _read_config_values(path)
    if values["WA_IR_DARK_LISTENER_SERVER_NAME"] != SERVER_NAME:
        raise DarkListenerError("dark-listener server name is not the fixed WA-IR production domain")

    certbot_root = _safe_absolute_path(
        values["WA_IR_DARK_LISTENER_CERTBOT_ROOT"], label="WA_IR_DARK_LISTENER_CERTBOT_ROOT"
    )
    _require_root_owned_directory(certbot_root, label="Certbot root", private=False)
    live_root = certbot_root / "live"
    _require_root_owned_directory(live_root, label="Certbot live root", private=False)
    certbot_lineage = _safe_absolute_path(
        values["WA_IR_DARK_LISTENER_CERTBOT_LINEAGE"], label="WA_IR_DARK_LISTENER_CERTBOT_LINEAGE"
    )
    if certbot_lineage != live_root / SERVER_NAME:
        raise DarkListenerError("Certbot lineage must be the fixed local production lineage")
    _require_root_owned_directory(certbot_lineage, label="Certbot production lineage", private=False)

    tls_root = _safe_absolute_path(values["WA_IR_DARK_LISTENER_TLS_ROOT"], label="WA_IR_DARK_LISTENER_TLS_ROOT")
    _require_root_owned_directory(tls_root, label="WA-IR local TLS root", private=True)
    certificate_path = _safe_absolute_path(
        values["WA_IR_DARK_LISTENER_CERTIFICATE_PATH"], label="WA_IR_DARK_LISTENER_CERTIFICATE_PATH"
    )
    certificate_key_path = _safe_absolute_path(
        values["WA_IR_DARK_LISTENER_CERTIFICATE_KEY_PATH"],
        label="WA_IR_DARK_LISTENER_CERTIFICATE_KEY_PATH",
    )
    if certificate_path != tls_root / CERTIFICATE_NAME or certificate_key_path != tls_root / KEY_NAME:
        raise DarkListenerError("WA-IR TLS destinations must be the fixed local certificate and key paths")
    if certificate_path == certificate_key_path:
        raise DarkListenerError("WA-IR certificate and key paths must differ")
    _validate_optional_destination(certificate_path, label="WA-IR certificate", private=True)
    _validate_optional_destination(certificate_key_path, label="WA-IR certificate key", private=True)

    site_path = _safe_absolute_path(values["WA_IR_DARK_LISTENER_SITE_PATH"], label="WA_IR_DARK_LISTENER_SITE_PATH")
    enabled_path = _safe_absolute_path(
        values["WA_IR_DARK_LISTENER_ENABLED_PATH"], label="WA_IR_DARK_LISTENER_ENABLED_PATH"
    )
    if site_path.name != SITE_NAME or enabled_path.name != SITE_NAME:
        raise DarkListenerError("dark-listener site and enabled paths must use the fixed site name")
    _require_root_owned_directory(site_path.parent, label="dark-listener site directory", private=False)
    _require_root_owned_directory(enabled_path.parent, label="dark-listener enabled directory", private=False)
    if site_path == enabled_path:
        raise DarkListenerError("dark-listener site and enabled paths must differ")
    _validate_site_state(site_path, enabled_path)

    receipt_path = _safe_absolute_path(
        values["WA_IR_DARK_LISTENER_RECEIPT_PATH"], label="WA_IR_DARK_LISTENER_RECEIPT_PATH"
    )
    _require_root_owned_directory(receipt_path.parent, label="dark-listener receipt directory", private=True)
    _validate_optional_destination(receipt_path, label="dark-listener receipt", private=True)

    protected = {certbot_root, certbot_lineage, tls_root, certificate_path, certificate_key_path, site_path, enabled_path}
    if receipt_path in protected:
        raise DarkListenerError("dark-listener receipt path conflicts with a protected path")
    if certbot_root == tls_root or certbot_root in tls_root.parents or tls_root in certbot_root.parents:
        raise DarkListenerError("Certbot source root and WA-IR TLS destination root must remain separate")
    return DarkListenerConfig(
        server_name=SERVER_NAME,
        certbot_root=certbot_root,
        certbot_lineage=certbot_lineage,
        tls_root=tls_root,
        certificate_path=certificate_path,
        certificate_key_path=certificate_key_path,
        site_path=site_path,
        enabled_path=enabled_path,
        receipt_path=receipt_path,
    )


def _validate_optional_destination(path: Path, *, label: str, private: bool) -> None:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return
    except OSError as exc:
        raise DarkListenerError(f"cannot inspect {label}") from exc
    if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        raise DarkListenerError(f"{label} must be absent or a regular file")
    _secure_read_regular_file(path, label=label, maximum=MAX_CERTIFICATE_BYTES, private=private)


def _read_certbot_source(config: DarkListenerConfig, *, filename: str, label: str, private: bool) -> bytes:
    source = config.certbot_lineage / filename
    try:
        source_metadata = source.lstat()
    except OSError as exc:
        raise DarkListenerError(f"{label} does not exist in the configured local Certbot lineage") from exc
    if source_metadata.st_uid != 0:
        raise DarkListenerError(f"{label} source must be root-owned")
    try:
        resolved = source.resolve(strict=True)
        resolved_root = config.certbot_root.resolve(strict=True)
    except OSError as exc:
        raise DarkListenerError(f"cannot resolve {label} source") from exc
    _require_within(
        resolved,
        resolved_root,
        label=label,
        error="{label} source must remain inside the local Certbot root",
    )
    return _secure_read_regular_file(resolved, label=label, maximum=MAX_CERTIFICATE_BYTES, private=private)


def _validate_local_certificate_pair(certificate: bytes, key: bytes) -> None:
    if b"-----BEGIN CERTIFICATE-----" not in certificate or b"-----END CERTIFICATE-----" not in certificate:
        raise DarkListenerError("local Certbot certificate is not a PEM certificate chain")
    if b"-----BEGIN " not in key or b"PRIVATE KEY-----" not in key or b"-----END " not in key:
        raise DarkListenerError("local Certbot key is not a PEM private key")


def _validate_template(template: str) -> None:
    expected = {
        "__SERVER_NAME__": 2,
        "__WA_IR_CERTIFICATE_PATH__": 1,
        "__WA_IR_CERTIFICATE_KEY_PATH__": 1,
    }
    placeholders = PLACEHOLDER.findall(template)
    if set(placeholders) != set(expected) or any(template.count(key) != count for key, count in expected.items()):
        raise DarkListenerError("dark-listener template placeholders are not pinned")
    required = (
        "listen 80;",
        "listen 443 ssl http2;",
        "location = /__standby/health",
        "allow 127.0.0.1;",
        "allow ::1;",
        "deny all;",
        "return 204;",
    )
    if any(value not in template for value in required) or template.count("return 503;") != 2:
        raise DarkListenerError("dark-listener template is not the pinned fenced 503 listener")
    forbidden = (
        "proxy_pass",
        "/api/sync/receive",
        "fastcgi_pass",
        "uwsgi_pass",
        "scgi_pass",
        "__FOREIGN_PUBLIC_IP__",
        "WEBAPP_FI",
        "FI_",
        "/etc/letsencrypt/live/",
    )
    if any(value in template for value in forbidden) or re.search(r"(?m)^\s*upstream\s+", template):
        raise DarkListenerError("dark-listener template contains an upstream, sync path, or foreign source material")


def render_dark_listener(template_path: Path, config: DarkListenerConfig) -> bytes:
    _require_root_owned_regular_file(template_path, label="dark-listener template", private=False)
    try:
        template = _secure_read_regular_file(
            template_path,
            label="dark-listener template",
            maximum=MAX_SITE_BYTES,
            private=False,
        ).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise DarkListenerError("cannot read dark-listener template") from exc
    _validate_template(template)
    rendered = (
        template.replace("__SERVER_NAME__", config.server_name)
        .replace("__WA_IR_CERTIFICATE_PATH__", str(config.certificate_path))
        .replace("__WA_IR_CERTIFICATE_KEY_PATH__", str(config.certificate_key_path))
    )
    if PLACEHOLDER.search(rendered):
        raise DarkListenerError("dark-listener template rendering left an unresolved placeholder")
    return rendered.encode("utf-8")


def _require_absent_or_rendered_dark_site(site_path: Path, rendered: bytes) -> None:
    """Refuse to replace any pre-existing Nginx site other than our dark one."""

    try:
        metadata = site_path.lstat()
    except FileNotFoundError:
        return
    except OSError as exc:
        raise DarkListenerError("cannot inspect current dark-listener site") from exc
    if stat.S_IMODE(metadata.st_mode) != 0o644:
        raise DarkListenerError("existing dark-listener site must be root-owned mode 0644")
    current = _secure_read_regular_file(
        site_path,
        label="current dark-listener site",
        maximum=MAX_SITE_BYTES,
        private=False,
    )
    if current != rendered:
        raise DarkListenerError("refusing to overwrite a non-dark existing listener site")


def _require_nginx_binary(path: Path) -> Path:
    _require_root_owned_regular_file(
        path,
        label="nginx binary",
        private=False,
        maximum=MAX_BINARY_BYTES,
    )
    if path.name != "nginx" or not os.access(path, os.X_OK):
        raise DarkListenerError("nginx binary must be an executable root-owned nginx path")
    return path


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _atomic_write(path: Path, payload: bytes, *, mode: int) -> None:
    descriptor = -1
    temporary: Path | None = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        temporary = Path(temporary_name)
        os.fchmod(descriptor, mode)
        offset = 0
        while offset < len(payload):
            written = os.write(descriptor, payload[offset:])
            if written <= 0:
                raise OSError("short write while staging local listener file")
            offset += written
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    except OSError as exc:
        raise DarkListenerError(f"cannot atomically write {path}") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary is not None and temporary.exists():
            temporary.unlink()


def _snapshot_file(path: Path, *, label: str, private: bool, maximum: int) -> FileSnapshot:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return FileSnapshot(exists=False, payload=None, mode=None)
    except OSError as exc:
        raise DarkListenerError(f"cannot inspect {label}") from exc
    if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        raise DarkListenerError(f"{label} must be a regular file")
    return FileSnapshot(
        exists=True,
        payload=_secure_read_regular_file(path, label=label, maximum=maximum, private=private),
        mode=stat.S_IMODE(metadata.st_mode),
    )


def _remove_created_file(path: Path, *, staged: bytes, label: str, private: bool, maximum: int) -> None:
    try:
        current = _secure_read_regular_file(path, label=label, maximum=maximum, private=private)
    except DarkListenerError as exc:
        raise DarkListenerError(f"cannot safely remove newly created {label}") from exc
    if current != staged:
        raise DarkListenerError(f"cannot safely remove changed {label}")
    try:
        path.unlink()
        _fsync_directory(path.parent)
    except OSError as exc:
        raise DarkListenerError(f"cannot remove newly created {label}") from exc


def _restore_file(
    path: Path,
    snapshot: FileSnapshot,
    *,
    staged: bytes,
    label: str,
    private: bool,
    maximum: int,
) -> None:
    if not snapshot.exists:
        try:
            path.lstat()
        except FileNotFoundError:
            return
        except OSError as exc:
            raise DarkListenerError(f"cannot inspect newly created {label}") from exc
        _remove_created_file(path, staged=staged, label=label, private=private, maximum=maximum)
        return
    if snapshot.payload is None or snapshot.mode is None:
        raise DarkListenerError(f"invalid rollback snapshot for {label}")
    current = _secure_read_regular_file(path, label=label, maximum=maximum, private=private)
    if current != staged:
        raise DarkListenerError(f"cannot safely restore externally changed {label}")
    _atomic_write(path, snapshot.payload, mode=snapshot.mode)


def _ensure_enabled_link(path: Path, *, site_path: Path) -> bool:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        try:
            os.symlink(str(site_path), path)
            _fsync_directory(path.parent)
        except OSError as exc:
            raise DarkListenerError("cannot create dark-listener enabled symlink") from exc
        return True
    except OSError as exc:
        raise DarkListenerError("cannot inspect dark-listener enabled path") from exc
    if not stat.S_ISLNK(metadata.st_mode):
        raise DarkListenerError("dark-listener enabled path must be a symlink")
    try:
        if path.resolve(strict=True) != site_path.resolve(strict=True):
            raise DarkListenerError("dark-listener enabled path must target the configured site")
    except OSError as exc:
        raise DarkListenerError("cannot resolve dark-listener enabled path") from exc
    return False


def _remove_created_link(path: Path, *, site_path: Path) -> None:
    try:
        metadata = path.lstat()
        if not stat.S_ISLNK(metadata.st_mode) or path.resolve(strict=True) != site_path.resolve(strict=True):
            raise DarkListenerError("cannot safely remove changed dark-listener enabled symlink")
        path.unlink()
        _fsync_directory(path.parent)
    except OSError as exc:
        raise DarkListenerError("cannot remove newly created dark-listener enabled symlink") from exc


def _default_command_runner(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            [str(item) for item in command],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=30,
            check=False,
            env={"PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C"},
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise DarkListenerError("cannot execute local Nginx control command") from exc


def _run_nginx(command_runner: CommandRunner, command: Sequence[str], *, label: str) -> None:
    result = command_runner(command)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        if len(detail) > 512:
            detail = detail[:512]
        suffix = f": {detail}" if detail else ""
        raise DarkListenerError(f"{label} failed{suffix}")


def _validate_certbot_hook_environment(config: DarkListenerConfig) -> None:
    if os.environ.get("RENEWED_LINEAGE") != str(config.certbot_lineage):
        raise DarkListenerError("Certbot deploy hook renewed lineage does not match the fixed WA-IR lineage")
    domains = os.environ.get("RENEWED_DOMAINS", "").split()
    if domains != [SERVER_NAME]:
        raise DarkListenerError("Certbot deploy hook renewed domains are not the fixed WA-IR production domain")


def _receipt_payload(
    config: DarkListenerConfig,
    *,
    operation: str,
    rendered: bytes | None,
    certificate: bytes,
    completed_at: datetime,
) -> bytes:
    payload: dict[str, object] = {
        "schema": RECEIPT_SCHEMA,
        "status": "reloaded",
        "operation": operation,
        "server_name": config.server_name,
        "listener_mode": "dark-503" if operation == "install" else "existing-local-listener",
        "certificate_sha256": hashlib.sha256(certificate).hexdigest(),
        "external_route_changed": False,
        "completed_at": completed_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    if rendered is not None:
        payload["site_config_sha256"] = hashlib.sha256(rendered).hexdigest()
    return json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"


def _rollback(
    *,
    config: DarkListenerConfig,
    certificate_snapshot: FileSnapshot,
    key_snapshot: FileSnapshot,
    certificate: bytes,
    key: bytes,
    site_snapshot: FileSnapshot | None,
    rendered: bytes | None,
    link_was_created: bool,
    nginx: Path,
    command_runner: CommandRunner,
    reload_prior_configuration: bool,
) -> str | None:
    failures: list[str] = []
    try:
        if link_was_created:
            _remove_created_link(config.enabled_path, site_path=config.site_path)
    except DarkListenerError as exc:
        failures.append(str(exc))
    try:
        if site_snapshot is not None and rendered is not None:
            _restore_file(
                config.site_path,
                site_snapshot,
                staged=rendered,
                label="dark-listener site",
                private=False,
                maximum=MAX_SITE_BYTES,
            )
    except DarkListenerError as exc:
        failures.append(str(exc))
    try:
        _restore_file(
            config.certificate_path,
            certificate_snapshot,
            staged=certificate,
            label="WA-IR certificate",
            private=True,
            maximum=MAX_CERTIFICATE_BYTES,
        )
    except DarkListenerError as exc:
        failures.append(str(exc))
    try:
        _restore_file(
            config.certificate_key_path,
            key_snapshot,
            staged=key,
            label="WA-IR certificate key",
            private=True,
            maximum=MAX_CERTIFICATE_BYTES,
        )
    except DarkListenerError as exc:
        failures.append(str(exc))
    if not failures and reload_prior_configuration:
        try:
            _run_nginx(command_runner, (str(nginx), "-t"), label="nginx rollback configuration test")
            _run_nginx(command_runner, (str(nginx), "-s", "reload"), label="nginx rollback reload")
        except DarkListenerError as exc:
            failures.append(str(exc))
    return "; ".join(failures) or None


def install_dark_listener(
    *,
    config_path: Path,
    template_path: Path = DEFAULT_TEMPLATE,
    nginx_binary: Path = Path("/usr/sbin/nginx"),
    apply: bool,
    certbot_deploy_hook: bool = False,
    command_runner: CommandRunner = _default_command_runner,
    now: datetime | None = None,
) -> dict[str, object]:
    """Plan or apply only the local WA-IR TLS/dark-listener transaction."""

    config = load_dark_listener_config(config_path)
    if certbot_deploy_hook:
        _validate_certbot_hook_environment(config)
    certificate = _read_certbot_source(
        config,
        filename=CERTIFICATE_NAME,
        label="local Certbot certificate",
        private=False,
    )
    key = _read_certbot_source(
        config,
        filename=KEY_NAME,
        label="local Certbot key",
        private=True,
    )
    _validate_local_certificate_pair(certificate, key)
    rendered = None if certbot_deploy_hook else render_dark_listener(template_path, config)
    if rendered is not None:
        _require_absent_or_rendered_dark_site(config.site_path, rendered)
    nginx = _require_nginx_binary(nginx_binary)
    operation = "certbot-deploy-hook" if certbot_deploy_hook else "install"
    result: dict[str, object] = {
        "schema": RECEIPT_SCHEMA,
        "status": "planned",
        "operation": operation,
        "server_name": config.server_name,
        "listener_mode": "dark-503" if not certbot_deploy_hook else "existing-local-listener",
        "certificate_sha256": hashlib.sha256(certificate).hexdigest(),
        "external_route_changed": False,
    }
    if rendered is not None:
        result["site_config_sha256"] = hashlib.sha256(rendered).hexdigest()
    if not apply:
        return result

    certificate_snapshot = _snapshot_file(
        config.certificate_path,
        label="WA-IR certificate",
        private=True,
        maximum=MAX_CERTIFICATE_BYTES,
    )
    key_snapshot = _snapshot_file(
        config.certificate_key_path,
        label="WA-IR certificate key",
        private=True,
        maximum=MAX_CERTIFICATE_BYTES,
    )
    site_snapshot = (
        _snapshot_file(config.site_path, label="dark-listener site", private=False, maximum=MAX_SITE_BYTES)
        if not certbot_deploy_hook
        else None
    )
    site_was_written = False
    link_was_created = False
    nginx_tested = False
    try:
        _atomic_write(config.certificate_path, certificate, mode=0o600)
        _atomic_write(config.certificate_key_path, key, mode=0o600)
        if not certbot_deploy_hook:
            if rendered is None:
                raise DarkListenerError("dark-listener rendering is missing")
            if site_snapshot is None or not site_snapshot.exists:
                _atomic_write(config.site_path, rendered, mode=0o644)
                site_was_written = True
            link_was_created = _ensure_enabled_link(config.enabled_path, site_path=config.site_path)
        _run_nginx(command_runner, (str(nginx), "-t"), label="nginx configuration test")
        nginx_tested = True
        _run_nginx(command_runner, (str(nginx), "-s", "reload"), label="nginx reload")
        _atomic_write(
            config.receipt_path,
            _receipt_payload(
                config,
                operation=operation,
                rendered=rendered,
                certificate=certificate,
                completed_at=now or datetime.now(timezone.utc),
            ),
            mode=0o600,
        )
    except DarkListenerError as exc:
        rollback_error = _rollback(
            config=config,
            certificate_snapshot=certificate_snapshot,
            key_snapshot=key_snapshot,
            certificate=certificate,
            key=key,
            site_snapshot=site_snapshot if site_was_written else None,
            rendered=rendered,
            link_was_created=link_was_created,
            nginx=nginx,
            command_runner=command_runner,
            reload_prior_configuration=nginx_tested,
        )
        if rollback_error:
            raise DarkListenerError(f"{exc}; rollback failed: {rollback_error}") from exc
        raise

    result["status"] = "reloaded"
    return result


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path, help="Root-only local WA-IR dark-listener config.")
    parser.add_argument("--template", type=Path, default=DEFAULT_TEMPLATE)
    parser.add_argument("--nginx-binary", type=Path, default=Path("/usr/sbin/nginx"))
    parser.add_argument("--apply", action="store_true", help="Required for any local file or Nginx change.")
    parser.add_argument(
        "--certbot-deploy-hook",
        action="store_true",
        help="Require exact Certbot renewal environment and refresh only the local TLS pair.",
    )
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        _require_root()
        if args.certbot_deploy_hook and not args.apply:
            raise DarkListenerError("--certbot-deploy-hook requires --apply")
        result = install_dark_listener(
            config_path=args.config,
            template_path=args.template,
            nginx_binary=args.nginx_binary,
            apply=args.apply,
            certbot_deploy_hook=args.certbot_deploy_hook,
        )
    except DarkListenerError as exc:
        payload = {"status": "error", "error": str(exc), "external_route_changed": False}
        print(json.dumps(payload, ensure_ascii=True, sort_keys=True))
        return 1
    if args.json:
        print(json.dumps(result, ensure_ascii=True, sort_keys=True))
    else:
        print(result["status"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
