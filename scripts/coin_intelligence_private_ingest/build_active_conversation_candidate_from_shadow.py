#!/usr/bin/env python3
"""Reconcile accepted live-group staging into a copy of the active dataset.

The v1 importer was append-only, so later parser corrections could not remove
or relabel a previously imported row.  This builder replaces only imports
owned by the live-group pipeline, preserves every historical/manual import,
and emits a candidate for quality annotation and atomic promotion.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from scripts.coin_intelligence_private_ingest.runtime_paths import (
        CONVERSATION_DB as ACTIVE,
        PIPELINE_ROOT as PIPE,
    )
except ModuleNotFoundError:  # Standalone immutable runtime deployment.
    PIPE = Path(__file__).resolve().parent
    ACTIVE = PIPE.parents[1] / "apps/coin-intelligence/data/conversation_events.sqlite3"


COMPONENT = PIPE / "offer_field_staging.sqlite3"
RAW = PIPE / "raw_events.sqlite3"
STAGE = PIPE / "text_staging.sqlite3"
TRADES = PIPE / "trade_link_staging.sqlite3"
OUT = PIPE / "conversation_events.live-group-shadow.candidate.sqlite3"
REPORT = PIPE / "conversation_candidate_import.latest.json"
VERSION = "live-group-reconciled-v2.0"
OWNED_VERSION_PREFIXES = (
    "live-group-shadow-import-v1.",
    "live-group-reconciled-v2.",
)
GROUP_SOURCES = ("account2_group1", "account2_group2")


def sha256(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def now() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def integer(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (ValueError, TypeError):
        return None


def _atomic_report(payload: dict) -> None:
    temporary = REPORT.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.chmod(temporary, 0o600)
    temporary.replace(REPORT)


def _owned_import_clause() -> tuple[str, list[str]]:
    clauses = ["extractor_version LIKE ?" for _ in OWNED_VERSION_PREFIXES]
    return " OR ".join(clauses), [prefix + "%" for prefix in OWNED_VERSION_PREFIXES]


def _source_fingerprint(
    accepted: list[sqlite3.Row],
    eligible_trades: list[sqlite3.Row],
    raw_records: dict[tuple[str, str], dict],
    needed: set[tuple[str, str]],
    preserved_legacy: dict[str, list[dict]],
) -> str:
    digest = hashlib.sha256()
    for row in accepted:
        digest.update(
            json.dumps(
                [
                    row["source_key"],
                    row["message_id"],
                    row["offer_index"],
                    json.loads(row["extracted_json"]),
                ],
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        )
        digest.update(b"\n")
    for row in eligible_trades:
        digest.update(
            json.dumps(
                [
                    row["source_key"],
                    row["offer_message_id"],
                    row["request_message_id"],
                    row["confirmation_message_id"],
                    json.loads(row["trade_json"]),
                ],
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        )
        digest.update(b"\n")
    for key in sorted(needed):
        record = raw_records.get(key) or {}
        minimized = {
            "text": str(record.get("text") or ""),
            "telegram_datetime": record.get("telegram_datetime"),
            "reply_message_id": record.get("reply_message_id"),
        }
        digest.update(
            json.dumps(
                [*key, minimized],
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        )
        digest.update(b"\n")
    digest.update(
        json.dumps(
            preserved_legacy,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    )
    return digest.hexdigest()


def _table_count(connection: sqlite3.Connection, table: str) -> int:
    return int(connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0])


def main() -> None:
    for required in (ACTIVE, COMPONENT, RAW, STAGE, TRADES):
        if not required.is_file():
            raise RuntimeError(f"required database missing: {required}")
    component = sqlite3.connect(
        f"file:{COMPONENT.resolve()}?mode=ro", uri=True, timeout=30
    )
    component.row_factory = sqlite3.Row
    raw = sqlite3.connect(f"file:{RAW.resolve()}?mode=ro", uri=True, timeout=30)
    raw.row_factory = sqlite3.Row
    stage = sqlite3.connect(
        f"file:{STAGE.resolve()}?mode=ro", uri=True, timeout=30
    )
    stage.row_factory = sqlite3.Row
    trade = sqlite3.connect(
        f"file:{TRADES.resolve()}?mode=ro", uri=True, timeout=30
    )
    trade.row_factory = sqlite3.Row
    for connection in (component, raw, stage, trade):
        connection.execute("BEGIN")

    placeholders = ",".join("?" for _ in GROUP_SOURCES)
    raw_records = {
        (row["source_key"], row["message_id"]): json.loads(row["record_json"])
        for row in raw.execute(
            f"""SELECT source_key,message_id,record_json
            FROM source_messages_current WHERE source_key IN ({placeholders})""",
            GROUP_SOURCES,
        )
    }
    stage_times = {
        (row["source_key"], row["message_id"]): row["telegram_datetime"]
        for row in stage.execute(
            f"""SELECT source_key,message_id,telegram_datetime
            FROM text_candidates WHERE source_key IN ({placeholders})""",
            GROUP_SOURCES,
        )
    }
    accepted = component.execute(
        """SELECT * FROM offer_component_candidates
        WHERE extraction_status='SHADOW_ACCEPTED'
        ORDER BY source_key,message_id,offer_index"""
    ).fetchall()
    accepted_keys = {(row["source_key"], row["message_id"]) for row in accepted}
    current_component_business_keys = {
        (
            1 if row["source_key"] == "account2_group1" else 2,
            int(row["message_id"]),
        )
        for row in component.execute(
            """SELECT DISTINCT source_key,message_id
            FROM offer_component_candidates"""
        )
    }
    needed = set(accepted_keys)
    eligible_trades = []
    for row in trade.execute(
        """SELECT * FROM linked_confirmed_trades
        ORDER BY source_key,offer_message_id,confirmation_message_id"""
    ):
        key = (row["source_key"], str(row["offer_message_id"] or ""))
        if key not in accepted_keys:
            continue
        eligible_trades.append(row)
        for message in (row["request_message_id"], row["confirmation_message_id"]):
            if message is not None:
                needed.add((row["source_key"], str(message)))

    active_source_sha = sha256(ACTIVE)
    probe = sqlite3.connect(f"file:{ACTIVE.resolve()}?mode=ro", uri=True)
    probe.row_factory = sqlite3.Row
    owned_clause, owned_parameters = _owned_import_clause()
    owned_imports = probe.execute(
        f"SELECT id,archive_sha256,extractor_version FROM imports WHERE {owned_clause}",
        owned_parameters,
    ).fetchall()
    owned_ids = [int(row["id"]) for row in owned_imports]
    preserved_offers: dict[tuple[int, int, int], dict] = {}
    preserved_trades: dict[tuple[int, int, int], dict] = {}
    preserved_messages: dict[tuple[int, int], dict] = {}
    if owned_ids:
        owned_marks = ",".join("?" for _ in owned_ids)
        for row in probe.execute(
            f"""SELECT o.*,m.roles_json
            FROM offers AS o
            JOIN messages AS m
              ON m.import_id=o.import_id AND m.message_id=o.message_id
            WHERE o.import_id IN ({owned_marks})
            ORDER BY o.import_id,o.id""",
            owned_ids,
        ):
            group_number = int(
                json.loads(row["roles_json"] or "{}").get("group_number") or 0
            )
            business_key = (group_number, int(row["message_id"]))
            if business_key not in current_component_business_keys:
                preserved_offers[
                    (group_number, int(row["message_id"]), int(row["offer_index"]))
                ] = dict(row)
        for row in probe.execute(
            f"""SELECT t.*,m.roles_json
            FROM confirmed_trades AS t
            JOIN messages AS m
              ON m.import_id=t.import_id AND m.message_id=t.offer_message_id
            WHERE t.import_id IN ({owned_marks})
            ORDER BY t.import_id,t.id""",
            owned_ids,
        ):
            group_number = int(
                json.loads(row["roles_json"] or "{}").get("group_number") or 0
            )
            business_key = (group_number, int(row["offer_message_id"]))
            if business_key not in current_component_business_keys:
                preserved_trades[
                    (
                        group_number,
                        int(row["confirmation_message_id"]),
                        int(row["request_message_id"] or 0),
                    )
                ] = dict(row)
        preserved_needed = {
            (key[0], key[1]) for key in preserved_offers
        }
        for key, row in preserved_trades.items():
            group_number = key[0]
            preserved_needed.add((group_number, int(row["offer_message_id"])))
            if row["request_message_id"] is not None:
                preserved_needed.add((group_number, int(row["request_message_id"])))
            if row["confirmation_message_id"] is not None:
                preserved_needed.add(
                    (group_number, int(row["confirmation_message_id"]))
                )
        for row in probe.execute(
            f"""SELECT m.* FROM messages AS m
            WHERE m.import_id IN ({owned_marks})
            ORDER BY m.import_id,m.message_id""",
            owned_ids,
        ):
            group_number = int(
                json.loads(row["roles_json"] or "{}").get("group_number") or 0
            )
            key = (group_number, int(row["message_id"]))
            if key in preserved_needed:
                preserved_messages[key] = dict(row)

    preserved_fingerprint = {
        "offers": [
            {
                "key": key,
                "commodity": row["commodity"],
                "price": row["price"],
                "quantity": row["quantity"],
                "side": row["side"],
                "settlement": row["settlement"],
                "trade_form": row["trade_form"],
            }
            for key, row in sorted(preserved_offers.items())
        ],
        "trades": [
            {
                "key": key,
                "commodity": row["commodity"],
                "price": row["price"],
                "quantity": row["quantity"],
                "settlement": row["settlement"],
                "training_eligible": row["training_eligible"],
            }
            for key, row in sorted(preserved_trades.items())
        ],
    }
    source_fingerprint = _source_fingerprint(
        accepted,
        eligible_trades,
        raw_records,
        needed,
        preserved_fingerprint,
    )
    v1_exists = any(str(row["extractor_version"]).startswith("live-group-shadow-import-v1.") for row in owned_imports)
    current_v2 = [
        row
        for row in owned_imports
        if str(row["extractor_version"]).startswith("live-group-reconciled-v2.")
    ]
    if (
        not v1_exists
        and len(current_v2) == 1
        and str(current_v2[0]["archive_sha256"]) == source_fingerprint
    ):
        probe.close()
        for connection in (component, raw, stage, trade):
            connection.close()
        OUT.unlink(missing_ok=True)
        report = {
            "version": VERSION,
            "status": "NO_RECONCILIATION_CHANGE",
            "active_source_sha256": active_source_sha,
            "source_fingerprint_sha256": source_fingerprint,
            "accepted_offers": len(accepted),
            "eligible_confirmed_trades": len(eligible_trades),
        }
        _atomic_report(report)
        print(json.dumps(report, ensure_ascii=False))
        return
    probe.close()

    temporary = OUT.with_suffix(".sqlite3.tmp")
    temporary.unlink(missing_ok=True)
    shutil.copy2(ACTIVE, temporary)
    out = sqlite3.connect(temporary)
    out.row_factory = sqlite3.Row
    out.execute("PRAGMA foreign_keys=OFF")
    before_counts = {
        table: _table_count(out, table)
        for table in ("imports", "messages", "offers", "confirmed_trades")
    }
    old_live_offers: dict[tuple[int, int, int], tuple[str, int]] = {}
    for row in out.execute(
        f"""SELECT o.*,m.roles_json FROM offers AS o
        JOIN messages AS m
          ON m.import_id=o.import_id AND m.message_id=o.message_id
        JOIN imports AS i ON i.id=o.import_id
        WHERE {owned_clause.replace('extractor_version', 'i.extractor_version')}
        ORDER BY i.id,o.id""",
        owned_parameters,
    ):
        group_number = int(
            json.loads(row["roles_json"] or "{}").get("group_number") or 0
        )
        old_live_offers[
            (group_number, int(row["message_id"]), int(row["offer_index"]))
        ] = (str(row["commodity"]), int(row["price"]))

    out.execute("BEGIN IMMEDIATE")
    live_import_ids = owned_ids
    removed = {"imports": 0, "messages": 0, "offers": 0, "confirmed_trades": 0}
    if live_import_ids:
        marks = ",".join("?" for _ in live_import_ids)
        offer_ids = [
            int(row[0])
            for row in out.execute(
                f"SELECT id FROM offers WHERE import_id IN ({marks})",
                live_import_ids,
            )
        ]
        trade_ids = [
            int(row[0])
            for row in out.execute(
                f"SELECT id FROM confirmed_trades WHERE import_id IN ({marks})",
                live_import_ids,
            )
        ]
        if offer_ids:
            offer_marks = ",".join("?" for _ in offer_ids)
            out.execute(
                f"DELETE FROM offer_market_quality WHERE offer_id IN ({offer_marks})",
                offer_ids,
            )
        if trade_ids:
            trade_marks = ",".join("?" for _ in trade_ids)
            out.execute(
                f"DELETE FROM trade_market_quality WHERE trade_id IN ({trade_marks})",
                trade_ids,
            )
        for table in (
            "review_queue",
            "trade_requests",
            "confirmed_trades",
            "offers",
            "messages",
        ):
            cursor = out.execute(
                f"DELETE FROM {table} WHERE import_id IN ({marks})", live_import_ids
            )
            if table in removed:
                removed[table] = max(0, int(cursor.rowcount))
        cursor = out.execute(
            f"DELETE FROM imports WHERE id IN ({marks})", live_import_ids
        )
        removed["imports"] = max(0, int(cursor.rowcount))

    cursor = out.execute(
        """INSERT INTO imports(
          archive_path,archive_sha256,imported_at_utc,cutoff_utc,message_count,
          retained_message_count,dropped_message_count,extractor_version
        ) VALUES(?,?,?,?,?,?,?,?)""",
        (
            "private_telegram_group_shadow_reconciled",
            source_fingerprint,
            now(),
            now(),
            len(needed) + len(preserved_messages),
            len(needed) + len(preserved_messages),
            0,
            VERSION,
        ),
    )
    import_id = int(cursor.lastrowid)
    inserted_messages = 0
    inserted_message_keys: set[tuple[int, int]] = set()
    for source, message_id in sorted(needed):
        record = raw_records.get((source, message_id))
        if not record:
            continue
        group_number = 1 if source == "account2_group1" else 2
        event = str(
            stage_times.get((source, message_id))
            or record.get("telegram_datetime")
            or now()
        )
        out.execute(
            """INSERT INTO messages(
              import_id,message_id,event_time_utc,event_time_tehran,sender_hash,
              text,reply_to_message_id,source_html_file,roles_json,relevance_json
            ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (
                import_id,
                integer(message_id),
                event,
                event,
                None,
                str(record.get("text") or ""),
                integer(record.get("reply_message_id")),
                f"group_{group_number}",
                json.dumps({"group_number": group_number}),
                json.dumps({"source": "accepted_live_group_shadow_v2"}),
            ),
        )
        inserted_messages += 1
        inserted_message_keys.add((group_number, int(message_id)))
    for (group_number, message_id), row in sorted(preserved_messages.items()):
        if (group_number, message_id) in inserted_message_keys:
            continue
        out.execute(
            """INSERT INTO messages(
              import_id,message_id,event_time_utc,event_time_tehran,sender_hash,
              text,reply_to_message_id,source_html_file,roles_json,relevance_json
            ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (
                import_id,
                message_id,
                row["event_time_utc"],
                row["event_time_tehran"],
                row["sender_hash"],
                row["text"],
                row["reply_to_message_id"],
                row["source_html_file"],
                row["roles_json"],
                row["relevance_json"],
            ),
        )
        inserted_messages += 1
        inserted_message_keys.add((group_number, message_id))

    new_live_offers = {}
    for row in accepted:
        data = json.loads(row["extracted_json"])
        out.execute(
            """INSERT INTO offers(
              import_id,message_id,offer_index,commodity,price,quantity,side,
              settlement,trade_form,confidence,source_text,price_raw,price_method,
              commodity_method,quantity_method
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                import_id,
                integer(row["message_id"]),
                row["offer_index"],
                data["commodity"],
                int(data["price"]),
                data.get("quantity"),
                data["side"],
                data["settlement"],
                data["trade_form"],
                float(data["confidence"]),
                data.get("source_text") or "",
                data.get("price_raw"),
                data.get("price_method"),
                data.get("commodity_method"),
                data.get("quantity_method"),
            ),
        )
        new_live_offers[
            (int(row["group_number"]), int(row["message_id"]), int(row["offer_index"]))
        ] = (str(data["commodity"]), int(data["price"]))
    for key, row in sorted(preserved_offers.items()):
        out.execute(
            """INSERT INTO offers(
              import_id,message_id,offer_index,commodity,price,quantity,side,
              settlement,trade_form,confidence,source_text,price_raw,price_method,
              commodity_method,quantity_method
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                import_id,
                int(row["message_id"]),
                int(row["offer_index"]),
                row["commodity"],
                int(row["price"]),
                row["quantity"],
                row["side"],
                row["settlement"],
                row["trade_form"],
                float(row["confidence"]),
                row["source_text"],
                row["price_raw"],
                row["price_method"],
                row["commodity_method"],
                row["quantity_method"],
            ),
        )
        new_live_offers[key] = (str(row["commodity"]), int(row["price"]))

    for row in eligible_trades:
        data = json.loads(row["trade_json"])
        out.execute(
            """INSERT INTO confirmed_trades(
              import_id,confirmation_message_id,offer_message_id,
              request_message_id,event_time_utc,commodity,price,price_raw,
              price_method,quantity,quantity_method,reported_quantity,
              is_aggregate,training_eligible,side,settlement,trade_form,
              confidence,confirmation_type,evidence_json,context_json
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                import_id,
                integer(row["confirmation_message_id"]),
                integer(row["offer_message_id"]),
                integer(row["request_message_id"]),
                data["event_time_utc"],
                data["commodity"],
                int(data["price"]),
                data.get("price_raw"),
                data.get("price_method"),
                data.get("quantity"),
                data.get("quantity_method"),
                data.get("reported_quantity"),
                int(bool(data.get("is_aggregate"))),
                int(bool(data.get("training_eligible"))),
                data["side"],
                data["settlement"],
                data["trade_form"],
                float(data["confidence"]),
                data["confirmation_type"],
                json.dumps(data.get("evidence") or [], ensure_ascii=False),
                json.dumps({"status": data.get("status")}, ensure_ascii=False),
            ),
        )
    for _, row in sorted(preserved_trades.items()):
        out.execute(
            """INSERT INTO confirmed_trades(
              import_id,confirmation_message_id,offer_message_id,
              request_message_id,event_time_utc,commodity,price,price_raw,
              price_method,quantity,quantity_method,reported_quantity,
              is_aggregate,training_eligible,side,settlement,trade_form,
              confidence,confirmation_type,evidence_json,context_json
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                import_id,
                row["confirmation_message_id"],
                row["offer_message_id"],
                row["request_message_id"],
                row["event_time_utc"],
                row["commodity"],
                row["price"],
                row["price_raw"],
                row["price_method"],
                row["quantity"],
                row["quantity_method"],
                row["reported_quantity"],
                row["is_aggregate"],
                row["training_eligible"],
                row["side"],
                row["settlement"],
                row["trade_form"],
                row["confidence"],
                row["confirmation_type"],
                row["evidence_json"],
                row["context_json"],
            ),
        )
    out.commit()
    integrity = str(out.execute("PRAGMA integrity_check").fetchone()[0])
    after_counts = {
        table: _table_count(out, table)
        for table in ("imports", "messages", "offers", "confirmed_trades")
    }
    out.close()
    for connection in (component, raw, stage, trade):
        connection.close()
    if integrity != "ok":
        temporary.unlink(missing_ok=True)
        raise RuntimeError(integrity)
    os.chmod(temporary, 0o600)
    temporary.replace(OUT)

    old_keys = set(old_live_offers)
    new_keys = set(new_live_offers)
    report = {
        "version": VERSION,
        "status": "RECONCILIATION_CANDIDATE_READY",
        "active_source_sha256": active_source_sha,
        "candidate_sha256": sha256(OUT),
        "source_fingerprint_sha256": source_fingerprint,
        "import_id": import_id,
        "replaced_live_import_count": len(live_import_ids),
        "removed": removed,
        "inserted_messages": inserted_messages,
        "inserted_offers": len(accepted) + len(preserved_offers),
        "inserted_confirmed_trades": len(eligible_trades) + len(preserved_trades),
        "preserved_outside_staging_window": {
            "offers": len(preserved_offers),
            "confirmed_trades": len(preserved_trades),
            "messages": len(preserved_messages),
        },
        "offer_reconciliation": {
            "removed_keys": len(old_keys - new_keys),
            "added_keys": len(new_keys - old_keys),
            "corrected_values": sum(
                old_live_offers[key] != new_live_offers[key]
                for key in old_keys & new_keys
            ),
        },
        "before_counts": before_counts,
        "candidate_counts": after_counts,
        "integrity": integrity,
        "candidate": str(OUT),
    }
    _atomic_report(report)
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
