"""Fail-closed contracts for PRIVATE_PRIMARY secret provision and inventory."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from scripts import inventory_private_primary_active_runtime as inventory
from scripts import prepare_private_primary_control_release as preparer
from scripts import provision_private_primary_secrets as provisioner
from scripts import render_private_primary_runtime_env as renderer
from scripts.manage_market_pipeline_stage3 import portable_image_content_digest
from scripts.prepare_market_pipeline_release import DYNAMIC_VALUES


REPO_ROOT = Path(__file__).resolve().parents[1]
RELEASE_SCRIPT = REPO_ROOT / "scripts" / "production_deploy_online.sh"
SECRET = b"unit-test-secret-material-not-for-production-use!!"


def _write(path: Path, payload: str | bytes, *, mode: int = 0o440) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(payload, str):
        payload = payload.encode()
    path.write_bytes(payload)
    os.chmod(path, mode)
    return path


def _json(path: Path, document: dict[str, object], *, mode: int = 0o600) -> Path:
    return _write(
        path,
        json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n",
        mode=mode,
    )


def _runtime(role: str, mounts: list[dict[str, str]]) -> dict[str, object]:
    root = inventory.ALLOWED_ADOPTED_DATA_ROOTS[role]
    return {
        "schema": inventory.INVENTORY_SCHEMA,
        "environment": "production",
        "status": "PASS",
        "host_role": role,
        "project_name": inventory.EXPECTED_PROJECT,
        "feed_mode": inventory.EXPECTED_FEED_MODE,
        "pipeline_mode": "live",
        "historical_path_name": True,
        "production_owned": True,
        "adopted_data_root": root,
        "adopted_snapshot_root": f"{root}/snapshots",
        "historical_secret_root": inventory.HISTORICAL_SECRET_ROOT,
        "canonical_secret_root": inventory.CANONICAL_SECRET_ROOT,
        "bind_ip": "10.240.1.10" if role == "bot" else "10.240.1.20",
        "safe_env": {
            "MARKET_PIPELINE_FEED_MODE": inventory.EXPECTED_FEED_MODE,
            "MARKET_PIPELINE_IMAGE": "sha256:" + "a" * 64,
            "MARKET_PIPELINE_RELEASE_SHA": "b" * 40,
            "MARKET_PIPELINE_MODE": "live",
        },
        "containers": [],
        "container_ids": ["ab" * 6],
        "bind_mounts": [],
        "secret_mounts": mounts,
        "env_file_secret_paths": [
            {"env_key": item["env_key"], "path": item["source"]}
            for item in mounts
            if item.get("env_key")
        ],
        "capture_authority_markers": [],
        "mount_identity_sha256": "c" * 64,
        "decision": "adopt_live_roots",
        "relocation_required": False,
        "secrets_disclosed": False,
    }


def _openssl(*arguments: str) -> None:
    result = subprocess.run(["openssl", *arguments], check=False, capture_output=True, text=True)
    if result.returncode:
        raise AssertionError(result.stderr or result.stdout or "openssl failed")


def _make_pki(directory: Path, *, bot_ip: str, web_ip: str) -> None:
    ca_key = directory / "ca.key"
    ca_cert = directory / "transport-ca.pem"
    _openssl("genrsa", "-out", str(ca_key), "2048")
    _openssl(
        "req",
        "-x509",
        "-new",
        "-nodes",
        "-key",
        str(ca_key),
        "-sha256",
        "-days",
        "30",
        "-subj",
        "/CN=private-primary-test-ca",
        "-out",
        str(ca_cert),
    )
    for role, ip, eku in (
        ("bot", bot_ip, "clientAuth"),
        ("web", web_ip, "serverAuth"),
    ):
        key = directory / f"{role}-transport-key.pem"
        csr = directory / f"{role}.csr"
        ext = directory / f"{role}.ext"
        cert = directory / f"{role}-transport-cert.pem"
        _openssl("genrsa", "-out", str(key), "2048")
        _openssl(
            "req",
            "-new",
            "-key",
            str(key),
            "-subj",
            f"/CN=private-primary-test-{role}",
            "-out",
            str(csr),
        )
        ext.write_text(
            f"subjectAltName=IP:{ip}\nextendedKeyUsage={eku}\n",
            encoding="ascii",
        )
        _openssl(
            "x509",
            "-req",
            "-in",
            str(csr),
            "-CA",
            str(ca_cert),
            "-CAkey",
            str(ca_key),
            "-CAcreateserial",
            "-out",
            str(cert),
            "-days",
            "30",
            "-sha256",
            "-extfile",
            str(ext),
        )
        os.chmod(key, 0o440)
        os.chmod(cert, 0o440)
    os.chmod(ca_cert, 0o440)


def _account_config(account: str) -> dict[str, object]:
    from core.market_intelligence.private_capture import ACCOUNT_SOURCES

    sources = ACCOUNT_SOURCES[account]
    order = (
        provisioner.ACCOUNT1_SOURCES if account == "account1" else provisioner.ACCOUNT2_SOURCES
    )
    return {
        "contract": "market_telegram_capture_config/1.0",
        "account": account,
        "api_id": 1,
        "api_hash": "ab" * 16,
        "session_filename": f"{account}.session",
        "sources": [
            {"source_code": code, "peer_id": -(index + 1)}
            for index, code in enumerate(order)
            if code in sources
        ],
    }


class ProvisionPrivatePrimarySecretsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.workspace = Path(tempfile.mkdtemp(prefix="pp-secret-", dir="/root"))
        os.chmod(self.workspace, 0o700)
        self.sources = self.workspace / "live-secrets"
        self.sources.mkdir(mode=0o700)
        self.canonical = self.workspace / "pp-secret-canonical"
        self.receipts = self.workspace / "receipts"
        self.receipts.mkdir(mode=0o700)

    def tearDown(self) -> None:
        shutil.rmtree(self.workspace, ignore_errors=True)

    def test_inventory_covers_both_roles_without_secret_bytes(self) -> None:
        _write(self.sources / "hmac-active", SECRET)
        for role in ("bot", "web"):
            payload = provisioner.inventory_secrets(role=role, secret_root=self.canonical)
            names = {item["env_key"] for item in payload["secrets"]}
            expected = {item[0] for item in provisioner.SECRET_SPECS[role]}
            self.assertEqual(names, expected)
            encoded = json.dumps(payload)
            self.assertNotIn(SECRET.decode(), encoded)
            self.assertFalse(payload["generated"])
            self.assertFalse(payload["secrets_disclosed"])

    def test_atomic_install_reuses_and_refuses_invalid(self) -> None:
        source = _write(self.sources / "hmac-active", SECRET)
        destination = self.canonical / "hmac-active"
        self.assertEqual(provisioner._install_atomic(source, destination), "installed")
        self.assertEqual(provisioner._install_atomic(source, destination), "reused")
        other = _write(self.sources / "hmac-active-other", SECRET + b"x")
        with self.assertRaisesRegex(provisioner.ProvisionError, "existing_divergent_refused"):
            provisioner._install_atomic(other, destination)
        broken = self.canonical / "broken"
        _write(broken, SECRET, mode=0o644)
        with self.assertRaisesRegex(provisioner.ProvisionError, "existing_invalid_refused"):
            provisioner._install_atomic(source, broken)

    def test_continuity_secret_may_reuse_sibling_of_live_mounts(self) -> None:
        live = _write(self.sources / "hmac-active", SECRET)
        extra = _write(self.sources / "research-archive.key", b"R" * 32)
        runtime = _runtime(
            "web",
            [
                {
                    "source": str(live),
                    "destination": "/run/secrets/market_hmac_active",
                    "env_key": "MARKET_HMAC_ACTIVE_FILE",
                }
            ],
        )
        runtime["historical_secret_root"] = str(self.sources)
        chosen = provisioner._select_source(
            runtime,
            "MARKET_RESEARCH_ENCRYPTION_KEY_FILE",
            "research-archive.key",
            continuity_required=True,
        )
        self.assertEqual(chosen, extra)

    def test_prepare_requires_live_source_and_does_not_generate(self) -> None:
        hmac = _write(self.sources / "hmac-active", SECRET)
        runtime = _runtime(
            "bot",
            [{"source": str(hmac), "destination": "/run/secrets/market_hmac_active", "env_key": "MARKET_HMAC_ACTIVE_FILE"}],
        )
        with self.assertRaisesRegex(provisioner.ProvisionError, "live_source_missing"):
            provisioner.prepare_secrets(role="bot", secret_root=self.canonical, inventory=runtime)

    def test_prepare_rehomes_proven_mounts_and_keeps_source(self) -> None:
        mounts = []
        for env_key, filename, _, _ in provisioner.SECRET_SPECS["bot"]:
            path = _write(self.sources / filename, SECRET if "hmac" in filename else SECRET)
            if filename.endswith(".pem"):
                continue
            mounts.append(
                {
                    "source": str(path),
                    "destination": f"/run/secrets/{filename}",
                    "env_key": env_key,
                }
            )
        _make_pki(self.sources, bot_ip=provisioner.BOT_BIND_IP, web_ip=provisioner.WEB_BIND_IP)
        for env_key, filename, _, _ in provisioner.SECRET_SPECS["bot"]:
            if filename.endswith(".pem"):
                mounts.append(
                    {
                        "source": str(self.sources / filename),
                        "destination": f"/run/secrets/{filename}",
                        "env_key": env_key,
                    }
                )
        runtime = _runtime("bot", mounts)
        payload = provisioner.prepare_secrets(
            role="bot", secret_root=self.canonical, inventory=runtime
        )
        self.assertEqual(payload["generated_count"], 0)
        self.assertFalse(payload["source_deleted"])
        for env_key, filename, _, _ in provisioner.SECRET_SPECS["bot"]:
            self.assertTrue((self.canonical / filename).is_file())
            self.assertTrue((self.sources / filename).is_file())
        encoded = json.dumps(payload)
        self.assertNotIn(SECRET.decode(), encoded)

    def test_certificate_hmac_account_and_mtls(self) -> None:
        self.canonical.mkdir(mode=0o700)
        _make_pki(self.canonical, bot_ip=provisioner.BOT_BIND_IP, web_ip=provisioner.WEB_BIND_IP)
        _write(self.canonical / "hmac-active", os.urandom(32))
        _write(self.canonical / "hmac-previous", os.urandom(32))
        _write(
            self.canonical / "account1-config.json",
            json.dumps(_account_config("account1")),
        )
        _write(
            self.canonical / "account2-config.json",
            json.dumps(_account_config("account2")),
        )
        _write(self.canonical / "research-archive.key", b"R" * 32)
        _write(self.canonical / "postgres-password", os.urandom(32))
        pki = provisioner.verify_certificate_pair(
            ca=self.canonical / "transport-ca.pem",
            cert=self.canonical / "bot-transport-cert.pem",
            key=self.canonical / "bot-transport-key.pem",
            expected_ip=provisioner.BOT_BIND_IP,
            role="bot",
        )
        self.assertTrue(pki["chain_ok"])
        self.assertTrue(pki["cert_key_match"])
        self.assertTrue(pki["san_ok"])
        self.assertTrue(pki["eku_ok"])
        self.assertTrue(pki["expiry_ok"])
        hmac_result = provisioner.verify_hmac(
            self.canonical / "hmac-active", self.canonical / "hmac-previous"
        )
        self.assertTrue(hmac_result["active_ok"])
        self.assertTrue(hmac_result["previous_ok"])
        account1 = provisioner.verify_account_config(
            self.canonical / "account1-config.json", account="account1"
        )
        self.assertEqual(set(account1["source_codes"]), set(provisioner.ACCOUNT1_SOURCES))
        account2 = provisioner.verify_account_config(
            self.canonical / "account2-config.json", account="account2"
        )
        self.assertEqual(set(account2["source_codes"]), set(provisioner.ACCOUNT2_SOURCES))
        self.assertTrue(
            provisioner.verify_mtls(
                ca=self.canonical / "transport-ca.pem",
                server_cert=self.canonical / "web-transport-cert.pem",
                server_key=self.canonical / "web-transport-key.pem",
                client_cert=self.canonical / "bot-transport-cert.pem",
                client_key=self.canonical / "bot-transport-key.pem",
            )
        )
        self.assertTrue(provisioner.verify_research_key(self.canonical / "research-archive.key")["roundtrip_ok"])
        self.assertTrue(provisioner.verify_postgres_password(self.canonical / "postgres-password")["file_ok"])

    def test_cli_refuses_tmp_secret_root(self) -> None:
        receipt = self.receipts / "blocked.json"
        self.assertEqual(
            provisioner.main(
                [
                    "inventory",
                    "--confirm",
                    provisioner.CONFIRMATION,
                    "--role",
                    "bot",
                    "--secret-root",
                    "/tmp/pp-secret",
                    "--receipt",
                    str(receipt),
                ]
            ),
            2,
        )


class RuntimeContinuityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.workspace = Path(tempfile.mkdtemp(prefix="pp-cont-", dir="/root"))
        os.chmod(self.workspace, 0o700)

    def tearDown(self) -> None:
        shutil.rmtree(self.workspace, ignore_errors=True)

    def test_validate_inventory_rejects_unapproved_root(self) -> None:
        document = _runtime("bot", [])
        document["adopted_data_root"] = "/srv/trading-bot/other-staging"
        document["adopted_snapshot_root"] = "/srv/trading-bot/other-staging/snapshots"
        with self.assertRaisesRegex(inventory.InventoryError, "inventory_data_root_not_adopted"):
            inventory.validate_inventory(document, role="bot")

    def test_topology_accepts_only_receipt_bound_adopted_root(self) -> None:
        adopted = inventory.ALLOWED_ADOPTED_DATA_ROOTS["bot"]
        source = _write(
            self.workspace / "bot.source.env",
            (
                f"MARKET_BOT_DATA_ROOT={adopted}\n"
                f"MARKET_PRODUCT_SNAPSHOT_ROOT={adopted}/snapshots\n"
                f"MARKET_TRANSPORT_CA_FILE={inventory.CANONICAL_SECRET_ROOT}/transport-ca.pem\n"
            ),
            mode=0o600,
        )
        with self.assertRaisesRegex(preparer.PrepareError, "bot_data_root_mismatch"):
            preparer.validate_topology_source(source, role="bot", repository_root=REPO_ROOT)
        receipt = _json(
            self.workspace / "continuity.json",
            _runtime("bot", []),
        )
        values = preparer.validate_topology_source(
            source,
            role="bot",
            repository_root=REPO_ROOT,
            continuity_receipt=receipt,
        )
        self.assertEqual(values["MARKET_BOT_DATA_ROOT"], adopted)
        other = _write(
            self.workspace / "other.source.env",
            (
                "MARKET_BOT_DATA_ROOT=/srv/trading-bot/unrelated-staging\n"
                "MARKET_PRODUCT_SNAPSHOT_ROOT=/srv/trading-bot/unrelated-staging/snapshots\n"
            ),
            mode=0o600,
        )
        with self.assertRaisesRegex(preparer.PrepareError, "bot_data_root_mismatch"):
            preparer.validate_topology_source(
                other,
                role="bot",
                repository_root=REPO_ROOT,
                continuity_receipt=receipt,
            )

    def test_old_env_keeps_shadow_identity_and_same_data_root(self) -> None:
        runtime = _runtime("bot", [])
        receipt = _json(self.workspace / "bot-runtime.json", runtime)
        live = _write(
            self.workspace / "live.env",
            (
                "MARKET_PIPELINE_PROJECT_NAME=market-private-pipeline-stage13-shadow\n"
                "MARKET_PIPELINE_FEED_MODE=PRIVATE_SHADOW\n"
                "MARKET_PIPELINE_MODE=live\n"
                f"MARKET_PIPELINE_IMAGE=sha256:{'a' * 64}\n"
                f"MARKET_PIPELINE_RELEASE_SHA={'b' * 40}\n"
                "MARKET_PRIVATE_BIND_IP=10.240.1.10\n"
                "MARKET_HMAC_ACTIVE_FILE=/srv/trading-bot/secure/agent-access/market-data-staging/hmac-active\n"
            ),
            mode=0o600,
        )
        payload = renderer.render(
            role="bot",
            inventory_path=receipt,
            live_env_path=live,
            old_env_path=self.workspace / "bot.old.env",
            topology_path=self.workspace / "bot.source.env",
            receipt_path=self.workspace / "old-env-receipt.json",
        )
        old_text = Path(payload["old_env_path"]).read_text(encoding="utf-8")
        topology = Path(payload["topology_source_path"]).read_text(encoding="utf-8")
        self.assertIn("MARKET_PIPELINE_FEED_MODE=PRIVATE_SHADOW", old_text)
        self.assertIn(f"MARKET_BOT_DATA_ROOT={inventory.ALLOWED_ADOPTED_DATA_ROOTS['bot']}", old_text)
        self.assertIn(f"MARKET_BOT_DATA_ROOT={inventory.ALLOWED_ADOPTED_DATA_ROOTS['bot']}", topology)
        self.assertIn(f"{inventory.CANONICAL_SECRET_ROOT}/hmac-active", topology)
        self.assertNotIn("PRIVATE_PRIMARY", old_text)
        self.assertNotIn(SECRET.decode(), old_text + topology)
        topology_keys = {
            line.split("=", 1)[0]
            for line in topology.splitlines()
            if "=" in line
        }
        self.assertFalse(DYNAMIC_VALUES.intersection(topology_keys))

    def test_web_topology_binds_authorized_backfill_contract(self) -> None:
        runtime = _runtime("web", [])
        receipt = _json(self.workspace / "web-runtime.json", runtime)
        live = _write(
            self.workspace / "web-live.env",
            (
                "MARKET_PIPELINE_PROJECT_NAME=market-private-pipeline-stage13-shadow\n"
                "MARKET_PIPELINE_FEED_MODE=PRIVATE_SHADOW\n"
                "MARKET_PIPELINE_MODE=live\n"
                f"MARKET_PIPELINE_IMAGE=sha256:{'a' * 64}\n"
                f"MARKET_PIPELINE_RELEASE_SHA={'b' * 40}\n"
                "MARKET_PRIVATE_BIND_IP=10.240.1.20\n"
                "MARKET_CAPTURE_BACKFILL_SOURCE_CODES=GROUP_1\n"
            ),
            mode=0o600,
        )
        payload = renderer.render(
            role="web",
            inventory_path=receipt,
            live_env_path=live,
            old_env_path=self.workspace / "web.old.env",
            topology_path=self.workspace / "web.source.env",
            receipt_path=self.workspace / "web-old-env-receipt.json",
        )
        topology = Path(payload["topology_source_path"]).read_text(encoding="utf-8")
        self.assertIn("MARKET_CAPTURE_BACKFILL_NOT_BEFORE_UTC=2026-08-25T09:33:00Z", topology)
        self.assertIn(
            "MARKET_CAPTURE_BACKFILL_SOURCE_CODES=MELTED_PRIMARY_FLOW,GROUP_1,GROUP_2",
            topology,
        )
        self.assertIn("MARKET_CAPTURE_BACKFILL_MAX_MESSAGES=100000", topology)

    def test_portable_digest_equality_and_mismatch(self) -> None:
        document = {
            "Os": "linux",
            "Architecture": "amd64",
            "Created": "2026-01-01T00:00:00Z",
            "Config": {"User": "10001:10001", "Env": ["PATH=/usr/bin"]},
            "RootFS": {"Type": "layers", "Layers": ["sha256:" + "d" * 64]},
        }
        first = portable_image_content_digest(document)
        second = portable_image_content_digest(document)
        self.assertEqual(first, second)
        changed = dict(document)
        changed["Created"] = "2026-01-02T00:00:00Z"
        self.assertNotEqual(first, portable_image_content_digest(changed))
        self.assertNotIn("PATH=/usr/bin", first)

    def test_shell_collects_both_preflight_roles(self) -> None:
        source = RELEASE_SCRIPT.read_text(encoding="utf-8")
        body = source.split("run_market_pipeline_two_host_preflight() {", 1)[1]
        body = body.split("\nprepare_market_pipeline_two_host_preflight() {", 1)[0]
        self.assertIn("bot_rc", body)
        self.assertIn("web_rc", body)
        self.assertLess(body.index("--role bot"), body.index("--role web"))
        self.assertLess(body.index("bot_rc=$"), body.index("--role web"))
        self.assertIn("after collecting both role diagnostics", body)
        self.assertIn("portable_content_digest", body)
        self.assertIn("market_pipeline_two_host_preflight/1.1", body)
        self.assertIn("inventory-private-primary-runtime", source)
        self.assertIn("provision-private-primary-secrets", source)
        self.assertIn("render-private-primary-runtime-env", source)
        self.assertIn("PYTHONPATH=", source)
        self.assertIn("production-web-secret-verify", source)
        self.assertIn("private-primary-mtls-verify.json", source)

    def test_prepare_skips_apply_on_adopted_roots(self) -> None:
        source = RELEASE_SCRIPT.read_text(encoding="utf-8")
        body = source.split("run_prepare_private_primary_control_release() {", 1)[1]
        body = body.split("\nrun_private_primary_choreography_controller() {", 1)[0]
        self.assertIn("continuity-receipt", body)
        self.assertIn("adopted bot path contract inspect", body)
        self.assertIn("local_bot_data_root", body)


if __name__ == "__main__":
    unittest.main()
