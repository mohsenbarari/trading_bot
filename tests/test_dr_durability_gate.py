import unittest

from core.dr_durability_gate import (
    DURABILITY_GATE_READ_FUNCTION,
    DrDurabilityGateError,
    enforce_session_durability,
)


class _Rows:
    def __init__(self, row):
        self.row = row

    def mappings(self):
        return self

    def one_or_none(self):
        return self.row


class _Connection:
    def __init__(self, row):
        self.row = row
        self.statement = None

    def execute(self, statement):
        self.statement = str(statement)
        return _Rows(self.row)


class _Session:
    def __init__(self, row):
        self.connection_value = _Connection(row)

    def connection(self):
        return self.connection_value


class DurabilityGateTests(unittest.TestCase):
    def test_gate_reads_through_security_definer_without_table_lock_privilege(self):
        session = _Session(
            {
                "connectivity_mode": "online",
                "event_journal_healthy": True,
                "blob_journal_healthy": True,
                "evidence_expires_at": None,
            }
        )

        with self.assertRaisesRegex(DrDurabilityGateError, "durability_evidence_missing"):
            enforce_session_durability(session, {"offers"})

        statement = session.connection_value.statement
        self.assertIn(f"public.{DURABILITY_GATE_READ_FUNCTION}()", statement)
        self.assertNotIn("FOR SHARE", statement.upper())
        self.assertNotIn("FROM dr_durability_state", statement)


if __name__ == "__main__":
    unittest.main()
