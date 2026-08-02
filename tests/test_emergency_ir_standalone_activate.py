from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
SPEC = importlib.util.spec_from_file_location(
    "emergency_ir_standalone_activate",
    ROOT / "scripts" / "emergency_ir_standalone_activate.py",
)
assert SPEC and SPEC.loader
ACTIVATE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = ACTIVATE
SPEC.loader.exec_module(ACTIVATE)


SOURCE_SHA = ACTIVATE.SOURCE_RELEASE_SHA
PATCH_SHA = "a" * 40


def add_member(archive: tarfile.TarFile, name: str, payload: bytes, *, kind: bytes = tarfile.REGTYPE) -> None:
    info = tarfile.TarInfo(name)
    info.mode = 0o600
    info.uid = 0
    info.gid = 0
    info.type = kind
    if kind == tarfile.REGTYPE:
        info.size = len(payload)
        archive.addfile(info, io.BytesIO(payload))
    else:
        info.linkname = "target"
        archive.addfile(info)


def root_file(path: Path, payload: bytes) -> None:
    path.write_bytes(payload)
    path.chmod(0o600)


def nginx_paths(root: Path) -> tuple[ACTIVATE.ActivationPaths, Path]:
    nginx_root = root / "nginx"
    available = nginx_root / "sites-available" / "trading-bot-emergency-ir"
    enabled_root = nginx_root / "sites-enabled"
    enabled_root.mkdir(parents=True)
    original = root / "original-default.conf"
    root_file(original, b"server { return 444; }\n")
    default = enabled_root / "default"
    os.symlink(str(original), default)
    return (
        ACTIVATE.ActivationPaths(
            emergency_root=root / "emergency",
            inbox_root=root / "emergency" / "inbox",
            bootstrap_root=root / "bootstrap",
            activation_root=root / "emergency" / "activation",
            releases_root=root / "emergency" / "releases",
            current_link=root / "emergency" / "current",
            age_identity=root / "age-identity.txt",
            runtime_env=root / "runtime.env",
            nginx_available=available,
            nginx_enabled=enabled_root / "trading-bot-emergency-ir",
            nginx_default=default,
            nginx_backup_root=root / "emergency" / "nginx-backups",
            nginx_sms_rate_limit=nginx_root / "conf.d" / "trading-bot-emergency-ir-sms-rate-limit.conf",
            sms_preflight_receipt=root / "sms-preflight.json",
        ),
        original,
    )


def nginx_package(root: Path) -> Path:
    package_root = root / "package"
    source = package_root / "deploy" / "emergency-ir" / "nginx.standalone.conf.template"
    source.parent.mkdir(parents=True)
    root_file(
        source,
        b"server_name coin.gold-trade.ir;\n"
        b"proxy_pass http://127.0.0.1:18000;\n"
        b"ssl_certificate /tmp/only-a-test.pem;\n",
    )
    return package_root


def completed(command: object, returncode: int = 0) -> subprocess.CompletedProcess[bytes]:
    return subprocess.CompletedProcess(command, returncode, stdout=b"", stderr=b"")


def package_files() -> dict[str, bytes]:
    return {
        "deploy/emergency-ir/docker-compose.standalone.yml": b"name: trading-bot-emergency-ir\n",
        "deploy/emergency-ir/docker-compose.sms-otp.yml": b"services: {}\n",
        "deploy/emergency-ir/nginx.standalone.conf.template": b"server {}\n",
        "deploy/emergency-ir/reset-emergency-sessions.sql": b"BEGIN; COMMIT;\n",
        "scripts/render_emergency_ir_standalone_env.py": b"# renderer\n",
        "scripts/preflight_emergency_ir_host_isolation.py": b"# host preflight\n",
        "scripts/validate_emergency_ir_compose_contract.py": b"# Compose validator\n",
        "scripts/verify_emergency_ir_standalone.py": b"# verifier\n",
        "scripts/verify_emergency_ir_image_provenance.py": b"# provenance\n",
        "scripts/emergency_ir_standalone_activate.py": b"# activation\n",
    }


def write_package(
    path: Path,
    *,
    symlink_member: bool = False,
    wrong_hash: bool = False,
    omit: str | None = None,
) -> None:
    files = package_files()
    if omit is not None:
        del files[omit]
    release = {
        "schema": ACTIVATE.PACKAGE_RELEASE_SCHEMA,
        "source_release_sha": SOURCE_SHA,
        "emergency_patch_sha": PATCH_SHA,
        "files": [
            {
                "path": name,
                "sha256": ("0" * 64 if wrong_hash and index == 0 else hashlib.sha256(payload).hexdigest()),
                "bytes": len(payload),
            }
            for index, (name, payload) in enumerate(sorted(files.items()))
        ],
    }
    with tarfile.open(path, "w:gz") as archive:
        add_member(archive, f"{ACTIVATE.PACKAGE_ROOT_NAME}/RELEASE.json", ACTIVATE._canonical_json(release))
        for index, (name, payload) in enumerate(sorted(files.items())):
            add_member(
                archive,
                f"{ACTIVATE.PACKAGE_ROOT_NAME}/{name}",
                payload,
                kind=tarfile.SYMTYPE if symlink_member and index == 0 else tarfile.REGTYPE,
            )
    path.chmod(0o600)


def host_isolation_preflight_report(*, profile: str = "telegram-only") -> dict[str, object]:
    return {
        "schema": ACTIVATE.HOST_ISOLATION_PREFLIGHT_SCHEMA,
        "status": "ready",
        "profile": profile,
        "collisions": [],
        "checked": {
            "compose_project": ACTIVATE.HOST_ISOLATION_PROJECT_NAME,
            "planned_host_port": ACTIVATE.HOST_ISOLATION_APP_PORT,
            "container_count": 3,
            "volume_count": 4,
            "network_count": 2,
            "protected_host_roots": 2,
        },
        "read_only_actions": list(ACTIVATE.HOST_ISOLATION_READ_ONLY_ACTIONS),
        "mutating_actions": [],
        "docker_or_service_changed": False,
        "authorizes_activation": False,
    }


def host_isolation_compose_report(*, package_root: Path, profile: str = "telegram-only") -> dict[str, object]:
    identities = {
        str(item["path"]): item
        for item in ACTIVATE.sealed_host_isolation_member_identities(package_root=package_root)
    }
    base = identities["deploy/emergency-ir/docker-compose.standalone.yml"]
    report: dict[str, object] = {
        "schema": ACTIVATE.HOST_ISOLATION_COMPOSE_SCHEMA,
        "status": "verified-local-only",
        "profile": profile,
        "base_sha256": base["sha256"],
        "base_bytes": base["bytes"],
        "docker_or_service_changed": False,
        "network_action": False,
    }
    if profile == "sms-otp":
        sms = identities["deploy/emergency-ir/docker-compose.sms-otp.yml"]
        report.update({"sms_sha256": sms["sha256"], "sms_bytes": sms["bytes"]})
    return report


def canonical_host_control_output(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=True, sort_keys=True, allow_nan=False) + "\n").encode("ascii")


def host_control_completed(
    command: object,
    payload: object,
    *,
    returncode: int = 0,
    stderr: bytes = b"",
) -> subprocess.CompletedProcess[object]:
    return subprocess.CompletedProcess(command, returncode, stdout=payload, stderr=stderr)


def write_settings(path: Path, *, profile: str = "telegram-only", extra: bool = False) -> None:
    members = {
        "trading_settings.json": b'{"commodities":[]}',
        "webapp_initdata_token": b"123456:only-a-test-token\n",
    }
    if profile == "sms-otp":
        members.update(
            {
                "smsir_api_key": b"smsir-test-key\n",
                "smsir_otp_template_id": b"123456\n",
                "smsir_otp_template_parameter": b"CODE\n",
            }
        )
    if extra:
        members["unexpected"] = b"no"
    with tarfile.open(path, "w:") as archive:
        for name, payload in sorted(members.items()):
            add_member(archive, name, payload)
    path.chmod(0o600)


def write_image_bundle(path: Path, *, bad_app_labels: bool = False) -> None:
    labels = {
        "org.opencontainers.image.revision": PATCH_SHA,
        "org.goldtrade.emergency.base-revision": SOURCE_SHA,
        "org.goldtrade.emergency.scope": "ir-standalone",
        "org.goldtrade.emergency.auth": "webapp-initdata-and-local-sms-otp",
    }
    if bad_app_labels:
        labels["org.goldtrade.emergency.scope"] = "wrong"
    rows = [
        ("a" * 64, f"trading_bot_emergency_ir_app:{PATCH_SHA}", {"Labels": labels, "Env": ["PATH=/usr/bin"]}),
        ("b" * 64, "trading_bot_emergency_ir_postgres:15-alpine-a1b2", {"Labels": {}, "Env": ["PATH=/usr/bin"]}),
        ("c" * 64, "trading_bot_emergency_ir_redis:7-alpine-a1b2", {"Labels": {}, "Env": ["PATH=/usr/bin"]}),
    ]
    manifest_rows: list[dict[str, object]] = []
    with tarfile.open(path, "w:") as archive:
        for identity, tag, config in rows:
            layer = f"{identity}/layer.tar"
            add_member(archive, layer, b"one-layer")
            add_member(archive, f"{identity}.json", json.dumps({"config": config}).encode("utf-8"))
            manifest_rows.append({"Config": f"{identity}.json", "RepoTags": [tag], "Layers": [layer]})
        add_member(archive, "manifest.json", json.dumps(manifest_rows).encode("utf-8"))
    path.chmod(0o600)


class EmergencyIrStandaloneActivationTests(unittest.TestCase):
    def test_staging_listener_uses_only_the_fixed_endpoint_for_each_required_port(self) -> None:
        cases = (
            (8213, ("127.0.0.1", 8213)),
            (8443, ("95.38.164.29", 8443)),
        )
        for port, expected_endpoint in cases:
            connection = MagicMock()
            connection.__enter__.return_value = connection
            connection.getpeername.return_value = expected_endpoint
            connection.getsockname.return_value = (expected_endpoint[0], 49152)
            with self.subTest(port=port), patch.object(
                ACTIVATE.socket, "socket", return_value=connection
            ) as create_socket, patch.object(
                ACTIVATE.socket, "create_connection"
            ) as generic_connection, patch.object(
                ACTIVATE, "_require_ipv4_nonlocal_bind_disabled"
            ) as nonlocal_bind_gate:
                ACTIVATE._check_staging_listener(port)
            create_socket.assert_called_once_with(ACTIVATE.socket.AF_INET, ACTIVATE.socket.SOCK_STREAM)
            generic_connection.assert_not_called()
            if port == 8443:
                nonlocal_bind_gate.assert_called_once_with()
            else:
                nonlocal_bind_gate.assert_not_called()
            connection.setsockopt.assert_called_once_with(
                ACTIVATE.socket.SOL_SOCKET, ACTIVATE.socket.SO_DONTROUTE, 1
            )
            connection.settimeout.assert_called_once_with(3)
            connection.bind.assert_called_once_with((expected_endpoint[0], 0))
            connection.connect.assert_called_once_with(expected_endpoint)
            connection.getpeername.assert_called_once_with()
            connection.getsockname.assert_called_once_with()

    def test_staging_listener_rejects_any_unapproved_port_without_network_access(self) -> None:
        for port in (80, 443, 8212, 8214, 8442, 8444, 65535):
            with self.subTest(port=port), patch.object(ACTIVATE.socket, "socket") as create_socket:
                with self.assertRaisesRegex(
                    ACTIVATE.EmergencyActivationError,
                    "staging listener port is not permitted",
                ):
                    ACTIVATE._check_staging_listener(port)
            create_socket.assert_not_called()

    def test_public_staging_listener_fails_before_socket_when_nonlocal_bind_is_enabled(self) -> None:
        with tempfile.TemporaryDirectory(prefix="emergency-ir-nonlocal-bind-") as raw:
            root = Path(raw)
            root.chmod(0o700)
            sysctl_root = root / "proc" / "sys" / "net" / "ipv4"
            sysctl_root.mkdir(mode=0o700, parents=True)
            disabled_sysctl = sysctl_root / "ip_nonlocal_bind"
            root_file(disabled_sysctl, b"0\n")
            with patch.object(ACTIVATE, "IPV4_NONLOCAL_BIND_PATH", disabled_sysctl):
                ACTIVATE._require_ipv4_nonlocal_bind_disabled()

            enabled_sysctl = sysctl_root / "ip_nonlocal_bind-enabled"
            root_file(enabled_sysctl, b"1\n")
            with patch.object(ACTIVATE, "IPV4_NONLOCAL_BIND_PATH", enabled_sysctl), patch.object(
                ACTIVATE.socket, "socket"
            ) as create_socket:
                with self.assertRaisesRegex(
                    ACTIVATE.EmergencyActivationError,
                    "IPv4 nonlocal bind must be disabled",
                ):
                    ACTIVATE._check_staging_listener(8443)
            create_socket.assert_not_called()

    def test_public_staging_listener_rejects_unsafe_kernel_control_forms_before_socket(self) -> None:
        with tempfile.TemporaryDirectory(prefix="emergency-ir-nonlocal-bind-invalid-") as raw:
            root = Path(raw)
            root.chmod(0o700)
            sysctl_root = root / "proc" / "sys" / "net" / "ipv4"
            sysctl_root.mkdir(mode=0o700, parents=True)
            target = sysctl_root / "target"
            root_file(target, b"0\n")
            cases = (
                ("malformed", b"false\n", 0o600, "must be disabled"),
                ("oversized", b"0\n" + b"x" * ACTIVATE.MAX_KERNEL_TOGGLE_BYTES, 0o600, "is oversized"),
                ("writable", b"0\n", 0o622, "is not root-controlled"),
            )
            for name, payload, mode, message in cases:
                candidate = sysctl_root / name
                root_file(candidate, payload)
                candidate.chmod(mode)
                with self.subTest(name=name), patch.object(
                    ACTIVATE, "IPV4_NONLOCAL_BIND_PATH", candidate
                ), patch.object(ACTIVATE.socket, "socket") as create_socket:
                    with self.assertRaisesRegex(ACTIVATE.EmergencyActivationError, message):
                        ACTIVATE._check_staging_listener(8443)
                create_socket.assert_not_called()

            symlink = sysctl_root / "symlink"
            os.symlink(target, symlink)
            with patch.object(ACTIVATE, "IPV4_NONLOCAL_BIND_PATH", symlink), patch.object(
                ACTIVATE.socket, "socket"
            ) as create_socket:
                with self.assertRaisesRegex(ACTIVATE.EmergencyActivationError, "is not root-controlled"):
                    ACTIVATE._check_staging_listener(8443)
            create_socket.assert_not_called()

    def test_staging_listener_rejects_peer_or_local_endpoint_drift(self) -> None:
        expected_endpoint = ("95.38.164.29", 8443)
        for peer, local, message in (
            (("127.0.0.1", 8443), (expected_endpoint[0], 49152), "peer endpoint did not remain pinned"),
            (expected_endpoint, ("127.0.0.1", 49152), "local endpoint did not remain pinned"),
            (expected_endpoint, (expected_endpoint[0], 0), "local endpoint did not remain pinned"),
        ):
            connection = MagicMock()
            connection.__enter__.return_value = connection
            connection.getpeername.return_value = peer
            connection.getsockname.return_value = local
            with self.subTest(peer=peer, local=local), patch.object(
                ACTIVATE.socket, "socket", return_value=connection
            ), patch.object(ACTIVATE, "_require_ipv4_nonlocal_bind_disabled"):
                with self.assertRaisesRegex(ACTIVATE.EmergencyActivationError, message):
                    ACTIVATE._check_staging_listener(8443)

    def test_staging_listener_socket_setup_failures_are_fail_closed(self) -> None:
        connection = MagicMock()
        connection.__enter__.return_value = connection
        connection.setsockopt.side_effect = OSError("denied")
        with patch.object(ACTIVATE.socket, "socket", return_value=connection):
            with self.assertRaisesRegex(ACTIVATE.EmergencyActivationError, "listener is not healthy"):
                ACTIVATE._check_staging_listener(8213)
        connection.bind.assert_not_called()
        connection.connect.assert_not_called()

    def test_prepare_requires_aggregate_plaintext_disk_budget_before_decrypt(self) -> None:
        with tempfile.TemporaryDirectory(prefix="emergency-ir-activation-") as raw:
            root = Path(raw)
            root.chmod(0o700)
            campaign = ACTIVATE.VerifiedCampaign(
                campaign_id="20260801T220000Z-emergency-ir-01",
                manifest_sha256="d" * 64,
                plan={},
                artifacts={
                    kind: {"plaintext_bytes": 100 + index}
                    for index, kind in enumerate(ACTIVATE.manifest.ARTIFACT_ORDER)
                },
            )
            paths = ACTIVATE.ActivationPaths(
                emergency_root=root,
                activation_root=root / "activation",
            )
            required = ACTIVATE.DISK_HEADROOM_BYTES + sum(
                int(item["plaintext_bytes"]) for item in campaign.artifacts.values()
            )
            with patch.object(ACTIVATE.shutil, "disk_usage", return_value=SimpleNamespace(free=required)):
                ACTIVATE._require_prepare_disk_budget(campaign=campaign, paths=paths)
            with patch.object(ACTIVATE.shutil, "disk_usage", return_value=SimpleNamespace(free=required - 1)):
                with self.assertRaisesRegex(ACTIVATE.EmergencyActivationError, "aggregate"):
                    ACTIVATE._require_prepare_disk_budget(campaign=campaign, paths=paths)

    def test_package_release_hashes_and_exact_members_extract_create_only(self) -> None:
        with tempfile.TemporaryDirectory(prefix="emergency-ir-activation-") as raw:
            root = Path(raw)
            root.chmod(0o700)
            package = root / "package.tar.gz"
            write_package(package)
            identity = ACTIVATE.extract_and_verify_package(package_tar=package, releases_root=root / "releases")
            self.assertEqual(identity.source_release_sha, SOURCE_SHA)
            self.assertEqual(identity.emergency_patch_sha, PATCH_SHA)
            self.assertTrue((identity.package_root / "scripts/emergency_ir_standalone_activate.py").is_file())
            with self.assertRaisesRegex(ACTIVATE.EmergencyActivationError, "overwrite"):
                ACTIVATE.extract_and_verify_package(package_tar=package, releases_root=root / "releases")

    def test_package_rejects_hash_mismatch_and_link_member(self) -> None:
        with tempfile.TemporaryDirectory(prefix="emergency-ir-activation-") as raw:
            root = Path(raw)
            root.chmod(0o700)
            for name, kwargs in (("wrong-hash.tar.gz", {"wrong_hash": True}), ("link.tar.gz", {"symlink_member": True})):
                package = root / name
                write_package(package, **kwargs)
                with self.subTest(name=name), self.assertRaises(ACTIVATE.EmergencyActivationError):
                    ACTIVATE.extract_and_verify_package(package_tar=package, releases_root=root / name)

    def test_host_isolation_controls_are_mandatory_and_rehashed_from_sealed_release(self) -> None:
        with tempfile.TemporaryDirectory(prefix="emergency-ir-activation-") as raw:
            root = Path(raw)
            root.chmod(0o700)
            package = root / "package.tar.gz"
            write_package(package)
            identity = ACTIVATE.extract_and_verify_package(package_tar=package, releases_root=root / "releases")
            identities = ACTIVATE.sealed_host_isolation_member_identities(package_root=identity.package_root)
            self.assertEqual(
                [item["path"] for item in identities],
                list(ACTIVATE.HOST_ISOLATION_SEALED_MEMBERS),
            )
            self.assertTrue(all(len(str(item["sha256"])) == 64 for item in identities))

            validator = identity.package_root / ACTIVATE.HOST_ISOLATION_COMPOSE_VALIDATOR_MEMBER
            root_file(validator, b"# changed validator\n")
            with self.assertRaisesRegex(ACTIVATE.EmergencyActivationError, "RELEASE.json identity"):
                ACTIVATE.sealed_host_isolation_member_identities(package_root=identity.package_root)

            for omitted in (
                ACTIVATE.HOST_ISOLATION_PREFLIGHT_MEMBER,
                ACTIVATE.HOST_ISOLATION_COMPOSE_VALIDATOR_MEMBER,
            ):
                missing = root / f"missing-{Path(omitted).stem}.tar.gz"
                write_package(missing, omit=omitted)
                with self.subTest(omitted=omitted), self.assertRaisesRegex(
                    ACTIVATE.EmergencyActivationError, "mandatory Emergency activation control"
                ):
                    ACTIVATE.extract_and_verify_package(package_tar=missing, releases_root=root / Path(omitted).stem)

    def test_host_isolation_receipt_is_campaign_bound_nonsecret_and_rehashes_controls(self) -> None:
        with tempfile.TemporaryDirectory(prefix="emergency-ir-activation-") as raw:
            root = Path(raw)
            root.chmod(0o700)
            package = root / "package.tar.gz"
            write_package(package)
            identity = ACTIVATE.extract_and_verify_package(package_tar=package, releases_root=root / "releases")
            campaign = ACTIVATE.VerifiedCampaign(
                campaign_id="20260801T220000Z-emergency-ir-01",
                manifest_sha256="d" * 64,
                plan={},
                artifacts={},
            )
            paths = ACTIVATE.ActivationPaths(emergency_root=root / "emergency", activation_root=root / "emergency" / "activation")
            receipt = ACTIVATE.write_host_isolation_receipt(
                paths=paths,
                campaign=campaign,
                package_root=identity.package_root,
                profile="telegram-only",
                preflight_report=host_isolation_preflight_report(),
                compose_report=host_isolation_compose_report(package_root=identity.package_root),
            )
            serialized = json.dumps(receipt, sort_keys=True)
            self.assertNotIn("JWT_SECRET_KEY", serialized)
            self.assertNotIn("runtime.env", serialized)
            self.assertEqual(receipt, ACTIVATE.read_host_isolation_receipt(
                paths=paths,
                campaign=campaign,
                package_root=identity.package_root,
                profile="telegram-only",
            ))
            different_manifest = ACTIVATE.VerifiedCampaign(
                campaign_id=campaign.campaign_id,
                manifest_sha256="e" * 64,
                plan={},
                artifacts={},
            )
            with self.assertRaisesRegex(ACTIVATE.EmergencyActivationError, "not bound to this campaign"):
                ACTIVATE.read_host_isolation_receipt(
                    paths=paths,
                    campaign=different_manifest,
                    package_root=identity.package_root,
                    profile="telegram-only",
                )

            root_file(
                identity.package_root / ACTIVATE.HOST_ISOLATION_PREFLIGHT_MEMBER,
                b"# changed host preflight\n",
            )
            with self.assertRaisesRegex(ACTIVATE.EmergencyActivationError, "sealed package member|sealed package identities"):
                ACTIVATE.read_host_isolation_receipt(
                    paths=paths,
                    campaign=campaign,
                    package_root=identity.package_root,
                    profile="telegram-only",
                )

    def test_host_isolation_receipt_refuses_unknown_or_secret_bearing_evidence(self) -> None:
        with tempfile.TemporaryDirectory(prefix="emergency-ir-activation-") as raw:
            root = Path(raw)
            root.chmod(0o700)
            package = root / "package.tar.gz"
            write_package(package)
            identity = ACTIVATE.extract_and_verify_package(package_tar=package, releases_root=root / "releases")
            campaign = ACTIVATE.VerifiedCampaign(
                campaign_id="20260801T220000Z-emergency-ir-01",
                manifest_sha256="d" * 64,
                plan={},
                artifacts={},
            )
            paths = ACTIVATE.ActivationPaths(emergency_root=root / "emergency", activation_root=root / "emergency" / "activation")
            preflight = host_isolation_preflight_report()
            preflight["runtime_env"] = "must-never-be-persisted"
            with self.assertRaisesRegex(ACTIVATE.EmergencyActivationError, "evidence fields"):
                ACTIVATE.write_host_isolation_receipt(
                    paths=paths,
                    campaign=campaign,
                    package_root=identity.package_root,
                    profile="telegram-only",
                    preflight_report=preflight,
                    compose_report=host_isolation_compose_report(package_root=identity.package_root),
                )
            self.assertFalse((paths.activation_root / campaign.campaign_id / "host-isolation.json").exists())

    def test_host_isolation_receipt_binds_compose_evidence_to_the_sealed_sources(self) -> None:
        with tempfile.TemporaryDirectory(prefix="emergency-ir-activation-") as raw:
            root = Path(raw)
            root.chmod(0o700)
            package = root / "package.tar.gz"
            write_package(package)
            identity = ACTIVATE.extract_and_verify_package(package_tar=package, releases_root=root / "releases")
            campaign = ACTIVATE.VerifiedCampaign(
                campaign_id="20260801T220000Z-emergency-ir-01",
                manifest_sha256="d" * 64,
                plan={},
                artifacts={},
            )
            paths = ACTIVATE.ActivationPaths(emergency_root=root / "emergency", activation_root=root / "emergency" / "activation")
            compose = host_isolation_compose_report(package_root=identity.package_root)
            compose["base_sha256"] = "0" * 64
            with self.assertRaisesRegex(ACTIVATE.EmergencyActivationError, "sealed base source"):
                ACTIVATE.write_host_isolation_receipt(
                    paths=paths,
                    campaign=campaign,
                    package_root=identity.package_root,
                    profile="telegram-only",
                    preflight_report=host_isolation_preflight_report(),
                    compose_report=compose,
                )

    def test_settings_tar_accepts_only_profile_exact_members_without_persisting_secret(self) -> None:
        with tempfile.TemporaryDirectory(prefix="emergency-ir-activation-") as raw:
            root = Path(raw)
            root.chmod(0o700)
            telegram = root / "telegram.tar"
            write_settings(telegram)
            values = ACTIVATE.read_settings_bundle(settings_tar=telegram, profile="telegram-only")
            self.assertEqual(values.webapp_initdata_token, "123456:only-a-test-token")
            self.assertIsNone(values.smsir_api_key)

            sms = root / "sms.tar"
            write_settings(sms, profile="sms-otp")
            sms_values = ACTIVATE.read_settings_bundle(settings_tar=sms, profile="sms-otp")
            self.assertEqual(sms_values.smsir_otp_template_parameter, "CODE")

            extra = root / "extra.tar"
            write_settings(extra, extra=True)
            with self.assertRaisesRegex(ACTIVATE.EmergencyActivationError, "allowlist"):
                ACTIVATE.read_settings_bundle(settings_tar=extra, profile="telegram-only")

    def test_image_archive_is_checked_before_docker_load(self) -> None:
        with tempfile.TemporaryDirectory(prefix="emergency-ir-activation-") as raw:
            root = Path(raw)
            root.chmod(0o700)
            image = root / "images.tar"
            write_image_bundle(image)
            entries = ACTIVATE.inspect_image_bundle(
                image_tar=image, source_sha=SOURCE_SHA, patch_sha=PATCH_SHA, profile="telegram-only"
            )
            self.assertEqual({entry.kind for entry in entries}, {"app", "postgres", "redis"})
            self.assertEqual(next(entry for entry in entries if entry.kind == "app").config_id, "sha256:" + "a" * 64)

            bad = root / "bad-images.tar"
            write_image_bundle(bad, bad_app_labels=True)
            with self.assertRaisesRegex(ACTIVATE.EmergencyActivationError, "provenance"):
                ACTIVATE.inspect_image_bundle(
                    image_tar=bad, source_sha=SOURCE_SHA, patch_sha=PATCH_SHA, profile="telegram-only"
                )

    def test_plan_is_non_authorizing_and_binds_each_stage_confirmation(self) -> None:
        campaign = ACTIVATE.VerifiedCampaign(
            campaign_id="20260801T220000Z-emergency-ir-01",
            manifest_sha256="d" * 64,
            plan={},
            artifacts={},
        )
        plan = ACTIVATE.activation_plan(campaign, profile="telegram-only")
        self.assertEqual(plan["status"], "planned-local-only")
        self.assertEqual([item["stage"] for item in plan["stages"]], ["prepare", "images", "database", "api", "prearm"])
        self.assertEqual(
            plan["stages"][0]["confirm"],
            "activate-emergency-ir:20260801T220000Z-emergency-ir-01:" + "d" * 64 + ":telegram-only:prepare",
        )
        self.assertIn("volume deletion", plan["never"])

    def test_reload_failure_restores_default_preserves_emergency_link_and_skips_ufw(self) -> None:
        with tempfile.TemporaryDirectory(prefix="emergency-ir-nginx-") as raw:
            root = Path(raw)
            root.chmod(0o700)
            paths, original = nginx_paths(root)
            package_root = nginx_package(root)
            campaign = ACTIVATE.VerifiedCampaign(
                campaign_id="20260801T220000Z-emergency-ir-01",
                manifest_sha256="d" * 64,
                plan={},
                artifacts={},
            )
            calls: list[list[str]] = []
            reload_count = 0

            def runner(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
                nonlocal reload_count
                calls.append(list(command))
                if command == [ACTIVATE.SYSTEMCTL_BINARY, "reload", "nginx"]:
                    reload_count += 1
                    return completed(command, 1 if reload_count == 1 else 0)
                return completed(command)

            with self.assertRaisesRegex(ACTIVATE.EmergencyActivationError, "previous default site was restored"):
                ACTIVATE._prearm_nginx(
                    paths=paths,
                    campaign=campaign,
                    package_root=package_root,
                    profile="telegram-only",
                    runner=runner,
                )

            failed = paths.nginx_backup_root / f"emergency-site.failed-{campaign.campaign_id}"
            self.assertTrue(paths.nginx_default.is_symlink())
            self.assertEqual(os.readlink(paths.nginx_default), str(original))
            self.assertTrue(failed.is_symlink())
            self.assertEqual(os.readlink(failed), str(paths.nginx_available))
            self.assertFalse(paths.nginx_enabled.exists() or paths.nginx_enabled.is_symlink())
            self.assertFalse(any(call and call[0] == ACTIVATE.UFW_BINARY for call in calls))
            self.assertEqual(calls.count([ACTIVATE.NGINX_BINARY, "-t"]), 2)
            self.assertEqual(calls.count([ACTIVATE.SYSTEMCTL_BINARY, "reload", "nginx"]), 2)

    def test_ufw_uses_one_atomic_multiport_rule_and_rolls_back_on_failure(self) -> None:
        with tempfile.TemporaryDirectory(prefix="emergency-ir-nginx-") as raw:
            root = Path(raw)
            root.chmod(0o700)
            paths, original = nginx_paths(root)
            package_root = nginx_package(root)
            campaign = ACTIVATE.VerifiedCampaign(
                campaign_id="20260801T220000Z-emergency-ir-01",
                manifest_sha256="d" * 64,
                plan={},
                artifacts={},
            )
            calls: list[list[str]] = []

            def runner(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
                calls.append(list(command))
                return completed(command, 1 if command and command[0] == ACTIVATE.UFW_BINARY else 0)

            with self.assertRaisesRegex(ACTIVATE.EmergencyActivationError, "UFW rule could not be added"):
                ACTIVATE._prearm_nginx(
                    paths=paths,
                    campaign=campaign,
                    package_root=package_root,
                    profile="telegram-only",
                    runner=runner,
                )

            expected_ufw = [
                ACTIVATE.UFW_BINARY,
                "allow",
                "proto",
                "tcp",
                "from",
                "any",
                "to",
                "any",
                "port",
                "80,443",
                "comment",
                "trading-bot-emergency-ir",
            ]
            self.assertEqual([call for call in calls if call and call[0] == ACTIVATE.UFW_BINARY], [expected_ufw])
            failed = paths.nginx_backup_root / f"emergency-site.failed-{campaign.campaign_id}"
            self.assertTrue(paths.nginx_default.is_symlink())
            self.assertEqual(os.readlink(paths.nginx_default), str(original))
            self.assertTrue(failed.is_symlink())
            self.assertEqual(os.readlink(failed), str(paths.nginx_available))


if __name__ == "__main__":
    unittest.main()
