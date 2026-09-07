import copy
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


SPEC = importlib.util.spec_from_file_location(
    "processor_calibration_handoff",
    Path(__file__).resolve().parents[1]
    / "scripts/incident_processor_calibration_handoff_20260907.py",
)
handoff = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(handoff)


class ProcessorCalibrationHandoffGuards(unittest.TestCase):
    def configs(self):
        prior = {
            "services": {
                handoff.ROLE: {
                    "image": handoff.OLD_IMAGE,
                    "restart": "unless-stopped",
                    "environment": {
                        "MARKET_PIPELINE_RELEASE_SHA": handoff.OLD_RELEASE,
                        "MARKET_PROCESSOR_MAX_FACT_EXPORTS_PER_CYCLE": "500",
                    },
                    "labels": {
                        "org.opencontainers.image.revision": handoff.OLD_RELEASE,
                    },
                    "volumes": [{"source": "/retained/state", "target": "/state"}],
                    "mem_limit": "384m",
                },
                "market-fact-sync-worker": {"image": "untouched-sender"},
                "market-capture-account2": {"image": "untouched-capture"},
            },
            "secrets": {"mounted-key": {"file": "/retained/key"}},
            "networks": {"private": {"internal": True}},
        }
        candidate = copy.deepcopy(prior)
        target = candidate["services"][handoff.ROLE]
        target["image"] = handoff.NEW_IMAGE
        target["environment"]["MARKET_PIPELINE_RELEASE_SHA"] = handoff.NEW_RELEASE
        target["environment"][
            "MARKET_PROCESSOR_MAX_FACT_EXPORTS_PER_CYCLE"
        ] = handoff.EXPORT_LIMIT
        target["labels"][
            "org.opencontainers.image.revision"
        ] = handoff.NEW_RELEASE
        return prior, candidate

    def test_exact_processor_delta_is_accepted(self):
        handoff.validate_config(*self.configs())

    def test_bystander_and_infrastructure_drift_are_rejected(self):
        for scope in ("sender", "capture", "network", "secret"):
            prior, candidate = self.configs()
            if scope == "sender":
                candidate["services"]["market-fact-sync-worker"]["image"] = "changed"
            elif scope == "capture":
                candidate["services"]["market-capture-account2"]["image"] = "changed"
            elif scope == "network":
                candidate["networks"] = {}
            else:
                candidate["secrets"] = {}
            with self.subTest(scope=scope), self.assertRaises(RuntimeError):
                handoff.validate_config(prior, candidate)

    def test_processor_mount_resource_and_export_limit_drift_are_rejected(self):
        for field, value in (
            ("volumes", []),
            ("mem_limit", "4g"),
            ("restart", "always"),
        ):
            prior, candidate = self.configs()
            candidate["services"][handoff.ROLE][field] = value
            with self.subTest(field=field), self.assertRaisesRegex(
                RuntimeError, "unexpected_target_config_drift"
            ):
                handoff.validate_config(prior, candidate)
        prior, candidate = self.configs()
        candidate["services"][handoff.ROLE]["environment"][
            "MARKET_PROCESSOR_MAX_FACT_EXPORTS_PER_CYCLE"
        ] = "5000"
        with self.assertRaisesRegex(RuntimeError, "unexpected_target_config_drift"):
            handoff.validate_config(prior, candidate)

    def test_candidate_override_is_last_and_rollback_excludes_it(self):
        rollback = handoff.compose(candidate=False)
        candidate = handoff.compose(candidate=True)
        latency_override = (
            "/srv/trading-bot/incident-recovery/20260907-processor-latency/"
            "processor-latency.override.json"
        )
        self.assertIn(latency_override, rollback)
        self.assertEqual(candidate, rollback + ["-f", str(handoff.OVERRIDE)])
        self.assertNotIn(str(handoff.OVERRIDE), rollback)

    def test_candidate_image_identity_and_portable_digest_are_bound(self):
        image = {
            "Id": handoff.NEW_IMAGE,
            "Architecture": "amd64",
            "Os": "linux",
            "Created": "fixed",
            "RootFS": {"Type": "layers", "Layers": ["sha256:one"]},
            "Config": {
                "User": "10001:10001",
                "Labels": {
                    "org.opencontainers.image.revision": handoff.NEW_RELEASE,
                    "io.gold-trade.release.tree": handoff.NEW_TREE,
                    "io.gold-trade.release.input-signature": handoff.NEW_INPUT_SIGNATURE,
                },
            },
        }
        expected = handoff.portable_digest(image)
        with patch.object(handoff, "PORTABLE_IMAGE_DIGEST", expected):
            handoff.validate_candidate_image(image)
        image["Config"]["Labels"]["io.gold-trade.release.tree"] = "drift"
        with patch.object(
            handoff, "PORTABLE_IMAGE_DIGEST", expected
        ), self.assertRaisesRegex(RuntimeError, "candidate_tree_drift"):
            handoff.validate_candidate_image(image)

    def test_lock_file_cannot_be_replaced_by_hardlink(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "owner.lock"
            handoff.atomic_json(path, {"retained": True})
            link = Path(directory) / "second-name"
            os.link(path, link)
            with self.assertRaisesRegex(RuntimeError, "unsafe_file_metadata"):
                handoff.file_metadata(path, uid=os.getuid())


if __name__ == "__main__":
    unittest.main()
