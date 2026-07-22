BEGIN;

CREATE TABLE dr_failover_operation_ledger (
    operation_id VARCHAR(36) PRIMARY KEY,
    operation_nonce VARCHAR(36) NOT NULL UNIQUE,
    plan_hash VARCHAR(64) NOT NULL,
    status VARCHAR(16) NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    reservation_receipt_id VARCHAR(64) NOT NULL UNIQUE,
    reservation_receipt_hash VARCHAR(64) NOT NULL,
    final_evidence_hash VARCHAR(64),
    finalized_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT ck_dr_failover_operation_status CHECK (
        status IN ('reserved', 'completed', 'rolled_back')
    ),
    CONSTRAINT ck_dr_failover_operation_final CHECK (
        (status = 'reserved' AND final_evidence_hash IS NULL AND finalized_at IS NULL)
        OR
        (status <> 'reserved' AND final_evidence_hash IS NOT NULL AND finalized_at IS NOT NULL)
    )
);

CREATE INDEX ix_dr_failover_operation_created_at
    ON dr_failover_operation_ledger (created_at);

DELETE FROM writer_witness_schema_version;
INSERT INTO writer_witness_schema_version (version_num) VALUES ('002');

COMMIT;
