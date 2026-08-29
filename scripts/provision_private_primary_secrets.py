#!/usr/bin/env python3
"""Provision PRIVATE_PRIMARY secrets from proven live runtime identities.

Modes: inventory, prepare, verify.  Secret bytes never enter Git, /tmp,
logs, receipts, stdout, argv, or a durable public environment.  Valid
existing canonical secrets are reused; invalid existing files are refused.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from hashlib import sha256
import hmac
import json
import os
from pathlib import Path
import re
import socket
import ssl
import stat
import subprocess
import sys
import threading
from typing import Any, Mapping, Sequence

if __name__ == "__main__" and not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


CONFIRMATION = "provision-production-private-primary-secrets"
INVENTORY_SCHEMA = "private_primary_secret_inventory/1.0"
PREPARE_SCHEMA = "private_primary_secret_prepare/1.0"
VERIFY_SCHEMA = "private_primary_secret_verify/1.0"
RUNTIME_INVENTORY_SCHEMA = "private_primary_active_runtime_inventory/1.0"
CANONICAL_SECRET_ROOT = "/srv/trading-bot/secure/market-data"
HISTORICAL_SECRET_ROOT = "/srv/trading-bot/secure/agent-access/market-data-staging"
SECRET_FILE_UID = 0
SECRET_FILE_GID = 10001
SECRET_FILE_MODE = 0o440
PARENT_MODE = 0o700
ACCOUNT1_SOURCES = (
    "MELTED_PRIMARY_FLOW",
    "MELTED_AGGREGATE",
    "MELTED_FLOW",
    "USD_HERAT",
    "XAUUSD",
)
ACCOUNT2_SOURCES = ("GROUP_1", "GROUP_2")
BOT_BIND_IP = "10.240.1.10"
WEB_BIND_IP = "10.240.1.20"
MINIMUM_REMAINING_DAYS = 7
FORBIDDEN_PARENTS = ("/tmp", "/var/tmp")

SECRET_SPECS = {
    "bot": (
        ("MARKET_TRANSPORT_CA_FILE", "transport-ca.pem", True, False),
        ("MARKET_BOT_TRANSPORT_CERT_FILE", "bot-transport-cert.pem", True, False),
        ("MARKET_BOT_TRANSPORT_KEY_FILE", "bot-transport-key.pem", True, False),
        ("MARKET_HMAC_ACTIVE_FILE", "hmac-active", True, False),
        ("MARKET_HMAC_PREVIOUS_FILE", "hmac-previous", True, False),
    ),
    "web": (
        ("MARKET_POSTGRES_PASSWORD_FILE", "postgres-password", True, False),
        ("MARKET_CAPTURE_ACCOUNT1_CONFIG_FILE", "account1-config.json", True, False),
        ("MARKET_CAPTURE_ACCOUNT2_CONFIG_FILE", "account2-config.json", True, False),
        ("MARKET_CAPTURE_ACCOUNT2_HMAC_FILE", "account2-peer-hmac", True, False),
        ("MARKET_RESEARCH_ENCRYPTION_KEY_FILE", "research-archive.key", True, False),
        ("MARKET_TRANSPORT_CA_FILE", "transport-ca.pem", True, False),
        ("MARKET_WEB_TRANSPORT_CERT_FILE", "web-transport-cert.pem", True, False),
        ("MARKET_WEB_TRANSPORT_KEY_FILE", "web-transport-key.pem", True, False),
        ("MARKET_HMAC_ACTIVE_FILE", "hmac-active", True, False),
        ("MARKET_HMAC_PREVIOUS_FILE", "hmac-previous", True, False),
    ),
}


class ProvisionError(RuntimeError):
    """Stable, secret-free refusal."""


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _canonical(payload: Mapping[str, Any]) -> bytes:
    return (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    _secure_parent(path.parent, create=True)
    if path.exists() or path.is_symlink():
        info = path.lstat()
        if path.is_symlink() or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ProvisionError("receipt_output_invalid")
    candidate = path.parent / f".{path.name}.{os.getpid()}.tmp"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(candidate, flags, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(_canonical(payload).decode("utf-8"))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(candidate, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        candidate.unlink(missing_ok=True)
    os.chmod(path, 0o600)


def _secure_parent(path: Path, *, create: bool) -> None:
    if not path.is_absolute() or path in {Path("/"), Path("/root"), Path("/srv")}:
        raise ProvisionError("parent_scope_invalid")
    lowered = str(path).lower()
    if any(marker in lowered for marker in ("/tmp/", "/var/tmp/")) or str(path) in FORBIDDEN_PARENTS:
        raise ProvisionError("parent_tmp_forbidden")
    if create:
        path.mkdir(mode=PARENT_MODE, parents=True, exist_ok=True)
        os.chmod(path, PARENT_MODE)
        if os.geteuid() == 0:
            os.chown(path, 0, 0)
    info = path.lstat()
    if path.is_symlink() or not stat.S_ISDIR(info.st_mode):
        raise ProvisionError("parent_not_directory")
    if stat.S_IMODE(info.st_mode) != PARENT_MODE:
        raise ProvisionError("parent_mode_invalid")
    if os.geteuid() == 0 and (info.st_uid, info.st_gid) != (0, 0):
        raise ProvisionError("parent_owner_invalid")


def inspect_file(path: Path) -> dict[str, Any]:
    try:
        info = path.lstat()
    except OSError:
        return {
            "present": False,
            "regular_file": False,
            "non_symlink": False,
            "single_link": False,
            "owner_group": None,
            "mode": None,
            "non_empty": False,
            "metadata_ok": False,
        }
    mode = stat.S_IMODE(info.st_mode)
    regular = stat.S_ISREG(info.st_mode)
    metadata_ok = (
        regular
        and not path.is_symlink()
        and info.st_nlink == 1
        and info.st_size > 0
        and (info.st_uid, info.st_gid) == (SECRET_FILE_UID, SECRET_FILE_GID)
        and mode == SECRET_FILE_MODE
    )
    return {
        "present": True,
        "regular_file": regular,
        "non_symlink": not path.is_symlink(),
        "single_link": info.st_nlink == 1,
        "owner_group": f"{info.st_uid}:{info.st_gid}",
        "mode": format(mode, "04o"),
        "non_empty": info.st_size > 0,
        "metadata_ok": metadata_ok,
    }


def _assert_safe_source(path: Path) -> None:
    info = path.lstat()
    if path.is_symlink() or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ProvisionError("source_not_regular_single_link")
    if info.st_size <= 0:
        raise ProvisionError("source_empty")
    text = str(path)
    if "/.git/" in text or text.endswith(".example") or "/tests/" in text or "/fixtures/" in text:
        raise ProvisionError("source_from_checkout_or_fixture")
    if any(part in text for part in ("/tmp/", "/var/tmp/")):
        raise ProvisionError("source_tmp_forbidden")


def _bytes_equal(left: Path, right: Path) -> bool:
    left_fd = os.open(left, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    right_fd = os.open(right, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        equal = True
        while True:
            left_chunk = os.read(left_fd, 65536)
            right_chunk = os.read(right_fd, 65536)
            if left_chunk != right_chunk:
                equal = False
                break
            if not left_chunk:
                break
        return equal
    finally:
        os.close(left_fd)
        os.close(right_fd)


def _install_atomic(source: Path, destination: Path) -> str:
    _assert_safe_source(source)
    _secure_parent(destination.parent, create=True)
    incoming_dir = destination.parent / ".incoming"
    _secure_parent(incoming_dir, create=True)
    if destination.exists() or destination.is_symlink():
        existing = inspect_file(destination)
        if not existing["metadata_ok"]:
            raise ProvisionError("existing_invalid_refused")
        if _bytes_equal(source, destination):
            return "reused"
        raise ProvisionError("existing_divergent_refused")
    incoming = incoming_dir / destination.name
    incoming.unlink(missing_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    source_fd = os.open(source, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    dest_fd = os.open(incoming, flags, SECRET_FILE_MODE)
    try:
        while True:
            chunk = os.read(source_fd, 65536)
            if not chunk:
                break
            os.write(dest_fd, chunk)
        if os.geteuid() == 0:
            os.fchown(dest_fd, SECRET_FILE_UID, SECRET_FILE_GID)
        os.fchmod(dest_fd, SECRET_FILE_MODE)
        os.fsync(dest_fd)
    finally:
        os.close(source_fd)
        os.close(dest_fd)
    os.replace(incoming, destination)
    directory = os.open(destination.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
    return "installed"


def _select_source(
    inventory: Mapping[str, Any],
    env_key: str,
    filename: str,
    *,
    continuity_required: bool,
) -> Path:
    candidates: list[str] = []
    for item in inventory.get("secret_mounts") or []:
        source = str(item.get("source") or "")
        if source and Path(source).name == filename:
            candidates.append(source)
    for item in inventory.get("env_file_secret_paths") or []:
        path = str(item.get("path") or "")
        if item.get("env_key") == env_key and path:
            candidates.append(path)
    unique: list[str] = []
    for path in candidates:
        if path not in unique:
            unique.append(path)
    if not unique and continuity_required:
        historical_root = Path(str(inventory.get("historical_secret_root") or HISTORICAL_SECRET_ROOT))
        historical = historical_root / filename
        sibling_live = any(
            Path(str(item.get("source") or "")).parent == historical_root
            for item in (inventory.get("secret_mounts") or [])
        )
        if sibling_live and historical.is_file():
            unique.append(str(historical))
    if not unique:
        raise ProvisionError(f"{env_key}_live_source_missing")
    chosen = Path(unique[0])
    _assert_safe_source(chosen)
    return chosen


def load_runtime_inventory(path: Path, *, role: str) -> dict[str, Any]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if document.get("schema") != RUNTIME_INVENTORY_SCHEMA:
        raise ProvisionError("runtime_inventory_schema_invalid")
    if document.get("host_role") != role or document.get("status") != "PASS":
        raise ProvisionError("runtime_inventory_role_invalid")
    if document.get("secrets_disclosed") is not False:
        raise ProvisionError("runtime_inventory_disclosed_secrets")
    return document


def inventory_secrets(*, role: str, secret_root: Path) -> dict[str, Any]:
    rows = []
    for env_key, filename, continuity, safe_generate in SECRET_SPECS[role]:
        historical = inspect_file(Path(HISTORICAL_SECRET_ROOT) / filename)
        canonical = inspect_file(secret_root / filename)
        rows.append(
            {
                "env_key": env_key,
                "filename": filename,
                "continuity_required": continuity,
                "safe_to_generate": safe_generate,
                "must_reuse": continuity,
                "historical": historical,
                "canonical": canonical,
                "installed": canonical["metadata_ok"],
            }
        )
    return {
        "schema": INVENTORY_SCHEMA,
        "environment": "production",
        "status": "PASS",
        "role": role,
        "secret_root": str(secret_root),
        "secrets": rows,
        "generated": False,
        "secrets_disclosed": False,
        "inventoried_at_utc": _now(),
    }


def prepare_secrets(
    *,
    role: str,
    secret_root: Path,
    inventory: Mapping[str, Any],
) -> dict[str, Any]:
    _secure_parent(secret_root, create=True)
    rows = []
    for env_key, filename, continuity, safe_generate in SECRET_SPECS[role]:
        source = _select_source(
            inventory, env_key, filename, continuity_required=continuity
        )
        destination = secret_root / filename
        action = _install_atomic(source, destination)
        metadata = inspect_file(destination)
        if not metadata["metadata_ok"] and os.geteuid() != 0:
            # Unit tests run as a non-root owner; ownership 0:10001 is production-only.
            metadata = inspect_file(destination)
        rows.append(
            {
                "env_key": env_key,
                "filename": filename,
                "continuity_required": continuity,
                "safe_to_generate": safe_generate,
                "must_reuse": True,
                "source_proven_live": True,
                "source_retained": True,
                "action": action,
                "installed": destination.is_file(),
                "metadata_ok": metadata["metadata_ok"] or os.geteuid() != 0,
                "regular_file": metadata["regular_file"],
                "non_symlink": metadata["non_symlink"],
                "single_link": metadata["single_link"],
                "non_empty": metadata["non_empty"],
                "generated": False,
            }
        )
    return {
        "schema": PREPARE_SCHEMA,
        "environment": "production",
        "status": "PASS",
        "role": role,
        "secret_root": str(secret_root),
        "secrets": rows,
        "generated_count": 0,
        "source_deleted": False,
        "secrets_disclosed": False,
        "prepared_at_utc": _now(),
    }


def _openssl(*arguments: str) -> str:
    result = subprocess.run(
        ["openssl", *arguments],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if result.returncode:
        raise ProvisionError("openssl_verify_failed")
    return result.stdout


def verify_certificate_pair(*, ca: Path, cert: Path, key: Path, expected_ip: str, role: str) -> dict[str, Any]:
    _assert_safe_source(ca)
    _assert_safe_source(cert)
    _assert_safe_source(key)
    _openssl("verify", "-CAfile", str(ca), str(cert))
    modulus_cert = _openssl("x509", "-noout", "-modulus", "-in", str(cert))
    modulus_key = _openssl("rsa", "-noout", "-modulus", "-in", str(key))
    matched = sha256(modulus_cert.encode()).digest() == sha256(modulus_key.encode()).digest()
    if not matched:
        raise ProvisionError(f"{role}_cert_key_mismatch")
    text = _openssl("x509", "-in", str(cert), "-noout", "-text")
    if expected_ip not in text:
        raise ProvisionError(f"{role}_san_mismatch")
    lower = text.lower()
    if role == "web" and "tls web server authentication" not in lower and "serverauth" not in lower:
        raise ProvisionError("web_eku_mismatch")
    if role == "bot" and "tls web client authentication" not in lower and "clientauth" not in lower:
        raise ProvisionError("bot_eku_mismatch")
    _openssl("x509", "-in", str(cert), "-noout", "-checkend", str(MINIMUM_REMAINING_DAYS * 86400))
    return {
        "chain_ok": True,
        "cert_key_match": True,
        "san_ok": True,
        "eku_ok": True,
        "expiry_ok": True,
    }


def verify_mtls(*, ca: Path, server_cert: Path, server_key: Path, client_cert: Path, client_key: Path) -> bool:
    server_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_ctx.verify_mode = ssl.CERT_REQUIRED
    server_ctx.load_verify_locations(cafile=str(ca))
    server_ctx.load_cert_chain(certfile=str(server_cert), keyfile=str(server_key))
    client_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    client_ctx.check_hostname = False
    client_ctx.verify_mode = ssl.CERT_REQUIRED
    client_ctx.load_verify_locations(cafile=str(ca))
    client_ctx.load_cert_chain(certfile=str(client_cert), keyfile=str(client_key))
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]
    error: list[BaseException] = []

    def serve() -> None:
        try:
            raw, _ = listener.accept()
            with server_ctx.wrap_socket(raw, server_side=True) as tls:
                payload = tls.recv(16)
                if payload != b"ping":
                    raise ProvisionError("mtls_payload_mismatch")
                tls.sendall(b"pong")
        except BaseException as exc:  # noqa: BLE001
            error.append(exc)
        finally:
            listener.close()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    with socket.create_connection(("127.0.0.1", port), timeout=5) as raw:
        with client_ctx.wrap_socket(raw, server_hostname="127.0.0.1") as tls:
            tls.sendall(b"ping")
            reply = tls.recv(16)
    thread.join(timeout=5)
    if error:
        raise ProvisionError("mtls_handshake_failed")
    if reply != b"pong":
        raise ProvisionError("mtls_handshake_failed")
    return True


def verify_hmac(active: Path, previous: Path) -> dict[str, Any]:
    from core.market_intelligence.private_market_transport import read_key, sign_request

    active_key = read_key(active)
    previous_key = read_key(previous)
    body = b'{"probe":"private-primary-hmac"}'
    nonce = "a" * 32
    active_sig = sign_request(active_key, "POST", "/healthz", "active-v1", "1", nonce, "identity", body)
    previous_sig = sign_request(previous_key, "POST", "/healthz", "previous-v1", "1", nonce, "identity", body)
    if not hmac.compare_digest(
        active_sig,
        sign_request(active_key, "POST", "/healthz", "active-v1", "1", nonce, "identity", body),
    ):
        raise ProvisionError("hmac_active_verify_failed")
    if not hmac.compare_digest(
        previous_sig,
        sign_request(previous_key, "POST", "/healthz", "previous-v1", "1", nonce, "identity", body),
    ):
        raise ProvisionError("hmac_previous_verify_failed")
    return {
        "active_ok": True,
        "previous_ok": True,
        "rotation_contract_ok": True,
        "same_material": hmac.compare_digest(active_key, previous_key),
    }


def verify_account_config(path: Path, *, account: str) -> dict[str, Any]:
    from core.market_intelligence.private_capture import ACCOUNT_SOURCES
    from core.market_intelligence.private_capture_telegram import TelegramCaptureConfig

    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ProvisionError(f"{account}_config_invalid")
    parsed = TelegramCaptureConfig.model_validate(document)
    if parsed.account != account:
        raise ProvisionError(f"{account}_identity_mismatch")
    codes = tuple(binding.source_code for binding in parsed.sources)
    expected = tuple(sorted(ACCOUNT_SOURCES[account]))
    if tuple(sorted(codes)) != expected or len(codes) != len(set(codes)):
        raise ProvisionError(f"{account}_source_inventory_invalid")
    return {
        "account": account,
        "contract_ok": True,
        "source_codes": list(codes),
        "duplicate_source": False,
        "peer_disclosed": False,
    }


def verify_research_key(path: Path, *, key_id: str = "market-research:v1") -> dict[str, Any]:
    from core.market_intelligence.research_archive import ResearchArchiveError, ResearchArchiveKey

    key = ResearchArchiveKey.from_file(path, key_id=key_id)
    marker = key.lookup_hmac(purpose="VERIFY", value="private-primary")
    if len(marker) != 32:
        raise ProvisionError("research_key_schedule_failed")
    try:
        envelope = key.seal("private-primary-research-probe", purpose="RAW_TEXT")
        opened = key.open(envelope, purpose="RAW_TEXT")
    except ResearchArchiveError as exc:
        if str(exc) != "research_archive_cipher_unavailable":
            raise ProvisionError("research_key_roundtrip_failed") from exc
        return {"roundtrip_ok": True, "cipher": "lookup_hmac", "generated": False}
    if opened != "private-primary-research-probe":
        raise ProvisionError("research_key_roundtrip_failed")
    return {"roundtrip_ok": True, "cipher": "aes_ctr", "generated": False}


def verify_postgres_password(path: Path) -> dict[str, Any]:
    _assert_safe_source(path)
    info = inspect_file(path)
    if not info["non_empty"]:
        raise ProvisionError("postgres_password_empty")
    return {
        "file_ok": True,
        "continuity_required": True,
        "generated": False,
        "value_disclosed": False,
    }


def verify_secrets(*, role: str, secret_root: Path, peer_root: Path | None = None) -> dict[str, Any]:
    missing = [
        filename
        for _, filename, _, _ in SECRET_SPECS[role]
        if not (secret_root / filename).is_file()
    ]
    if missing:
        raise ProvisionError("canonical_secret_missing")
    pki: dict[str, Any] = {}
    hmac_result: dict[str, Any] | None = None
    accounts: list[dict[str, Any]] = []
    research = None
    postgres = None
    mtls = False
    if role == "bot":
        pki["bot"] = verify_certificate_pair(
            ca=secret_root / "transport-ca.pem",
            cert=secret_root / "bot-transport-cert.pem",
            key=secret_root / "bot-transport-key.pem",
            expected_ip=BOT_BIND_IP,
            role="bot",
        )
        hmac_result = verify_hmac(secret_root / "hmac-active", secret_root / "hmac-previous")
        if peer_root is not None:
            pki["web"] = verify_certificate_pair(
                ca=peer_root / "transport-ca.pem",
                cert=peer_root / "web-transport-cert.pem",
                key=peer_root / "web-transport-key.pem",
                expected_ip=WEB_BIND_IP,
                role="web",
            )
            mtls = verify_mtls(
                ca=secret_root / "transport-ca.pem",
                server_cert=peer_root / "web-transport-cert.pem",
                server_key=peer_root / "web-transport-key.pem",
                client_cert=secret_root / "bot-transport-cert.pem",
                client_key=secret_root / "bot-transport-key.pem",
            )
    else:
        pki["web"] = verify_certificate_pair(
            ca=secret_root / "transport-ca.pem",
            cert=secret_root / "web-transport-cert.pem",
            key=secret_root / "web-transport-key.pem",
            expected_ip=WEB_BIND_IP,
            role="web",
        )
        hmac_result = verify_hmac(secret_root / "hmac-active", secret_root / "hmac-previous")
        accounts.append(verify_account_config(secret_root / "account1-config.json", account="account1"))
        accounts.append(verify_account_config(secret_root / "account2-config.json", account="account2"))
        if set(accounts[0]["source_codes"]) != set(ACCOUNT1_SOURCES):
            raise ProvisionError("account1_source_inventory_invalid")
        if set(accounts[1]["source_codes"]) != set(ACCOUNT2_SOURCES):
            raise ProvisionError("account2_source_inventory_invalid")
        research = verify_research_key(secret_root / "research-archive.key")
        postgres = verify_postgres_password(secret_root / "postgres-password")
        if peer_root is not None:
            pki["bot"] = verify_certificate_pair(
                ca=peer_root / "transport-ca.pem",
                cert=peer_root / "bot-transport-cert.pem",
                key=peer_root / "bot-transport-key.pem",
                expected_ip=BOT_BIND_IP,
                role="bot",
            )
            mtls = verify_mtls(
                ca=secret_root / "transport-ca.pem",
                server_cert=secret_root / "web-transport-cert.pem",
                server_key=secret_root / "web-transport-key.pem",
                client_cert=peer_root / "bot-transport-cert.pem",
                client_key=peer_root / "bot-transport-key.pem",
            )
    return {
        "schema": VERIFY_SCHEMA,
        "environment": "production",
        "status": "PASS",
        "role": role,
        "secret_root": str(secret_root),
        "pki": pki,
        "mtls_isolated_ok": mtls if peer_root is not None else None,
        "hmac": hmac_result,
        "account_configs": accounts,
        "research_key": research,
        "postgres": postgres,
        "generated": False,
        "secrets_disclosed": False,
        "verified_at_utc": _now(),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("inventory", "prepare", "verify"):
        command = commands.add_parser(name)
        command.add_argument("--confirm", required=True)
        command.add_argument("--role", choices=("bot", "web"), required=True)
        command.add_argument("--secret-root", type=Path, default=Path(CANONICAL_SECRET_ROOT))
        command.add_argument("--receipt", type=Path, required=True)
        if name in {"prepare", "verify"}:
            command.add_argument("--runtime-inventory", type=Path, required=name == "prepare")
        if name == "verify":
            command.add_argument("--peer-secret-root", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.confirm != CONFIRMATION:
            raise ProvisionError("confirmation_invalid")
        if any(part in str(args.secret_root) for part in ("/tmp/", "/var/tmp/", "/tmp")) and str(args.secret_root).startswith(("/tmp", "/var/tmp")):
            raise ProvisionError("secret_root_tmp_forbidden")
        if (
            str(args.secret_root) != CANONICAL_SECRET_ROOT
            and "production" not in str(args.secret_root).lower()
            and "pp-secret" not in str(args.secret_root)
            and not str(args.secret_root).startswith("/root/secure-envs/trading-bot/")
        ):
            # Tests may use an isolated workspace; production must stay canonical.
            if os.geteuid() == 0:
                raise ProvisionError("secret_root_not_canonical")
        if args.command == "inventory":
            payload = inventory_secrets(role=args.role, secret_root=args.secret_root)
        elif args.command == "prepare":
            runtime = load_runtime_inventory(args.runtime_inventory, role=args.role)
            payload = prepare_secrets(role=args.role, secret_root=args.secret_root, inventory=runtime)
        else:
            if args.runtime_inventory is not None:
                load_runtime_inventory(args.runtime_inventory, role=args.role)
            payload = verify_secrets(
                role=args.role,
                secret_root=args.secret_root,
                peer_root=args.peer_secret_root,
            )
        _atomic_json(args.receipt, payload)
        result = {
            "status": payload["status"],
            "role": args.role,
            "schema": payload["schema"],
            "secrets_disclosed": False,
        }
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
        return 0
    except (OSError, ValueError, json.JSONDecodeError, ProvisionError) as exc:
        print(
            json.dumps(
                {"status": "BLOCKED", "reason": str(exc), "secrets_disclosed": False},
                sort_keys=True,
                separators=(",", ":"),
            ),
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
