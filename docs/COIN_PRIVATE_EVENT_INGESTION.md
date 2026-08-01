# Private Telegram market-event ingestion

Status: repository-owned source, disabled and unscheduled by default
Branch: `candidate/coin-price-intelligence`

## Purpose

This slice versions the source code used to receive the three private JSON
event streams (`offer`, `trade`, and `coin`), preserve an idempotent raw/staging
boundary, extract coin-group offers and reply-chain trades, parse melted-gold
offers and delayed trade confirmations, and build data-minimized model inputs.
It does not include captured messages, databases, Telegram sessions, channel
IDs, credentials, sender peer IDs, trained artifacts, logs, or deployment
schedules.

The source lives in:

- `scripts/coin_intelligence_private_ingest/` for listener and pipeline jobs;
- `core/market_intelligence/group_offer_parser.py` for deterministic informal
  offer extraction;
- `core/market_intelligence/group_trade_parser.py` for reply-chain linking;
- `core/market_intelligence/conversation_quality.py` for deterministic label
  and crossed-book quality rules;
- `core/market_intelligence/private_ingest_docs/` for the final-data and
  exclusion contracts.

## Runtime boundary

All mutable state must be outside the checkout. Configuration starts from
`config/coin-private-event-ingest.env.example`. The implementation rejects a
runtime root inside the repository. Private channel IDs and their forward-only
activation boundaries are supplied as JSON through
`COIN_PRIVATE_EVENT_CHANNELS_JSON`. Source-vintage message boundaries are also
runtime configuration and are intentionally absent from Git.

The listener writes one append-only JSONL archive per Tehran day and an atomic
cursor file. The ingest job then maintains separate raw-version and current-row
tables. A Telegram post may contain one JSON event, a JSON array, or multiple
complete JSON objects separated by the supported divider. Each inner event is
deduplicated by its own source/message identity. Cross-routed events remain in
the outer archive but cannot enter staging.

## Processing order

1. `listen_json_events.py` captures only messages after each configured
   boundary and continuously repairs missed forward messages.
2. `telegram_event_pipeline.py` validates stream/market/event-kind routing and
   updates raw plus text staging idempotently.
3. Group messages pass rules extraction, relevance filtering, conservative
   adjudication, field extraction, context validation, and exact reply-ID trade
   linking. Commodity price validation is causal and always runs, even when a
   commodity name is explicit. Unnamed offers may be resolved only from a
   compatible reply parent or decisive strictly-prior local price anchors from
   the same settlement/trade form. Explicit names are never silently rewritten;
   a strong name/price conflict, or an unresolved overlapping unnamed price,
   abstains before offer or linked-trade promotion.
4. Accepted group data is projected without private Telegram identifiers. A
   candidate copy is quality-annotated before the atomic data-only promotion
   job may replace the active conversation dataset.
5. Gold offers are parsed separately by physical/paper, today/tomorrow, and
   normal/reverse/swim. Delayed verifier updates retain the original offer and
   use edit time as trade time when that is the available evidence.
6. Gold lifecycle events retain individual physical offers/trades. Paper price
   aggregation gives confirmed trades greater weight than offers. Conditional
   offers remain available but are model-ineligible when their price fails the
   local market check.
7. Rebuildable raw/staging data has a three-day retention job. Active datasets,
   model artifacts, promotion backups, and ledgers are outside that deletion
   scope.

## Activation and promotion

Committing this source does not authorize a production/staging scheduler or
change the application’s authoritative offer parser. Every command is inert
until explicitly configured and scheduled. Model prediction is never a label;
only validated confirmed trades or reviewed labels may enter trusted training.
The three-site data plane and the project-authenticated operator UI remain
separate, deferred implementation slices.
