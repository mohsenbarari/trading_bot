from __future__ import annotations

from dataclasses import replace
import sqlite3
import unittest

from core.market_intelligence.coin_group_calibration_corpus import (
    CoinGroupCalibrationCorpusError,
    append_coin_group_feedback_revisions,
)
from core.market_intelligence.coin_group_feedback import CoinGroupParserFeedback


def feedback(*, revision: int = 1, price: int = 188_600) -> CoinGroupParserFeedback:
    return CoinGroupParserFeedback(
        event_key=b"k" * 32,
        event_type="OFFER",
        group_number=2,
        source_event_time_utc="2026-08-25T10:00:00Z",
        ambiguous_fields=frozenset({"commodity", "price"}),
        event_confirmed=True,
        commodity_code="IMAM",
        side="SELL",
        price_project_thousand_toman=price,
        quantity=5,
        settlement_term="TOMORROW",
        trade_form="PHYSICAL",
        is_conditional=False,
        review_revision=revision,
        reviewed_at_utc=f"2026-08-25T10:0{revision}:00Z",
        applied_revision=revision,
        applied_at_utc=f"2026-08-25T10:0{revision}:01Z",
        application_count=1,
    )


class CoinGroupCalibrationCorpusTests(unittest.TestCase):
    def setUp(self) -> None:
        self.connection = sqlite3.connect(":memory:")
        self.connection.row_factory = sqlite3.Row

    def tearDown(self) -> None:
        self.connection.close()

    def test_revisions_append_without_overwriting_history(self) -> None:
        first = append_coin_group_feedback_revisions(
            self.connection,
            [feedback()],
            parser_version_before="parser-v1",
            appended_at_utc="2026-08-25T10:01:05Z",
        )
        replay = append_coin_group_feedback_revisions(
            self.connection,
            [feedback()],
            parser_version_before="parser-v1",
            appended_at_utc="2026-08-25T10:02:05Z",
        )
        second = append_coin_group_feedback_revisions(
            self.connection,
            [feedback(revision=2, price=188_700)],
            parser_version_before="parser-v2",
            appended_at_utc="2026-08-25T10:03:05Z",
        )

        self.assertEqual((first.revisions_appended, replay.idempotent_replays), (1, 1))
        self.assertEqual(second.revisions_appended, 1)
        rows = self.connection.execute(
            """
            SELECT review_revision,price_project_thousand_toman,parser_version_before
            FROM coin_group_calibration_corpus ORDER BY review_revision
            """
        ).fetchall()
        self.assertEqual(
            [tuple(row) for row in rows],
            [(1, 188_600, "parser-v1"), (2, 188_700, "parser-v2")],
        )
        columns = {
            str(row["name"])
            for row in self.connection.execute(
                "PRAGMA table_info(coin_group_calibration_corpus)"
            )
        }
        self.assertNotIn("raw_text", columns)
        self.assertNotIn("reviewer_digest", columns)

    def test_existing_revision_with_changed_payload_fails_closed(self) -> None:
        append_coin_group_feedback_revisions(
            self.connection,
            [feedback()],
            parser_version_before="parser-v1",
        )
        with self.assertRaisesRegex(
            CoinGroupCalibrationCorpusError,
            "revision_conflict",
        ):
            append_coin_group_feedback_revisions(
                self.connection,
                [replace(feedback(), price_project_thousand_toman=188_700)],
                parser_version_before="parser-v1",
            )


if __name__ == "__main__":
    unittest.main()
