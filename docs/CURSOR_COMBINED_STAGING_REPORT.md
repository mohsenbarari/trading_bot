# Combined Staging Report — Offer Overtime + Coin Intelligence

Date: 2026-08-05  
Writer branch: `candidate/combined-staging-overtime-coin` @ `d629f03b`  
Production mutation: **none**

> Section 11 holds the final pre-staging verification and the go/no-go decision.

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

---

## 11. Final pre-staging verification (`d629f03b`)

This section supersedes the provisional findings above. Every check below was run
on the combined branch, in an isolated environment, against real PostgreSQL where
schema behaviour was involved.

### 11.1 Merge integrity — no lost work

Five paths were changed by both parents. Diffing the merge result against each
parent proves the merge is additive in both directions:

| Path | Coin lines added on top of overtime | Overtime lines added on top of coin |
| --- | --- | --- |
| `api/routers/offers.py` | +429 / −15 | +147 / −44 |
| `frontend/src/views/MarketView.vue` | +88 / −5 | +13 / −1 |
| `core/config.py` | +11 | +5 / −1 |
| `models/offer.py` | +10 | +11 |
| `bot/states.py` | +1 | +5 |

Every deletion was inspected: they are the replaced halves of the same function
(overtime replaced the flat `expires_at_ts` computation with the lifecycle
projection; coin replaced the flat parse response with the inference-aware one).
No hunk from either feature was dropped. Both surfaces are still mounted —
`OfferOvertimePreferencePanel` and `CommodityInferenceSelectionModal` in
`MarketView.vue`, overtime lifecycle fields and coin inference preview/selection
in the offers router.

All 204 changed Python files compile.

### 11.2 Combined migration on real PostgreSQL

Scratch database `stage1_migration_combined_*` on the staging PostgreSQL
container. Both feature chains descend from `a274f5a6b8c9`, so the merge
revision `f9b0c1d2e3a4` is required for a single head.

| Step | Result |
| --- | --- |
| `upgrade head` (117 revisions incl. both features) | passed, head `f9b0c1d2e3a4` |
| Feature tables present | `coin_intelligence_market_outbox`, `coin_intelligence_inference_audits`, `coin_intelligence_inference_outcomes`, `offer_requests` |
| Overtime partial indexes present | all four |
| `users.offer_overtime_minutes` default | `0` |
| Delivery actions present | `overtime_owner_approval`, `overtime_channel_edit`, `final_tail_channel_edit` |
| `downgrade a274f5a6b8c9` | passed; coin tables and overtime indexes fully removed |
| `upgrade head` again | passed, head `f9b0c1d2e3a4` |

Rollback is therefore reversible **while no live overtime or coin rows exist**;
overtime's own downgrade guard still refuses to discard live decision evidence.

### 11.3 Runtime coupling between the two features

`models/offer.py` registers the coin market-outbox SQLAlchemy listener at import
time, with **no feature flag**. This is the only always-on coupling and it has
two consequences for staging:

1. `coin_intelligence_market_outbox` must exist before any Offer or Trade write.
   Migration-first is not a preference here, it is a hard requirement.
2. Dropping the coin tables while coin code is deployed would break every offer
   write. Roll back code before schema, never the reverse.

A probe on the scratch database exercised the overtime transitions against the
live listener:

| Overtime action | Outbox result |
| --- | --- |
| Offer created | one `OFFER_OPENED` row |
| `overtime_trade_committed = True` only | **no new row** — the overtime marker does not fabricate a market event |
| Terminal overtime expiry | `OFFER_EXPIRED` appended inside the same commit |
| Idempotency keys | all distinct |

Everything else coin adds to the app process is flag-gated and off by default
(`coin_intelligence_inference_preview_enabled`, `_selection_enabled`,
`_auto_selection_enabled`, `_snapshot_path`). Coin registers no background job in
the app; its collectors and bridge run as systemd units outside it.

### 11.4 Defects found and fixed on the integration branch

Three failures were real. All three were reproduced on the coin parent branch, so
the merge introduced none of them — but they would have failed the staging gate.

1. **Coin tables unregistered in the sync registry** (`c773a07d`).
   `test_registry_covers_every_current_model_table` failed for all three coin
   tables. They are now `NO_SYNC` local-only entries. The outbox in particular
   must never sync: the authoritative Offer/Trade row already reaches the peer, so
   copying the projection would double-count the same market event.
2. **The coin inference-choice state had no text-offer recovery path** (`d629f03b`).
   Coin stacked a second `@router.message` decorator on
   `handle_text_offer_while_confirmation_pending`, which registered a second
   handler ahead of the `awaiting_text_confirm` one and left the new state outside
   the recovery-state contract. Both states now go through one
   `_TEXT_OFFER_PENDING_CONFIRMATION_STATES` tuple used by the router and the test.
3. **Delivery callsite fingerprint drift** (`3a02952c`, refreshed in `d629f03b`).
   Coin inserted code above eight reviewed Telegram callsites. The 92 reviewed
   callsites are byte-for-byte identical in path, scope, callee, kind, and
   disposition — only line numbers moved — so the identity fingerprint was
   re-reviewed rather than the dispositions relaxed. Counts are unchanged and
   `remaining_interactive_direct` stays at 0.

### 11.5 Test evidence on `d629f03b`

| Suite | Result |
| --- | --- |
| Backend combined matrix (286 modules: Stage 15 list ∪ all coin ∪ shared seams) | **2154 tests, 178 skipped, OK** |
| Combined migration up / down / re-up on PostgreSQL | **passed** |
| Coin-outbox ↔ overtime coupling probe | **passed** |
| Frontend unit (132 files) | 1186 / 1188 passed |
| Frontend production build | **passed** (169 precache entries) |

Two environment caveats worth recording, because both produced misleading
failures during this review:

- **Shell environment pollution.** The first matrix run reported 49 failures and
  5 errors. The terminal exported `SERVER_MODE=iran`,
  `REGISTRATION_SYNC_V2_ENABLED=true`, `TELEGRAM_DIRECT_REGISTRATION_ENABLED=true`
  and others, and OS variables outrank the env file in pydantic-settings. Re-running
  under `env -i … APP_ENV_FILE=.env.test` left only the three genuine defects above.
  Always run the gate with an isolated environment.
- **Frontend flakiness under parallel load.** The two remaining `UserProfile.test.ts`
  failures are 5s timeouts; the file passes 16/16 in isolation. The overtime parent
  branch fails the same full suite with a *different* set (`PublicProfile`,
  `LoginView`), so the failing set moves between runs. This is pre-existing
  load-sensitive flakiness, not a merge effect.
- **Full 634-module discovery run.** Excluded from the gate: it stalls for tens of
  minutes inside the heavy infrastructure/matrix/mutation suites, which are
  unrelated to either feature. The 286-module curated matrix is the evidence of
  record, consistent with the Stage 15 pattern.

### 11.6 Go / no-go

**Go for staging deployment of `d629f03b`**, under these conditions:

1. Deploy migration-first to `f9b0c1d2e3a4` on both peers before starting app code.
2. Keep every staging user at overtime `0` and all `coin_intelligence_inference_*`
   flags off; enable only for isolated test users afterwards.
3. Run the acceptance preflight with the combined branch name:

```bash
python3 scripts/run_staging_offer_overtime_acceptance.py --mode preflight \
  --expected-branch candidate/combined-staging-overtime-coin \
  --expected-release-sha "$(git rev-parse HEAD)"
```

**Still blocked, unchanged from section 9:** Iran staging times out and foreign
`/foreign-sync` returns 502, so neither the acceptance preflight nor `execute` can
pass yet. Restore both peers first.

**Not ready for `main`.** Two items must close first: the Stage 16 acceptance
execute on real staging, and the coin-price vs coin-commodity comparison required
by the handoff prompt.

### 11.7 Watch items for the staging session

| Item | Why it matters |
| --- | --- |
| Outbox idempotency collisions | `idempotency_key` folds `version_id`; a retried transition that reuses a version would raise `IntegrityError` inside the offer transaction |
| Outbox growth under overtime expiry sweeps | Each swept offer appends a row; confirm the consumer keeps up |
| `expected_alembic_head` in the acceptance runner | Still pinned to the overtime head `e8a4b5c6d7e9`; on the combined line the head is `f9b0c1d2e3a4` |
| Coin flag flip with overtime active | Never validated together; enable coin preview only after overtime acceptance is green |
