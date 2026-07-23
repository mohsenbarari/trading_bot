from __future__ import annotations

import unittest

from core.three_site_staging_source_contract import (
    LEGACY_STAGING_PROJECTS,
    legacy_staging_project_allowed,
)


class ThreeSiteStagingSourceProjectContractTests(unittest.TestCase):
    def test_only_exact_deployed_staging_projects_are_allowlisted(self):
        self.assertEqual(
            LEGACY_STAGING_PROJECTS,
            {"trading_bot_staging", "trading_bot_staging_iran"},
        )
        self.assertTrue(
            legacy_staging_project_allowed(
                "trading_bot_staging", ("bot_fi", "webapp_fi")
            )
        )
        self.assertTrue(
            legacy_staging_project_allowed(
                "trading_bot_staging_iran", ("webapp_fi",)
            )
        )

    def test_iran_project_cannot_be_relabelled_as_bot_source(self):
        self.assertFalse(
            legacy_staging_project_allowed(
                "trading_bot_staging_iran", ("bot_fi",)
            )
        )

    def test_production_or_unknown_project_is_rejected(self):
        for project in ("trading_bot", "trading_bot_production", "", "other"):
            self.assertFalse(
                legacy_staging_project_allowed(project, ("webapp_fi",))
            )
        self.assertFalse(
            legacy_staging_project_allowed("trading_bot_staging", ())
        )
