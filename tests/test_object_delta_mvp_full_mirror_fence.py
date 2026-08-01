from __future__ import annotations

from pathlib import Path
import unittest

from core.object_delta_mvp_full_mirror_fence import (
    ObjectDeltaMvpFullMirrorFenceError,
    assess_object_delta_mvp_full_mirror,
    require_object_delta_mvp_not_full_mirror,
)


class ObjectDeltaMvpFullMirrorFenceTests(unittest.TestCase):
    def test_current_mvp_is_explicitly_blocked_from_full_mirror_claims(self):
        assessment = assess_object_delta_mvp_full_mirror()

        self.assertEqual("blocked", assessment.status)
        self.assertEqual(23, len(assessment.source_tables))
        self.assertEqual(assessment.source_tables, assessment.receiver_tables)
        self.assertEqual(assessment.source_tables, assessment.unavailable_receiver_tables)
        self.assertEqual((("commodities", "INSERT"),), assessment.executable_receiver_slots)
        self.assertIn(("commodities", "UPDATE"), assessment.missing_receiver_slots)
        self.assertIn(("users", "INSERT"), assessment.missing_receiver_slots)
        self.assertIn(("trades", "DELETE"), assessment.missing_receiver_slots)

        with self.assertRaisesRegex(ObjectDeltaMvpFullMirrorFenceError, "not a complete"):
            require_object_delta_mvp_not_full_mirror()

    def test_fence_is_pure_and_cannot_be_mistaken_for_a_runtime_enabler(self):
        source = (
            Path(__file__).resolve().parents[1]
            / "core/object_delta_mvp_full_mirror_fence.py"
        ).read_text(encoding="utf-8")
        forbidden_imports = (
            "import sqlalchemy",
            "from sqlalchemy",
            "import boto",
            "from boto",
            "import requests",
            "from requests",
            "import httpx",
            "from httpx",
            "import aiohttp",
            "from aiohttp",
            "import subprocess",
            "from subprocess",
            "import socket",
            "from socket",
            "import os",
            "from os",
        )
        self.assertFalse([item for item in forbidden_imports if item in source])


if __name__ == "__main__":
    unittest.main()
