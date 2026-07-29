from __future__ import annotations

from datetime import date, datetime, time, timezone
from decimal import Decimal
from enum import Enum
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]


class ProductionShadowParityImportTests(unittest.TestCase):
    @staticmethod
    def _hash(label: str) -> str:
        return hashlib.sha256(label.encode("ascii")).hexdigest()

    def _snapshot(
        self,
        records: list[dict[str, str]] | None = None,
        *,
        table_name: str = "offers",
        truncated: bool = False,
        duplicate_identity_count: int = 0,
    ) -> dict[str, object]:
        from core import production_shadow_parity as parity

        if records is None:
            records = [
                {
                    "identity_hash": self._hash("identity"),
                    "business_hash": self._hash("business"),
                    "local_only_hash": self._hash("local"),
                    "volatile_hash": self._hash("volatile"),
                }
            ]
        entries = [
            {
                "identity_hash": record["identity_hash"],
                "business_hash": record["business_hash"],
            }
            for record in records
        ]
        table = {
            "row_count": len(records),
            "truncated": truncated,
            "duplicate_identity_count": duplicate_identity_count,
            "records": records,
            "records_hash": parity._hash_payload(records),
            "business_records_hash": parity._hash_payload(entries),
        }
        return {"tables": {table_name: table}}

    def _isolated_python(self, code: str, *, cwd: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-I", "-S", "-B", "-c", code],
            cwd=cwd,
            env={
                "PATH": os.defpath,
                "HOME": "/nonexistent",
                "LANG": "C.UTF-8",
                "LC_ALL": "C.UTF-8",
                "PYTHONDONTWRITEBYTECODE": "1",
            },
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=15,
        )

    def test_pure_parity_module_imports_without_site_packages(self) -> None:
        code = (
            "import sys; "
            f"sys.path.insert(0, {str(REPO_ROOT)!r}); "
            "import core.production_shadow_parity as parity; "
            "assert parity.SYNC_PARITY_SCHEMA_VERSION == 1; "
            "print('ok')"
        )
        with tempfile.TemporaryDirectory() as directory:
            completed = self._isolated_python(code, cwd=Path(directory))

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout, "ok\n")

    def test_source_set_imports_do_not_require_sqlalchemy_or_pyyaml(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stubs = root / "stubs"
            self._write_cryptography_stubs(stubs)
            code = "\n".join(
                (
                    "import builtins, sys",
                    f"sys.path[:0] = [{str(stubs)!r}, {str(REPO_ROOT)!r}]",
                    "real_import = builtins.__import__",
                    "forbidden = ('sqlalchemy', 'yaml', 'models', "
                    "'scripts.orchestrate_production_shadow_prepared_clone_inventory')",
                    "def guarded(name, globals=None, locals=None, fromlist=(), level=0):",
                    "    if any(name == item or name.startswith(item + '.') for item in forbidden):",
                    "        raise AssertionError('forbidden import: ' + name)",
                    "    return real_import(name, globals, locals, fromlist, level)",
                    "builtins.__import__ = guarded",
                    "import scripts.produce_production_shadow_convergence_source_set",
                    "import scripts.orchestrate_production_shadow_convergence_gate",
                    "assert 'scripts.orchestrate_production_shadow_prepared_clone_inventory' not in sys.modules",
                    "print('ok')",
                )
            )
            completed = self._isolated_python(code, cwd=root)

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout, "ok\n")

    def test_sync_parity_reexports_pure_snapshot_functions(self) -> None:
        from core import production_shadow_parity
        from core import sync_parity

        self.assertIs(
            sync_parity.business_snapshot_fingerprint,
            production_shadow_parity.business_snapshot_fingerprint,
        )
        self.assertIs(
            sync_parity.compare_parity_snapshots,
            production_shadow_parity.compare_parity_snapshots,
        )

    def test_canonical_value_handles_supported_scalar_and_nested_types(self) -> None:
        from core import production_shadow_parity as parity

        class Marker(Enum):
            VALUE = "enum-value"

        class Wrapped:
            value = Marker.VALUE

        value = {
            "enum": Wrapped(),
            "datetime": datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc),
            "date": date(2026, 7, 29),
            "time": time(12, 0, 1),
            "decimal": Decimal("12.50"),
            "nested": ("item", [Decimal("2")]),
        }

        self.assertEqual(
            parity._canonical_value(value),
            {
                "enum": "enum-value",
                "datetime": "2026-07-29T12:00:00+00:00",
                "date": "2026-07-29",
                "time": "12:00:01",
                "decimal": "12.50",
                "nested": ["item", ["2"]],
            },
        )

    def test_business_fingerprint_rejects_malformed_or_incomplete_snapshots(self) -> None:
        from core import production_shadow_parity as parity

        with self.assertRaisesRegex(ValueError, "no tables"):
            parity.business_snapshot_fingerprint(None)  # type: ignore[arg-type]
        with self.assertRaisesRegex(ValueError, "no tables"):
            parity.business_snapshot_fingerprint({"tables": {}})

        incomplete = self._snapshot()
        table = incomplete["tables"]["offers"]  # type: ignore[index]
        table["truncated"] = True
        with self.assertRaisesRegex(ValueError, "incomplete"):
            parity.business_snapshot_fingerprint(incomplete)  # type: ignore[arg-type]

        invalid_records = self._snapshot()
        invalid_records["tables"]["offers"]["records"] = {}  # type: ignore[index]
        with self.assertRaisesRegex(ValueError, "records are invalid"):
            parity.business_snapshot_fingerprint(invalid_records)  # type: ignore[arg-type]

        invalid_count = self._snapshot()
        invalid_count["tables"]["offers"]["row_count"] = 2  # type: ignore[index]
        with self.assertRaisesRegex(ValueError, "row count"):
            parity.business_snapshot_fingerprint(invalid_count)  # type: ignore[arg-type]

        duplicate_count = self._snapshot()
        duplicate_count["tables"]["offers"]["duplicate_identity_count"] = 1  # type: ignore[index]
        with self.assertRaisesRegex(ValueError, "duplicate identities"):
            parity.business_snapshot_fingerprint(duplicate_count)  # type: ignore[arg-type]

        invalid_record = self._snapshot()
        invalid_record["tables"]["offers"]["records"] = [None]  # type: ignore[index]
        with self.assertRaisesRegex(ValueError, "record is invalid"):
            parity.business_snapshot_fingerprint(invalid_record)  # type: ignore[arg-type]

        invalid_hash = self._snapshot()
        invalid_hash["tables"]["offers"]["records"][0]["identity_hash"] = "invalid"  # type: ignore[index]
        with self.assertRaisesRegex(ValueError, "record hash"):
            parity.business_snapshot_fingerprint(invalid_hash)  # type: ignore[arg-type]

        duplicated = self._snapshot()
        duplicated_record = dict(duplicated["tables"]["offers"]["records"][0])  # type: ignore[index]
        duplicated["tables"]["offers"]["records"].append(duplicated_record)  # type: ignore[index]
        duplicated["tables"]["offers"]["row_count"] = 2  # type: ignore[index]
        duplicated["tables"]["offers"]["records_hash"] = parity._hash_payload(  # type: ignore[index]
            duplicated["tables"]["offers"]["records"]  # type: ignore[index]
        )
        duplicated["tables"]["offers"]["business_records_hash"] = parity._hash_payload(  # type: ignore[index]
            [
                {
                    "identity_hash": record["identity_hash"],
                    "business_hash": record["business_hash"],
                }
                for record in duplicated["tables"]["offers"]["records"]  # type: ignore[index]
            ]
        )
        with self.assertRaisesRegex(ValueError, "duplicate identities"):
            parity.business_snapshot_fingerprint(duplicated)  # type: ignore[arg-type]

        invalid_record_fingerprint = self._snapshot()
        invalid_record_fingerprint["tables"]["offers"]["records_hash"] = "0" * 64  # type: ignore[index]
        with self.assertRaisesRegex(ValueError, "record fingerprint"):
            parity.business_snapshot_fingerprint(invalid_record_fingerprint)  # type: ignore[arg-type]

        invalid_business_fingerprint = self._snapshot()
        invalid_business_fingerprint["tables"]["offers"]["business_records_hash"] = "0" * 64  # type: ignore[index]
        with self.assertRaisesRegex(ValueError, "business fingerprint"):
            parity.business_snapshot_fingerprint(invalid_business_fingerprint)  # type: ignore[arg-type]

        self.assertRegex(parity.business_snapshot_fingerprint(self._snapshot()), r"^[0-9a-f]{64}$")

    def test_comparison_and_record_helpers_cover_drift_categories(self) -> None:
        from core import production_shadow_parity as parity

        local = self._snapshot()
        peer = self._snapshot()
        self.assertEqual(parity.compare_parity_snapshots(local, peer)["status"], "ok")

        incomplete = self._snapshot(truncated=True)
        self.assertEqual(
            parity.compare_parity_snapshots(incomplete, peer)["status"], "incomplete"
        )

        duplicate = self._snapshot(duplicate_identity_count=1)
        self.assertEqual(
            parity.compare_parity_snapshots(duplicate, peer)["status"], "critical_drift"
        )

        missing = self._snapshot(records=[])
        self.assertEqual(
            parity.compare_parity_snapshots(missing, peer)["status"], "critical_drift"
        )

        record = dict(local["tables"]["offers"]["records"][0])  # type: ignore[index]
        business = dict(record)
        business["business_hash"] = self._hash("changed-business")
        self.assertEqual(
            parity.compare_parity_snapshots(local, self._snapshot([business]))["status"],
            "business_drift",
        )

        local_only = dict(record)
        local_only["local_only_hash"] = self._hash("changed-local")
        self.assertEqual(
            parity.compare_parity_snapshots(local, self._snapshot([local_only]))["status"],
            "non_business_difference",
        )

        volatile = dict(record)
        volatile["volatile_hash"] = self._hash("changed-volatile")
        self.assertEqual(
            parity.compare_parity_snapshots(local, self._snapshot([volatile]))["status"],
            "non_business_difference",
        )

        self.assertEqual(parity._records_by_identity({"records": None}), {})
        self.assertEqual(
            parity._records_by_identity(
                {"records": [None, {"identity_hash": ""}, {"identity_hash": "row"}]}
            ),
            {"row": {"identity_hash": "row"}},
        )
        self.assertEqual(parity._duplicate_identity_hashes({"records": None}), [])
        self.assertEqual(
            parity._duplicate_identity_hashes(
                {
                    "records": [
                        None,
                        {"identity_hash": ""},
                        {"identity_hash": "same"},
                        {"identity_hash": "same"},
                    ]
                }
            ),
            ["same"],
        )
        self.assertEqual(
            parity._duplicate_identity_count(
                {"duplicate_identity_count": "2", "records": []}
            ),
            2,
        )
        self.assertEqual(
            parity._duplicate_identity_count(
                {
                    "duplicate_identity_count": "invalid",
                    "records": [
                        None,
                        {"identity_hash": ""},
                        {"identity_hash": "same"},
                        {"identity_hash": "same"},
                    ],
                }
            ),
            1,
        )
        self.assertEqual(
            parity._duplicate_identity_count(
                {"duplicate_identity_count": "invalid", "records": None}
            ),
            0,
        )

    @staticmethod
    def _write_cryptography_stubs(root: Path) -> None:
        files = {
            "cryptography/__init__.py": "",
            "cryptography/exceptions.py": "class InvalidSignature(Exception):\n    pass\n",
            "cryptography/hazmat/__init__.py": "",
            "cryptography/hazmat/primitives/__init__.py": "",
            "cryptography/hazmat/primitives/asymmetric/__init__.py": "",
            "cryptography/hazmat/primitives/asymmetric/ed25519.py": (
                "class Ed25519PrivateKey:\n    pass\n\n"
                "class Ed25519PublicKey:\n    pass\n"
            ),
        }
        for relative, content in files.items():
            path = root / relative
            path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
