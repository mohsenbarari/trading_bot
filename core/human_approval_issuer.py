"""Isolated issuer for password + TOTP action-bound human approvals."""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import stat
import struct
from typing import Any
from urllib.parse import quote, urlencode
from uuid import UUID, uuid4

from cryptography.hazmat.primitives import serialization
from cryptography.exceptions import InvalidSignature, InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from core.canonical_json import canonical_json_bytes
from core.human_approval import (
    ALLOWED_AUTHENTICATION_METHODS,
    POLICY_SCHEMA,
    TOKEN_SCHEMA,
    HumanApprovalError,
    approval_policy_hash,
    load_human_approval_policy,
    validate_approval_subject,
)
from core.secure_file_io import (
    append_hash_chained_jsonl,
    read_secure_text,
    write_secure_atomic_bytes,
)


SECRETS_SCHEMA = "three-site-human-approval-secrets-v1"
STATE_SCHEMA = "three-site-human-approval-state-v1"
BOOTSTRAP_SCHEMA = "three-site-human-approval-bootstrap-receipt-v1"
PRIVATE_KEY_ENVELOPE_SCHEMA = "three-site-human-approval-private-key-envelope-v1"
DEFAULT_SCRYPT_N = 2**17
DEFAULT_SCRYPT_R = 8
DEFAULT_SCRYPT_P = 1
DEFAULT_SCRYPT_MAXMEM = 256 * 1024 * 1024
DEFAULT_TOKEN_TTL_SECONDS = 600
TOTP_PERIOD_SECONDS = 30
TOTP_DIGITS = 6
TOTP_DRIFT_STEPS = 1
MIN_PASSWORD_LENGTH = 16
RECOVERY_CODE_COUNT = 10
DEFAULT_ACTIONS = (
    {"action": "approve_inventory", "environments": ["staging"], "max_ttl_seconds": 86400},
    {"action": "approve_migration", "environments": ["staging"], "max_ttl_seconds": 14400},
    {"action": "start_full_matrix", "environments": ["staging"], "max_ttl_seconds": 600},
    {"action": "approve_gate_d", "environments": ["staging"], "max_ttl_seconds": 600},
    {"action": "run_writer_witness_matrix", "environments": ["staging"], "max_ttl_seconds": 600},
    {"action": "promote_ir", "environments": ["staging", "production"], "max_ttl_seconds": 600},
    {"action": "failback_fi", "environments": ["staging", "production"], "max_ttl_seconds": 600},
)


class HumanApprovalIssuerError(HumanApprovalError):
    """Raised for enrollment, authentication, or issuance failure."""


@dataclass(frozen=True)
class EnrollmentArtifacts:
    secrets_payload: dict[str, Any]
    policy_payload: dict[str, Any]
    state_payload: dict[str, Any]
    private_key_envelope: dict[str, Any]
    recovery_codes: tuple[str, ...]
    totp_secret: str
    otpauth_uri: str
    bootstrap_receipt: dict[str, Any]


def utc_iso(value: datetime) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise HumanApprovalIssuerError("approval timestamp must include a timezone")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def normalize_base32_secret(value: str) -> str:
    normalized = re.sub(r"[\s-]", "", value).upper().rstrip("=")
    if not normalized or re.fullmatch(r"[A-Z2-7]+", normalized) is None:
        raise HumanApprovalIssuerError("TOTP secret is invalid")
    return normalized


def totp_code(secret: str, *, at: datetime, counter_offset: int = 0) -> tuple[int, str]:
    if not isinstance(at, datetime) or at.tzinfo is None:
        raise HumanApprovalIssuerError("TOTP time must include a timezone")
    normalized = normalize_base32_secret(secret)
    padding = "=" * ((8 - len(normalized) % 8) % 8)
    try:
        key = base64.b32decode(normalized + padding, casefold=False)
    except ValueError as exc:
        raise HumanApprovalIssuerError("TOTP secret cannot be decoded") from exc
    counter = int(at.astimezone(timezone.utc).timestamp()) // TOTP_PERIOD_SECONDS + counter_offset
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    binary = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return counter, f"{binary % (10**TOTP_DIGITS):0{TOTP_DIGITS}d}"


def match_totp(secret: str, code: str, *, at: datetime, last_counter: int) -> int | None:
    normalized_code = str(code).strip()
    if re.fullmatch(rf"[0-9]{{{TOTP_DIGITS}}}", normalized_code) is None:
        return None
    matched: int | None = None
    for offset in range(-TOTP_DRIFT_STEPS, TOTP_DRIFT_STEPS + 1):
        counter, expected = totp_code(secret, at=at, counter_offset=offset)
        if hmac.compare_digest(expected, normalized_code) and counter > last_counter:
            matched = counter if matched is None else max(matched, counter)
    return matched


def _password_hash(
    password: str,
    *,
    salt: bytes,
    n: int = DEFAULT_SCRYPT_N,
    r: int = DEFAULT_SCRYPT_R,
    p: int = DEFAULT_SCRYPT_P,
) -> bytes:
    return hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=n,
        r=r,
        p=p,
        maxmem=DEFAULT_SCRYPT_MAXMEM,
        dklen=32,
    )


def _private_key_aad(policy_payload: dict[str, Any]) -> bytes:
    policy = load_human_approval_policy(policy_payload)
    return canonical_json_bytes(
        {
            "schema": PRIVATE_KEY_ENVELOPE_SCHEMA,
            "policy_id": policy.policy_id,
            "issuer_id": policy.issuer_id,
            "key_id": policy.key_id,
            "public_key_sha256": hashlib.sha256(policy.public_key).hexdigest(),
        }
    )


def encrypt_private_key(
    private_key_raw: bytes,
    *,
    password: str,
    policy_payload: dict[str, Any],
    scrypt_n: int,
) -> dict[str, Any]:
    if len(private_key_raw) != 32:
        raise HumanApprovalIssuerError("approval issuer private key is malformed")
    policy = load_human_approval_policy(policy_payload)
    salt = secrets.token_bytes(16)
    nonce = secrets.token_bytes(12)
    wrapping_key = _password_hash(password, salt=salt, n=scrypt_n)
    ciphertext = AESGCM(wrapping_key).encrypt(
        nonce, private_key_raw, _private_key_aad(policy_payload)
    )
    return {
        "schema": PRIVATE_KEY_ENVELOPE_SCHEMA,
        "policy_id": policy.policy_id,
        "issuer_id": policy.issuer_id,
        "key_id": policy.key_id,
        "public_key_sha256": hashlib.sha256(policy.public_key).hexdigest(),
        "kdf": {
            "algorithm": "scrypt-sha256",
            "n": scrypt_n,
            "r": DEFAULT_SCRYPT_R,
            "p": DEFAULT_SCRYPT_P,
            "salt": base64.b64encode(salt).decode(),
        },
        "cipher": {
            "algorithm": "AES-256-GCM",
            "nonce": base64.b64encode(nonce).decode(),
            "ciphertext": base64.b64encode(ciphertext).decode(),
        },
    }


def decrypt_private_key(
    payload: Any,
    *,
    password: str,
    policy_payload: dict[str, Any],
) -> bytes:
    policy = load_human_approval_policy(policy_payload)
    fields = {
        "schema", "policy_id", "issuer_id", "key_id", "public_key_sha256",
        "kdf", "cipher",
    }
    if (
        not isinstance(payload, dict)
        or set(payload) != fields
        or payload.get("schema") != PRIVATE_KEY_ENVELOPE_SCHEMA
        or payload.get("policy_id") != policy.policy_id
        or payload.get("issuer_id") != policy.issuer_id
        or payload.get("key_id") != policy.key_id
        or payload.get("public_key_sha256")
        != hashlib.sha256(policy.public_key).hexdigest()
    ):
        raise HumanApprovalIssuerError("approval private-key envelope identity is invalid")
    kdf = payload.get("kdf")
    cipher = payload.get("cipher")
    if (
        not isinstance(kdf, dict)
        or set(kdf) != {"algorithm", "n", "r", "p", "salt"}
        or kdf.get("algorithm") != "scrypt-sha256"
        or type(kdf.get("n")) is not int
        or kdf["n"] < 2**14
        or kdf["n"] > DEFAULT_SCRYPT_N
        or kdf["n"] & (kdf["n"] - 1)
        or kdf.get("r") != DEFAULT_SCRYPT_R
        or kdf.get("p") != DEFAULT_SCRYPT_P
        or not isinstance(cipher, dict)
        or set(cipher) != {"algorithm", "nonce", "ciphertext"}
        or cipher.get("algorithm") != "AES-256-GCM"
    ):
        raise HumanApprovalIssuerError("approval private-key envelope parameters are invalid")
    try:
        salt = base64.b64decode(str(kdf["salt"]), validate=True)
        nonce = base64.b64decode(str(cipher["nonce"]), validate=True)
        ciphertext = base64.b64decode(str(cipher["ciphertext"]), validate=True)
    except (ValueError, binascii.Error) as exc:
        raise HumanApprovalIssuerError(
            "approval private-key envelope encoding is invalid"
        ) from exc
    if len(salt) != 16 or len(nonce) != 12 or len(ciphertext) != 48:
        raise HumanApprovalIssuerError("approval private-key envelope material is malformed")
    wrapping_key = _password_hash(
        password,
        salt=salt,
        n=kdf["n"],
        r=kdf["r"],
        p=kdf["p"],
    )
    try:
        private_key_raw = AESGCM(wrapping_key).decrypt(
            nonce, ciphertext, _private_key_aad(policy_payload)
        )
    except InvalidTag as exc:
        raise HumanApprovalIssuerError(
            "approval private key cannot be decrypted with authenticated material"
        ) from exc
    if len(private_key_raw) != 32:
        raise HumanApprovalIssuerError("approval issuer private key is malformed")
    return private_key_raw


def validate_new_password(password: str) -> None:
    if (
        not isinstance(password, str)
        or len(password) < MIN_PASSWORD_LENGTH
        or len(password) > 256
        or password != password.strip("\r\n")
        or any(ord(character) < 32 for character in password)
    ):
        raise HumanApprovalIssuerError(
            f"approval password must be {MIN_PASSWORD_LENGTH}-256 printable characters"
        )


def _password_record_material(password_record: Any) -> tuple[bytes, bytes]:
    fields = {"algorithm", "n", "r", "p", "salt", "digest"}
    if (
        not isinstance(password_record, dict)
        or set(password_record) != fields
        or password_record.get("algorithm") != "scrypt-sha256"
        or type(password_record.get("n")) is not int
        or password_record["n"] < 2**14
        or password_record["n"] > DEFAULT_SCRYPT_N
        or password_record["n"] & (password_record["n"] - 1)
        or type(password_record.get("r")) is not int
        or not 1 <= password_record["r"] <= 32
        or type(password_record.get("p")) is not int
        or not 1 <= password_record["p"] <= 16
    ):
        raise HumanApprovalIssuerError("password verifier configuration is invalid")
    try:
        salt = base64.b64decode(str(password_record["salt"]), validate=True)
        expected = base64.b64decode(str(password_record["digest"]), validate=True)
    except (ValueError, binascii.Error) as exc:
        raise HumanApprovalIssuerError("password verifier encoding is invalid") from exc
    if len(salt) != 16 or len(expected) != 32:
        raise HumanApprovalIssuerError("password verifier material is malformed")
    return salt, expected


def verify_password(password: str, password_record: Any) -> bool:
    salt, expected = _password_record_material(password_record)
    actual = _password_hash(
        password,
        salt=salt,
        n=password_record["n"],
        r=password_record["r"],
        p=password_record["p"],
    )
    return hmac.compare_digest(actual, expected)


def _recovery_digest(code: str, salt: bytes) -> str:
    normalized = re.sub(r"[\s-]", "", str(code)).upper()
    if re.fullmatch(r"GT[A-Z2-7]{20}", normalized) is None:
        raise HumanApprovalIssuerError("recovery code format is invalid")
    return hmac.new(salt, normalized.encode("ascii"), hashlib.sha256).hexdigest()


def _new_recovery_code() -> str:
    raw = base64.b32encode(secrets.token_bytes(13)).decode().rstrip("=")[:20]
    return "GT-" + "-".join(raw[index : index + 5] for index in range(0, 20, 5))


def create_enrollment(
    *,
    operator: str,
    password: str,
    now: datetime,
    issuer_id: str = "three-site-witness-approval-service",
    label: str = "GoldTrade Three-Site Control",
    scrypt_n: int = DEFAULT_SCRYPT_N,
) -> EnrollmentArtifacts:
    operator = str(operator).strip()
    issuer_id = str(issuer_id).strip()
    if now.tzinfo is None:
        raise HumanApprovalIssuerError("enrollment time must include a timezone")
    if not operator or not issuer_id:
        raise HumanApprovalIssuerError("operator and issuer identity are required")
    validate_new_password(password)
    if (
        scrypt_n < 2**14
        or scrypt_n > DEFAULT_SCRYPT_N
        or scrypt_n & (scrypt_n - 1)
    ):
        raise HumanApprovalIssuerError("scrypt work factor is invalid")

    policy_id = str(uuid4())
    key_id = f"witness-approval-{now.astimezone(timezone.utc):%Y%m%d}"
    authenticator_id = str(uuid4())
    private = Ed25519PrivateKey.generate()
    private_raw = private.private_bytes(
        serialization.Encoding.Raw,
        serialization.PrivateFormat.Raw,
        serialization.NoEncryption(),
    )
    public_raw = private.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    totp_secret = base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")
    password_salt = secrets.token_bytes(16)
    password_digest = _password_hash(password, salt=password_salt, n=scrypt_n)
    recovery_salt = secrets.token_bytes(32)
    recovery_codes = tuple(_new_recovery_code() for _ in range(RECOVERY_CODE_COUNT))
    recovery_hashes = [_recovery_digest(code, recovery_salt) for code in recovery_codes]
    policy = {
        "schema": POLICY_SCHEMA,
        "policy_id": policy_id,
        "issuer": {
            "issuer_id": issuer_id,
            "key_id": key_id,
            "operator": operator,
            "authenticator_id": authenticator_id,
            "public_key": base64.b64encode(public_raw).decode(),
        },
        "actions": [dict(value) for value in DEFAULT_ACTIONS],
    }
    load_human_approval_policy(policy)
    private_key_envelope = encrypt_private_key(
        private_raw,
        password=password,
        policy_payload=policy,
        scrypt_n=scrypt_n,
    )
    secret_payload = {
        "schema": SECRETS_SCHEMA,
        "policy_id": policy_id,
        "issuer_id": issuer_id,
        "key_id": key_id,
        "operator": operator,
        "authenticator_id": authenticator_id,
        "password": {
            "algorithm": "scrypt-sha256",
            "n": scrypt_n,
            "r": DEFAULT_SCRYPT_R,
            "p": DEFAULT_SCRYPT_P,
            "salt": base64.b64encode(password_salt).decode(),
            "digest": base64.b64encode(password_digest).decode(),
        },
        "totp": {
            "algorithm": "SHA1",
            "digits": TOTP_DIGITS,
            "period": TOTP_PERIOD_SECONDS,
            "secret": totp_secret,
        },
        "recovery": {
            "salt": base64.b64encode(recovery_salt).decode(),
            "digests": recovery_hashes,
        },
    }
    state = {
        "schema": STATE_SCHEMA,
        "last_totp_counter": -1,
        "failed_attempts": 0,
        "locked_until": None,
        "used_recovery_digests": [],
        "issued_sequence": 0,
    }
    receipt_unsigned = {
        "schema": BOOTSTRAP_SCHEMA,
        "bootstrap_id": str(uuid4()),
        "authorized_at": utc_iso(now),
        "operator": operator,
        "decision": "replace-two-device-human-signing-with-password-plus-totp",
        "legacy_human_approval_status": "superseded",
        "new_policy_hash": approval_policy_hash(policy),
        "issuer_id": issuer_id,
        "key_id": key_id,
    }
    receipt = {
        **receipt_unsigned,
        "signature": base64.b64encode(
            private.sign(canonical_json_bytes(receipt_unsigned))
        ).decode(),
    }
    account_name = quote(operator, safe="")
    query = urlencode(
        {
            "secret": totp_secret,
            "issuer": label,
            "algorithm": "SHA1",
            "digits": str(TOTP_DIGITS),
            "period": str(TOTP_PERIOD_SECONDS),
        }
    )
    return EnrollmentArtifacts(
        secrets_payload=secret_payload,
        policy_payload=policy,
        state_payload=state,
        private_key_envelope=private_key_envelope,
        recovery_codes=recovery_codes,
        totp_secret=totp_secret,
        otpauth_uri=f"otpauth://totp/{quote(label, safe='')}:{account_name}?{query}",
        bootstrap_receipt=receipt,
    )


def parse_secrets(payload: Any, *, policy: dict[str, Any]) -> dict[str, Any]:
    fields = {
        "schema", "policy_id", "issuer_id", "key_id", "operator",
        "authenticator_id", "password", "totp", "recovery",
    }
    if (
        not isinstance(payload, dict)
        or set(payload) != fields
        or payload.get("schema") != SECRETS_SCHEMA
    ):
        raise HumanApprovalIssuerError("approval issuer secrets are invalid")
    parsed_policy = load_human_approval_policy(policy)
    for field in ("policy_id", "issuer_id", "key_id", "operator", "authenticator_id"):
        expected = getattr(parsed_policy, field)
        if payload.get(field) != expected:
            raise HumanApprovalIssuerError("approval issuer secrets do not match public policy")
    totp = payload.get("totp")
    if (
        not isinstance(totp, dict)
        or set(totp) != {"algorithm", "digits", "period", "secret"}
        or totp.get("algorithm") != "SHA1"
        or totp.get("digits") != TOTP_DIGITS
        or totp.get("period") != TOTP_PERIOD_SECONDS
    ):
        raise HumanApprovalIssuerError("approval TOTP configuration is invalid")
    normalize_base32_secret(str(totp["secret"]))
    recovery = payload.get("recovery")
    if not isinstance(recovery, dict) or set(recovery) != {"salt", "digests"}:
        raise HumanApprovalIssuerError("approval recovery configuration is invalid")
    try:
        recovery_salt = base64.b64decode(str(recovery["salt"]), validate=True)
    except (ValueError, binascii.Error) as exc:
        raise HumanApprovalIssuerError("approval recovery salt is invalid") from exc
    if (
        len(recovery_salt) != 32
        or not isinstance(recovery["digests"], list)
        or len(recovery["digests"]) != RECOVERY_CODE_COUNT
        or len(set(recovery["digests"])) != RECOVERY_CODE_COUNT
        or any(re.fullmatch(r"[0-9a-f]{64}", str(value)) is None for value in recovery["digests"])
    ):
        raise HumanApprovalIssuerError("approval recovery material is malformed")
    _password_record_material(payload["password"])
    return payload


def parse_state(payload: Any) -> dict[str, Any]:
    fields = {
        "schema", "last_totp_counter", "failed_attempts", "locked_until",
        "used_recovery_digests", "issued_sequence",
    }
    if (
        not isinstance(payload, dict)
        or set(payload) != fields
        or payload.get("schema") != STATE_SCHEMA
        or type(payload.get("last_totp_counter")) is not int
        or payload["last_totp_counter"] < -1
        or type(payload.get("failed_attempts")) is not int
        or payload["failed_attempts"] < 0
        or type(payload.get("issued_sequence")) is not int
        or payload["issued_sequence"] < 0
        or not isinstance(payload.get("used_recovery_digests"), list)
        or len(set(payload["used_recovery_digests"])) != len(payload["used_recovery_digests"])
        or any(re.fullmatch(r"[0-9a-f]{64}", str(value)) is None for value in payload["used_recovery_digests"])
    ):
        raise HumanApprovalIssuerError("approval issuer state is invalid")
    locked = payload.get("locked_until")
    if locked is not None:
        try:
            parsed = datetime.fromisoformat(str(locked).replace("Z", "+00:00"))
        except ValueError as exc:
            raise HumanApprovalIssuerError("approval lock timestamp is invalid") from exc
        if parsed.tzinfo is None:
            raise HumanApprovalIssuerError("approval lock timestamp lacks timezone")
    return payload


def verify_bootstrap_receipt(
    payload: Any, *, policy_payload: dict[str, Any]
) -> dict[str, Any]:
    fields = {
        "schema", "bootstrap_id", "authorized_at", "operator", "decision",
        "legacy_human_approval_status", "new_policy_hash", "issuer_id",
        "key_id", "signature",
    }
    policy = load_human_approval_policy(policy_payload)
    if (
        not isinstance(payload, dict)
        or set(payload) != fields
        or payload.get("schema") != BOOTSTRAP_SCHEMA
        or payload.get("operator") != policy.operator
        or payload.get("issuer_id") != policy.issuer_id
        or payload.get("key_id") != policy.key_id
        or payload.get("new_policy_hash") != policy.policy_hash
        or payload.get("decision")
        != "replace-two-device-human-signing-with-password-plus-totp"
        or payload.get("legacy_human_approval_status") != "superseded"
    ):
        raise HumanApprovalIssuerError("approval bootstrap receipt is invalid")
    try:
        UUID(str(payload["bootstrap_id"]))
        authorized = datetime.fromisoformat(
            str(payload["authorized_at"]).replace("Z", "+00:00")
        )
        if authorized.tzinfo is None:
            raise ValueError("timezone is required")
        signature = base64.b64decode(str(payload["signature"]), validate=True)
        unsigned = {key: value for key, value in payload.items() if key != "signature"}
        Ed25519PublicKey.from_public_bytes(policy.public_key).verify(
            signature, canonical_json_bytes(unsigned)
        )
    except (ValueError, binascii.Error, InvalidSignature) as exc:
        raise HumanApprovalIssuerError("approval bootstrap receipt signature is invalid") from exc
    return payload


def verify_issuer_audit(
    records: Any,
    *,
    bootstrap_receipt: dict[str, Any],
    policy_payload: dict[str, Any],
    state_payload: dict[str, Any],
) -> None:
    """Bind the audit chain, bootstrap receipt, and mutable issuer state."""

    policy = load_human_approval_policy(policy_payload)
    state = parse_state(state_payload)
    if not isinstance(records, list) or not records:
        raise HumanApprovalIssuerError("approval issuer audit is empty")
    enrollment = records[0]
    enrollment_fields = {
        "event", "occurred_at", "operator", "policy_hash",
        "bootstrap_receipt_sha256", "previous_hash", "event_hash",
    }
    if (
        not isinstance(enrollment, dict)
        or set(enrollment) != enrollment_fields
        or enrollment.get("event") != "human_approval_enrolled"
        or enrollment.get("occurred_at") != bootstrap_receipt.get("authorized_at")
        or enrollment.get("operator") != policy.operator
        or enrollment.get("policy_hash") != policy.policy_hash
        or enrollment.get("bootstrap_receipt_sha256")
        != hashlib.sha256(canonical_json_bytes(bootstrap_receipt)).hexdigest()
    ):
        raise HumanApprovalIssuerError(
            "approval issuer audit does not match its bootstrap receipt"
        )

    issued_sequence = 0
    consecutive_failures = 0
    approval_ids: set[str] = set()
    for record in records[1:]:
        event = record.get("event") if isinstance(record, dict) else None
        if event == "human_approval_authentication_failed":
            expected_fields = {
                "event", "occurred_at", "operator", "action", "environment",
                "failed_attempts", "previous_hash", "event_hash",
            }
            consecutive_failures += 1
            if (
                set(record) != expected_fields
                or record.get("operator") != policy.operator
                or record.get("action") not in policy.actions
                or record.get("environment")
                not in policy.actions[record["action"]].environments
                or record.get("failed_attempts") != consecutive_failures
            ):
                raise HumanApprovalIssuerError(
                    "approval issuer failure audit is inconsistent"
                )
        elif event == "human_approval_issued":
            expected_fields = {
                "event", "occurred_at", "operator", "approval_id", "action",
                "environment", "subject_sha256", "token_sha256",
                "authentication_methods", "issued_sequence", "previous_hash",
                "event_hash",
            }
            issued_sequence += 1
            consecutive_failures = 0
            try:
                approval_id = str(UUID(str(record.get("approval_id"))))
            except ValueError as exc:
                raise HumanApprovalIssuerError(
                    "approval issuer issuance audit id is invalid"
                ) from exc
            methods = record.get("authentication_methods")
            if (
                set(record) != expected_fields
                or approval_id in approval_ids
                or record.get("operator") != policy.operator
                or record.get("action") not in policy.actions
                or record.get("environment")
                not in policy.actions[record["action"]].environments
                or re.fullmatch(r"[0-9a-f]{64}", str(record.get("subject_sha256"))) is None
                or re.fullmatch(r"[0-9a-f]{64}", str(record.get("token_sha256"))) is None
                or not isinstance(methods, list)
                or tuple(methods) not in ALLOWED_AUTHENTICATION_METHODS
                or record.get("issued_sequence") != issued_sequence
            ):
                raise HumanApprovalIssuerError(
                    "approval issuer issuance audit is inconsistent"
                )
            approval_ids.add(approval_id)
        else:
            raise HumanApprovalIssuerError("approval issuer audit has an unknown event")
    if (
        state["issued_sequence"] != issued_sequence
        or state["failed_attempts"] != consecutive_failures
    ):
        raise HumanApprovalIssuerError(
            "approval issuer state does not match its durable audit"
        )


def _failure_state(state: dict[str, Any], *, now: datetime) -> dict[str, Any]:
    result = dict(state)
    attempts = int(state["failed_attempts"]) + 1
    result["failed_attempts"] = attempts
    if attempts >= 5:
        delay = min(3600, 30 * (2 ** min(attempts - 5, 7)))
        result["locked_until"] = utc_iso(now + timedelta(seconds=delay))
    return result


def authenticate_and_issue(
    *,
    secrets_payload: dict[str, Any],
    state_payload: dict[str, Any],
    policy_payload: dict[str, Any],
    private_key_envelope: dict[str, Any],
    password: str,
    totp: str | None,
    recovery_code: str | None,
    action: str,
    environment: str,
    subject: dict[str, Any],
    ttl_seconds: int,
    now: datetime,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Authenticate, issue a token, and return token/new-state/audit-event."""

    if now.tzinfo is None:
        raise HumanApprovalIssuerError("approval issuance time must include a timezone")
    parsed_policy = load_human_approval_policy(policy_payload)
    secrets_payload = parse_secrets(secrets_payload, policy=policy_payload)
    state_payload = parse_state(state_payload)
    action_policy = parsed_policy.actions.get(action)
    if (
        action_policy is None
        or environment not in action_policy.environments
        or type(ttl_seconds) is not int
        or not 30 <= ttl_seconds <= action_policy.max_ttl_seconds
    ):
        raise HumanApprovalIssuerError("requested approval action/lifetime is not allowed")
    if (totp is None) == (recovery_code is None):
        raise HumanApprovalIssuerError("provide exactly one TOTP or recovery code")
    try:
        subject = validate_approval_subject(subject)
    except HumanApprovalError as exc:
        raise HumanApprovalIssuerError("approval subject is invalid") from exc
    locked_until = state_payload.get("locked_until")
    if locked_until is not None:
        lock_time = datetime.fromisoformat(str(locked_until).replace("Z", "+00:00")).astimezone(timezone.utc)
        if now.astimezone(timezone.utc) < lock_time:
            raise HumanApprovalIssuerError("approval authentication is temporarily locked")

    password_valid = verify_password(password, secrets_payload["password"])
    methods: tuple[str, str]
    matched_counter: int | None = None
    matched_recovery: str | None = None
    if totp is not None:
        methods = ("password", "totp")
        matched_counter = match_totp(
            str(secrets_payload["totp"]["secret"]),
            totp,
            at=now,
            last_counter=state_payload["last_totp_counter"],
        )
        factor_valid = matched_counter is not None
    else:
        methods = ("password", "recovery_code")
        recovery_salt = base64.b64decode(secrets_payload["recovery"]["salt"], validate=True)
        try:
            candidate = _recovery_digest(str(recovery_code), recovery_salt)
        except HumanApprovalIssuerError:
            # Keep all credential failures on the same generic, rate-limited
            # path; input formatting must not become an account oracle.
            candidate = hmac.new(
                recovery_salt, b"invalid-recovery-code", hashlib.sha256
            ).hexdigest()
        matched_recovery = next(
            (
                digest for digest in secrets_payload["recovery"]["digests"]
                if hmac.compare_digest(candidate, digest)
            ),
            None,
        )
        factor_valid = (
            matched_recovery is not None
            and matched_recovery not in state_payload["used_recovery_digests"]
        )
    if not (password_valid and factor_valid):
        failed = _failure_state(state_payload, now=now)
        return {}, failed, {
            "event": "human_approval_authentication_failed",
            "occurred_at": utc_iso(now),
            "operator": parsed_policy.operator,
            "action": action,
            "environment": environment,
            "failed_attempts": failed["failed_attempts"],
        }

    private_key_raw = decrypt_private_key(
        private_key_envelope,
        password=password,
        policy_payload=policy_payload,
    )
    private = Ed25519PrivateKey.from_private_bytes(private_key_raw)
    derived_public = private.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    if not hmac.compare_digest(derived_public, parsed_policy.public_key):
        raise HumanApprovalIssuerError("approval issuer private key does not match policy")
    issued = now.astimezone(timezone.utc)
    token_unsigned = {
        "schema": TOKEN_SCHEMA,
        "approval_id": str(uuid4()),
        "policy_id": parsed_policy.policy_id,
        "policy_hash": parsed_policy.policy_hash,
        "issuer_id": parsed_policy.issuer_id,
        "key_id": parsed_policy.key_id,
        "operator": parsed_policy.operator,
        "authenticator_id": parsed_policy.authenticator_id,
        "action": action,
        "environment": environment,
        "subject": json.loads(canonical_json_bytes(subject)),
        "issued_at": utc_iso(issued),
        "expires_at": utc_iso(issued + timedelta(seconds=ttl_seconds)),
        "authentication": {"methods": list(methods)},
    }
    token = {
        **token_unsigned,
        "signature": base64.b64encode(
            private.sign(canonical_json_bytes(token_unsigned))
        ).decode(),
    }
    new_state = dict(state_payload)
    new_state["failed_attempts"] = 0
    new_state["locked_until"] = None
    new_state["issued_sequence"] = int(state_payload["issued_sequence"]) + 1
    if matched_counter is not None:
        new_state["last_totp_counter"] = matched_counter
    if matched_recovery is not None:
        new_state["used_recovery_digests"] = [
            *state_payload["used_recovery_digests"], matched_recovery
        ]
    return token, new_state, {
        "event": "human_approval_issued",
        "occurred_at": utc_iso(now),
        "operator": parsed_policy.operator,
        "approval_id": token["approval_id"],
        "action": action,
        "environment": environment,
        "subject_sha256": hashlib.sha256(canonical_json_bytes(subject)).hexdigest(),
        "token_sha256": hashlib.sha256(canonical_json_bytes(token)).hexdigest(),
        "authentication_methods": list(methods),
        "issued_sequence": new_state["issued_sequence"],
    }


def secure_json(path: Path, *, label: str) -> dict[str, Any]:
    def strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise HumanApprovalIssuerError(f"{label} contains a duplicate key")
            result[key] = value
        return result

    try:
        value = json.loads(
            read_secure_text(path, label=label, max_size=1024 * 1024),
            object_pairs_hook=strict_object,
        )
    except (json.JSONDecodeError, OSError) as exc:
        raise HumanApprovalIssuerError(f"{label} is invalid") from exc
    if not isinstance(value, dict):
        raise HumanApprovalIssuerError(f"{label} must be a JSON object")
    return value


def write_enrollment(directory: Path, artifacts: EnrollmentArtifacts) -> None:
    """Persist a brand-new enrollment; existing material is never overwritten."""

    paths = {
        "secrets": directory / "issuer-secrets.json",
        "key": directory / "issuer-ed25519.key.enc.json",
        "policy": directory / "human-approval-policy.json",
        "state": directory / "issuer-state.json",
        "receipt": directory / "bootstrap-receipt.json",
        "audit": directory / "approval-audit.jsonl",
    }
    if not directory.is_absolute():
        raise HumanApprovalIssuerError("approval enrollment directory must be absolute")
    if directory.exists() or any(path.exists() for path in paths.values()):
        raise HumanApprovalIssuerError(
            "approval enrollment directory already exists; rotation requires a separate ceremony"
        )
    parent_flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        parent_descriptor = os.open(directory.parent, parent_flags)
    except OSError as exc:
        raise HumanApprovalIssuerError(
            "approval enrollment parent must already exist as a real directory"
        ) from exc
    try:
        parent_metadata = os.fstat(parent_descriptor)
        if (
            not stat.S_ISDIR(parent_metadata.st_mode)
            or parent_metadata.st_uid != os.geteuid()
            or stat.S_IMODE(parent_metadata.st_mode) & 0o022
        ):
            raise HumanApprovalIssuerError(
                "approval enrollment parent is not owner-controlled"
            )
        directory.mkdir(parents=False, mode=0o700)
        os.fsync(parent_descriptor)
    finally:
        os.close(parent_descriptor)
    os.chmod(directory, 0o700)
    documents = {
        paths["secrets"]: artifacts.secrets_payload,
        paths["policy"]: artifacts.policy_payload,
        paths["state"]: artifacts.state_payload,
        paths["receipt"]: artifacts.bootstrap_receipt,
    }
    for path, payload in documents.items():
        write_secure_atomic_bytes(
            path,
            (json.dumps(payload, sort_keys=True, indent=2) + "\n").encode(),
            label=path.name,
            mode=0o600,
        )
    write_secure_atomic_bytes(
        paths["key"],
        (json.dumps(artifacts.private_key_envelope, sort_keys=True, indent=2) + "\n").encode(),
        label="encrypted approval issuer private key",
        mode=0o600,
    )
    append_hash_chained_jsonl(
        paths["audit"],
        {
            "event": "human_approval_enrolled",
            "occurred_at": artifacts.bootstrap_receipt["authorized_at"],
            "operator": artifacts.policy_payload["issuer"]["operator"],
            "policy_hash": approval_policy_hash(artifacts.policy_payload),
            "bootstrap_receipt_sha256": hashlib.sha256(
                canonical_json_bytes(artifacts.bootstrap_receipt)
            ).hexdigest(),
        },
    )


def issuer_paths(directory: Path) -> dict[str, Path]:
    return {
        "secrets": directory / "issuer-secrets.json",
        "key": directory / "issuer-ed25519.key.enc.json",
        "policy": directory / "human-approval-policy.json",
        "state": directory / "issuer-state.json",
        "receipt": directory / "bootstrap-receipt.json",
        "audit": directory / "approval-audit.jsonl",
        "lock": directory / "issuer.lock",
    }
