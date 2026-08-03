from __future__ import annotations

import hashlib
import unittest

from scripts.prepare_three_site_stage4r_journal_material import (
    JOURNAL_KEY_ID,
    JournalMaterialError,
    build_environment,
)


RELEASE = "a" * 40
SOURCE = "\n".join(
    (
        "STAGING_RELEASE_SHA=" + "b" * 40,
        "STAGING_SOURCE_ROOT=/srv/trading-bot-three-site-staging-data/releases/old/source",
        "FRONTEND_URL=https://staging.gold-trade.ir",
    )
) + "\n"


class Stage4RJournalMaterialTests(unittest.TestCase):
    def test_builds_one_disabled_two_phase_environment_with_matched_pairwise_keys(self):
        values = iter(("database-secret-" + "a" * 48, "pairwise-secret-" + "b" * 48, "encryption-secret-" + "c" * 48))
        rendered, metadata = build_environment(
            SOURCE,
            release_sha=RELEASE,
            token_factory=lambda _length: next(values),
        )
        text = rendered.decode()
        self.assertIn(f"STAGING_RELEASE_SHA={RELEASE}\n", text)
        self.assertIn("STAGING_WEBAPP_FI_JOURNAL_TWO_PHASE_ENABLED=false\n", text)
        self.assertIn("STAGING_WEBAPP_FI_MAX_PREPARED_TRANSACTIONS=32\n", text)
        self.assertIn(f"WEBAPP_FI_SAME_REGION_JOURNAL_ENCRYPTION_KEY_ID={JOURNAL_KEY_ID}\n", text)
        bot = next(line for line in text.splitlines() if line.startswith("BOT_FI_SAME_REGION_JOURNAL_KEYS_JSON="))
        webapp = next(line for line in text.splitlines() if line.startswith("WEBAPP_FI_SAME_REGION_JOURNAL_KEYS_JSON="))
        self.assertEqual(bot.partition("=")[2], webapp.partition("=")[2])
        self.assertEqual(metadata["environment_sha256"], hashlib.sha256(rendered).hexdigest())
        self.assertEqual(metadata["two_phase_enabled"], "false")

    def test_rejects_existing_journal_variables_to_prevent_implicit_rotation(self):
        with self.assertRaisesRegex(JournalMaterialError, "already contains journal material"):
            build_environment(SOURCE + "BOT_FI_JOURNAL_DB_PASSWORD=already-present\n", release_sha=RELEASE)

    def test_rejects_non_exact_release_identity(self):
        with self.assertRaisesRegex(JournalMaterialError, "exactly 40"):
            build_environment(SOURCE, release_sha="not-a-release")
