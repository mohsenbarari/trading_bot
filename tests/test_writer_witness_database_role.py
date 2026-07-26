import unittest

from writer_witness_app import (
    WitnessServiceConfigurationError,
    verify_witness_runtime_database_role,
)


class _MappingResult:
    def __init__(self, row):
        self.row = row

    def mappings(self):
        return self

    def one_or_none(self):
        return self.row


class _FakeSession:
    def __init__(self, row):
        self.row = row

    async def execute(self, _statement):
        return _MappingResult(self.row)


def _least_privilege_row(**overrides):
    row = {
        "database_user": "writer_witness_runtime",
        "database_owner": "writer_witness_migrator",
        "database_create": False,
        "schema_create": False,
        "schema_read": True,
        "state_dml": True,
        "receipt_dml": True,
        "ledger_dml": True,
        "relay_dml": True,
        "owned_objects": 0,
    }
    row.update(overrides)
    return row


class WriterWitnessDatabaseRoleTests(unittest.IsolatedAsyncioTestCase):
    async def test_runtime_role_accepts_exact_dml_without_ownership(self):
        await verify_witness_runtime_database_role(
            _FakeSession(_least_privilege_row()),
            expected_user="writer_witness_runtime",
        )

    async def test_owner_or_ddl_capable_runtime_is_rejected(self):
        unsafe = (
            {"database_owner": "writer_witness_runtime"},
            {"database_create": True},
            {"schema_create": True},
            {"owned_objects": 1},
            {"ledger_dml": False},
            {"relay_dml": False},
        )
        for overrides in unsafe:
            with self.subTest(overrides=overrides), self.assertRaises(
                WitnessServiceConfigurationError
            ):
                await verify_witness_runtime_database_role(
                    _FakeSession(_least_privilege_row(**overrides)),
                    expected_user="writer_witness_runtime",
                )


if __name__ == "__main__":
    unittest.main()
