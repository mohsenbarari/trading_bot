from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.build_full_matrix_ingress_config import (
    FullMatrixIngressConfigError,
    SCHEMA as INGRESS_CONFIG_SCHEMA,
    build,
)
from scripts.full_matrix_live.origin_probe import OriginProbeError, probe as local_probe
from scripts.full_matrix_live.public_ingress_probe import (
    PublicIngressProbeError,
    _authorization,
    probe as public_probe,
    probe_safe_unavailable,
)


class FullMatrixIngressProbeTests(unittest.TestCase):
    def test_ingress_config_binds_only_credential_digest_not_plaintext(self):
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            credential = root / "basic-auth.txt"
            secret = "matrix_operator:QWERTYuiopASDFGHJKLzxcvbnm1234567890_-token\n"
            credential.write_text(secret, encoding="ascii")
            credential.chmod(0o600)
            config = build(release_sha="a" * 40, client_auth_file=credential)
            self.assertEqual(config["schema"], INGRESS_CONFIG_SCHEMA)
            self.assertEqual(config["expected_active_origin"], "webapp_fi")
            self.assertEqual(
                config["client_auth_sha256"],
                hashlib.sha256(secret.encode("ascii")).hexdigest(),
            )
            self.assertNotIn(secret.rstrip(), str(config))

    def test_ingress_config_rejects_non_owner_only_or_malformed_credential(self):
        with tempfile.TemporaryDirectory() as raw_root:
            credential = Path(raw_root) / "basic-auth.txt"
            credential.write_text("matrix_operator:not-valid\n", encoding="ascii")
            credential.chmod(0o600)
            with self.assertRaises(FullMatrixIngressConfigError):
                build(release_sha="a" * 40, client_auth_file=credential)
            credential.write_text(
                "matrix_operator:QWERTYuiopASDFGHJKLzxcvbnm1234567890_-token\n",
                encoding="ascii",
            )
            credential.chmod(0o644)
            with self.assertRaises(FullMatrixIngressConfigError):
                build(release_sha="a" * 40, client_auth_file=credential)

    def test_public_probe_uses_bound_credential_without_returning_it(self):
        with tempfile.TemporaryDirectory() as raw_root:
            credential = Path(raw_root) / "basic-auth.txt"
            raw = b"matrix_operator:QWERTYuiopASDFGHJKLzxcvbnm1234567890_-token\n"
            credential.write_bytes(raw)
            credential.chmod(0o600)
            authorization = _authorization(
                client_auth_file=credential,
                expected_sha256=hashlib.sha256(raw).hexdigest(),
            )
            self.assertTrue(authorization.startswith("Basic "))
            self.assertNotIn(raw.decode("ascii").rstrip(), authorization)
            with self.assertRaises(PublicIngressProbeError):
                _authorization(
                    client_auth_file=credential,
                    expected_sha256="0" * 64,
                )

    def test_local_probe_rejects_non_pinned_role_port_before_network_access(self):
        with self.assertRaises(OriginProbeError):
            local_probe(site="webapp_fi", release_sha="a" * 40, port=8213)

    def test_public_probe_requires_no_store_dynamic_and_nonstale_health_reads(self):
        health = {
            "origin_ready": True,
            "physical_site": "webapp_fi",
            "runtime_role": "active",
            "writer_epoch": 7,
            "release_sha": "a" * 40,
            "migration_revision": "d2e7f8a9b0c1",
            "database_ok": True,
            "redis_ok": True,
            "global_convergence_required": True,
            "reasons": [],
            "witness_lease_id": "lease-1",
        }
        responses = [
            (200, json.dumps(health).encode(), "no-store, max-age=0", None, "", ""),
            (200, json.dumps(health).encode(), "no-store, max-age=0", "0", "", ""),
            (
                200,
                b'{"bot_username":"matrixbot","frontend_url":"https://app.gold-trading.ir"}',
                "no-store, max-age=0",
                None,
                "https://app.gold-trading.ir",
                "",
            ),
            (401, b"", "", None, "", 'Basic realm="Trading Bot Full Matrix"'),
            (404, b"", "", None, "", ""),
        ]
        with patch(
            "scripts.full_matrix_live.public_ingress_probe._authorization",
            return_value="Basic redacted",
        ), patch(
            "scripts.full_matrix_live.public_ingress_probe._request_json",
            side_effect=responses,
        ):
            result = public_probe(
                release_sha="a" * 40,
                expected_active_origin="webapp_fi",
                client_auth_file=Path("/root/secure/basic-auth.txt"),
                client_auth_sha256="b" * 64,
            )
        self.assertTrue(result["dynamic_cache_no_store"])
        self.assertTrue(result["health_cache_not_stale"])
        self.assertEqual(result["repeated_health_status"], 200)
        self.assertTrue(result["canonical_frontend_url"])
        self.assertTrue(result["canonical_cors_origin"])
        self.assertTrue(result["basic_auth_enforced"])
        self.assertTrue(result["dev_login_denied"])

    def test_public_probe_can_verify_the_ir_writer_after_a_bound_transition(self):
        health = {
            "origin_ready": True,
            "physical_site": "webapp_ir",
            "runtime_role": "active",
            "writer_epoch": 8,
            "release_sha": "a" * 40,
            "migration_revision": "d2e7f8a9b0c1",
            "database_ok": True,
            "redis_ok": True,
            "global_convergence_required": True,
            "reasons": [],
            "witness_lease_id": "lease-2",
        }
        responses = [
            (200, json.dumps(health).encode(), "no-store", None, "", ""),
            (200, json.dumps(health).encode(), "no-store", "0", "", ""),
            (
                200,
                b'{"bot_username":"matrixbot","frontend_url":"https://app.gold-trading.ir"}',
                "no-store",
                None,
                "https://app.gold-trading.ir",
                "",
            ),
            (401, b"", "", None, "", 'Basic realm="Trading Bot Full Matrix"'),
            (404, b"", "", None, "", ""),
        ]
        with patch(
            "scripts.full_matrix_live.public_ingress_probe._authorization",
            return_value="Basic redacted",
        ), patch(
            "scripts.full_matrix_live.public_ingress_probe._request_json",
            side_effect=responses,
        ):
            result = public_probe(
                release_sha="a" * 40,
                expected_active_origin="webapp_ir",
                client_auth_file=Path("/root/secure/basic-auth.txt"),
                client_auth_sha256="b" * 64,
            )
        self.assertEqual(result["expected_active_origin"], "webapp_ir")
        self.assertEqual(result["writer_epoch"], 8)

    def test_safe_unavailable_requires_two_authenticated_uncached_failure_responses(self):
        unavailable = (503, b'{"detail":"unavailable"}', "no-store", None, "", "")
        with patch(
            "scripts.full_matrix_live.public_ingress_probe._authorization",
            return_value="Basic private",
        ), patch(
            "scripts.full_matrix_live.public_ingress_probe._request_json",
            side_effect=[unavailable, unavailable],
        ):
            result = probe_safe_unavailable(
                release_sha="a" * 40,
                client_auth_file=Path("/tmp/client-auth"),
                client_auth_sha256="b" * 64,
            )
        self.assertEqual(result["status"], "safe_unavailable")
        self.assertEqual(result["first_http_status"], 503)

    def test_safe_unavailable_rejects_a_successful_response(self):
        bad = (200, b"{}", "no-store", None, "", "")
        with patch(
            "scripts.full_matrix_live.public_ingress_probe._authorization",
            return_value="Basic private",
        ), patch(
            "scripts.full_matrix_live.public_ingress_probe._request_json",
            side_effect=[bad, bad],
        ), self.assertRaises(PublicIngressProbeError):
            probe_safe_unavailable(
                release_sha="a" * 40,
                client_auth_file=Path("/tmp/client-auth"),
                client_auth_sha256="b" * 64,
            )


if __name__ == "__main__":
    unittest.main()
