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

---

## 12. Staging brought up on the combined SHA (`3c8b3e07`)

Both peers now run the combined branch. Migration-first was enforced by the
compose `depends_on` chain, and both databases reached the merge head.

| Peer | Host | Services | Alembic head | Release |
| --- | --- | --- | --- | --- |
| Iran | `65.109.220.59` | `app` (healthy), `sync_worker`, `db`, `redis` | `f9b0c1d2e3a4` | `3c8b3e07` |
| Foreign | `65.109.216.187` | `foreign_app` (healthy), `bot`, `foreign_sync_worker`, `db`, `redis` | `f9b0c1d2e3a4` | `3c8b3e07` |

### 12.1 What had to be repaired first

- **Iran had no git repository.** `/srv/trading-bot/staging-iran` was a file
  snapshot whose `.git` pointed at a worktree admin directory that only exists on
  the foreign host. It was rebuilt in place from a `git bundle` of the combined
  branch (no push to GitHub). The broken pointer is kept as `.git.broken.<ts>` and
  `.env.staging` was backed up to `/root/staging-iran-env-backup-<ts>.env`.
- **The Iran public hostname does not point at the origin.**
  `staging.gold-trade.ir` resolves to an Arvan CDN edge (`185.143.234.238`,
  `185.143.233.238`) whose origin path is broken: it answers `504` after 15s.
  The origin itself is healthy — `--resolve` to `65.109.220.59` answers in 17ms
  with a valid certificate. The foreign host therefore pins the domain to the
  origin in `/etc/hosts`, exactly as compose already does for the containers via
  `extra_hosts`. `/etc/hosts` was backed up to `/root/hosts.backup-<ts>`.
  **The CDN origin configuration still needs fixing in the Arvan panel** before
  anyone reaches this staging WebApp from a browser.
- **Frontend dist was rebuilt** from the combined branch and shipped to both hosts,
  so the WebApp bundle contains both the overtime preference panel and the coin
  inference modal.

### 12.2 Verification after bring-up

| Check | Iran | Foreign |
| --- | --- | --- |
| `/api/config` with staging Basic Auth | 200 | — |
| Public surface is not a WebApp | — | 401 (guarded) |
| `POST /api/sync/receive` | 401 at app layer | 401 at app layer |
| `POST /api/trades/internal/execute` | 422 at app layer | 422 at app layer |
| `POST /api/offers/internal/expire` | 422 at app layer | 422 at app layer |
| `POST /api/sessions/internal/authority-check` | 422 at app layer | 422 at app layer |
| `/api/sync/health` | `status: ok`, `server_mode: iran` | `status: ok`, `server_mode: foreign` |
| Stage 14 `overtime_reconciliation` block | `ok`, zero findings | `ok`, zero findings |

No internal endpoint is behind Basic Auth on either peer, which is the condition
the two-server matrix design requires. Health snapshots are archived in
`tmp/combined-staging-evidence/staging-sync-health.json`.

### 12.3 Acceptance preflight now passes

`OT-ACC-COMBINED-PREFLIGHT`: **14 of 14 checks passed**, artifacts under
`tmp/staging-offer-overtime-acceptance/OT-ACC-COMBINED-PREFLIGHT/`.

The `expected_alembic_head` check was replaced while doing this. It used to assert
a pinned constant (`e8a4b5c6d7e9`, the overtime head) and therefore always passed
even though the deployed head is `f9b0c1d2e3a4`. It now resolves heads from the
checkout and fails when a line has more than one head — which is the actual risk
a two-feature merge introduces. Two regression tests cover it.

### 12.5 Live sync works, but the foreign staging database carries stale pollution

The foreign sync worker reaches Iran and Iran accepts the transport:

```
POST https://staging.gold-trade.ir/api/sync/receive "HTTP/1.1 200 OK"
```

The bot also started and polls Telegram as `@mbmtrading1_bot` with
`release_sha: 3c8b3e07`, and the offer-publication, trade-delivery, admin-broadcast
and notification-outbox workers all came up.

However four `users` change-log rows fail to apply on Iran:

| change_log id | table | op | record | created_at | state |
| --- | --- | --- | --- | --- | --- |
| 1281 | users | UPDATE | 86 | 2026-08-05 10:20:34Z | quarantined after 5 attempts |
| 1282 | users | UPDATE | 86 | 2026-08-05 10:21:58Z | retrying |
| 1283 | users | UPDATE | 86 | 2026-08-05 10:27:19Z | retrying |
| 1284 | users | UPDATE | 86 | 2026-08-05 11:16:53Z | retrying |

Iran logs `sync.apply_unexpected_error` with `error_type: ProgrammingError`,
`error_digest: 268ef289d84ee5ed`.

**This is not caused by the merge.** Three independent facts establish that:

1. All four rows were written between 10:20 and 11:16 today, hours before this
   deploy started (Iran app 14:25, foreign services 14:31).
2. The `users` table is byte-identical across both peers — 43 columns each,
   including `offer_overtime_minutes`. There is no schema mismatch.
3. The payload carries 38 keys and none of them is an overtime or coin field.

What it actually is: user 86 has `home_server = iran` on both peers, so these are
**foreign-authored updates to an Iran-authoritative user** — the exact
single-host pollution the staging README warns about when an Iran-mode `app` runs
on the foreign host. The fail-closed path behaved correctly: the row was
quarantined and nothing was applied on Iran.

Impact on Stage 16: the remaining three rows will quarantine the same way, so
`quarantined_change_log_count` will settle at 4 and add noise to sync-health and
parity evidence. Overtime scenarios themselves write fresh, correctly-authored
rows and are unaffected. Cleaning the four rows is an operator decision with
audit implications and was deliberately left untouched.

### 12.6 Production is untouched, and is 23 revisions behind

Checked read-only on the foreign production database (`trading_bot_db`) to confirm
the failing rows are staging-only:

| Fact | Production | Foreign staging |
| --- | --- | --- |
| `change_log` rows | 17,058 | 352 |
| unsynced | **0** | 4 |
| quarantined | **0** | 1 (rest will follow) |
| `users` columns | 42 | 43 |
| `offer_overtime_minutes` | absent | present |
| coin-intelligence tables | none | all three |
| overtime columns on `offer_requests` | none | present |
| Alembic head | `f2c7d8e9a0b1` | `f9b0c1d2e3a4` |

So the only `users` difference is production versus staging, and it is the expected
consequence of staging having taken the combined migration. The two staging peers
match each other exactly.

The Iran production stack (`trading_bot_db` on the Iran host, `SERVER_MODE=iran`)
was checked the same way and is equally clean: head `f2c7d8e9a0b1`, 6,864
change-log rows with zero unsynced and zero quarantined, 42 `users` columns, no
overtime column and no coin tables. Both production peers therefore agree with
each other and neither was touched.

Worth carrying into Stage 17: production is **23 revisions** behind the combined
head, not five. Promoting this line to production is a much larger migration step
than the overtime and coin chains alone, and needs its own plan.

### 12.7 First mutating scenario passes end to end

`OT-PREF-WEBAPP-SAVE` now runs for real against the live pair. The driver
(`scripts/staging_overtime_scenario_driver.py`) executes inside the Iran app
container and goes through `persist_overtime_preference`, the same authoritative
writer the WebApp endpoint calls.

| Assertion | Result |
| --- | --- |
| Owner is eligible (not accountant, not tier-2) | true |
| Requested 4 minutes, persisted | 4 |
| Approved success copy and reachability warning returned | yes |
| Value 11 refused by the writer, not just the UI | true |
| Change-log rows emitted for the user | 2 |
| Mirrored to the foreign peer within ~20s | user 1360, value 4, `home_server=iran` |
| Cleanup retired the user and propagated | `is_deleted=true` on foreign |

Evidence: `tmp/combined-staging-evidence/stage16-driver-OT-PREF-WEBAPP-SAVE.json`.

Three things the product refused along the way, all correctly:

1. A standalone script that skips `setup_event_listeners()` writes user rows that
   never enter the sync stream. The driver now registers them, which is why the
   change-log count is non-zero.
2. `SyncOutboxBypassError` blocked a bulk delete on `users` — a raw delete would
   leave the peer unaware.
3. `SyncOutboxError` then blocked an ORM delete, because hard-deleting a synced
   user emits no outbox row. Cleanup uses `delete_user_account`, the product's own
   flow, which already invalidates overtime state.

Authentication note for the remaining WebApp-facing scenarios: dev-login is
deliberately disabled on the Iran staging nginx
(`return 404; # Full Matrix: dev-login disabled after key rotation`), so drivers
run in-container rather than as external HTTP clients.

### 12.8 Preference + offer snapshot drivers (4/15)

Three more Iran-container drivers passed on the live pair after
`OT-PREF-WEBAPP-SAVE`. All mutate only `OTACC_*` synthetic users and retire them
through `delete_user_account`.

| Scenario | Iran assertion | Foreign mirror | Evidence |
| --- | --- | --- | --- |
| `OT-PREF-BOT-SAVE` | `save_overtime_preference_from_bot` persisted 3 | user 1368 → 3 within ~5s | `stage16-driver-OT-PREF-BOT-SAVE.json` |
| `OT-PREF-DISABLED-REGRESSION` | preference 0 → snapshot 0; after normal deadline phase `expired`, intake `rejected` | offer 233 snapshot 0 | `stage16-driver-OT-PREF-DISABLED-REGRESSION.json` |
| `OT-OFFER-WEBAPP-ORIGIN` | preference 5 frozen on offer; clearing preference leaves snapshot 5; public response has no private identity; overtime phase accepts approval only | offer 237 snapshot 5 | `stage16-driver-OT-OFFER-WEBAPP-ORIGIN.json` |

Notes kept honest:

- `OT-PREF-BOT-SAVE` covers the Iran path of the bot helper. Foreign-forward + M7
  unreachable behavior is still a follow-up.
- Offer creation uses the WebApp quota path (`OfferCreationQuotaPolicy`) so the
  snapshot freeze runs; market competitive-price validation is skipped so staging
  price state cannot flake the overtime contract.
- Acceptance `execute` now knows these four wired drivers. With confirm + green
  preflight + `STAGING_IRAN_SSH_HOST`, it runs them and returns `execute_partial`
  until the remaining 11 scenarios are wired. Without transport env it stays
  fail-closed.

### 12.9 Bot-origin offer driver (`OT-OFFER-BOT-ORIGIN`)

Registration sync v2 refuses foreign `users` INSERT outbox rows, so this
scenario is intentionally two-peer:

1. **Iran seed** — create owner with `home_server=foreign` (change-log emitted)
2. **Foreign run** — wait for user mirror → bot preference forward to Iran →
   wait for preference mirror → create `TELEGRAM_BOT` offer with quota freeze →
   clear preference and prove snapshot stays frozen → public lifecycle safe
3. **Iran cleanup** — `delete_user_account` retires the owner; foreign soft-delete
   and offer expiry converge

| Assertion | Result |
| --- | --- |
| Seed owner 1370, `home_server=foreign` | yes |
| Bot preference forward + foreign mirror to 5 | ~2s |
| Offer `ofr_7Gn2_…` snapshot 5, home foreign | yes |
| Preference cleared to 0; snapshot remains 5 | yes |
| Overtime phase accepts approval only | yes |
| Iran mirrored offer 482 snapshot 5 | ~5s |
| Cleanup propagated (`is_deleted` / offer `EXPIRED`) | yes |

Evidence: `tmp/combined-staging-evidence/stage16-driver-OT-OFFER-BOT-ORIGIN.json`.

This also exercises the real foreign bot preference path (signed forward + sync
mirror) that the earlier Iran-local `OT-PREF-BOT-SAVE` run could not cover alone.

### 12.10 Iran overtime request path (`OT-REQ-IRAN-TO-IRAN`)

End-to-end request workflow on an Iran-home offer, without waiting real wall-clock
lifetime: the driver backdates `created_at` into the overtime window, creates a
WebApp overtime request (promotes straight to `overtime_presented`), then the
owner rejects.

| Assertion | Result |
| --- | --- |
| Offer snapshot 5 after preference freeze | yes |
| Request `req_s7dN…` presented on Iran | yes |
| Owner reject → `overtime_rejected_by_owner` | yes |
| `offer_requests` change-log rows | 3 |
| Foreign mirror of terminal request | id 271 within ~5s |
| Cleanup retired owner + requester | 2 users |

Evidence: `tmp/combined-staging-evidence/stage16-driver-OT-REQ-IRAN-TO-IRAN.json`.

### 12.11 Requester cancel (`OT-CANCEL-REQUESTER`)

Iran WebApp overtime request is presented, then closed by `لغو درخواست`
(`cancel_by_requester`). The offer seat becomes free (`get_active_request_for_offer`
returns none) and the terminal row mirrors to foreign.

Evidence: `tmp/combined-staging-evidence/stage16-driver-OT-CANCEL-REQUESTER.json`.

### 12.12 Owner queue FIFO (`OT-QUEUE-ORDER`)

Same owner, two Iran-home offers, two requesters: first request presents, second
stays `overtime_queued` (one owner-occupying seat). Owner reject promotes the
queued row to `overtime_presented` in FIFO order (`queue_sequence` 1 then 2).

Evidence: `tmp/combined-staging-evidence/stage16-driver-OT-QUEUE-ORDER.json`.

### 12.13 Foreign bot request path (`OT-REQ-FOREIGN-TO-FOREIGN`)

Iran seeds foreign-home owner+requester with Telegram ids; foreign forwards the
preference, creates a bot offer, opens an overtime request that promotes to
`overtime_delivering`, simulates Telegram accept via `mark_presented`, then
owner reject. Request home stays `foreign` and mirrors to Iran.

Evidence: `tmp/combined-staging-evidence/stage16-driver-OT-REQ-FOREIGN-TO-FOREIGN.json`.

### 12.14 Final-tail remainder (`OT-FINAL-TAIL`)

Iran retail offer (lot size 2 of quantity 5) enters overtime, first request is
presented and owner-approved through the real trade commit path. Remainder stays
active with `overtime_trade_committed=true`. A second occupying request is then
held past the final deadline: without the hold the lifecycle is `expired` /
terminal-due; with the hold it is `final_tail`, public interaction closed, and
the channel overtime marker stays visible.

| Assertion | Result |
| --- | --- |
| Partial trade qty 2 → remaining 3, offer still `active` | yes |
| `overtime_trade_committed` after overtime approval | true |
| Second request occupies past final deadline | yes |
| Phase without hold / with hold | `expired` / `final_tail` |
| Terminal expiry deferred while occupied | yes |
| Channel marker visible in `final_tail` | yes |

Evidence: `tmp/combined-staging-evidence/stage16-driver-OT-FINAL-TAIL.json`.

### 12.15 Cross-server forward pending (`OT-REQ-CROSS-FORWARD`)

Two-peer path: Iran seeds an Iran-home overtime offer; just before the foreign
edge run, Iran re-pins the offer into overtime. On foreign:

1. Forced home-forward timeout (`504`) returns inventory M18
   (`⏳ در حال بررسی درخواست...`) with `workflow=forward_pending`, retains the
   Redis pending marker, queues reconcile, and creates **no** local ledger row.
2. Live forward reaches Iran and returns overtime intake (`202` /
   `overtime_presented`) — not a false trade-complete ack — still without a
   foreign-local ledger.

| Assertion | Result |
| --- | --- |
| M18 copy + `forward_pending` on ambiguous timeout | yes |
| Redis pending retained then cleared by driver | yes |
| No local foreign ledger for either idempotency key | yes |
| Live forward workflow `overtime`, no `trade_number` | yes |

Evidence: `tmp/combined-staging-evidence/stage16-driver-OT-REQ-CROSS-FORWARD.json`.

### 12.4 Remaining work before `main`

| Item | State |
| --- | --- |
| Mutating Stage 16 scenario drivers | 11 of 15 wired and passing live; `execute` → `execute_partial` once transport env is set |
| Overtime preferences | staging users remain at `0` after driver cleanup |
| Coin inference flags | off by default, untouched |
| Arvan CDN origin for `staging.gold-trade.ir` | broken, needs panel fix |
| Sync parity comparison | `comparison_status: missing` — no parity run yet on this pair |
| coin-price vs coin-commodity comparison | still owed per the handoff prompt |
| Next drivers | channel / Telegram / UI reconnect axes |
