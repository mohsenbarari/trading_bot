"""Parser for historical Python test-selector specifications."""
from __future__ import annotations

from fnmatch import fnmatchcase
from pathlib import Path
import re
import shlex

try:
    from scripts.two_site_agent_sandbox import (
        SandboxError, _validate_overlay_path, source_files,
    )
except ImportError:
    try:
        from two_site_agent_sandbox import (
            SandboxError, _validate_overlay_path, source_files,
        )
    except ImportError:
        class SandboxError(ValueError):
            """Raised when API argument types are malformed."""
        def _validate_overlay_path(value: str): raise SandboxError("sandbox unavailable")
        def source_files(worktree, extra_paths): raise SandboxError("sandbox unavailable")

__all__ = [
    "SandboxError", "normalize_test_selectors",
    "parse_test_spec", "tracked_test_inventory",
]

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

MAX_SPECS, MAX_INVENTORY, MAX_OVERLAYS = 32, 10000, 32


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


def normalize_test_selectors(
    specs: list[str] | tuple[str, ...],
    available_paths: list[str] | tuple[str, ...],
) -> tuple[list[str], list[str]]:
    """Normalize and resolve test specs against available test inventory."""
    if not isinstance(specs, (list, tuple)) or not isinstance(available_paths, (list, tuple)):
        raise SandboxError("specs and available_paths must be lists or tuples")
    if not specs or len(specs) > MAX_SPECS:
        raise SandboxError("specs must be nonempty and within bounded size")
    if len(available_paths) > MAX_INVENTORY:
        raise SandboxError("available_paths exceeds bounded size")
    if any(not isinstance(s, str) for s in specs) or any(not isinstance(p, str) for p in available_paths):
        raise SandboxError("specs and available_paths entries must be strings")

    valid_inventory = sorted({p for p in available_paths if PATH_PATTERN.fullmatch(p)})
    valid_inventory_set = set(valid_inventory)
    selectors, seen_selectors, unsupported_specs, seen_unsupported = [], set(), [], set()

    for spec in specs:
        tokens = parse_test_spec(spec)
        spec_selectors: list[str] = []
        spec_valid = bool(tokens)
        for token in tokens or ():
            if "*" in token:
                parent, pat = token.rsplit("/", 1)
                matches = [p for p in valid_inventory if p.rsplit("/", 1)[0] == parent and fnmatchcase(p.rsplit("/", 1)[1], pat)]
                if not matches:
                    spec_valid = False
                    break
                spec_selectors.extend(matches)
            else:
                base = token.split("::", 1)[0] if "::" in token else token
                if base not in valid_inventory_set:
                    spec_valid = False
                    break
                spec_selectors.append(token)

        if not spec_valid:
            if spec not in seen_unsupported:
                seen_unsupported.add(spec)
                unsupported_specs.append(spec)
        else:
            for sel in spec_selectors:
                if sel not in seen_selectors:
                    seen_selectors.add(sel)
                    selectors.append(sel)

    if not selectors:
        raise SandboxError("no concrete selector remains")
    return selectors, unsupported_specs


def tracked_test_inventory(
    root: str | Path,
    overlay_paths: list[str] | tuple[str, ...] = (),
) -> list[str]:
    """Return deterministic inventory of tracked test files and valid overlays."""
    if isinstance(root, str):
        if not root.strip():
            raise SandboxError("root must be a nonempty path")
        root = Path(root)
    elif not isinstance(root, Path):
        raise SandboxError(f"root must be a str or Path, got {type(root).__name__}")

    if not isinstance(overlay_paths, (list, tuple)):
        raise SandboxError("overlay_paths must be a list or tuple")
    if len(overlay_paths) > MAX_OVERLAYS:
        raise SandboxError("overlay_paths exceeds bounded size")

    overlay_tests: list[str] = []
    for p in overlay_paths:
        if not isinstance(p, str) or not p.strip():
            raise SandboxError("overlay path must be a nonempty string")
        _validate_overlay_path(p)
        if PATH_PATTERN.fullmatch(p):
            overlay_tests.append(p)

    tracked = source_files(root, [])
    tests = {p for p in tracked if PATH_PATTERN.fullmatch(p)}
    tests.update(overlay_tests)
    return sorted(tests)
