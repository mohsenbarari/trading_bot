# Dual-Platform Registration Stage 4: Intent And Reconciliation

Date: 2026-07-11

Status: source implementation complete; independent review required before Stage 5.

## Purpose

Stage 4 connects the Stage 1/2 persistence and authoritative transaction without enabling direct
Telegram registration. A confirmed foreign Telegram intent can be durably retained and retried, but
only Iran may create/link a User. An Iran HTTP response alone never grants bot access; the foreign
side waits for the required normal-sync projections.

No migration remains applied, no feature flag was enabled, and no deployment, push, or production
release was performed. The transient verification incident and completed rollback are documented
below.

## Implemented Design

- `create_or_reuse_ready_registration_intent()` persists one foreign-local `ready` intent. Its UUID
  is the stable command id; retries retain the first proof/profile snapshots. A changed address,
  mobile, token, Telegram identity, or expiry snapshot conflicts instead of replacing evidence.
- The worker claims due `ready`, `retry_wait`, and expired-lease `forwarding` rows through
  `FOR UPDATE SKIP LOCKED`. It increments an attempt generation, commits the lease before HTTP, and
  ignores stale worker updates after lease recovery.
- Retry uses bounded exponential backoff with deterministic jitter. Poison local rows become safe
  terminal rejections without starving valid due work. No transaction remains open across HTTP.
- `POST /api/auth/internal/telegram-registration/reconcile` accepts strict canonical JSON only on
  Iran from a signed foreign source. Iran creates trusted receive time internally and delegates to
  the existing `complete_invitation_registration()` transaction and command receipt.
- A lost Iran response reuses the same command/receipt. Changed-payload replay is terminal.
- `created`, `linked_existing`, and `already_linked` responses remain locally retryable until the
  synced User, completed Invitation, exact Telegram/mobile/account identity, and required Relation
  projection are visible. Customer Tier-1 requires its active Relation; Standard/admin/police do
  not wait for one; accountant/Tier-2 cannot pass the shared bot-access policy.
- Both internal endpoints set `Cache-Control: no-store`; logs/audits contain command ids and bounded
  outcome codes, never raw token, mobile, address, Telegram id, name, body, signature, or API key.
- No manual-review state, route, queue, operator action, or force path exists.

## Legacy Account-Link Migration

The existing WebApp-issued Telegram link-token flow previously mutated `User.telegram_id`, profile,
address, link-token state, and channel membership directly on foreign. Stage 4 adds a strict signed
account-link command and one Iran transaction that:

- re-hashes and locks the current Iran token;
- re-reads the current Iran User and bot eligibility;
- locks canonical identity and Telegram id;
- rejects Telegram-id reuse, account conflict, inactive/deleted/Web-only users, and token terminal
  states;
- preserves Web full name, address, role, status, limits, and all unrelated fields;
- changes only Telegram identity, an actually incomplete legacy address, token use state, and
  mandatory membership;
- finalizes the existing Iran command receipt atomically.

For mixed-version safety, the legacy handler remains unchanged while
`REGISTRATION_SYNC_V2_ENABLED=false`. When the flag is true, the legacy writer fails closed and the
handler must use the Iran command. The Iran account-link endpoint uses the same flag. Deploying
source with defaults off therefore does not silently break the current link flow.

## Foreign Iran-Owned Writer Inventory

| Writer family | Sync-v2 behavior |
|---|---|
| Web-to-Telegram link-token completion/address | Routed to Iran; legacy writer raises when v2 is on |
| Bot admin role/status/block/limit/unlimit/capability | Existing shared-admin authority rejects foreign writes |
| Counter increments from bot activity | Existing v2 event/receipt contract; not generic User overwrite |
| Counter reset from admin unlimit | Covered by the existing foreign admin rejection and Iran-only reset contract |
| Bot onboarding steps | Explicit foreign-owned monotonic fields |
| `last_seen_at` | Existing shared max-merge field |

Sync-v2 remains disabled. Stage 10 must execute every handler with the flag on and fail release if a
new unapproved foreign Iran-owned writer appears.

## Concurrency And Recovery

Real PostgreSQL tests cover:

- concurrent exact intent persistence producing one row;
- changed business payload rejection and crash-after-commit snapshot recovery;
- duplicate claim, expired lease recovery, and stale attempt suppression;
- poison-row isolation;
- account-link response loss returning one receipt result;
- two distinct Telegram identities racing one token with exactly one winner;
- delayed Tier-1 Relation projection gating.

The account-link race initially found a real stale identity-map defect: session B could probe a
pending token, wait on the canonical advisory lock, and then receive its cached pre-lock object from
`SELECT FOR UPDATE`. Locked Token/User reads now use `populate_existing=True`, forcing a reload of
the state committed by session A before the decision.

## Verification

- Focused backend: `135` tests passed, including the direct Sync-v2 handler paths and the unchanged
  legacy address-normalization path.
- Full backend: `2800` tests passed; `35` opt-in tests skipped.
- Real PostgreSQL: `7` tests passed, including the scratch-target safety test.
- Static: diff check, compile, no-manual-review, no foreign User construction in the intent worker,
  no-sync registry, foreign-only background placement, and sensitive-log-template checks passed.
- Database final state: active `trading_bot_db` is at Alembic `f7c8d9e0a1b2`, Stage 1 schema object
  count is zero, and Stage 4 scratch database count is zero.

### Verification Incident And Recovery

The first containerized PostgreSQL test invocation used `docker compose run` without `--no-deps`.
Compose therefore started its configured migration dependency against the active runtime database
and temporarily advanced it from `f7c8d9e0a1b2` to `a8d9e0f1b2c3`. This was detected immediately
during the mandatory active-database check. Before rollback, all four new local-state tables were
queried and contained zero rows. The official Alembic downgrade then restored `f7c8d9e0a1b2`; a
second query proved all four Stage 1 schema objects absent, the scratch database was dropped, and
the running services continued processing sync health and batches.

This incident changes the test procedure: scratch migration is performed explicitly through the
guarded runner, and any later Compose test container must use `--no-deps` or an isolated Compose
project whose migration service targets only the scratch database. The independent reviewer must
treat this operational boundary as part of the Stage 4 evidence, not as a successful runtime
migration or rollout.

## Open Gates

- The Stage 3 independent review returned `NO-GO`; all confirmed High/Medium findings were
  remediated after the Stage 4 commit and Stage 4 PostgreSQL compatibility was rerun. The exact
  remediation plus Stage 4 range still requires a combined independent `GO` before Stage 5.
- Stage 4 itself requires independent review of the exact commit and evidence package.
- The known Stage 2 post-Web-session-commit response-loss issue still blocks deployment of the
  migrated Web completion route; Stage 4 does not claim to fix Web session token recovery.
- Direct Telegram contact/address/confirmation FSM and commit-before-FSM-clear integration belong to
  Stage 5. Stage 4 supplies the persistence API but does not expose the new user entry flow.
- No Stage 1 schema may reach a writing environment until Stage 3/4 reviews, mixed-version checks,
  backup/rollback proof, and explicit rollout approval are complete.
- Sync-v2 and reconciliation flags remain off. Account-link authority switching requires both
  servers to have compatible source before the flag changes.
- Performance under combined market load, NTP evidence, ledger capacity/retention, real two-server
  sync ordering, and outage soak remain Stages 9, 10, and 12 gates.

## Rollback Boundary

This is one source-only Stage 4 commit. With all flags off, current runtime behavior remains on the
legacy path. Before any later enablement, rollback means disabling reconciliation/Sync-v2 first,
preserving intents/receipts, then reverting the reviewed source as a unit. Never delete ready
intents, receipts, completed Invitations, Relations, or Users during rollback.
