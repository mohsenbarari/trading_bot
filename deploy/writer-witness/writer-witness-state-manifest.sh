#!/bin/bash
set -Eeuo pipefail
set +x
[[ "$-" != *x* ]] || { echo "Writer Witness state manifest refuses shell tracing" >&2; exit 70; }

database_name="writer_witness"
output_mode="hash"
while [[ "$#" -gt 0 ]]; do
    case "$1" in
        --database)
            database_name="${2:-}"
            shift 2
            ;;
        --json)
            output_mode="json"
            shift
            ;;
        *)
            echo "usage: writer-witness-state-manifest [--database NAME] [--json]" >&2
            exit 2
            ;;
    esac
done

if [[ ! "$database_name" =~ ^writer_witness(_(candidate|rollback|failed)_(wwm_[0-9a-f]{12}_)?[0-9_]+)?$ ]]; then
    echo "unsafe writer witness database name" >&2
    exit 2
fi

canonical_stream() {
    LC_ALL=C runuser -u postgres -- psql -XAt -v ON_ERROR_STOP=1 -d "$database_name" <<'SQL'
SELECT json_build_object(
    'record', 'schema',
    'version_num', version_num,
    'installed_at', to_char(installed_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"')
)::text
FROM writer_witness_schema_version
ORDER BY version_num;

SELECT json_build_object(
    'record', 'state',
    'authority', authority,
    'holder_site', holder_site,
    'writer_epoch', writer_epoch,
    'lease_id', lease_id,
    'lease_status', lease_status,
    'issued_at', CASE WHEN issued_at IS NULL THEN NULL ELSE to_char(issued_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"') END,
    'expires_at', CASE WHEN expires_at IS NULL THEN NULL ELSE to_char(expires_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"') END,
    'transition_id', transition_id,
    'updated_by', updated_by,
    'reason', reason,
    'created_at', to_char(created_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
    'updated_at', to_char(updated_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"')
)::text
FROM webapp_writer_witness_state
ORDER BY authority;

SELECT json_build_object(
    'record', 'receipt',
    'request_id', request_id,
    'request_hash', request_hash,
    'action', action,
    'transition_id', transition_id,
    'response_sha256', encode(sha256(convert_to(response_json, 'UTF8')), 'hex'),
    'created_at', to_char(created_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"')
)::text
FROM webapp_writer_witness_receipts
ORDER BY request_id;

SELECT json_build_object(
    'record', 'failover_operation',
    'operation_id', operation_id,
    'operation_nonce', operation_nonce,
    'plan_hash', plan_hash,
    'status', status,
    'expires_at', to_char(expires_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
    'reservation_receipt_id', reservation_receipt_id,
    'reservation_receipt_hash', reservation_receipt_hash,
    'final_evidence_hash', final_evidence_hash,
    'finalized_at', CASE WHEN finalized_at IS NULL THEN NULL ELSE to_char(finalized_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"') END,
    'created_at', to_char(created_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
    'updated_at', to_char(updated_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"')
)::text
FROM dr_failover_operation_ledger
ORDER BY operation_id;
SQL

    # Keep the byte stream for a legacy schema-002 backup exactly unchanged.
    # That permits a guarded restore to prove the backup before it applies the
    # reviewed schema-003 migration.  Schema-003 manifests additionally bind
    # the relay receipt ledger below.
    local relay_table
    relay_table="$(runuser -u postgres -- psql -XAt -d "$database_name" -c \
        "SELECT to_regclass('public.human_approval_relay_receipts') IS NOT NULL")"
    if [[ "$relay_table" == "t" ]]; then
        LC_ALL=C runuser -u postgres -- psql -XAt -v ON_ERROR_STOP=1 -d "$database_name" <<'SQL'
-- Relay receipts are security-relevant durable evidence.  Include only their
-- immutable identifiers and digests, never a full approval subject or any
-- private session material.
SELECT json_build_object(
    'record', 'human_approval_relay_receipt',
    'request_id', request_id,
    'request_sha256', request_sha256,
    'approval_id', approval_id,
    'action', action,
    'subject_sha256', subject_sha256,
    'session_token_sha256', session_token_sha256,
    'receipt_sha256', encode(sha256(convert_to(receipt::text, 'UTF8')), 'hex'),
    'expires_at', to_char(expires_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
    'created_at', to_char(created_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"')
)::text
FROM human_approval_relay_receipts
ORDER BY request_id;
SQL
    fi
}

# PostgreSQL 15 supplies sha256(bytea), so the receipt body is represented only
# by its digest.  The whole ordered stream is then bound to one stable digest.
manifest_sha256="$(canonical_stream | sha256sum | awk '{print $1}')"
[[ "$manifest_sha256" =~ ^[0-9a-f]{64}$ ]] || {
    echo "failed to build writer witness manifest digest" >&2
    exit 1
}

if [[ "$output_mode" == "json" ]]; then
    state="$(runuser -u postgres -- psql -XAt -d "$database_name" -c \
        "SELECT authority || ':' || writer_epoch || ':' || lease_status FROM webapp_writer_witness_state")"
    receipts="$(runuser -u postgres -- psql -XAt -d "$database_name" -c \
        'SELECT count(*) FROM webapp_writer_witness_receipts')"
    operations="$(runuser -u postgres -- psql -XAt -d "$database_name" -c \
        'SELECT count(*) FROM dr_failover_operation_ledger')"
    printf '{"database":"%s","manifest_sha256":"%s","operation_count":%s,"receipt_count":%s,"state":"%s"}\n' \
        "$database_name" "$manifest_sha256" "$operations" "$receipts" "$state"
else
    printf '%s\n' "$manifest_sha256"
fi
