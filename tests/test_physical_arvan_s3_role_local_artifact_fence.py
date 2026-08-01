"""Static fence: enabled Full-Matrix paths cannot reach paired S3 APIs.

The retired paired loaders/factories may remain as historical, default-off
compatibility code while migration evidence is retained.  They must never be
imported, named, or instantiated by the one-role artifacts, their binder, or
an enabled normal/reverse handoff runtime.
"""

from __future__ import annotations

import ast
from pathlib import Path
import unittest


_ROOT = Path(__file__).resolve().parents[1]
_PAIRED_MODULES = frozenset(
    {
        "core.physical_arvan_s3_separated_credential_loader",
        "core.physical_arvan_s3_failback_separated_credential_loader",
        "core.physical_arvan_s3_separated_client_factory",
        "core.physical_arvan_s3_failback_separated_client_factory",
    }
)
_PAIRED_SYMBOLS = frozenset(
    {
        "RootOwnedArvanS3SeparatedCredentialLoaderConfig",
        "RootOwnedArvanS3FailbackSeparatedCredentialLoaderConfig",
        "RootOwnedArvanS3SeparatedClientFactory",
        "RootOwnedArvanS3FailbackSeparatedClientFactory",
        "load_root_owned_arvan_s3_separated_credential_pair",
        "load_root_owned_arvan_s3_failback_separated_credential_pair",
        "project_root_owned_arvan_s3_immutability_probe_credentials",
        "project_root_owned_arvan_s3_failback_separated_credentials",
    }
)
_ENABLED_PATHS = (
    "core/physical_arvan_s3_fi_publisher_role_factory.py",
    "core/physical_arvan_s3_ir_receiver_role_loader.py",
    "core/physical_arvan_s3_ir_publisher_failback_role_factory.py",
    "core/physical_arvan_s3_fi_receiver_failback_role_factory.py",
    "core/physical_arvan_s3_four_role_preflight_binding.py",
    "core/physical_arvan_s3_four_role_live_iam_evidence.py",
    "core/physical_arvan_s3_four_role_live_iam_preflight_gate.py",
    "core/physical_arvan_s3_four_role_live_iam_witness_ledger_runtime.py",
    "core/physical_arvan_s3_four_role_live_iam_durable_admission_bridge.py",
    "core/physical_ir_to_fi_object_storage_failback_preflight.py",
    "core/physical_wa_fi_postgres_object_storage_handoff_runtime.py",
    "core/physical_wa_fi_postgres_failback_materialization_runtime.py",
    "core/physical_wa_ir_postgres_recovery_pull_runtime.py",
    "core/physical_wa_ir_postgres_failback_capture_bridge.py",
    "core/physical_wa_ir_postgres_failback_handoff_runtime.py",
    "core/physical_wa_fi_postgres_failback_pull_runtime.py",
    "core/dedicated_host_preflight_fi_request_provisioning_runtime.py",
    "core/dedicated_host_preflight_ir_request_provisioning_runtime.py",
)


def _tree(path: str) -> ast.Module:
    return ast.parse((_ROOT / path).read_text(encoding="utf-8"), filename=path)


def _imported_modules(tree: ast.AST) -> set[str]:
    result: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            result.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            result.add(node.module)
            if node.module == "core":
                result.update("core." + alias.name for alias in node.names)
    return result


def _referenced_symbols(tree: ast.AST) -> set[str]:
    result: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            result.add(node.id)
        elif isinstance(node, ast.Attribute):
            result.add(node.attr)
    return result


class PhysicalArvanS3RoleLocalArtifactFenceTests(unittest.TestCase):
    def test_enabled_paths_have_no_paired_import_or_symbol_reference(self) -> None:
        for relative in _ENABLED_PATHS:
            with self.subTest(path=relative):
                tree = _tree(relative)
                self.assertFalse(_imported_modules(tree) & _PAIRED_MODULES)
                self.assertFalse(_referenced_symbols(tree) & _PAIRED_SYMBOLS)

    def test_one_role_artifacts_depend_on_neutral_reader_and_route_policy(self) -> None:
        for relative in (
            "core/physical_arvan_s3_fi_publisher_role_factory.py",
            "core/physical_arvan_s3_ir_receiver_role_loader.py",
            "core/physical_arvan_s3_ir_publisher_failback_role_factory.py",
            "core/physical_arvan_s3_fi_receiver_failback_role_factory.py",
        ):
            with self.subTest(path=relative):
                imported = _imported_modules(_tree(relative))
                self.assertIn("core.physical_arvan_s3_role_local_credential_reader", imported)
                self.assertIn("core.physical_arvan_s3_role_local_route_policy", imported)

    def test_binder_accepts_only_neutral_route_policy_surface(self) -> None:
        tree = _tree("core/physical_arvan_s3_four_role_preflight_binding.py")
        imported = _imported_modules(tree)
        self.assertIn("core.physical_arvan_s3_role_local_route_policy", imported)
        self.assertNotIn("credential_loader_config", _referenced_symbols(tree))

    def test_enabled_reverse_preflight_accepts_only_durable_admission(self) -> None:
        tree = _tree("core/physical_ir_to_fi_object_storage_failback_preflight.py")
        imported = _imported_modules(tree)
        symbols = _referenced_symbols(tree)
        self.assertIn(
            "core.physical_arvan_s3_four_role_live_iam_durable_admission_bridge",
            imported,
        )
        self.assertNotIn(
            "core.physical_arvan_s3_four_role_live_iam_preflight_gate",
            imported,
        )
        self.assertNotIn("four_role_live_iam_gate", symbols)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
