from __future__ import annotations

from datetime import datetime, timezone
import re
import sqlite3
from zoneinfo import ZoneInfo


_TIME_ONLY_RE = re.compile(r"^(\d{1,2}):(\d{2}):(\d{2})$")


def _scalar(connection: sqlite3.Connection, query: str) -> int:
    row = connection.execute(query).fetchone()
    return int(row[0] if row else 0)


def _time_audit(connection: sqlite3.Connection) -> dict[str, int]:
    local_field_mismatches = 0
    source_time_samples = 0
    source_time_over_120_seconds = 0
    max_source_time_drift_seconds = 0

    rows = connection.execute(
        """
        SELECT event_time_utc, tehran_datetime, tehran_date, tehran_minute,
               tehran_weekday, tehran_weekday_name, source_datetime_text
        FROM price_events
        GROUP BY raw_post_id
        """
    )
    weekday_names = (
        "دوشنبه",
        "سه‌شنبه",
        "چهارشنبه",
        "پنج‌شنبه",
        "جمعه",
        "شنبه",
        "یکشنبه",
    )

    for row in rows:
        parsed = datetime.fromisoformat(row["event_time_utc"].replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        local = parsed.astimezone(ZoneInfo("Asia/Tehran"))
        expected = (
            local.isoformat(timespec="seconds"),
            local.date().isoformat(),
            local.strftime("%H:%M"),
            local.isoweekday(),
            weekday_names[local.weekday()],
        )
        actual = (
            row["tehran_datetime"],
            row["tehran_date"],
            row["tehran_minute"],
            row["tehran_weekday"],
            row["tehran_weekday_name"],
        )
        if actual != expected:
            local_field_mismatches += 1

        source_text = row["source_datetime_text"] or ""
        match = _TIME_ONLY_RE.fullmatch(source_text)
        if not match:
            continue
        source_time_samples += 1
        source_seconds = int(match.group(1)) * 3_600 + int(match.group(2)) * 60 + int(match.group(3))
        local_seconds = local.hour * 3_600 + local.minute * 60 + local.second
        drift = abs(source_seconds - local_seconds)
        drift = min(drift, 86_400 - drift)
        max_source_time_drift_seconds = max(max_source_time_drift_seconds, drift)
        if drift > 120:
            source_time_over_120_seconds += 1

    return {
        "local_field_mismatches": local_field_mismatches,
        "source_time_samples": source_time_samples,
        "source_time_over_120_seconds": source_time_over_120_seconds,
        "max_source_time_drift_seconds": max_source_time_drift_seconds,
    }


def build_audit_report(connection: sqlite3.Connection) -> dict[str, object]:
    connection.row_factory = sqlite3.Row
    raw_posts = _scalar(connection, "SELECT COUNT(*) FROM raw_posts")
    parsed_posts = _scalar(
        connection,
        "SELECT COUNT(*) FROM raw_posts WHERE parse_status = 'PARSED'",
    )
    ignored_posts = _scalar(
        connection,
        "SELECT COUNT(*) FROM raw_posts WHERE parse_status = 'IGNORED'",
    )
    checks = {
        "duplicate_raw_posts": _scalar(
            connection,
            """
            SELECT COUNT(*) FROM (
                SELECT source_code, message_id
                FROM raw_posts GROUP BY source_code, message_id HAVING COUNT(*) > 1
            )
            """,
        ),
        "duplicate_price_events": _scalar(
            connection,
            """
            SELECT COUNT(*) FROM (
                SELECT raw_post_id, event_index
                FROM price_events GROUP BY raw_post_id, event_index HAVING COUNT(*) > 1
            )
            """,
        ),
        "non_positive_prices": _scalar(
            connection,
            "SELECT COUNT(*) FROM price_events WHERE price_num <= 0",
        ),
        "structured_price_posts_unmatched": _scalar(
            connection,
            """
            SELECT COUNT(*) FROM raw_posts
            WHERE parse_status = 'UNMATCHED' AND (
                raw_text LIKE '%#مظنه%' OR raw_text LIKE '%#آبشده%'
                OR raw_text LIKE '%#ابشده%' OR raw_text LIKE '%#گرم%طلا%'
                OR raw_text LIKE '%#سکه%' OR raw_text LIKE '%#درهم%'
            )
            """,
        ),
        "duplicate_external_observations": _scalar(
            connection,
            """
            SELECT COUNT(*) FROM (
                SELECT instrument_code, observed_at_utc, interval_seconds, quote_kind
                FROM external_market_observations
                GROUP BY instrument_code, observed_at_utc, interval_seconds, quote_kind
                HAVING COUNT(*) > 1
            )
            """,
        ),
        "non_positive_external_raw_prices": _scalar(
            connection,
            "SELECT COUNT(*) FROM external_market_observations WHERE raw_price_num <= 0",
        ),
        "wallex_identity_conversion_mismatches": _scalar(
            connection,
            """
            SELECT COUNT(*)
            FROM external_market_observations
            WHERE instrument_code = 'USDT_IRT'
              AND ABS(raw_price_num - normalized_price_num) > 0.000001
            """,
        ),
    }
    time_checks = _time_audit(connection)
    checks.update(time_checks)

    instruments = [
        dict(row)
        for row in connection.execute(
            """
            SELECT instrument, COUNT(*) AS event_count,
                   MIN(price_num) AS min_price, MAX(price_num) AS max_price
            FROM price_events GROUP BY instrument ORDER BY event_count DESC
            """
        )
    ]
    external_instruments = [
        dict(row)
        for row in connection.execute(
            """
            SELECT external_instruments.code, external_instruments.source,
                   external_instruments.symbol, external_instruments.raw_unit,
                   external_instruments.normalized_unit,
                   COUNT(external_market_observations.id) AS observation_count,
                   MIN(external_market_observations.observed_at_utc) AS first_observation_utc,
                   MAX(external_market_observations.observed_at_utc) AS last_observation_utc,
                   MIN(external_market_observations.normalized_price_num) AS min_normalized,
                   MAX(external_market_observations.normalized_price_num) AS max_normalized
            FROM external_instruments
            LEFT JOIN external_market_observations
              ON external_market_observations.instrument_code = external_instruments.code
            GROUP BY external_instruments.code
            ORDER BY external_instruments.code
            """
        )
    ]
    runs = [
        dict(row)
        for row in connection.execute(
            """
            SELECT id, source_code, status, cutoff_utc, started_at, finished_at,
                   message_count, parsed_event_count, error_text
            FROM collection_runs
            ORDER BY id
            """
        )
    ]
    required_zero_checks = (
        "duplicate_raw_posts",
        "duplicate_price_events",
        "non_positive_prices",
        "structured_price_posts_unmatched",
        "local_field_mismatches",
        "source_time_over_120_seconds",
        "duplicate_external_observations",
        "non_positive_external_raw_prices",
        "wallex_identity_conversion_mismatches",
    )
    passed = all(int(checks[name]) == 0 for name in required_zero_checks)

    return {
        "passed": passed,
        "coverage": {
            "raw_posts": raw_posts,
            "parsed_posts": parsed_posts,
            "ignored_posts": ignored_posts,
            "unmatched_posts": raw_posts - parsed_posts - ignored_posts,
            "parsed_percent": round((parsed_posts / raw_posts * 100) if raw_posts else 0.0, 3),
            "price_events": _scalar(connection, "SELECT COUNT(*) FROM price_events"),
            "minute_prices": _scalar(connection, "SELECT COUNT(*) FROM minute_prices"),
        },
        "checks": checks,
        "instruments": instruments,
        "external_instruments": external_instruments,
        "runs": runs,
    }
