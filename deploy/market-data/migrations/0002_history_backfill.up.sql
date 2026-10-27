BEGIN;

CREATE TABLE market_data.history_import_batches (
    import_batch_id BYTEA PRIMARY KEY CHECK (octet_length(import_batch_id) = 32),
    source_code TEXT NOT NULL REFERENCES market_data.source_registry(source_code),
    source_system TEXT NOT NULL CHECK (source_system ~ '^[A-Z][A-Z0-9_]{1,63}$'),
    source_artifact_hash BYTEA NOT NULL CHECK (octet_length(source_artifact_hash) = 32),
    source_record_count BIGINT NOT NULL CHECK (source_record_count >= 0),
    source_min_occurred_at_utc TIMESTAMPTZ,
    source_max_occurred_at_utc TIMESTAMPTZ,
    source_reconciliation_hash BYTEA CHECK (
        source_reconciliation_hash IS NULL
        OR octet_length(source_reconciliation_hash) = 32
    ),
    archive_fact_count BIGINT CHECK (archive_fact_count IS NULL OR archive_fact_count >= 0),
    archive_min_occurred_at_utc TIMESTAMPTZ,
    archive_max_occurred_at_utc TIMESTAMPTZ,
    archive_reconciliation_hash BYTEA CHECK (
        archive_reconciliation_hash IS NULL
        OR octet_length(archive_reconciliation_hash) = 32
    ),
    imported_revision_count BIGINT NOT NULL DEFAULT 0 CHECK (imported_revision_count >= 0),
    duplicate_revision_count BIGINT NOT NULL DEFAULT 0 CHECK (duplicate_revision_count >= 0),
    quarantined_revision_count BIGINT NOT NULL DEFAULT 0 CHECK (quarantined_revision_count >= 0),
    status TEXT NOT NULL CHECK (status IN ('RUNNING','RECONCILED','FAILED')),
    started_at_utc TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    completed_at_utc TIMESTAMPTZ,
    UNIQUE (source_code, source_system, source_artifact_hash),
    CHECK (
        (source_min_occurred_at_utc IS NULL AND source_max_occurred_at_utc IS NULL)
        OR
        (source_record_count > 0
         AND source_min_occurred_at_utc IS NOT NULL
         AND source_max_occurred_at_utc IS NOT NULL
         AND source_max_occurred_at_utc >= source_min_occurred_at_utc)
    ),
    CHECK (
        (status = 'RUNNING' AND completed_at_utc IS NULL)
        OR (status IN ('RECONCILED','FAILED') AND completed_at_utc IS NOT NULL)
    )
);

CREATE INDEX history_import_batches_source_idx
ON market_data.history_import_batches(source_code, started_at_utc DESC);

CREATE TABLE market_data.history_import_items (
    import_batch_id BYTEA NOT NULL REFERENCES market_data.history_import_batches(import_batch_id),
    source_position BIGINT NOT NULL CHECK (source_position >= 0),
    source_code TEXT NOT NULL REFERENCES market_data.source_registry(source_code),
    logical_identity_hash BYTEA NOT NULL CHECK (octet_length(logical_identity_hash) = 32),
    source_revision INTEGER NOT NULL CHECK (source_revision > 0),
    record_hash BYTEA NOT NULL CHECK (octet_length(record_hash) = 32),
    fact_id BYTEA NOT NULL REFERENCES market_data.market_facts(fact_id),
    archive_fact_revision INTEGER NOT NULL CHECK (archive_fact_revision > 0),
    import_disposition TEXT NOT NULL CHECK (import_disposition IN ('IMPORTED','DUPLICATE')),
    imported_at_utc TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (import_batch_id, source_position),
    UNIQUE (source_code, logical_identity_hash, source_revision)
);

CREATE INDEX history_import_items_fact_idx
ON market_data.history_import_items(fact_id, source_revision);

CREATE TABLE market_data.history_import_quarantine (
    import_batch_id BYTEA NOT NULL REFERENCES market_data.history_import_batches(import_batch_id),
    source_position BIGINT NOT NULL CHECK (source_position >= 0),
    source_code TEXT NOT NULL REFERENCES market_data.source_registry(source_code),
    record_hash BYTEA NOT NULL CHECK (octet_length(record_hash) = 32),
    reason_code TEXT NOT NULL CHECK (reason_code ~ '^[A-Z][A-Z0-9_]{1,95}$'),
    logical_identity_hash BYTEA CHECK (
        logical_identity_hash IS NULL OR octet_length(logical_identity_hash) = 32
    ),
    source_revision INTEGER CHECK (source_revision IS NULL OR source_revision > 0),
    quarantined_at_utc TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (import_batch_id, source_position)
);

CREATE INDEX history_import_quarantine_reason_idx
ON market_data.history_import_quarantine(source_code, reason_code, quarantined_at_utc DESC);

INSERT INTO market_data.schema_migrations(version, name)
VALUES (2, 'history_backfill_lineage');

COMMIT;
