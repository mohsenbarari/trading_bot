from __future__ import annotations

import hashlib
import importlib.util
import io
import json
from pathlib import Path
import stat
import sys
import tarfile
import tempfile
import unittest


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
        self.assertEqual([item["stage"] for item in plan["stages"]], ["prepare", "images", "database", "api", "prearm"])
        self.assertEqual(
            plan["stages"][0]["confirm"],
            "activate-emergency-ir:20260801T220000Z-emergency-ir-01:" + "d" * 64 + ":telegram-only:prepare",
        )
        self.assertIn("volume deletion", plan["never"])


if __name__ == "__main__":
    unittest.main()
