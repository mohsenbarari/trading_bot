#!/usr/bin/env python3
"""Generate campaign-new private material for one planned three-site inventory.

The only reusable provider inputs are four Telegram values and the two Arvan
S3 credential values.  Telegram identity checks are read-only.  TLS, Witness,
blob, database, application, and directed transport secrets are all generated
inside a new owner-only directory and published with no-replace semantics.

This tool deliberately does not create an age identity or any approval,
session, relay, seed, or migration material.
"""

from __future__ import annotations

import argparse
import base64
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import sys
from typing import Any, Callable
import urllib.parse
import urllib.request
from uuid import UUID


sys.dont_write_bytecode = True
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import yaml
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from core.three_site_execution_safety import (
    DEDICATED_HOST_DESTRUCTIVE,
    EXECUTION_CLASSES,
)
from scripts.render_three_site_staging_role_compose import (
    canonical_role_compose_bytes,
    canonical_role_env_bytes,
    parse_env_values,
    referenced_environment_names,
    render_role_compose,
)
from scripts.fresh_campaign_secure_io import (
    FreshCampaignSecureIOError,
    SecureOutputDirectory,
    prove_exact_git_release,
    read_secure_material_tree,
    read_secure_root_file,
)
from scripts.verify_three_site_staging_inventory import (
    _canonical_bytes,
    verify_inventory,
)


S3_ENDPOINT = "https://s3.ir-thr-at1.arvanstorage.ir"
S3_REGION = "ir-thr-at1"
TELEGRAM_FIELDS = frozenset(
    {
        "BOT_TOKEN",
        "BOT_USERNAME",
        "CHANNEL_ID",
        "TELEGRAM_DELIVERY_QUEUE_CHANNEL_EDITOR_BOT_TOKEN",
    }
)
S3_FIELDS = frozenset({"access_key", "secret_key"})
RELEASE_RE = re.compile(r"^[0-9a-f]{40}$")
CAMPAIGN_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
DEPLOYMENT_RE = re.compile(r"^[a-z0-9][a-z0-9-]{7,95}$")
OBJECT_PREFIX_RE = re.compile(
    r"^staging/three-site/[0-9a-f]{40}/[0-9a-f-]{36}/[a-z0-9][a-z0-9-]{7,95}/$"
)
BUCKET_RE = re.compile(r"^[a-z0-9][a-z0-9.-]{2,62}$")
CREDENTIAL_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$")
BOT_TOKEN_RE = re.compile(r"^[0-9]{6,16}:[A-Za-z0-9_-]{20,80}$")
BOT_USERNAME_RE = re.compile(r"^@?[A-Za-z0-9_]{5,32}$")
CHANNEL_ID_RE = re.compile(r"^-100[0-9]{6,20}$")
ROLE_NAMES = ("bot-fi", "webapp-fi", "webapp-ir", "witness")
ROLE_INVENTORY_NAMES = {
    "bot-fi": "bot_fi",
    "webapp-fi": "webapp_fi",
    "webapp-ir": "webapp_ir",
    "witness": "witness",
}
ROLE_CERTIFICATES = {
    "bot-fi": ("bot-fi-dr", "bot-fi-dr.staging.internal"),
    "webapp-fi": ("webapp-fi-dr", "webapp-fi-dr.staging.internal"),
    "webapp-ir": ("webapp-ir-dr", "webapp-ir-dr.staging.internal"),
    "witness": ("witness-dr", "witness-dr.staging.internal"),
}
RUNTIME_SECRET_KEYS = {
    "STAGING_DR_BLOB_CREDENTIALS_FILE": "staging-dr-blob-s3.json",
    "STAGING_DR_BLOB_ENCRYPTION_KEYRING_FILE": "staging-dr-blob-keyring.json",
    "STAGING_DR_CA_CERT": "staging-dr-ca.crt",
    "STAGING_BOT_FI_TLS_CERT": "bot-fi-dr.crt",
    "STAGING_BOT_FI_TLS_KEY": "bot-fi-dr.key",
    "STAGING_WEBAPP_FI_TLS_CERT": "webapp-fi-dr.crt",
    "STAGING_WEBAPP_FI_TLS_KEY": "webapp-fi-dr.key",
    "STAGING_WEBAPP_IR_TLS_CERT": "webapp-ir-dr.crt",
    "STAGING_WEBAPP_IR_TLS_KEY": "webapp-ir-dr.key",
    "STAGING_WITNESS_TLS_CERT": "witness-dr.crt",
    "STAGING_WITNESS_TLS_KEY": "witness-dr.key",
    "STAGING_WITNESS_SIGNING_KEY": "witness-ed25519-private.key",
}
CONTROLLER_ONLY_FILES = frozenset({"secrets/staging-dr-ca.key"})
TEMPLATE_BLOBS = (
    "deploy/staging/env.three-site.staging.example",
    "deploy/staging/docker-compose.three-site.yml",
)
SECRET_ENV_NAMES = frozenset(
    {
        "ORIGIN_READINESS_API_KEY",
        "BOT_FI_POSTGRES_PASSWORD",
        "BOT_FI_APP_DB_PASSWORD",
        "BOT_FI_RECEIVER_DB_PASSWORD",
        "BOT_FI_DELIVERY_DB_PASSWORD",
        "BOT_FI_PROJECTION_DB_PASSWORD",
        "BOT_FI_OBSERVER_DB_PASSWORD",
        "BOT_FI_JWT_SECRET_KEY",
        "WEBAPP_FI_POSTGRES_PASSWORD",
        "WEBAPP_FI_APP_DB_PASSWORD",
        "WEBAPP_FI_RECEIVER_DB_PASSWORD",
        "WEBAPP_FI_DELIVERY_DB_PASSWORD",
        "WEBAPP_FI_PROJECTION_DB_PASSWORD",
        "WEBAPP_FI_BLOB_DB_PASSWORD",
        "WEBAPP_FI_EFFECT_DB_PASSWORD",
        "WEBAPP_FI_CONTROL_DB_PASSWORD",
        "WEBAPP_FI_OBSERVER_DB_PASSWORD",
        "WEBAPP_IR_POSTGRES_PASSWORD",
        "WEBAPP_IR_APP_DB_PASSWORD",
        "WEBAPP_IR_RECEIVER_DB_PASSWORD",
        "WEBAPP_IR_DELIVERY_DB_PASSWORD",
        "WEBAPP_IR_PROJECTION_DB_PASSWORD",
        "WEBAPP_IR_BLOB_DB_PASSWORD",
        "WEBAPP_IR_EFFECT_DB_PASSWORD",
        "WEBAPP_IR_CONTROL_DB_PASSWORD",
        "WEBAPP_IR_OBSERVER_DB_PASSWORD",
        "WEBAPP_JWT_SECRET_KEY",
        "WITNESS_POSTGRES_PASSWORD",
        "WITNESS_MIGRATOR_DB_PASSWORD",
        "WITNESS_RUNTIME_DB_PASSWORD",
        "WEBAPP_FI_WITNESS_SECRET",
        "WEBAPP_IR_WITNESS_SECRET",
    }
)
PAIRWISE_DIRECTIONS = (
    ("bot-to-fi", "bot_fi", "webapp_fi"),
    ("fi-to-bot", "webapp_fi", "bot_fi"),
    ("fi-to-ir", "webapp_fi", "webapp_ir"),
    ("ir-to-fi", "webapp_ir", "webapp_fi"),
)
INTENTIONALLY_EMPTY_ENV_NAMES = frozenset(
    {
        "STAGING_HUMAN_APPROVAL_RELAY_ORCHESTRATOR_KEY_ID",
        "STAGING_HUMAN_APPROVAL_RELAY_ORCHESTRATOR_SECRET",
        "SMSIR_API_KEY",
        "WEB_PUSH_VAPID_PUBLIC_KEY",
        "WEB_PUSH_VAPID_PRIVATE_KEY",
        "WEB_PUSH_VAPID_SUBJECT",
    }
)
FIXED_LITERAL_ENV_NAMES = frozenset(
    {
        "STAGING_FRONTEND_DOCKER_DIST_DIR", "STAGING_CGROUP_PARENT",
        "STAGING_APP_CPU_LIMIT", "STAGING_APP_MEMORY_LIMIT", "STAGING_APP_PIDS_LIMIT",
        "STAGING_POSTGRES_CPU_LIMIT", "STAGING_POSTGRES_MEMORY_LIMIT", "STAGING_POSTGRES_PIDS_LIMIT",
        "STAGING_REDIS_CPU_LIMIT", "STAGING_REDIS_MEMORY_LIMIT", "STAGING_REDIS_PIDS_LIMIT",
        "STAGING_NGINX_CPU_LIMIT", "STAGING_NGINX_MEMORY_LIMIT", "STAGING_NGINX_PIDS_LIMIT",
        "STAGING_TRUSTED_PROXY_CIDRS", "ORIGIN_EXPECTED_MIGRATION_REVISION",
        "ORIGIN_READINESS_REQUIRE_RECOVERY_MANIFEST", "DR_ISOLATED_CRITICAL_WRITE_POLICY",
        "BOT_FI_POSTGRES_USER", "BOT_FI_POSTGRES_DB", "WEBAPP_FI_POSTGRES_USER",
        "WEBAPP_FI_POSTGRES_DB", "WEBAPP_IR_POSTGRES_USER", "WEBAPP_IR_POSTGRES_DB",
        "WITNESS_POSTGRES_USER", "WITNESS_POSTGRES_DB", "WITNESS_MIGRATOR_DB_USER",
        "WITNESS_RUNTIME_DB_USER", "STAGING_WITNESS_URL", "TELEGRAM_DELIVERY_PRODUCER_MODE",
        "TELEGRAM_DELIVERY_EXPECTED_EXECUTION_OWNER", "TELEGRAM_DELIVERY_EXECUTION_OWNER",
        "TELEGRAM_DELIVERY_QUEUE_WORKER_ENABLED", "TELEGRAM_DELIVERY_QUEUE_CUTOVER_READY",
        "BOT_FI_DR_PEERS_JSON", "WEBAPP_FI_DR_PEERS_JSON", "WEBAPP_IR_DR_PEERS_JSON",
        "WEB_PUSH_ENABLED",
    }
)
DYNAMIC_ENV_NAMES = frozenset(
    {
        "STAGING_RELEASE_SHA", "STAGING_SOURCE_ROOT", "STAGING_DATA_ROOT",
        "STAGING_STORAGE_NAMESPACE", "FRONTEND_URL", "PUBLIC_WEBAPP_URL",
        "WRITER_WITNESS_PUBLIC_KEY", "WEBAPP_FI_WITNESS_KEY_ID",
        "WEBAPP_IR_WITNESS_KEY_ID", "STAGING_HUMAN_APPROVAL_RELAY_ENABLED",
        "STAGING_HUMAN_APPROVAL_RELAY_MATERIAL_DIR", "BOT_TOKEN", "BOT_USERNAME",
        "CHANNEL_ID", "CHANNEL_INVITE_LINK", "TELEGRAM_DELIVERY_QUEUE_CHANNEL_EDITOR_ENABLED",
        "TELEGRAM_DELIVERY_QUEUE_CHANNEL_EDITOR_BOT_TOKEN",
        "TELEGRAM_DELIVERY_QUEUE_EXPECTED_PRIMARY_BOT_ID",
        "TELEGRAM_DELIVERY_QUEUE_EXPECTED_CHANNEL_EDITOR_BOT_ID",
        "TELEGRAM_DELIVERY_QUEUE_EXPECTED_CHANNEL_ID", "BOT_FI_DR_PAIRWISE_KEYS_JSON",
        "WEBAPP_FI_DR_PAIRWISE_KEYS_JSON", "WEBAPP_IR_DR_PAIRWISE_KEYS_JSON",
        "BOT_FI_DR_BIND_ADDRESS", "WEBAPP_FI_DR_BIND_ADDRESS",
        "WEBAPP_IR_DR_BIND_ADDRESS", "WITNESS_DR_BIND_ADDRESS",
        "BOT_FI_PEER_WEBAPP_FI_IP", "WEBAPP_FI_PEER_BOT_FI_IP",
        "WEBAPP_FI_PEER_WEBAPP_IR_IP", "WEBAPP_FI_WITNESS_IP",
        "WEBAPP_IR_PEER_WEBAPP_FI_IP", "WEBAPP_IR_WITNESS_IP",
        "DR_BLOB_OBJECT_ENDPOINT", "DR_BLOB_OBJECT_REGION", "DR_BLOB_OBJECT_BUCKET",
        "DR_BLOB_OBJECT_PREFIX", "DR_BLOB_REQUIRE_VERSIONING",
        *RUNTIME_SECRET_KEYS,
        *SECRET_ENV_NAMES,
    }
)


class FreshPrivateMaterialError(RuntimeError):
    """Fresh campaign material cannot be safely generated."""


TelegramReader = Callable[[str, str, dict[str, str] | None], dict[str, Any]]


def _strict_object(
    pairs: list[tuple[str, Any]], *, label: str
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise FreshPrivateMaterialError(f"{label} contains duplicate fields")
        result[key] = value
    return result


def _strict_json(raw: str, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(
            raw,
            object_pairs_hook=lambda pairs: _strict_object(pairs, label=label),
        )
    except (json.JSONDecodeError, FreshPrivateMaterialError) as exc:
        raise FreshPrivateMaterialError(f"{label} is not strict JSON") from exc
    if not isinstance(payload, dict):
        raise FreshPrivateMaterialError(f"{label} must be a JSON object")
    return payload


def _private_text(path: Path, *, label: str, max_size: int) -> str:
    try:
        return read_secure_root_file(
            path,
            label=label,
            expected_mode=0o600,
            max_size=max_size,
        ).decode("utf-8")
    except (UnicodeDecodeError, FreshCampaignSecureIOError) as exc:
        raise FreshPrivateMaterialError(f"{label} is unavailable or unsafe") from None


def _load_provider_environment(path: Path) -> dict[str, str]:
    try:
        all_values = parse_env_values(
            _private_text(path, label="Telegram provider environment", max_size=1024 * 1024)
        )
    except Exception:
        raise FreshPrivateMaterialError(
            "Telegram provider environment is invalid"
        ) from None
    if not TELEGRAM_FIELDS <= set(all_values):
        raise FreshPrivateMaterialError("Telegram provider values are incomplete")
    provider = {name: all_values[name] for name in TELEGRAM_FIELDS}
    if (
        BOT_TOKEN_RE.fullmatch(provider["BOT_TOKEN"]) is None
        or BOT_TOKEN_RE.fullmatch(
            provider["TELEGRAM_DELIVERY_QUEUE_CHANNEL_EDITOR_BOT_TOKEN"]
        )
        is None
        or provider["BOT_TOKEN"]
        == provider["TELEGRAM_DELIVERY_QUEUE_CHANNEL_EDITOR_BOT_TOKEN"]
        or BOT_USERNAME_RE.fullmatch(provider["BOT_USERNAME"]) is None
        or CHANNEL_ID_RE.fullmatch(provider["CHANNEL_ID"]) is None
    ):
        raise FreshPrivateMaterialError("Telegram provider values are malformed")
    return provider


def _load_s3_credentials(path: Path) -> dict[str, str]:
    payload = _strict_json(
        _private_text(path, label="Arvan S3 provider credential", max_size=16 * 1024),
        label="Arvan S3 provider credential",
    )
    if set(payload) != S3_FIELDS or any(
        not isinstance(payload[name], str)
        or not payload[name]
        or len(payload[name]) > 512
        or any(ord(character) < 0x20 for character in payload[name])
        for name in S3_FIELDS
    ):
        raise FreshPrivateMaterialError("Arvan S3 provider credential fields are invalid")
    if len(payload["access_key"]) < 8 or len(payload["secret_key"]) < 32:
        raise FreshPrivateMaterialError("Arvan S3 provider credentials are malformed")
    return {name: str(payload[name]) for name in S3_FIELDS}


def _load_planned_inventory(
    path: Path, *, execution_class: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    payload = _strict_json(
        _private_text(path, label="planned inventory", max_size=4 * 1024 * 1024),
        label="planned inventory",
    )
    if execution_class not in EXECUTION_CLASSES:
        raise FreshPrivateMaterialError("execution class is invalid")
    try:
        result = verify_inventory(
            payload,
            host_destructive=execution_class == DEDICATED_HOST_DESTRUCTIVE,
        )
    except Exception:
        raise FreshPrivateMaterialError(
            "planned inventory verification failed"
        ) from None
    if (
        result["inventory_stage"] != "planned"
        or result["host_safety_mode"] != execution_class
        or RELEASE_RE.fullmatch(result["release_sha"]) is None
    ):
        raise FreshPrivateMaterialError(
            "private material requires an exact planned inventory"
        )
    campaign = str(payload.get("campaign_id") or "")
    try:
        if str(UUID(campaign)) != campaign or CAMPAIGN_RE.fullmatch(campaign) is None:
            raise ValueError
    except ValueError:
        raise FreshPrivateMaterialError("planned campaign ID is not canonical") from None
    deployment = str(payload.get("deployment_id") or "")
    storage = payload["object_storage"]
    expected_prefix = (
        f"staging/three-site/{result['release_sha']}/{campaign}/{deployment}/"
    )
    if (
        DEPLOYMENT_RE.fullmatch(deployment) is None
        or OBJECT_PREFIX_RE.fullmatch(str(storage.get("prefix") or "")) is None
        or storage.get("prefix") != expected_prefix
        or BUCKET_RE.fullmatch(str(storage.get("bucket") or "")) is None
        or CREDENTIAL_ID_RE.fullmatch(str(storage.get("credential_id") or "")) is None
        or storage.get("private") is not True
        or storage.get("versioning") is not True
    ):
        raise FreshPrivateMaterialError("planned Object Storage binding is invalid")
    roles = payload.get("roles")
    if not isinstance(roles, list) or len(roles) != 4:
        raise FreshPrivateMaterialError("planned inventory role set is invalid")
    for field in ("host_ip", "machine_id", "docker_daemon_id"):
        values = [str(row.get(field) or "") for row in roles if isinstance(row, dict)]
        if len(values) != 4 or not all(values) or len(set(values)) != 4:
            raise FreshPrivateMaterialError(
                f"planned inventory requires four distinct {field} values"
            )
    return payload, result


def telegram_read(
    token: str,
    method: str,
    payload: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Call one allowlisted Telegram identity lookup using HTTP GET."""

    if method not in {"getMe", "getChat", "getChatMember"}:
        raise FreshPrivateMaterialError("Telegram method is not read-only allowlisted")
    query = urllib.parse.urlencode(payload or {})
    suffix = f"?{query}" if query else ""
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/{method}{suffix}",
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            decoded = _strict_json(
                response.read(1024 * 1024 + 1).decode("utf-8"),
                label="Telegram response",
            )
    except Exception:
        raise FreshPrivateMaterialError("Telegram identity lookup failed") from None
    if set(decoded) < {"ok", "result"} or decoded.get("ok") is not True:
        raise FreshPrivateMaterialError("Telegram identity lookup response is invalid")
    result = decoded.get("result")
    if not isinstance(result, dict):
        raise FreshPrivateMaterialError("Telegram identity result is invalid")
    return result


def _verify_telegram_identities(
    provider: dict[str, str],
    *,
    telegram_reader: TelegramReader,
) -> dict[str, Any]:
    identities = {
        "primary": provider["BOT_TOKEN"],
        "editor": provider["TELEGRAM_DELIVERY_QUEUE_CHANNEL_EDITOR_BOT_TOKEN"],
    }
    records: dict[str, tuple[int, dict[str, Any]]] = {}
    invite_link = ""
    for role, token in identities.items():
        bot = telegram_reader(token, "getMe", None)
        bot_id = bot.get("id")
        if type(bot_id) is not int or bot_id <= 0 or bot.get("is_bot") is not True:
            raise FreshPrivateMaterialError("Telegram bot identity differs")
        if role == "primary" and (
            str(bot.get("username") or "").lstrip("@").lower()
            != provider["BOT_USERNAME"].lstrip("@").lower()
        ):
            raise FreshPrivateMaterialError("Telegram primary username differs")
        if role == "editor" and BOT_USERNAME_RE.fullmatch(
            str(bot.get("username") or "").lstrip("@")
        ) is None:
            raise FreshPrivateMaterialError("Telegram editor username differs")
        channel = telegram_reader(token, "getChat", {"chat_id": provider["CHANNEL_ID"]})
        if (
            str(channel.get("id") or "") != provider["CHANNEL_ID"]
            or channel.get("type") != "channel"
        ):
            raise FreshPrivateMaterialError("Telegram channel identity differs")
        if role == "primary":
            invite_link = str(channel.get("invite_link") or "")
            if not invite_link.startswith("https://t.me/") or any(
                character in invite_link for character in "\r\n\x00"
            ):
                raise FreshPrivateMaterialError("Telegram channel invite differs")
        member = telegram_reader(
            token,
            "getChatMember",
            {"chat_id": provider["CHANNEL_ID"], "user_id": str(bot_id)},
        )
        user = member.get("user")
        required_booleans = {
            "can_be_edited", "is_anonymous", "can_manage_chat", "can_delete_messages",
            "can_manage_video_chats", "can_restrict_members", "can_promote_members",
            "can_change_info", "can_invite_users", "can_post_stories",
            "can_edit_stories", "can_delete_stories",
        }
        if (
            not isinstance(user, dict)
            or user.get("id") != bot_id
            or user.get("is_bot") is not True
            or member.get("status") != "administrator"
            or member.get("is_anonymous") is not False
            or any(not isinstance(member.get(name), bool) for name in required_booleans)
        ):
            raise FreshPrivateMaterialError("Telegram membership proof differs")
        enabled = {
            name
            for name, value in member.items()
            if name.startswith("can_") and name != "can_be_edited" and value is True
        }
        if any(
            not isinstance(value, bool)
            for name, value in member.items()
            if name.startswith("can_")
        ) or "can_manage_chat" not in enabled:
            raise FreshPrivateMaterialError("Telegram administrator permissions differ")
        if role == "primary":
            if not {"can_post_messages", "can_edit_messages", "can_restrict_members"} <= enabled:
                raise FreshPrivateMaterialError("Telegram primary permissions differ")
        elif enabled != {"can_manage_chat", "can_edit_messages"}:
            raise FreshPrivateMaterialError("Telegram editor permissions are excessive")
        records[role] = (bot_id, member)
    primary_id = records["primary"][0]
    editor_id = records["editor"][0]
    if primary_id == editor_id:
        raise FreshPrivateMaterialError("Telegram bot identities must be distinct")
    return {
        "primary_id": primary_id,
        "editor_id": editor_id,
        "invite_link": invite_link,
    }


class _FreshSecretPool:
    def __init__(self, *, forbidden: set[str]) -> None:
        self._seen = set(forbidden)

    def token(self) -> str:
        for _attempt in range(32):
            value = secrets.token_urlsafe(48).rstrip("=")
            if (
                len(value.encode("utf-8")) >= 48
                and value not in self._seen
                and re.search(r"""[$\\'"#\s]""", value) is None
            ):
                self._seen.add(value)
                return value
        raise FreshPrivateMaterialError("cannot generate a distinct fresh secret")


def _certificate_material(
    *,
    now: datetime,
    write: Callable[[str, bytes, int], None],
) -> dict[str, str]:
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca_name = x509.Name(
        [x509.NameAttribute(NameOID.COMMON_NAME, "Trading Bot Three-Site Staging CA")]
    )
    ca_cert = (
        x509.CertificateBuilder()
        .subject_name(ca_name)
        .issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=120))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .sign(ca_key, hashes.SHA256())
    )
    write(
        "secrets/staging-dr-ca.key",
        ca_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ),
        0o600,
    )
    write(
        "secrets/staging-dr-ca.crt",
        ca_cert.public_bytes(serialization.Encoding.PEM),
        0o644,
    )
    fingerprints = {
        "ca_sha256": hashlib.sha256(
            ca_cert.public_bytes(serialization.Encoding.DER)
        ).hexdigest()
    }
    for role in ROLE_NAMES:
        stem, dns_name = ROLE_CERTIFICATES[role]
        key = ec.generate_private_key(ec.SECP256R1())
        cert = (
            x509.CertificateBuilder()
            .subject_name(
                x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, dns_name)])
            )
            .issuer_name(ca_name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=5))
            .not_valid_after(now + timedelta(days=90))
            .add_extension(
                x509.BasicConstraints(ca=False, path_length=None), critical=True
            )
            .add_extension(
                x509.SubjectAlternativeName([x509.DNSName(dns_name)]),
                critical=False,
            )
            .add_extension(
                x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]),
                critical=False,
            )
            .add_extension(
                x509.KeyUsage(
                    digital_signature=True,
                    content_commitment=False,
                    key_encipherment=False,
                    data_encipherment=False,
                    key_agreement=True,
                    key_cert_sign=False,
                    crl_sign=False,
                    encipher_only=False,
                    decipher_only=False,
                ),
                critical=True,
            )
            .sign(ca_key, hashes.SHA256())
        )
        write(
            f"secrets/{stem}.key",
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            ),
            0o600,
        )
        write(
            f"secrets/{stem}.crt",
            cert.public_bytes(serialization.Encoding.PEM),
            0o644,
        )
        fingerprints[f"{stem}_sha256"] = hashlib.sha256(
            cert.public_bytes(serialization.Encoding.DER)
        ).hexdigest()
    return fingerprints


def _pairwise_json(
    entries: dict[str, dict[str, str]], *names: str
) -> str:
    return json.dumps(
        [entries[name] for name in names],
        sort_keys=True,
        separators=(",", ":"),
    )


def _safe_template_literals(template_values: dict[str, str]) -> dict[str, str]:
    expected = (
        FIXED_LITERAL_ENV_NAMES | INTENTIONALLY_EMPTY_ENV_NAMES | DYNAMIC_ENV_NAMES
    )
    if set(template_values) != expected:
        raise FreshPrivateMaterialError(
            "exact-release environment template has an unclassified shape"
        )
    values = {name: template_values[name] for name in FIXED_LITERAL_ENV_NAMES}
    if any(
        not value
        or "change_me" in value.lower()
        or any(character in value for character in "\x00\r\n")
        for value in values.values()
    ):
        raise FreshPrivateMaterialError("exact-release literal environment is unsafe")
    if any(template_values[name] != "" for name in INTENTIONALLY_EMPTY_ENV_NAMES):
        raise FreshPrivateMaterialError("exact-release disabled environment is nonempty")
    values.update({name: "" for name in INTENTIONALLY_EMPTY_ENV_NAMES})
    return values


def _role_file_closure(
    *,
    role_env: dict[str, dict[str, str]],
    runtime_secret_root: str,
) -> dict[str, list[str]]:
    root = f"{runtime_secret_root}/"
    result: dict[str, list[str]] = {}
    for role, values in role_env.items():
        files = {f"roles/{role}.compose.yml", f"roles/{role}.env"}
        for value in values.values():
            if value.startswith(root):
                filename = value.removeprefix(root)
                if "/" in filename or not filename:
                    raise FreshPrivateMaterialError("role references an unsafe secret path")
                files.add(f"secrets/{filename}")
        result[role] = sorted(files)
    return result


def _verify_generated_crypto(
    *,
    payloads: dict[str, bytes],
    role_env: dict[str, dict[str, str]],
    now: datetime,
) -> dict[str, str]:
    """Reject generated cryptographic material that does not close on itself."""

    try:
        ca_key = serialization.load_pem_private_key(
            payloads["secrets/staging-dr-ca.key"], password=None
        )
        ca_cert = x509.load_pem_x509_certificate(payloads["secrets/staging-dr-ca.crt"])
        basic = ca_cert.extensions.get_extension_for_class(x509.BasicConstraints).value
        usage = ca_cert.extensions.get_extension_for_class(x509.KeyUsage).value
        if (
            not isinstance(ca_key, ec.EllipticCurvePrivateKey)
            or not basic.ca
            or not usage.key_cert_sign
            or not usage.crl_sign
            or ca_cert.not_valid_after.replace(tzinfo=timezone.utc) < now + timedelta(days=30)
        ):
            raise ValueError
        if ca_key.public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
        ) != ca_cert.public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
        ):
            raise ValueError
        fingerprints = {
            "ca_sha256": hashlib.sha256(ca_cert.public_bytes(serialization.Encoding.DER)).hexdigest()
        }
        serials = {ca_cert.serial_number}
        for role, (stem, dns_name) in ROLE_CERTIFICATES.items():
            key = serialization.load_pem_private_key(payloads[f"secrets/{stem}.key"], password=None)
            cert = x509.load_pem_x509_certificate(payloads[f"secrets/{stem}.crt"])
            san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
            eku = cert.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
            leaf_basic = cert.extensions.get_extension_for_class(x509.BasicConstraints).value
            if (
                not isinstance(key, ec.EllipticCurvePrivateKey)
                or cert.issuer != ca_cert.subject
                or cert.serial_number in serials
                or leaf_basic.ca
                or set(san.get_values_for_type(x509.DNSName)) != {dns_name}
                or ExtendedKeyUsageOID.SERVER_AUTH not in eku
                or cert.not_valid_after.replace(tzinfo=timezone.utc) < now + timedelta(days=30)
            ):
                raise ValueError
            ca_cert.public_key().verify(
                cert.signature, cert.tbs_certificate_bytes,
                ec.ECDSA(cert.signature_hash_algorithm),
            )
            if key.public_key().public_bytes(
                serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
            ) != cert.public_key().public_bytes(
                serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
            ):
                raise ValueError
            serials.add(cert.serial_number)
            fingerprints[f"{stem}_sha256"] = hashlib.sha256(
                cert.public_bytes(serialization.Encoding.DER)
            ).hexdigest()
        witness_private = base64.b64decode(
            payloads["secrets/witness-ed25519-private.key"].strip(), validate=True
        )
        witness = ed25519.Ed25519PrivateKey.from_private_bytes(witness_private)
        witness_public = base64.b64encode(witness.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        )).decode("ascii")
        witness_values = [
            values["WRITER_WITNESS_PUBLIC_KEY"]
            for values in role_env.values()
            if "WRITER_WITNESS_PUBLIC_KEY" in values
        ]
        if not witness_values or any(value != witness_public for value in witness_values):
            raise ValueError
        keyring = _strict_json(
            payloads["secrets/staging-dr-blob-keyring.json"].decode("utf-8"),
            label="generated blob keyring",
        )
        key_id = keyring.get("active_key_id")
        if (
            keyring.get("schema") != "trading-bot-dr-blob-keyring-v1"
            or not isinstance(key_id, str)
            or set(keyring) != {"schema", "active_key_id", "keys"}
            or set(keyring.get("keys") or {}) != {key_id}
            or len(base64.b64decode(keyring["keys"][key_id], validate=True)) != 32
        ):
            raise ValueError
        return fingerprints
    except Exception:
        raise FreshPrivateMaterialError("generated cryptographic material is invalid") from None


def verify_fresh_private_material_manifest(root: Path) -> dict[str, Any]:
    """Verify one generated material directory before it can be packaged."""

    try:
        tree = read_secure_material_tree(root)
        manifest_bytes, manifest_mode = tree["material-manifest.json"]
        if manifest_mode != 0o600:
            raise FreshPrivateMaterialError("material manifest mode is unsafe")
        manifest = _strict_json(
            manifest_bytes.decode("utf-8"), label="material manifest"
        )
    except (KeyError, UnicodeDecodeError, FreshCampaignSecureIOError) as exc:
        raise FreshPrivateMaterialError("material manifest is unavailable or unsafe") from exc
    if manifest.get("schema") != "three-site-staging-private-material-manifest-v4":
        raise FreshPrivateMaterialError("material manifest schema is invalid")
    files = manifest.get("files")
    role_files = manifest.get("role_files")
    controller_only = manifest.get("controller_only_files")
    if not isinstance(files, dict) or not isinstance(role_files, dict):
        raise FreshPrivateMaterialError("material manifest file inventory is invalid")
    if controller_only != sorted(CONTROLLER_ONLY_FILES) or set(role_files) != set(ROLE_NAMES):
        raise FreshPrivateMaterialError("material manifest role topology is invalid")
    if set(tree) != set(files) | {"material-manifest.json"}:
        raise FreshPrivateMaterialError("material manifest file closure is invalid")
    for relative, metadata in files.items():
        if not isinstance(metadata, dict) or relative not in tree:
            raise FreshPrivateMaterialError("material manifest file entry is invalid")
        payload, mode = tree[relative]
        if metadata != {
            "mode": f"{mode:04o}",
            "sha256": hashlib.sha256(payload).hexdigest(),
            "size": len(payload),
        }:
            raise FreshPrivateMaterialError("material manifest file digest differs")
    all_role_files = set().union(*(set(value) for value in role_files.values()))
    if not all(isinstance(value, list) and value == sorted(set(value)) for value in role_files.values()):
        raise FreshPrivateMaterialError("material manifest role files are invalid")
    if set(files) != all_role_files | set(CONTROLLER_ONLY_FILES):
        raise FreshPrivateMaterialError("material manifest leaks or omits material")
    for role, entries in role_files.items():
        env = parse_env_values(tree[f"roles/{role}.env"][0].decode("utf-8"))
        expected = _role_file_closure(
            role_env={role: env},
            runtime_secret_root=str(manifest.get("runtime_secret_root") or ""),
        )[role]
        if entries != expected:
            raise FreshPrivateMaterialError("material manifest role secret closure differs")
    forbidden = {
        "bot-fi": {"secrets/staging-dr-blob-s3.json", "secrets/staging-dr-blob-keyring.json"},
        "witness": {"secrets/staging-dr-blob-s3.json", "secrets/staging-dr-blob-keyring.json"},
    }
    if any(forbidden[role] & set(role_files[role]) for role in forbidden):
        raise FreshPrivateMaterialError("material manifest assigns blob authority to a forbidden role")
    if any("secrets/staging-dr-ca.key" in entries for entries in role_files.values()):
        raise FreshPrivateMaterialError("material manifest assigns CA private key to a role")
    return manifest


def generate_fresh_private_material(
    *,
    provider_environment: Path,
    provider_s3: Path,
    planned_inventory: Path,
    output: Path,
    execution_class: str,
    telegram_reader: TelegramReader | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Create and atomically publish one fresh campaign material directory."""

    inventory, inventory_result = _load_planned_inventory(
        planned_inventory,
        execution_class=execution_class,
    )
    release = inventory_result["release_sha"]
    exact_release = prove_exact_git_release(
        repo_root=REPO_ROOT,
        release_sha=release,
        bound_files=(
            Path(__file__).resolve(),
            (REPO_ROOT / "scripts/fresh_campaign_secure_io.py").resolve(),
            (REPO_ROOT / "scripts/render_three_site_staging_role_compose.py").resolve(),
            (REPO_ROOT / "scripts/verify_three_site_staging_inventory.py").resolve(),
        ),
        blob_paths=TEMPLATE_BLOBS,
    )
    provider = _load_provider_environment(provider_environment)
    s3 = _load_s3_credentials(provider_s3)
    telegram = _verify_telegram_identities(
        provider,
        telegram_reader=telegram_read if telegram_reader is None else telegram_reader,
    )
    current = datetime.now(timezone.utc) if now is None else now
    if current.tzinfo is None:
        raise FreshPrivateMaterialError("generation time must include a timezone")
    current = current.astimezone(timezone.utc)

    files: dict[str, dict[str, Any]] = {}
    payloads: dict[str, bytes] = {}

    def write(relative: str, payload: bytes, mode: int) -> None:
        relative_path = Path(relative)
        if (
            relative_path.is_absolute()
            or ".." in relative_path.parts
            or relative in files
        ):
            raise FreshPrivateMaterialError("generated material path is unsafe or duplicate")
        transaction.write(relative, payload, mode=mode)
        payloads[relative] = payload
        files[relative] = {
            "mode": f"{mode:04o}",
            "sha256": hashlib.sha256(payload).hexdigest(),
            "size": len(payload),
        }

    try:
        transaction = SecureOutputDirectory(output)
    except FreshCampaignSecureIOError as exc:
        raise FreshPrivateMaterialError("private material output is unavailable") from None
    with transaction:
        transaction.mkdir("roles")
        transaction.mkdir("secrets")
        fingerprints = _certificate_material(now=current, write=write)
        forbidden_values = set(provider.values()) | set(s3.values())
        fresh = _FreshSecretPool(forbidden=forbidden_values)

        witness_key = ed25519.Ed25519PrivateKey.generate()
        witness_private = witness_key.private_bytes(
            serialization.Encoding.Raw,
            serialization.PrivateFormat.Raw,
            serialization.NoEncryption(),
        )
        witness_public = witness_key.public_key().public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )
        write(
            "secrets/witness-ed25519-private.key",
            base64.b64encode(witness_private) + b"\n",
            0o600,
        )
        write(
            "secrets/staging-dr-blob-s3.json",
            json.dumps(s3, sort_keys=True, separators=(",", ":")).encode("utf-8")
            + b"\n",
            0o600,
        )
        keyring = {
            "schema": "trading-bot-dr-blob-keyring-v1",
            "active_key_id": "campaign-v1",
            "keys": {
                "campaign-v1": base64.b64encode(secrets.token_bytes(32)).decode(
                    "ascii"
                )
            },
        }
        write(
            "secrets/staging-dr-blob-keyring.json",
            json.dumps(keyring, sort_keys=True, separators=(",", ":")).encode(
                "utf-8"
            )
            + b"\n",
            0o600,
        )

        try:
            template_values = parse_env_values(
                exact_release.blobs[TEMPLATE_BLOBS[0]].decode("utf-8")
            )
            canonical_compose = yaml.safe_load(exact_release.blobs[TEMPLATE_BLOBS[1]])
        except Exception:
            raise FreshPrivateMaterialError(
                "exact-release staging templates are invalid"
            ) from None
        if not isinstance(canonical_compose, dict):
            raise FreshPrivateMaterialError("canonical staging Compose is invalid")

        values = _safe_template_literals(template_values)
        for name in SECRET_ENV_NAMES:
            if name not in template_values:
                raise FreshPrivateMaterialError(
                    "exact-release environment secret shape is incomplete"
                )
            values[name] = fresh.token()

        campaign = inventory_result["campaign_id"]
        deployment = inventory_result["deployment_id"]
        storage = inventory["object_storage"]
        runtime_secret_root = (
            f"/etc/trading-bot-three-site/campaigns/{campaign}/{deployment}/secrets"
        )
        values.update(
            {
                "STAGING_RELEASE_SHA": release,
                "STAGING_SOURCE_ROOT": f"/srv/trading-bot-three-site/releases/{release}",
                "STAGING_STORAGE_NAMESPACE": inventory["compose_project_namespace"],
                "FRONTEND_URL": f"https://{inventory['canonical_domain']}",
                "PUBLIC_WEBAPP_URL": f"https://{inventory['canonical_domain']}",
                "BOT_TOKEN": provider["BOT_TOKEN"],
                "BOT_USERNAME": provider["BOT_USERNAME"],
                "CHANNEL_ID": provider["CHANNEL_ID"],
                "CHANNEL_INVITE_LINK": telegram["invite_link"],
                "TELEGRAM_DELIVERY_QUEUE_CHANNEL_EDITOR_ENABLED": "false",
                "TELEGRAM_DELIVERY_QUEUE_CHANNEL_EDITOR_BOT_TOKEN": provider[
                    "TELEGRAM_DELIVERY_QUEUE_CHANNEL_EDITOR_BOT_TOKEN"
                ],
                "TELEGRAM_DELIVERY_QUEUE_EXPECTED_PRIMARY_BOT_ID": str(
                    telegram["primary_id"]
                ),
                "TELEGRAM_DELIVERY_QUEUE_EXPECTED_CHANNEL_EDITOR_BOT_ID": str(
                    telegram["editor_id"]
                ),
                "TELEGRAM_DELIVERY_QUEUE_EXPECTED_CHANNEL_ID": provider["CHANNEL_ID"],
                "WRITER_WITNESS_PUBLIC_KEY": base64.b64encode(witness_public).decode(
                    "ascii"
                ),
                "DR_BLOB_OBJECT_ENDPOINT": S3_ENDPOINT,
                "DR_BLOB_OBJECT_REGION": S3_REGION,
                "DR_BLOB_OBJECT_BUCKET": storage["bucket"],
                "DR_BLOB_OBJECT_PREFIX": f"{storage['prefix']}blobs/sha256",
                "DR_BLOB_REQUIRE_VERSIONING": "true",
                "STAGING_HUMAN_APPROVAL_RELAY_ENABLED": "false",
                "STAGING_HUMAN_APPROVAL_RELAY_MATERIAL_DIR": "/dev/null",
                "STAGING_HUMAN_APPROVAL_RELAY_ORCHESTRATOR_KEY_ID": "",
                "STAGING_HUMAN_APPROVAL_RELAY_ORCHESTRATOR_SECRET": "",
            }
        )
        for name, filename in RUNTIME_SECRET_KEYS.items():
            values[name] = f"{runtime_secret_root}/{filename}"

        by_role = {str(role["role"]): role for role in inventory["roles"]}
        values.update(
            {
                "BOT_FI_DR_BIND_ADDRESS": by_role["bot_fi"]["host_ip"],
                "WEBAPP_FI_DR_BIND_ADDRESS": by_role["webapp_fi"]["host_ip"],
                "WEBAPP_IR_DR_BIND_ADDRESS": by_role["webapp_ir"]["host_ip"],
                "WITNESS_DR_BIND_ADDRESS": by_role["witness"]["host_ip"],
                "BOT_FI_PEER_WEBAPP_FI_IP": by_role["webapp_fi"]["host_ip"],
                "WEBAPP_FI_PEER_BOT_FI_IP": by_role["bot_fi"]["host_ip"],
                "WEBAPP_FI_PEER_WEBAPP_IR_IP": by_role["webapp_ir"]["host_ip"],
                "WEBAPP_FI_WITNESS_IP": by_role["witness"]["host_ip"],
                "WEBAPP_IR_PEER_WEBAPP_FI_IP": by_role["webapp_fi"]["host_ip"],
                "WEBAPP_IR_WITNESS_IP": by_role["witness"]["host_ip"],
            }
        )

        campaign_short = campaign.replace("-", "")[:12]
        values["WEBAPP_FI_WITNESS_KEY_ID"] = (
            f"wfi-{campaign_short}-{secrets.token_hex(6)}"
        )
        values["WEBAPP_IR_WITNESS_KEY_ID"] = (
            f"wir-{campaign_short}-{secrets.token_hex(6)}"
        )
        pairwise: dict[str, dict[str, str]] = {}
        for label, source, destination in PAIRWISE_DIRECTIONS:
            pairwise[label] = {
                "key_id": f"{label}-{campaign_short}-{secrets.token_hex(4)}",
                "source_site": source,
                "destination_site": destination,
                "secret": fresh.token(),
            }
        values["BOT_FI_DR_PAIRWISE_KEYS_JSON"] = _pairwise_json(
            pairwise, "bot-to-fi", "fi-to-bot"
        )
        values["WEBAPP_FI_DR_PAIRWISE_KEYS_JSON"] = _pairwise_json(
            pairwise, "bot-to-fi", "fi-to-bot", "fi-to-ir", "ir-to-fi"
        )
        values["WEBAPP_IR_DR_PAIRWISE_KEYS_JSON"] = _pairwise_json(
            pairwise, "fi-to-ir", "ir-to-fi"
        )

        if any(
            "change_me" in value.lower()
            or "\x00" in value
            or "\r" in value
            or "\n" in value
            for value in values.values()
        ):
            raise FreshPrivateMaterialError(
                "generated environment contains an unsafe or placeholder value"
            )

        role_env: dict[str, dict[str, str]] = {}
        for role in ROLE_NAMES:
            role_compose = render_role_compose(
                canonical_compose,
                role=role,
                project_namespace=inventory["compose_project_namespace"],
            )
            compose_bytes = canonical_role_compose_bytes(role_compose)
            role_values = dict(values)
            role_values["STAGING_DATA_ROOT"] = by_role[
                ROLE_INVENTORY_NAMES[role]
            ]["storage_root"]
            env_bytes = canonical_role_env_bytes(
                role_values,
                required_names=referenced_environment_names(role_compose),
            )
            write(f"roles/{role}.compose.yml", compose_bytes, 0o640)
            write(f"roles/{role}.env", env_bytes, 0o600)
            role_env[role] = parse_env_values(env_bytes.decode("utf-8"))

        if _verify_generated_crypto(
            payloads=payloads,
            role_env=role_env,
            now=current,
        ) != fingerprints:
            raise FreshPrivateMaterialError("generated certificate fingerprints differ")
        role_files = _role_file_closure(
            role_env=role_env,
            runtime_secret_root=runtime_secret_root,
        )
        all_role_files = set().union(*(set(value) for value in role_files.values()))
        if set(files) != all_role_files | set(CONTROLLER_ONLY_FILES):
            raise FreshPrivateMaterialError("role material file closure is incomplete")

        manifest = {
            "schema": "three-site-staging-private-material-manifest-v4",
            "created_at": current.isoformat(),
            "release_sha": release,
            "campaign_id": campaign,
            "deployment_id": deployment,
            "execution_class": execution_class,
            "inventory_sha256": hashlib.sha256(
                _canonical_bytes(inventory)
            ).hexdigest(),
            "runtime_secret_root": runtime_secret_root,
            "template_blobs": dict(sorted(exact_release.blob_sha256.items())),
            "controller_only_files": sorted(CONTROLLER_ONLY_FILES),
            "role_files": role_files,
            "object_storage": {
                "endpoint": S3_ENDPOINT,
                "region": S3_REGION,
                "bucket": storage["bucket"],
                "prefix": storage["prefix"],
                "credential_id": storage["credential_id"],
                "private": True,
                "versioned": True,
            },
            "provider_inputs": {
                "telegram_fields": sorted(TELEGRAM_FIELDS),
                "arvan_s3_fields": sorted(S3_FIELDS),
                "telegram_identity_verified_read_only": True,
            },
            "certificate_fingerprints": fingerprints,
            "witness_public_key_sha256": hashlib.sha256(witness_public).hexdigest(),
            "freshness": {
                "tls_ca_and_role_keys_generated": True,
                "writer_witness_signing_key_generated": True,
                "blob_keyring_generated": True,
                "database_application_pairwise_secrets_generated": True,
                "old_campaign_secret_material_copied": False,
                "approval_material_generated": False,
                "human_approval_relay_material_generated": False,
                "seed_age_identity_generated": False,
            },
            "files": dict(sorted(files.items())),
        }
        manifest_bytes = (
            json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode(
                "utf-8"
            )
            + b"\n"
        )
        write("material-manifest.json", manifest_bytes, 0o600)
        transaction.publish(before_publish=exact_release.recheck)

    verify_fresh_private_material_manifest(output)

    return {
        "status": "fresh-private-material-created",
        "output": str(output),
        "release_sha": inventory_result["release_sha"],
        "campaign_id": inventory_result["campaign_id"],
        "deployment_id": inventory_result["deployment_id"],
        "execution_class": execution_class,
        "inventory_sha256": hashlib.sha256(_canonical_bytes(inventory)).hexdigest(),
        "material_manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "provider_identities_verified_read_only": True,
        "secret_values_printed": False,
        "seed_age_identity_generated": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider-environment", type=Path, required=True)
    parser.add_argument("--provider-s3", type=Path, required=True)
    parser.add_argument("--planned-inventory", type=Path, required=True)
    parser.add_argument("--execution-class", choices=sorted(EXECUTION_CLASSES), required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = generate_fresh_private_material(
            provider_environment=args.provider_environment,
            provider_s3=args.provider_s3,
            planned_inventory=args.planned_inventory,
            output=args.output,
            execution_class=args.execution_class,
        )
    except Exception as exc:
        print(
            json.dumps(
                {"status": "blocked", "error_class": type(exc).__name__},
                sort_keys=True,
            )
        )
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
