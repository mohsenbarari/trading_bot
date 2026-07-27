from __future__ import annotations

from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest

from scripts.build_three_site_human_approval_subject import (
    ApprovalSubjectError,
    main,
    migration_subject,
)
from tests.test_three_site_staging_signed_inventory import _inventory


class BuildThreeSiteHumanApprovalSubjectTests(unittest.TestCase):
    def test_generic_builder_refuses_unvalidated_migration_subjects(self):
        with self.assertRaisesRegex(
            ApprovalSubjectError, "full evidence validation"
        ):
            migration_subject(Path("/root-only/unsafe-migration-plan.json"))

    def test_generic_cli_cannot_publish_a_migration_subject(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "subject.json"
            with redirect_stdout(io.StringIO()):
                result = main(
                    [
                        "migration",
                        "--artifact",
                        str(Path(directory) / "unsafe-plan.json"),
                        "--output",
                        str(output),
                    ]
                )
            self.assertEqual(result, 1)
            self.assertFalse(output.exists())

    def test_inventory_subject_input_is_owner_only_and_output_never_overwrites(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            root.chmod(0o700)
            inventory = root / "inventory.json"
            inventory.write_text(
                json.dumps(_inventory(), sort_keys=True),
                encoding="utf-8",
            )
            inventory.chmod(0o600)
            output = root / "subject.json"
            argv = [
                "inventory",
                "--artifact",
                str(inventory),
                "--output",
                str(output),
            ]
            with redirect_stdout(io.StringIO()):
                self.assertEqual(main(argv), 0)
            original = output.read_bytes()
            with redirect_stdout(io.StringIO()):
                self.assertEqual(main(argv), 1)
            self.assertEqual(output.read_bytes(), original)

            duplicate = root / "duplicate.json"
            duplicate.write_text('{"schema":"one","schema":"two"}\n', encoding="utf-8")
            duplicate.chmod(0o600)
            with redirect_stdout(io.StringIO()):
                self.assertEqual(
                    main(
                        [
                            "inventory",
                            "--artifact",
                            str(duplicate),
                            "--output",
                            str(root / "duplicate-subject.json"),
                        ]
                    ),
                    1,
                )


if __name__ == "__main__":
    unittest.main()
