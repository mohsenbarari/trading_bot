from __future__ import annotations

import importlib.util
import stat
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "render_webapp_ir_dark_standby_env",
    ROOT / "scripts" / "render_webapp_ir_dark_standby_env.py",
)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class RenderDarkStandbyEnvTests(unittest.TestCase):
    def test_render_forces_data_only_contract_and_removes_s3_secrets(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.env"
            output = root / "target.env"
            source.write_text(
                "SERVER_MODE=foreign\nBACKGROUND_JOBS_ENABLED=true\n"
                "BOT_TOKEN=retained-for-future-auth\nARVAN_S3_SECRET_KEY=remove-me\n",
                encoding="utf-8",
            )
            MODULE.render(source, output, "a" * 40)
            values = MODULE.parse(output)
            self.assertEqual(values["SERVER_MODE"], "iran")
            self.assertEqual(values["BACKGROUND_JOBS_ENABLED"], "false")
            self.assertEqual(values["COMPOSE_PROJECT_NAME"], "trading_bot_dark_ir")
            self.assertEqual(values["RELEASE_SHA"], "a" * 40)
            self.assertEqual(values["BOT_TOKEN"], "retained-for-future-auth")
            self.assertNotIn("ARVAN_S3_SECRET_KEY", values)
            self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)


if __name__ == "__main__":
    unittest.main()
