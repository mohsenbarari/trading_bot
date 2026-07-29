"""Extend the DR delivery projection policy for first-attempt evidence.

Revision ID: c097d8e9f1a2
Revises: b986c7d8e0f1
"""

from __future__ import annotations

from alembic import op


revision = "c097d8e9f1a2"
down_revision = "b986c7d8e0f1"
branch_labels = None
depends_on = None


LOCK_PROJECTION_POLICY_SQL = r"""
LOCK TABLE
    public.dr_projection_table_allowlist,
    public.dr_projection_field_allowlist
IN SHARE ROW EXCLUSIVE MODE;
"""


EXTEND_PROJECTION_POLICY_SQL = r"""
DO $migration$
DECLARE
    delivery_column_count integer;
    delivery_table_policy_count integer;
    delivery_field_policy_count integer;
BEGIN
    SELECT count(*)
      INTO delivery_column_count
      FROM pg_catalog.pg_attribute attribute
      JOIN pg_catalog.pg_class relation
        ON relation.oid = attribute.attrelid
      JOIN pg_catalog.pg_namespace namespace
        ON namespace.oid = relation.relnamespace
     WHERE namespace.nspname = 'public'
       AND relation.relname = 'dr_event_deliveries'
       AND relation.relkind IN ('r', 'p')
       AND attribute.attname = 'first_attempt_at'
       AND attribute.attnum > 0
       AND attribute.attisdropped IS FALSE
       AND pg_catalog.format_type(
               attribute.atttypid,
               attribute.atttypmod
           ) = 'timestamp with time zone'
       AND attribute.attnotnull IS FALSE
       AND attribute.atthasdef IS FALSE
       AND attribute.attidentity = ''
       AND attribute.attgenerated = '';
    IF delivery_column_count <> 1 THEN
        RAISE EXCEPTION
            'canonical dr_event_deliveries.first_attempt_at column is missing';
    END IF;

    SELECT count(*)
      INTO delivery_table_policy_count
      FROM public.dr_projection_table_allowlist
     WHERE table_name = 'dr_event_deliveries';
    IF delivery_table_policy_count <> 1 THEN
        RAISE EXCEPTION
            'canonical dr_event_deliveries projection table policy is missing';
    END IF;

    SELECT count(*)
      INTO delivery_field_policy_count
      FROM public.dr_projection_field_allowlist
     WHERE table_name = 'dr_event_deliveries'
       AND column_name = 'first_attempt_at';
    IF delivery_field_policy_count <> 0 THEN
        RAISE EXCEPTION
            'dr_event_deliveries.first_attempt_at projection policy already exists';
    END IF;

    INSERT INTO public.dr_projection_field_allowlist (
        table_name,
        column_name
    ) VALUES (
        'dr_event_deliveries',
        'first_attempt_at'
    );

    SELECT count(*)
      INTO delivery_field_policy_count
      FROM public.dr_projection_field_allowlist
     WHERE table_name = 'dr_event_deliveries'
       AND column_name = 'first_attempt_at';
    IF delivery_field_policy_count <> 1 THEN
        RAISE EXCEPTION
            'dr_event_deliveries.first_attempt_at projection policy was not installed';
    END IF;
END;
$migration$;
"""


def upgrade() -> None:
    op.execute(LOCK_PROJECTION_POLICY_SQL)
    op.execute(EXTEND_PROJECTION_POLICY_SQL)


def downgrade() -> None:
    raise RuntimeError(
        "c097d8e9f1a2 is a forward-only projection policy migration; "
        "use the reviewed restore/forward-rollback runbook"
    )
