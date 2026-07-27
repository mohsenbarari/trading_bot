import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import socket
import stat
import subprocess
import tempfile
import threading
import time
import unittest
from unittest import mock

from core.secure_file_io import sha256_secure_file, verify_hash_chained_jsonl
from scripts import prepare_webapp_ir_tls_dns as worker


CAMPAIGN_ID = "7fb08095-7a9e-4a92-9fa9-3f9a301b2944"
OPERATION_ID = "167a5b2f-c3b7-4cd4-9af3-30cc1b13e719"
RELEASE_SHA = "a" * 40
VALIDATION = "abcDEF0123456789_abcdefghijklmnopqrstuvwxyz-XYZ"


def a_record(ip: str = "65.109.220.59") -> dict:
    return {
        "id": "a-record-id",
        "type": "a",
        "name": "coin",
        "value": [{"ip": ip, "port": None, "weight": 100, "country": ""}],
        "ttl": 120,
        "cloud": False,
        "upstream_https": "default",
        "ip_filter_mode": {
            "count": "single",
            "order": "none",
            "geo_filter": "none",
        },
    }


class FakeArvan:
    def __init__(self, *, extra_records: list[dict] | None = None) -> None:
        self.records = [a_record(), *(extra_records or [])]
        self.calls: list[tuple[str, str, dict | None]] = []
        self.next_id = 1

    def __call__(
        self,
        method: str,
        url: str,
        token: str,
        payload: dict | None,
    ) -> dict:
        if token != "secret-token":
            raise AssertionError("unexpected token")
        self.calls.append((method, url, json.loads(json.dumps(payload)) if payload else None))
        if method == "GET":
            return {"data": json.loads(json.dumps(self.records))}
        if method == "POST":
            assert payload is not None
            record = {
                "id": f"txt-{self.next_id}",
                **payload,
                "created_at": "2026-07-27T20:00:00Z",
                "updated_at": "2026-07-27T20:00:00Z",
            }
            self.next_id += 1
            self.records.append(record)
            return {"status": True, "data": json.loads(json.dumps(record))}
        if method == "DELETE":
            record_id = url.rsplit("/", 1)[-1]
            self.records = [record for record in self.records if record["id"] != record_id]
            return {"status": True}
        raise AssertionError(method)

    @property
    def current_a(self) -> dict:
        return next(record for record in self.records if record["type"] == "a")

    @property
    def txt_records(self) -> list[dict]:
        return [record for record in self.records if record["type"] == "txt"]


def private_dir(path: Path) -> Path:
    path.mkdir(parents=True, mode=0o700, exist_ok=True)
    path.chmod(0o700)
    return path


def write_private_json(path: Path, payload: dict) -> Path:
    path.write_text(
        json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    path.chmod(0o600)
    return path


def runtime_source_binding() -> dict:
    return {
        "schema": f"{worker.SCHEMA_PREFIX}.runtime-source-binding.v1",
        "release_sha": RELEASE_SHA,
        "git_head": RELEASE_SHA,
        "repository_root_sha256": "a" * 64,
        "worker_path": "scripts/prepare_webapp_ir_tls_dns.py",
        "worker_sha256": "b" * 64,
        "secure_file_helper_path": "core/secure_file_io.py",
        "secure_file_helper_sha256": "c" * 64,
        "tracked_files_clean": True,
    }


def run_checked(argv: list[str | Path]) -> None:
    subprocess.run(
        [os.fspath(value) for value in argv],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def unused_port() -> int:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])
    finally:
        listener.close()


class LiveHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path != "/health/live":
            self.send_response(404)
            self.end_headers()
            return
        body = json.dumps(
            {
                "status": "ok",
                "physical_site": "webapp_ir",
                "logical_authority": "webapp",
            },
            separators=(",", ":"),
        ).encode("ascii")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        del format, args


class Dns01Tests(unittest.TestCase):
    def test_txt_payload_matches_official_arvan_shape(self) -> None:
        self.assertEqual(
            worker.build_txt_payload(VALIDATION),
            {
                "type": "txt",
                "name": "_acme-challenge.coin",
                "value": {"text": VALIDATION},
                "ttl": 120,
                "cloud": False,
            },
        )
        with self.assertRaisesRegex(worker.WebAppIrTlsError, "unexpected format"):
            worker.build_txt_payload("contains spaces")

    def test_create_propagate_delete_preserves_a_and_journals_exact_record(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = private_dir(Path(tmp) / "operation")
            state_dir = root / "state"
            journal = root / "journal" / "dns01.jsonl"
            api = FakeArvan()
            original_a = json.loads(json.dumps(api.current_a))

            created = worker.create_dns01_challenge(
                campaign_id=CAMPAIGN_ID,
                operation_id=OPERATION_ID,
                release_sha=RELEASE_SHA,
                validation=VALIDATION,
                token="secret-token",
                state_dir=state_dir,
                journal_path=journal,
                request_fn=api,
                propagation_fn=lambda **_: {
                    "nameservers": ["ns1.example", "ns2.example"],
                    "rounds": 2,
                },
            )

            self.assertEqual(created["status"], "created_and_propagated")
            self.assertEqual(len(api.txt_records), 1)
            self.assertEqual(api.current_a, original_a)
            state_files = list(state_dir.glob("challenge-*.json"))
            self.assertEqual(len(state_files), 1)
            self.assertEqual(stat.S_IMODE(state_files[0].stat().st_mode), 0o600)
            self.assertNotIn(VALIDATION, journal.read_text(encoding="utf-8"))

            deleted = worker.delete_dns01_challenge(
                campaign_id=CAMPAIGN_ID,
                operation_id=OPERATION_ID,
                release_sha=RELEASE_SHA,
                validation=VALIDATION,
                token="secret-token",
                state_dir=state_dir,
                journal_path=journal,
                request_fn=api,
            )

            self.assertEqual(deleted["status"], "deleted_and_verified")
            self.assertEqual(api.txt_records, [])
            self.assertEqual(api.current_a, original_a)
            self.assertEqual(list(state_dir.glob("challenge-*.json")), [])
            self.assertNotIn("PUT", [call[0] for call in api.calls])
            events = verify_hash_chained_jsonl(journal)
            self.assertEqual(
                [event["event"] for event in events],
                [
                    "webapp_ir.tls.dns01.create.intent",
                    "webapp_ir.tls.dns01.create.readback",
                    "webapp_ir.tls.dns01.propagated",
                    "webapp_ir.tls.dns01.delete.intent",
                    "webapp_ir.tls.dns01.delete.readback",
                ],
            )

    def test_interrupted_hook_reconcile_uses_record_id_and_validation_hash(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = private_dir(Path(tmp) / "operation")
            state_dir = root / "state"
            journal = root / "journal" / "dns01.jsonl"
            api = FakeArvan()
            worker.create_dns01_challenge(
                campaign_id=CAMPAIGN_ID,
                operation_id=OPERATION_ID,
                release_sha=RELEASE_SHA,
                validation=VALIDATION,
                token="secret-token",
                state_dir=state_dir,
                journal_path=journal,
                request_fn=api,
                propagation_fn=lambda **_: {"stable": True},
            )

            result = worker.reconcile_dns01_state(
                campaign_id=CAMPAIGN_ID,
                operation_id=OPERATION_ID,
                release_sha=RELEASE_SHA,
                token="secret-token",
                state_dir=state_dir,
                journal_path=journal,
                request_fn=api,
            )

            self.assertEqual(result["status"], "owned_record_deleted_and_verified")
            self.assertEqual(api.txt_records, [])
            self.assertEqual(list(state_dir.glob("challenge-*.json")), [])

    def test_post_succeeded_readback_crash_is_adopted_without_second_create(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = private_dir(Path(tmp) / "operation")
            state_dir = root / "state"
            journal = root / "journal" / "dns01.jsonl"
            api = FakeArvan()
            fail_readback = True

            def interrupted_request(method, url, token, payload):
                nonlocal fail_readback
                if method == "GET" and fail_readback and api.txt_records:
                    fail_readback = False
                    raise worker.WebAppIrTlsError("simulated provider readback loss")
                return api(method, url, token, payload)

            with self.assertRaisesRegex(
                worker.WebAppIrTlsError,
                "simulated provider readback loss",
            ):
                worker.create_dns01_challenge(
                    campaign_id=CAMPAIGN_ID,
                    operation_id=OPERATION_ID,
                    release_sha=RELEASE_SHA,
                    validation=VALIDATION,
                    token="secret-token",
                    state_dir=state_dir,
                    journal_path=journal,
                    request_fn=interrupted_request,
                    propagation_fn=lambda **_: {"stable": True},
                )
            self.assertEqual(len(api.txt_records), 1)
            state_path = next(state_dir.glob("challenge-*.json"))
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(state["phase"], "create_intent")
            self.assertIsNone(state["record_id"])

            resumed = worker.create_dns01_challenge(
                campaign_id=CAMPAIGN_ID,
                operation_id=OPERATION_ID,
                release_sha=RELEASE_SHA,
                validation=VALIDATION,
                token="secret-token",
                state_dir=state_dir,
                journal_path=journal,
                request_fn=api,
                propagation_fn=lambda **_: {"stable": True},
            )
            self.assertEqual(resumed["status"], "already_present")
            self.assertEqual(
                [call[0] for call in api.calls].count("POST"),
                1,
            )
            worker.reconcile_dns01_state(
                campaign_id=CAMPAIGN_ID,
                operation_id=OPERATION_ID,
                release_sha=RELEASE_SHA,
                token="secret-token",
                state_dir=state_dir,
                journal_path=journal,
                request_fn=api,
            )
            self.assertEqual(api.txt_records, [])
            self.assertNotIn(VALIDATION, journal.read_text(encoding="utf-8"))

    def test_existing_txt_owner_is_never_overwritten_or_deleted(self) -> None:
        existing = {
            "id": "other-challenge",
            "type": "txt",
            "name": "_acme-challenge.coin",
            "value": {"text": "OtherChallengeValue012345678901234567890"},
            "ttl": 120,
            "cloud": False,
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = private_dir(Path(tmp) / "operation")
            api = FakeArvan(extra_records=[existing])
            with self.assertRaisesRegex(worker.WebAppIrTlsError, "not empty"):
                worker.create_dns01_challenge(
                    campaign_id=CAMPAIGN_ID,
                    operation_id=OPERATION_ID,
                    release_sha=RELEASE_SHA,
                    validation=VALIDATION,
                    token="secret-token",
                    state_dir=root / "state",
                    journal_path=root / "journal" / "dns01.jsonl",
                    request_fn=api,
                    propagation_fn=lambda **_: {"stable": True},
                )
            self.assertEqual(api.txt_records, [existing])
            self.assertNotIn("POST", [call[0] for call in api.calls])
            self.assertNotIn("DELETE", [call[0] for call in api.calls])

    def test_propagation_failure_removes_only_new_txt(self) -> None:
        def fail_propagation(**_: object) -> dict:
            raise worker.WebAppIrTlsError("not propagated")

        with tempfile.TemporaryDirectory() as tmp:
            root = private_dir(Path(tmp) / "operation")
            api = FakeArvan()
            with self.assertRaisesRegex(worker.WebAppIrTlsError, "not propagated"):
                worker.create_dns01_challenge(
                    campaign_id=CAMPAIGN_ID,
                    operation_id=OPERATION_ID,
                    release_sha=RELEASE_SHA,
                    validation=VALIDATION,
                    token="secret-token",
                    state_dir=root / "state",
                    journal_path=root / "journal" / "dns01.jsonl",
                    request_fn=api,
                    propagation_fn=fail_propagation,
                )
            self.assertEqual(api.txt_records, [])
            self.assertEqual(list((root / "state").glob("challenge-*.json")), [])

    def test_authoritative_propagation_requires_two_stable_rounds_on_all_ns(self) -> None:
        calls: list[list[str]] = []

        def fake_run(argv, **kwargs):
            del kwargs
            normalized = [os.fspath(item) for item in argv]
            calls.append(normalized)
            if "NS" in normalized:
                stdout = b"ns1.example.\nns2.example.\n"
            else:
                stdout = f'"{VALIDATION}"\n'.encode("ascii")
            return subprocess.CompletedProcess(normalized, 0, stdout, b"")

        ticks = iter([0.0, 0.0, 0.1])
        result = worker.wait_for_authoritative_txt(
            validation=VALIDATION,
            run_fn=fake_run,
            sleep_fn=lambda _: None,
            monotonic_fn=lambda: next(ticks, 0.1),
        )
        self.assertEqual(result["stable_rounds"], 2)
        self.assertEqual(result["rounds"], 2)
        self.assertEqual(
            len([call for call in calls if "TXT" in call]),
            4,
        )


class CertificateTests(unittest.TestCase):
    def _create_campaign(self, root: Path) -> tuple[dict, Path]:
        campaign_root = private_dir(root / "campaigns")
        receipt = worker.generate_wa_ir_key_and_csr(
            campaign_root=campaign_root,
            campaign_id=CAMPAIGN_ID,
            operation_id=OPERATION_ID,
            release_sha=RELEASE_SHA,
        )
        return receipt, campaign_root

    def _issue_test_certificate(
        self,
        root: Path,
        csr_path: Path,
    ) -> tuple[Path, Path, Path]:
        cert_root = private_dir(root / "certs")
        ca_key = cert_root / "ca.key"
        ca_cert = cert_root / "ca.crt"
        leaf = cert_root / "leaf.pem"
        chain = cert_root / "chain.pem"
        fullchain = cert_root / "fullchain.pem"
        run_checked(
            [
                worker.DEFAULT_OPENSSL,
                "req",
                "-x509",
                "-newkey",
                "rsa:2048",
                "-sha256",
                "-nodes",
                "-days",
                "30",
                "-subj",
                "/CN=WA-IR Test Root CA",
                "-addext",
                "basicConstraints=critical,CA:TRUE,pathlen:0",
                "-addext",
                "keyUsage=critical,keyCertSign,cRLSign",
                "-keyout",
                ca_key,
                "-out",
                ca_cert,
            ]
        )
        run_checked(
            [
                worker.DEFAULT_OPENSSL,
                "x509",
                "-req",
                "-in",
                csr_path,
                "-CA",
                ca_cert,
                "-CAkey",
                ca_key,
                "-CAcreateserial",
                "-days",
                "30",
                "-sha256",
                "-copy_extensions",
                "copyall",
                "-out",
                leaf,
            ]
        )
        chain.write_bytes(ca_cert.read_bytes())
        fullchain.write_bytes(leaf.read_bytes() + ca_cert.read_bytes())
        for path in (ca_key, ca_cert, leaf, chain, fullchain):
            path.chmod(0o600)
        return fullchain, chain, ca_cert

    def test_wa_key_never_leaves_owner_only_operation_and_csr_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            receipt, campaign_root = self._create_campaign(root)
            key_path = Path(receipt["private_key_path"])
            csr_path = Path(receipt["csr_path"])
            self.assertEqual(stat.S_IMODE(key_path.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(csr_path.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(key_path.parent.stat().st_mode), 0o700)
            self.assertFalse(receipt["private_key_exported"])
            self.assertNotIn("PRIVATE KEY", json.dumps(receipt))
            verified = worker.verify_csr(csr_path)
            self.assertEqual(verified["exact_sans"], {"dns": ["coin.gold-trade.ir"], "ip": []})
            second = worker.generate_wa_ir_key_and_csr(
                campaign_root=campaign_root,
                campaign_id=CAMPAIGN_ID,
                operation_id=OPERATION_ID,
                release_sha=RELEASE_SHA,
            )
            self.assertEqual(second["status"], "verified_existing")
            self.assertEqual(second["public_key_spki_sha256"], receipt["public_key_spki_sha256"])

    def test_partial_csr_generation_recovers_without_rotating_private_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            receipt, campaign_root = self._create_campaign(root)
            key_path = Path(receipt["private_key_path"])
            csr_path = Path(receipt["csr_path"])
            receipt_path = key_path.parent / "csr-receipt.json"
            key_sha256 = sha256_secure_file(key_path)[0]
            csr_path.unlink()
            receipt_path.unlink()
            residue = key_path.parent / f".csr-{'0' * 32}.tmp"
            residue.write_text("partial", encoding="ascii")
            residue.chmod(0o600)

            recovered = worker.generate_wa_ir_key_and_csr(
                campaign_root=campaign_root,
                campaign_id=CAMPAIGN_ID,
                operation_id=OPERATION_ID,
                release_sha=RELEASE_SHA,
            )

            self.assertEqual(recovered["status"], "recovered_partial_generation")
            self.assertEqual(sha256_secure_file(key_path)[0], key_sha256)
            self.assertTrue(csr_path.is_file())
            self.assertFalse(residue.exists())
            self.assertEqual(
                worker.verify_csr(csr_path)["exact_sans"],
                {"dns": [worker.PRODUCTION_HOSTNAME], "ip": []},
            )

    def test_certificate_is_verified_installed_transactionally_and_candidate_is_loopback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            csr_receipt, campaign_root = self._create_campaign(root)
            fullchain, chain, ca_bundle = self._issue_test_certificate(
                root,
                Path(csr_receipt["csr_path"]),
            )
            verification = worker.verify_certificate_material(
                private_key_path=Path(csr_receipt["private_key_path"]),
                csr_path=Path(csr_receipt["csr_path"]),
                fullchain_path=fullchain,
                chain_path=chain,
                ca_bundle=ca_bundle,
            )
            self.assertTrue(verification["key_cert_match"])
            self.assertTrue(verification["chain_verified"])

            installed = worker.install_wa_ir_certificate_generation(
                campaign_root=campaign_root,
                campaign_id=CAMPAIGN_ID,
                operation_id=OPERATION_ID,
                release_sha=RELEASE_SHA,
                transported_fullchain_path=fullchain,
                transported_chain_path=chain,
                ca_bundle=ca_bundle,
            )
            generation = Path(installed["generation_path"])
            self.assertEqual(installed["status"], "installed_and_verified")
            self.assertTrue((generation / "leaf.pem").is_file())
            self.assertEqual(stat.S_IMODE(generation.stat().st_mode), 0o700)
            self.assertFalse(any(generation.parent.glob("initializing-*")))
            for item in installed["files"].values():
                self.assertEqual(item["mode"], "0600")
                self.assertEqual(item["nlink"], 1)

            config = worker.render_loopback_candidate_nginx(
                generation_root=generation,
                candidate_port=19443,
                shadow_upstream_port=19313,
            )
            self.assertIn("listen 127.0.0.1:19443 ssl;", config)
            self.assertIn("proxy_pass http://127.0.0.1:19313;", config)
            self.assertIn("location = /health/live", config)
            self.assertNotIn("listen 443", config)
            self.assertNotIn("sites-enabled", config)
            self.assertNotIn("proxy_pass http://95.38.164.29", config)

            second = worker.install_wa_ir_certificate_generation(
                campaign_root=campaign_root,
                campaign_id=CAMPAIGN_ID,
                operation_id=OPERATION_ID,
                release_sha=RELEASE_SHA,
                transported_fullchain_path=fullchain,
                transported_chain_path=chain,
                ca_bundle=ca_bundle,
            )
            self.assertEqual(second["status"], "verified_existing")

    def test_certificate_from_different_key_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first, _ = self._create_campaign(root / "first")
            second, _ = self._create_campaign(root / "second")
            fullchain, chain, ca_bundle = self._issue_test_certificate(
                root,
                Path(second["csr_path"]),
            )
            with self.assertRaisesRegex(worker.WebAppIrTlsError, "does not match transported CSR"):
                worker.verify_certificate_material(
                    private_key_path=Path(first["private_key_path"]),
                    csr_path=Path(first["csr_path"]),
                    fullchain_path=fullchain,
                    chain_path=chain,
                    ca_bundle=ca_bundle,
                )

    def test_generation_receipt_crash_is_recovered_from_fresh_material(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            csr_receipt, campaign_root = self._create_campaign(root)
            fullchain, chain, ca_bundle = self._issue_test_certificate(
                root,
                Path(csr_receipt["csr_path"]),
            )
            installed = worker.install_wa_ir_certificate_generation(
                campaign_root=campaign_root,
                campaign_id=CAMPAIGN_ID,
                operation_id=OPERATION_ID,
                release_sha=RELEASE_SHA,
                transported_fullchain_path=fullchain,
                transported_chain_path=chain,
                ca_bundle=ca_bundle,
            )
            generation = Path(installed["generation_path"])
            receipt_path = generation / "installation-receipt.json"
            receipt_path.unlink()

            recovered = worker.install_wa_ir_certificate_generation(
                campaign_root=campaign_root,
                campaign_id=CAMPAIGN_ID,
                operation_id=OPERATION_ID,
                release_sha=RELEASE_SHA,
                transported_fullchain_path=fullchain,
                transported_chain_path=chain,
                ca_bundle=ca_bundle,
            )

            self.assertEqual(recovered["status"], "recovered_generation_receipt")
            self.assertTrue(receipt_path.is_file())
            self.assertTrue(recovered["receipt_recovered"])
            self.assertFalse(any(generation.parent.glob("initializing-*")))

    def test_initializing_generation_residue_is_reconciled_before_install(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            csr_receipt, campaign_root = self._create_campaign(root)
            fullchain, chain, ca_bundle = self._issue_test_certificate(
                root,
                Path(csr_receipt["csr_path"]),
            )
            verification = worker.verify_certificate_material(
                private_key_path=Path(csr_receipt["private_key_path"]),
                csr_path=Path(csr_receipt["csr_path"]),
                fullchain_path=fullchain,
                chain_path=chain,
                ca_bundle=ca_bundle,
            )
            generation_id = (
                f"{OPERATION_ID}-{verification['leaf_cert_sha256'][:16]}"
            )
            generations = private_dir(
                campaign_root
                / CAMPAIGN_ID
                / "public-tls"
                / "generations"
            )
            initializing = private_dir(
                generations / f"initializing-{generation_id}"
            )
            residue = initializing / "private-key.pem"
            residue.write_text("interrupted-copy", encoding="ascii")
            residue.chmod(0o600)

            installed = worker.install_wa_ir_certificate_generation(
                campaign_root=campaign_root,
                campaign_id=CAMPAIGN_ID,
                operation_id=OPERATION_ID,
                release_sha=RELEASE_SHA,
                transported_fullchain_path=fullchain,
                transported_chain_path=chain,
                ca_bundle=ca_bundle,
            )

            self.assertEqual(installed["status"], "installed_and_verified")
            self.assertFalse(initializing.exists())
            self.assertTrue(Path(installed["generation_path"]).is_dir())

    def test_complete_certbot_outputs_recover_missing_issuance_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            csr_receipt, _ = self._create_campaign(root)
            source_fullchain, source_chain, ca_bundle = self._issue_test_certificate(
                root,
                Path(csr_receipt["csr_path"]),
            )
            output_dir = private_dir(root / "issuance")
            state_dir = private_dir(output_dir / "dns01-state")
            for name in ("certbot-config", "certbot-work", "certbot-logs"):
                private_dir(output_dir / name)
            cert_path = output_dir / "leaf.pem"
            chain_path = output_dir / "chain.pem"
            fullchain_path = output_dir / "fullchain.pem"
            cert_path.write_bytes(
                worker._pem_certificates(source_fullchain.read_bytes())[0]
            )
            chain_path.write_bytes(source_chain.read_bytes())
            fullchain_path.write_bytes(source_fullchain.read_bytes())
            for path in (cert_path, chain_path, fullchain_path):
                path.chmod(0o600)
            journal = output_dir / "dns01-journal.jsonl"
            identity = {
                "campaign_id": CAMPAIGN_ID,
                "operation_id": OPERATION_ID,
                "release_sha": RELEASE_SHA,
                "record_id": "txt-recovered",
                "validation_sha256": "1" * 64,
            }
            for event in (
                "webapp_ir.tls.dns01.create.readback",
                "webapp_ir.tls.dns01.propagated",
                "webapp_ir.tls.dns01.delete.readback",
            ):
                worker._append_event(journal, {"event": event, **identity})
            token_file = root / "arvan-token"
            token_file.write_text("secret-token\n", encoding="ascii")
            token_file.chmod(0o600)
            api = FakeArvan()
            certbot_called = False

            def no_certbot(argv, **kwargs):
                nonlocal certbot_called
                normalized = [os.fspath(item) for item in argv]
                if normalized[0] == str(worker.DEFAULT_CERTBOT):
                    certbot_called = True
                    raise AssertionError("Certbot must not rerun for complete outputs")
                return subprocess.run(normalized, **kwargs)

            recovered = worker.issue_certificate_from_csr(
                campaign_id=CAMPAIGN_ID,
                operation_id=OPERATION_ID,
                release_sha=RELEASE_SHA,
                csr_path=Path(csr_receipt["csr_path"]),
                output_dir=output_dir,
                email="ops@example.com",
                token_file=token_file,
                script_path=Path(worker.__file__).resolve(),
                ca_bundle=ca_bundle,
                request_fn=api,
                run_fn=no_certbot,
            )

            self.assertEqual(recovered["status"], "recovered_complete_issuance")
            self.assertTrue(recovered["receipt_recovered"])
            self.assertFalse(certbot_called)
            self.assertTrue((output_dir / "issuance-receipt.json").is_file())
            self.assertFalse((output_dir / "issuance-state.json").exists())
            self.assertEqual(
                [path for path in state_dir.iterdir() if path.name != "dns01-provider.lock"],
                [],
            )

    def test_partial_certbot_outputs_are_exactly_cleaned_before_retry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            csr_receipt, _ = self._create_campaign(root)
            source_fullchain, source_chain, ca_bundle = self._issue_test_certificate(
                root,
                Path(csr_receipt["csr_path"]),
            )
            source_leaf = worker._pem_certificates(
                source_fullchain.read_bytes()
            )[0]
            output_dir = private_dir(root / "issuance")
            cert_path = output_dir / "leaf.pem"
            cert_path.write_text("partial-certbot-output", encoding="ascii")
            cert_path.chmod(0o600)
            journal = output_dir / "dns01-journal.jsonl"
            identity = {
                "campaign_id": CAMPAIGN_ID,
                "operation_id": OPERATION_ID,
                "release_sha": RELEASE_SHA,
                "record_id": "txt-retry",
                "validation_sha256": "1" * 64,
            }
            for event in (
                "webapp_ir.tls.dns01.create.readback",
                "webapp_ir.tls.dns01.propagated",
                "webapp_ir.tls.dns01.delete.readback",
            ):
                worker._append_event(journal, {"event": event, **identity})
            token_file = root / "arvan-token"
            token_file.write_text("secret-token\n", encoding="ascii")
            token_file.chmod(0o600)
            certbot_calls = 0

            def certbot_once(argv, **kwargs):
                nonlocal certbot_calls
                normalized = [os.fspath(item) for item in argv]
                if normalized[0] != str(worker.DEFAULT_CERTBOT):
                    return subprocess.run(normalized, **kwargs)
                certbot_calls += 1
                outputs = {
                    "--cert-path": source_leaf,
                    "--chain-path": source_chain.read_bytes(),
                    "--fullchain-path": source_fullchain.read_bytes(),
                }
                for option, payload in outputs.items():
                    path = Path(normalized[normalized.index(option) + 1])
                    path.write_bytes(payload)
                    path.chmod(0o600)
                return subprocess.CompletedProcess(
                    normalized,
                    0,
                    b"issued",
                    b"",
                )

            issued = worker.issue_certificate_from_csr(
                campaign_id=CAMPAIGN_ID,
                operation_id=OPERATION_ID,
                release_sha=RELEASE_SHA,
                csr_path=Path(csr_receipt["csr_path"]),
                output_dir=output_dir,
                email="ops@example.com",
                token_file=token_file,
                script_path=Path(worker.__file__).resolve(),
                ca_bundle=ca_bundle,
                request_fn=FakeArvan(),
                run_fn=certbot_once,
            )

            self.assertEqual(issued["status"], "issued_and_dns_cleaned")
            self.assertEqual(certbot_calls, 1)
            self.assertEqual(cert_path.read_bytes(), source_leaf)
            self.assertFalse((output_dir / "issuance-state.json").exists())

    def test_real_loopback_nginx_candidate_serves_verified_peer_and_cleans_listener(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            csr_receipt, campaign_root = self._create_campaign(root)
            fullchain, chain, ca_bundle = self._issue_test_certificate(
                root,
                Path(csr_receipt["csr_path"]),
            )
            installed = worker.install_wa_ir_certificate_generation(
                campaign_root=campaign_root,
                campaign_id=CAMPAIGN_ID,
                operation_id=OPERATION_ID,
                release_sha=RELEASE_SHA,
                transported_fullchain_path=fullchain,
                transported_chain_path=chain,
                ca_bundle=ca_bundle,
            )
            generation = Path(installed["generation_path"])
            installation_receipt = generation / "installation-receipt.json"
            baseline_before = generation / "nginx-baseline-before.json"
            baseline_after = generation / "nginx-baseline-after.json"

            def baseline_run(argv, **kwargs):
                normalized = [os.fspath(item) for item in argv]
                if normalized == [str(worker.DEFAULT_NGINX), "-T"]:
                    return subprocess.CompletedProcess(
                        normalized,
                        0,
                        b"stable-active-nginx-config\n",
                        b"syntax is ok\n",
                    )
                return subprocess.run(normalized, **kwargs)

            worker.capture_active_nginx_baseline(
                campaign_id=CAMPAIGN_ID,
                operation_id=OPERATION_ID,
                release_sha=RELEASE_SHA,
                output_path=baseline_before,
                run_fn=baseline_run,
            )
            upstream_port = unused_port()
            candidate_port = unused_port()
            while candidate_port == upstream_port:
                candidate_port = unused_port()
            server = ThreadingHTTPServer(("127.0.0.1", upstream_port), LiveHandler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                staged = worker.stage_loopback_candidate_nginx(
                    installation_receipt_path=installation_receipt,
                    candidate_port=candidate_port,
                    shadow_upstream_port=upstream_port,
                    nginx_baseline_before_path=baseline_before,
                    nginx_baseline_after_path=baseline_after,
                    run_fn=baseline_run,
                )
                probe_path = generation / "candidate-probe-receipt.json"
                probed = worker.probe_loopback_candidate_nginx(
                    candidate_receipt_path=generation / "candidate-nginx-receipt.json",
                    installation_receipt_path=installation_receipt,
                    output_path=probe_path,
                    ca_bundle=ca_bundle,
                )
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)
            self.assertEqual(staged["status"], "staged_and_syntax_verified")
            self.assertEqual(probed["readiness_http_status"], 200)
            self.assertTrue(probed["listener_absent_after_twice"])
            self.assertFalse(worker._listener_accepting(candidate_port))
            self.assertEqual(
                probed["peer_leaf_cert_sha256"],
                installed["leaf_cert_sha256"],
            )


class CandidateCrashRecoveryTests(unittest.TestCase):
    def test_stage_recovers_config_written_before_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            generation = private_dir(Path(tmp) / "generation")
            installation = {
                "schema": f"{worker.SCHEMA_PREFIX}.installation-receipt.v1",
                "campaign_id": CAMPAIGN_ID,
                "operation_id": OPERATION_ID,
                "release_sha": RELEASE_SHA,
                "generation_id": "generation-1",
                "generation_path": str(generation),
            }
            installation_path = write_private_json(
                generation / "installation-receipt.json",
                installation,
            )
            identity = {
                "schema": f"{worker.SCHEMA_PREFIX}.nginx-baseline.v1",
                "campaign_id": CAMPAIGN_ID,
                "operation_id": OPERATION_ID,
                "release_sha": RELEASE_SHA,
                "active_nginx_generation_sha256": "7" * 64,
            }
            before = write_private_json(generation / "before.json", identity)
            after = write_private_json(generation / "after.json", identity)
            config_path = generation / "candidate-nginx.conf"
            config_path.write_text(
                worker.render_loopback_candidate_nginx(
                    generation_root=generation,
                    candidate_port=19443,
                    shadow_upstream_port=19313,
                ),
                encoding="ascii",
            )
            config_path.chmod(0o600)

            def syntax_ok(argv, **kwargs):
                del kwargs
                normalized = [os.fspath(item) for item in argv]
                return subprocess.CompletedProcess(normalized, 0, b"", b"syntax is ok")

            staged = worker.stage_loopback_candidate_nginx(
                installation_receipt_path=installation_path,
                candidate_port=19443,
                shadow_upstream_port=19313,
                nginx_baseline_before_path=before,
                nginx_baseline_after_path=after,
                run_fn=syntax_ok,
            )

            self.assertEqual(staged["status"], "staged_and_syntax_verified")
            self.assertTrue((generation / "candidate-nginx-receipt.json").is_file())
            self.assertIn(
                "master_process off;",
                config_path.read_text(encoding="ascii"),
            )

    def test_stale_candidate_process_group_is_reconciled_from_root_only_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            generation = private_dir(Path(tmp) / "generation")
            command = ["/usr/bin/sleep", "30"]
            process = subprocess.Popen(command, start_new_session=True)
            try:
                identity = None
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    identity = worker._read_proc_identity(process.pid)
                    if identity is not None and identity[2] == command:
                        break
                    time.sleep(0.01)
                self.assertIsNotNone(identity)
                assert identity is not None
                candidate = {
                    "campaign_id": CAMPAIGN_ID,
                    "operation_id": OPERATION_ID,
                    "release_sha": RELEASE_SHA,
                    "generation_id": "generation-1",
                }
                state = {
                    "schema": f"{worker.SCHEMA_PREFIX}.candidate-process-state.v1",
                    "phase": "running",
                    **candidate,
                    "command": command,
                    "command_sha256": worker._sha256_json(command),
                    "executable_identity": worker._candidate_executable_identity(
                        command
                    ),
                    "config_sha256": "8" * 64,
                    "candidate_port": 19443,
                    "created_at": worker._now_text(),
                    "pid": process.pid,
                    "pgid": process.pid,
                    "proc_start_ticks": identity[0],
                    "started_at": worker._now_text(),
                }
                write_private_json(
                    generation / "candidate-process-state.json",
                    state,
                )
                with mock.patch.object(worker, "_wait_for_listener"):
                    worker._reconcile_candidate_process(
                        generation_root=generation,
                        candidate=candidate,
                        command=command,
                        config_sha256="8" * 64,
                        port=19443,
                        sleep_fn=lambda _: time.sleep(0.01),
                    )
                process.wait(timeout=5)
                self.assertIn(process.returncode, (-15, -9))
                self.assertFalse(
                    (generation / "candidate-process-state.json").exists()
                )
            finally:
                if process.poll() is None:
                    os.killpg(process.pid, 9)
                    process.wait(timeout=5)


class ActivationDocumentTests(unittest.TestCase):
    def _documents(self, root: Path) -> dict[str, Path]:
        private_dir(root)
        identity = {
            "campaign_id": CAMPAIGN_ID,
            "operation_id": OPERATION_ID,
            "release_sha": RELEASE_SHA,
        }
        current = worker._normalize_a_record(a_record())
        current_hash = worker._sha256_json(current)
        desired = worker.desired_wa_ir_a_rrset(current)
        generation = private_dir(root / "generation")
        files = {}
        filenames = {
            "private_key": "private-key.pem",
            "csr": "request.csr",
            "leaf": "leaf.pem",
            "chain": "chain.pem",
            "fullchain": "fullchain.pem",
        }
        for name, filename in filenames.items():
            path = generation / filename
            path.write_text(name, encoding="ascii")
            path.chmod(0o600)
            files[name] = worker._file_attestation(path, label=name)
        ca_attestation = {
            "path": "/etc/ssl/certs/ca-certificates.crt",
            "sha256": "f" * 64,
            "bytes": 1,
            "uid": 0,
            "gid": 0,
            "mode": "0644",
            "nlink": 1,
        }
        verification = {
            "key_csr_match": True,
            "key_cert_match": True,
            "csr_cert_match": True,
            "exact_sans": {"dns": [worker.PRODUCTION_HOSTNAME], "ip": []},
            "required_eku": ["serverAuth"],
            "eku_server_auth": True,
            "chain_verified": True,
            "hostname_verified": True,
            "validity_verified": True,
            "not_before": "2026-07-27T00:00:00+00:00",
            "not_after": "2026-10-25T00:00:00+00:00",
            "csr_sha256": "1" * 64,
            "leaf_cert_sha256": "2" * 64,
            "fullchain_sha256": "3" * 64,
            "chain_sha256": "a" * 64,
            "public_key_spki_sha256": "4" * 64,
            "ca_bundle": ca_attestation,
        }
        installation = {
            **identity,
            "schema": f"{worker.SCHEMA_PREFIX}.installation-receipt.v1",
            "role": "webapp_ir",
            "expected_host": worker.WA_IR_PUBLIC_IP,
            "production_hostname": worker.PRODUCTION_HOSTNAME,
            "generation_id": "generation-1",
            "generation_path": str(generation),
            **verification,
            "files": files,
        }
        issuance = {
            **identity,
            "schema": f"{worker.SCHEMA_PREFIX}.issuance-receipt.v1",
            "csr_sha256": "1" * 64,
            "leaf_cert_sha256": "2" * 64,
            "chain_sha256": "a" * 64,
            "fullchain_sha256": "3" * 64,
            "public_key_spki_sha256": "4" * 64,
            "ca_bundle": ca_attestation,
            "dns01_journal_sha256": "5" * 64,
            "dns01_event_hashes": ["6" * 64],
            "dns01_record_receipts": [{"record_id": "txt-1"}],
            "before_a_rrset_sha256": current_hash,
            "after_a_rrset_sha256": current_hash,
        }
        dns = {
            **identity,
            "schema": f"{worker.SCHEMA_PREFIX}.dns-baseline.v1",
            "expected_pre_activation_dns_a_rrset": current,
            "expected_pre_activation_dns_a_rrset_sha256": current_hash,
            "desired_dns_a_rrset": desired,
            "desired_dns_a_rrset_sha256": worker._sha256_json(desired),
            "rollback_dns_a_rrset": current,
            "rollback_dns_a_rrset_sha256": current_hash,
        }
        nginx = {
            **identity,
            "schema": f"{worker.SCHEMA_PREFIX}.nginx-baseline.v1",
            "active_nginx_generation_sha256": "7" * 64,
        }
        candidate_config = generation / "candidate-nginx.conf"
        candidate_config.write_text("candidate config", encoding="ascii")
        candidate_config.chmod(0o600)
        candidate_config_sha256 = sha256_secure_file(candidate_config)[0]
        candidate = {
            **identity,
            "schema": f"{worker.SCHEMA_PREFIX}.candidate-nginx-receipt.v1",
            "role": "webapp_ir",
            "generation_id": "generation-1",
            "candidate_nginx_generation_path": str(candidate_config),
            "candidate_nginx_generation_sha256": candidate_config_sha256,
            "candidate_listener": "127.0.0.1:19443",
            "shadow_upstream": "127.0.0.1:19313",
            "readiness_url": "https://coin.gold-trade.ir:19443/health/live",
            "readiness_path": "/health/live",
            "nginx_t_argv_sha256": "9" * 64,
            "nginx_t_exit": 0,
            "nginx_t_stdout": {"sha256": "a" * 64, "bytes": 0},
            "nginx_t_stderr": {"sha256": "b" * 64, "bytes": 0},
            "active_nginx_before_sha256": "7" * 64,
            "active_nginx_after_sha256": "7" * 64,
        }
        probe = {
            **identity,
            "schema": f"{worker.SCHEMA_PREFIX}.candidate-probe-receipt.v1",
            "generation_id": "generation-1",
            "candidate_listener": "127.0.0.1:19443",
            "shadow_upstream": "127.0.0.1:19313",
            "listener_absent_before": True,
            "listener_bound_during_probe": True,
            "listener_absent_after_twice": True,
            "shadow_upstream_loopback": True,
            "peer_hostname_verified": True,
            "peer_chain_verified": True,
            "readiness_http_status": 200,
            "readiness_body_sha256": "c" * 64,
            "curl_argv_sha256": "d" * 64,
            "peer_leaf_cert_sha256": "2" * 64,
            "peer_public_key_spki_sha256": "4" * 64,
            "ca_bundle": ca_attestation,
        }
        runtime = {
            **identity,
            "physical_site": "webapp_ir",
            "background_jobs_enabled": False,
            "effects_started": False,
            "shadow_upstream_loopback": True,
        }
        payloads = {
            "installation": installation,
            "issuance": issuance,
            "dns": dns,
            "nginx_before": nginx,
            "nginx_after": nginx,
            "candidate": candidate,
            "probe": probe,
            "runtime": runtime,
        }
        paths = {
            name: write_private_json(root / f"{name}.json", payload)
            for name, payload in payloads.items()
        }
        legacy_release_sha = "d" * 40
        artifacts = {
            name: chr(97 + index % 6) * 64
            for index, name in enumerate(worker.CUTOVER_ARTIFACT_FIELDS)
            if name
            not in {"app_image_id", "postgres_image_id", "postgres_image_ref"}
        }
        artifacts.update(
            {
                "app_image_id": "sha256:" + "1" * 64,
                "postgres_image_id": "sha256:" + "2" * 64,
                "postgres_image_ref": (
                    f"trading_bot_postgres_boottime:15-{RELEASE_SHA}"
                ),
            }
        )
        cutover = {
            "schema": worker.CUTOVER_MANIFEST_SCHEMA,
            "campaign_id": CAMPAIGN_ID,
            "operation_id": OPERATION_ID,
            "created_at": "2026-07-27T00:00:00+00:00",
            "release_sha": RELEASE_SHA,
            "release_tree_sha": "b" * 40,
            "legacy_release_sha": legacy_release_sha,
            "topology": json.loads(
                json.dumps(worker.EXPECTED_CUTOVER_TOPOLOGY)
            ),
            "deployment": {
                "production_hostname": worker.PRODUCTION_HOSTNAME,
                "legacy_compose_project": "trading_bot",
                "shadow_compose_project": (
                    f"tb_prod_{CAMPAIGN_ID.replace('-', '')[:16]}"
                ),
                "shadow_root": (
                    f"/srv/trading-bot-production-shadow/{CAMPAIGN_ID}"
                ),
                "controller_journal_path": (
                    "/root/secure-envs/trading-bot/production-cutover/"
                    f"{CAMPAIGN_ID}/journal.json"
                ),
                "controller_evidence_root": (
                    "/root/secure-envs/trading-bot/production-cutover/"
                    f"{CAMPAIGN_ID}/evidence"
                ),
            },
            "artifacts": artifacts,
            "policy": {
                field: True for field in worker.CUTOVER_POLICY_FIELDS
            },
        }
        paths["cutover"] = write_private_json(
            root / "cutover-manifest.json",
            cutover,
        )
        cutover_sha256 = sha256_secure_file(paths["cutover"])[0]
        precondition = {
            "schema": worker.ACTIVATION_PRECONDITION_SCHEMA,
            "status": "verified",
            "phase": worker.ACTIVATION_PRECONDITION_PHASE,
            "operation": worker.ACTIVATION_PRECONDITION_OPERATION,
            "campaign_id": CAMPAIGN_ID,
            "operation_id": OPERATION_ID,
            "release_sha": RELEASE_SHA,
            "legacy_release_sha": legacy_release_sha,
            "manifest_sha256": cutover_sha256,
            "plan_sha256": "1" * 64,
            "approval_sha256": artifacts["cutover_approval_sha256"],
            "phase_evidence_schema_sha256": artifacts[
                "phase_evidence_schema_sha256"
            ],
            "manifest_artifact_bindings_sha256": worker._sha256_json(
                artifacts
            ),
            "prior_phase_evidence_closure_sha256": "2" * 64,
            "phase_input_closure_sha256": "3" * 64,
            "prior_phase_count": 20,
            "evidence_sha256": "4" * 64,
            "verified_roles": worker.EXPECTED_ACTIVATION_ROLES,
            "verified_claim_count": 1,
            "captured_at": "2026-07-27T00:00:01+00:00",
            "verified_at": "2026-07-27T00:00:02+00:00",
            "production_contacted": False,
        }
        paths["precondition"] = write_private_json(
            root / "activation-precondition.json",
            precondition,
        )
        paths["verification"] = verification
        return paths

    def test_final_documents_bind_dns_nginx_runtime_and_tls_without_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = private_dir(Path(tmp) / "documents")
            documents = self._documents(root)
            manifest_path = root / "activation-manifest.json"
            evidence_path = root / "activation-evidence.json"
            with mock.patch.object(
                worker,
                "verify_certificate_material",
                return_value=documents["verification"],
            ):
                manifest, evidence = worker.build_activation_documents(
                    installation_receipt_path=documents["installation"],
                    issuance_receipt_path=documents["issuance"],
                    dns_baseline_path=documents["dns"],
                    nginx_baseline_before_path=documents["nginx_before"],
                    nginx_baseline_after_path=documents["nginx_after"],
                    candidate_receipt_path=documents["candidate"],
                    probe_receipt_path=documents["probe"],
                    runtime_safety_attestation_path=documents["runtime"],
                    cutover_manifest_path=documents["cutover"],
                    activation_precondition_evidence_path=documents[
                        "precondition"
                    ],
                    runtime_source_binding=runtime_source_binding(),
                    manifest_output_path=manifest_path,
                    evidence_output_path=evidence_path,
                )
            self.assertFalse(manifest["production_a_record_mutated"])
            self.assertFalse(manifest["active_nginx_mutated"])
            self.assertEqual(
                manifest["desired_dns_a_rrset"]["value"][0]["ip"],
                worker.WA_IR_PUBLIC_IP,
            )
            self.assertTrue(evidence["production_a_route_unchanged"])
            self.assertTrue(evidence["active_nginx_unchanged"])
            self.assertEqual(
                evidence["manifest_sha256"],
                sha256_secure_file(manifest_path)[0],
            )
            self.assertNotIn("PRIVATE KEY", manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(stat.S_IMODE(manifest_path.stat().st_mode), 0o600)

    def test_finalization_rejects_enabled_jobs_or_effects(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = private_dir(Path(tmp) / "documents")
            documents = self._documents(root)
            runtime = json.loads(documents["runtime"].read_text(encoding="utf-8"))
            runtime["background_jobs_enabled"] = True
            documents["runtime"].write_text(json.dumps(runtime), encoding="utf-8")
            documents["runtime"].chmod(0o600)
            with mock.patch.object(
                worker,
                "verify_certificate_material",
                return_value=documents["verification"],
            ), self.assertRaisesRegex(worker.WebAppIrTlsError, "inert WA shadow"):
                worker.build_activation_documents(
                    installation_receipt_path=documents["installation"],
                    issuance_receipt_path=documents["issuance"],
                    dns_baseline_path=documents["dns"],
                    nginx_baseline_before_path=documents["nginx_before"],
                    nginx_baseline_after_path=documents["nginx_after"],
                    candidate_receipt_path=documents["candidate"],
                    probe_receipt_path=documents["probe"],
                    runtime_safety_attestation_path=documents["runtime"],
                    cutover_manifest_path=documents["cutover"],
                    activation_precondition_evidence_path=documents[
                        "precondition"
                    ],
                    runtime_source_binding=runtime_source_binding(),
                    manifest_output_path=root / "manifest.json",
                    evidence_output_path=root / "evidence.json",
                )

    def test_finalization_rejects_precondition_not_bound_to_manifest_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = private_dir(Path(tmp) / "documents")
            documents = self._documents(root)
            precondition = json.loads(
                documents["precondition"].read_text(encoding="utf-8")
            )
            precondition["manifest_sha256"] = "9" * 64
            write_private_json(documents["precondition"], precondition)
            with mock.patch.object(
                worker,
                "verify_certificate_material",
                return_value=documents["verification"],
            ), self.assertRaisesRegex(
                worker.WebAppIrTlsError,
                "precondition evidence binding mismatch",
            ):
                worker.build_activation_documents(
                    installation_receipt_path=documents["installation"],
                    issuance_receipt_path=documents["issuance"],
                    dns_baseline_path=documents["dns"],
                    nginx_baseline_before_path=documents["nginx_before"],
                    nginx_baseline_after_path=documents["nginx_after"],
                    candidate_receipt_path=documents["candidate"],
                    probe_receipt_path=documents["probe"],
                    runtime_safety_attestation_path=documents["runtime"],
                    cutover_manifest_path=documents["cutover"],
                    activation_precondition_evidence_path=documents[
                        "precondition"
                    ],
                    runtime_source_binding=runtime_source_binding(),
                    manifest_output_path=root / "manifest.json",
                    evidence_output_path=root / "evidence.json",
                )

    def test_finalization_rehashes_installed_generation_and_rejects_tamper(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = private_dir(Path(tmp) / "documents")
            documents = self._documents(root)
            installation = json.loads(
                documents["installation"].read_text(encoding="utf-8")
            )
            leaf_path = Path(installation["files"]["leaf"]["path"])
            leaf_path.write_text("tampered", encoding="ascii")
            leaf_path.chmod(0o600)
            with mock.patch.object(
                worker,
                "verify_certificate_material",
                return_value=documents["verification"],
            ), self.assertRaisesRegex(
                worker.WebAppIrTlsError,
                "files changed after installation receipt",
            ):
                worker.build_activation_documents(
                    installation_receipt_path=documents["installation"],
                    issuance_receipt_path=documents["issuance"],
                    dns_baseline_path=documents["dns"],
                    nginx_baseline_before_path=documents["nginx_before"],
                    nginx_baseline_after_path=documents["nginx_after"],
                    candidate_receipt_path=documents["candidate"],
                    probe_receipt_path=documents["probe"],
                    runtime_safety_attestation_path=documents["runtime"],
                    cutover_manifest_path=documents["cutover"],
                    activation_precondition_evidence_path=documents[
                        "precondition"
                    ],
                    runtime_source_binding=runtime_source_binding(),
                    manifest_output_path=root / "manifest.json",
                    evidence_output_path=root / "evidence.json",
                )


class StaticSafetyTests(unittest.TestCase):
    def test_worker_exposes_no_a_record_write_or_active_nginx_mutation_primitive(self) -> None:
        source = Path(worker.__file__).read_text(encoding="utf-8")
        self.assertNotIn('"PUT"', source)
        self.assertNotIn("/etc/nginx/sites-enabled", source)
        self.assertNotIn("nginx -s reload", source)
        self.assertNotIn("systemctl restart nginx", source)
        self.assertNotIn("systemctl reload nginx", source)
        self.assertNotIn("ssh ", source)
        self.assertNotIn("scp ", source)
        self.assertNotIn("rsync ", source)

    def test_scope_and_ports_are_fail_closed(self) -> None:
        with self.assertRaisesRegex(worker.WebAppIrTlsError, "pinned"):
            worker.validate_production_scope(
                root_domain="example.com",
                hostname=worker.PRODUCTION_HOSTNAME,
            )
        with self.assertRaisesRegex(worker.WebAppIrTlsError, "between 1024"):
            worker.validate_tcp_port(443, label="candidate")
        with self.assertRaisesRegex(worker.WebAppIrTlsError, "differ"):
            worker.render_loopback_candidate_nginx(
                generation_root=Path("/tmp/generation"),
                candidate_port=19443,
                shadow_upstream_port=19443,
            )

    def test_operation_lock_rejects_overlap(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = private_dir(Path(tmp) / "lock")
            with worker._exclusive_operation_lock(root, name="operation.lock"):
                with self.assertRaisesRegex(worker.WebAppIrTlsError, "owns this operation lock"):
                    with worker._exclusive_operation_lock(root, name="operation.lock"):
                        pass

    def test_private_paths_executables_and_cli_ca_fail_closed_on_aliases(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = private_dir(Path(tmp) / "private")
            target = private_dir(root / "target")
            (root / "alias").symlink_to(target, target_is_directory=True)
            with self.assertRaisesRegex(
                worker.WebAppIrTlsError,
                "directory chain is unsafe",
            ):
                worker._assert_private_directory(root / "alias" / "child", create=True)

            executable_alias = root / "openssl"
            executable_alias.symlink_to(worker.DEFAULT_OPENSSL)
            with self.assertRaisesRegex(
                worker.WebAppIrTlsError,
                "trusted root-owned executable",
            ):
                worker.validate_executable(executable_alias, label="openssl alias")

            untrusted_ca = root / "ca.pem"
            untrusted_ca.write_text("not a CA", encoding="ascii")
            untrusted_ca.chmod(0o666)
            with self.assertRaisesRegex(
                worker.WebAppIrTlsError,
                "trusted root-owned regular file",
            ):
                worker.attest_trusted_ca_bundle(untrusted_ca)
            with self.assertRaisesRegex(
                worker.WebAppIrTlsError,
                "pinned to the system CA bundle",
            ):
                worker._require_production_ca_bundle(untrusted_ca)


if __name__ == "__main__":
    unittest.main()
