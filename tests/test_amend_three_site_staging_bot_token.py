from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.amend_three_site_staging_bot_token import (
    BotMaterialAmendmentError,
    prepare,
    verify_amendment,
)


CAMPAIGN_ID = "11111111-1111-4111-8111-111111111111"
RELEASE_SHA = "a" * 40
PLAN_SHA = "b" * 64
STAGING_TOKEN = "123456789:" + "A" * 35
PRODUCTION_TOKEN = "987654321:" + "B" * 35


class _Response:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self, _limit):
        return json.dumps(
            {
                "ok": True,
                "result": {"id": 123456789, "is_bot": True, "username": "stage_bot"},
            }
        ).encode()


def _private(path: Path, content: str) -> Path:
    path.write_text(content)
    path.chmod(0o600)
    return path


class BotTokenAmendmentTests(unittest.TestCase):
    def _paths(self, root: Path):
        base = _private(
            root / "base.env",
            "# retained\nBOT_TOKEN=disabled-stage3-value\nRELEASE_SHA=" + RELEASE_SHA + "\n",
        )
        source = _private(root / "source.env", f"BOT_TOKEN={STAGING_TOKEN}\n")
        forbidden = _private(root / "production.env", f"BOT_TOKEN={PRODUCTION_TOKEN}\n")
        return base, source, forbidden, root / "runtime.env", root / "evidence.json"

    def test_prepare_and_verify_change_only_bot_token(self):
        with tempfile.TemporaryDirectory() as stack:
            root = Path(stack)
            base, source, forbidden, runtime, evidence = self._paths(root)
            with patch(
                "scripts.amend_three_site_staging_bot_token.urllib.request.urlopen",
                return_value=_Response(),
            ):
                result = prepare(
                    campaign_id=CAMPAIGN_ID,
                    release_sha=RELEASE_SHA,
                    plan_sha256=PLAN_SHA,
                    base_env=base,
                    source_env=source,
                    forbidden_envs=[forbidden],
                    runtime_env_output=runtime,
                    evidence_output=evidence,
                    telegram_api_base="https://api.telegram.test",
                )
            verified = verify_amendment(
                evidence_path=evidence,
                base_env=base,
                runtime_env=runtime,
                campaign_id=CAMPAIGN_ID,
                release_sha=RELEASE_SHA,
                plan_sha256=PLAN_SHA,
            )

            self.assertEqual(result["status"], "prepared")
            self.assertEqual(verified["token_sha256"], result["token_sha256"])
            self.assertIn("# retained", runtime.read_text())
            self.assertNotIn("disabled-stage3", runtime.read_text())
            self.assertNotIn(STAGING_TOKEN, evidence.read_text())
            self.assertEqual(runtime.stat().st_mode & 0o777, 0o600)

    def test_prepare_rejects_production_token_source(self):
        with tempfile.TemporaryDirectory() as stack:
            root = Path(stack)
            base, _source, forbidden, runtime, evidence = self._paths(root)
            with self.assertRaisesRegex(BotMaterialAmendmentError, "forbidden"):
                prepare(
                    campaign_id=CAMPAIGN_ID,
                    release_sha=RELEASE_SHA,
                    plan_sha256=PLAN_SHA,
                    base_env=base,
                    source_env=forbidden,
                    forbidden_envs=[forbidden],
                    runtime_env_output=runtime,
                    evidence_output=evidence,
                    telegram_api_base="https://api.telegram.test",
                )

    def test_verify_rejects_any_second_env_change(self):
        with tempfile.TemporaryDirectory() as stack:
            root = Path(stack)
            base, source, forbidden, runtime, evidence = self._paths(root)
            with patch(
                "scripts.amend_three_site_staging_bot_token.urllib.request.urlopen",
                return_value=_Response(),
            ):
                prepare(
                    campaign_id=CAMPAIGN_ID,
                    release_sha=RELEASE_SHA,
                    plan_sha256=PLAN_SHA,
                    base_env=base,
                    source_env=source,
                    forbidden_envs=[forbidden],
                    runtime_env_output=runtime,
                    evidence_output=evidence,
                    telegram_api_base="https://api.telegram.test",
                )
            runtime.write_text(runtime.read_text().replace(RELEASE_SHA, "c" * 40))
            runtime.chmod(0o600)
            with self.assertRaises(BotMaterialAmendmentError):
                verify_amendment(
                    evidence_path=evidence,
                    base_env=base,
                    runtime_env=runtime,
                    campaign_id=CAMPAIGN_ID,
                    release_sha=RELEASE_SHA,
                    plan_sha256=PLAN_SHA,
                )


if __name__ == "__main__":
    unittest.main()
