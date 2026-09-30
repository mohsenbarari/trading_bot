"""Tests for Codex autopilot prompt final-review test subprocess environment configuration."""

from pathlib import Path
import re
import unittest

PROMPT_REL_PATH = Path("docs/refactor/two-server/templates/CODEX_AUTOPILOT_PROMPT.md")


def _load_prompt_text() -> str:
    candidates = [
        PROMPT_REL_PATH,
        Path(__file__).resolve().parents[1] / PROMPT_REL_PATH,
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate.read_text(encoding="utf-8")
    raise FileNotFoundError(f"Could not locate {PROMPT_REL_PATH}")


class TestTwoSiteCodexPromptTestEnv(unittest.TestCase):
    def setUp(self) -> None:
        self.prompt_text = _load_prompt_text()

    def test_final_review_test_command_pattern_and_environment_flags(self) -> None:
        expected_command = (
            "env -u TWO_SITE_ASSIGNMENT_SCOPE_REQUIRED -u TWO_SITE_ASSIGNMENT_ID "
            "-u TWO_SITE_ASSIGNMENT_SHA256 APP_ENV_FILE=config/unit-test.env.example "
            "PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. python3 -m pytest -q <focused selectors>"
        )
        self.assertIn(expected_command, self.prompt_text)
        self.assertIn("python3 -m pytest", self.prompt_text)
        self.assertIn("APP_ENV_FILE=config/unit-test.env.example", self.prompt_text)
        self.assertIn("PYTHONPATH=.", self.prompt_text)
        self.assertIn("PYTHONDONTWRITEBYTECODE=1", self.prompt_text)

        cmd_match = re.search(r"env (?:-u [A-Za-z0-9_]+ )+APP_ENV_FILE", self.prompt_text)
        self.assertIsNotNone(cmd_match)
        matched_str = cmd_match.group(0)
        unset_vars = re.findall(r"-u ([A-Za-z0-9_]+)", matched_str)
        self.assertEqual(
            set(unset_vars),
            {
                "TWO_SITE_ASSIGNMENT_SCOPE_REQUIRED",
                "TWO_SITE_ASSIGNMENT_ID",
                "TWO_SITE_ASSIGNMENT_SHA256",
            },
        )
        self.assertEqual(len(unset_vars), 3)

    def test_scope_limitation_wording(self) -> None:
        self.assertIn(
            "Assignment scope still applies to every non-test operation and the environment override is confined to hermetic pytest subprocesses",
            self.prompt_text,
        )
        self.assertIn(
            "Do not prescribe unsetting any scope variable for Runner, Broker, Supervisor, task validation, review, staging, commit or transition",
            self.prompt_text,
        )
        self.assertIn(
            "do not weaken any existing security or review instruction",
            self.prompt_text,
        )
        self.assertIn(
            "Adapt selectors to the task.",
            self.prompt_text,
        )

    def test_retained_exact_sha_and_review_guardrails(self) -> None:
        self.assertIn("scripts/two_site_cycle_tools.py review --task <path>", self.prompt_text)
        self.assertIn("--applied BEFORE staging", self.prompt_text)
        self.assertIn("BYTES_VALID is not test/semantic approval", self.prompt_text)
        self.assertIn("scripts/two_site_cycle_packet.py packet", self.prompt_text)
        self.assertIn("proposal_sha256", self.prompt_text)
        self.assertIn("Supervisor exact-proposal validation", self.prompt_text)


if __name__ == "__main__":
    unittest.main()
