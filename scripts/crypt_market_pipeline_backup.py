#!/usr/bin/env python3
"""Authenticated encryption for off-host Market Pipeline backups.

Only encrypted bytes are written at the destination.  Verification decrypts
to a pipe and hashes the plaintext in memory; it never materializes a second
plaintext copy.  The master key is a root-owned 32-byte hexadecimal secret and
is passed to OpenSSL through a private file, never through argv or output.
"""

from __future__ import annotations

import argparse
from hashlib import sha256
import hmac
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
from typing import Any, Mapping, Sequence


CONFIRMATION = "encrypt-production-market-pipeline-offhost-backup"
KEY_CONFIRMATION = "generate-production-market-pipeline-backup-key"
SCHEMA = "market_pipeline_backup_encryption/1.1"
ALGORITHM = "AES-256-CBC+PBKDF2-HMAC-SHA256"
KDF = "PBKDF2-HMAC-SHA256"
KDF_ITERATIONS = 600_000
HEX64 = re.compile(r"^[0-9a-f]{64}$")


class BackupCryptError(RuntimeError):
    """Stable, content-free refusal."""


def _digest(path: Path) -> str:
    value = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def _secure_regular(path: Path, *, mode: int = 0o600) -> None:
    try:
        info = path.lstat()
    except OSError as exc:
        raise BackupCryptError("backup_crypt_file_unavailable") from exc
    if (
        path.is_symlink()
        or not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.geteuid()
        or stat.S_IMODE(info.st_mode) != mode
        or info.st_nlink != 1
    ):
        raise BackupCryptError("backup_crypt_file_security_invalid")


def _secure_parent(path: Path) -> None:
    parent = path.parent
    try:
        info = parent.lstat()
    except OSError as exc:
        raise BackupCryptError("backup_crypt_parent_unavailable") from exc
    if (
        not parent.is_absolute()
        or parent.is_symlink()
        or not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.geteuid()
        or stat.S_IMODE(info.st_mode) != 0o700
    ):
        raise BackupCryptError("backup_crypt_parent_security_invalid")


def generate_key(*, key_file: Path) -> dict[str, Any]:
    _secure_parent(key_file)
    if key_file.exists() or key_file.is_symlink():
        _secure_regular(key_file)
        try:
            text = key_file.read_text(encoding="ascii").strip()
        except (OSError, UnicodeDecodeError) as exc:
            raise BackupCryptError("backup_crypt_key_invalid") from exc
        if not HEX64.fullmatch(text):
            raise BackupCryptError("backup_crypt_key_invalid")
        return {
            "status": "PASS",
            "reused": True,
            "created": False,
            "mode": "0600",
            "secrets_disclosed": False,
        }
    payload = os.urandom(32).hex() + "\n"
    candidate = key_file.parent / f".{key_file.name}.{os.getpid()}.tmp"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(candidate, flags, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="ascii") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(candidate, key_file)
        directory = os.open(key_file.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        candidate.unlink(missing_ok=True)
    os.chmod(key_file, 0o600)
    return {
        "status": "PASS",
        "reused": False,
        "created": True,
        "mode": "0600",
        "secrets_disclosed": False,
    }


def _master_key(path: Path) -> bytes:
    _secure_regular(path)
    try:
        text = path.read_text(encoding="ascii").strip()
    except (OSError, UnicodeDecodeError) as exc:
        raise BackupCryptError("backup_crypt_key_invalid") from exc
    if not HEX64.fullmatch(text):
        raise BackupCryptError("backup_crypt_key_invalid")
    return bytes.fromhex(text)


def _authentication(key: bytes, path: Path) -> str:
    authentication_key = hmac.new(
        key, b"gold-trade/market-pipeline/offhost-auth/v1", sha256
    ).digest()
    value = hmac.new(authentication_key, digestmod=sha256)
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def _openssl() -> str:
    executable = shutil.which("openssl")
    if not executable or not Path(executable).is_absolute():
        raise BackupCryptError("backup_crypt_openssl_unavailable")
    return executable


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    _secure_parent(path)
    candidate = path.parent / f".{path.name}.{os.getpid()}.tmp"
    descriptor = os.open(
        candidate,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, sort_keys=True, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        if path.exists():
            _secure_regular(path)
            if path.read_bytes() != candidate.read_bytes():
                raise BackupCryptError("backup_crypt_receipt_drift")
            candidate.unlink()
        else:
            os.replace(candidate, path)
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        candidate.unlink(missing_ok=True)


def _decrypt_digest(*, artifact: Path, key_file: Path) -> tuple[str, int]:
    process = subprocess.Popen(
        [
            _openssl(), "enc", "-d", "-aes-256-cbc", "-pbkdf2",
            "-iter", str(KDF_ITERATIONS), "-md", "sha256",
            "-in", str(artifact), "-pass", f"file:{key_file}",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert process.stdout is not None
    value = sha256()
    size = 0
    for chunk in iter(lambda: process.stdout.read(1024 * 1024), b""):
        value.update(chunk)
        size += len(chunk)
    _stdout, stderr = process.communicate()
    if process.returncode:
        del stderr
        raise BackupCryptError("backup_crypt_decrypt_failed")
    return value.hexdigest(), size


def verify(
    *, artifact: Path, key_file: Path, receipt: Path,
    expected_plaintext_sha256: str | None = None,
    expected_plaintext_size_bytes: int | None = None,
) -> dict[str, Any]:
    _secure_regular(artifact)
    _secure_regular(receipt)
    key = _master_key(key_file)
    try:
        payload = json.loads(receipt.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BackupCryptError("backup_crypt_receipt_invalid") from exc
    expected_keys = {
        "schema", "algorithm", "kdf", "kdf_iterations",
        "plaintext_sha256", "plaintext_size_bytes", "ciphertext_sha256",
        "ciphertext_size_bytes", "authentication_hmac_sha256",
        "plaintext_materialized_offhost", "secrets_disclosed",
    }
    if (
        not isinstance(payload, dict)
        or set(payload) != expected_keys
        or payload.get("schema") != SCHEMA
        or payload.get("algorithm") != ALGORITHM
        or payload.get("kdf") != KDF
        or payload.get("kdf_iterations") != KDF_ITERATIONS
        or payload.get("plaintext_materialized_offhost") is not False
        or payload.get("secrets_disclosed") is not False
        or not HEX64.fullmatch(str(payload.get("plaintext_sha256") or ""))
        or not HEX64.fullmatch(str(payload.get("ciphertext_sha256") or ""))
        or not HEX64.fullmatch(str(payload.get("authentication_hmac_sha256") or ""))
        or int(payload.get("plaintext_size_bytes") or 0) <= 0
        or int(payload.get("ciphertext_size_bytes") or 0) <= 0
    ):
        raise BackupCryptError("backup_crypt_receipt_contract_invalid")
    if (
        _digest(artifact) != payload["ciphertext_sha256"]
        or artifact.stat().st_size != payload["ciphertext_size_bytes"]
        or not hmac.compare_digest(
            _authentication(key, artifact), payload["authentication_hmac_sha256"]
        )
    ):
        raise BackupCryptError("backup_crypt_ciphertext_authentication_failed")
    plaintext_sha256, plaintext_size = _decrypt_digest(
        artifact=artifact, key_file=key_file
    )
    if (
        plaintext_sha256 != payload["plaintext_sha256"]
        or plaintext_size != payload["plaintext_size_bytes"]
        or (
            expected_plaintext_sha256 is not None
            and plaintext_sha256 != expected_plaintext_sha256
        )
        or (
            expected_plaintext_size_bytes is not None
            and plaintext_size != expected_plaintext_size_bytes
        )
    ):
        raise BackupCryptError("backup_crypt_plaintext_reconciliation_failed")
    return payload


def _receipt_payload(
    *, source: Path, destination: Path, key: bytes
) -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "algorithm": ALGORITHM,
        "kdf": KDF,
        "kdf_iterations": KDF_ITERATIONS,
        "plaintext_sha256": _digest(source),
        "plaintext_size_bytes": source.stat().st_size,
        "ciphertext_sha256": _digest(destination),
        "ciphertext_size_bytes": destination.stat().st_size,
        "authentication_hmac_sha256": _authentication(key, destination),
        "plaintext_materialized_offhost": False,
        "secrets_disclosed": False,
    }


def encrypt(
    *, source: Path, destination: Path, key_file: Path, receipt: Path
) -> dict[str, Any]:
    _secure_regular(source)
    _secure_parent(destination)
    if destination.parent != receipt.parent or source == destination:
        raise BackupCryptError("backup_crypt_destination_invalid")
    key = _master_key(key_file)
    if destination.exists() or receipt.exists():
        if destination.exists() and not receipt.exists():
            # The ciphertext rename is durable before its receipt is written.
            # A crash in that narrow window must be resumable without replacing
            # or weakening the already-created encrypted backup.
            _secure_regular(destination)
            plaintext_sha256, plaintext_size = _decrypt_digest(
                artifact=destination, key_file=key_file
            )
            if (
                plaintext_sha256 != _digest(source)
                or plaintext_size != source.stat().st_size
            ):
                raise BackupCryptError("backup_crypt_partial_output_drift")
            _atomic_json(
                receipt,
                _receipt_payload(source=source, destination=destination, key=key),
            )
        elif not destination.exists() or not receipt.exists():
            raise BackupCryptError("backup_crypt_partial_output")
        return verify(
            artifact=destination,
            key_file=key_file,
            receipt=receipt,
            expected_plaintext_sha256=_digest(source),
            expected_plaintext_size_bytes=source.stat().st_size,
        )
    candidate = destination.parent / f".{destination.name}.{os.getpid()}.tmp"
    try:
        completed = subprocess.run(
            [
                _openssl(), "enc", "-aes-256-cbc", "-pbkdf2",
                "-iter", str(KDF_ITERATIONS), "-md", "sha256", "-salt",
                "-in", str(source), "-out", str(candidate),
                "-pass", f"file:{key_file}",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            check=False,
        )
        if completed.returncode:
            raise BackupCryptError("backup_crypt_encrypt_failed")
        os.chmod(candidate, 0o600)
        with candidate.open("rb") as stream:
            os.fsync(stream.fileno())
        os.replace(candidate, destination)
        directory = os.open(destination.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        candidate.unlink(missing_ok=True)
    _secure_regular(destination)
    payload = _receipt_payload(source=source, destination=destination, key=key)
    _atomic_json(receipt, payload)
    return verify(
        artifact=destination,
        key_file=key_file,
        receipt=receipt,
        expected_plaintext_sha256=payload["plaintext_sha256"],
        expected_plaintext_size_bytes=payload["plaintext_size_bytes"],
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("encrypt")
    create.add_argument("--source", type=Path, required=True)
    create.add_argument("--destination", type=Path, required=True)
    create.add_argument("--key-file", type=Path, required=True)
    create.add_argument("--receipt", type=Path, required=True)
    create.add_argument("--confirm", required=True)
    check = commands.add_parser("verify")
    check.add_argument("--artifact", type=Path, required=True)
    check.add_argument("--key-file", type=Path, required=True)
    check.add_argument("--receipt", type=Path, required=True)
    check.add_argument("--expected-plaintext-sha256")
    check.add_argument("--expected-plaintext-size-bytes", type=int)
    keygen = commands.add_parser("generate-key")
    keygen.add_argument("--key-file", type=Path, required=True)
    keygen.add_argument("--confirm", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "generate-key":
            if args.confirm != KEY_CONFIRMATION:
                raise BackupCryptError("backup_crypt_confirmation_invalid")
            payload = generate_key(key_file=args.key_file)
            print(json.dumps(payload, sort_keys=True))
            return 0
        if args.command == "encrypt":
            if args.confirm != CONFIRMATION:
                raise BackupCryptError("backup_crypt_confirmation_invalid")
            payload = encrypt(
                source=args.source,
                destination=args.destination,
                key_file=args.key_file,
                receipt=args.receipt,
            )
        else:
            if args.expected_plaintext_sha256 is not None and not HEX64.fullmatch(
                args.expected_plaintext_sha256
            ):
                raise BackupCryptError("backup_crypt_expected_digest_invalid")
            payload = verify(
                artifact=args.artifact,
                key_file=args.key_file,
                receipt=args.receipt,
                expected_plaintext_sha256=args.expected_plaintext_sha256,
                expected_plaintext_size_bytes=args.expected_plaintext_size_bytes,
            )
        print(json.dumps({"status": "PASS", **payload}, sort_keys=True))
        return 0
    except (OSError, ValueError, BackupCryptError) as exc:
        print(
            json.dumps({"status": "BLOCKED", "reason": str(exc), "secrets_disclosed": False}, sort_keys=True),
            file=os.sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
