#!/usr/bin/env python3
"""Transfer one exact staging image bundle directly from Bot-FI to WebApp-FI."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shlex
import subprocess
import sys
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import yaml

from core.secure_file_io import read_secure_bytes, write_secure_new_bytes
from core.three_site_transport_policy import (
    ThreeSiteTransportPolicyError,
    verify_payload_transport,
)
from scripts.verify_three_site_staging_image_inventory import (
    verify_image_document,
)
from scripts.verify_three_site_staging_inventory import (
    load_inventory,
    verify_inventory,
)
from scripts.verify_three_site_staging_role_bundle import (
    _verify_bundle_source,
    verify_role_bundle,
)


SCHEMA = "three-site-staging-fi-image-transfer-v1"
SOURCE_ROLE = "bot_fi"
DESTINATION_ROLE = "webapp_fi"
MAX_BUNDLE_BYTES = 2 * 1024 * 1024 * 1024
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
RELEASE_RE = re.compile(r"^[0-9a-f]{40}$")
MACHINE_ID_RE = re.compile(r"^[0-9a-f]{32}$")


class FinlandImageTransferError(RuntimeError):
    pass


def confirmation_phrase(
    campaign_id: str, release_sha: str, bundle_sha256: str
) -> str:
    return (
        f"transfer-fi-images:{campaign_id}:{release_sha}:{bundle_sha256}"
    )


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _digest(path: Path, *, maximum: int) -> tuple[str, int]:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise FinlandImageTransferError("image bundle is unreadable") from exc
    if path.is_symlink() or not path.is_file() or metadata.st_nlink != 1:
        raise FinlandImageTransferError("image bundle is not one safe regular file")
    digest = hashlib.sha256()
    size = 0
    try:
        with path.open("rb") as source:
            while chunk := source.read(1024 * 1024):
                size += len(chunk)
                if size > maximum:
                    raise FinlandImageTransferError("image bundle exceeds size bound")
                digest.update(chunk)
    except OSError as exc:
        raise FinlandImageTransferError("image bundle read failed") from exc
    if size < 1:
        raise FinlandImageTransferError("image bundle is empty")
    return digest.hexdigest(), size


def _strict_json(raw: bytes, *, label: str) -> dict[str, Any]:
    def hook(pairs):  # noqa: ANN001
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise FinlandImageTransferError(f"{label} contains a duplicate key")
            result[key] = value
        return result

    try:
        value = json.loads(raw, object_pairs_hook=hook)
    except (json.JSONDecodeError, FinlandImageTransferError) as exc:
        raise FinlandImageTransferError(f"{label} is not strict JSON") from exc
    if not isinstance(value, dict):
        raise FinlandImageTransferError(f"{label} must contain one JSON object")
    return value


def verify_transfer_evidence(
    document: dict[str, Any],
    *,
    campaign_id: str,
    release_sha: str,
    source_host_ip: str,
    destination_host_ip: str,
    bundle_sha256: str,
    bundle_bytes: int,
    inventory_sha256: str,
) -> dict[str, Any]:
    fields = {
        "schema", "created_at", "status", "campaign_id", "release_sha",
        "source_role", "destination_role", "source_host_ip",
        "destination_host_ip", "bundle", "transport",
        "remote_image_inventory_sha256", "database_restarted",
        "application_started",
    }
    bundle_fields = {"sha256", "bytes", "remote_retained_until_cleanup"}
    transport_fields = {
        "kind", "encrypted", "resumable", "strict_host_key_checking",
        "object_storage_used", "arvan_endpoint_contacted",
    }
    if (
        not isinstance(document, dict)
        or set(document) != fields
        or document.get("schema") != SCHEMA
        or document.get("status") != "loaded-attested-and-returned"
        or document.get("campaign_id") != campaign_id
        or document.get("release_sha") != release_sha
        or document.get("source_role") != SOURCE_ROLE
        or document.get("destination_role") != DESTINATION_ROLE
        or document.get("source_host_ip") != source_host_ip
        or document.get("destination_host_ip") != destination_host_ip
        or document.get("remote_image_inventory_sha256") != inventory_sha256
        or document.get("database_restarted") is not False
        or document.get("application_started") is not False
    ):
        raise FinlandImageTransferError("direct-transfer evidence identity is invalid")
    bundle = document.get("bundle")
    transport = document.get("transport")
    if (
        not isinstance(bundle, dict)
        or set(bundle) != bundle_fields
        or bundle.get("sha256") != bundle_sha256
        or bundle.get("bytes") != bundle_bytes
        or bundle.get("remote_retained_until_cleanup") is not True
        or not isinstance(transport, dict)
        or set(transport) != transport_fields
        or transport.get("encrypted") is not True
        or transport.get("resumable") is not True
        or transport.get("strict_host_key_checking") is not True
        or transport.get("object_storage_used") is not False
        or transport.get("arvan_endpoint_contacted") is not False
        or transport
        != {
            "kind": "direct-rsync-over-ssh-finland",
            "encrypted": True,
            "resumable": True,
            "strict_host_key_checking": True,
            "object_storage_used": False,
            "arvan_endpoint_contacted": False,
        }
    ):
        raise FinlandImageTransferError("direct-transfer transport contract is invalid")
    try:
        verify_payload_transport(
            source_role=SOURCE_ROLE,
            destination_role=DESTINATION_ROLE,
            transport=str(transport["kind"]),
            object_storage_used=bool(transport["object_storage_used"]),
            arvan_endpoint_contacted=bool(transport["arvan_endpoint_contacted"]),
        )
    except ThreeSiteTransportPolicyError as exc:
        raise FinlandImageTransferError(
            "direct-transfer regional policy verification failed"
        ) from exc
    try:
        created_at = datetime.fromisoformat(
            str(document["created_at"]).replace("Z", "+00:00")
        )
    except ValueError as exc:
        raise FinlandImageTransferError(
            "direct-transfer evidence timestamp is invalid"
        ) from exc
    if created_at.tzinfo is None:
        raise FinlandImageTransferError(
            "direct-transfer evidence timestamp lacks timezone"
        )
    return {
        "status": "verified",
        "document_sha256": hashlib.sha256(_canonical_bytes(document)).hexdigest(),
    }


def _run(
    arguments: list[str],
    *,
    timeout: int,
    stdin: bytes | None = None,
) -> bytes:
    try:
        result = subprocess.run(
            arguments,
            input=stdin,
            capture_output=True,
            check=False,
            timeout=timeout,
            env={
                "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
                "HOME": "/root",
                "LANG": "C.UTF-8",
                "LC_ALL": "C.UTF-8",
                "RSYNC_RSH": "",
            },
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise FinlandImageTransferError(
            f"transfer command unavailable: {Path(arguments[0]).name}"
        ) from exc
    if result.returncode:
        raise FinlandImageTransferError(
            f"transfer command failed closed: {Path(arguments[0]).name}"
        )
    return result.stdout


def _ssh_base(*, identity: Path, host: str, user: str) -> list[str]:
    return [
        "/usr/bin/ssh",
        "-o", "BatchMode=yes",
        "-o", "ConnectTimeout=8",
        "-o", "IdentitiesOnly=yes",
        "-o", "StrictHostKeyChecking=yes",
        "-o", "LogLevel=ERROR",
        "-i", str(identity),
        f"{user}@{host}",
    ]


def _ssh(
    *,
    identity: Path,
    host: str,
    user: str,
    arguments: list[str],
    timeout: int,
) -> bytes:
    remote = " ".join(shlex.quote(value) for value in arguments)
    return _run(
        [*_ssh_base(identity=identity, host=host, user=user), remote],
        timeout=timeout,
    )


def _role(inventory: dict[str, Any], name: str) -> dict[str, Any]:
    try:
        return next(item for item in inventory["roles"] if item["role"] == name)
    except StopIteration as exc:
        raise FinlandImageTransferError(
            f"signed inventory lacks required role: {name}"
        ) from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--canonical-compose", type=Path, required=True)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--inventory-approval", type=Path, required=True)
    parser.add_argument("--approval-policy", type=Path, required=True)
    parser.add_argument("--bot-image-inventory", type=Path, required=True)
    parser.add_argument("--roles-dir", type=Path, required=True)
    parser.add_argument("--remote-campaign-root", type=Path, required=True)
    parser.add_argument("--remote-repo", type=Path, required=True)
    parser.add_argument("--ssh-user", default="root")
    parser.add_argument("--ssh-identity", type=Path, required=True)
    parser.add_argument("--output-inventory", type=Path, required=True)
    parser.add_argument("--output-evidence", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm")
    args = parser.parse_args(argv)
    try:
        if args.output_inventory.exists() or args.output_evidence.exists():
            raise FinlandImageTransferError("direct-transfer output already exists")
        inventory = load_inventory(args.inventory)
        verify_inventory(inventory)
        if inventory.get("inventory_stage") != "provisioned":
            raise FinlandImageTransferError(
                "direct image transfer requires provisioned inventory"
            )
        release_sha = str(inventory["release_sha"])
        campaign_id = str(inventory["campaign_id"])
        if not RELEASE_RE.fullmatch(release_sha):
            raise FinlandImageTransferError("release SHA is malformed")
        source = _role(inventory, SOURCE_ROLE)
        destination = _role(inventory, DESTINATION_ROLE)
        if not MACHINE_ID_RE.fullmatch(str(destination["machine_id"])):
            raise FinlandImageTransferError("destination machine identity is malformed")
        expected_campaign_root = Path(
            f"/root/secure-envs/trading-bot/three-site-staging-{release_sha[:8]}"
        )
        if (
            not args.remote_campaign_root.is_absolute()
            or args.remote_campaign_root != expected_campaign_root
            or args.remote_repo
            != Path("/srv/trading-bot-three-site/current")
            or args.canonical_compose
            != args.remote_repo / "deploy/staging/docker-compose.three-site.yml"
            or args.ssh_user != "root"
            or args.inventory
            != expected_campaign_root / "provisioned-inventory.json"
            or args.inventory_approval
            != expected_campaign_root / "provisioned-inventory-approval.json"
            or args.approval_policy
            != expected_campaign_root / "human-approval-policy.json"
            or args.bot_image_inventory
            != expected_campaign_root / "image-inventory-bot-fi.json"
            or args.roles_dir != expected_campaign_root / "roles"
            or args.output_inventory
            != expected_campaign_root / "image-inventory-webapp-fi.json"
            or args.output_evidence
            != expected_campaign_root / "webapp-fi-direct-image-transfer.json"
        ):
            raise FinlandImageTransferError("remote staging boundary is invalid")
        if (
            not args.ssh_identity.is_absolute()
            or args.ssh_identity.is_symlink()
            or not args.ssh_identity.is_file()
        ):
            raise FinlandImageTransferError("SSH identity is unsafe")

        bundle_sha256, bundle_bytes = _digest(
            args.bundle, maximum=MAX_BUNDLE_BYTES
        )
        roles_dir = args.roles_dir
        bot_compose = roles_dir / "bot-fi.compose.yml"
        bot_env = roles_dir / "bot-fi.env"
        webapp_compose = roles_dir / "webapp-fi.compose.yml"
        webapp_env = roles_dir / "webapp-fi.env"
        for path, label in (
            (bot_compose, "Bot-FI role Compose"),
            (bot_env, "Bot-FI role environment"),
            (webapp_compose, "WebApp-FI role Compose"),
            (webapp_env, "WebApp-FI role environment"),
        ):
            if not path.is_file() or path.is_symlink():
                raise FinlandImageTransferError(f"{label} is unsafe")
        canonical_compose = yaml.safe_load(
            args.canonical_compose.read_text(encoding="utf-8")
        )
        approval = load_inventory(args.inventory_approval)
        approval_policy = load_inventory(args.approval_policy)
        bot_bundle = verify_role_bundle(
            role="bot-fi",
            canonical_compose=canonical_compose,
            role_compose_bytes=_verify_bundle_source(
                bot_compose, expected_mode=0o640
            ),
            env_bytes=_verify_bundle_source(bot_env, expected_mode=0o600),
            inventory=inventory,
            approval=approval,
            approval_policy=approval_policy,
            verify_files=True,
            required_inventory_stage="provisioned",
        )
        webapp_bundle = verify_role_bundle(
            role="webapp-fi",
            canonical_compose=canonical_compose,
            role_compose_bytes=_verify_bundle_source(
                webapp_compose, expected_mode=0o640
            ),
            env_bytes=_verify_bundle_source(webapp_env, expected_mode=0o600),
            inventory=inventory,
            approval=approval,
            approval_policy=approval_policy,
            verify_files=True,
            required_inventory_stage="provisioned",
        )

        baseline_raw = read_secure_bytes(
            args.bot_image_inventory,
            label="Bot-FI image inventory",
            max_size=4 * 1024 * 1024,
        )
        baseline = _strict_json(baseline_raw, label="Bot-FI image inventory")
        baseline_verified = verify_image_document(
            baseline,
            role="bot-fi",
            campaign_id=campaign_id,
            release_sha=release_sha,
            role_compose_sha256=bot_bundle["compose_sha256"],
            role_env_sha256=bot_bundle["environment_sha256"],
        )

        host = str(destination["host_ip"])
        machine_id = str(destination["machine_id"])
        artifact_dir = (
            Path(str(destination["storage_root"]))
            / "artifacts"
            / release_sha[:8]
            / "direct-fi"
        )
        partial = artifact_dir / f".three-site-images-{release_sha[:8]}.tar.zst.partial"
        remote_bundle = artifact_dir / f"three-site-images-{release_sha[:8]}.tar.zst"
        remote_inventory = (
            args.remote_campaign_root / "image-inventory-webapp-fi.json"
        )

        prep = [
            "/usr/bin/env", "bash", "-lc",
            (
                "set -euo pipefail; "
                f"test \"$(tr -d '\\n' </etc/machine-id)\" = {shlex.quote(machine_id)}; "
                f"test \"$(git -C {shlex.quote(str(args.remote_repo))} rev-parse HEAD)\" = {shlex.quote(release_sha)}; "
                f"test -z \"$(git -C {shlex.quote(str(args.remote_repo))} status --porcelain)\"; "
                f"if test -e {shlex.quote(str(artifact_dir))}; then "
                f"test -d {shlex.quote(str(artifact_dir))}; "
                f"test ! -L {shlex.quote(str(artifact_dir))}; fi; "
                f"if test -f {shlex.quote(str(remote_bundle))}; then "
                f"test \"$(stat -c %s {shlex.quote(str(remote_bundle))})\" = {bundle_bytes}; "
                f"test \"$(sha256sum {shlex.quote(str(remote_bundle))} | cut -d' ' -f1)\" = {bundle_sha256}; "
                "echo present; "
                "else echo transfer; fi"
            ),
        ]
        prep_status = _ssh(
            identity=args.ssh_identity,
            host=host,
            user=args.ssh_user,
            arguments=prep,
            timeout=60,
        ).decode().strip()
        if prep_status not in {"present", "transfer"}:
            raise FinlandImageTransferError("remote transfer preflight is invalid")
        required_confirmation = confirmation_phrase(
            campaign_id, release_sha, bundle_sha256
        )
        if not args.apply:
            print(
                json.dumps(
                    {
                        "status": "planned",
                        "campaign_id": campaign_id,
                        "release_sha": release_sha,
                        "source_role": SOURCE_ROLE,
                        "destination_role": DESTINATION_ROLE,
                        "bundle_sha256": bundle_sha256,
                        "bundle_bytes": bundle_bytes,
                        "remote_state": prep_status,
                        "transport": "direct-rsync-over-ssh-finland",
                        "object_storage_used": False,
                        "required_confirmation": required_confirmation,
                    },
                    sort_keys=True,
                )
            )
            return 0
        if args.confirm != required_confirmation:
            raise FinlandImageTransferError(
                "direct Finland image transfer confirmation mismatch"
            )
        _ssh(
            identity=args.ssh_identity,
            host=host,
            user=args.ssh_user,
            arguments=[
                "/usr/bin/env", "bash", "-lc",
                (
                    "set -euo pipefail; "
                    f"install -d -m 0700 {shlex.quote(str(artifact_dir))}; "
                    f"test ! -L {shlex.quote(str(artifact_dir))}"
                ),
            ],
            timeout=60,
        )
        if prep_status == "transfer":
            ssh_command = " ".join(
                shlex.quote(value)
                for value in _ssh_base(
                    identity=args.ssh_identity,
                    host=host,
                    user=args.ssh_user,
                )[:-1]
            )
            _run(
                [
                    "/usr/bin/rsync",
                    "--archive",
                    "--partial",
                    "--append-verify",
                    "--protect-args",
                    "--chmod=F600",
                    "--timeout=300",
                    "-e", ssh_command,
                    str(args.bundle),
                    f"{args.ssh_user}@{host}:{partial}",
                ],
                timeout=7200,
            )

        finalize = [
            "/usr/bin/env", "bash", "-lc",
            (
                "set -euo pipefail; "
                f"if test ! -f {shlex.quote(str(remote_bundle))}; then "
                f"test -f {shlex.quote(str(partial))}; "
                f"test \"$(stat -c %s {shlex.quote(str(partial))})\" = {bundle_bytes}; "
                f"test \"$(sha256sum {shlex.quote(str(partial))} | cut -d' ' -f1)\" = {bundle_sha256}; "
                f"chmod 0600 {shlex.quote(str(partial))}; "
                f"mv -T {shlex.quote(str(partial))} {shlex.quote(str(remote_bundle))}; "
                "fi; "
                f"zstd -q -t {shlex.quote(str(remote_bundle))}; "
                f"zstd -q -d -c {shlex.quote(str(remote_bundle))} | docker load >/dev/null; "
                f"python3 {shlex.quote(str(args.remote_repo / 'scripts/verify_three_site_staging_image_inventory.py'))} "
                "--role webapp-fi "
                f"--canonical-compose {shlex.quote(str(args.remote_repo / 'deploy/staging/docker-compose.three-site.yml'))} "
                f"--role-compose {shlex.quote(str(args.remote_campaign_root / 'roles/webapp-fi.compose.yml'))} "
                f"--env-file {shlex.quote(str(args.remote_campaign_root / 'roles/webapp-fi.env'))} "
                f"--inventory {shlex.quote(str(args.remote_campaign_root / 'provisioned-inventory.json'))} "
                f"--inventory-approval {shlex.quote(str(args.remote_campaign_root / 'provisioned-inventory-approval.json'))} "
                f"--approval-policy {shlex.quote(str(args.remote_campaign_root / 'human-approval-policy.json'))} "
                f"--output {shlex.quote(str(remote_inventory))} >/dev/null"
            ),
        ]
        _ssh(
            identity=args.ssh_identity,
            host=host,
            user=args.ssh_user,
            arguments=finalize,
            timeout=9000,
        )
        remote_raw = _ssh(
            identity=args.ssh_identity,
            host=host,
            user=args.ssh_user,
            arguments=["/usr/bin/cat", str(remote_inventory)],
            timeout=60,
        )
        remote_document = _strict_json(
            remote_raw, label="WebApp-FI image inventory"
        )
        remote_verified = verify_image_document(
            remote_document,
            role="webapp-fi",
            campaign_id=campaign_id,
            release_sha=release_sha,
            role_compose_sha256=webapp_bundle["compose_sha256"],
            role_env_sha256=webapp_bundle["environment_sha256"],
        )
        baseline_identities = baseline_verified["content_identities"]
        if (
            remote_verified["content_identities"] != baseline_identities
            or remote_verified["image_count"] != baseline_verified["image_count"]
        ):
            raise FinlandImageTransferError(
                "WebApp-FI image content differs from Bot-FI baseline"
            )
        write_secure_new_bytes(
            args.output_inventory,
            remote_raw,
            label="WebApp-FI image inventory",
            max_size=4 * 1024 * 1024,
        )
        evidence = {
            "schema": SCHEMA,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "status": "loaded-attested-and-returned",
            "campaign_id": campaign_id,
            "release_sha": release_sha,
            "source_role": SOURCE_ROLE,
            "destination_role": DESTINATION_ROLE,
            "source_host_ip": str(source["host_ip"]),
            "destination_host_ip": host,
            "bundle": {
                "sha256": bundle_sha256,
                "bytes": bundle_bytes,
                "remote_retained_until_cleanup": True,
            },
            "transport": {
                "kind": "direct-rsync-over-ssh-finland",
                "encrypted": True,
                "resumable": True,
                "strict_host_key_checking": True,
                "object_storage_used": False,
                "arvan_endpoint_contacted": False,
            },
            "remote_image_inventory_sha256": hashlib.sha256(remote_raw).hexdigest(),
            "database_restarted": False,
            "application_started": False,
        }
        verified_evidence = verify_transfer_evidence(
            evidence,
            campaign_id=campaign_id,
            release_sha=release_sha,
            source_host_ip=str(source["host_ip"]),
            destination_host_ip=host,
            bundle_sha256=bundle_sha256,
            bundle_bytes=bundle_bytes,
            inventory_sha256=hashlib.sha256(remote_raw).hexdigest(),
        )
        write_secure_new_bytes(
            args.output_evidence,
            (json.dumps(evidence, indent=2, sort_keys=True) + "\n").encode(),
            label="direct Finland image transfer evidence",
            max_size=1024 * 1024,
        )
        print(
            json.dumps(
                {
                    "status": verified_evidence["status"],
                    "source_role": SOURCE_ROLE,
                    "destination_role": DESTINATION_ROLE,
                    "release_sha": release_sha,
                    "bundle_sha256": bundle_sha256,
                    "image_count": remote_verified["image_count"],
                    "transport": "direct-rsync-over-ssh-finland",
                    "object_storage_used": False,
                    "database_restarted": False,
                    "application_started": False,
                },
                sort_keys=True,
            )
        )
        return 0
    except Exception as exc:
        print(
            json.dumps(
                {
                    "status": "blocked",
                    "error": str(exc),
                    "error_class": type(exc).__name__,
                },
                sort_keys=True,
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
