BEGIN;

DROP INDEX IF EXISTS market_data.market_fact_outbox_compaction_idx;
ALTER TABLE market_data.market_fact_outbox
DROP COLUMN IF EXISTS envelope_compacted_at_utc;
DROP TABLE IF EXISTS market_data.research_fact_raw_messages;
DROP TABLE IF EXISTS market_data.research_raw_messages;

UPDATE market_data.source_registry
SET permanent_archive=FALSE
WHERE source_code IN ('MELTED_AGGREGATE','MELTED_FLOW');

UPDATE market_data.source_registry
SET upstream_schema_version='2.0'
WHERE source_code IN ('GROUP_1','GROUP_2');

DELETE FROM market_data.schema_migrations WHERE version=3;

COMMIT;
