BEGIN;

-- The two public melted-gold channels are now part of the permanent research
-- corpus. Existing rows are promoted in place; their parsed economics and
-- provenance do not change.
UPDATE market_data.source_registry
SET permanent_archive=TRUE
WHERE source_code IN ('MELTED_AGGREGATE','MELTED_FLOW');

UPDATE market_data.source_registry
SET upstream_schema_version='2.1'
WHERE source_code IN ('GROUP_1','GROUP_2');

UPDATE market_data.market_facts
SET retention_class='PERMANENT',purge_after_utc=NULL
WHERE source_code IN ('MELTED_AGGREGATE','MELTED_FLOW')
  AND retention_class='LIVE_3D';

CREATE TABLE market_data.research_raw_messages (
    raw_message_key BYTEA NOT NULL CHECK (octet_length(raw_message_key) = 32),
    plaintext_hash BYTEA NOT NULL CHECK (octet_length(plaintext_hash) = 32),
    source_code TEXT NOT NULL REFERENCES market_data.source_registry(source_code),
    occurred_at_utc TIMESTAMPTZ NOT NULL,
    available_at_utc TIMESTAMPTZ NOT NULL,
    raw_kind TEXT NOT NULL CHECK (raw_kind IN ('OFFER_TEXT','SOURCE_TEXT')),
    ciphertext BYTEA NOT NULL,
    encryption_key_id TEXT NOT NULL,
    created_at_utc TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (raw_message_key, plaintext_hash),
    CHECK (available_at_utc >= occurred_at_utc)
);

CREATE INDEX research_raw_messages_source_time_idx
ON market_data.research_raw_messages(source_code, occurred_at_utc DESC);

CREATE TABLE market_data.research_fact_raw_messages (
    fact_id BYTEA NOT NULL,
    fact_revision INTEGER NOT NULL CHECK (fact_revision > 0),
    raw_message_key BYTEA NOT NULL,
    plaintext_hash BYTEA NOT NULL,
    raw_role TEXT NOT NULL CHECK (raw_role IN ('OFFER_TEXT','SOURCE_TEXT')),
    PRIMARY KEY (fact_id, fact_revision, raw_role),
    FOREIGN KEY (fact_id, fact_revision)
        REFERENCES market_data.market_fact_revisions(fact_id, fact_revision)
        ON DELETE CASCADE,
    FOREIGN KEY (raw_message_key, plaintext_hash)
        REFERENCES market_data.research_raw_messages(raw_message_key, plaintext_hash)
        ON DELETE CASCADE
);

ALTER TABLE market_data.market_fact_outbox
ADD COLUMN envelope_compacted_at_utc TIMESTAMPTZ;

CREATE INDEX market_fact_outbox_compaction_idx
ON market_data.market_fact_outbox(acknowledged_at_utc)
WHERE acknowledged_at_utc IS NOT NULL
  AND envelope_compacted_at_utc IS NULL;

INSERT INTO market_data.schema_migrations(version, name)
VALUES (3, 'research_archive_and_outbox_compaction');

COMMIT;
