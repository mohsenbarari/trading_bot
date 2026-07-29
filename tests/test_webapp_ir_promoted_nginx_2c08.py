from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "deploy/production/nginx-webapp-ir-promoted-2c08-https.conf.template"


def location_body(source: str, location: str) -> str:
    match = re.search(
        rf"(?m)^    location {re.escape(location)} \{{(?P<body>.*?)^    \}}",
        source,
        re.DOTALL,
    )
    if match is None:
        raise AssertionError(f"missing Nginx location {location!r}")
    return match.group("body")


class WebappIrPromotedNginx2c08Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.text = TEMPLATE.read_text(encoding="utf-8")

    def test_uses_only_local_root_only_tls_and_exact_release_static_root(self) -> None:
        self.assertIn("ssl_certificate __WA_IR_CERTIFICATE_PATH__;", self.text)
        self.assertIn("ssl_certificate_key __WA_IR_CERTIFICATE_KEY_PATH__;", self.text)
        self.assertIn("root __WA_IR_RELEASE_ROOT__/mini_app_dist;", self.text)
        self.assertNotIn("__APP_ROOT__", self.text)
        self.assertNotIn("/srv/trading-bot/current", self.text)
        self.assertNotIn("/etc/letsencrypt/live/", self.text)
        self.assertNotIn("__FOREIGN_PUBLIC_IP__", self.text)
        self.assertNotIn("WEBAPP_FI", self.text)
        self.assertNotIn("FI_", self.text)

    def test_all_api_backends_are_the_fixed_loopback_promotion_port(self) -> None:
        backends = re.findall(r"(?m)^\s*proxy_pass\s+(https?://[^;]+);", self.text)
        self.assertEqual(backends, ["http://127.0.0.1:18000"] * 3)
        self.assertNotRegex(self.text, r"(?m)^\s*upstream\s+")

        api = location_body(self.text, "/api/")
        websocket = location_body(self.text, "/api/realtime/ws")
        self.assertIn("proxy_pass http://127.0.0.1:18000;", api)
        self.assertIn('proxy_set_header Connection "";', api)
        self.assertIn("proxy_pass http://127.0.0.1:18000;", websocket)
        self.assertIn('proxy_set_header Connection "upgrade";', websocket)
        self.assertIn("proxy_buffering off;", websocket)

    def test_direct_sync_is_fenced_before_the_generic_api_route(self) -> None:
        direct_sync = location_body(self.text, "= /api/sync/receive")
        self.assertIn("return 404;", direct_sync)
        self.assertNotIn("proxy_pass", direct_sync)
        self.assertNotIn("sync_worker", self.text)
        self.assertNotIn("__FOREIGN_PUBLIC_IP__", self.text)

    def test_static_assets_are_served_by_nginx_from_the_immutable_release(self) -> None:
        self.assertIn("location /assets/ {", self.text)
        self.assertIn("location @stale_js_chunk {", self.text)
        self.assertIn('add_header X-Static-Delivery "nginx" always;', self.text)
        self.assertIn('add_header Cache-Control "public, max-age=31536000, immutable" always;', self.text)
        self.assertNotIn("proxy_pass http://127.0.0.1:8000", self.text)

        assets = location_body(self.text, "/assets/")
        uploads = location_body(self.text, "/uploads/")
        self.assertIn("try_files $uri =404;", assets)
        self.assertNotIn("proxy_pass", assets)
        self.assertIn("return 404;", uploads)

    def test_rendering_consumes_only_wa_ir_promotion_placeholders(self) -> None:
        rendered = (
            self.text.replace("__SERVER_NAME__", "coin.gold-trade.ir")
            .replace(
                "__WA_IR_CERTIFICATE_PATH__",
                "/etc/trading-bot-three-site/wa-ir/tls/fullchain.pem",
            )
            .replace(
                "__WA_IR_CERTIFICATE_KEY_PATH__",
                "/etc/trading-bot-three-site/wa-ir/tls/privkey.pem",
            )
            .replace(
                "__WA_IR_RELEASE_ROOT__",
                "/srv/trading-bot/releases/2c08da14bfa0ef94d9c788e478d30ddc3f31a3c5",
            )
        )
        self.assertNotRegex(rendered, r"__[A-Z0-9_]+__")
        self.assertIn("server_name coin.gold-trade.ir;", rendered)
        self.assertIn(
            "root /srv/trading-bot/releases/2c08da14bfa0ef94d9c788e478d30ddc3f31a3c5/mini_app_dist;",
            rendered,
        )


if __name__ == "__main__":
    unittest.main()
