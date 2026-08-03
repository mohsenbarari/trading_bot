import unittest
from unittest.mock import patch

from scripts.ensure_three_site_stage4_public_ingress import (
    PUBLIC_RULES,
    Stage4PublicIngressError,
    confirmation_phrase,
    execute,
)


def group_with_rules(rules=()):
    return {
        "rules": [
            {
                "description": description,
                "direction": "ingress",
                "protocol": "tcp",
                "port_start": port,
                "port_end": port,
                "ip": source,
                "ether_type": "IPv4",
            }
            for description, source, port in rules
        ]
    }


class Stage4PublicIngressTests(unittest.TestCase):
    def test_rules_are_exact_public_http_and_https_for_disposable_webapp(self):
        self.assertEqual(
            PUBLIC_RULES,
            (
                ("stage4-webapp-fi-public-http", "0.0.0.0/0", 80),
                ("stage4-webapp-fi-public-https", "0.0.0.0/0", 443),
            ),
        )

    def test_dry_run_is_non_mutating_and_reports_confirmation(self):
        group = group_with_rules()
        with (
            patch("scripts.ensure_three_site_stage4_public_ingress._read_server"),
            patch(
                "scripts.ensure_three_site_stage4_public_ingress._read_groups",
                return_value={"eu-west1-a": group},
            ),
            patch("scripts.ensure_three_site_stage4_public_ingress.api_request") as request,
        ):
            result = execute("token", apply=False, confirm=None)
        self.assertEqual(result["status"], "planned")
        self.assertEqual(result["missing_rule_count"], 2)
        self.assertEqual(result["required_confirmation"], confirmation_phrase())
        request.assert_not_called()

    def test_apply_requires_exact_confirmation_before_mutation(self):
        group = group_with_rules()
        with (
            patch("scripts.ensure_three_site_stage4_public_ingress._read_server"),
            patch(
                "scripts.ensure_three_site_stage4_public_ingress._read_groups",
                return_value={"eu-west1-a": group},
            ),
            patch("scripts.ensure_three_site_stage4_public_ingress.api_request") as request,
        ):
            with self.assertRaisesRegex(Stage4PublicIngressError, "confirmation mismatch"):
                execute("token", apply=True, confirm="wrong")
        request.assert_not_called()


if __name__ == "__main__":
    unittest.main()
