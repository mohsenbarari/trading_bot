import copy
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location(
    "processor_handoff",
    Path(__file__).resolve().parents[1] / "scripts/incident_processor_handoff_20260907.py",
)
handoff = importlib.util.module_from_spec(spec)
spec.loader.exec_module(handoff)


class ProcessorHandoffGuards(unittest.TestCase):
    def configs(self):
        old = {
            "services": {
                handoff.ROLE: {
                    "image": handoff.OLD_IMAGE,
                    "restart": "on-failure:5",
                    "environment": {"MARKET_PIPELINE_RELEASE_SHA": handoff.OLD},
                    "labels": {"org.opencontainers.image.revision": handoff.OLD},
                    "volumes": [{"source": "/retained/state", "target": "/state"}],
                    "mem_limit": "384m",
                },
                "market-fact-sync-worker": {"image": "untouched-sender"},
                "market-capture-account2": {"image": "untouched-capture"},
            },
            "secrets": {"mounted-key": {"file": "/retained/key"}},
            "networks": {"private": {"internal": True}},
        }
        new = copy.deepcopy(old)
        target = new["services"][handoff.ROLE]
        target["image"] = handoff.NEW_IMAGE
        target["restart"] = "unless-stopped"
        target["environment"]["MARKET_PIPELINE_RELEASE_SHA"] = handoff.NEW
        target["labels"]["org.opencontainers.image.revision"] = handoff.NEW
        return old, new

    def test_only_exact_processor_delta_is_allowed(self):
        handoff.validate_config(*self.configs())

    def test_both_capture_and_sender_are_protected(self):
        for role in ("market-fact-sync-worker", "market-capture-account2"):
            with self.subTest(role=role):
                old, new = self.configs()
                new["services"][role]["image"] = "changed"
                with self.assertRaisesRegex(RuntimeError, "unrelated_service_drift"):
                    handoff.validate_config(old, new)

    def test_processor_mount_environment_and_resource_drift_are_rejected(self):
        for field, value in (("volumes", []), ("mem_limit", "4g"),
                             ("environment", {}), ("restart", "always")):
            with self.subTest(field=field):
                old, new = self.configs()
                new["services"][handoff.ROLE][field] = value
                with self.assertRaisesRegex(RuntimeError, "unexpected_target_config_drift"):
                    handoff.validate_config(old, new)

    def test_secrets_and_networks_are_protected(self):
        for field in ("secrets", "networks"):
            with self.subTest(field=field):
                old, new = self.configs()
                new[field] = {}
                with self.assertRaisesRegex(RuntimeError, "infrastructure_config_drift"):
                    handoff.validate_config(old, new)

    def test_service_addition_is_rejected(self):
        old, new = self.configs()
        new["services"]["new-owner"] = {}
        with self.assertRaisesRegex(RuntimeError, "service_set_drift"):
            handoff.validate_config(old, new)

    def test_parent_lock_cannot_have_two_owners_and_bytes_are_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "owner.lock"
            handoff.atomic(path, {"retain": True})
            before = path.read_bytes()
            with handoff.held(path, os.getuid()):
                with self.assertRaises(BlockingIOError):
                    with handoff.held(path, os.getuid()):
                        pass
            self.assertEqual(path.read_bytes(), before)

    def test_hardlinks_and_symlinks_cannot_substitute_owner_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "owner.lock"
            handoff.atomic(path, {"retain": True})
            link = Path(directory) / "link"
            link.symlink_to(path)
            with self.assertRaisesRegex(RuntimeError, "symlink_path"):
                with handoff.held(link, os.getuid()):
                    pass
            link.unlink()
            os.link(path, link)
            with self.assertRaisesRegex(RuntimeError, "unsafe_file_metadata"):
                with handoff.held(path, os.getuid()):
                    pass


if __name__ == "__main__":
    unittest.main()
