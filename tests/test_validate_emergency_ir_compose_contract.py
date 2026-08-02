"""Focused strict-JSON tests for the immutable Emergency Compose contract."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import shutil
import stat
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE_BASE = ROOT / "deploy/emergency-ir/docker-compose.standalone.yml"
SOURCE_SMS = ROOT / "deploy/emergency-ir/docker-compose.sms-otp.yml"
MODULE_PATH = ROOT / "scripts/validate_emergency_ir_compose_contract.py"
SPEC = importlib.util.spec_from_file_location("validate_emergency_ir_compose_contract", MODULE_PATH)
assert SPEC and SPEC.loader
CONTRACT = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = CONTRACT
SPEC.loader.exec_module(CONTRACT)


def _canonical(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode("ascii")


@unittest.skipUnless(os.geteuid() == 0, "contract validator requires root-controlled fixtures")
class EmergencyComposeContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="emergency-compose-contract-")
        self.root = Path(self.temporary.name)
        self.root.chmod(0o700)
        self.base = self.root / "docker-compose.standalone.json"
        self.sms = self.root / "docker-compose.sms-otp.json"
        shutil.copyfile(SOURCE_BASE, self.base)
        shutil.copyfile(SOURCE_SMS, self.sms)
        self.base.chmod(0o644)
        self.sms.chmod(0o644)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_checked_in_sources_are_canonical_and_deep_exact(self) -> None:
        telegram = CONTRACT.validate_contract(base=SOURCE_BASE, profile="telegram-only")
        self.assertEqual("verified-local-only", telegram["status"])
        self.assertEqual(CONTRACT.BASE_SHA256, telegram["base_sha256"])
        self.assertFalse(telegram["docker_or_service_changed"])
        sms = CONTRACT.validate_contract(base=SOURCE_BASE, profile="sms-otp", sms=SOURCE_SMS)
        self.assertEqual(CONTRACT.SMS_SHA256, sms["sms_sha256"])
        self.assertEqual("sms-otp", sms["profile"])

    def test_canonical_nested_drift_is_rejected_not_just_top_level_shape(self) -> None:
        value = json.loads(self.base.read_text(encoding="utf-8"))
        value["services"]["api"]["ports"] = ["0.0.0.0:18000:8000"]
        self.base.write_bytes(_canonical(value))
        self.base.chmod(0o644)
        with self.assertRaisesRegex(CONTRACT.EmergencyComposeContractError, "deep-exact"):
            CONTRACT.validate_contract(base=self.base, profile="telegram-only")

    def test_noncanonical_json_and_duplicate_keys_fail_before_contract_comparison(self) -> None:
        self.base.write_bytes(b'{"services":{},"name":"trading-bot-emergency-ir"}\n')
        self.base.chmod(0o644)
        with self.assertRaisesRegex(CONTRACT.EmergencyComposeContractError, "canonical JSON"):
            CONTRACT.validate_contract(base=self.base, profile="telegram-only")
        self.base.write_bytes(b'{"name":"a","name":"b"}\n')
        self.base.chmod(0o644)
        with self.assertRaisesRegex(CONTRACT.EmergencyComposeContractError, "duplicate"):
            CONTRACT.validate_contract(base=self.base, profile="telegram-only")

    def test_profile_overlay_pairing_is_exact(self) -> None:
        with self.assertRaisesRegex(CONTRACT.EmergencyComposeContractError, "requires"):
            CONTRACT.validate_contract(base=self.base, profile="sms-otp")
        with self.assertRaisesRegex(CONTRACT.EmergencyComposeContractError, "refuses"):
            CONTRACT.validate_contract(base=self.base, profile="telegram-only", sms=self.sms)
        overlay = json.loads(self.sms.read_text(encoding="utf-8"))
        overlay["networks"]["emergency_ir_sms_egress"]["internal"] = True
        self.sms.write_bytes(_canonical(overlay))
        self.sms.chmod(0o644)
        with self.assertRaisesRegex(CONTRACT.EmergencyComposeContractError, "deep-exact"):
            CONTRACT.validate_contract(base=self.base, profile="sms-otp", sms=self.sms)

    def test_symlink_and_non_owner_writable_source_are_rejected(self) -> None:
        alias = self.root / "alias.json"
        alias.symlink_to(self.base)
        with self.assertRaisesRegex(CONTRACT.EmergencyComposeContractError, "cannot be read|root-controlled"):
            CONTRACT.validate_contract(base=alias, profile="telegram-only")
        self.base.chmod(0o666)
        with self.assertRaisesRegex(CONTRACT.EmergencyComposeContractError, "root-controlled"):
            CONTRACT.validate_contract(base=self.base, profile="telegram-only")
        self.assertEqual(0o666, stat.S_IMODE(self.base.stat().st_mode))


if __name__ == "__main__":
    unittest.main()
