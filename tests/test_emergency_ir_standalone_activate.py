from __future__ import annotations

import dataclasses
from datetime import datetime, timedelta, timezone
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
import unittest
from unittest.mock import patch

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID


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
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.write_bytes(payload)
    path.chmod(0o600)


def write_tls_pair(
    fullchain: Path,
    private_key: Path,
    *,
    domain: str = ACTIVATE.EMERGENCY_DOMAIN,
    expires_in: timedelta = timedelta(days=30),
    key: rsa.RSAPrivateKey | None = None,
) -> rsa.RSAPrivateKey:
    now = datetime.now(timezone.utc)
    pair_key = key or rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, domain)])
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(pair_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + expires_in)
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(domain)]), critical=False)
        .sign(pair_key, hashes.SHA256())
    )
    root_file(fullchain, certificate.public_bytes(serialization.Encoding.PEM))
    root_file(
        private_key,
        pair_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        ),
    )
    return pair_key


def certbot_source_layout(root: Path, *, domain: str = ACTIVATE.EMERGENCY_DOMAIN, expires_in: timedelta = timedelta(days=30)) -> tuple[Path, Path, Path]:
    archive = root / "acme" / "config" / "archive" / "emergency-coin-gold-trade-ir"
    live = root / "acme" / "config" / "live" / "emergency-coin-gold-trade-ir"
    fullchain_target = archive / "fullchain1.pem"
    private_key_target = archive / "privkey1.pem"
    write_tls_pair(fullchain_target, private_key_target, domain=domain, expires_in=expires_in)
    live.mkdir(mode=0o700, parents=True)
    fullchain_source = live / "fullchain.pem"
    private_key_source = live / "privkey.pem"
    os.symlink(os.path.relpath(fullchain_target, live), fullchain_source)
    os.symlink(os.path.relpath(private_key_target, live), private_key_source)
    return fullchain_source, private_key_source, archive


def nginx_paths(root: Path) -> tuple[ACTIVATE.ActivationPaths, Path]:
    nginx_root = root / "nginx"
    available = nginx_root / "sites-available" / "trading-bot-emergency-ir"
    enabled_root = nginx_root / "sites-enabled"
    enabled_root.mkdir(parents=True)
    original = root / "original-default.conf"
    root_file(original, b"server { return 444; }\n")
    default = enabled_root / "default"
    os.symlink(str(original), default)
    pinned_root = root / "pinned-tls"
    pinned_fullchain = pinned_root / "fullchain.pem"
    pinned_private_key = pinned_root / "privkey.pem"
    write_tls_pair(pinned_fullchain, pinned_private_key)
    tls_source_fullchain, tls_source_privkey, tls_archive_root = certbot_source_layout(root)
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
            tls_source_fullchain=tls_source_fullchain,
            tls_source_privkey=tls_source_privkey,
            tls_source_archive_root=tls_archive_root,
            tls_pinned_fullchain=pinned_fullchain,
            tls_pinned_privkey=pinned_private_key,
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
        b"ssl_certificate __EMERGENCY_TLS_FULLCHAIN__;\n"
        b"ssl_certificate_key __EMERGENCY_TLS_PRIVATE_KEY__;\n"
        b"ssl_certificate __EMERGENCY_TLS_FULLCHAIN__;\n"
        b"ssl_certificate_key __EMERGENCY_TLS_PRIVATE_KEY__;\n",
    )
    return package_root


def completed(command: object, returncode: int = 0, *, stdout: str | bytes = b"") -> subprocess.CompletedProcess[object]:
    return subprocess.CompletedProcess(command, returncode, stdout=stdout, stderr=b"")


def package_files() -> dict[str, bytes]:
    return {
        "deploy/emergency-ir/docker-compose.standalone.yml": b"name: trading-bot-emergency-ir\n",
        "deploy/emergency-ir/nginx.standalone.conf.template": b"server {}\n",
        "deploy/emergency-ir/reset-emergency-sessions.sql": b"BEGIN; COMMIT;\n",
        "scripts/render_emergency_ir_standalone_env.py": b"# renderer\n",
        "scripts/verify_emergency_ir_standalone.py": b"# verifier\n",
        "scripts/verify_emergency_ir_image_provenance.py": b"# provenance\n",
        "scripts/emergency_ir_standalone_activate.py": b"# activation\n",
    }


def write_package(path: Path, *, symlink_member: bool = False, wrong_hash: bool = False) -> None:
    files = package_files()
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
        self.assertEqual([item["stage"] for item in plan["stages"]], ["prepare", "images", "database", "api", "tls", "prearm"])
        self.assertEqual(
            plan["stages"][0]["confirm"],
            "activate-emergency-ir:20260801T220000Z-emergency-ir-01:" + "d" * 64 + ":telegram-only:prepare",
        )
        self.assertIn("volume deletion", plan["never"])

    def _campaign(self) -> ACTIVATE.VerifiedCampaign:
        return ACTIVATE.VerifiedCampaign(
            campaign_id="20260801T220000Z-emergency-ir-01",
            manifest_sha256="d" * 64,
            plan={},
            artifacts={},
        )

    def _runner(
        self,
        events: list[object],
        *,
        enabled: bool,
        active: bool,
        fail_action: str | None = None,
        ufw_rule_present: bool = False,
        ufw_ipv6_rule_present: bool = False,
        ufw_conflicting_rule: bool = False,
    ) -> object:
        action_counts: dict[str, int] = {}
        state = {
            "enabled": enabled,
            "active": active,
            "ufw_rule_present": ufw_rule_present,
            "ufw_ipv6_rule_present": ufw_ipv6_rule_present,
            "ufw_conflicting_rule": ufw_conflicting_rule,
        }

        def runner(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[object]:
            call = list(command)
            events.append(("command", *call))
            if call == [ACTIVATE.SYSTEMCTL_BINARY, "is-enabled", "nginx"]:
                return completed(
                    call,
                    0 if state["enabled"] else 1,
                    stdout="enabled\n" if state["enabled"] else "disabled\n",
                )
            if call == [ACTIVATE.SYSTEMCTL_BINARY, "is-active", "nginx"]:
                return completed(
                    call,
                    0 if state["active"] else 3,
                    stdout="active\n" if state["active"] else "inactive\n",
                )
            if call[:1] == [ACTIVATE.SYSTEMCTL_BINARY] and len(call) == 3:
                action = call[1]
                action_counts[action] = action_counts.get(action, 0) + 1
                if action == fail_action and action_counts[action] == 1:
                    return completed(call, 1)
                if action == "enable":
                    state["enabled"] = True
                elif action == "disable":
                    state["enabled"] = False
                elif action == "start":
                    state["active"] = True
                elif action == "stop":
                    state["active"] = False
            if call == [ACTIVATE.UFW_BINARY, "status", "numbered"]:
                if fail_action == "ufw-status-after-allow" and state["ufw_rule_present"]:
                    raise OSError("synthetic post-UFW status failure")
                lines = ["Status: active"]
                if state["ufw_rule_present"]:
                    lines.append(
                        "[ 1] 80,443/tcp                ALLOW IN    Anywhere                   # trading-bot-emergency-ir"
                    )
                if state["ufw_ipv6_rule_present"]:
                    lines.append(
                        "[ 2] 80,443/tcp (v6)           ALLOW IN    Anywhere (v6)              # trading-bot-emergency-ir"
                    )
                return completed(call, stdout="\n".join(lines) + "\n")
            if call == [ACTIVATE.UFW_BINARY, "show", "added"]:
                lines = ["Added user rules (see 'ufw status' for running firewall):"]
                if state["ufw_rule_present"]:
                    lines.append(ACTIVATE.UFW_SHOW_ADDED_OWNED_RULE)
                if state["ufw_conflicting_rule"]:
                    lines.append("ufw allow 80,443/tcp comment 'another-owner'")
                else:
                    if not state["ufw_rule_present"]:
                        lines.append("(None)")
                return completed(call, stdout="\n".join(lines) + "\n")
            if call[:1] == [ACTIVATE.UFW_BINARY] and len(call) > 1 and call[1] == "allow":
                if fail_action == "ufw":
                    return completed(call, 1)
                state["ufw_rule_present"] = True
            return completed(call)

        return runner

    def _ufw_allow_events(self, events: list[object]) -> list[tuple[object, ...]]:
        return [
            event
            for event in events
            if isinstance(event, tuple)
            and len(event) > 2
            and event[0] == "command"
            and event[1] == ACTIVATE.UFW_BINARY
            and event[2] == "allow"
        ]

    def _assert_default_restored(
        self, *, paths: ACTIVATE.ActivationPaths, original: Path, campaign: ACTIVATE.VerifiedCampaign
    ) -> None:
        failed = paths.nginx_backup_root / f"emergency-site.failed-{campaign.campaign_id}"
        self.assertTrue(paths.nginx_default.is_symlink())
        self.assertEqual(os.readlink(paths.nginx_default), str(original))
        self.assertTrue(failed.is_symlink())
        self.assertEqual(os.readlink(failed), str(paths.nginx_available))
        self.assertFalse(paths.nginx_enabled.exists() or paths.nginx_enabled.is_symlink())

    def _prearm(
        self,
        *,
        paths: ACTIVATE.ActivationPaths,
        campaign: ACTIVATE.VerifiedCampaign,
        package_root: Path,
        runner: object,
        tls_probe: object,
        staging_listener: object,
    ) -> dict[str, object]:
        with patch.object(
            ACTIVATE,
            "_require_pinned_tls",
            return_value=(paths.tls_pinned_fullchain, paths.tls_pinned_privkey),
        ):
            return ACTIVATE._prearm_nginx(
                paths=paths,
                campaign=campaign,
                package_root=package_root,
                profile="telegram-only",
                runner=runner,
                tls_probe=tls_probe,
                staging_listener=staging_listener,
            )

    def test_tls_pin_accepts_only_root_regular_matching_exact_domain_with_validity_margin(self) -> None:
        with tempfile.TemporaryDirectory(prefix="emergency-ir-tls-") as raw:
            root = Path(raw)
            root.chmod(0o700)
            paths, _ = nginx_paths(root)
            paths = dataclasses.replace(
                paths,
                tls_pinned_fullchain=root / "fresh-pin" / "fullchain.pem",
                tls_pinned_privkey=root / "fresh-pin" / "privkey.pem",
            )
            campaign = self._campaign()
            with patch.object(ACTIVATE, "_require_prepare", return_value={}), patch.object(
                ACTIVATE, "_read_receipt", return_value={}
            ):
                payload = ACTIVATE.pin_tls(campaign=campaign, paths=paths, profile="telegram-only")
            self.assertEqual(payload["fullchain_path"], str(paths.tls_pinned_fullchain))
            self.assertEqual(payload["private_key_path"], str(paths.tls_pinned_privkey))
            self.assertFalse(paths.tls_pinned_fullchain.is_symlink())
            self.assertFalse(paths.tls_pinned_privkey.is_symlink())
            self.assertEqual(paths.tls_pinned_fullchain.stat().st_mode & 0o777, 0o600)
            self.assertEqual(paths.tls_pinned_privkey.stat().st_mode & 0o777, 0o600)
            self.assertEqual(
                ACTIVATE._read_receipt(paths, campaign, stage="tls-pinned"),
                payload,
            )

    def test_tls_pin_rejects_wrong_san_mismatched_key_expiry_and_archive_escape(self) -> None:
        for name, configure, error in (
            (
                "wrong-san",
                lambda root, paths: certbot_source_layout(root / "wrong", domain="example.invalid"),
                "SAN",
            ),
            (
                "expired-margin",
                lambda root, paths: certbot_source_layout(root / "expired", expires_in=timedelta(hours=1)),
                "minimum remaining validity",
            ),
        ):
            with self.subTest(name=name), tempfile.TemporaryDirectory(prefix="emergency-ir-tls-") as raw:
                root = Path(raw)
                root.chmod(0o700)
                paths, _ = nginx_paths(root)
                source_fullchain, source_privkey, archive = configure(root, paths)
                paths = dataclasses.replace(
                    paths,
                    tls_source_fullchain=source_fullchain,
                    tls_source_privkey=source_privkey,
                    tls_source_archive_root=archive,
                    tls_pinned_fullchain=root / "pin" / "fullchain.pem",
                    tls_pinned_privkey=root / "pin" / "privkey.pem",
                )
                with patch.object(ACTIVATE, "_require_prepare", return_value={}), patch.object(
                    ACTIVATE, "_read_receipt", return_value={}
                ), self.assertRaisesRegex(ACTIVATE.EmergencyActivationError, error):
                    ACTIVATE.pin_tls(campaign=self._campaign(), paths=paths, profile="telegram-only")

        with tempfile.TemporaryDirectory(prefix="emergency-ir-tls-") as raw:
            root = Path(raw)
            root.chmod(0o700)
            paths, _ = nginx_paths(root)
            key_target = paths.tls_source_privkey.resolve()
            write_tls_pair(root / "other-cert.pem", key_target)
            with patch.object(ACTIVATE, "_require_prepare", return_value={}), patch.object(
                ACTIVATE, "_read_receipt", return_value={}
            ), self.assertRaisesRegex(ACTIVATE.EmergencyActivationError, "does not match"):
                ACTIVATE.pin_tls(campaign=self._campaign(), paths=paths, profile="telegram-only")

            outside = root / "outside.pem"
            root_file(outside, b"not-a-certificate")
            paths.tls_source_fullchain.unlink()
            os.symlink(str(outside), paths.tls_source_fullchain)
            with patch.object(ACTIVATE, "_require_prepare", return_value={}), patch.object(
                ACTIVATE, "_read_receipt", return_value={}
            ), self.assertRaisesRegex(ACTIVATE.EmergencyActivationError, "escapes"):
                ACTIVATE.pin_tls(campaign=self._campaign(), paths=paths, profile="telegram-only")

    def test_local_tls_probe_uses_local_sni_and_real_http_line_endings(self) -> None:
        sent: list[bytes] = []
        server_names: list[str] = []

        class RawConnection:
            def __enter__(self) -> "RawConnection":
                return self

            def __exit__(self, *_args: object) -> None:
                return None

        class TlsConnection:
            def __init__(self, response: bytes) -> None:
                self.response = response

            def __enter__(self) -> "TlsConnection":
                return self

            def __exit__(self, *_args: object) -> None:
                return None

            def sendall(self, payload: bytes) -> None:
                sent.append(payload)

            def recv(self, _size: int) -> bytes:
                response, self.response = self.response, b""
                return response

        connections = [
            TlsConnection(b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n"),
            TlsConnection(b"HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\n\r\n"),
        ]

        class Context:
            def wrap_socket(self, _raw: RawConnection, *, server_hostname: str) -> TlsConnection:
                server_names.append(server_hostname)
                return connections.pop(0)

        with patch.object(ACTIVATE.ssl, "create_default_context", return_value=Context()), patch.object(
            ACTIVATE.socket, "create_connection", side_effect=[RawConnection(), RawConnection()]
        ) as create_connection:
            ACTIVATE._local_tls_probe()

        self.assertEqual(
            create_connection.call_args_list[0].args,
            (("127.0.0.1", 443),),
        )
        self.assertEqual(server_names, [ACTIVATE.EMERGENCY_DOMAIN, ACTIVATE.EMERGENCY_DOMAIN])
        self.assertEqual(
            sent,
            [
                b"GET /api/config HTTP/1.1\r\nHost: coin.gold-trade.ir\r\n"
                b"Connection: close\r\nAccept: application/json\r\n\r\n",
                b"GET /api/sync HTTP/1.1\r\nHost: coin.gold-trade.ir\r\n"
                b"Connection: close\r\nAccept: application/json\r\n\r\n",
            ],
        )

    def test_emergency_link_creation_failure_restores_default_before_any_lifecycle_or_ufw_change(self) -> None:
        with tempfile.TemporaryDirectory(prefix="emergency-ir-nginx-") as raw:
            root = Path(raw)
            root.chmod(0o700)
            paths, original = nginx_paths(root)
            campaign = self._campaign()
            events: list[object] = []
            with patch.object(ACTIVATE.os, "symlink", side_effect=OSError("synthetic symlink failure")), self.assertRaisesRegex(
                ACTIVATE.EmergencyActivationError, "cannot move the default site"
            ):
                self._prearm(
                    paths=paths,
                    campaign=campaign,
                    package_root=nginx_package(root),
                    runner=self._runner(events, enabled=True, active=True),
                    tls_probe=lambda: events.append("tls"),
                    staging_listener=lambda port: events.append(("staging", port)),
                )
            self.assertTrue(paths.nginx_default.is_symlink())
            self.assertEqual(os.readlink(paths.nginx_default), str(original))
            self.assertFalse((paths.nginx_backup_root / f"default.before-{campaign.campaign_id}").exists())
            self.assertFalse(paths.nginx_enabled.exists() or paths.nginx_enabled.is_symlink())
            self.assertNotIn("tls", events)
            self.assertEqual(self._ufw_allow_events(events), [])

    def test_reload_failure_restores_default_preserves_emergency_link_and_skips_ufw(self) -> None:
        with tempfile.TemporaryDirectory(prefix="emergency-ir-nginx-") as raw:
            root = Path(raw)
            root.chmod(0o700)
            paths, original = nginx_paths(root)
            campaign = self._campaign()
            events: list[object] = []
            with self.assertRaisesRegex(ACTIVATE.EmergencyActivationError, "default site was restored"):
                self._prearm(
                    paths=paths,
                    campaign=campaign,
                    package_root=nginx_package(root),
                    runner=self._runner(events, enabled=True, active=True, fail_action="reload"),
                    tls_probe=lambda: events.append("tls"),
                    staging_listener=lambda port: events.append(("staging", port)),
                )
            self._assert_default_restored(paths=paths, original=original, campaign=campaign)
            self.assertNotIn("tls", events)
            self.assertEqual(self._ufw_allow_events(events), [])

    def test_inactive_nginx_is_enabled_started_probed_and_opened_last(self) -> None:
        with tempfile.TemporaryDirectory(prefix="emergency-ir-nginx-") as raw:
            root = Path(raw)
            root.chmod(0o700)
            paths, _ = nginx_paths(root)
            campaign = self._campaign()
            events: list[object] = []
            result = self._prearm(
                paths=paths,
                campaign=campaign,
                package_root=nginx_package(root),
                runner=self._runner(events, enabled=False, active=False),
                tls_probe=lambda: events.append("tls"),
                staging_listener=lambda port: events.append(("staging", port)),
            )
            self.assertEqual(result["nginx_lifecycle"]["before"], {"enabled": False, "active": False})
            self.assertEqual(result["nginx_lifecycle"]["after"], {"enabled": True, "active": True})
            self.assertEqual(result["nginx_lifecycle"]["action"], "enabled-and-started")
            command_events = [event for event in events if isinstance(event, tuple) and event[0] == "command"]
            self.assertIn(("command", ACTIVATE.SYSTEMCTL_BINARY, "enable", "nginx"), command_events)
            self.assertIn(("command", ACTIVATE.SYSTEMCTL_BINARY, "start", "nginx"), command_events)
            ufw_index = next(
                index
                for index, event in enumerate(events)
                if isinstance(event, tuple) and len(event) > 2 and event[1] == ACTIVATE.UFW_BINARY and event[2] == "allow"
            )
            self.assertLess(events.index("tls"), ufw_index)
            self.assertLess(events.index(("staging", 8213)), ufw_index)
            self.assertLess(events.index(("staging", 8443)), ufw_index)
            later_mutations = [
                event
                for event in command_events[command_events.index(events[ufw_index]) + 1 :]
                if event[1] != ACTIVATE.UFW_BINARY
            ]
            self.assertEqual(later_mutations, [])

    def test_inactive_nginx_start_failure_restores_original_disabled_inactive_lifecycle(self) -> None:
        with tempfile.TemporaryDirectory(prefix="emergency-ir-nginx-") as raw:
            root = Path(raw)
            root.chmod(0o700)
            paths, original = nginx_paths(root)
            campaign = self._campaign()
            events: list[object] = []
            with self.assertRaisesRegex(ACTIVATE.EmergencyActivationError, "default site was restored"):
                self._prearm(
                    paths=paths,
                    campaign=campaign,
                    package_root=nginx_package(root),
                    runner=self._runner(events, enabled=False, active=False, fail_action="start"),
                    tls_probe=lambda: events.append("tls"),
                    staging_listener=lambda port: events.append(("staging", port)),
                )
            self._assert_default_restored(paths=paths, original=original, campaign=campaign)
            command_events = [event for event in events if isinstance(event, tuple) and event[0] == "command"]
            self.assertIn(("command", ACTIVATE.SYSTEMCTL_BINARY, "stop", "nginx"), command_events)
            self.assertIn(("command", ACTIVATE.SYSTEMCTL_BINARY, "disable", "nginx"), command_events)
            self.assertEqual(self._ufw_allow_events(events), [])

    def test_tls_probe_failure_rolls_back_before_any_ufw_mutation(self) -> None:
        with tempfile.TemporaryDirectory(prefix="emergency-ir-nginx-") as raw:
            root = Path(raw)
            root.chmod(0o700)
            paths, original = nginx_paths(root)
            campaign = self._campaign()
            events: list[object] = []

            def failed_probe() -> None:
                events.append("tls")
                raise ACTIVATE.EmergencyActivationError("synthetic TLS probe failure")

            with self.assertRaisesRegex(ACTIVATE.EmergencyActivationError, "synthetic TLS probe failure"):
                self._prearm(
                    paths=paths,
                    campaign=campaign,
                    package_root=nginx_package(root),
                    runner=self._runner(events, enabled=True, active=True),
                    tls_probe=failed_probe,
                    staging_listener=lambda port: events.append(("staging", port)),
                )
            self._assert_default_restored(paths=paths, original=original, campaign=campaign)
            self.assertEqual(self._ufw_allow_events(events), [])

    def test_intent_directory_sync_failure_aborts_before_any_ingress_switch(self) -> None:
        with tempfile.TemporaryDirectory(prefix="emergency-ir-nginx-") as raw:
            root = Path(raw)
            root.chmod(0o700)
            paths, original = nginx_paths(root)
            campaign = self._campaign()
            events: list[object] = []
            with patch.object(
                ACTIVATE,
                "_fsync_directory",
                side_effect=ACTIVATE.EmergencyActivationError("synthetic intent directory sync failure"),
            ), self.assertRaisesRegex(ACTIVATE.EmergencyActivationError, "synthetic intent directory sync failure"):
                self._prearm(
                    paths=paths,
                    campaign=campaign,
                    package_root=nginx_package(root),
                    runner=self._runner(events, enabled=True, active=True),
                    tls_probe=lambda: events.append("tls"),
                    staging_listener=lambda port: events.append(("staging", port)),
                )
            self.assertTrue(paths.nginx_default.is_symlink())
            self.assertEqual(os.readlink(paths.nginx_default), str(original))
            self.assertFalse(paths.nginx_enabled.exists() or paths.nginx_enabled.is_symlink())
            self.assertEqual(self._ufw_allow_events(events), [])
            self.assertFalse(
                any(
                    isinstance(event, tuple)
                    and len(event) > 2
                    and event[0] == "command"
                    and (
                        event[1] == ACTIVATE.NGINX_BINARY
                        or (
                            event[1] == ACTIVATE.SYSTEMCTL_BINARY
                            and event[2] in {"enable", "disable", "start", "stop", "reload"}
                        )
                    )
                    for event in events
                )
            )

    def test_ufw_outcome_failure_preserves_journaled_candidate_after_local_probes(self) -> None:
        with tempfile.TemporaryDirectory(prefix="emergency-ir-nginx-") as raw:
            root = Path(raw)
            root.chmod(0o700)
            paths, _ = nginx_paths(root)
            campaign = self._campaign()
            events: list[object] = []
            with self.assertRaisesRegex(ACTIVATE.EmergencyActivationError, "verification-only recovery"):
                self._prearm(
                    paths=paths,
                    campaign=campaign,
                    package_root=nginx_package(root),
                    runner=self._runner(events, enabled=True, active=True, fail_action="ufw"),
                    tls_probe=lambda: events.append("tls"),
                    staging_listener=lambda port: events.append(("staging", port)),
                )
            self.assertTrue(paths.nginx_enabled.is_symlink())
            self.assertFalse(paths.nginx_default.exists() or paths.nginx_default.is_symlink())
            self.assertFalse(
                (paths.nginx_backup_root / f"emergency-site.failed-{campaign.campaign_id}").exists()
            )
            self.assertTrue(ACTIVATE._receipt_path(paths, campaign.campaign_id, ACTIVATE.PREARM_INTENT_STAGE).is_file())
            ufw_index = next(
                index
                for index, event in enumerate(events)
                if isinstance(event, tuple) and len(event) > 2 and event[1] == ACTIVATE.UFW_BINARY and event[2] == "allow"
            )
            self.assertLess(events.index("tls"), ufw_index)
            self.assertLess(events.index(("staging", 8213)), ufw_index)
            self.assertLess(events.index(("staging", 8443)), ufw_index)

    def test_post_ufw_inspection_error_preserves_candidate_for_verification_only_recovery(self) -> None:
        with tempfile.TemporaryDirectory(prefix="emergency-ir-nginx-") as raw:
            root = Path(raw)
            root.chmod(0o700)
            paths, _ = nginx_paths(root)
            campaign = self._campaign()
            package_root = nginx_package(root)
            events: list[object] = []
            with self.assertRaisesRegex(ACTIVATE.EmergencyActivationError, "verification-only recovery"):
                self._prearm(
                    paths=paths,
                    campaign=campaign,
                    package_root=package_root,
                    runner=self._runner(
                        events,
                        enabled=True,
                        active=True,
                        fail_action="ufw-status-after-allow",
                    ),
                    tls_probe=lambda: events.append("tls"),
                    staging_listener=lambda port: events.append(("staging", port)),
                )
            self.assertTrue(paths.nginx_enabled.is_symlink())
            self.assertFalse(paths.nginx_default.exists() or paths.nginx_default.is_symlink())
            self.assertFalse(
                (paths.nginx_backup_root / f"emergency-site.failed-{campaign.campaign_id}").exists()
            )
            self.assertTrue(ACTIVATE._receipt_path(paths, campaign.campaign_id, ACTIVATE.PREARM_INTENT_STAGE).is_file())
            self.assertFalse(ACTIVATE._receipt_path(paths, campaign.campaign_id, "prearmed").exists())
            self.assertEqual(len(self._ufw_allow_events(events)), 1)
            allow_event = self._ufw_allow_events(events)[0]
            command_events = [event for event in events if isinstance(event, tuple) and event[0] == "command"]
            later_systemctl_actions = [
                event
                for event in command_events[command_events.index(allow_event) + 1 :]
                if event[1] == ACTIVATE.SYSTEMCTL_BINARY and event[2] in {"enable", "disable", "start", "stop", "reload"}
            ]
            self.assertEqual(later_systemctl_actions, [])

            recovery_events: list[object] = []
            recovery_runner = self._runner(
                recovery_events,
                enabled=True,
                active=True,
                ufw_rule_present=True,
            )
            original_read = ACTIVATE._read_receipt

            def read_receipt(
                paths_arg: ACTIVATE.ActivationPaths,
                campaign_arg: ACTIVATE.VerifiedCampaign,
                *,
                stage: str,
            ) -> dict[str, object]:
                if stage == "api-ready":
                    return {}
                return original_read(paths_arg, campaign_arg, stage=stage)

            with patch.object(ACTIVATE, "_require_prepare", return_value={"package_root": str(package_root)}), patch.object(
                ACTIVATE,
                "_require_pinned_tls",
                return_value=(paths.tls_pinned_fullchain, paths.tls_pinned_privkey),
            ), patch.object(ACTIVATE, "_read_receipt", side_effect=read_receipt), patch.object(
                ACTIVATE, "_local_tls_probe", side_effect=lambda: recovery_events.append("tls")
            ), patch.object(
                ACTIVATE, "_check_staging_listener", side_effect=lambda port: recovery_events.append(("staging", port))
            ):
                recovered = ACTIVATE._recover_prearm_receipt(
                    paths=paths,
                    campaign=campaign,
                    profile="telegram-only",
                    runner=recovery_runner,
                )
            self.assertEqual(recovered["ufw"]["action"], "added")
            self.assertTrue(ACTIVATE._receipt_path(paths, campaign.campaign_id, "prearmed").is_file())
            self.assertEqual(self._ufw_allow_events(recovery_events), [])
            self.assertFalse(
                any(
                    isinstance(event, tuple)
                    and len(event) > 2
                    and event[0] == "command"
                    and event[1] == ACTIVATE.SYSTEMCTL_BINARY
                    and event[2] in {"enable", "disable", "start", "stop", "reload"}
                    for event in recovery_events
                )
            )

    def test_preexisting_owned_ufw_rule_is_journaled_without_another_firewall_mutation(self) -> None:
        with tempfile.TemporaryDirectory(prefix="emergency-ir-nginx-") as raw:
            root = Path(raw)
            root.chmod(0o700)
            paths, _ = nginx_paths(root)
            campaign = self._campaign()
            events: list[object] = []
            result = self._prearm(
                paths=paths,
                campaign=campaign,
                package_root=nginx_package(root),
                runner=self._runner(
                    events,
                    enabled=True,
                    active=True,
                    ufw_rule_present=True,
                    ufw_ipv6_rule_present=True,
                ),
                tls_probe=lambda: events.append("tls"),
                staging_listener=lambda port: events.append(("staging", port)),
            )
            self.assertEqual(result["ufw"]["action"], "already-present")
            self.assertTrue(result["ufw"]["rule_present_before"])
            self.assertTrue(result["ufw"]["ipv6_rule_present_final"])
            self.assertEqual(self._ufw_allow_events(events), [])
            self.assertFalse(
                any(
                    isinstance(event, tuple)
                    and len(event) > 2
                    and event[0] == "command"
                    and event[1] == ACTIVATE.UFW_BINARY
                    and event[2] == "delete"
                    for event in events
                )
            )
            intent = ACTIVATE._read_receipt(paths, campaign, stage=ACTIVATE.PREARM_INTENT_STAGE)
            self.assertTrue(intent["ufw_rule_present_before"])

    def test_unowned_overlapping_ufw_rule_blocks_before_ingress_switch_or_mutation(self) -> None:
        with tempfile.TemporaryDirectory(prefix="emergency-ir-nginx-") as raw:
            root = Path(raw)
            root.chmod(0o700)
            paths, original = nginx_paths(root)
            campaign = self._campaign()
            events: list[object] = []
            with self.assertRaisesRegex(ACTIVATE.EmergencyActivationError, "unowned overlapping"):
                self._prearm(
                    paths=paths,
                    campaign=campaign,
                    package_root=nginx_package(root),
                    runner=self._runner(
                        events,
                        enabled=True,
                        active=True,
                        ufw_conflicting_rule=True,
                    ),
                    tls_probe=lambda: events.append("tls"),
                    staging_listener=lambda port: events.append(("staging", port)),
                )
            self.assertTrue(paths.nginx_default.is_symlink())
            self.assertEqual(os.readlink(paths.nginx_default), str(original))
            self.assertFalse(paths.nginx_enabled.exists() or paths.nginx_enabled.is_symlink())
            self.assertFalse(ACTIVATE._receipt_path(paths, campaign.campaign_id, ACTIVATE.PREARM_INTENT_STAGE).exists())
            self.assertEqual(self._ufw_allow_events(events), [])
            self.assertFalse(
                any(
                    isinstance(event, tuple)
                    and len(event) > 2
                    and event[0] == "command"
                    and event[1] == ACTIVATE.SYSTEMCTL_BINARY
                    and event[2] in {"enable", "disable", "start", "stop", "reload"}
                    for event in events
                )
            )

    def test_final_receipt_failure_recovery_is_verification_only_and_blocks_wrong_final_state(self) -> None:
        with tempfile.TemporaryDirectory(prefix="emergency-ir-nginx-") as raw:
            root = Path(raw)
            root.chmod(0o700)
            paths, _ = nginx_paths(root)
            campaign = self._campaign()
            package_root = nginx_package(root)
            events: list[object] = []
            runner = self._runner(events, enabled=True, active=True)
            original_read = ACTIVATE._read_receipt
            original_write = ACTIVATE._write_receipt

            def read_receipt(
                paths_arg: ACTIVATE.ActivationPaths,
                campaign_arg: ACTIVATE.VerifiedCampaign,
                *,
                stage: str,
            ) -> dict[str, object]:
                if stage == "api-ready":
                    return {}
                return original_read(paths_arg, campaign_arg, stage=stage)

            def fail_final_receipt(
                paths_arg: ACTIVATE.ActivationPaths,
                campaign_arg: ACTIVATE.VerifiedCampaign,
                *,
                stage: str,
                payload: dict[str, object],
            ) -> None:
                if stage == "prearmed":
                    raise ACTIVATE.EmergencyActivationError("synthetic final receipt failure")
                original_write(paths_arg, campaign_arg, stage=stage, payload=payload)

            with patch.object(ACTIVATE, "_require_prepare", return_value={"package_root": str(package_root)}), patch.object(
                ACTIVATE,
                "_require_pinned_tls",
                return_value=(paths.tls_pinned_fullchain, paths.tls_pinned_privkey),
            ), patch.object(ACTIVATE, "_read_receipt", side_effect=read_receipt), patch.object(
                ACTIVATE, "_local_tls_probe", side_effect=lambda: events.append("tls")
            ), patch.object(
                ACTIVATE, "_check_staging_listener", side_effect=lambda port: events.append(("staging", port))
            ), patch.object(ACTIVATE, "_write_receipt", side_effect=fail_final_receipt), self.assertRaisesRegex(
                ACTIVATE.EmergencyActivationError, "final receipt could not be registered"
            ):
                ACTIVATE.prearm(campaign=campaign, paths=paths, profile="telegram-only", runner=runner)

            self.assertTrue(paths.nginx_enabled.is_symlink())
            self.assertFalse(paths.nginx_default.exists() or paths.nginx_default.is_symlink())
            self.assertTrue(ACTIVATE._receipt_path(paths, campaign.campaign_id, ACTIVATE.PREARM_INTENT_STAGE).is_file())
            self.assertTrue(ACTIVATE._receipt_path(paths, campaign.campaign_id, ACTIVATE.PREARM_ARMED_STAGE).is_file())
            self.assertFalse(ACTIVATE._receipt_path(paths, campaign.campaign_id, "prearmed").exists())
            self.assertEqual(len(self._ufw_allow_events(events)), 1)

            wrong_events: list[object] = []
            wrong_runner = self._runner(wrong_events, enabled=True, active=True, ufw_rule_present=False)
            with patch.object(ACTIVATE, "_require_prepare", return_value={"package_root": str(package_root)}), patch.object(
                ACTIVATE,
                "_require_pinned_tls",
                return_value=(paths.tls_pinned_fullchain, paths.tls_pinned_privkey),
            ), patch.object(ACTIVATE, "_read_receipt", side_effect=read_receipt), patch.object(
                ACTIVATE, "_local_tls_probe", side_effect=lambda: wrong_events.append("tls")
            ), patch.object(
                ACTIVATE, "_check_staging_listener", side_effect=lambda port: wrong_events.append(("staging", port))
            ), self.assertRaisesRegex(ACTIVATE.EmergencyActivationError, "does not have the bounded UFW rule"):
                ACTIVATE.prearm(campaign=campaign, paths=paths, profile="telegram-only", runner=wrong_runner)
            self.assertEqual(self._ufw_allow_events(wrong_events), [])
            self.assertFalse(
                any(
                    isinstance(event, tuple)
                    and len(event) > 2
                    and event[0] == "command"
                    and event[1] == ACTIVATE.SYSTEMCTL_BINARY
                    and event[2] in {"enable", "disable", "start", "stop", "reload"}
                    for event in wrong_events
                )
            )

            events.clear()
            with patch.object(ACTIVATE, "_require_prepare", return_value={"package_root": str(package_root)}), patch.object(
                ACTIVATE,
                "_require_pinned_tls",
                return_value=(paths.tls_pinned_fullchain, paths.tls_pinned_privkey),
            ), patch.object(ACTIVATE, "_read_receipt", side_effect=read_receipt), patch.object(
                ACTIVATE, "_local_tls_probe", side_effect=lambda: events.append("tls")
            ), patch.object(
                ACTIVATE, "_check_staging_listener", side_effect=lambda port: events.append(("staging", port))
            ):
                recovered = ACTIVATE.prearm(campaign=campaign, paths=paths, profile="telegram-only", runner=runner)
            self.assertEqual(recovered["ufw"]["action"], "added")
            self.assertTrue(ACTIVATE._receipt_path(paths, campaign.campaign_id, "prearmed").is_file())
            self.assertEqual(self._ufw_allow_events(events), [])
            self.assertFalse(
                any(
                    isinstance(event, tuple)
                    and len(event) > 2
                    and event[0] == "command"
                    and event[1] in {ACTIVATE.NGINX_BINARY, ACTIVATE.SYSTEMCTL_BINARY}
                    and (
                        event[1] == ACTIVATE.NGINX_BINARY
                        or event[2] in {"enable", "disable", "start", "stop", "reload"}
                    )
                    for event in events
                )
            )

    def test_partial_final_receipt_fails_closed_without_rearming_or_deleting_ufw(self) -> None:
        with tempfile.TemporaryDirectory(prefix="emergency-ir-nginx-") as raw:
            root = Path(raw)
            root.chmod(0o700)
            paths, _ = nginx_paths(root)
            campaign = self._campaign()
            package_root = nginx_package(root)
            events: list[object] = []
            original_write = ACTIVATE._write_receipt

            def partial_final_receipt(
                paths_arg: ACTIVATE.ActivationPaths,
                campaign_arg: ACTIVATE.VerifiedCampaign,
                *,
                stage: str,
                payload: dict[str, object],
            ) -> None:
                if stage == "prearmed":
                    root_file(ACTIVATE._receipt_path(paths_arg, campaign_arg.campaign_id, stage), b"{")
                    raise ACTIVATE.EmergencyActivationError("synthetic partial final receipt")
                original_write(paths_arg, campaign_arg, stage=stage, payload=payload)

            with patch.object(
                ACTIVATE,
                "_require_pinned_tls",
                return_value=(paths.tls_pinned_fullchain, paths.tls_pinned_privkey),
            ), patch.object(ACTIVATE, "_write_receipt", side_effect=partial_final_receipt), self.assertRaisesRegex(
                ACTIVATE.EmergencyActivationError, "final receipt could not be registered"
            ):
                self._prearm(
                    paths=paths,
                    campaign=campaign,
                    package_root=package_root,
                    runner=self._runner(events, enabled=True, active=True),
                    tls_probe=lambda: events.append("tls"),
                    staging_listener=lambda port: events.append(("staging", port)),
                )

            recovery_events: list[object] = []
            original_read = ACTIVATE._read_receipt

            def read_receipt(
                paths_arg: ACTIVATE.ActivationPaths,
                campaign_arg: ACTIVATE.VerifiedCampaign,
                *,
                stage: str,
            ) -> dict[str, object]:
                if stage == "api-ready":
                    return {}
                return original_read(paths_arg, campaign_arg, stage=stage)

            with patch.object(ACTIVATE, "_require_prepare", return_value={"package_root": str(package_root)}), patch.object(
                ACTIVATE,
                "_require_pinned_tls",
                return_value=(paths.tls_pinned_fullchain, paths.tls_pinned_privkey),
            ), patch.object(ACTIVATE, "_read_receipt", side_effect=read_receipt), patch.object(
                ACTIVATE, "_local_tls_probe", side_effect=lambda: recovery_events.append("tls")
            ), patch.object(
                ACTIVATE, "_check_staging_listener", side_effect=lambda port: recovery_events.append(("staging", port))
            ), self.assertRaises(ACTIVATE.EmergencyActivationError):
                ACTIVATE._recover_prearm_receipt(
                    paths=paths,
                    campaign=campaign,
                    profile="telegram-only",
                    runner=self._runner(recovery_events, enabled=True, active=True, ufw_rule_present=True),
                )
            self.assertEqual(self._ufw_allow_events(recovery_events), [])
            self.assertFalse(
                any(
                    isinstance(event, tuple)
                    and len(event) > 2
                    and event[0] == "command"
                    and event[1] == ACTIVATE.SYSTEMCTL_BINARY
                    and event[2] in {"enable", "disable", "start", "stop", "reload"}
                    for event in recovery_events
                )
            )

    def test_runner_exception_during_candidate_test_restores_default_and_never_opens_ufw(self) -> None:
        with tempfile.TemporaryDirectory(prefix="emergency-ir-nginx-") as raw:
            root = Path(raw)
            root.chmod(0o700)
            paths, original = nginx_paths(root)
            campaign = self._campaign()
            events: list[object] = []
            base_runner = self._runner(events, enabled=True, active=True)

            def runner(command: list[str], **kwargs: object) -> object:
                if command == [ACTIVATE.NGINX_BINARY, "-t"]:
                    raise OSError("synthetic nginx binary failure")
                return base_runner(command, **kwargs)

            with self.assertRaisesRegex(ACTIVATE.EmergencyActivationError, "default site was restored"):
                self._prearm(
                    paths=paths,
                    campaign=campaign,
                    package_root=nginx_package(root),
                    runner=runner,
                    tls_probe=lambda: events.append("tls"),
                    staging_listener=lambda port: events.append(("staging", port)),
                )
            self._assert_default_restored(paths=paths, original=original, campaign=campaign)
            self.assertNotIn("tls", events)
            self.assertEqual(self._ufw_allow_events(events), [])

    def test_subprocess_error_during_candidate_test_restores_default_and_never_opens_ufw(self) -> None:
        with tempfile.TemporaryDirectory(prefix="emergency-ir-nginx-") as raw:
            root = Path(raw)
            root.chmod(0o700)
            paths, original = nginx_paths(root)
            campaign = self._campaign()
            events: list[object] = []
            base_runner = self._runner(events, enabled=True, active=True)

            def runner(command: list[str], **kwargs: object) -> object:
                if command == [ACTIVATE.NGINX_BINARY, "-t"]:
                    raise subprocess.TimeoutExpired(command, 60)
                return base_runner(command, **kwargs)

            with self.assertRaisesRegex(ACTIVATE.EmergencyActivationError, "default site was restored"):
                self._prearm(
                    paths=paths,
                    campaign=campaign,
                    package_root=nginx_package(root),
                    runner=runner,
                    tls_probe=lambda: events.append("tls"),
                    staging_listener=lambda port: events.append(("staging", port)),
                )
            self._assert_default_restored(paths=paths, original=original, campaign=campaign)
            self.assertNotIn("tls", events)
            self.assertEqual(self._ufw_allow_events(events), [])


if __name__ == "__main__":
    unittest.main()
