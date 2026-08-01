"""Global durable one-time registry for V2 Witness attestations.

The Gen1 and Gen2 V2 strict-writer tables deliberately remain separate: Gen2
does not turn the old table into a nullable hybrid.  A table-local unique
constraint, however, cannot stop the same signed Witness attestation from
being consumed once by each generation.  This small registry makes the
canonical attestation digest the single PostgreSQL identity across both
append-only source tables.

Source-table ``BEFORE INSERT`` triggers claim this record in the same local
transaction as their source row.  A unique conflict therefore aborts the
whole source transaction; no application response or partial consumption can
become durable.  The registry is evidence only.  It neither verifies a
Witness signature nor grants writer, promotion, traffic, or remote-storage
authority.
"""

from __future__ import annotations

from sqlalchemy import CheckConstraint, Column, DateTime, String

from .database import Base


__all__ = (
    "PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_ATTESTATION_CONSUMPTION_REGISTRY_SCHEMA",
    "PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_ATTESTATION_CONSUMPTION_SOURCE_GEN1",
    "PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_ATTESTATION_CONSUMPTION_SOURCE_GEN2",
    "PhysicalWalV2WitnessRoundtripAttestationConsumption",
)


PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_ATTESTATION_CONSUMPTION_REGISTRY_SCHEMA = (
    "gold-trade-physical-wal-v2-witness-roundtrip-attestation-consumption-registry-v1"
)
PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_ATTESTATION_CONSUMPTION_SOURCE_GEN1 = (
    "strict_writer_gen1"
)
PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_ATTESTATION_CONSUMPTION_SOURCE_GEN2 = (
    "strict_writer_gen2"
)


_SHA256 = "^[0-9a-f]{64}$"
_GEN1_COMMIT_ID = "^v2-witness-strict-writer-[0-9a-f]{64}$"
_GEN2_COMMIT_ID = "^v2-witness-strict-writer-g2-[0-9a-f]{64}$"


class PhysicalWalV2WitnessRoundtripAttestationConsumption(Base):
    """One immutable global claim for one canonical Witness attestation.

    ``attestation_sha256`` is intentionally the only primary key.  The
    source generation and deterministic source commit id are retained for
    audit/reconciliation, but cannot be changed after the source-table
    transaction commits.
    """

    __tablename__ = "physical_wal_v2_witness_roundtrip_attestation_consumptions"
    __table_args__ = (
        CheckConstraint(
            f"attestation_sha256 ~ '{_SHA256}' "
            f"AND attestation_sha256 <> '{'0' * 64}'",
            name="ck_v2wsrc_registry_attestation",
        ),
        CheckConstraint(
            "(source_generation = 'strict_writer_gen1' "
            f"AND source_commit_id ~ '{_GEN1_COMMIT_ID}') "
            "OR (source_generation = 'strict_writer_gen2' "
            f"AND source_commit_id ~ '{_GEN2_COMMIT_ID}')",
            name="ck_v2wsrc_registry_source",
        ),
        CheckConstraint(
            "consumed_at IS NOT NULL",
            name="ck_v2wsrc_registry_consumed_at",
        ),
    )

    attestation_sha256 = Column(String(64), primary_key=True)
    source_generation = Column(String(32), nullable=False)
    source_commit_id = Column(String(128), nullable=False)
    consumed_at = Column(DateTime(timezone=True), nullable=False)
