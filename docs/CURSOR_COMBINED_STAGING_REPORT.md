# Combined Staging Report — Offer Overtime + Coin Intelligence

Date: 2026-08-05  
Writer branch: `candidate/combined-staging-overtime-coin` @ `f806c843`  
Production mutation: **none**

## 1. Branch / merge analysis

| Branch | HEAD | Role |
| --- | --- | --- |
| `candidate/offer-overtime` | `0e331c97` | وقت اضافه (Stages 0–16 harness) |
| `candidate/coin-commodity-inference-promotion` | `0fbd6a1b` | Coin staging candidate + handoff prompt |
| `candidate/coin-price-intelligence` | `b174c0c4` | Second coin candidate (parallel eval, not merged) |
| `candidate/combined-staging-overtime-coin` | `f806c843` | **Staging integration line** |

Merge bases:

- overtime ∩ coin-commodity = `540b2c0c` (`main`-line baseline used by overtime Stage 0)
- coin-price ∩ coin-commodity = `f81d2c8e`
- Unique commits: overtime +46 / coin-commodity +62 from `540b2c0c`; coin-price has +157 vs commodity, commodity has +67 vs coin-price

Worktrees:

- `/root/trading-bot/trading_bot` → `candidate/offer-overtime`
- `/root/trading-bot/coin-commodity-inference-promotion` → coin-commodity
- `/tmp/coin-price-intelligence` → coin-price
- `/root/trading-bot/combined-staging-overtime-coin` → combined staging line

## 2. File overlap and merge outcome

Paths changed on **both** overtime and coin-commodity (auto-merged cleanly):

- `api/routers/offers.py`
- `bot/states.py`
- `core/config.py`
- `frontend/src/views/MarketView.vue`
- `models/offer.py`

Post-merge coexistence checks:

- Market/Settings still mount `OfferOvertimePreferencePanel`
- Market still mounts `CommodityInferenceSelectionModal`
- `Offer` retains overtime snapshot/marker columns
- Offers router retains lifecycle projection **and** coin inference preview/selection paths
- Coin flags remain off by default (`coin_intelligence_inference_*_enabled = False`)

## 3. Migration graph

Both features branched from `a274f5a6b8c9`:

- overtime: `b5d1… → c6e2… → d7f3… → e8a4b5c6d7e9`
- coin: `b2d4… → d3f7… → d4e8… → e5a1c4d7b2f9`

Combined head after empty merge revision:

- `f9b0c1d2e3a4` (parents: `e8a4b5c6d7e9`, `e5a1c4d7b2f9`)

Deploy rule for staging/main: **migration-first to `f9b0c1d2e3a4` on both peers before enabling either feature flag / overtime preference ≠ 0**.

## 4. Topology for combined staging

### Trading-bot two-server (وقت اضافه)

| Role | URL | Notes |
| --- | --- | --- |
| Iran | `https://staging.gold-trade.ir` | WebApp + Iran sync |
| Foreign | `https://staging.362514.ir` | Bot + foreign sync |

Current blocker (from Stage 16 preflight): Iran timeout; foreign `/foreign-sync` 502; local staging compose currently only `db`+`redis`.

Deploy candidate SHA for both peers once healthy: `f806c843` (or later tip of `candidate/combined-staging-overtime-coin`).

### Coin-intelligence (read-only market inputs)

Canonical store (do not write from eval runners):

`/srv/trading-bot/production-data/coin-intelligence/private-gold-live/market/market.sqlite3`

Bridge state:

`/srv/trading-bot/production-data/coin-intelligence/private-gold-live/staging/market-input-bridge.state.json`

Writer lock:

`/srv/trading-bot/production-data/coin-intelligence/private-gold-live/staging/.market-store-writer.lock`

Observed service state on this host (2026-08-05):

- `coin-intelligence-staging-market-input-bridge.timer` = active
- `coin-public-market-telegram.service` = active
- `trading-bot-private-gold-collector.timer` = active

Do **not** start duplicate collectors. Keep polling/delivery/auto-selection/promotion off for coin preview.

### Isolation rule (coin-price vs coin-commodity)

Per `docs/CURSOR_COMBINED_STAGING_HANDOFF_PROMPT.md`, do **not** merge the two coin candidates merely to compare them. Run them against identical cutoffs with isolated output roots/ports/namespaces. The trading-bot combined line merges only **coin-commodity + overtime**.

## 5. Market Store integrity snapshot

Read-only probe of canonical SQLite:

| Check | Result |
| --- | --- |
| Tables | `market_observations`, `market_source_checkpoints`, `market_store_metadata` |
| Observation rows | 901,380 |
| Duplicate `event_key` | **0** |
| Checkpoints | 6 rows present (private gold + melted/USD/XAU advancing) |
| Bridge state | `staging-market-input-bridge-v1`, updated `2026-08-05T13:08:03Z` |
| `source_code` mix | `MELTED_AGGREGATE`, `WALLEX_PUBLIC_API`, `MELTED_FLOW`, `USD_HERAT`, `XAUUSD`, `GROUP_HISTORICAL`, `GROUP_2`, `IME_REALTIME_BOARD`, `PRIVATE_GOLD_CHANNEL`, `GROUP_1`, `PRIVATE_GOLD_PAPER_MINUTE` — `GROUP_HISTORICAL` remains distinct from groups 1/2 |

Full chronological replay / side-by-side coin-price metrics are **not** declared complete in this report; they remain the next coin-eval session work under the handoff prompt, with isolated outputs.

## 6. Automated tests run on combined tip

Focused suite on `f806c843` (default feature flags off):

- overtime data model + Stage 16 acceptance harness
- staging market input bridge
- coin inference audit / market store / preview API
- offers public routes + create success

Result: **OK** (71 + follow-up 34/3 green; no production DB writes).

`git diff --check` notes a pre-existing blank-line warning in `docs/STAGING_RUNTIME_SECRET_HARDENING.md` (from coin branch); not introduced by the merge migration.

## 7. Recommendation

1. **Use `candidate/combined-staging-overtime-coin` as the single staging deploy SHA** for the trading-bot Iran/foreign pair once topology is healthy.
2. Keep coin preview flags default-off; enable only for isolated staging users after migration `f9b0c1d2e3a4`.
3. Keep all overtime users at `0` until Stage 16 acceptance scenarios pass on the combined SHA.
4. Keep `candidate/coin-price-intelligence` as a **parallel evaluation branch**, not part of the trading-bot merge line, until the handoff comparison report recommends otherwise.
5. **Do not merge to `main` yet.** Main promotion should be ordered and human-gated:

### Proposed main merge order (future)

1. Close Stage 16 execute on combined staging (overtime acceptance + coin shadow checks).
2. Land migrations to production peers migration-first (`f9b0c1d2e3a4`).
3. Merge `candidate/combined-staging-overtime-coin` → `main` (or merge overtime then coin-commodity sequentially if review prefers smaller PRs — both must include the merge revision).
4. Keep coin auto-selection / Telegram mutation paths off until separate promotion approval (`docs/COIN_INTELLIGENCE_MAIN_PROMOTION_ROADMAP.md` P7-G/H/I).
5. Expand overtime preferences only after monitored rollout (Stage 17).

## 8. Rollback

Trading-bot staging:

```bash
# redeploy previous known-good staging SHA on both peers
# do not downgrade overtime/coin schema if any live overtime/coin rows exist
```

Coin inputs:

- Stop only the eval/snapshot publishers you started.
- Never delete SQLite WAL/SHM or the canonical market store.
- Bridge/collector timers already own the writer lock; do not launch duplicates.

Combined branch abandon (if needed):

```bash
git worktree remove /root/trading-bot/combined-staging-overtime-coin
# branch tip f806c843 can remain for archaeology; do not push to main
```

## 9. Unresolved risks / next actions

| Item | Status |
| --- | --- |
| Iran staging app health | Blocked (timeout) |
| Foreign staging app/sync | Blocked (502; compose app not up) |
| Mutating overtime Stage 16 drivers | Not wired |
| Coin-price vs coin-commodity full replay compare | Pending separate session |
| Production readiness | **Not declared** |

### Immediate next commands (when staging peers are up)

```bash
cd /root/trading-bot/combined-staging-overtime-coin
# deploy this SHA migration-first to Iran + foreign staging
python3 scripts/run_staging_offer_overtime_acceptance.py --mode preflight \
  --expected-branch candidate/combined-staging-overtime-coin \
  --expected-release-sha "$(git rev-parse HEAD)"
# only after green preflight + human confirm:
# STAGING_OFFER_OVERTIME_ACCEPTANCE_CONFIRM=execute-staging-offer-overtime-acceptance \
# python3 scripts/run_staging_offer_overtime_acceptance.py --mode execute ...
```

## 10. Confirmation

- No production PostgreSQL business rows created
- No Telegram messages sent by this session
- No merge to `main`
- Combined staging integration branch prepared and tested at unit level
