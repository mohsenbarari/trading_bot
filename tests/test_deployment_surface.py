import unittest
from types import SimpleNamespace

from core.deployment_surface import (
    allowed_cors_origins,
    extract_host,
    foreign_server_aliases,
    iran_server_aliases,
    normalize_origin,
    sms_public_host,
)


class DeploymentSurfaceTests(unittest.TestCase):
    def test_normalize_origin_and_extract_host_handle_bare_domains_urls_and_ports(self):
        self.assertEqual(normalize_origin("coin.gold-trade.ir"), "https://coin.gold-trade.ir")
        self.assertEqual(normalize_origin("http://65.109.220.59"), "http://65.109.220.59")
        self.assertEqual(extract_host("https://coin.gold-trade.ir:443/path"), "coin.gold-trade.ir")
        self.assertEqual(extract_host("coin.362514.ir:8443"), "coin.362514.ir")

    def test_alias_sets_merge_env_aliases_domains_and_urls(self):
        settings = SimpleNamespace(
            iran_server_aliases="iran-a.example, 10.0.0.8",
            iran_server_domain="coin.gold-trade.ir",
            iran_server_url="https://iran-api.example",
            frontend_url="https://iran-web.example",
            foreign_server_aliases="foreign-a.example, 10.0.0.9",
            foreign_server_domain="coin.362514.ir",
            foreign_server_url="https://foreign-api.example",
            germany_server_url="https://foreign-web.example",
        )

        self.assertEqual(
            iran_server_aliases(settings),
            {"iran-a.example", "10.0.0.8", "coin.gold-trade.ir", "iran-api.example", "iran-web.example"},
        )
        self.assertEqual(
            foreign_server_aliases(settings),
            {"foreign-a.example", "10.0.0.9", "coin.362514.ir", "foreign-api.example", "foreign-web.example"},
        )

    def test_allowed_cors_origins_include_runtime_urls_and_extra_origins(self):
        settings = SimpleNamespace(
            environment="staging",
            frontend_url="https://coin.gold-trade.ir",
            foreign_server_domain="coin.362514.ir",
            iran_server_domain="coin.gold-trade.ir",
            foreign_server_url="https://api.coin.362514.ir",
            iran_server_url="https://api.coin.gold-trade.ir",
            extra_cors_origins="http://65.109.220.59,https://staging.gold-trade.ir",
        )

        origins = allowed_cors_origins(settings)

        self.assertIn("http://localhost:5173", origins)
        self.assertIn("https://coin.gold-trade.ir", origins)
        self.assertIn("https://coin.362514.ir", origins)
        self.assertIn("https://api.coin.362514.ir", origins)
        self.assertIn("https://api.coin.gold-trade.ir", origins)
        self.assertIn("http://65.109.220.59", origins)
        self.assertIn("https://staging.gold-trade.ir", origins)

    def test_allowed_cors_origins_exclude_localhost_in_production(self):
        settings = SimpleNamespace(
            environment="production",
            frontend_url="https://coin.gold-trade.ir",
            foreign_server_domain="coin.362514.ir",
            iran_server_domain="coin.gold-trade.ir",
            foreign_server_url="https://coin.362514.ir",
            iran_server_url="https://coin.gold-trade.ir",
            extra_cors_origins="",
        )

        origins = allowed_cors_origins(settings)

        self.assertNotIn("http://localhost:5173", origins)
        self.assertNotIn("http://127.0.0.1:8000", origins)
        self.assertIn("https://coin.gold-trade.ir", origins)
        self.assertIn("https://coin.362514.ir", origins)

    def test_allowed_cors_origins_treat_empty_environment_as_production_safe(self):
        settings = SimpleNamespace(
            environment="",
            frontend_url="https://coin.gold-trade.ir",
            foreign_server_domain="coin.362514.ir",
            iran_server_domain="coin.gold-trade.ir",
            foreign_server_url="https://coin.362514.ir",
            iran_server_url="https://coin.gold-trade.ir",
            extra_cors_origins="",
        )

        origins = allowed_cors_origins(settings)

        self.assertNotIn("http://localhost:5173", origins)
        self.assertNotIn("http://127.0.0.1:8000", origins)
        self.assertIn("https://coin.gold-trade.ir", origins)
        self.assertIn("https://coin.362514.ir", origins)

    def test_sms_public_host_prefers_explicit_override_then_runtime_urls(self):
        explicit = SimpleNamespace(
            sms_public_host="sms.gold-trade.ir",
            frontend_url="https://coin.gold-trade.ir",
            iran_server_domain=None,
            iran_server_url=None,
        )
        derived = SimpleNamespace(
            sms_public_host=None,
            frontend_url="https://coin.gold-trade.ir",
            iran_server_domain=None,
            iran_server_url=None,
        )

        self.assertEqual(sms_public_host(explicit), "sms.gold-trade.ir")
        self.assertEqual(sms_public_host(derived), "coin.gold-trade.ir")


if __name__ == "__main__":
    unittest.main()
