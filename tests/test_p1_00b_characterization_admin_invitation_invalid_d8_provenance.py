"""Hermetic provenance test for d8ca931a invalid invitation input characterization."""

from __future__ import annotations

import ast
import hashlib
from pathlib import Path
import unittest

TARGET_FILE_NAME = "test_p1_00b_characterization_admin_invitation_inputs_batch.py"
HISTORICAL_D8_SHA256 = (
    "283e2e87da3049f4c74a1c16914bf129c2384fd9b204e8eb8412cdc22a56c091"
)
EXPECTED_INVALID_QUEUE_SELECTORS = (
    "test_account_name_invalid_queue_suppresses_stale_prompt_delete",
    "test_mobile_invalid_queue_suppresses_stale_prompt_delete",
)
EXCLUDED_VALID_QUEUE_METHODS = (
    "test_account_name_valid_queue_suppresses_stale_delete_and_advances",
    "test_mobile_valid_queue_suppresses_stale_delete_and_advances",
)


def reconstruct_d8_source(source: str) -> str:
    """Reconstruct d8ca931a source by reversing the later 67cd61b9 additions."""
    lines_to_remove = (
        "import bot.handlers.admin as admin_handlers\n",
        "from bot.keyboards import get_invitable_roles_for_admin\n",
        "from core.enums import UserRole\n",
    )

    last_pos = -1
    for line in lines_to_remove:
        count = source.count(line)
        if count != 1:
            raise AssertionError(
                f"Source drift: expected exactly 1 occurrence of {line!r}, found {count}"
            )
        pos = source.index(line)
        if pos <= last_pos:
            raise AssertionError(
                f"Source drift: line {line!r} occurred out of expected order"
            )
        last_pos = pos

    reconstructed = source
    for line in lines_to_remove:
        reconstructed = reconstructed.replace(line, "", 1)

    start_marker = (
        "    async def test_account_name_valid_queue_suppresses_stale_delete_and_advances("
    )
    final_block = '\n\nif __name__ == "__main__":'

    if reconstructed.count(start_marker) != 1:
        raise AssertionError(
            f"Source drift: expected exactly 1 occurrence of start marker {start_marker!r}"
        )
    if reconstructed.count(final_block) != 1:
        raise AssertionError(
            f"Source drift: expected exactly 1 occurrence of final block {final_block!r}"
        )

    start_idx = reconstructed.index(start_marker)
    final_idx = reconstructed.index(final_block)
    if start_idx >= final_idx:
        raise AssertionError(
            "Source drift: start marker does not precede final block"
        )

    # Remove the two valid queue methods and retain exactly one leading newline
    # of the final block to preserve the original blank-line layout.
    tail = reconstructed[final_idx + 1 :]
    return reconstructed[:start_idx] + tail


class InvitationInvalidD8ProvenanceTests(unittest.TestCase):
    def test_historical_invalid_queue_source_and_selectors(self) -> None:
        source_path = Path(__file__).resolve().parent / TARGET_FILE_NAME
        self.assertTrue(
            source_path.exists(),
            f"Target source file not found at {source_path}",
        )
        source = source_path.read_text(encoding="utf-8")

        reconstructed_source = reconstruct_d8_source(source)

        actual_sha256 = hashlib.sha256(
            reconstructed_source.encode("utf-8")
        ).hexdigest()
        self.assertEqual(
            actual_sha256,
            HISTORICAL_D8_SHA256,
            "Reconstructed source SHA-256 does not match authoritative d8ca931a hash",
        )

        tree = ast.parse(reconstructed_source)
        class_node: ast.ClassDef | None = None
        for node in tree.body:
            if (
                isinstance(node, ast.ClassDef)
                and node.name == "AdminInvitationInputsCharacterizationTests"
            ):
                class_node = node
                break

        self.assertIsNotNone(
            class_node,
            "Class AdminInvitationInputsCharacterizationTests not found in reconstructed AST",
        )
        assert class_node is not None

        async_methods = [
            n.name
            for n in class_node.body
            if isinstance(n, ast.AsyncFunctionDef)
        ]

        for expected_method in EXPECTED_INVALID_QUEUE_SELECTORS:
            self.assertIn(
                expected_method,
                async_methods,
                f"Missing expected invalid queue selector {expected_method}",
            )

        for excluded_method in EXCLUDED_VALID_QUEUE_METHODS:
            self.assertNotIn(
                excluded_method,
                async_methods,
                f"Excluded method {excluded_method} unexpectedly present in reconstructed AST",
            )


if __name__ == "__main__":
    unittest.main()
