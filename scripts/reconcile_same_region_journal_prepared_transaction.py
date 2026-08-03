#!/usr/bin/env python3
"""Reconcile one WebApp-FI PostgreSQL prepared transaction safely.

This command does not transfer payloads and it never decrypts the opaque
Bot-FI record.  It accepts one GID printed by the fail-closed coordinator,
reads that exact signed journal state over the private FI path, and applies
the only matching PostgreSQL terminal action:

* Bot decision ``committed``   -> ``COMMIT PREPARED`` locally;
* Bot decision ``prepared``    -> record remote rollback, then local rollback;
* Bot decision ``rolled_back`` -> ``ROLLBACK PREPARED`` locally.

It deliberately refuses missing/unknown GIDs rather than touching an
unrelated prepared transaction.
"""

from __future__ import annotations

import argparse
import sys

from sqlalchemy import create_engine, text

from core.config import settings
from core.dr_durability_journal_client import (
    recover_prepared_journal_by_gid,
    rollback_prepared_journal_by_gid,
)
from core.dr_durability_journal import recovery_lookup_payload


class JournalRecoveryError(RuntimeError):
    pass


def action_for_remote_state(state: str) -> str:
    if state == "committed":
        return "commit"
    if state in {"prepared", "rolled_back"}:
        return "rollback"
    raise JournalRecoveryError("signed journal state is not a terminal recovery decision")


def _prepared_gid_exists(connection, *, gid: str) -> bool:  # noqa: ANN001
    return bool(
        connection.scalar(
            text(
                "SELECT 1 FROM pg_prepared_xacts "
                "WHERE database = current_database() AND gid = :gid"
            ),
            {"gid": gid},
        )
    )


def reconcile(*, gid: str, dry_run: bool) -> str:
    normalized_gid = str(recovery_lookup_payload(local_transaction_gid=gid)["local_transaction_gid"])
    engine = create_engine(settings.sync_database_url, pool_pre_ping=True)
    try:
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
            if not _prepared_gid_exists(connection, gid=normalized_gid):
                raise JournalRecoveryError("local PostgreSQL prepared transaction does not exist")
            remote = recover_prepared_journal_by_gid(local_transaction_gid=normalized_gid)
            action = action_for_remote_state(str(remote["state"]))
            if dry_run:
                return action
            if action == "rollback" and remote["state"] == "prepared":
                # Persist the remote decision first.  If its acknowledgement
                # is lost, this command leaves the local prepared transaction
                # intact; rerun will observe rolled_back and finish safely.
                rollback_prepared_journal_by_gid(local_transaction_gid=normalized_gid)
            statement = (
                f"COMMIT PREPARED '{normalized_gid}'"
                if action == "commit"
                else f"ROLLBACK PREPARED '{normalized_gid}'"
            )
            # The GID passed validation above and permits no SQL quoting
            # characters; driver SQL is needed because PREPARED grammar does
            # not accept a bind parameter in PostgreSQL.
            connection.exec_driver_sql(statement)
            return action
    finally:
        engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gid", required=True, help="exact GID from the coordinator error/alert")
    parser.add_argument("--dry-run", action="store_true", help="verify state without a terminal action")
    args = parser.parse_args()
    try:
        action = reconcile(gid=args.gid, dry_run=bool(args.dry_run))
    except Exception as exc:
        print(f"reconciliation_refused:{type(exc).__name__}", file=sys.stderr)
        return 2
    print(f"reconciliation_{'planned' if args.dry_run else 'completed'}:{action}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
