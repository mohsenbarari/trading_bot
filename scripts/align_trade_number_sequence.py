#!/usr/bin/env python3
"""Align the PostgreSQL trade-number sequence with this server's parity."""

from __future__ import annotations

import os

import psycopg2


TRADE_NUMBER_SEQUENCE_NAME = "trade_number_seq"
TRADE_NUMBER_INCREMENT = 2
TRADE_NUMBER_MINIMUM = {"foreign": 10000, "iran": 10001}


def normalize_database_url(raw_url: str) -> str:
    return raw_url.replace("postgresql+psycopg2://", "postgresql://").replace(
        "postgresql+asyncpg://", "postgresql://"
    )


def expected_trade_number_parity(server_mode: str) -> int:
    normalized = str(server_mode or "").strip().lower()
    if normalized not in TRADE_NUMBER_MINIMUM:
        raise RuntimeError("SERVER_MODE must be either 'iran' or 'foreign'")
    return TRADE_NUMBER_MINIMUM[normalized] % TRADE_NUMBER_INCREMENT


def plan_trade_number_alignment(
    *,
    server_mode: str,
    last_value: int,
    is_called: bool,
    max_partition_trade_number: int | None,
) -> tuple[int | None, int]:
    """Return ``(setval_target, effective_next_value)`` for a locked sequence."""

    normalized_mode = str(server_mode or "").strip().lower()
    parity = expected_trade_number_parity(normalized_mode)
    minimum = TRADE_NUMBER_MINIMUM[normalized_mode]
    current_next = int(last_value) + TRADE_NUMBER_INCREMENT if is_called else int(last_value)
    partition_max = int(max_partition_trade_number) if max_partition_trade_number is not None else minimum - 2

    if current_next >= minimum and current_next % TRADE_NUMBER_INCREMENT == parity and current_next > partition_max:
        return None, current_next

    # ``last_value`` is already consumed only when is_called is true. Keeping it
    # in the bound in that case prevents a deploy-time realignment from moving
    # behind a number handed out by a transaction that has not committed yet.
    sequence_bound = int(last_value) if is_called else int(last_value) - 1
    target = max(partition_max, sequence_bound, minimum - 2) + 1
    if target % TRADE_NUMBER_INCREMENT != parity:
        target += 1
    return target, target


def align_trade_number_sequence(database_url: str, server_mode: str) -> tuple[str, int]:
    normalized_mode = str(server_mode or "").strip().lower()
    parity = expected_trade_number_parity(normalized_mode)

    with psycopg2.connect(normalize_database_url(database_url), connect_timeout=10) as connection:
        with connection.cursor() as cursor:
            # ALTER takes an exclusive sequence lock for this transaction. The
            # subsequent state read and optional setval therefore cannot race a
            # concurrent nextval while the alignment decision is made.
            cursor.execute(
                f"ALTER SEQUENCE {TRADE_NUMBER_SEQUENCE_NAME} INCREMENT BY {TRADE_NUMBER_INCREMENT}"
            )
            cursor.execute(f"SELECT last_value, is_called FROM {TRADE_NUMBER_SEQUENCE_NAME}")
            last_value, is_called = cursor.fetchone()
            cursor.execute(
                "SELECT MAX(trade_number) FROM trades WHERE MOD(trade_number, %s) = %s",
                (TRADE_NUMBER_INCREMENT, parity),
            )
            max_partition_trade_number = cursor.fetchone()[0]
            target, effective_next = plan_trade_number_alignment(
                server_mode=normalized_mode,
                last_value=int(last_value),
                is_called=bool(is_called),
                max_partition_trade_number=max_partition_trade_number,
            )
            if target is not None:
                cursor.execute(
                    f"SELECT setval('{TRADE_NUMBER_SEQUENCE_NAME}', %s, false)",
                    (target,),
                )

    return ("realigned" if target is not None else "verified"), effective_next


def main() -> None:
    database_url = (os.environ.get("SYNC_DATABASE_URL") or "").strip()
    if not database_url:
        raise RuntimeError("SYNC_DATABASE_URL is required")
    server_mode = (os.environ.get("SERVER_MODE") or "").strip()
    action, next_value = align_trade_number_sequence(database_url, server_mode)
    print(
        "trade-number sequence "
        f"{action}: server_mode={server_mode.lower()} next_value={next_value} increment={TRADE_NUMBER_INCREMENT}"
    )


if __name__ == "__main__":
    main()
