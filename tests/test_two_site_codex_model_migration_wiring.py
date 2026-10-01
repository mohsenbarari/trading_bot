#!/usr/bin/env python3
"""Tests for Codex model migration wiring replacements."""
from __future__ import annotations

import ast
from collections import defaultdict
import os
from pathlib import Path
import stat
import tempfile
import unittest

try:
    from scripts.two_site_codex_model_migration_wiring import WIRING_REPLACEMENTS
except ModuleNotFoundError:
    from two_site_codex_model_migration_wiring import WIRING_REPLACEMENTS

MAX_READ_BYTES = 200_000

EXPECTED_PATHS = frozenset(
    {
        "scripts/two_site_codex_autopilot.py",
        "docs/refactor/two-server/templates/CODEX_AUTOPILOT_PROMPT.md",
        "docs/refactor/two-server/PROPOSAL_AUTOMATION.md",
        "tests/test_two_site_autopilot_stable_cli.py",
        "tests/test_two_site_zero_echo_recipe.py",
    }
)

EXPECTED_OLD_CLI_VERSION = "0.156.1"
EXPECTED_NEW_CLI_VERSION = "0.159.2"
EXPECTED_OLD_CLI_SHA = "0b2e9301d6100dddda3b9d5c80ebaeaa3a2f1962388f2f36f6b96a9f08b1f33f"
EXPECTED_NEW_CLI_SHA = "1748767b230ebfc3d4ab7e4e254920d0c0ad9691fd8c11f190e7d44511a4a92e"
EXPECTED_OLD_HOST_SHA = "45ba654b0e145406f3316be4729b886980ce811aa2e3b6ed4e033dd41ab524f1"
EXPECTED_NEW_HOST_SHA = "5b2c075ac2380fa04d76d7313fbc044d29c8d0a0d0b9138415acd4610211ca03"
EXPECTED_OLD_PKG_SHA = "6cf97f0c8999775e81c038429a31f28c406e077ecdbece52a9700e244bd2458f"
EXPECTED_NEW_PKG_SHA = "6e812d1eecc3ffa0b339df730a53137d8ec1d1e2617d09c23878bd25e1a56845"
EXPECTED_OLD_TOOL_PATH = ".local/tools/codex-0.156.1-0b2e9301d6100ddd/codex"
EXPECTED_NEW_TOOL_PATH = ".local/tools/codex-0.159.2-1748767b230ebfc3/codex"


def safe_read_file(path: Path, max_bytes: int = MAX_READ_BYTES) -> str:
    """Safely read a regular, single-link file up to max_bytes with UTF-8 decoding."""
    if path.is_symlink():
        raise ValueError(f"Path is a symlink: {path}")
    st = os.stat(path, follow_symlinks=False)
    if not stat.S_ISREG(st.st_mode):
        raise ValueError(f"Path is not a regular file: {path}")
    if st.st_nlink != 1:
        raise ValueError(f"Path has multiple hard links ({st.st_nlink}): {path}")
    if st.st_size > max_bytes:
        raise ValueError(f"File size {st.st_size} exceeds bound {max_bytes}: {path}")
    with open(path, "rb") as f:
        raw = f.read(max_bytes + 1)
    if len(raw) > max_bytes:
        raise ValueError(f"Read bytes exceed bound {max_bytes}: {path}")
    return raw.decode("utf-8")


def classify_state_and_validate(
    rel_path: str,
    content: str,
    replacements: list[tuple[str, str, str]],
) -> str:
    """Classify content as PRE_MIGRATION or POST_MIGRATION and validate syntax."""
    counts = []
    for path, old, new in replacements:
        if path != rel_path:
            raise ValueError(f"Replacement path {path} does not match {rel_path}")
        old_count = content.count(old)
        new_count = content.count(new)
        counts.append((old_count, new_count))

    is_pre = all(oc == 1 and nc == 0 for oc, nc in counts)
    is_post = all(oc == 0 and nc == 1 for oc, nc in counts)

    if not is_pre and not is_post:
        raise ValueError(
            f"File {rel_path} has invalid/mixed anchor state: {counts}"
        )

    if is_pre:
        content.encode("utf-8")
        if rel_path.endswith(".py"):
            ast.parse(content, filename=rel_path)
        simulated = content
        for _, old, new in replacements:
            simulated = simulated.replace(old, new, 1)
        simulated.encode("utf-8")
        if rel_path.endswith(".py"):
            ast.parse(simulated, filename=rel_path)
        return "PRE_MIGRATION"
    else:
        content.encode("utf-8")
        if rel_path.endswith(".py"):
            ast.parse(content, filename=rel_path)
        reverted = content
        for _, old, new in replacements:
            reverted = reverted.replace(new, old, 1)
        reverted.encode("utf-8")
        if rel_path.endswith(".py"):
            ast.parse(reverted, filename=rel_path)
        return "POST_MIGRATION"


class TestWiringReplacementsContract(unittest.TestCase):
    """R1: Test immutable tuple contract, whitelist, and fact bindings."""

    def test_immutable_tuple_structure_and_count(self) -> None:
        self.assertIsInstance(WIRING_REPLACEMENTS, tuple)
        self.assertEqual(len(WIRING_REPLACEMENTS), 17)
        for entry in WIRING_REPLACEMENTS:
            self.assertIsInstance(entry, tuple)
            self.assertEqual(len(entry), 3)
            path, old, new = entry
            self.assertIsInstance(path, str)
            self.assertIsInstance(old, str)
            self.assertIsInstance(new, str)

    def test_exact_five_path_whitelist_and_unique_anchors(self) -> None:
        observed_paths = set(path for path, _, _ in WIRING_REPLACEMENTS)
        self.assertEqual(observed_paths, EXPECTED_PATHS)
        self.assertEqual(len(observed_paths), 5)

        pairs = [(path, old) for path, old, _ in WIRING_REPLACEMENTS]
        self.assertEqual(len(pairs), 17)
        self.assertEqual(len(set(pairs)), 17)

    def test_no_empty_or_noop_snippets(self) -> None:
        for path, old, new in WIRING_REPLACEMENTS:
            self.assertTrue(bool(old.strip()), f"Empty old snippet in {path}")
            self.assertTrue(bool(new.strip()), f"Empty new snippet in {path}")
            self.assertNotEqual(old, new, f"No-op snippet in {path}")

    def test_model_cli_hash_and_effort_facts(self) -> None:
        joined_old = "\n".join(old for _, old, _ in WIRING_REPLACEMENTS)
        joined_new = "\n".join(new for _, _, new in WIRING_REPLACEMENTS)

        # Model bindings
        self.assertIn("gpt-6-sol", joined_old)
        self.assertIn("gpt-6.1-sol", joined_new)
        self.assertNotIn("gpt-6.1-sol", joined_old)

        # Reasoning effort bindings
        for _, old, new in WIRING_REPLACEMENTS:
            if "gpt-6-sol/xhigh" in old:
                self.assertIn("gpt-6.1-sol/xhigh", new)
            if "با `xhigh`" in old:
                self.assertIn("با `xhigh`", new)

        # CLI version and hashes
        self.assertIn(EXPECTED_OLD_CLI_VERSION, joined_old)
        self.assertIn(EXPECTED_NEW_CLI_VERSION, joined_new)
        self.assertIn(EXPECTED_OLD_CLI_SHA, joined_old)
        self.assertIn(EXPECTED_NEW_CLI_SHA, joined_new)
        self.assertIn(EXPECTED_OLD_HOST_SHA, joined_old)
        self.assertIn(EXPECTED_NEW_HOST_SHA, joined_new)
        self.assertIn(EXPECTED_OLD_PKG_SHA, joined_old)
        self.assertIn(EXPECTED_NEW_PKG_SHA, joined_new)

        # Standalone tool paths
        self.assertIn(EXPECTED_OLD_TOOL_PATH, joined_old)
        self.assertIn(EXPECTED_NEW_TOOL_PATH, joined_new)

        # Prompt headroom bounds
        self.assertIn("prompt_bytes < 17000", joined_old)
        self.assertIn("prompt_bytes < 18000", joined_new)

    def test_no_unrelated_model_or_effort_or_runtime_changes(self) -> None:
        forbidden_models = {"gpt-4", "gpt-5", "claude", "gemini", "sonnet"}
        forbidden_efforts = {"xlow", "low", "medium", "high"}
        for path, old, new in WIRING_REPLACEMENTS:
            for fm in forbidden_models:
                self.assertNotIn(fm, new.lower())
            for fe in forbidden_efforts:
                if f"/{fe}" in new or f"`{fe}`" in new:
                    self.fail(f"Unrelated effort {fe} introduced in {path}: {new}")


class TestWiringSourceState(unittest.TestCase):
    """R2: Test pre/post-migration state checks, AST validity, and negative cases."""

    def setUp(self) -> None:
        self.repo_root = Path(__file__).resolve().parent.parent
        self.replacements_by_path: dict[str, list[tuple[str, str, str]]] = defaultdict(list)
        for entry in WIRING_REPLACEMENTS:
            self.replacements_by_path[entry[0]].append(entry)

    def test_tracked_repository_checkout_state(self) -> None:
        states = set()
        for rel_path, reps in self.replacements_by_path.items():
            full_path = self.repo_root / rel_path
            self.assertTrue(full_path.is_file(), f"Target file missing from repository: {rel_path}")
            content = safe_read_file(full_path)
            state = classify_state_and_validate(rel_path, content, reps)
            states.add(state)

        # All files must agree: either 100% pre-migration or 100% post-migration
        self.assertEqual(len(states), 1, f"Repository has mixed file states: {states}")
        self.assertIn(list(states)[0], {"PRE_MIGRATION", "POST_MIGRATION"})

    def test_simulated_post_migration_in_temporary_copies(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_root = Path(tmp_dir)
            for rel_path, reps in self.replacements_by_path.items():
                orig_path = self.repo_root / rel_path
                orig_content = safe_read_file(orig_path)
                state = classify_state_and_validate(rel_path, orig_content, reps)

                if state == "PRE_MIGRATION":
                    migrated = orig_content
                    for _, old, new in reps:
                        migrated = migrated.replace(old, new, 1)
                    dest_file = tmp_root / Path(rel_path).name
                    dest_file.write_text(migrated, encoding="utf-8")
                    content = safe_read_file(dest_file)
                    post_state = classify_state_and_validate(rel_path, content, reps)
                    self.assertEqual(post_state, "POST_MIGRATION")
                else:
                    reverted = orig_content
                    for _, old, new in reps:
                        reverted = reverted.replace(new, old, 1)
                    dest_file = tmp_root / Path(rel_path).name
                    dest_file.write_text(reverted, encoding="utf-8")
                    content = safe_read_file(dest_file)
                    pre_state = classify_state_and_validate(rel_path, content, reps)
                    self.assertEqual(pre_state, "PRE_MIGRATION")

    def test_negative_cases_reject_duplicate_missing_and_mixed(self) -> None:
        reps = self.replacements_by_path["docs/refactor/two-server/PROPOSAL_AUTOMATION.md"]
        self.assertGreaterEqual(len(reps), 2)
        old0, new0 = reps[0][1], reps[0][2]
        old1, new1 = reps[1][1], reps[1][2]

        # Case 1: Duplicate anchor (pre-migration)
        content_dup_pre = f"{old0}\n{old0}\n{old1}\n"
        with self.assertRaises(ValueError):
            classify_state_and_validate(reps[0][0], content_dup_pre, reps)

        # Case 2: Duplicate anchor (post-migration)
        content_dup_post = f"{new0}\n{new0}\n{new1}\n"
        with self.assertRaises(ValueError):
            classify_state_and_validate(reps[0][0], content_dup_post, reps)

        # Case 3: Missing anchor
        content_missing = "No matching anchor here.\n"
        with self.assertRaises(ValueError):
            classify_state_and_validate(reps[0][0], content_missing, reps)

        # Case 4: Mixed anchor (first new, second old)
        content_mixed1 = f"{new0}\n{old1}\n"
        with self.assertRaises(ValueError):
            classify_state_and_validate(reps[0][0], content_mixed1, reps)

        # Case 5: Mixed anchor (first old, second new)
        content_mixed2 = f"{old0}\n{new1}\n"
        with self.assertRaises(ValueError):
            classify_state_and_validate(reps[0][0], content_mixed2, reps)

        # Case 6: Third state pre-migration (anchor substituted with third-party model)
        third_pre = old0.replace("gpt-6-sol", "gpt-7-sol")
        content_third_pre = f"{third_pre}\n{old1}\n"
        with self.assertRaises(ValueError):
            classify_state_and_validate(reps[0][0], content_third_pre, reps)

        # Case 7: Third state post-migration (anchor substituted with third-party model)
        third_post = new0.replace("gpt-6.1-sol", "gpt-7-sol")
        content_third_post = f"{third_post}\n{new1}\n"
        with self.assertRaises(ValueError):
            classify_state_and_validate(reps[0][0], content_third_post, reps)

        # Case 8: Path mismatch
        with self.assertRaises(ValueError):
            classify_state_and_validate("unmatched/path.txt", content_dup_pre, reps)

    def test_negative_cases_reject_syntax_error(self) -> None:
        reps = self.replacements_by_path["scripts/two_site_codex_autopilot.py"]
        # Valid anchor counts for all entries, but malformed Python syntax appended
        malformed_python = "\n".join(old for _, old, _ in reps) + "\ndef broken_syntax(:\n"
        with self.assertRaises(SyntaxError):
            classify_state_and_validate(reps[0][0], malformed_python, reps)

    def test_safe_read_file_guards(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            base = Path(tmp_dir)
            regular = base / "regular.txt"
            regular.write_text("valid content", encoding="utf-8")

            # 1. Successful read
            self.assertEqual(safe_read_file(regular), "valid content")

            # 2. Oversize rejection
            oversize = base / "oversize.txt"
            oversize.write_bytes(b"x" * (MAX_READ_BYTES + 1))
            with self.assertRaises(ValueError):
                safe_read_file(oversize, max_bytes=MAX_READ_BYTES)

            # 3. Symlink rejection
            symlink = base / "symlink.txt"
            symlink.symlink_to(regular)
            with self.assertRaises(ValueError):
                safe_read_file(symlink)

            # 4. Hard link rejection
            hardlink = base / "hardlink.txt"
            os.link(regular, hardlink)
            with self.assertRaises(ValueError):
                safe_read_file(regular)


if __name__ == "__main__":
    unittest.main()
