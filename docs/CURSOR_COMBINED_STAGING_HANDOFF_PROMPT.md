# Cursor Handoff — Combined Staging Evaluation

You are taking over a controlled staging evaluation of two candidate branches
for the coin-price intelligence feature. Inspect both branches, connect them to
the same staging inputs, isolate their outputs, compare them fairly, and write a
complete report. Do not promote anything to production.

## Safety

- Start read-only. Never modify `main`, reset, rebase, force-push, or delete data.
- Never expose Telegram credentials, sessions, API keys, user identities, raw
  offer text, message IDs, URLs, or private payloads.
- Do not create real PostgreSQL Offers/Trades or send Telegram messages.
- Keep polling, delivery workers, auto-selection, and automatic promotion off.
- Never fabricate missing prices; preserve `MISSING`/`ABSTAIN`.
- Preserve live collectors; never delete SQLite WAL/SHM files.
- Historical replay must respect `available_at_utc`; backfill must not leak into
  the simulated past.

## Branch discovery

The handoff branch is `candidate/coin-commodity-inference-promotion`, HEAD
`e32f00b0` (`ops(coin-intelligence): serialize staging market writers`), normally
at `/root/trading-bot/coin-commodity-inference-promotion`.

First inspect `git branch --all`, `git worktree list`, `git status`, and
`git log`. Identify the second candidate branch from the actual repository; do
not guess its name. Record both worktrees, HEADs, merge base, unique commits,
dirty files, and overlapping files. If ambiguous, report before writing.

Read completely:

- `docs/COIN_INTELLIGENCE_STAGING_INPUT_BRIDGE.md`
- `docs/COIN_INTELLIGENCE_MAIN_PROMOTION_ROADMAP.md` (P7-G/P7-H/P7-I)
- `scripts/bridge_staging_market_inputs.py`
- `tests/test_staging_market_input_bridge.py`
- `deploy/coin_intelligence/systemd/coin-intelligence-staging-market-input-bridge.service`
- `deploy/coin_intelligence/systemd/coin-intelligence-staging-market-input-bridge.timer`
- `deploy/coin_intelligence/systemd/trading-bot-private-gold-collector.service.template`

Do not merge branches merely to run tests. If an integration worktree is
needed, keep it outside `main` and document the exact commit.

## Runtime staging inputs

Canonical store: `/srv/trading-bot/production-data/coin-intelligence/private-gold-live/market/market.sqlite3`

Bridge state: `/srv/trading-bot/production-data/coin-intelligence/private-gold-live/staging/market-input-bridge.state.json`

Legacy public/external source: `/srv/trading-bot-three-site-staging-data/coin-intelligence/apps/telegram-price-poc/data/market_prices.sqlite3`

Group conversation source: `/srv/trading-bot-three-site-staging-data/coin-intelligence/apps/coin-intelligence/data/conversation_events.sqlite3`

Expected canonical sources are `MELTED_AGGREGATE`, `MELTED_FLOW`, `USD_HERAT`,
`XAUUSD`, `WALLEX_PUBLIC_API`, `IME_REALTIME_BOARD`, `PRIVATE_GOLD_CHANNEL`,
`PRIVATE_GOLD_PAPER_MINUTE`, `GROUP_1`, `GROUP_2`, and `GROUP_HISTORICAL`.
`GROUP_HISTORICAL` must not be relabeled as group 1 or 2.

The stable `coin-public-market-telegram.service` supplies public data. The
duplicate public staging timer is intentionally disabled. Normally inspect:
`coin-public-market-telegram.service`,
`trading-bot-private-gold-collector.timer`,
`coin-intelligence-staging-market-input-bridge.timer`,
`coin-live-group-sync.service`, and
`coin-intelligence-staging-snapshot-publish.timer`.

Private collector and bridge share
`/srv/trading-bot/production-data/coin-intelligence/private-gold-live/staging/.market-store-writer.lock`;
the bridge also has an overlap lock. Do not start duplicate collectors.

## Combined-staging requirement

Both branches must read identical, read-only canonical inputs and identical
chronological cutoffs. Their snapshots, model weights, residual artifacts,
shadow outcomes, and audit files must be isolated by runtime root, port, or
explicit output namespace. No branch may write production PostgreSQL data.
Document the selected topology and rollback procedure.

## Required tests

1. Run bridge tests, all relevant coin-intelligence tests, syntax checks, and
   `git diff --check`.
2. Verify Market Store integrity, zero duplicate `event_key`, positive prices,
   valid units, privacy-safe attributes, expected sources, advancing
   checkpoints, live row growth, and correct group attribution.
3. Inspect collector, group-sync, bridge, and snapshot logs for locks,
   transport errors, stale checkpoints, and repeated inserts.
4. Run chronological replay. At each cutoff, only rows with
   `available_at_utc <= cutoff` may be features. Use identical cutoffs/targets
   for both branches.
5. Compare commodity detection, abstention, Imam/low-date separation,
   cash/future, physical/paper, Herat/USDT behavior, melted-gold order flow,
   regime/anchor selection, interval center/width/coverage, out-of-range
   rejection, error against trusted future observations, latency, determinism,
   privacy, and side effects.
6. Test market reopening, stale coin offers, and 10–30 minute periods without
   coin offers where data exists.
7. Run Web and Bot shadow checks: no Offer/Trade creation, no Telegram polling,
   no delivery, safe `MISSING`/`ABSTAIN`, no Imam fallback for ambiguity, no
   PostgreSQL business-row mutation, and no form-input loss on refresh.

## Required report

Write `tmp/CURSOR_COMBINED_STAGING_REPORT.md` containing branch/merge analysis,
file comparison, topology, exact services/ports/paths/flags, source counts and
freshness, checkpoints, integrity/privacy results, replay cutoffs and leakage
controls, side-by-side metrics, failure examples, shadow evidence, confirmation
of no production mutation, unresolved risks, fixes, recommendation, and exact
rollback commands.

Do not declare production readiness merely because tests pass. Promotion requires
multiple real staging sessions, acceptable interval coverage, correct
cash/future semantics, no privacy violations, no persistent source failures,
and explicit human approval.
