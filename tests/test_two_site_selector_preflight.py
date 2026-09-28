from __future__ import annotations

import contextlib
import json
import os
from pathlib import Path
from unittest import mock
import pytest

os.environ.setdefault("APP_ENV_FILE", "config/unit-test.env.example")
try:
    from tests.test_p1_00_characterization_network_isolation import (
        install_p1_00_network_fence,
    )

    install_p1_00_network_fence()
except ImportError:
    pass

from scripts.two_site_proposal_runner import (
    ProposalError,
    bounded_file_bytes,
    check,
    load_task,
    run,
    validate_task_test_selectors,
    validate_test_specification,
)

ROOT = Path(__file__).resolve().parents[1]
EXCERPT_MARKER = "\nCANONICAL_SOURCE_EXCERPTS_JSON:\n"


def _make_task_payload(
    goal: str,
    test_selectors: list[str],
    allowed_files: list[str] | None = None,
) -> dict:
    return {
        "task_id": "AE01-20A2-PREFLIGHT-001",
        "goal": goal,
        "context_files": ["tests/test_two_site_selector_preflight.py"],
        "allowed_files": allowed_files
        or [
            "tests/test_two_site_selector_preflight.py",
        ],
        "requirements": ["Requirement 1"],
        "test_selectors": test_selectors,
    }


@contextlib.contextmanager
def _mock_task_bytes(task: dict):
    orig_bounded_file_bytes = bounded_file_bytes

    def _fake_bounded(root, path, limit, label, failure_callback=None):
        if label == "task":
            return json.dumps(task).encode("utf-8")
        return orig_bounded_file_bytes(
            root, path, limit, label, failure_callback=failure_callback
        )

    with mock.patch(
        "scripts.two_site_proposal_runner.bounded_file_bytes",
        side_effect=_fake_bounded,
    ):
        yield ROOT / "task.json"


def test_direct_path_and_node_acceptance() -> None:
    overlay_file = "tests/test_two_site_selector_preflight.py"
    allowed = [overlay_file]

    # Direct canonical path resolving to allowed new test overlay
    validate_test_specification(ROOT, overlay_file, allowed)

    # Valid single and multi-level node parts on allowed overlay
    validate_test_specification(
        ROOT, f"{overlay_file}::test_direct_path_and_node_acceptance", allowed
    )
    validate_test_specification(
        ROOT, f"{overlay_file}::TestPreflightSuite::test_scenario_one", allowed
    )

    # Tracked test in inventory via git ls-files
    with mock.patch(
        "scripts.two_site_proposal_runner._is_tracked_test", return_value=True
    ):
        validate_test_specification(ROOT, "tests/test_existing_repo_file.py", [])
        validate_test_specification(
            ROOT, "tests/test_existing_repo_file.py::test_case", []
        )
        validate_test_specification(
            ROOT, "tests/unit/nested/test_example.py::TestClass::test_inner", []
        )


def test_command_option_missing_path_rejection() -> None:
    allowed = ["tests/test_two_site_selector_preflight.py"]

    # Command and runner prefixes
    with pytest.raises(ProposalError):
        validate_test_specification(
            ROOT, "pytest tests/test_two_site_selector_preflight.py", allowed
        )
    with pytest.raises(ProposalError):
        validate_test_specification(
            ROOT,
            "python -m pytest tests/test_two_site_selector_preflight.py",
            allowed,
        )
    with pytest.raises(ProposalError):
        validate_test_specification(
            ROOT, "sh -c tests/test_two_site_selector_preflight.py", allowed
        )

    # Options and flags
    with pytest.raises(ProposalError):
        validate_test_specification(
            ROOT, "tests/test_two_site_selector_preflight.py -v", allowed
        )
    with pytest.raises(ProposalError):
        validate_test_specification(ROOT, "-k test_case", allowed)
    with pytest.raises(ProposalError):
        validate_test_specification(
            ROOT, "tests/test_two_site_selector_preflight.py --tb=short", allowed
        )
    with pytest.raises(ProposalError):
        validate_test_specification(
            ROOT,
            "tests/test_two_site_selector_preflight.py::test_case -s",
            allowed,
        )

    # Missing path (not in allowed overlay and not tracked in git)
    with mock.patch(
        "scripts.two_site_proposal_runner._is_tracked_test", return_value=False
    ):
        with pytest.raises(ProposalError):
            validate_test_specification(
                ROOT, "tests/test_missing_untracked_nonexistent.py", []
            )

    # Unsupported syntax, non-test files, and noncanonical path escapes
    with pytest.raises(ProposalError):
        validate_test_specification(ROOT, "tests/conftest.py", allowed)
    with pytest.raises(ProposalError):
        validate_test_specification(
            ROOT, "tests/test_two_site_selector_preflight.py::", allowed
        )
    with pytest.raises(ProposalError):
        validate_test_specification(
            ROOT, "tests/test_two_site_selector_preflight.py::123invalid", allowed
        )
    with pytest.raises(ProposalError):
        validate_test_specification(
            ROOT, "tests/test_two_site_selector_preflight.py::test-hyphen", allowed
        )
    with pytest.raises(ProposalError):
        validate_test_specification(
            ROOT, "/tests/test_two_site_selector_preflight.py", allowed
        )
    with pytest.raises(ProposalError):
        validate_test_specification(
            ROOT, "tests/../tests/test_two_site_selector_preflight.py", allowed
        )
    with pytest.raises(ProposalError):
        validate_test_specification(
            ROOT, "scripts/two_site_proposal_runner.py", allowed
        )


def test_check_and_run_fail_before_provider_boundary() -> None:
    marker_goal = f"Canonical marker task goal{EXCERPT_MARKER}{json.dumps({'excerpts': []})}"
    invalid_spec = "pytest tests/test_two_site_selector_preflight.py"
    task = _make_task_payload(marker_goal, [invalid_spec])

    # Directly validate task selector helper
    with pytest.raises(ProposalError):
        validate_task_test_selectors(ROOT, task)

    with _mock_task_bytes(task) as task_path:
        # check() must fail before Provider contact
        with mock.patch(
            "scripts.two_site_cycle_tools.validate_excerpts", return_value=None
        ):
            with pytest.raises(ProposalError):
                check(ROOT, task_path)

        # run() must fail before proposal_cli_context
        with mock.patch(
            "scripts.two_site_cycle_tools.validate_excerpts", return_value=None
        ):
            with mock.patch(
                "scripts.two_site_proposal_runner.proposal_cli_context"
            ) as mock_cli_ctx:
                with pytest.raises(ProposalError):
                    run(ROOT, task_path, "worker", "run-preflight-001", 300)
                mock_cli_ctx.assert_not_called()


def test_unchanged_unmarked_fixture_acceptance() -> None:
    # Unmarked task without CANONICAL_SOURCE_EXCERPTS_JSON marker
    unmarked_goal = "Legacy task without any canonical excerpts marker"
    legacy_specs = ["pytest tests/legacy_test.py", "-v"]
    unmarked_task = _make_task_payload(unmarked_goal, legacy_specs)

    # validate_task_test_selectors is a no-op on unmarked fixtures
    validate_task_test_selectors(ROOT, unmarked_task)

    with _mock_task_bytes(unmarked_task) as task_path:
        loaded_task, _ = load_task(ROOT, task_path)
        assert loaded_task["task_id"] == unmarked_task["task_id"]
