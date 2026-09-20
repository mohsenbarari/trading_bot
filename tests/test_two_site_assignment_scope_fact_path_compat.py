"""Hermetic unit tests for _task_path_outside_scope_fact compatibility helper."""

from __future__ import annotations

import unittest

from scripts.two_site_assignment_scope import (
    AssignmentFact,
    TASK_PATH_OUTSIDE_SCOPE,
    _task_path_outside_scope_fact,
    _validate_path_or_pattern,
)


class TestTaskPathOutsideScopeFactCompat(unittest.TestCase):
    """Verify total mapping from concrete task-valid path to TASK_PATH_OUTSIDE_SCOPE fact."""

    def test_canonical_normal_path_retained(self) -> None:
        path = "src/workflow/task_runner.py"
        validated = _validate_path_or_pattern(path, is_pattern=False)
        self.assertEqual(validated, path)

        fact = _task_path_outside_scope_fact(path)
        self.assertIsInstance(fact, AssignmentFact)
        self.assertEqual(fact.code, TASK_PATH_OUTSIDE_SCOPE)
        self.assertEqual(fact.path, path)

    def test_credential_token_path_safe_fallback(self) -> None:
        path = "outside/token.py"
        validated = _validate_path_or_pattern(path, is_pattern=False)
        self.assertEqual(validated, path)

        with self.assertRaises(ValueError):
            AssignmentFact(TASK_PATH_OUTSIDE_SCOPE, path)

        fact = _task_path_outside_scope_fact(path)
        self.assertIsInstance(fact, AssignmentFact)
        self.assertEqual(fact.code, TASK_PATH_OUTSIDE_SCOPE)
        self.assertIsNone(fact.path)

    def test_at_sign_path_safe_fallback(self) -> None:
        path = "packages/@scope/module.py"
        validated = _validate_path_or_pattern(path, is_pattern=False)
        self.assertEqual(validated, path)

        with self.assertRaises(ValueError):
            AssignmentFact(TASK_PATH_OUTSIDE_SCOPE, path)

        fact = _task_path_outside_scope_fact(path)
        self.assertIsInstance(fact, AssignmentFact)
        self.assertEqual(fact.code, TASK_PATH_OUTSIDE_SCOPE)
        self.assertIsNone(fact.path)

    def test_drive_prefix_path_safe_fallback(self) -> None:
        path = "C:components/view.py"
        validated = _validate_path_or_pattern(path, is_pattern=False)
        self.assertEqual(validated, path)

        with self.assertRaises(ValueError):
            AssignmentFact(TASK_PATH_OUTSIDE_SCOPE, path)

        fact = _task_path_outside_scope_fact(path)
        self.assertIsInstance(fact, AssignmentFact)
        self.assertEqual(fact.code, TASK_PATH_OUTSIDE_SCOPE)
        self.assertIsNone(fact.path)

    def test_multibyte_utf8_byte_limit_safe_fallback(self) -> None:
        # Construct path with Persian characters and ZWNJ U+200C:
        # Each repeat is 3 characters (600 chars) and 7 UTF-8 bytes (1400 bytes).
        # Total path has 611 chars (<= 1024 char limit) but 1411 bytes (> 1024 byte limit).
        unit = "\u0628" + chr(0x200C) + "\u06af"
        path = "data/" + (unit * 200) + ".txt"
        self.assertLessEqual(len(path), 1024)
        self.assertGreater(len(path.encode("utf-8")), 1024)

        validated = _validate_path_or_pattern(path, is_pattern=False)
        self.assertEqual(validated, path)

        with self.assertRaises(ValueError):
            AssignmentFact(TASK_PATH_OUTSIDE_SCOPE, path)

        fact = _task_path_outside_scope_fact(path)
        self.assertIsInstance(fact, AssignmentFact)
        self.assertEqual(fact.code, TASK_PATH_OUTSIDE_SCOPE)
        self.assertIsNone(fact.path)

    def test_fact_immutability(self) -> None:
        fact = _task_path_outside_scope_fact("src/app.py")
        self.assertEqual(fact.code, TASK_PATH_OUTSIDE_SCOPE)
        self.assertEqual(fact.path, "src/app.py")
        with self.assertRaises((AttributeError, TypeError)):
            fact.path = "other.py"  # type: ignore[misc]


if __name__ == "__main__":
    unittest.main()
