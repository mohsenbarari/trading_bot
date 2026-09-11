"""Parser for historical Python test-selector specifications."""
from __future__ import annotations

import re
import shlex

try:
    from scripts.two_site_agent_sandbox import SandboxError
except ImportError:
    try:
        from two_site_agent_sandbox import SandboxError
    except ImportError:
        class SandboxError(ValueError):
            """Raised when API argument types are malformed."""

__all__ = ["SandboxError", "parse_test_spec"]

ALLOWED_ENV = frozenset({
    "APP_ENV_FILE=config/unit-test.env.example",
    "PYTHONDONTWRITEBYTECODE=1",
    "PYTHONPATH=.",
})

FORBIDDEN_CHARS = frozenset(";&|<>$`()\n\r!~{}\\")

GLOB_PATTERN = re.compile(r"^tests/(?:[A-Za-z0-9_]+/)*test_[A-Za-z0-9_]*\*[A-Za-z0-9_*]*\.py$")
DOTTED_PATTERN = re.compile(r"^tests(?:\.[A-Za-z0-9_]+)*\.(test_[A-Za-z0-9_]+)$")
PATH_PATTERN = re.compile(r"^tests/(?:[A-Za-z0-9_]+/)*test_[A-Za-z0-9_]+\.py$")
NODE_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
PYTEST_OPT_PATTERN = re.compile(r"^-[qv]+$")
UNITTEST_OPT_PATTERN = re.compile(r"^-v+$")


def _normalize_selector(token: str) -> str | None:
    if "*" in token:
        if "::" in token or not GLOB_PATTERN.fullmatch(token):
            return None
        return token

    if DOTTED_PATTERN.fullmatch(token):
        return token.replace(".", "/") + ".py"

    if "::" in token:
        path, *nodes = token.split("::")
        if not nodes or any(not NODE_PATTERN.fullmatch(n) for n in nodes):
            return None
        if not PATH_PATTERN.fullmatch(path):
            return None
        return f"{path}::{'::'.join(nodes)}"

    if PATH_PATTERN.fullmatch(token):
        return token

    return None


def parse_test_spec(spec: str) -> tuple[str, ...] | None:
    """Parse a test specification into normalized selector tokens."""
    if not isinstance(spec, str):
        raise SandboxError(f"spec must be a str, got {type(spec).__name__}")

    if any(c in FORBIDDEN_CHARS for c in spec):
        return None

    try:
        tokens = shlex.split(spec)
    except ValueError:
        return None

    if not tokens:
        return None

    idx = 0
    while idx < len(tokens) and "=" in tokens[idx]:
        if tokens[idx] not in ALLOWED_ENV:
            return None
        idx += 1

    rem = tokens[idx:]
    if not rem or any("=" in tok for tok in rem):
        return None

    if rem[0] == "pytest":
        runner, args = "pytest", rem[1:]
    elif rem[:3] == ["python3", "-m", "pytest"]:
        runner, args = "pytest", rem[3:]
    elif rem[:3] == ["python3", "-m", "unittest"]:
        runner, args = "unittest", rem[3:]
    elif rem[0] == "python3":
        return None
    else:
        runner, args = "direct", rem

    selectors: list[str] = []
    for arg in args:
        if arg.startswith("-"):
            if runner == "pytest" and PYTEST_OPT_PATTERN.fullmatch(arg):
                continue
            if runner == "unittest" and UNITTEST_OPT_PATTERN.fullmatch(arg):
                continue
            return None

        normalized = _normalize_selector(arg)
        if normalized is None:
            return None
        selectors.append(normalized)

    if not selectors:
        return None

    return tuple(selectors)
