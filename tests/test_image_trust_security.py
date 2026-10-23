"""Hermetic regressions for the bounded CA gate and immutable image inputs."""

from __future__ import annotations

import contextlib
import io
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from scripts import verify_image_trust as gate


ROOT = Path(__file__).resolve().parents[1]


def certificate(name: str) -> dict:
    return {"subject": ((("organizationName", "Example"),), (("commonName", name),))}


class ImageTrustSecurityTests(unittest.TestCase):
    def test_blocked_ca_is_rejected_among_other_roots(self):
        for name in ("emSign ECC Root CA - G3", "emSign ECC Root CA-G3"):
            with self.subTest(name=name):
                ctx = Mock()
                ctx.get_ca_certs.return_value = [certificate("Example CA"), certificate(name)]
                with self.assertRaisesRegex(ValueError, "distrusted CA"):
                    gate.check_context(ctx, "fixture")

    def test_unrelated_emsign_roots_remain_usable(self):
        ctx = Mock()
        ctx.get_ca_certs.return_value = [certificate("emSign Root CA - G1")]
        self.assertEqual(gate.check_context(ctx, "fixture"), {
            "store": "fixture", "ca_count": 1, "blocked_ca_present": False,
        })

    def test_empty_store_cannot_pass(self):
        ctx = Mock()
        ctx.get_ca_certs.return_value = []
        with self.assertRaisesRegex(ValueError, "empty trust"):
            gate.check_context(ctx, "fixture")

    def test_required_separate_certifi_store_cannot_be_skipped(self):
        ctx = Mock()
        ctx.get_ca_certs.return_value = [certificate("Example CA")]
        with patch.object(gate.ssl, "create_default_context", return_value=ctx), patch.object(
            gate.importlib.util, "find_spec", return_value=None,
        ):
            self.assertEqual(len(gate.verify_trust()), 1)
            with self.assertRaisesRegex(ValueError, "required trust store"):
                gate.verify_trust(require_certifi=True)

    def test_certifi_context_is_checked_in_addition_to_system(self):
        ctx = Mock()
        ctx.get_ca_certs.return_value = [certificate("Example CA")]
        blocked = Mock()
        blocked.get_ca_certs.return_value = [certificate("emSign ECC Root CA - G3")]
        fake_certifi = Mock()
        fake_certifi.where.return_value = "/fixture/ca.pem"
        with patch.object(gate.ssl, "create_default_context", side_effect=[ctx, blocked]) as create, patch.object(
            gate.importlib.util, "find_spec", return_value=object(),
        ), patch.dict("sys.modules", {"certifi": fake_certifi}):
            with self.assertRaisesRegex(ValueError, "certifi: distrusted CA"):
                gate.verify_trust(require_certifi=True)
        self.assertEqual(create.call_args_list[1].kwargs, {"cafile": "/fixture/ca.pem"})

    def test_cli_failure_is_nonzero_without_paths_or_certificate_material(self):
        output = io.StringIO()
        with patch("sys.argv", ["verify_image_trust.py"]), patch.object(
            gate, "verify_trust", side_effect=OSError("/private/fixture.pem"),
        ), contextlib.redirect_stdout(output):
            self.assertEqual(gate.main(), 1)
        self.assertNotIn("/private", output.getvalue())
        self.assertIn('"status": "FAIL"', output.getvalue())

    def test_image_sources_refresh_os_packages_and_apply_effective_trust_gate(self):
        digest = "sha256:f06d01d4ba3f8ad5af45a3ec87df19c1d317d8e8961edc0fabda61811af6f7cf"
        for relative in ("Dockerfile", "Dockerfile.iran", "deploy/market-data/Dockerfile"):
            with self.subTest(relative=relative):
                source = (ROOT / relative).read_text()
                self.assertIn("3.11.17-slim-bookworm@" + digest, source)
                self.assertNotIn("bullseye", source)
                self.assertIn("upgrade -y --no-install-recommends", source)
                self.assertIn("ca-certificates", source)
                self.assertIn("verify_image_trust.py", source)
                if relative != "deploy/market-data/Dockerfile":
                    self.assertIn("--require-certifi", source)
                else:
                    self.assertIn("FROM patched-base AS assembly", source)
                    self.assertIn("FROM patched-base AS runtime", source)

    def test_wheel_cache_verifier_and_release_signature_use_the_exact_app_base(self):
        base = (ROOT / "Dockerfile.iran").read_text().splitlines()[0].split()[1]
        source = (ROOT / "scripts/production_deploy_online.sh").read_text()
        verifier = source.split("prepare_pip_packages() {", 1)[1].split("\n}", 1)[0]
        signature = source.split("build_image_bundle_signature() {", 1)[1].split("\n}", 1)[0]
        self.assertIn(f'"{base}" sh -lc', verifier)
        self.assertIn("printf 'python_base_image=%s\\n' " + f'"{base}"', signature)
        self.assertNotIn("bullseye", verifier)
        self.assertNotIn("bullseye", signature)

    def test_market_trust_gate_is_in_image_signature_and_transferred_control_payload(self):
        source = (ROOT / "scripts/production_deploy_online.sh").read_text()
        signature = source.split("market_pipeline_image_input_signature() {", 1)[1].split("\n}", 1)[0]
        payload = source.split("prepare_market_pipeline_control_payload() {", 1)[1].split("\n}", 1)[0]
        self.assertIn("scripts/verify_image_trust.py", signature)
        self.assertIn("scripts/verify_image_trust.py", payload)


if __name__ == "__main__":
    unittest.main()
