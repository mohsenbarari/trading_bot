from __future__ import annotations

import io
import json
import unittest
from unittest.mock import patch

from scripts import rehearse_market_external_stage7 as rehearsal


class Stage7ExternalRehearsalTests(unittest.TestCase):
    def test_main_reports_only_bounded_reason_code(self) -> None:
        output = io.StringIO()
        with patch.object(
            rehearsal,
            "run_gate",
            side_effect=rehearsal.Stage7ExternalGateError("bounded_failure"),
        ), patch("sys.stdout", output):
            self.assertEqual(rehearsal.main(), 1)
        self.assertEqual(
            json.loads(output.getvalue()),
            {"reason_code": "bounded_failure", "status": "fail"},
        )


if __name__ == "__main__":
    unittest.main()
