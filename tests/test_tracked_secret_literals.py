import re
import subprocess
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
DEV_API_KEY_PATTERNS = (
    re.compile(r"\bDEV_API_KEY\s*=\s*([^\s#]+)"),
    re.compile(r"\bDEV_API_KEY\s*:\s*`([^`]+)`"),
    re.compile(r"\bDEV_API_KEY\s*:\s*[\"']?([^\"'\s#`,]+)"),
)
PLACEHOLDER_VALUES = {
    "",
    "dev-key",
    "secret",
    "secret-key",
    "change-me",
    "change-me-dev-api-key",
    "not_set",
    "not-set",
    "redacted_dev_api_key",
    "[redacted]",
}


def _tracked_files() -> list[Path]:
    output = subprocess.check_output(["git", "ls-files"], cwd=REPO_ROOT, text=True)
    return [REPO_ROOT / line for line in output.splitlines() if line]


def _normalize_candidate(value: str) -> str:
    return value.strip().strip("\"'`").strip()


def _is_allowed_placeholder(value: str) -> bool:
    lowered = value.lower()
    if lowered in PLACEHOLDER_VALUES:
        return True
    if value.startswith("$") or "${" in value:
        return True
    if "redacted" in lowered or "example" in lowered or "placeholder" in lowered:
        return True
    return False


class TrackedSecretLiteralsTests(unittest.TestCase):
    def test_tracked_files_do_not_contain_real_dev_api_key_literals(self):
        findings: list[str] = []

        for path in _tracked_files():
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            for line_number, line in enumerate(text.splitlines(), start=1):
                for pattern in DEV_API_KEY_PATTERNS:
                    match = pattern.search(line)
                    if not match:
                        continue
                    candidate = _normalize_candidate(match.group(1))
                    if len(candidate) < 24 or _is_allowed_placeholder(candidate):
                        continue
                    relative_path = path.relative_to(REPO_ROOT)
                    findings.append(f"{relative_path}:{line_number}: DEV_API_KEY literal")

        self.assertEqual(findings, [], "real DEV_API_KEY-like literals found in tracked files")


if __name__ == "__main__":
    unittest.main()
