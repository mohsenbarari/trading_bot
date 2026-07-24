BEGIN;

-- The approval session itself is deliberately never stored here.  This ledger
-- retains only the already signed, action-specific receipts so retry handling
-- is deterministic and the Witness has a durable audit surface.
CREATE TABLE human_approval_relay_receipts (
    request_id VARCHAR(64) PRIMARY KEY,
    request_sha256 VARCHAR(64) NOT NULL,
    approval_id UUID NOT NULL,
    action VARCHAR(128) NOT NULL,
    subject_sha256 VARCHAR(64) NOT NULL,
    session_token_sha256 VARCHAR(64) NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    receipt JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT ck_human_approval_relay_request_sha256 CHECK (
        request_sha256 ~ '^[0-9a-f]{64}$'
    ),
    CONSTRAINT ck_human_approval_relay_subject_sha256 CHECK (
        subject_sha256 ~ '^[0-9a-f]{64}$'
    ),
    CONSTRAINT ck_human_approval_relay_session_sha256 CHECK (
        session_token_sha256 ~ '^[0-9a-f]{64}$'
    )
);

CREATE INDEX ix_human_approval_relay_receipts_expires_at
    ON human_approval_relay_receipts (expires_at);

DELETE FROM writer_witness_schema_version;
INSERT INTO writer_witness_schema_version (version_num) VALUES ('003');

COMMIT;
