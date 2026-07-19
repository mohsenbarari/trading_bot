from pathlib import Path
import re
import unittest


ROADMAP = Path(__file__).resolve().parents[1] / "docs" / "TELEGRAM_OFFER_PUBLICATION_QUEUE_ROADMAP_20260715.md"


class TelegramQueueRoadmapConsistencyTests(unittest.TestCase):
    def test_exactly_one_authoritative_stage_table_and_one_current_status(self):
        text = ROADMAP.read_text(encoding="utf-8")
        self.assertEqual(text.count("### نمای authoritative Stageها"), 1)
        self.assertEqual(text.count("وضعیت authoritative پس از remediation review"), 1)
        rows = re.findall(r"^\| `([0-6])` \| `([^`]+)` \|", text, flags=re.MULTILINE)
        self.assertEqual([stage for stage, _status in rows[:7]], list("0123456"))
        self.assertEqual(len(rows), 7)
        statuses = dict(rows)
        self.assertIn("RE-REVIEW-REQUIRED", statuses["3"])
        self.assertIn("BLOCKED-BY-STAGE-3-REVIEW", statuses["4"])
        self.assertIn("NOT-AUTHORIZED", statuses["6"])

    def test_every_final_review_finding_has_one_disposition_row(self):
        text = ROADMAP.read_text(encoding="utf-8")
        for index in range(1, 13):
            finding = f"TQ-F{index:03d}"
            self.assertEqual(
                len(re.findall(rf"^\| `{finding}` \|", text, flags=re.MULTILINE)),
                1,
                finding,
            )


if __name__ == "__main__":
    unittest.main()
