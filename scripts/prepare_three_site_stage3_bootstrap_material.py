#!/usr/bin/env python3
"""Create fresh, owner-only bootstrap material for one approved Stage 3 campaign.

This command is deliberately local-only.  It generates role bundles and the
credentials/files needed to attest them, but it does not contact a host,
Object Storage, Docker, DNS, or a provider lifecycle API.
"""

from __future__ import annotations

import argparse
import base64
from datetime import datetime, timedelta, timezone
import hashlib
import ipaddress
import json
from pathlib import Path
import re
import secrets
import stat
import sys
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ed25519, rsa
from cryptography.x509.oid import NameOID
import yaml

from core.secure_file_io import read_secure_text, write_secure_new_bytes
from scripts.render_three_site_staging_role_compose import (
    _atomic_write,
    canonical_role_env_bytes,
    parse_env_values,
    referenced_environment_names,
    render_role_compose,
)
from scripts.verify_three_site_staging_inventory import (
    load_inventory,
    verify_approved_inventory,
)


ROLES = ("bot-fi", "webapp-fi", "webapp-ir", "witness")
ROLE_SITES = {
    "bot-fi": "bot_fi",
    "webapp-fi": "webapp_fi",
    "webapp-ir": "webapp_ir",
    "witness": "witness",
}
TLS_NAMES = {
    "bot-fi": "bot-fi-dr.staging.internal",
    "webapp-fi": "webapp-fi-dr.staging.internal",
    "webapp-ir": "webapp-ir-dr.staging.internal",
    "witness": "witness-dr.staging.internal",
}
DATABASE_SECRET_RE = re.compile(r"(?:_DB_PASSWORD|_POSTGRES_PASSWORD)$")


class BootstrapMaterialError(RuntimeError):
    pass


def _token() -> str:
    return secrets.token_hex(32)


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, indent=2) + "\n").encode()


def _read_s3_credentials(path: Path) -> dict[str, str]:
    raw = read_secure_text(path, label="Stage 3 Object Storage credentials", max_size=16384)
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        values = parse_env_values(raw)
        payload = {
            "access_key": values.get("ARVAN_S3_ACCESS_KEY", ""),
            "secret_key": values.get("ARVAN_S3_SECRET_KEY", ""),
        }
    if not isinstance(payload, dict):
        raise BootstrapMaterialError("Object Storage credentials must be an object or env file")
    access_key = str(payload.get("access_key") or "")
    secret_key = str(payload.get("secret_key") or "")
    if len(access_key) < 8 or len(secret_key) < 32:
        raise BootstrapMaterialError("Object Storage credentials are malformed")
    return {"access_key": access_key, "secret_key": secret_key}


def _pairwise(secret_by_pair: dict[tuple[str, str], str], pairs: tuple[tuple[str, str], ...]) -> str:
    return json.dumps(
        [
            {
                "key_id": f"{source.replace('_', '-')}-to-{destination.replace('_', '-')}-fd34231d",
                "source_site": source,
                "destination_site": destination,
                "secret": secret_by_pair[(source, destination)],
            }
            for source, destination in pairs
        ],
        separators=(",", ":"),
    )


def build_environment(
    template: str,
    *,
    inventory: dict[str, Any],
    source_root: Path,
    remote_material_root: Path,
    witness_public_key: str,
) -> dict[str, str]:
    values = parse_env_values(template)
    by_role = {item["role"]: item for item in inventory["roles"]}
    replacements = {
        "STAGING_RELEASE_SHA": inventory["release_sha"],
        "STAGING_SOURCE_ROOT": str(source_root),
        "STAGING_DATA_ROOT": "/srv/trading-bot-three-site-staging-data",
        "STAGING_STORAGE_NAMESPACE": inventory["compose_project_namespace"],
        "FRONTEND_URL": f"https://{inventory['canonical_domain']}",
        "PUBLIC_WEBAPP_URL": f"https://{inventory['canonical_domain']}",
        "DR_BLOB_OBJECT_BUCKET": inventory["object_storage"]["bucket"],
        "DR_BLOB_OBJECT_PREFIX": f"{inventory['object_storage']['prefix']}blobs/sha256",
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
        "WRITER_WITNESS_PUBLIC_KEY": witness_public_key,
        "STAGING_HUMAN_APPROVAL_RELAY_ENABLED": "false",
        "STAGING_HUMAN_APPROVAL_RELAY_SESSION_FILE": "/dev/null",
        "STAGING_HUMAN_APPROVAL_RELAY_POLICY_FILE": "/dev/null",
        "STAGING_HUMAN_APPROVAL_RELAY_ORCHESTRATOR_KEY_ID": "",
        "STAGING_HUMAN_APPROVAL_RELAY_ORCHESTRATOR_SECRET": "",
        "TELEGRAM_DELIVERY_PRODUCER_MODE": "legacy",
        "TELEGRAM_DELIVERY_EXPECTED_EXECUTION_OWNER": "legacy",
        "TELEGRAM_DELIVERY_EXECUTION_OWNER": "legacy",
        "TELEGRAM_DELIVERY_QUEUE_WORKER_ENABLED": "false",
        "TELEGRAM_DELIVERY_QUEUE_CUTOVER_READY": "false",
        "TELEGRAM_DELIVERY_QUEUE_CHANNEL_EDITOR_ENABLED": "false",
        "TELEGRAM_DELIVERY_QUEUE_CHANNEL_EDITOR_BOT_TOKEN": "",
        "TELEGRAM_DELIVERY_QUEUE_EXPECTED_CHANNEL_EDITOR_BOT_ID": "0",
        "DR_BLOB_REQUIRE_VERSIONING": "true",
        "STAGING_DR_BLOB_CREDENTIALS_FILE": str(remote_material_root / "secrets/staging-dr-blob-s3.json"),
        "STAGING_DR_BLOB_ENCRYPTION_KEYRING_FILE": str(remote_material_root / "secrets/staging-dr-blob-keyring.json"),
        "STAGING_DR_CA_CERT": str(remote_material_root / "secrets/staging-dr-ca.crt"),
        "STAGING_BOT_FI_TLS_CERT": str(remote_material_root / "secrets/bot-fi-dr.crt"),
        "STAGING_BOT_FI_TLS_KEY": str(remote_material_root / "secrets/bot-fi-dr.key"),
        "STAGING_WEBAPP_FI_TLS_CERT": str(remote_material_root / "secrets/webapp-fi-dr.crt"),
        "STAGING_WEBAPP_FI_TLS_KEY": str(remote_material_root / "secrets/webapp-fi-dr.key"),
        "STAGING_WEBAPP_IR_TLS_CERT": str(remote_material_root / "secrets/webapp-ir-dr.crt"),
        "STAGING_WEBAPP_IR_TLS_KEY": str(remote_material_root / "secrets/webapp-ir-dr.key"),
        "STAGING_WITNESS_TLS_CERT": str(remote_material_root / "secrets/witness-dr.crt"),
        "STAGING_WITNESS_TLS_KEY": str(remote_material_root / "secrets/witness-dr.key"),
        "STAGING_WITNESS_SIGNING_KEY": str(remote_material_root / "secrets/witness-ed25519-private.key"),
    }
    values.update(replacements)
    for name in values:
        if DATABASE_SECRET_RE.search(name):
            values[name] = _token()
    values["ORIGIN_READINESS_API_KEY"] = _token()
    values["BOT_FI_JWT_SECRET_KEY"] = _token()
    values["WEBAPP_JWT_SECRET_KEY"] = _token()
    values["WEBAPP_FI_WITNESS_KEY_ID"] = "stage3-webapp-fi-fd34231d"
    values["WEBAPP_IR_WITNESS_KEY_ID"] = "stage3-webapp-ir-fd34231d"
    values["WEBAPP_FI_WITNESS_SECRET"] = _token()
    values["WEBAPP_IR_WITNESS_SECRET"] = _token()
    values["BOT_TOKEN"] = f"disabled-stage3-{_token()}"
    values["BOT_USERNAME"] = f"stage3_disabled_{secrets.token_hex(8)}"
    values["CHANNEL_INVITE_LINK"] = f"https://t.me/+disabled-stage3-{secrets.token_hex(12)}"
    values["CHANNEL_ID"] = "-1000000000001"
    values["TELEGRAM_DELIVERY_QUEUE_EXPECTED_CHANNEL_ID"] = values["CHANNEL_ID"]
    values["TELEGRAM_DELIVERY_QUEUE_EXPECTED_PRIMARY_BOT_ID"] = "1"

    secret_by_pair = {
        pair: _token()
        for pair in (
            ("bot_fi", "webapp_fi"),
            ("webapp_fi", "bot_fi"),
            ("webapp_fi", "webapp_ir"),
            ("webapp_ir", "webapp_fi"),
        )
    }
    values["BOT_FI_DR_PAIRWISE_KEYS_JSON"] = _pairwise(
        secret_by_pair,
        (("bot_fi", "webapp_fi"), ("webapp_fi", "bot_fi")),
    )
    values["WEBAPP_FI_DR_PAIRWISE_KEYS_JSON"] = _pairwise(
        secret_by_pair,
        (
            ("bot_fi", "webapp_fi"),
            ("webapp_fi", "bot_fi"),
            ("webapp_fi", "webapp_ir"),
            ("webapp_ir", "webapp_fi"),
        ),
    )
    values["WEBAPP_IR_DR_PAIRWISE_KEYS_JSON"] = _pairwise(
        secret_by_pair,
        (("webapp_fi", "webapp_ir"), ("webapp_ir", "webapp_fi")),
    )
    if any("change_me" in value.lower() for value in values.values()):
        raise BootstrapMaterialError("bootstrap environment retains a template placeholder")
    database_values = [value for name, value in values.items() if DATABASE_SECRET_RE.search(name)]
    if len(database_values) != len(set(database_values)):
        raise BootstrapMaterialError("database credentials are not globally distinct")
    return values


def _create_tls(inventory: dict[str, Any]) -> tuple[dict[str, bytes], dict[str, str]]:
    now = datetime.now(timezone.utc)
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Trading Bot Stage 3 Internal CA")])
    ca_cert = (
        x509.CertificateBuilder()
        .subject_name(ca_name)
        .issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=45))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .sign(ca_key, hashes.SHA256())
    )
    files = {
        "staging-dr-ca.key": ca_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ),
        "staging-dr-ca.crt": ca_cert.public_bytes(serialization.Encoding.PEM),
    }
    fingerprints = {
        "staging-dr-ca.crt": ca_cert.fingerprint(hashes.SHA256()).hex(),
    }
    by_role = {item["role"]: item for item in inventory["roles"]}
    for role in ROLES:
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = TLS_NAMES[role]
        cert = (
            x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)]))
            .issuer_name(ca_name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=5))
            .not_valid_after(now + timedelta(days=30))
            .add_extension(
                x509.SubjectAlternativeName(
                    [
                        x509.DNSName(name),
                        x509.IPAddress(ipaddress.ip_address(by_role[ROLE_SITES[role]]["host_ip"])),
                    ]
                ),
                critical=False,
            )
            .sign(ca_key, hashes.SHA256())
        )
        stem = role
        files[f"{stem}-dr.key"] = key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        files[f"{stem}-dr.crt"] = cert.public_bytes(serialization.Encoding.PEM)
        fingerprints[f"{stem}-dr.crt"] = cert.fingerprint(hashes.SHA256()).hex()
    return files, fingerprints


def create_material(args: argparse.Namespace) -> dict[str, Any]:
    inventory = load_inventory(args.inventory)
    approval = load_inventory(args.approval)
    policy = load_inventory(args.approval_policy)
    approved = verify_approved_inventory(
        inventory,
        approval=approval,
        approval_policy=policy,
        host_destructive=True,
    )
    if approved["inventory_stage"] != "planned":
        raise BootstrapMaterialError("bootstrap material requires an approved planned inventory")
    if args.output_dir.exists():
        raise BootstrapMaterialError("bootstrap material output already exists")
    if not args.source_root.is_absolute() or not args.remote_material_root.is_absolute():
        raise BootstrapMaterialError("remote paths must be absolute")
    args.output_dir.mkdir(mode=0o700, parents=False)
    if stat.S_IMODE(args.output_dir.stat().st_mode) != 0o700:
        raise BootstrapMaterialError("bootstrap material directory must be mode 0700")

    s3 = _read_s3_credentials(args.object_storage_credentials)
    witness_key = ed25519.Ed25519PrivateKey.generate()
    witness_private = witness_key.private_bytes_raw()
    witness_public = base64.b64encode(witness_key.public_key().public_bytes_raw()).decode()
    tls_files, tls_fingerprints = _create_tls(inventory)
    secrets_dir = args.output_dir / "secrets"
    for name, payload in tls_files.items():
        write_secure_new_bytes(secrets_dir / name, payload, label=f"Stage 3 {name}")
    write_secure_new_bytes(
        secrets_dir / "witness-ed25519-private.key",
        base64.b64encode(witness_private) + b"\n",
        label="Stage 3 Witness signing key",
    )
    write_secure_new_bytes(
        secrets_dir / "staging-dr-blob-s3.json",
        _json_bytes(s3),
        label="Stage 3 Object Storage credential copy",
    )
    blob_key_id = "stage3-fd34231d-v1"
    write_secure_new_bytes(
        secrets_dir / "staging-dr-blob-keyring.json",
        _json_bytes(
            {
                "schema": "trading-bot-dr-blob-keyring-v1",
                "active_key_id": blob_key_id,
                "keys": {blob_key_id: base64.b64encode(secrets.token_bytes(32)).decode()},
            }
        ),
        label="Stage 3 blob encryption keyring",
    )

    template = args.env_template.read_text(encoding="utf-8")
    values = build_environment(
        template,
        inventory=inventory,
        source_root=args.source_root,
        remote_material_root=args.remote_material_root,
        witness_public_key=witness_public,
    )
    full_env = canonical_role_env_bytes(values, required_names=frozenset(values))
    write_secure_new_bytes(
        args.output_dir / "three-site-full.env",
        full_env,
        label="Stage 3 full environment",
    )
    canonical = yaml.safe_load(args.canonical_compose.read_text(encoding="utf-8"))
    role_hashes: dict[str, dict[str, str]] = {}
    roles_dir = args.output_dir / "roles"
    for role in ROLES:
        payload = render_role_compose(
            canonical,
            role=role,
            project_namespace=inventory["compose_project_namespace"],
        )
        compose_path = roles_dir / f"{role}.compose.yml"
        env_path = roles_dir / f"{role}.env"
        compose_bytes = yaml.safe_dump(
            payload,
            allow_unicode=True,
            default_flow_style=False,
            sort_keys=True,
        ).encode()
        env_bytes = canonical_role_env_bytes(
            values,
            required_names=referenced_environment_names(payload),
        )
        _atomic_write(compose_path, compose_bytes, mode=0o640)
        _atomic_write(env_path, env_bytes, mode=0o600)
        role_hashes[role] = {
            "compose_sha256": hashlib.sha256(compose_bytes).hexdigest(),
            "environment_sha256": hashlib.sha256(env_bytes).hexdigest(),
        }
    manifest = {
        "schema": "three-site-stage3-bootstrap-material-v1",
        "campaign_id": inventory["campaign_id"],
        "deployment_id": inventory["deployment_id"],
        "release_sha": inventory["release_sha"],
        "inventory_sha256": approved["inventory_sha256"],
        "approval_id": approved["approval_id"],
        "source_root": str(args.source_root),
        "remote_material_root": str(args.remote_material_root),
        "roles": role_hashes,
        "tls_certificate_fingerprints_sha256": tls_fingerprints,
        "witness_public_key_sha256": hashlib.sha256(base64.b64decode(witness_public)).hexdigest(),
        "object_storage_access_key_sha256": hashlib.sha256(s3["access_key"].encode()).hexdigest(),
        "telegram_runtime": "disabled-synthetic-bootstrap-values",
        "external_effects": False,
    }
    write_secure_new_bytes(
        args.output_dir / "bootstrap-material-manifest.json",
        _json_bytes(manifest),
        label="Stage 3 bootstrap material manifest",
    )
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--approval", type=Path, required=True)
    parser.add_argument("--approval-policy", type=Path, required=True)
    parser.add_argument("--canonical-compose", type=Path, required=True)
    parser.add_argument("--env-template", type=Path, required=True)
    parser.add_argument("--object-storage-credentials", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--remote-material-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = create_material(args)
    except Exception as exc:
        print(
            json.dumps(
                {"status": "blocked", "error": str(exc), "error_class": type(exc).__name__},
                sort_keys=True,
            )
        )
        return 1
    print(
        json.dumps(
            {
                "status": "created",
                "campaign_id": result["campaign_id"],
                "release_sha": result["release_sha"],
                "output_dir": str(args.output_dir),
                "external_effects": False,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
