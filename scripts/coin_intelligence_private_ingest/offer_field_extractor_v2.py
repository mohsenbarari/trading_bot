#!/usr/bin/env python3
"""Shadow v2 structured-field extractor for relevant Telegram group offers.

It improves only three observed text forms over the legacy parser:
* rial-like 9-digit prices (186.450.000 -> 186450 project units),
* compact low-date markers such as ``زیر ب 80``, and
* multiple price/side clauses in one message.

This script deliberately writes a separate staging database.  It does not alter
the first relevance decision, linked trades, or any production/model input.
"""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timezone

from core.market_intelligence.group_commodity_context import (
    commodity_context_requires_abstention,
    is_strong_contextual_resolution,
    resolve_offer_commodity,
)
from core.market_intelligence.group_offer_parser import (
    extract_single_offer,
    normalize_text,
    numeric_tokens,
    explicit_commodity,
)
from scripts.coin_intelligence_private_ingest.runtime_paths import PIPELINE_ROOT as PIPE

STAGE = PIPE / 'text_staging.sqlite3'
FILTER = PIPE / 'group_filter.sqlite3'
OUT = PIPE / 'offer_field_staging.sqlite3'
VERSION = 'group-offer-fields-shadow-v2.2-market-context'
GROUP_NUMBER = {'account2_group1': 1, 'account2_group2': 2}

SCHEMA = '''
CREATE TABLE IF NOT EXISTS offer_component_candidates (
 source_key TEXT NOT NULL,
 message_id TEXT NOT NULL,
 offer_index INTEGER NOT NULL,
 group_number INTEGER NOT NULL,
 extracted_json TEXT NOT NULL,
 extraction_status TEXT NOT NULL,
 extraction_confidence REAL NOT NULL,
 extractor_version TEXT NOT NULL,
 created_at_utc TEXT NOT NULL,
 PRIMARY KEY(source_key,message_id,offer_index)
);
CREATE TABLE IF NOT EXISTS offer_component_review_queue (
 source_key TEXT NOT NULL,
 message_id TEXT NOT NULL,
 reason TEXT NOT NULL,
 text TEXT NOT NULL,
 detail_json TEXT NOT NULL,
 extractor_version TEXT NOT NULL,
 created_at_utc TEXT NOT NULL,
 PRIMARY KEY(source_key,message_id,reason)
);
CREATE INDEX IF NOT EXISTS idx_offer_components_status
 ON offer_component_candidates(extraction_status,source_key);
CREATE TABLE IF NOT EXISTS offer_component_ignored (
 source_key TEXT NOT NULL,
 message_id TEXT NOT NULL,
 reason TEXT NOT NULL,
 text TEXT NOT NULL,
 extractor_version TEXT NOT NULL,
 created_at_utc TEXT NOT NULL,
 PRIMARY KEY(source_key,message_id,reason)
);
'''

NOW = lambda: datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00', 'Z')


def ignore_reason(text: str) -> str | None:
    value = normalize_text(text)
    # 403/404 (with or without 14, including slash forms) are deliberately
    # excluded until they have enough independent market data.
    if re.search(r'(?<!\d)(?:1403|1404|403|404)(?:\s*[/\\-]\s*(?:140)?[34])?(?!\d)', value):
        return 'EXCLUDED_403_404_COHORT'
    # Thursday/cashier is a special half-holiday market and outside scope.
    if re.search(r'(?:پنج|پن|5)\s*شنبه|کشیک', value):
        return 'EXCLUDED_THURSDAY_OR_CASHIER'
    return None


def prepare(text: str) -> str:
    """Normalise only unambiguous observed price/low-date forms."""
    value = normalize_text(text)
    # Group offers quote project-unit prices.  A 9-digit price with two thousand
    # separators is a rial rendering of the same rate, never a new 9-digit unit.
    value = re.sub(r'(?<!\d)([12]\d{2})\s*[./٬،,]\s*(\d{3})\s*[./٬،,]\s*(\d{3})(?!\d)',
                   lambda m: m.group(1) + m.group(2), value)
    # The legacy helper accepts a commodity suffix inside a Persian word.  Keep
    # only standalone coin words before handing text to it (e.g. ``می زنیم`` is
    # not the half-coin ``نیم``).
    value = re.sub(r'(?<=[\u0600-\u06FF])نیم', 'نـیم', value)
    value = re.sub(r'(?<=[\u0600-\u06FF])ربع', 'ربـع', value)
    # Common trader shorthand.
    value = re.sub(r'(?<![\u0600-\u06FF])رب(?![\u0600-\u06FF])', 'ربع', value)
    # In these exports a weekday after/alongside an offer denotes tomorrow
    # settlement.  The legacy parser otherwise mistakes a preceding price for
    # a clock/day number and excludes it.
    value = re.sub(r'(?:پنج\s*شنبه|5\s*شنبه|شنبه)', 'فردا', value)
    # Quantity sometimes touches the abbreviated side before the settlement.
    # Separating it prevents the quantity from competing with the later full
    # price (e.g. ``40ف فردا 187300``).
    value = re.sub(r'(?<!\d)(\d{1,3})([خف])\s*فردا\s*(\d{5,6})(?!\d)',
                   r'\1 تا \2 \3 فردا', value)
    # "زیر ب 80" / "بالا ب80" are community shorthand for low-date coins.
    value = re.sub(r'(?:زیر|بالا)\s*ب\s*80', 'تاریخ پایین', value)
    value = re.sub(r'(?<=(?:ربع|نیم)\s)ب\s*80', 'تاریخ پایین', value)
    return value


def price_token_spans(text: str) -> list[tuple[int, int]]:
    spans = []
    for token in numeric_tokens(text):
        raw = re.sub(r'\D', '', token.digits)
        # Clause splitting is deliberately limited to complete price tokens.
        # Short forms such as 94/500 are ambiguous with quantity and are already
        # handled conservatively by the legacy single-offer parser/anchor logic.
        if len(raw) >= 4:
            spans.append((token.start, token.end))
    return spans


def segments(text: str) -> list[str]:
    """Return conservative local clauses around each candidate price."""
    spans = price_token_spans(text)
    if len(spans) < 2:
        return [text]
    result = []
    for i, (start, end) in enumerate(spans):
        left = 0 if i == 0 else (spans[i - 1][1] + start) // 2
        # A quantity immediately before its price is a stronger clause boundary
        # than the midpoint.  This preserves the ``1 تا`` in messages that
        # contain two offers back-to-back.
        prefix = text[max(0, start - 36):start]
        quantities = list(re.finditer(r'(?<!\d)\d{1,3}\s*(?:د?تا|عدد)', prefix))
        if quantities and i > 0:
            # Do not let a midpoint cut through ``1 تا``.  It is safe to move
            # back to the last quantity only as far as the prior price ends.
            left = max(spans[i - 1][1], max(0, start - 36) + quantities[-1].start())
        right = len(text) if i + 1 == len(spans) else (end + spans[i + 1][0]) // 2
        # Keep enough neighbouring text for qty/side; prepend a globally
        # explicit commodity only when this segment has none.
        segment = text[left:right].strip()
        global_commodity = explicit_commodity(text)
        if global_commodity and not explicit_commodity(segment):
            segment = global_commodity + ' ' + segment
        result.append(segment)
    return result


def extract(text: str) -> tuple[list[dict], list[str]]:
    normal = prepare(text)
    parsed = []
    for fragment in segments(normal):
        item = extract_single_offer(fragment)
        if item is not None:
            item['source_fragment'] = fragment
            parsed.append(item)
    # Stable de-duplication: overlapping fragments may resolve identically.
    unique = []
    seen = set()
    for item in parsed:
        key = (item['commodity'], item['price'], item['side'], item.get('quantity'))
        if key not in seen:
            seen.add(key)
            unique.append(item)
    reasons = []
    if not unique:
        reasons.append('RELEVANT_TEXT_WITHOUT_COMPLETE_FIELDS')
    for item in unique:
        if item['side'] == 'UNKNOWN':
            reasons.append('UNKNOWN_SIDE')
        # Quantity is genuinely optional in the source contract.  Its absence
        # reduces usefulness for trade-volume learning, not field correctness.
        if float(item['confidence']) < 0.84:
            reasons.append('LOW_CONFIDENCE')
    return unique, sorted(set(reasons))


def comparable_fields(items: list[dict]) -> list[tuple]:
    return [
        (x.get('commodity'), x.get('price'), x.get('quantity'), x.get('side'), x.get('settlement'))
        for x in items
    ]


def event_epoch(value: str | None, day: str | None = None) -> float | None:
    raw = str(value or '')
    try:
        if 'T' in raw:
            return datetime.fromisoformat(raw.replace('Z', '+00:00')).timestamp()
        if ':' in raw and day:
            return datetime.fromisoformat(f'{day}T{raw}+00:00').timestamp()
    except ValueError:
        return None
    return None


def main() -> None:
    stage = sqlite3.connect(STAGE); stage.row_factory = sqlite3.Row
    filt = sqlite3.connect(FILTER); filt.row_factory = sqlite3.Row
    out = sqlite3.connect(OUT); out.executescript(SCHEMA)
    rows = stage.execute("""
      SELECT t.source_key,t.message_id,t.telegram_datetime,t.telegram_day,
             t.reply_message_id,t.text,t.extracted_json,f.decision
      FROM text_candidates t JOIN filterdb.filter_decisions f
        ON f.source_key=t.source_key AND f.message_id=t.message_id
      WHERE t.source_key IN ('account2_group1','account2_group2')
        AND f.decision LIKE 'KEEP%'
      ORDER BY COALESCE(t.telegram_datetime,''),t.source_key,t.message_id
    """).fetchall() if False else None
    # SQLite attachments are connection-local; use the filter DB as the driver.
    filt.execute('ATTACH DATABASE ? AS stage', (str(STAGE),))
    rows = filt.execute("""
      SELECT t.source_key,t.message_id,t.telegram_datetime,t.telegram_day,
             t.reply_message_id,t.text,t.extracted_json,f.decision
      FROM filter_decisions f JOIN stage.text_candidates t
        ON f.source_key=t.source_key AND f.message_id=t.message_id
      WHERE t.source_key IN ('account2_group1','account2_group2')
        AND f.decision LIKE 'KEEP%'
      ORDER BY COALESCE(t.telegram_datetime,''),t.source_key,t.message_id
    """).fetchall()
    unparsed_keys = {
        (r['source_key'], r['message_id'])
        for r in filt.execute("""
          SELECT source_key,message_id FROM trade_adjudications
          WHERE reason='OFFER_OR_TRADE_LIKE_TEXT_NOT_FULLY_PARSED'
        """)
    }
    out.execute('DELETE FROM offer_component_candidates')
    out.execute('DELETE FROM offer_component_review_queue')
    out.execute('DELETE FROM offer_component_ignored')
    summary = {'messages': 0, 'offers': 0, 'accepted': 0, 'review': 0, 'ignored': 0, 'by_group': {1: 0, 2: 0}}
    prior_offers: list[dict] = []
    offers_by_message: dict[tuple[str, str], list[dict]] = {}
    for row in rows:
        try:
            legacy_payload = json.loads(row['extracted_json'] or '{}')
        except json.JSONDecodeError:
            legacy_payload = {}
        legacy_kind = str(legacy_payload.get('kind') or '')
        # Relevance is broader than an offer: requests/acknowledgements remain
        # available to the trade linker but are never treated as field-parser
        # failures.  The only exception is a manually adjudicated unparsed offer.
        if not legacy_payload.get('offers') and legacy_kind not in {'OFFER_CANDIDATE', 'OFFER_SOURCE_CONFIRMED'} and (row['source_key'], row['message_id']) not in unparsed_keys:
            continue
        summary['messages'] += 1
        excluded = ignore_reason(row['text'])
        if excluded:
            out.execute('INSERT INTO offer_component_ignored VALUES(?,?,?,?,?,?)',
                        (row['source_key'],row['message_id'],excluded,row['text'],VERSION,NOW()))
            summary['ignored'] += 1
            continue
        offers, reasons = extract(row['text'])
        # A source-confirmed offer/relevant manual label may have no numeric
        # fields yet.  It stays in review rather than being discarded.
        if not offers:
            legacy = legacy_payload.get('offers') or []
            offers = legacy
        when = event_epoch(row['telegram_datetime'], row['telegram_day'])
        parent_key = (row['source_key'], str(row['reply_message_id'] or ''))
        parent_offers = offers_by_message.get(parent_key, [])
        if offers and when is not None:
            offers = [
                resolve_offer_commodity(
                    item,
                    as_of_epoch=when,
                    parent_offers=parent_offers,
                    prior_offers=prior_offers,
                )
                for item in offers
            ]
        if any(commodity_context_requires_abstention(item) for item in offers):
            statuses = {
                str(item.get('commodity_validation_status') or '')
                for item in offers
                if commodity_context_requires_abstention(item)
            }
            reasons.extend(sorted(statuses))
        legacy = legacy_payload.get('offers') or []
        # The legacy output is not a gold label, but conflicting structured
        # fields are unsafe weak labels.  Retain both through the source text
        # and force a review rather than silently choosing the newer parser.
        if (
            legacy
            and offers
            and comparable_fields(legacy) != comparable_fields(offers)
            and not all(is_strong_contextual_resolution(item) for item in offers)
        ):
            reasons.append('LEGACY_FIELD_DISAGREEMENT')
        if not offers:
            out.execute('INSERT INTO offer_component_ignored VALUES(?,?,?,?,?,?)',
                        (row['source_key'],row['message_id'],'UNPARSEABLE_NO_STRUCTURED_OFFER',row['text'],VERSION,NOW()))
            summary['ignored'] += 1
            continue
        status = 'SHADOW_ACCEPTED' if offers and not reasons else 'SHADOW_REVIEW'
        if status == 'SHADOW_ACCEPTED': summary['accepted'] += len(offers)
        else: summary['review'] += 1
        for index, item in enumerate(offers):
            payload = dict(item)
            payload['group_number'] = GROUP_NUMBER[row['source_key']]
            payload['source_text'] = row['text']
            out.execute('INSERT INTO offer_component_candidates VALUES(?,?,?,?,?,?,?,?,?)',
                        (row['source_key'],row['message_id'],index,GROUP_NUMBER[row['source_key']],
                         json.dumps(payload,ensure_ascii=False,separators=(',',':')),status,
                         float(item.get('confidence') or 0),VERSION,NOW()))
            summary['offers'] += 1; summary['by_group'][GROUP_NUMBER[row['source_key']]] += 1
        if status == 'SHADOW_ACCEPTED':
            offers_by_message[(row['source_key'], str(row['message_id']))] = [dict(item) for item in offers]
        provisional_context = all(
            str(item.get('commodity_method') or '') == 'price_inference'
            and str(item.get('commodity_validation_status') or '') == 'AMBIGUOUS_PRICE_CONTEXT'
            for item in offers
        )
        if (status == 'SHADOW_ACCEPTED' or provisional_context) and when is not None:
            for item in offers:
                if item.get('price') is None:
                    continue
                prior_offers.append({**dict(item), 'event_epoch': when})
        for reason in reasons:
            out.execute('INSERT OR REPLACE INTO offer_component_review_queue VALUES(?,?,?,?,?,?,?)',
                        (row['source_key'],row['message_id'],reason,row['text'],
                         json.dumps({'offer_count':len(offers)},ensure_ascii=False),VERSION,NOW()))
    out.commit()
    print(json.dumps({**summary, 'integrity':out.execute('PRAGMA integrity_check').fetchone()[0], 'version':VERSION},ensure_ascii=False))


if __name__ == '__main__':
    main()
