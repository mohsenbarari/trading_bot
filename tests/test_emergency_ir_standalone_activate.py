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


def semantic_package(root: Path, *, sms: bool = False) -> Path:
    """Create only the sealed control-file shape needed by verifier gate tests."""

    package_root = root / "semantic-package"
    files = {
        "scripts/verify_emergency_ir_standalone.py": b"# verifier\n",
        "scripts/verify_emergency_ir_image_provenance.py": b"# image verifier\n",
        "scripts/verify_emergency_ir_sms_egress_image.py": b"# sms image verifier\n",
        "deploy/emergency-ir/docker-compose.standalone.yml": b"name: trading-bot-emergency-ir\n",
        "deploy/emergency-ir/nginx.standalone.conf.template": b"server {}\n",
        "deploy/emergency-ir/reset-emergency-sessions.sql": b"BEGIN; COMMIT;\n",
    }
    if sms:
        files.update(
            {
                "deploy/emergency-ir/docker-compose.sms-otp.yml": b"services: {}\n",
                "deploy/emergency-ir/nginx.sms-otp.conf.template": b"server {}\n",
                "deploy/emergency-ir/nginx.sms-otp.rate-limit.conf": b"limit_req_zone $binary_remote_addr zone=otp:10m;\n",
                "deploy/emergency-ir/sms-egress.nginx.conf": b"server {}\n",
            }
        )
    for relative, payload in files.items():
        root_file(package_root / relative, payload)
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
        "scripts/verify_emergency_ir_sms_egress_image.py": b"# sms provenance\n",
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
        self.assertEqual(
            [item["stage"] for item in plan["stages"]],
            ["prepare", "images", "database", "api", "tls", "firewall", "prearm"],
        )
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
        ufw_default_allow: bool = False,
    ) -> object:
        action_counts: dict[str, int] = {}
        state = {
            "enabled": enabled,
            "active": active,
            "ufw_rule_present": ufw_rule_present,
            "ufw_ipv6_rule_present": ufw_ipv6_rule_present,
            "ufw_conflicting_rule": ufw_conflicting_rule,
            "ufw_default_allow": ufw_default_allow,
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
            if call in ([ACTIVATE.UFW_BINARY, "status", "verbose"], [ACTIVATE.UFW_BINARY, "status", "numbered"]):
                if fail_action == "ufw-status-after-allow" and state["ufw_rule_present"]:
                    raise OSError("synthetic post-UFW status failure")
                lines = ["Status: active"]
                if call[-1] == "verbose":
                    default = "allow" if state["ufw_default_allow"] else "deny"
                    lines.extend(
                        [
                            "Logging: off",
                            f"Default: {default} (incoming), allow (outgoing), deny (routed)",
                            "",
                            "To                         Action      From",
                            "--                         ------      ----",
                        ]
                    )
                else:
                    lines.extend(["", "     To                         Action      From", "     --                         ------      ----"])
                prefix = "" if call[-1] == "verbose" else "[ 1] "
                lines.append(f"{prefix}22/tcp                     ALLOW IN    Anywhere                   # three-site-wa-ir-control")
                prefix = "" if call[-1] == "verbose" else "[ 2] "
                lines.append(f"{prefix}22/tcp (v6)                ALLOW IN    Anywhere (v6)              # three-site-wa-ir-control")
                if state["ufw_rule_present"]:
                    prefix = "" if call[-1] == "verbose" else "[ 3] "
                    lines.append(
                        f"{prefix}80,443/tcp                ALLOW IN    Anywhere                   # trading-bot-emergency-ir"
                    )
                if state["ufw_ipv6_rule_present"]:
                    prefix = "" if call[-1] == "verbose" else "[ 4] "
                    lines.append(
                        f"{prefix}80,443/tcp (v6)           ALLOW IN    Anywhere (v6)              # trading-bot-emergency-ir"
                    )
                if state["ufw_conflicting_rule"]:
                    prefix = "" if call[-1] == "verbose" else "[ 5] "
                    lines.append(f"{prefix}80/tcp                    ALLOW IN    Anywhere                   # another-owner")
                return completed(call, stdout="\n".join(lines) + "\n")
            if call == [ACTIVATE.UFW_BINARY, "show", "added"]:
                lines = ["Added user rules (see 'ufw status' for running firewall):"]
                lines.append(ACTIVATE.UFW_CONTROL_SHOW_ADDED_RULE)
                if state["ufw_rule_present"]:
                    lines.append(ACTIVATE.UFW_SHOW_ADDED_OWNED_RULE)
                if state["ufw_conflicting_rule"]:
                    lines.append("ufw allow 80/tcp comment 'another-owner'")
                return completed(call, stdout="\n".join(lines) + "\n")
            if call[:1] == [ACTIVATE.UFW_BINARY] and len(call) > 1 and call[1] == "allow":
                if fail_action == "ufw":
                    return completed(call, 1)
                state["ufw_rule_present"] = True
                state["ufw_ipv6_rule_present"] = True
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
        def attestation(**_kwargs: object) -> dict[str, object]:
            return {
                "ufw_baseline": dict(
                    ACTIVATE._capture_ufw_rule_state(runner=runner, expect_emergency_rule=False).baseline
                )
            }
        with patch.object(
            ACTIVATE,
            "_require_pinned_tls",
            return_value=(paths.tls_pinned_fullchain, paths.tls_pinned_privkey),
        ), patch.object(
            ACTIVATE, "_require_firewall_attestation", side_effect=attestation
        ), patch.object(ACTIVATE, "_assert_nginx_public_listener_inventory"):
            return ACTIVATE._prearm_nginx(
                paths=paths,
                campaign=campaign,
                package_root=package_root,
                profile="telegram-only",
                runner=runner,
                tls_probe=tls_probe,
                staging_listener=staging_listener,
            )

    def _attempts(self, paths: ACTIVATE.ActivationPaths, campaign: ACTIVATE.VerifiedCampaign) -> list[ACTIVATE.PrearmAttempt]:
        root = ACTIVATE._activation_campaign_root(paths, campaign.campaign_id)
        result: list[ACTIVATE.PrearmAttempt] = []
        for path in root.glob("prearm-attempt-*-intent.json"):
            matched = ACTIVATE.PREARM_ATTEMPT_STAGE_RE.fullmatch(path.stem)
            assert matched is not None
            result.append(ACTIVATE._attempt_paths(paths, campaign, matched.group(1)))
        return result

    def _prearm_stage(
        self,
        *,
        paths: ACTIVATE.ActivationPaths,
        campaign: ACTIVATE.VerifiedCampaign,
        package_root: Path,
        runner: object,
        events: list[object],
    ) -> dict[str, object]:
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

        def attestation(**_kwargs: object) -> dict[str, object]:
            return {
                "ufw_baseline": dict(
                    ACTIVATE._capture_ufw_rule_state(runner=runner, expect_emergency_rule=False).baseline
                )
            }

        with patch.object(ACTIVATE, "_require_prepare", return_value={"package_root": str(package_root)}), patch.object(
            ACTIVATE, "_require_pinned_tls", return_value=(paths.tls_pinned_fullchain, paths.tls_pinned_privkey)
        ), patch.object(ACTIVATE, "_read_receipt", side_effect=read_receipt), patch.object(
            ACTIVATE, "_require_firewall_attestation", side_effect=attestation
        ), patch.object(ACTIVATE, "_local_tls_probe", side_effect=lambda: events.append("tls")), patch.object(
            ACTIVATE, "_check_staging_listener", side_effect=lambda port: events.append(("staging", port))
        ), patch.object(ACTIVATE, "_assert_nginx_public_listener_inventory"):
            return ACTIVATE.prearm(campaign=campaign, paths=paths, profile="telegram-only", runner=runner)

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

    def test_tls_pin_rejects_a_regular_certbot_live_leaf(self) -> None:
        with tempfile.TemporaryDirectory(prefix="emergency-ir-tls-") as raw:
            root = Path(raw)
            root.chmod(0o700)
            paths, _ = nginx_paths(root)
            live_fullchain = paths.tls_source_fullchain
            payload = live_fullchain.resolve().read_bytes()
            live_fullchain.unlink()
            root_file(live_fullchain, payload)
            with self.assertRaisesRegex(ACTIVATE.EmergencyActivationError, "must be a root-controlled symlink"):
                ACTIVATE._read_certbot_tls_source(
                    source=live_fullchain,
                    archive_root=paths.tls_source_archive_root,
                    label="Emergency certificate",
                    private=False,
                )

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

    def test_nginx_listener_inventory_accepts_only_the_expected_site(self) -> None:
        with tempfile.TemporaryDirectory(prefix="emergency-ir-nginx-inventory-") as raw:
            root = Path(raw)
            root.chmod(0o700)
            paths, _ = nginx_paths(root)
            allowed = paths.nginx_default
            controlled = "\n".join(
                (
                    f"# configuration file {allowed}:",
                    "server {",
                    "    listen 80 default_server;",
                    "    listen [::]:80 default_server;",
                    "}",
                )
            )

            def runner(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[object]:
                self.assertEqual(command, [ACTIVATE.NGINX_BINARY, "-T"])
                return completed(command, stdout=controlled)

            ACTIVATE._assert_nginx_public_listener_inventory(
                paths=paths, allowed_config=allowed, required_ports=frozenset({80}), runner=runner
            )
            with self.assertRaisesRegex(ACTIVATE.EmergencyActivationError, "uncontrolled public 80/443 listener"):
                ACTIVATE._assert_nginx_public_listener_inventory(
                    paths=paths, allowed_config=allowed, required_ports=frozenset({80, 443}), runner=runner
                )

            competing = controlled + "\n" + "\n".join(
                (
                    f"# configuration file {paths.nginx_enabled}:",
                    "server { listen 443 ssl; }",
                )
            )

            def competing_runner(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[object]:
                return completed(command, stdout=competing)

            with self.assertRaisesRegex(ACTIVATE.EmergencyActivationError, "uncontrolled public 80/443 listener"):
                ACTIVATE._assert_nginx_public_listener_inventory(
                    paths=paths,
                    allowed_config=allowed,
                    required_ports=frozenset({80}),
                    runner=competing_runner,
                )

    def test_failed_packaged_verifier_blocks_database_before_any_docker_call(self) -> None:
        with tempfile.TemporaryDirectory(prefix="emergency-ir-semantic-gate-") as raw:
            root = Path(raw)
            root.chmod(0o700)
            paths, _ = nginx_paths(root)
            package_root = semantic_package(root)
            root_file(paths.runtime_env, b"EMERGENCY_AUTH_PROFILE=telegram-only\n")
            campaign = self._campaign()
            prepared = {
                "package_root": str(package_root),
                "source_release_sha": SOURCE_SHA,
                "emergency_patch_sha": PATCH_SHA,
            }
            calls: list[list[str]] = []

            def failed_verifier(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[object]:
                calls.append(list(command))
                return completed(command, 1)

            with self.assertRaisesRegex(ACTIVATE.EmergencyActivationError, "semantic verification failed"):
                ACTIVATE._verify_rendered_emergency_semantics(
                    package_root=package_root,
                    paths=paths,
                    prepared=prepared,
                    profile="telegram-only",
                    runner=failed_verifier,
                )
            self.assertEqual(len(calls), 1)
            self.assertIn("verify_emergency_ir_standalone.py", calls[0][3])
            self.assertFalse(any(call and call[0] == ACTIVATE.DOCKER_BINARY for call in calls))

            with patch.object(ACTIVATE, "_require_prepare", return_value=prepared), patch.object(
                ACTIVATE, "_read_receipt", return_value={}
            ), patch.object(ACTIVATE, "read_settings_bundle", return_value=ACTIVATE.SettingsBundle(b"{}", "token")), patch.object(
                ACTIVATE, "_ensure_current_link"
            ), patch.object(ACTIVATE, "_run_renderer"), patch.object(
                ACTIVATE,
                "_verify_rendered_emergency_semantics",
                side_effect=ACTIVATE.EmergencyActivationError("unsafe rendered configuration"),
            ), patch.object(ACTIVATE, "_assert_fresh_docker_resources") as fresh_resources, patch.object(
                ACTIVATE, "_docker_result"
            ) as docker_result, self.assertRaisesRegex(ACTIVATE.EmergencyActivationError, "unsafe rendered configuration"):
                ACTIVATE.database(campaign=campaign, paths=paths, profile="telegram-only", runner=failed_verifier)
            fresh_resources.assert_not_called()
            docker_result.assert_not_called()

    def _legacy_emergency_link_creation_failure_restores_default_before_any_lifecycle_or_ufw_change(self) -> None:
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

    def _legacy_reload_failure_restores_default_preserves_emergency_link_and_skips_ufw(self) -> None:
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

    def _legacy_inactive_nginx_is_enabled_started_probed_and_opened_last(self) -> None:
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

    def _legacy_inactive_nginx_start_failure_restores_original_disabled_inactive_lifecycle(self) -> None:
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

    def _legacy_tls_probe_failure_rolls_back_before_any_ufw_mutation(self) -> None:
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

    def _legacy_intent_directory_sync_failure_aborts_before_any_ingress_switch(self) -> None:
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

    def _legacy_ufw_outcome_failure_preserves_journaled_candidate_after_local_probes(self) -> None:
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

    def _legacy_post_ufw_inspection_error_preserves_candidate_for_verification_only_recovery(self) -> None:
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

    def _legacy_preexisting_owned_ufw_rule_is_journaled_without_another_firewall_mutation(self) -> None:
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

    def _legacy_unowned_overlapping_ufw_rule_blocks_before_ingress_switch_or_mutation(self) -> None:
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

    def _legacy_final_receipt_failure_recovery_is_verification_only_and_blocks_wrong_final_state(self) -> None:
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

    def _legacy_partial_final_receipt_fails_closed_without_rearming_or_deleting_ufw(self) -> None:
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

    def _legacy_runner_exception_during_candidate_test_restores_default_and_never_opens_ufw(self) -> None:
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

    def _legacy_subprocess_error_during_candidate_test_restores_default_and_never_opens_ufw(self) -> None:
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

    def test_fresh_prearm_rejects_owned_broad_and_default_allow_before_switch(self) -> None:
        cases = (
            ("owned", {"ufw_rule_present": True, "ufw_ipv6_rule_present": True}, "already exposes"),
            ("broad", {"ufw_conflicting_rule": True}, "safe ingress contract"),
            ("default", {"ufw_default_allow": True}, "incoming-deny"),
        )
        for name, kwargs, message in cases:
            with self.subTest(name=name), tempfile.TemporaryDirectory(prefix="emergency-ir-nginx-") as raw:
                root = Path(raw)
                root.chmod(0o700)
                paths, original = nginx_paths(root)
                campaign = self._campaign()
                events: list[object] = []
                with self.assertRaisesRegex(ACTIVATE.EmergencyActivationError, message):
                    self._prearm(
                        paths=paths,
                        campaign=campaign,
                        package_root=nginx_package(root),
                        runner=self._runner(events, enabled=True, active=True, **kwargs),
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
                        and event[1] == ACTIVATE.SYSTEMCTL_BINARY
                        and event[2] in {"enable", "disable", "start", "stop", "reload"}
                        for event in events
                    )
                )

    def test_pre_ufw_failure_is_immutably_aborted_and_a_new_attempt_can_succeed(self) -> None:
        with tempfile.TemporaryDirectory(prefix="emergency-ir-nginx-") as raw:
            root = Path(raw)
            root.chmod(0o700)
            paths, original = nginx_paths(root)
            campaign = self._campaign()
            package_root = nginx_package(root)
            first_events: list[object] = []

            def failed_probe() -> None:
                first_events.append("tls")
                raise ACTIVATE.EmergencyActivationError("synthetic TLS probe failure")

            with self.assertRaisesRegex(ACTIVATE.EmergencyActivationError, "attempt was aborted"):
                self._prearm(
                    paths=paths,
                    campaign=campaign,
                    package_root=package_root,
                    runner=self._runner(first_events, enabled=True, active=True),
                    tls_probe=failed_probe,
                    staging_listener=lambda port: first_events.append(("staging", port)),
                )
            attempts = self._attempts(paths, campaign)
            self.assertEqual(len(attempts), 1)
            first = attempts[0]
            self.assertTrue(
                ACTIVATE._receipt_path(paths, campaign.campaign_id, ACTIVATE._attempt_stage(first.attempt_id, "aborted")).is_file()
            )
            self.assertTrue(paths.nginx_default.is_symlink())
            self.assertEqual(os.readlink(paths.nginx_default), str(original))
            self.assertEqual(self._ufw_allow_events(first_events), [])

            retry_events: list[object] = []
            result = self._prearm(
                paths=paths,
                campaign=campaign,
                package_root=package_root,
                runner=self._runner(retry_events, enabled=True, active=True),
                tls_probe=lambda: retry_events.append("tls"),
                staging_listener=lambda port: retry_events.append(("staging", port)),
            )
            self.assertEqual(result["ufw"]["action"], "added")
            self.assertEqual(len(self._attempts(paths, campaign)), 2)
            self.assertEqual(len(self._ufw_allow_events(retry_events)), 1)

    def test_crashed_pre_pending_candidate_is_rolled_back_then_fresh_attempt_runs(self) -> None:
        with tempfile.TemporaryDirectory(prefix="emergency-ir-nginx-") as raw:
            root = Path(raw)
            root.chmod(0o700)
            paths, original = nginx_paths(root)
            campaign = self._campaign()
            package_root = nginx_package(root)
            events: list[object] = []
            runner = self._runner(events, enabled=True, active=True)
            original_write = ACTIVATE._write_receipt

            def interrupt_pending(
                paths_arg: ACTIVATE.ActivationPaths,
                campaign_arg: ACTIVATE.VerifiedCampaign,
                *,
                stage: str,
                payload: dict[str, object],
            ) -> None:
                if stage.endswith("-ufw-pending"):
                    raise KeyboardInterrupt("synthetic crash before UFW")
                original_write(paths_arg, campaign_arg, stage=stage, payload=payload)

            with patch.object(ACTIVATE, "_write_receipt", side_effect=interrupt_pending), self.assertRaises(KeyboardInterrupt):
                self._prearm(
                    paths=paths,
                    campaign=campaign,
                    package_root=package_root,
                    runner=runner,
                    tls_probe=lambda: events.append("tls"),
                    staging_listener=lambda port: events.append(("staging", port)),
                )
            self.assertTrue(paths.nginx_enabled.is_symlink())
            self.assertFalse(paths.nginx_default.exists() or paths.nginx_default.is_symlink())
            self.assertEqual(self._ufw_allow_events(events), [])

            recovered = self._prearm_stage(
                paths=paths, campaign=campaign, package_root=package_root, runner=runner, events=events
            )
            self.assertEqual(recovered["ufw"]["action"], "added")
            self.assertEqual(len(self._ufw_allow_events(events)), 1)
            self.assertNotEqual(os.readlink(paths.nginx_default) if paths.nginx_default.is_symlink() else "", str(original))
            attempts = self._attempts(paths, campaign)
            self.assertEqual(len(attempts), 2)
            self.assertTrue(
                any(
                    ACTIVATE._receipt_path(
                        paths, campaign.campaign_id, ACTIVATE._attempt_stage(item.attempt_id, "aborted")
                    ).is_file()
                    for item in attempts
                )
            )

    def test_ufw_pending_is_verification_only_after_post_arm_inspection_failure(self) -> None:
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
                    runner=self._runner(events, enabled=True, active=True, fail_action="ufw-status-after-allow"),
                    tls_probe=lambda: events.append("tls"),
                    staging_listener=lambda port: events.append(("staging", port)),
                )
            self.assertTrue(paths.nginx_enabled.is_symlink())
            self.assertFalse(paths.nginx_default.exists() or paths.nginx_default.is_symlink())
            self.assertEqual(len(self._ufw_allow_events(events)), 1)
            attempt = self._attempts(paths, campaign)[0]
            self.assertTrue(
                ACTIVATE._receipt_path(paths, campaign.campaign_id, ACTIVATE._attempt_stage(attempt.attempt_id, "ufw-pending")).is_file()
            )

            recovery_events: list[object] = []
            recovered = self._prearm_stage(
                paths=paths,
                campaign=campaign,
                package_root=package_root,
                runner=self._runner(
                    recovery_events, enabled=True, active=True, ufw_rule_present=True, ufw_ipv6_rule_present=True
                ),
                events=recovery_events,
            )
            self.assertEqual(recovered["ufw"]["action"], "added")
            self.assertEqual(self._ufw_allow_events(recovery_events), [])
            self.assertFalse(
                any(
                    isinstance(event, tuple)
                    and len(event) > 2
                    and event[1] == ACTIVATE.SYSTEMCTL_BINARY
                    and event[2] in {"enable", "disable", "start", "stop", "reload"}
                    for event in recovery_events
                )
            )

    def test_final_receipt_failure_recovers_only_after_exact_dual_stack_verification(self) -> None:
        with tempfile.TemporaryDirectory(prefix="emergency-ir-nginx-") as raw:
            root = Path(raw)
            root.chmod(0o700)
            paths, _ = nginx_paths(root)
            campaign = self._campaign()
            package_root = nginx_package(root)
            events: list[object] = []
            runner = self._runner(events, enabled=True, active=True)
            original_write = ACTIVATE._write_receipt

            def fail_final(
                paths_arg: ACTIVATE.ActivationPaths,
                campaign_arg: ACTIVATE.VerifiedCampaign,
                *,
                stage: str,
                payload: dict[str, object],
            ) -> None:
                if stage == "prearmed":
                    raise ACTIVATE.EmergencyActivationError("synthetic final receipt failure")
                original_write(paths_arg, campaign_arg, stage=stage, payload=payload)

            with patch.object(ACTIVATE, "_write_receipt", side_effect=fail_final), self.assertRaisesRegex(
                ACTIVATE.EmergencyActivationError, "final receipt could not be registered"
            ):
                self._prearm(
                    paths=paths,
                    campaign=campaign,
                    package_root=package_root,
                    runner=runner,
                    tls_probe=lambda: events.append("tls"),
                    staging_listener=lambda port: events.append(("staging", port)),
                )
            self.assertEqual(len(self._ufw_allow_events(events)), 1)
            attempt = self._attempts(paths, campaign)[0]
            self.assertTrue(
                ACTIVATE._receipt_path(paths, campaign.campaign_id, ACTIVATE._attempt_stage(attempt.attempt_id, "armed")).is_file()
            )

            missing_v6_events: list[object] = []
            with self.assertRaises(ACTIVATE.EmergencyActivationError):
                self._prearm_stage(
                    paths=paths,
                    campaign=campaign,
                    package_root=package_root,
                    runner=self._runner(missing_v6_events, enabled=True, active=True, ufw_rule_present=True),
                    events=missing_v6_events,
                )
            self.assertEqual(self._ufw_allow_events(missing_v6_events), [])

            recovered_events: list[object] = []
            recovered = self._prearm_stage(
                paths=paths,
                campaign=campaign,
                package_root=package_root,
                runner=self._runner(
                    recovered_events, enabled=True, active=True, ufw_rule_present=True, ufw_ipv6_rule_present=True
                ),
                events=recovered_events,
            )
            self.assertEqual(recovered["ufw"]["action"], "added")
            self.assertEqual(self._ufw_allow_events(recovered_events), [])

    def test_intent_directory_sync_failure_happens_before_any_ingress_mutation(self) -> None:
        with tempfile.TemporaryDirectory(prefix="emergency-ir-nginx-") as raw:
            root = Path(raw)
            root.chmod(0o700)
            paths, original = nginx_paths(root)
            campaign = self._campaign()
            events: list[object] = []
            with patch.object(
                ACTIVATE, "_fsync_directory", side_effect=ACTIVATE.EmergencyActivationError("synthetic directory sync failure")
            ), self.assertRaisesRegex(ACTIVATE.EmergencyActivationError, "synthetic directory sync failure"):
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

    def test_raw_firewall_counter_normalization_preserves_rules_and_detects_rule_drift(self) -> None:
        iptables_first = b"*filter\n:INPUT ACCEPT [10:20]\n-A INPUT -p tcp --dport 22 -j ACCEPT [2:3]\nCOMMIT\n"
        iptables_counters_only = b"*filter\n:INPUT ACCEPT [11:99]\n-A INPUT -p tcp --dport 22 -j ACCEPT [3:300]\nCOMMIT\n"
        iptables_changed_rule = b"*filter\n:INPUT ACCEPT [11:99]\n-A INPUT -p tcp --dport 23 -j ACCEPT [3:300]\nCOMMIT\n"
        self.assertEqual(
            ACTIVATE._normalized_iptables_save(iptables_first),
            ACTIVATE._normalized_iptables_save(iptables_counters_only),
        )
        self.assertNotEqual(
            ACTIVATE._normalized_iptables_save(iptables_first),
            ACTIVATE._normalized_iptables_save(iptables_changed_rule),
        )
        first = b"table ip filter { chain input { counter packets 10 bytes 20 accept } }\n"
        counters_only = b"table ip filter { chain input { counter packets 11 bytes 99 accept } }\n"
        changed_rule = b"table ip filter { chain input { counter packets 11 bytes 99 drop } }\n"
        self.assertEqual(ACTIVATE._normalized_nft_ruleset(first), ACTIVATE._normalized_nft_ruleset(counters_only))
        self.assertNotEqual(ACTIVATE._normalized_nft_ruleset(first), ACTIVATE._normalized_nft_ruleset(changed_rule))

    def test_raw_firewall_allows_8443_staging_dnat_but_rejects_external_80_or_443(self) -> None:
        staging_only = (
            b"*nat\n"
            b":PREROUTING ACCEPT [0:0]\n"
            b":DOCKER - [0:0]\n"
            b"-A PREROUTING -m addrtype --dst-type LOCAL -j DOCKER [1:2]\n"
            b"-A DOCKER ! -i br-123 -p tcp -m tcp --dport 8443 -j DNAT --to-destination 172.26.0.2:443 [3:4]\n"
            b"COMMIT\n"
            b"*filter\n"
            b":DOCKER - [0:0]\n"
            b"-A DOCKER -d 172.26.0.2/32 ! -i br-123 -o br-123 -p tcp -m tcp --dport 443 -j ACCEPT [5:6]\n"
            b"COMMIT\n"
        )
        nft_staging_only = (
            b"table ip nat {\n"
            b" chain DOCKER { tcp dport 8443 dnat to 172.26.0.2:443 }\n"
            b"}\n"
            b"table ip filter {\n"
            b" chain DOCKER { ip daddr 172.26.0.2 tcp dport 443 accept }\n"
            b"}\n"
        )
        ACTIVATE._assert_no_raw_external_http_exposure(
            iptables=staging_only,
            ip6tables=b"*filter\n:INPUT ACCEPT [0:0]\nCOMMIT\n",
            nft=nft_staging_only,
        )
        for bad in (
            staging_only.replace(b"--dport 8443", b"--dport 80"),
            staging_only.replace(b"--dport 8443", b"--dport 443"),
        ):
            with self.subTest(raw=bad), self.assertRaisesRegex(ACTIVATE.EmergencyActivationError, "TCP 80/443"):
                ACTIVATE._assert_no_raw_external_http_exposure(
                    iptables=bad,
                    ip6tables=b"*filter\n:INPUT ACCEPT [0:0]\nCOMMIT\n",
                    nft=nft_staging_only,
                )
        with self.assertRaisesRegex(ACTIVATE.EmergencyActivationError, "TCP 80/443"):
            ACTIVATE._assert_no_raw_external_http_exposure(
                iptables=staging_only,
                ip6tables=b"*filter\n:INPUT ACCEPT [0:0]\nCOMMIT\n",
                nft=nft_staging_only.replace(b"dport 8443", b"dport 443"),
            )

    def test_firewall_attestation_rejects_raw_baseline_drift(self) -> None:
        with tempfile.TemporaryDirectory(prefix="emergency-ir-firewall-") as raw:
            root = Path(raw)
            root.chmod(0o700)
            paths, _ = nginx_paths(root)
            campaign = self._campaign()
            baseline = {
                "schema": ACTIVATE.FIREWALL_ATTESTATION_SCHEMA,
                "ufw_version": "ufw 0.36.2",
                "ufw_baseline": {"schema": ACTIVATE.UFW_BASELINE_SCHEMA, "status_verbose": ["x"], "status_numbered": ["y"], "show_added": ["z"]},
                "ufw_static_sha256": {
                    "defaults": "a" * 64,
                    "before_rules": "a" * 64,
                    "after_rules": "a" * 64,
                    "before6_rules": "a" * 64,
                    "after6_rules": "a" * 64,
                },
                "iptables_save_sha256": "b" * 64,
                "ip6tables_save_sha256": "c" * 64,
                "nft_ruleset_sha256": "d" * 64,
            }
            ACTIVATE._write_receipt(paths, campaign, stage="firewall-attested", payload={"profile": "telegram-only", "attestation": baseline})
            with patch.object(ACTIVATE, "_capture_firewall_attestation", return_value=baseline):
                self.assertEqual(
                    ACTIVATE._require_firewall_attestation(
                        paths=paths, campaign=campaign, profile="telegram-only", runner=self._runner([], enabled=True, active=True)
                    ),
                    baseline,
                )
            drifted = dict(baseline)
            drifted["nft_ruleset_sha256"] = "e" * 64
            with patch.object(ACTIVATE, "_capture_firewall_attestation", return_value=drifted), self.assertRaisesRegex(
                ACTIVATE.EmergencyActivationError, "differs from the confirmed attestation"
            ):
                ACTIVATE._require_firewall_attestation(
                    paths=paths, campaign=campaign, profile="telegram-only", runner=self._runner([], enabled=True, active=True)
                )


if __name__ == "__main__":
    unittest.main()
