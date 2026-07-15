BEGIN;

CREATE TABLE writer_witness_schema_version (
    version_num VARCHAR(32) PRIMARY KEY,
    installed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);

CREATE TABLE webapp_writer_witness_state (
    authority VARCHAR(16) PRIMARY KEY,
    holder_site VARCHAR(16),
    writer_epoch BIGINT NOT NULL,
    lease_id VARCHAR(64),
    lease_status VARCHAR(16) NOT NULL,
    issued_at TIMESTAMPTZ,
    expires_at TIMESTAMPTZ,
    transition_id VARCHAR(64) NOT NULL,
    updated_by VARCHAR(128) NOT NULL,
    reason TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT ck_webapp_writer_witness_authority CHECK (authority = 'webapp'),
    CONSTRAINT ck_webapp_writer_witness_epoch CHECK (writer_epoch >= 0),
    CONSTRAINT ck_webapp_writer_witness_holder CHECK (
        holder_site IS NULL OR holder_site IN ('webapp_fi', 'webapp_ir')
    ),
    CONSTRAINT ck_webapp_writer_witness_status CHECK (
        lease_status IN ('vacant', 'leased', 'draining')
    ),
    CONSTRAINT ck_webapp_writer_witness_consistency CHECK (
        (
            lease_status = 'vacant'
            AND holder_site IS NULL
            AND lease_id IS NULL
            AND issued_at IS NULL
            AND expires_at IS NULL
        ) OR (
            lease_status <> 'vacant'
            AND holder_site IS NOT NULL
            AND lease_id IS NOT NULL
            AND issued_at IS NOT NULL
            AND expires_at IS NOT NULL
        )
    )
);

CREATE TABLE webapp_writer_witness_receipts (
    request_id VARCHAR(64) PRIMARY KEY,
    request_hash VARCHAR(64) NOT NULL,
    action VARCHAR(16) NOT NULL,
    transition_id VARCHAR(64) NOT NULL,
    response_json TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT ck_webapp_writer_witness_receipt_action CHECK (
        action IN ('acquire', 'renew', 'drain')
    )
);

CREATE INDEX ix_webapp_writer_witness_receipts_created_at
    ON webapp_writer_witness_receipts (created_at);

INSERT INTO writer_witness_schema_version (version_num) VALUES ('001');

INSERT INTO webapp_writer_witness_state (
    authority,
    holder_site,
    writer_epoch,
    lease_id,
    lease_status,
    issued_at,
    expires_at,
    transition_id,
    updated_by,
    reason
) VALUES (
    'webapp',
    NULL,
    0,
    NULL,
    'vacant',
    NULL,
    NULL,
    'dedicated-witness-bootstrap',
    'migration-001',
    'initialize dedicated writer witness database'
);

COMMIT;
