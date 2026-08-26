BEGIN;

CREATE SCHEMA IF NOT EXISTS market_data;

CREATE TABLE market_data.schema_migrations (
    version INTEGER PRIMARY KEY CHECK (version > 0),
    name TEXT NOT NULL UNIQUE,
    applied_at_utc TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);

INSERT INTO market_data.schema_migrations(version, name)
VALUES (1, 'market_archive_initial');

CREATE TABLE market_data.source_registry (
    source_code TEXT PRIMARY KEY CHECK (source_code ~ '^[A-Z][A-Z0-9_]{1,63}$'),
    capture_stream_id TEXT UNIQUE,
    fact_stream_id TEXT NOT NULL UNIQUE,
    source_family TEXT NOT NULL,
    upstream_schema TEXT NOT NULL,
    upstream_schema_version TEXT NOT NULL,
    parser_profile TEXT NOT NULL,
    capture_enabled BOOLEAN NOT NULL,
    permanent_archive BOOLEAN NOT NULL,
    raw_retention_seconds INTEGER NOT NULL CHECK (raw_retention_seconds BETWEEN 0 AND 604800),
    transfer_to_bot BOOLEAN NOT NULL,
    pii_classification TEXT NOT NULL CHECK (pii_classification IN ('NONE','LOW','MEDIUM','HIGH')),
    allowed_fact_kinds TEXT[] NOT NULL CHECK (cardinality(allowed_fact_kinds) > 0),
    CHECK ((capture_enabled AND capture_stream_id IS NOT NULL) OR NOT capture_enabled),
    CHECK (capture_stream_id IS NULL OR raw_retention_seconds = 259200)
);

INSERT INTO market_data.source_registry (
    source_code, capture_stream_id, fact_stream_id, source_family,
    upstream_schema, upstream_schema_version, parser_profile, capture_enabled,
    permanent_archive, raw_retention_seconds, transfer_to_bot,
    pii_classification, allowed_fact_kinds
) VALUES
('GROUP_1','capture.coin.group.1','market.fact.coin.group.1','TELEGRAM_GROUP','coin_group_event','2.0','COIN_GROUP',TRUE,TRUE,259200,TRUE,'HIGH',ARRAY['COIN_OFFER','COIN_TRADE']),
('GROUP_2','capture.coin.group.2','market.fact.coin.group.2','TELEGRAM_GROUP','coin_group_event','2.0','COIN_GROUP',TRUE,TRUE,259200,TRUE,'HIGH',ARRAY['COIN_OFFER','COIN_TRADE']),
('PRIVATE_GOLD_CHANNEL','capture.channel.private-gold','market.fact.private-gold','TELEGRAM_PRIVATE','market_channel_event','1.0','MELTED_PRIMARY',TRUE,TRUE,259200,TRUE,'MEDIUM',ARRAY['PRIVATE_GOLD_OFFER','PRIVATE_GOLD_OUTCOME']),
('USD_HERAT','capture.channel.usd-herat','market.fact.usd-herat','TELEGRAM_PUBLIC','market_channel_event','1.0','USD_HERAT',TRUE,TRUE,259200,TRUE,'LOW',ARRAY['OBSERVATION','EXTERNAL_QUOTE']),
('XAUUSD','capture.channel.xauusd','market.fact.xauusd','TELEGRAM_PUBLIC','market_channel_event','1.0','XAUUSD',TRUE,TRUE,259200,TRUE,'LOW',ARRAY['EXTERNAL_QUOTE']),
('WALLEX_PUBLIC_API','capture.api.wallex-usdt','market.fact.wallex-usdt','EXTERNAL_API','external_quote_event','1.0','WALLEX_USDT',TRUE,TRUE,259200,TRUE,'NONE',ARRAY['EXTERNAL_QUOTE']),
('BINANCE_PAXG_PUBLIC_API','capture.api.binance-paxg','market.fact.binance-paxg','EXTERNAL_API','external_quote_event','1.0','BINANCE_PAXG_PROXY',TRUE,TRUE,259200,TRUE,'NONE',ARRAY['EXTERNAL_QUOTE']),
('MELTED_AGGREGATE','capture.channel.melted-aggregate','market.fact.melted-aggregate','TELEGRAM_PUBLIC','market_channel_event','1.0','MELTED_AGGREGATE',TRUE,FALSE,259200,TRUE,'LOW',ARRAY['OBSERVATION']),
('MELTED_FLOW','capture.channel.melted-flow','market.fact.melted-flow','TELEGRAM_PUBLIC','market_channel_event','1.0','MELTED_FLOW',TRUE,FALSE,259200,TRUE,'LOW',ARRAY['OBSERVATION']),
('PRIVATE_GOLD_PAPER_MINUTE',NULL,'market.fact.private-gold-minute','DERIVED','derived_private_gold_minute','1.0','PRIVATE_GOLD_MINUTE',FALSE,TRUE,0,TRUE,'NONE',ARRAY['OBSERVATION']),
('IME_REALTIME_BOARD',NULL,'market.fact.ime-realtime','RESERVED','ime_quote','1.0','IME_RESERVED',FALSE,FALSE,0,FALSE,'NONE',ARRAY['OBSERVATION']);

CREATE TABLE market_data.stream_sequences (
    stream_id TEXT PRIMARY KEY,
    last_sequence BIGINT NOT NULL DEFAULT 0 CHECK (last_sequence >= 0),
    updated_at_utc TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);

CREATE TABLE market_data.capture_events (
    event_key BYTEA PRIMARY KEY CHECK (octet_length(event_key) = 32),
    upstream_event_id TEXT NOT NULL,
    source_code TEXT NOT NULL REFERENCES market_data.source_registry(source_code),
    stream_id TEXT NOT NULL,
    source_sequence BIGINT NOT NULL CHECK (source_sequence > 0),
    upstream_schema TEXT NOT NULL,
    upstream_schema_version TEXT NOT NULL,
    event_type TEXT NOT NULL,
    occurred_at_utc TIMESTAMPTZ NOT NULL,
    available_at_utc TIMESTAMPTZ NOT NULL,
    persisted_at_utc TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    payload_hash BYTEA NOT NULL CHECK (octet_length(payload_hash) = 32),
    raw_payload JSONB NOT NULL,
    contains_pii BOOLEAN NOT NULL,
    purge_after_utc TIMESTAMPTZ NOT NULL,
    UNIQUE (stream_id, source_sequence),
    CHECK (available_at_utc >= occurred_at_utc),
    CHECK (persisted_at_utc >= available_at_utc),
    CHECK (purge_after_utc > persisted_at_utc)
);

CREATE INDEX capture_events_purge_idx
ON market_data.capture_events(purge_after_utc);
CREATE INDEX capture_events_source_available_idx
ON market_data.capture_events(source_code, available_at_utc DESC);

CREATE TABLE market_data.capture_quarantine (
    quarantine_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_code TEXT REFERENCES market_data.source_registry(source_code),
    stream_id TEXT NOT NULL,
    source_sequence BIGINT,
    reason_code TEXT NOT NULL,
    payload_hash BYTEA NOT NULL CHECK (octet_length(payload_hash) = 32),
    payload_ciphertext BYTEA NOT NULL,
    encryption_key_id TEXT NOT NULL,
    first_seen_at_utc TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    last_seen_at_utc TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    occurrences INTEGER NOT NULL DEFAULT 1 CHECK (occurrences > 0),
    purge_after_utc TIMESTAMPTZ NOT NULL,
    UNIQUE (stream_id, source_sequence, payload_hash),
    CHECK (source_sequence IS NULL OR source_sequence > 0),
    CHECK (purge_after_utc > last_seen_at_utc)
);

CREATE INDEX capture_quarantine_purge_idx
ON market_data.capture_quarantine(purge_after_utc);

CREATE TABLE market_data.market_facts (
    fact_id BYTEA PRIMARY KEY CHECK (octet_length(fact_id) = 32),
    event_key BYTEA NOT NULL CHECK (octet_length(event_key) = 32),
    origin_event_key BYTEA NOT NULL CHECK (octet_length(origin_event_key) = 32),
    source_code TEXT NOT NULL REFERENCES market_data.source_registry(source_code),
    stream_id TEXT NOT NULL,
    source_sequence BIGINT NOT NULL CHECK (source_sequence > 0),
    occurred_at_utc TIMESTAMPTZ NOT NULL,
    available_at_utc TIMESTAMPTZ NOT NULL,
    persisted_at_utc TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    parser_version TEXT NOT NULL,
    fact_revision INTEGER NOT NULL CHECK (fact_revision > 0),
    fact_kind TEXT NOT NULL,
    quality_state TEXT NOT NULL CHECK (quality_state IN ('ELIGIBLE','REVIEW','REJECTED','AUDIT_ONLY')),
    quality_reason_codes TEXT[] NOT NULL DEFAULT '{}',
    payload_hash BYTEA NOT NULL CHECK (octet_length(payload_hash) = 32),
    payload JSONB NOT NULL,
    retention_class TEXT NOT NULL CHECK (retention_class IN ('PERMANENT','LIVE_3D')),
    purge_after_utc TIMESTAMPTZ,
    UNIQUE (stream_id, source_sequence),
    CHECK (available_at_utc >= occurred_at_utc),
    CHECK (persisted_at_utc >= available_at_utc),
    CHECK (
        (retention_class = 'PERMANENT' AND purge_after_utc IS NULL)
        OR (retention_class = 'LIVE_3D' AND purge_after_utc > persisted_at_utc)
    )
);

CREATE INDEX market_facts_snapshot_idx
ON market_data.market_facts(quality_state, source_code, available_at_utc DESC);
CREATE INDEX market_facts_purge_idx
ON market_data.market_facts(purge_after_utc)
WHERE purge_after_utc IS NOT NULL;
CREATE INDEX market_facts_payload_gin_idx
ON market_data.market_facts USING GIN(payload);

CREATE TABLE market_data.market_fact_revisions (
    fact_id BYTEA NOT NULL REFERENCES market_data.market_facts(fact_id) ON DELETE CASCADE,
    fact_revision INTEGER NOT NULL CHECK (fact_revision > 0),
    parser_version TEXT NOT NULL,
    quality_state TEXT NOT NULL,
    payload_hash BYTEA NOT NULL CHECK (octet_length(payload_hash) = 32),
    payload JSONB NOT NULL,
    revised_at_utc TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (fact_id, fact_revision)
);

CREATE TABLE market_data.market_fact_evidence (
    fact_id BYTEA NOT NULL REFERENCES market_data.market_facts(fact_id) ON DELETE CASCADE,
    evidence_ordinal SMALLINT NOT NULL CHECK (evidence_ordinal >= 0),
    evidence_kind TEXT NOT NULL,
    origin_event_key BYTEA NOT NULL CHECK (octet_length(origin_event_key) = 32),
    branch_digest BYTEA CHECK (branch_digest IS NULL OR octet_length(branch_digest) = 32),
    field_evidence JSONB NOT NULL DEFAULT '{}',
    PRIMARY KEY (fact_id, evidence_ordinal)
);

CREATE TABLE market_data.curated_raw_texts (
    fact_id BYTEA PRIMARY KEY REFERENCES market_data.market_facts(fact_id) ON DELETE CASCADE,
    ciphertext BYTEA NOT NULL,
    encryption_key_id TEXT NOT NULL,
    plaintext_hash BYTEA NOT NULL CHECK (octet_length(plaintext_hash) = 32),
    created_at_utc TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);

CREATE TABLE market_data.market_actor_identities (
    fact_id BYTEA NOT NULL REFERENCES market_data.market_facts(fact_id) ON DELETE CASCADE,
    actor_role TEXT NOT NULL CHECK (actor_role IN ('OFFERER','REQUESTER')),
    telegram_id_ciphertext BYTEA NOT NULL,
    telegram_id_lookup_hmac BYTEA NOT NULL CHECK (octet_length(telegram_id_lookup_hmac) = 32),
    display_name_ciphertext BYTEA,
    encryption_key_id TEXT NOT NULL,
    PRIMARY KEY (fact_id, actor_role)
);

CREATE INDEX market_actor_lookup_idx
ON market_data.market_actor_identities(telegram_id_lookup_hmac);

CREATE TABLE market_data.coin_offers (
    fact_id BYTEA PRIMARY KEY REFERENCES market_data.market_facts(fact_id) ON DELETE CASCADE,
    group_code SMALLINT NOT NULL CHECK (group_code IN (1,2)),
    instrument TEXT NOT NULL,
    side TEXT NOT NULL CHECK (side IN ('BUY','SELL')),
    settlement TEXT NOT NULL CHECK (settlement IN ('CASH','TOMORROW')),
    trade_form TEXT NOT NULL,
    offered_price NUMERIC(24,6) NOT NULL CHECK (offered_price > 0),
    price_unit TEXT NOT NULL,
    offered_quantity NUMERIC(24,6),
    quantity_unit TEXT,
    lifecycle_state TEXT NOT NULL,
    CHECK ((offered_quantity IS NULL) = (quantity_unit IS NULL))
);

CREATE TABLE market_data.coin_trade_outcomes (
    fact_id BYTEA PRIMARY KEY REFERENCES market_data.market_facts(fact_id) ON DELETE CASCADE,
    offer_fact_id BYTEA NOT NULL REFERENCES market_data.coin_offers(fact_id),
    outcome TEXT NOT NULL CHECK (outcome IN ('CONFIRMED_FULL','CONFIRMED_PARTIAL','REJECTED','AMBIGUOUS')),
    agreed_price NUMERIC(24,6),
    price_unit TEXT,
    agreed_quantity NUMERIC(24,6),
    quantity_unit TEXT,
    confirmed_at_utc TIMESTAMPTZ,
    CHECK (
        (outcome IN ('CONFIRMED_FULL','CONFIRMED_PARTIAL')
         AND agreed_price > 0 AND price_unit IS NOT NULL
         AND agreed_quantity > 0 AND quantity_unit IS NOT NULL
         AND confirmed_at_utc IS NOT NULL)
        OR
        (outcome IN ('REJECTED','AMBIGUOUS')
         AND agreed_price IS NULL AND price_unit IS NULL
         AND agreed_quantity IS NULL AND quantity_unit IS NULL)
    )
);

CREATE TABLE market_data.private_gold_offers (
    fact_id BYTEA PRIMARY KEY REFERENCES market_data.market_facts(fact_id) ON DELETE CASCADE,
    instrument TEXT NOT NULL CHECK (instrument = 'MELTED_GOLD_PRIVATE'),
    side TEXT NOT NULL CHECK (side IN ('BUY','SELL')),
    settlement TEXT NOT NULL,
    trade_form TEXT NOT NULL,
    offered_price NUMERIC(24,6) NOT NULL CHECK (offered_price > 0),
    price_unit TEXT NOT NULL CHECK (price_unit = 'TOMAN_PER_MESGHAL_750'),
    offered_quantity NUMERIC(24,6),
    quantity_unit TEXT,
    expires_at_utc TIMESTAMPTZ NOT NULL,
    CHECK ((offered_quantity IS NULL) = (quantity_unit IS NULL))
);

CREATE TABLE market_data.private_gold_outcomes (
    fact_id BYTEA PRIMARY KEY REFERENCES market_data.market_facts(fact_id) ON DELETE CASCADE,
    offer_fact_id BYTEA NOT NULL REFERENCES market_data.private_gold_offers(fact_id),
    outcome TEXT NOT NULL CHECK (outcome IN ('FULL','PARTIAL','NO_TRADE','AMBIGUOUS')),
    executed_quantity NUMERIC(24,6),
    remaining_quantity NUMERIC(24,6),
    quantity_unit TEXT,
    evidenced_at_utc TIMESTAMPTZ NOT NULL,
    CHECK (executed_quantity IS NULL OR executed_quantity >= 0),
    CHECK (remaining_quantity IS NULL OR remaining_quantity >= 0),
    CHECK (
        (executed_quantity IS NULL AND remaining_quantity IS NULL AND quantity_unit IS NULL)
        OR quantity_unit IS NOT NULL
    )
);

CREATE TABLE market_data.input_snapshots (
    input_snapshot_hash BYTEA PRIMARY KEY CHECK (octet_length(input_snapshot_hash) = 32),
    window_end_utc TIMESTAMPTZ NOT NULL,
    created_at_utc TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    selection_method TEXT NOT NULL,
    payload JSONB NOT NULL
);

CREATE TABLE market_data.input_snapshot_components (
    input_snapshot_hash BYTEA NOT NULL REFERENCES market_data.input_snapshots(input_snapshot_hash) ON DELETE CASCADE,
    feature_role TEXT NOT NULL,
    source_fact_id BYTEA REFERENCES market_data.market_facts(fact_id),
    consumed_value NUMERIC(24,8),
    consumed_unit TEXT,
    window_start_utc TIMESTAMPTZ NOT NULL,
    window_end_utc TIMESTAMPTZ NOT NULL,
    sample_count INTEGER NOT NULL CHECK (sample_count >= 0),
    selection_method TEXT NOT NULL,
    provenance JSONB NOT NULL DEFAULT '{}',
    PRIMARY KEY (input_snapshot_hash, feature_role)
);

CREATE TABLE market_data.inference_input_uses (
    inference_id BYTEA PRIMARY KEY CHECK (octet_length(inference_id) = 32),
    input_snapshot_hash BYTEA NOT NULL REFERENCES market_data.input_snapshots(input_snapshot_hash),
    model_version TEXT NOT NULL,
    settlement TEXT NOT NULL,
    inferred_at_utc TIMESTAMPTZ NOT NULL
);

CREATE TABLE market_data.market_fact_outbox (
    stream_id TEXT NOT NULL,
    delivery_sequence BIGINT NOT NULL CHECK (delivery_sequence > 0),
    fact_id BYTEA NOT NULL UNIQUE REFERENCES market_data.market_facts(fact_id),
    envelope JSONB NOT NULL,
    envelope_hash BYTEA NOT NULL CHECK (octet_length(envelope_hash) = 32),
    created_at_utc TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    next_attempt_at_utc TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    acknowledged_at_utc TIMESTAMPTZ,
    last_reason_code TEXT,
    PRIMARY KEY (stream_id, delivery_sequence)
);

CREATE INDEX market_fact_outbox_claim_idx
ON market_data.market_fact_outbox(stream_id, delivery_sequence)
WHERE acknowledged_at_utc IS NULL;

CREATE TABLE market_data.market_fact_delivery_checkpoints (
    stream_id TEXT PRIMARY KEY,
    highest_contiguous_sequence BIGINT NOT NULL DEFAULT 0 CHECK (highest_contiguous_sequence >= 0),
    last_batch_id BYTEA CHECK (last_batch_id IS NULL OR octet_length(last_batch_id) = 32),
    updated_at_utc TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);

CREATE TABLE market_data.estimator_snapshots (
    snapshot_id BYTEA PRIMARY KEY CHECK (octet_length(snapshot_id) = 32),
    snapshot_version BIGINT NOT NULL UNIQUE CHECK (snapshot_version > 0),
    generated_at_utc TIMESTAMPTZ NOT NULL,
    received_at_utc TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    input_snapshot_hash BYTEA NOT NULL CHECK (octet_length(input_snapshot_hash) = 32),
    model_version TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('OK','SAFE_NO_DATA','FAILURE')),
    payload JSONB NOT NULL,
    payload_hash BYTEA NOT NULL CHECK (octet_length(payload_hash) = 32)
);

CREATE TABLE market_data.review_items (
    review_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    fact_id BYTEA NOT NULL REFERENCES market_data.market_facts(fact_id),
    state TEXT NOT NULL CHECK (state IN ('OPEN','RESOLVED','DISMISSED')),
    ambiguous_fields TEXT[] NOT NULL,
    reason_codes TEXT[] NOT NULL,
    created_at_utc TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    resolved_at_utc TIMESTAMPTZ,
    resolved_by_user_id BIGINT,
    resolution JSONB,
    CHECK ((state = 'OPEN' AND resolved_at_utc IS NULL) OR state <> 'OPEN')
);

CREATE TABLE market_data.parser_corrections (
    correction_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    review_id BIGINT NOT NULL UNIQUE REFERENCES market_data.review_items(review_id),
    parser_version_before TEXT NOT NULL,
    corrected_fields JSONB NOT NULL,
    calibration_state TEXT NOT NULL CHECK (calibration_state IN ('CANDIDATE','VALIDATED','REJECTED','APPLIED')),
    created_at_utc TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    applied_parser_version TEXT
);

COMMIT;
