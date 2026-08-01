from __future__ import annotations

import ast
import copy
from datetime import datetime, timedelta, timezone
from pathlib import Path
import unittest

from scripts import production_shadow_convergence_observation_contract as CONTRACT
from scripts import production_shadow_convergence_observer_worker as WORKER


NOW = datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc)
CAMPAIGN_ID = "7fb08095-7a9e-4a92-9fa9-3f9a301b2944"
OPERATION_ID = "7fb08095-7a9e-4a92-9fa9-3f9a301b2945"
RELEASE_SHA = "1ddf277bc51ebe7c9b4d4d488c843efe90fc16e2"
TREE_SHA = "a" * 40


def _timestamp(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _request(*, role: str = "bot_fi") -> dict[str, object]:
    return WORKER.build_request(
        campaign_id=CAMPAIGN_ID,
        operation_id=OPERATION_ID,
        release_sha=RELEASE_SHA,
        release_tree_sha=TREE_SHA,
        manifest_sha256="7" * 64,
        runtime_target_binding_sha256=(
            "6" * 64 if role in WORKER.RUNTIME_SNAPSHOT_ROLES else None
        ),
        plan_sha256="b" * 64,
        approval_sha256="8" * 64,
        role=role,
        expected_host="127.0.0.1",
        phase_started_at=NOW - timedelta(seconds=10),
        worker_sha256="9" * 64,
        max_rows_per_table=100,
    )


def _attestation(request: dict[str, object]) -> dict[str, object]:
    proof: dict[str, object] = {
        "schema": CONTRACT.HOST_IDENTITY_PROOF_SCHEMA,
        "expected_host": request["expected_host"],
        "observed_host": request["expected_host"],
        "address_family": "inet",
        "interface": "eth0",
        "collector": "kernel-ip-json",
        "observed_at": _timestamp(NOW - timedelta(seconds=2)),
        "host_identity_proof_sha256": CONTRACT.ZERO_SHA256,
    }
    proof["host_identity_proof_sha256"] = CONTRACT._host_identity_proof_digest(proof)

    redacted_snapshot: dict[str, object] = {
        "schema": "redacted-parity-fixture-v1",
        "tables": {"offers": {"row_count": 0}},
    }
    database: dict[str, object] = {
        "table_set_sha256": "1" * 64,
        "business_fingerprint_sha256": "2" * 64,
        "row_count": 0,
        "table_count": 1,
        "redacted_snapshot_sha256": CONTRACT._sha256(redacted_snapshot),
        "database_state_sha256": CONTRACT.ZERO_SHA256,
    }
    database["database_state_sha256"] = CONTRACT._sha256(
        {
            key: value
            for key, value in database.items()
            if key != "database_state_sha256"
        }
    )
    dr: dict[str, object] = {
        "producer_epoch": 1,
        "source_streams": [],
        "destination_streams": [],
        "unresolved_conflict_count": 0,
        "dr_state_sha256": CONTRACT.ZERO_SHA256,
    }
    dr["dr_state_sha256"] = CONTRACT._sha256(
        {key: value for key, value in dr.items() if key != "dr_state_sha256"}
    )
    snapshot = {
        "captured_at": _timestamp(NOW - timedelta(seconds=1)),
        "database": database,
        "redacted_parity_snapshot": redacted_snapshot,
        "dr": dr,
    }
    document: dict[str, object] = {
        "schema": CONTRACT.ATTESTATION_SCHEMA,
        "status": "observed",
        "campaign_id": request["campaign_id"],
        "operation_id": request["operation_id"],
        "release_sha": request["release_sha"],
        "release_tree_sha": request["release_tree_sha"],
        "manifest_sha256": request["manifest_sha256"],
        "runtime_target_binding_sha256": request["runtime_target_binding_sha256"],
        "plan_sha256": request["plan_sha256"],
        "approval_sha256": request["approval_sha256"],
        "phase": request["phase"],
        "operation": request["operation"],
        "role": request["role"],
        "expected_host": request["expected_host"],
        "phase_started_at": request["phase_started_at"],
        "request_sha256": request["request_sha256"],
        "worker_sha256": request["worker_sha256"],
        "host_identity_proof": proof,
        "observed_at": _timestamp(NOW),
        "release_identity": {
            "release_root_sha256": "3" * 64,
            "head": request["release_sha"],
            "tree": request["release_tree_sha"],
            "source_tree_bound": True,
            "worker_sha256": request["worker_sha256"],
        },
        "runtime_snapshot": snapshot,
        "compose_execution": {
            "execution_plan_sha256": "4" * 64,
            "receipt_sha256": "5" * 64,
            "container_id_sha256": "a" * 64,
            "network_id_sha256": "b" * 64,
            "cleanup_verified": True,
        },
        "available_observations": ["database_parity", "dr_convergence"],
        "unavailable_observations": dict(CONTRACT.UNAVAILABLE_REASONS),
        "redaction": {
            "contains_credentials": False,
            "contains_raw_database_values": False,
            "contains_file_paths": False,
            "contains_object_keys": False,
            "contains_presigned_urls": False,
        },
        "production_mutated": False,
        "worker_transport_contacted": False,
        "object_storage_contacted": False,
        "attestation_sha256": CONTRACT.ZERO_SHA256,
    }
    document["attestation_sha256"] = CONTRACT._attestation_digest(document)
    return document


class ObservationContractTests(unittest.TestCase):
    def test_contract_constants_match_the_current_worker_wire_format(self) -> None:
        for name in (
            "REQUEST_SCHEMA",
            "ATTESTATION_SCHEMA",
            "HOST_IDENTITY_PROOF_SCHEMA",
            "PHASE",
            "OPERATION",
            "ROLES",
            "RUNTIME_SNAPSHOT_ROLES",
            "MAX_JSON_BYTES",
            "MAX_ROWS_PER_TABLE",
            "MAX_REQUEST_FUTURE_SKEW",
            "MAX_OBSERVATION_FUTURE_SKEW",
            "MAX_OBSERVATION_AGE",
            "MAX_CAPTURE_TO_ATTESTATION_SKEW",
            "MAX_HOST_PROOF_TO_ATTESTATION_SKEW",
            "EXPECTED_CONSTRAINTS",
            "REQUEST_FIELDS",
            "HOST_IDENTITY_PROOF_FIELDS",
            "ATTESTATION_FIELDS",
            "UNAVAILABLE_REASONS",
        ):
            with self.subTest(name=name):
                self.assertEqual(getattr(CONTRACT, name), getattr(WORKER, name))

    def test_canonical_paths_match_the_worker_for_all_roles_without_filesystem_access(
        self,
    ) -> None:
        for role in WORKER.ROLES:
            with self.subTest(role=role):
                expected = WORKER.canonical_paths(
                    operation_id=OPERATION_ID,
                    release_sha=RELEASE_SHA,
                    role=role,
                )
                actual = CONTRACT.canonical_paths(
                    operation_id=OPERATION_ID,
                    release_sha=RELEASE_SHA,
                    role=role,
                )
                self.assertEqual(
                    {key: str(value) for key, value in actual.items()},
                    {key: str(value) for key, value in expected.items()},
                )

    def test_contract_accepts_worker_requests_for_all_roles(self) -> None:
        for role in WORKER.ROLES:
            with self.subTest(role=role):
                request = _request(role=role)
                self.assertEqual(WORKER.validate_request(request, now=NOW), request)
                self.assertEqual(CONTRACT.validate_request(request, now=NOW), request)
                self.assertEqual(
                    CONTRACT._request_digest(request),
                    WORKER._request_digest(request),  # noqa: SLF001
                )

    def test_contract_rejects_noncanonical_serialized_request_path_spellings(
        self,
    ) -> None:
        # A worker-built request always uses these canonical spellings.  The
        # controller contract rejects aliases without consulting a local host
        # filesystem, so a path spelling cannot acquire meaning through a
        # controller-side symlink check.
        for role in WORKER.ROLES:
            request = _request(role=role)
            malformed_spellings = (
                (
                    "release-root-double-separator",
                    "release_root",
                    str(request["release_root"]).replace(
                        "/releases/",
                        "//releases/",
                        1,
                    ),
                ),
                (
                    "release-root-parent-segment",
                    "release_root",
                    f"{request['release_root']}/../{RELEASE_SHA}",
                ),
                (
                    "worker-path-current-segment",
                    "worker_path",
                    str(request["worker_path"]).replace(
                        "/scripts/",
                        "/scripts/./",
                        1,
                    ),
                ),
                (
                    "output-root-trailing-separator",
                    "output_root",
                    f"{request['output_root']}/",
                ),
            )
            for case, field, spelling in malformed_spellings:
                with self.subTest(role=role, case=case, spelling=spelling):
                    malformed = copy.deepcopy(request)
                    malformed[field] = spelling
                    malformed["request_sha256"] = CONTRACT._request_digest(malformed)
                    with self.assertRaisesRegex(
                        CONTRACT.ConvergenceObservationContractError,
                        "not canonical",
                    ):
                        CONTRACT.validate_request(malformed, now=NOW)

    def test_contract_accepts_a_worker_compatible_request_and_attestation(self) -> None:
        request = _request()
        attestation = _attestation(request)
        self.assertEqual(CONTRACT.validate_request(request, now=NOW), request)
        self.assertEqual(
            CONTRACT.validate_attestation(attestation, request=request, now=NOW),
            attestation,
        )
        # This fixture remains compatible with the pre-existing remote worker
        # validator; the new module only removes controller-side execution code.
        self.assertEqual(WORKER.validate_request(request, now=NOW), request)
        self.assertEqual(
            WORKER.validate_attestation(attestation, request=request, now=NOW),
            attestation,
        )

    def test_contract_rejects_a_tampered_compose_receipt(self) -> None:
        request = _request()
        attestation = _attestation(request)
        compose = dict(attestation["compose_execution"])
        compose["cleanup_verified"] = False
        attestation["compose_execution"] = compose
        attestation["attestation_sha256"] = CONTRACT._attestation_digest(attestation)
        with self.assertRaisesRegex(
            CONTRACT.ConvergenceObservationContractError,
            "cleanup",
        ):
            CONTRACT.validate_attestation(attestation, request=request, now=NOW)

    def test_contract_rejects_the_same_structural_tampering_as_the_worker(self) -> None:
        request = _request()
        attestation = _attestation(request)

        invalid_requests: list[dict[str, object]] = []
        wrong_role = copy.deepcopy(request)
        wrong_role["role"] = "not-a-role"
        wrong_role["request_sha256"] = CONTRACT._request_digest(wrong_role)
        invalid_requests.append(wrong_role)

        wrong_path = copy.deepcopy(request)
        wrong_path["output_root"] = "/root/untrusted"
        wrong_path["request_sha256"] = CONTRACT._request_digest(wrong_path)
        invalid_requests.append(wrong_path)

        zero_worker = copy.deepcopy(request)
        zero_worker["worker_sha256"] = CONTRACT.ZERO_SHA256
        zero_worker["request_sha256"] = CONTRACT._request_digest(zero_worker)
        invalid_requests.append(zero_worker)

        for invalid_request in invalid_requests:
            with self.subTest(request=invalid_request):
                with self.assertRaises(CONTRACT.ConvergenceObservationContractError):
                    CONTRACT.validate_request(invalid_request, now=NOW)
                with self.assertRaises(WORKER.ConvergenceRoleObserverError):
                    WORKER.validate_request(invalid_request, now=NOW)

        invalid_attestations: list[dict[str, object]] = []
        raw_data_claim = copy.deepcopy(attestation)
        raw_data_claim["redaction"] = {
            **raw_data_claim["redaction"],
            "contains_raw_database_values": True,
        }
        raw_data_claim["attestation_sha256"] = CONTRACT._attestation_digest(
            raw_data_claim
        )
        invalid_attestations.append(raw_data_claim)

        wrong_release = copy.deepcopy(attestation)
        wrong_release["release_identity"] = {
            **wrong_release["release_identity"],
            "head": "b" * 40,
        }
        wrong_release["attestation_sha256"] = CONTRACT._attestation_digest(
            wrong_release
        )
        invalid_attestations.append(wrong_release)

        for invalid_attestation in invalid_attestations:
            with self.subTest(attestation=invalid_attestation):
                with self.assertRaises(CONTRACT.ConvergenceObservationContractError):
                    CONTRACT.validate_attestation(
                        invalid_attestation,
                        request=request,
                        now=NOW,
                    )
                with self.assertRaises(WORKER.ConvergenceRoleObserverError):
                    WORKER.validate_attestation(
                        invalid_attestation,
                        request=request,
                        now=NOW,
                    )

        compose = copy.deepcopy(attestation["compose_execution"])
        compose["cleanup_verified"] = False
        with self.assertRaises(CONTRACT.ConvergenceObservationContractError):
            CONTRACT.validate_compose_execution_proof(compose)
        with self.assertRaises(WORKER.ConvergenceRoleObserverError):
            WORKER._validate_compose_execution_proof(  # noqa: SLF001
                compose,
                request=request,
            )

    def test_contract_source_has_no_execution_or_loader_surface(self) -> None:
        source_path = Path(CONTRACT.__file__).resolve()
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        imported_roots: set[str] = set()
        import_records: list[tuple[str, str, tuple[str, ...]]] = []
        bindings: dict[str, str] = {}
        forbidden_imports: set[str] = set()
        forbidden_calls: set[str] = set()

        forbidden_import_roots = {
            "importlib",
            "io",
            "os",
            "runpy",
            "subprocess",
        }
        forbidden_call_prefixes = (
            "importlib.",
            "io.",
            "os.",
            "runpy.",
            "subprocess.",
        )
        forbidden_exact_calls = {
            "__import__",
            "builtins.open",
            "compile",
            "eval",
            "exec",
            "open",
            "Path",
            "pathlib.Path",
            "pathlib.PosixPath",
            "pathlib.WindowsPath",
        }
        forbidden_path_methods = {
            "chmod",
            "chown",
            "cwd",
            "exists",
            "glob",
            "hardlink_to",
            "home",
            "is_dir",
            "is_file",
            "is_symlink",
            "iterdir",
            "lstat",
            "mkdir",
            "open",
            "owner",
            "read_bytes",
            "read_text",
            "rename",
            "resolve",
            "rglob",
            "samefile",
            "stat",
            "symlink_to",
            "touch",
            "unlink",
            "write_bytes",
            "write_text",
        }

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = tuple(
                    sorted(
                        f"{alias.name} as {alias.asname}"
                        if alias.asname
                        else alias.name
                        for alias in node.names
                    )
                )
                import_records.append(("import", "", names))
                for alias in node.names:
                    root = alias.name.split(".", 1)[0]
                    imported_roots.add(root)
                    bindings[alias.asname or root] = alias.name
                    if root in forbidden_import_roots:
                        forbidden_imports.add(alias.name)
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                names = tuple(
                    sorted(
                        f"{alias.name} as {alias.asname}"
                        if alias.asname
                        else alias.name
                        for alias in node.names
                    )
                )
                import_records.append(("from", module, names))
                root = module.split(".", 1)[0]
                if root:
                    imported_roots.add(root)
                for alias in node.names:
                    bindings[alias.asname or alias.name] = f"{module}.{alias.name}"
                if root in forbidden_import_roots:
                    forbidden_imports.add(module)

        def dotted_name(node: ast.AST) -> str | None:
            if isinstance(node, ast.Name):
                return node.id
            if isinstance(node, ast.Attribute):
                parent = dotted_name(node.value)
                return f"{parent}.{node.attr}" if parent else None
            return None

        def resolved_name(node: ast.AST) -> str | None:
            raw = dotted_name(node)
            if raw is None:
                return None
            root, separator, suffix = raw.partition(".")
            target = bindings.get(root, root)
            return f"{target}.{suffix}" if separator else target

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            raw = dotted_name(node.func)
            resolved = resolved_name(node.func)
            names = {name for name in (raw, resolved) if name}
            if (
                names & forbidden_exact_calls
                or any(
                    name.startswith(forbidden_call_prefixes)
                    for name in names
                )
                or (
                    isinstance(node.func, ast.Attribute)
                    and node.func.attr in forbidden_path_methods
                )
            ):
                forbidden_calls.add(resolved or raw or "<dynamic>")

        self.assertEqual(
            sorted(import_records),
            sorted(
                [
                    ("from", "__future__", ("annotations",)),
                    ("from", "datetime", ("datetime", "timedelta", "timezone")),
                    ("import", "", ("hashlib",)),
                    ("import", "", ("ipaddress",)),
                    ("import", "", ("json",)),
                    ("from", "pathlib", ("PurePosixPath",)),
                    ("import", "", ("re",)),
                    ("from", "typing", ("Any", "Mapping")),
                    ("from", "uuid", ("UUID",)),
                ]
            ),
        )
        self.assertEqual(
            imported_roots,
            {
                "__future__",
                "datetime",
                "hashlib",
                "ipaddress",
                "json",
                "pathlib",
                "re",
                "typing",
                "uuid",
            },
        )
        self.assertFalse(forbidden_imports)
        self.assertFalse(forbidden_calls)

        # The exact import list above prevents aliases for filesystem, loader,
        # process, or transport modules.  Keep the remaining dynamic surface
        # equally narrow: a new call must be a pure parser/hash primitive, a
        # validator defined in this module, or a data-container method.  This
        # catches indirect builtins such as ``getattr(__builtins__, "open")``
        # and loader escapes that a short deny-list of call names misses.
        forbidden_names = {
            "__builtins__",
            "__import__",
            "breakpoint",
            "compile",
            "delattr",
            "eval",
            "exec",
            "getattr",
            "globals",
            "input",
            "locals",
            "open",
            "setattr",
            "vars",
        }
        forbidden_attributes = {
            "__builtins__",
            "__cached__",
            "__class__",
            "__code__",
            "__dict__",
            "__file__",
            "__getattribute__",
            "__globals__",
            "__import__",
            "__loader__",
            "__mro__",
            "__path__",
            "__reduce__",
            "__reduce_ex__",
            "__spec__",
            "__subclasses__",
        }
        forbidden_references = {
            node.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Name) and node.id in forbidden_names
        }
        forbidden_references.update(
            f"attribute:{node.attr}"
            for node in ast.walk(tree)
            if isinstance(node, ast.Attribute) and node.attr in forbidden_attributes
        )
        self.assertFalse(forbidden_references)

        module_definitions = {
            node.name
            for node in tree.body
            if isinstance(
                node,
                (ast.AsyncFunctionDef, ast.ClassDef, ast.FunctionDef),
            )
        }
        safe_direct_calls = module_definitions | {
            "PurePosixPath",
            "UUID",
            "any",
            "dict",
            "frozenset",
            "isinstance",
            "set",
            "str",
            "timedelta",
            "type",
        }
        safe_imported_calls = {
            "datetime.datetime.fromisoformat",
            "datetime.datetime.now",
            "hashlib.sha256",
            "ipaddress.IPv4Address",
            "json.dumps",
            "json.loads",
            "re.compile",
            "re.fullmatch",
        }
        safe_data_methods = {
            "astimezone",
            "decode",
            "encode",
            "fullmatch",
            "get",
            "hexdigest",
            "is_absolute",
            "items",
            "replace",
            "utcoffset",
        }
        unexpected_calls: list[str] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            raw = dotted_name(node.func)
            resolved = resolved_name(node.func)
            if isinstance(node.func, ast.Name) and raw in safe_direct_calls:
                continue
            if resolved in safe_imported_calls:
                continue
            if (
                isinstance(node.func, ast.Attribute)
                and node.func.attr in safe_data_methods
            ):
                continue
            unexpected_calls.append(resolved or raw or "<dynamic-call-target>")
        self.assertEqual(unexpected_calls, [])


if __name__ == "__main__":
    unittest.main()
