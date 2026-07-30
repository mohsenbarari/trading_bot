#!/usr/bin/env python3
"""Safely activate the local WA-IR promoted Nginx listener.

This helper has no Object Storage, DNS, Arvan, or cross-host capability.  It
only replaces the already-enabled local Nginx site after validating a
root-only configuration, the local TLS files, the immutable 2c08 *application*
static release, and the pinned loopback-only listener template.  It executes
from a separate immutable control release.  Nginx configuration validation
and reload must both succeed before it emits its local receipt.
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
import sys
import tempfile
from typing import Callable, Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.manage_webapp_ir_release_provenance import (  # noqa: E402
    ReleaseProvenanceError,
    load_installed_release_receipt,
)
from scripts.install_webapp_ir_static_assets import (  # noqa: E402
    StaticAssetInstallError,
    verify_installed_static_assets,
)

DEFAULT_TEMPLATE = REPO_ROOT / "deploy/production/nginx-webapp-ir-promoted-2c08-https.conf.template"
RELEASE_SHA = "2c08da14bfa0ef94d9c788e478d30ddc3f31a3c5"
SERVER_NAME = "coin.gold-trade.ir"
LOOPBACK_UPSTREAM = "http://127.0.0.1:18000"
RECEIPT_SCHEMA = "gold-trade-wa-ir-promoted-listener-activation-v1"
MAX_CONFIG_BYTES = 16 * 1024
SAFE_PATH = re.compile(r"^/[A-Za-z0-9._/-]+$")
PLACEHOLDER = re.compile(r"__[A-Z0-9_]+__")

CONFIG_KEYS = frozenset(
    {
        "WA_IR_LISTENER_SERVER_NAME",
        "WA_IR_LISTENER_APPLICATION_RELEASE_ROOT",
        "WA_IR_LISTENER_RELEASE_PROVENANCE_RECEIPT",
        "WA_IR_LISTENER_STATIC_RELEASE_ROOT",
        "WA_IR_LISTENER_STATIC_RECEIPT",
        "WA_IR_LISTENER_TLS_ROOT",
        "WA_IR_LISTENER_CERTIFICATE_PATH",
        "WA_IR_LISTENER_CERTIFICATE_KEY_PATH",
        "WA_IR_LISTENER_SITE_PATH",
        "WA_IR_LISTENER_ENABLED_PATH",
        "WA_IR_LISTENER_RECEIPT_PATH",
    }
)


class ListenerActivationError(RuntimeError):
    """Raised when local listener activation cannot safely continue."""


CommandRunner = Callable[[Sequence[str]], subprocess.CompletedProcess[str]]


@dataclass(frozen=True)
class ListenerConfig:
    server_name: str
    release_root: Path
    release_provenance_receipt: Path
    static_root: Path
    static_receipt: Path
    tls_root: Path
    certificate_path: Path
    certificate_key_path: Path
    site_path: Path
    enabled_path: Path
    receipt_path: Path


def _require_root() -> None:
    if os.geteuid() != 0:
        raise ListenerActivationError("this command must run as root")


def _safe_absolute_path(value: str, *, label: str) -> Path:
    if not SAFE_PATH.fullmatch(value):
        raise ListenerActivationError(f"{label} must be a safe absolute path")
    return Path(value)


def _secure_read_regular_file(path: Path, *, label: str, maximum: int, private: bool) -> bytes:
    try:
        before = path.lstat()
    except OSError as exc:
        raise ListenerActivationError(f"{label} does not exist") from exc
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
        raise ListenerActivationError(f"{label} must be a {qualifier} regular file")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ListenerActivationError(f"cannot securely open {label}") from exc
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
            raise ListenerActivationError(f"{label} changed while being opened")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, maximum + 1)
            if not chunk:
                break
            chunks.append(chunk)
            if sum(len(item) for item in chunks) > maximum:
                raise ListenerActivationError(f"{label} exceeds its size limit")
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _secure_read_private_file(path: Path, *, label: str, maximum: int) -> bytes:
    return _secure_read_regular_file(path, label=label, maximum=maximum, private=True)


def _require_root_owned_directory(path: Path, *, label: str, private: bool) -> Path:
    if not path.is_absolute():
        raise ListenerActivationError(f"{label} must be absolute")
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise ListenerActivationError(f"{label} does not exist") from exc
    disallowed = 0o077 if private else 0o022
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != 0
        or metadata.st_mode & disallowed
        or path.resolve(strict=True) != path
    ):
        qualifier = "root-owned and private" if private else "root-owned and not group/world writable"
        raise ListenerActivationError(f"{label} must be {qualifier}")
    return path


def _require_root_owned_regular_file(path: Path, *, label: str, private: bool) -> Path:
    if not path.is_absolute():
        raise ListenerActivationError(f"{label} must be absolute")
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise ListenerActivationError(f"{label} does not exist") from exc
    disallowed = 0o077 if private else 0o022
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != 0
        or metadata.st_mode & disallowed
        or metadata.st_nlink != 1
        or path.resolve(strict=True) != path
    ):
        qualifier = "root-owned and private" if private else "root-owned and not group/world writable"
        raise ListenerActivationError(f"{label} must be a {qualifier} regular file")
    return path


def _require_within(path: Path, root: Path, *, label: str) -> Path:
    resolved_path = path.resolve(strict=True)
    resolved_root = root.resolve(strict=True)
    try:
        resolved_path.relative_to(resolved_root)
    except ValueError as exc:
        raise ListenerActivationError(f"{label} must remain under WA-IR local TLS root") from exc
    if resolved_path != path:
        raise ListenerActivationError(f"{label} must not use a symlink")
    return path


def _read_config_values(path: Path) -> dict[str, str]:
    raw = _secure_read_private_file(path, label="listener config", maximum=MAX_CONFIG_BYTES)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ListenerActivationError("listener config is not UTF-8") from exc
    values: dict[str, str] = {}
    for number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ListenerActivationError(f"listener config line {number} is not KEY=VALUE")
        key, value = line.split("=", 1)
        if key != key.strip() or value != value.strip() or not key or not value:
            raise ListenerActivationError(f"listener config line {number} is malformed")
        if key in values:
            raise ListenerActivationError(f"listener config duplicates {key}")
        values[key] = value
    if set(values) != CONFIG_KEYS:
        missing = sorted(CONFIG_KEYS - set(values))
        unexpected = sorted(set(values) - CONFIG_KEYS)
        details: list[str] = []
        if missing:
            details.append("missing " + ", ".join(missing))
        if unexpected:
            details.append("unexpected " + ", ".join(unexpected))
        raise ListenerActivationError("listener config keys are invalid: " + "; ".join(details))
    return values


def _verify_application_release_binding(*, receipt_path: Path, release_root: Path) -> dict[str, object]:
    """Require this local listener to use the receipt-bound application root."""

    try:
        installed = load_installed_release_receipt(receipt_path)
    except ReleaseProvenanceError as exc:
        raise ListenerActivationError(f"listener release provenance receipt is invalid: {exc}") from exc
    application = installed["application"]
    control = installed["control"]
    if (
        application["release_sha"] != RELEASE_SHA
        or application["release_root"] != str(release_root)
    ):
        raise ListenerActivationError("listener release provenance receipt does not bind this application release root")
    try:
        runtime_control_root = REPO_ROOT.resolve(strict=True)
    except OSError as exc:
        raise ListenerActivationError("listener control release root does not exist") from exc
    if control["release_root"] != str(runtime_control_root):
        raise ListenerActivationError("listener must execute from the receipt-bound control release root")
    return installed


def load_listener_config(path: Path) -> ListenerConfig:
    values = _read_config_values(path)
    if values["WA_IR_LISTENER_SERVER_NAME"] != SERVER_NAME:
        raise ListenerActivationError("listener server name is not the fixed WA-IR production domain")
    release_root = _safe_absolute_path(
        values["WA_IR_LISTENER_APPLICATION_RELEASE_ROOT"],
        label="WA_IR_LISTENER_APPLICATION_RELEASE_ROOT",
    )
    if release_root.name != RELEASE_SHA:
        raise ListenerActivationError("listener release root is not the exact 2c08 release")
    _require_root_owned_directory(release_root, label="listener release root", private=False)
    release_provenance_receipt = _safe_absolute_path(
        values["WA_IR_LISTENER_RELEASE_PROVENANCE_RECEIPT"],
        label="WA_IR_LISTENER_RELEASE_PROVENANCE_RECEIPT",
    )
    _require_root_owned_regular_file(
        release_provenance_receipt,
        label="listener release provenance receipt",
        private=True,
    )
    installed = _verify_application_release_binding(
        receipt_path=release_provenance_receipt,
        release_root=release_root,
    )
    static_root = _safe_absolute_path(
        values["WA_IR_LISTENER_STATIC_RELEASE_ROOT"],
        label="WA_IR_LISTENER_STATIC_RELEASE_ROOT",
    )
    static_receipt = _safe_absolute_path(
        values["WA_IR_LISTENER_STATIC_RECEIPT"],
        label="WA_IR_LISTENER_STATIC_RECEIPT",
    )
    _require_root_owned_regular_file(static_receipt, label="listener static receipt", private=True)
    try:
        static_verified = verify_installed_static_assets(
            receipt_path=static_receipt,
            expected_application_release_sha=release_root.name,
            pinned_controller_public_key_base64=str(
                installed["bootstrap_provenance"]["webapp_fi_controller_authorization_public_key_base64"]
            ),
        )
    except (KeyError, StaticAssetInstallError) as exc:
        raise ListenerActivationError(f"listener static receipt is invalid: {exc}") from exc
    if static_verified["static_root"] != str(static_root):
        raise ListenerActivationError("listener static receipt does not bind the configured static release root")
    _require_root_owned_directory(static_root, label="listener static release root", private=False)
    _require_root_owned_regular_file(static_root / "index.html", label="listener static index", private=False)
    try:
        static_root.relative_to(release_root)
    except ValueError:
        pass
    else:
        raise ListenerActivationError("listener static release root must remain outside the Git application release")
    try:
        release_root.relative_to(static_root)
    except ValueError:
        pass
    else:
        raise ListenerActivationError("listener static release root must not contain the Git application release")

    tls_root = _safe_absolute_path(values["WA_IR_LISTENER_TLS_ROOT"], label="WA_IR_LISTENER_TLS_ROOT")
    _require_root_owned_directory(tls_root, label="WA-IR local TLS root", private=True)
    certificate_path = _safe_absolute_path(
        values["WA_IR_LISTENER_CERTIFICATE_PATH"], label="WA_IR_LISTENER_CERTIFICATE_PATH"
    )
    certificate_key_path = _safe_absolute_path(
        values["WA_IR_LISTENER_CERTIFICATE_KEY_PATH"],
        label="WA_IR_LISTENER_CERTIFICATE_KEY_PATH",
    )
    _require_within(certificate_path, tls_root, label="WA-IR certificate path")
    _require_within(certificate_key_path, tls_root, label="WA-IR certificate key path")
    if certificate_path == certificate_key_path:
        raise ListenerActivationError("WA-IR certificate and key paths must differ")
    _require_root_owned_regular_file(certificate_path, label="WA-IR certificate", private=True)
    _require_root_owned_regular_file(certificate_key_path, label="WA-IR certificate key", private=True)

    site_path = _safe_absolute_path(values["WA_IR_LISTENER_SITE_PATH"], label="WA_IR_LISTENER_SITE_PATH")
    _require_root_owned_directory(site_path.parent, label="listener site directory", private=False)
    _require_root_owned_regular_file(site_path, label="current listener site", private=False)
    enabled_path = _safe_absolute_path(
        values["WA_IR_LISTENER_ENABLED_PATH"], label="WA_IR_LISTENER_ENABLED_PATH"
    )
    _require_root_owned_directory(enabled_path.parent, label="listener enabled directory", private=False)
    try:
        enabled_metadata = enabled_path.lstat()
    except OSError as exc:
        raise ListenerActivationError("listener enabled path does not exist") from exc
    if not stat.S_ISLNK(enabled_metadata.st_mode):
        raise ListenerActivationError("listener enabled path must be a symlink to the current listener site")
    try:
        if enabled_path.resolve(strict=True) != site_path.resolve(strict=True):
            raise ListenerActivationError("listener enabled path does not target the current listener site")
    except OSError as exc:
        raise ListenerActivationError("cannot resolve listener enabled path") from exc

    receipt_path = _safe_absolute_path(
        values["WA_IR_LISTENER_RECEIPT_PATH"], label="WA_IR_LISTENER_RECEIPT_PATH"
    )
    _require_root_owned_directory(receipt_path.parent, label="listener receipt directory", private=True)
    if receipt_path.exists():
        _require_root_owned_regular_file(receipt_path, label="listener receipt", private=True)

    protected = {
        path.resolve(strict=True)
        for path in (release_root, release_provenance_receipt, static_root, static_receipt, tls_root, site_path, enabled_path)
    }
    if receipt_path in protected:
        raise ListenerActivationError("listener receipt path conflicts with a protected listener path")
    return ListenerConfig(
        server_name=values["WA_IR_LISTENER_SERVER_NAME"],
        release_root=release_root,
        release_provenance_receipt=release_provenance_receipt,
        static_root=static_root,
        static_receipt=static_receipt,
        tls_root=tls_root,
        certificate_path=certificate_path,
        certificate_key_path=certificate_key_path,
        site_path=site_path,
        enabled_path=enabled_path,
        receipt_path=receipt_path,
    )


def _validate_template(template: str) -> None:
    expected = {
        "__SERVER_NAME__": 2,
        "__WA_IR_CERTIFICATE_PATH__": 1,
        "__WA_IR_CERTIFICATE_KEY_PATH__": 1,
        "__WA_IR_STATIC_RELEASE_ROOT__": 1,
    }
    placeholders = PLACEHOLDER.findall(template)
    if set(placeholders) != set(expected) or any(template.count(key) != count for key, count in expected.items()):
        raise ListenerActivationError("listener template placeholders are not pinned")
    if "root __WA_IR_STATIC_RELEASE_ROOT__;" not in template:
        raise ListenerActivationError("listener template does not use the immutable static release root")
    if "location = /api/sync/receive" not in template:
        raise ListenerActivationError("listener template does not explicitly fence direct sync")
    direct_sync = template.split("location = /api/sync/receive", 1)[1].split("\n    }", 1)[0]
    if "return 404;" not in direct_sync or "proxy_pass" in direct_sync:
        raise ListenerActivationError("listener template direct-sync fence is unsafe")
    backends = re.findall(r"(?m)^\s*proxy_pass\s+(https?://[^;]+);", template)
    if backends != [LOOPBACK_UPSTREAM] * 3 or re.search(r"(?m)^\s*upstream\s+", template):
        raise ListenerActivationError("listener template has an unsafe upstream")
    forbidden = ("__FOREIGN_PUBLIC_IP__", "WEBAPP_FI", "FI_", "/etc/letsencrypt/live/")
    if any(value in template for value in forbidden):
        raise ListenerActivationError("listener template contains non-local TLS or source-site material")


def render_listener_config(template_path: Path, config: ListenerConfig) -> bytes:
    _require_root_owned_regular_file(template_path, label="listener template", private=False)
    try:
        template = _secure_read_regular_file(
            template_path,
            label="listener template",
            maximum=1024 * 1024,
            private=False,
        ).decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ListenerActivationError("cannot read listener template") from exc
    _validate_template(template)
    rendered = (
        template.replace("__SERVER_NAME__", config.server_name)
        .replace("__WA_IR_CERTIFICATE_PATH__", str(config.certificate_path))
        .replace("__WA_IR_CERTIFICATE_KEY_PATH__", str(config.certificate_key_path))
        .replace("__WA_IR_STATIC_RELEASE_ROOT__", str(config.static_root))
    )
    if PLACEHOLDER.search(rendered):
        raise ListenerActivationError("listener template rendering left an unresolved placeholder")
    return rendered.encode("utf-8")


def _require_nginx_binary(path: Path) -> Path:
    _require_root_owned_regular_file(path, label="nginx binary", private=False)
    if path.name != "nginx" or not os.access(path, os.X_OK):
        raise ListenerActivationError("nginx binary must be an executable root-owned nginx path")
    return path


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
                raise OSError("short write while staging listener file")
            offset += written
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        os.replace(temporary, path)
        directory_descriptor = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    except OSError as exc:
        raise ListenerActivationError(f"cannot atomically write {path}") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary is not None and temporary.exists():
            temporary.unlink()


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
        raise ListenerActivationError("cannot execute local nginx control command") from exc


def _run_nginx(command_runner: CommandRunner, command: Sequence[str], *, label: str) -> None:
    result = command_runner(command)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        if len(detail) > 512:
            detail = detail[:512]
        suffix = f": {detail}" if detail else ""
        raise ListenerActivationError(f"{label} failed{suffix}")


def _receipt_payload(
    config: ListenerConfig,
    *,
    rendered: bytes,
    activated_at: datetime,
) -> bytes:
    payload = {
        "schema": RECEIPT_SCHEMA,
        "status": "reloaded",
        "release_sha": RELEASE_SHA,
        "server_name": config.server_name,
        "loopback_upstream": LOOPBACK_UPSTREAM,
        "site_config_sha256": hashlib.sha256(rendered).hexdigest(),
        "certificate_sha256": hashlib.sha256(
            _secure_read_private_file(
                config.certificate_path,
                label="WA-IR certificate",
                maximum=1024 * 1024,
            )
        ).hexdigest(),
        "activated_at": activated_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    return json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"


def activate_listener(
    *,
    config_path: Path,
    template_path: Path = DEFAULT_TEMPLATE,
    nginx_binary: Path = Path("/usr/sbin/nginx"),
    apply: bool,
    command_runner: CommandRunner = _default_command_runner,
    now: datetime | None = None,
) -> dict[str, object]:
    """Validate/render the listener, and on apply test then reload it locally.

    The only mutable files are the existing enabled site, restored on an Nginx
    failure, and the canonical root-only local receipt after a successful
    reload.  This function has no route or remote-control capability.
    """

    config = load_listener_config(config_path)
    rendered = render_listener_config(template_path, config)
    nginx = _require_nginx_binary(nginx_binary)
    result: dict[str, object] = {
        "schema": RECEIPT_SCHEMA,
        "status": "planned",
        "release_sha": RELEASE_SHA,
        "server_name": config.server_name,
        "loopback_upstream": LOOPBACK_UPSTREAM,
        "site_config_sha256": hashlib.sha256(rendered).hexdigest(),
        "site_path": str(config.site_path),
        "receipt_path": str(config.receipt_path),
        "external_route_changed": False,
    }
    if not apply:
        return result

    original_mode = stat.S_IMODE(config.site_path.lstat().st_mode)
    original = _secure_read_regular_file(
        config.site_path,
        label="current listener site",
        maximum=1024 * 1024,
        private=False,
    )
    tested = False
    _atomic_write(config.site_path, rendered, mode=0o600)
    try:
        _run_nginx(command_runner, (str(nginx), "-t"), label="nginx configuration test")
        tested = True
        _run_nginx(command_runner, (str(nginx), "-s", "reload"), label="nginx reload")
    except ListenerActivationError as exc:
        _atomic_write(config.site_path, original, mode=original_mode)
        if tested:
            try:
                _run_nginx(command_runner, (str(nginx), "-t"), label="nginx rollback configuration test")
                _run_nginx(command_runner, (str(nginx), "-s", "reload"), label="nginx rollback reload")
            except ListenerActivationError as rollback_error:
                raise ListenerActivationError(f"{exc}; rollback failed: {rollback_error}") from rollback_error
        raise

    _atomic_write(
        config.receipt_path,
        _receipt_payload(config, rendered=rendered, activated_at=now or datetime.now(timezone.utc)),
        mode=0o600,
    )
    result["status"] = "reloaded"
    return result


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path, help="Root-only local WA-IR listener config.")
    parser.add_argument("--template", type=Path, default=DEFAULT_TEMPLATE)
    parser.add_argument("--nginx-binary", type=Path, default=Path("/usr/sbin/nginx"))
    parser.add_argument("--apply", action="store_true", help="Required to replace/test/reload the local listener.")
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        _require_root()
        result = activate_listener(
            config_path=args.config,
            template_path=args.template,
            nginx_binary=args.nginx_binary,
            apply=args.apply,
        )
    except ListenerActivationError as exc:
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
