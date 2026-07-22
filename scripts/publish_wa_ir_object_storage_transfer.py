#!/usr/bin/env python3
"""Publish one short-lived Matrix file for WA-IR through Object Storage."""

from __future__ import annotations

from pathlib import Path
import re
import tempfile
from typing import Any
from collections.abc import Callable

from core.secure_file_io import read_secure_text
from scripts.publish_wa_ir_object_storage_preflight import (
    ARVAN_ENDPOINT,
    ARVAN_REGION,
    _client,
    _hash_regular,
    _presigned_get,
    _upload_and_readback,
    encrypt,
    require_private_versioned_bucket,
)
from scripts.wa_ir_object_storage_preflight_agent import (
    FILE_TRANSFER_IDENTITY,
    FILE_TRANSFER_MAX_BYTES,
    FILE_TRANSFER_NAMES,
    FILE_TRANSFER_SCHEMA,
)


DEFAULT_CONFIG = Path("/root/secure-envs/trading-bot/wa-ir-object-storage-transport.env")
CONFIG_FIELDS = frozenset(
    {
        "ARVAN_S3_ACCESS_KEY",
        "ARVAN_S3_SECRET_KEY",
        "ARVAN_S3_ENDPOINT",
        "ARVAN_S3_REGION",
        "WA_IR_OBJECT_STORAGE_BUCKET",
        "WA_IR_OBJECT_STORAGE_PREFIX",
        "WA_IR_AGE_RECIPIENT_FILE",
        "WA_IR_REMOTE_AGE_IDENTITY",
    }
)


class TransferPublicationError(RuntimeError):
    pass


def _config(path: Path) -> dict[str, str]:
    try:
        text = read_secure_text(path, label="WA-IR Object Storage transport config", max_size=32_768)
    except Exception as exc:
        raise TransferPublicationError("WA-IR Object Storage transport config is unavailable") from exc
    values: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise TransferPublicationError("WA-IR Object Storage transport config has an invalid line")
        key, value = line.split("=", 1)
        if key in values:
            raise TransferPublicationError("WA-IR Object Storage transport config has a duplicate key")
        values[key] = value
    if set(values) != CONFIG_FIELDS:
        raise TransferPublicationError("WA-IR Object Storage transport config fields are invalid")
    if values["ARVAN_S3_ENDPOINT"].rstrip("/") != ARVAN_ENDPOINT or values["ARVAN_S3_REGION"] != ARVAN_REGION:
        raise TransferPublicationError("WA-IR Object Storage transport endpoint/region drifted")
    if values["WA_IR_REMOTE_AGE_IDENTITY"] != str(FILE_TRANSFER_IDENTITY):
        raise TransferPublicationError("WA-IR Object Storage remote age identity drifted")
    prefix = values["WA_IR_OBJECT_STORAGE_PREFIX"].strip("/")
    if not re.fullmatch(r"[a-z0-9][a-z0-9./_-]{4,180}", prefix) or ".." in Path(prefix).parts:
        raise TransferPublicationError("WA-IR Object Storage transport prefix is invalid")
    values["WA_IR_OBJECT_STORAGE_PREFIX"] = prefix
    return values


def publish_file(
    source: Path,
    *,
    campaign_tag: str,
    destination: str,
    mode: int,
    config_path: Path = DEFAULT_CONFIG,
    client=None,  # noqa: ANN001
    encryptor: Callable[[Path, Path, str], tuple[str, int]] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if not re.fullmatch(r"wwm_[0-9a-f]{12}", campaign_tag):
        raise TransferPublicationError("Matrix campaign tag is invalid")
    destination_path = Path(destination)
    if (
        destination_path.parent != Path("/run/writer-witness-matrix") / campaign_tag
        or destination_path.name not in FILE_TRANSFER_NAMES
        or mode not in {0o600, 0o700}
    ):
        raise TransferPublicationError("WA-IR transfer destination is outside the allowlist")
    try:
        metadata = source.lstat()
    except OSError as exc:
        raise TransferPublicationError("WA-IR transfer source is unavailable") from exc
    if (
        not source.is_file()
        or source.is_symlink()
        or metadata.st_uid != 0
        or metadata.st_nlink != 1
        or metadata.st_size <= 0
        or metadata.st_size > FILE_TRANSFER_MAX_BYTES
    ):
        raise TransferPublicationError("WA-IR transfer source is not one bounded root-owned file")

    values = _config(config_path)
    recipient_path = Path(values["WA_IR_AGE_RECIPIENT_FILE"])
    try:
        recipient = read_secure_text(
            recipient_path, label="WA-IR Object Storage age recipient", max_size=4096
        ).strip()
    except Exception as exc:
        raise TransferPublicationError("WA-IR Object Storage age recipient is unavailable") from exc
    credentials = (
        values["ARVAN_S3_ACCESS_KEY"],
        values["ARVAN_S3_SECRET_KEY"],
        values["ARVAN_S3_ENDPOINT"],
        values["ARVAN_S3_REGION"],
    )
    if client is None:
        if len(credentials[0]) < 8 or len(credentials[1]) < 32:
            raise TransferPublicationError("WA-IR Object Storage credentials are malformed")
        client = _client(credentials)
    bucket = values["WA_IR_OBJECT_STORAGE_BUCKET"]
    try:
        require_private_versioned_bucket(client, bucket=bucket)
    except Exception as exc:
        raise TransferPublicationError(
            "WA-IR file transport requires one private versioned bucket"
        ) from exc

    plaintext_hash, plaintext_size = _hash_regular(
        source, label="WA-IR transfer source", max_size=FILE_TRANSFER_MAX_BYTES
    )
    with tempfile.TemporaryDirectory(prefix="wa-ir-transfer-") as raw:
        encrypted = Path(raw) / "payload.age"
        ciphertext_hash, ciphertext_size = (encryptor or encrypt)(
            source, encrypted, recipient
        )
        after_hash, after_size = _hash_regular(
            source, label="WA-IR transfer source", max_size=FILE_TRANSFER_MAX_BYTES
        )
        if after_hash != plaintext_hash or after_size != plaintext_size:
            raise TransferPublicationError("WA-IR transfer source changed during encryption")
        key = (
            f"{values['WA_IR_OBJECT_STORAGE_PREFIX']}/{campaign_tag}/"
            f"{destination_path.name}/{ciphertext_hash}.age"
        )
        obj = _upload_and_readback(
            client,
            bucket=bucket,
            key=key,
            source=encrypted,
            metadata={
                "kind": "matrix-file-transfer",
                "campaign-tag": campaign_tag,
                "destination-name": destination_path.name,
                "plaintext-sha256": plaintext_hash,
            },
        )
        url = _presigned_get(client, bucket=bucket, obj=obj, ttl=300)
    descriptor = {
        "schema": FILE_TRANSFER_SCHEMA,
        "role": "webapp-ir",
        "campaign_tag": campaign_tag,
        "destination": destination,
        "mode": mode,
        "artifact": {
            "url": url,
            "sha256": plaintext_hash,
            "bytes": plaintext_size,
            "encrypted": True,
            "ciphertext_sha256": ciphertext_hash,
            "ciphertext_bytes": ciphertext_size,
        },
    }
    evidence = {
        "bucket": bucket,
        "object_key": obj["object_key"],
        "version_id": obj["version_id"],
        "ciphertext_sha256": ciphertext_hash,
        "ciphertext_bytes": ciphertext_size,
        "plaintext_sha256": plaintext_hash,
        "plaintext_bytes": plaintext_size,
        "destination_name": destination_path.name,
        "presigned_url_persisted": False,
    }
    return descriptor, evidence
