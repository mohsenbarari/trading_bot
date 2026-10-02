"""Hermetic JSON schema domain characterization tests for continuation 17 inventory.

Prerequisite for Assignment WA-P1-00B-AFTER-OBSERVER-PROJECTION-20261002-161.
Verifies rejection of invalid schema_version types (float 1.0, string \"1\", null)
using independent standalone JSON fixtures, a frozen literal continuation-17 oracle,
and the existing provenance validator without importing application modules.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.test_p1_00b_characterization_recent_inventory_continuation_17_missing_target import (
    validate_continuation_seventeen_provenance,
)

REPO_ROOT = Path(__file__).resolve().parent.parent

INVENTORY_RELPATH = "docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-17.json"
PREDECESSOR_RELPATH = "docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-16.json"
RECORD_SOURCE_RELPATH = "tests/test_p1_00b_characterization_users_read_missing_http.py"

# Independently written frozen full literal expected continuation-17 dictionary
FROZEN_EXPECTED_CONTINUATION_17: dict[str, object] = {
    "schema_version": 1,
    "stage": "P1-00b",
    "predecessor_inventory": {
        "path": "docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-16.json",
        "sha256": "22e2c7c921ee8e20a2335a1ce1f32e0224f4b1403f2597ef6a11e36d4930fe3a",
        "last_commit": "0eebdb41c0300ae0d92a16d5cd0aa8db75ad2b9f",
    },
    "records": [
        {
            "path": "tests/test_p1_00b_characterization_users_read_missing_http.py",
            "sha256": "7ae68ea6d3239cf6ab844b3087efe45a56a38b068308316861362b1ff71c8dfb",
            "last_commit": "0f3deb613251c7c5f39822b2a1f0e5532e5e140f",
            "seed_ids": [
                "API-IDENTITY-GET-USERS-READ-USER-L440",
            ],
            "test_selectors": [
                "tests/test_p1_00b_characterization_users_read_missing_http.py::UsersReadMissingHttpCharacterizationTests::test_authorized_missing_target_returns_http_404_after_single_get",
            ],
        },
    ],
}

FLOAT_SCHEMA_VERSION_JSON = """{
  "schema_version": 1.0,
  "stage": "P1-00b",
  "predecessor_inventory": {
    "path": "docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-16.json",
    "sha256": "22e2c7c921ee8e20a2335a1ce1f32e0224f4b1403f2597ef6a11e36d4930fe3a",
    "last_commit": "0eebdb41c0300ae0d92a16d5cd0aa8db75ad2b9f"
  },
  "records": [
    {
      "path": "tests/test_p1_00b_characterization_users_read_missing_http.py",
      "sha256": "7ae68ea6d3239cf6ab844b3087efe45a56a38b068308316861362b1ff71c8dfb",
      "last_commit": "0f3deb613251c7c5f39822b2a1f0e5532e5e140f",
      "seed_ids": [
        "API-IDENTITY-GET-USERS-READ-USER-L440"
      ],
      "test_selectors": [
        "tests/test_p1_00b_characterization_users_read_missing_http.py::UsersReadMissingHttpCharacterizationTests::test_authorized_missing_target_returns_http_404_after_single_get"
      ]
    }
  ]
}"""

STRING_SCHEMA_VERSION_JSON = """{
  "schema_version": "1",
  "stage": "P1-00b",
  "predecessor_inventory": {
    "path": "docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-16.json",
    "sha256": "22e2c7c921ee8e20a2335a1ce1f32e0224f4b1403f2597ef6a11e36d4930fe3a",
    "last_commit": "0eebdb41c0300ae0d92a16d5cd0aa8db75ad2b9f"
  },
  "records": [
    {
      "path": "tests/test_p1_00b_characterization_users_read_missing_http.py",
      "sha256": "7ae68ea6d3239cf6ab844b3087efe45a56a38b068308316861362b1ff71c8dfb",
      "last_commit": "0f3deb613251c7c5f39822b2a1f0e5532e5e140f",
      "seed_ids": [
        "API-IDENTITY-GET-USERS-READ-USER-L440"
      ],
      "test_selectors": [
        "tests/test_p1_00b_characterization_users_read_missing_http.py::UsersReadMissingHttpCharacterizationTests::test_authorized_missing_target_returns_http_404_after_single_get"
      ]
    }
  ]
}"""

NULL_SCHEMA_VERSION_JSON = """{
  "schema_version": null,
  "stage": "P1-00b",
  "predecessor_inventory": {
    "path": "docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-16.json",
    "sha256": "22e2c7c921ee8e20a2335a1ce1f32e0224f4b1403f2597ef6a11e36d4930fe3a",
    "last_commit": "0eebdb41c0300ae0d92a16d5cd0aa8db75ad2b9f"
  },
  "records": [
    {
      "path": "tests/test_p1_00b_characterization_users_read_missing_http.py",
      "sha256": "7ae68ea6d3239cf6ab844b3087efe45a56a38b068308316861362b1ff71c8dfb",
      "last_commit": "0f3deb613251c7c5f39822b2a1f0e5532e5e140f",
      "seed_ids": [
        "API-IDENTITY-GET-USERS-READ-USER-L440"
      ],
      "test_selectors": [
        "tests/test_p1_00b_characterization_users_read_missing_http.py::UsersReadMissingHttpCharacterizationTests::test_authorized_missing_target_returns_http_404_after_single_get"
      ]
    }
  ]
}"""


def _load_actual_baseline() -> dict:
    raw_bytes = (REPO_ROOT / INVENTORY_RELPATH).read_bytes()
    return json.loads(raw_bytes.decode("utf-8"))


def _read_predecessor_bytes() -> bytes:
    return (REPO_ROOT / PREDECESSOR_RELPATH).read_bytes()


def _read_source_bytes() -> bytes:
    return (REPO_ROOT / RECORD_SOURCE_RELPATH).read_bytes()


def assert_continuation_seventeen_oracle(data: dict) -> None:
    """Assertion oracle checking exact integer schema_version and complete document equality."""
    assert isinstance(data, dict), f"Expected dict, got {type(data).__name__}"
    assert "schema_version" in data, "schema_version key missing from document"
    schema_version = data["schema_version"]
    assert type(schema_version) is int, (
        f"schema_version must be exact int, got {type(schema_version).__name__} ({schema_version!r})"
    )
    assert schema_version == 1, (
        f"schema_version must be literal 1, got {schema_version!r}"
    )
    assert data == FROZEN_EXPECTED_CONTINUATION_17, (
        "Document does not match frozen expected continuation-17 dictionary"
    )


def test_float_schema_version_rejected() -> None:
    """Verify schema_version 1.0 is rejected by both oracle and provenance validator."""
    # 1. Baseline before
    baseline = _load_actual_baseline()
    assert_continuation_seventeen_oracle(baseline)
    pred_bytes = _read_predecessor_bytes()
    source_bytes = _read_source_bytes()
    validate_continuation_seventeen_provenance(baseline, pred_bytes, source_bytes)

    # 2. Decode standalone complete JSON fixture
    mutant = json.loads(FLOAT_SCHEMA_VERSION_JSON)
    assert type(mutant["schema_version"]) is float
    assert mutant["schema_version"] == 1.0

    # 3. Prove unchanged other fields
    assert {k: v for k, v in mutant.items() if k != "schema_version"} == {
        k: v for k, v in FROZEN_EXPECTED_CONTINUATION_17.items() if k != "schema_version"
    }
    assert set(mutant.keys()) == set(FROZEN_EXPECTED_CONTINUATION_17.keys())

    # 4. Oracle rejects specifically at schema-version assertion
    with pytest.raises(AssertionError) as exc_info:
        assert_continuation_seventeen_oracle(mutant)
    assert "schema_version must be exact int" in str(exc_info.value)

    # 5. Provenance validator rejects mutant with ValueError matching Invalid schema_version
    with pytest.raises(ValueError, match=r"Invalid schema_version"):
        validate_continuation_seventeen_provenance(mutant, pred_bytes, source_bytes)

    # 6. Fresh baseline after restoration passes both
    restored_baseline = _load_actual_baseline()
    assert_continuation_seventeen_oracle(restored_baseline)
    validate_continuation_seventeen_provenance(restored_baseline, pred_bytes, source_bytes)


def test_string_schema_version_rejected() -> None:
    """Verify schema_version \"1\" is rejected by both oracle and provenance validator."""
    # 1. Baseline before
    baseline = _load_actual_baseline()
    assert_continuation_seventeen_oracle(baseline)
    pred_bytes = _read_predecessor_bytes()
    source_bytes = _read_source_bytes()
    validate_continuation_seventeen_provenance(baseline, pred_bytes, source_bytes)

    # 2. Decode standalone complete JSON fixture
    mutant = json.loads(STRING_SCHEMA_VERSION_JSON)
    assert type(mutant["schema_version"]) is str
    assert mutant["schema_version"] == "1"

    # 3. Prove unchanged other fields
    assert {k: v for k, v in mutant.items() if k != "schema_version"} == {
        k: v for k, v in FROZEN_EXPECTED_CONTINUATION_17.items() if k != "schema_version"
    }
    assert set(mutant.keys()) == set(FROZEN_EXPECTED_CONTINUATION_17.keys())

    # 4. Oracle rejects specifically at schema-version assertion
    with pytest.raises(AssertionError) as exc_info:
        assert_continuation_seventeen_oracle(mutant)
    assert "schema_version must be exact int" in str(exc_info.value)

    # 5. Provenance validator rejects mutant with ValueError matching Invalid schema_version
    with pytest.raises(ValueError, match=r"Invalid schema_version"):
        validate_continuation_seventeen_provenance(mutant, pred_bytes, source_bytes)

    # 6. Fresh baseline after restoration passes both
    restored_baseline = _load_actual_baseline()
    assert_continuation_seventeen_oracle(restored_baseline)
    validate_continuation_seventeen_provenance(restored_baseline, pred_bytes, source_bytes)


def test_null_schema_version_rejected() -> None:
    """Verify schema_version null is rejected by both oracle and provenance validator."""
    # 1. Baseline before
    baseline = _load_actual_baseline()
    assert_continuation_seventeen_oracle(baseline)
    pred_bytes = _read_predecessor_bytes()
    source_bytes = _read_source_bytes()
    validate_continuation_seventeen_provenance(baseline, pred_bytes, source_bytes)

    # 2. Decode standalone complete JSON fixture
    mutant = json.loads(NULL_SCHEMA_VERSION_JSON)
    assert mutant["schema_version"] is None
    assert type(mutant["schema_version"]) is type(None)

    # 3. Prove unchanged other fields
    assert {k: v for k, v in mutant.items() if k != "schema_version"} == {
        k: v for k, v in FROZEN_EXPECTED_CONTINUATION_17.items() if k != "schema_version"
    }
    assert set(mutant.keys()) == set(FROZEN_EXPECTED_CONTINUATION_17.keys())

    # 4. Oracle rejects specifically at schema-version assertion
    with pytest.raises(AssertionError) as exc_info:
        assert_continuation_seventeen_oracle(mutant)
    assert "schema_version must be exact int" in str(exc_info.value)

    # 5. Provenance validator rejects mutant with ValueError matching Invalid schema_version
    with pytest.raises(ValueError, match=r"Invalid schema_version"):
        validate_continuation_seventeen_provenance(mutant, pred_bytes, source_bytes)

    # 6. Fresh baseline after restoration passes both
    restored_baseline = _load_actual_baseline()
    assert_continuation_seventeen_oracle(restored_baseline)
    validate_continuation_seventeen_provenance(restored_baseline, pred_bytes, source_bytes)
