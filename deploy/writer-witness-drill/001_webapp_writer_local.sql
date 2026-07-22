BEGIN;

CREATE TABLE webapp_writer_state (
    authority VARCHAR(16) PRIMARY KEY,
    active_site VARCHAR(16),
    writer_epoch BIGINT NOT NULL,
    control_state VARCHAR(16) NOT NULL,
    transition_id VARCHAR(36) NOT NULL,
    readiness_evidence_hash VARCHAR(64),
    readiness_evidence_id VARCHAR(64),
    readiness_approved_by VARCHAR(128),
    readiness_approved_at TIMESTAMPTZ,
    readiness_expires_at TIMESTAMPTZ,
    witness_lease_id VARCHAR(64),
    witness_lease_issued_at TIMESTAMPTZ,
    witness_lease_expires_at TIMESTAMPTZ,
    witness_proof_hash VARCHAR(64),
    witness_transition_id VARCHAR(64),
    witness_local_boot_id VARCHAR(36),
    witness_local_boottime_deadline DOUBLE PRECISION,
    witness_observed_wall_at TIMESTAMPTZ,
    witness_observed_boottime DOUBLE PRECISION,
    witness_clock_offset_ms BIGINT,
    updated_by VARCHAR(128) NOT NULL,
    reason TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT ck_webapp_writer_state_authority CHECK (authority = 'webapp'),
    CONSTRAINT ck_webapp_writer_state_epoch_positive CHECK (writer_epoch >= 1),
    CONSTRAINT ck_webapp_writer_state_active_site CHECK (
        active_site IS NULL OR active_site IN ('webapp_fi', 'webapp_ir')
    ),
    CONSTRAINT ck_webapp_writer_state_control_state CHECK (
        control_state IN ('active', 'fenced', 'handoff')
    ),
    CONSTRAINT ck_webapp_writer_state_active_consistency CHECK (
        (control_state = 'active' AND active_site IS NOT NULL)
        OR (control_state <> 'active' AND active_site IS NULL)
    )
);

CREATE TABLE webapp_writer_transitions (
    transition_id VARCHAR(36) PRIMARY KEY,
    authority VARCHAR(16) NOT NULL,
    action VARCHAR(16) NOT NULL,
    previous_active_site VARCHAR(16),
    new_active_site VARCHAR(16),
    previous_epoch BIGINT NOT NULL,
    new_epoch BIGINT NOT NULL,
    operator VARCHAR(128) NOT NULL,
    reason TEXT NOT NULL,
    evidence_hash VARCHAR(64),
    witness_proof_hash VARCHAR(64),
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT ck_webapp_writer_transitions_action CHECK (
        action IN ('bootstrap', 'fence', 'activate', 'approve', 'handoff', 'lease_refresh')
    ),
    CONSTRAINT ck_webapp_writer_transitions_epoch CHECK (
        previous_epoch >= 1 AND new_epoch >= previous_epoch
    )
);

CREATE INDEX ix_webapp_writer_transitions_created_at
    ON webapp_writer_transitions (created_at);

COMMIT;
