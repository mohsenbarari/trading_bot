# Dual-Platform Registration - Stage 0 Baseline

- Date: 2026-07-11
- Baseline captured at: 2026-07-11T06:28:27Z
- Branch: `candidate/bot-webapp-integration`
- Inspected source commit: `a774c2d287e2361c70723995239642c03dc25036`
- Roadmap: `docs/DUAL_PLATFORM_REGISTRATION_AND_SYNCHRONIZED_OTP_ROADMAP_20260710.md`

## Status

Stage 0 is complete.

This stage changed no application code, database schema, runtime configuration, feature flag, or
deployed service. It performed a read-only code/runtime inventory and recorded the baseline needed
before Stage 1. No production action was performed and no staging deploy was run.

## Scope And Repository Guard

- The branch was `candidate/bot-webapp-integration` before inspection and before documentation edits.
- The inspected worktree was clean.
- The source baseline was exactly `a774c2d2`.
- The branch contains only the previously approved roadmap commits relative to its `main` base.
- Production flags remain absent/disabled; no production configuration was touched.
- CDN, object storage, messenger-data sync, Offer/request ownership, and existing-user migration are
  outside this implementation scope.

## Current Write-Path Inventory

### Invitation creation and cancellation

| Surface | Current writer | Current behavior | Required boundary |
|---|---|---|---|
| Standard/admin WebApp | `api/routers/invitations.py:create_invitation` | Iran API creates and commits `Invitation`, reads the one-day `core.config` value, then calls SMS synchronously | Move mutation to one Iran-authoritative invitation service; use the central two-day trading setting and per-kind SMS policy |
| Standard/admin Telegram | `bot/handlers/admin.py:process_invitation_role` | Foreign bot opens `AsyncSessionLocal` and directly calls the Web router function, therefore writing the foreign database | Replace with the existing signed-command pattern; foreign must not create authoritative invitations |
| Tier-1 customer WebApp | `api/routers/customers.py:create_my_customer` -> `create_owner_customer_relation` | Creates relation and invitation and currently calls SMS | Reuse canonical service; invitation SMS remains available but is disabled by the Tier-1 category flag |
| Tier-1 customer Telegram | `bot/handlers/panel.py` -> `core/customer_invite_forwarding.py` -> internal customer endpoint | Already forwards a signed HMAC command to Iran with Redis idempotency protection | Reuse this transport pattern; do not create a second transport stack |
| Tier-2 customer WebApp | customer relation service/router | Web-only; creates invitation/relation and sends the existing SMS template | Keep Web-only and keep SMS enabled |
| Accountant WebApp | `api/routers/accountants.py` -> accountant relation service | Web-only; creates invitation/relation and sends the existing SMS template | Keep Web-only and keep SMS enabled |
| Pending standard invitation deletion | `api/routers/invitations.py` | Hard deletes the invitation | Replace only as required by the approved soft-revocation lifecycle |
| Customer/accountant cancellation | relation services | Marks the relation terminal and marks/shortens the invitation, with service-owned commits | Preserve outcome while moving transaction ownership to the canonical boundary |

Both relation services currently own nested commits and fixed two-day constants. Their pending
relation timestamps must copy the canonical `Invitation.expires_at` exactly; they must expose
flush-based mutation helpers so the outer Iran transaction owns commit/rollback.

### Registration and Telegram identity

| Mutation | Current writer | Finding |
|---|---|---|
| Web registration | `api/routers/auth.py:register_complete` | Creates `User` inline without an invitation row lock, sets `telegram_id=None`, derives `home_server` from request context, activates a relation, commits, then performs notifications/session creation |
| Telegram link-token consumption | `core/services/telegram_link_token_service.py:consume_telegram_link_token` | Writes `users.telegram_id`, username, and full name in the database used by the foreign bot |
| Telegram link fallback | `bot/handlers/link_account.py:finalize_account_link` | Has a fallback that writes the same User identity fields directly on foreign and commits |
| Direct Telegram invitation start | `bot/handlers/start.py` | Validates the invitation/relation but currently redirects the user to Web registration; it does not complete Telegram-first registration |

The current contact guard in `bot/handlers/link_account.py` correctly requires
`contact.user_id == sender.id`; it must be reused. The current bot and Web address UIs enforce a
minimum of 10 characters, but `RegisterComplete.address` in `api/routers/auth.py` does not enforce
the same rule at the authoritative backend boundary.

### User sync field ownership

The current sync receiver upserts `users` mainly by integer ID and current User events carry broad
full-row payloads. That is unsafe when Iran and foreign can observe or mutate different User fields:

- Iran must own account, identity, role/admin, normalized mobile, address, and `home_server` fields.
- Foreign must own the monotonic Telegram onboarding state.
- `last_seen_at` must merge by maximum trusted timestamp.
- trade/customer counters must stay outside generic User patches.
- stale/unversioned full-row events need the compatibility and fail-closed policy defined in Stage 1.
- all new users from either completion surface must be written by Iran with literal
  `home_server="iran"`; foreign creates no Web session or token.

Current registry classification was verified: `users`, `invitations`, customer/accountant
relations, `telegram_link_tokens`, and `telegram_notification_outbox` sync; Web sessions and
messenger runtime data are no-sync.

## Current Product-Rule Baseline

### Eligibility and password behavior

- Existing inviter permission checks remain the authority for which invitations may be issued.
- A valid invitation for any currently permitted role is eligible for Telegram completion; no new
  Police, Middle Manager, or Super Admin OTP/approval restriction is added.
- Telegram registration uses invitation validity, sender-owned contact, exact normalized-mobile
  match, identity-conflict checks, and audit. It never uses login OTP.
- `core/services/bot_access_policy.py:evaluate_bot_access` remains the bot access authority:
  standard/police/middle/super and eligible Tier-1 users are allowed; watch, inactive/deleted,
  accountant, and Tier-2 users are denied as currently defined.
- New Telegram registrations retain `must_change_password=False`.
- Existing `admin_password_hash`/legacy setup-password behavior is not expanded, removed, or
  migrated. Later WebApp login always uses OTP.

### Invitation expiry and SMS

- Runtime `core.config.settings.invitation_expiry_days` is currently `1`.
- Effective staging `get_trading_settings_async().invitation_expiry_days` is currently `2`.
- Stage 1 must make `core.trading_settings` the only creation source and prevent the obsolete
  one-day setting from being reintroduced into invitation writes.
- Standard/admin and Tier-1 invitation SMS: capability retained, category flags default `false`,
  links are shared manually by the admin.
- Accountant and Tier-2 invitation SMS: Web-only, category flags default `true`, current templates
  and provider contracts remain unchanged.
- OTP SMS fallback is independent of every invitation-SMS flag.

### Registration lifecycle and retention

- No `manual_review` state, queue, override, support action, SLA, or force-create/force-link path may
  be introduced. Invalid, conflicting, ambiguous, revoked, expired, or late work terminates with a
  safe bounded result.
- Foreign registration intents are durable foreign-local no-sync records and follow current
  database/backup retention. No new timed purge is introduced.
- Only accepted Invitation/User/Relation product state syncs normally.
- Existing pending-invitation WebApp UI remains the only required administrative visibility.
- Existing two-step onboarding/tutorial acknowledgement and welcome/channel/WebApp message sequence
  is preserved without copy or ordering changes.

## Current OTP Baseline

`api/routers/auth.py` currently provides one `otp:{mobile}` code-of-record with a 120-second TTL.
For linked users it attempts Telegram delivery and otherwise sends SMS; the resend endpoint reuses
the current code. Current gaps recorded for Stage 6 are:

- code generation uses `random.randint` rather than a cryptographically secure generator;
- the Telegram relay does not provide a strict delivery acknowledgement;
- SMS fallback requires a second user action instead of an automatic due job;
- `LoginView.vue` displays a 30-second Telegram countdown;
- some OTP logs include raw mobile/Telegram identifiers;
- staging currently has `STAGING_LOG_OTP_CODES=true`, which short-circuits real delivery testing.

The target keeps `otp:{mobile}` as the single code-of-record during compatibility, changes the
countdown/fallback due time to 40 seconds, and sends SMS automatically only if login/verification has
not completed. Telegram registration/login itself has no OTP path.

## Runtime And Staging Baseline

Read-only checks were captured without deploy:

| Check | Result |
|---|---|
| Staging public health | `https://staging.362514.ir/api/config` returned the expected bot username and frontend URL |
| Services | `app`, `foreign_app`, `bot`, both sync workers, PostgreSQL, and Redis were up; both API containers were healthy |
| Iran sync health | `status=ok`, Redis OK, peer configured, outbound/retry queues `0`, unsynced rows `0`, oldest age `0` |
| Foreign sync health | `status=ok`, Redis OK, peer configured, outbound/retry queues `0`, unsynced rows `0`, oldest age `0` |
| Publication reconciliation | Both nodes reported zero findings/backlog |
| Latest deep parity | 23 tables, status `ok`, zero business/critical drift, duplicates, truncation, or incomplete tables |
| Parity freshness | Stale: observed `2026-07-10T19:21:24Z`, age about 11.1 hours, required maximum 900 seconds |
| Repo migration head | `f7c8d9e0a1b2` |
| Iran staging DB migration | `f7c8d9e0a1b2 (head)` |
| Foreign staging DB migration | `f7c8d9e0a1b2 (head)` |
| Runtime mode | Iran app: `iran/staging`; foreign app: `foreign/staging` |
| Runtime release metadata | Iran app reports `01a69a13d788`; foreign app reports generic `staging` |
| Effective invitation lifetime | Central trading setting reports `2`; legacy config reports `1` |
| Staging OTP log bypass | Currently `true` on both app surfaces |

The stale parity record is a baseline observation, not a clean fresh-parity assertion. A new deep
comparison is mandatory before any state-mutating two-server rollout. Release metadata must also be
made immutable/consistent before Stage 10 evidence is accepted. Real Telegram OTP testing is
forbidden until Stage 6 changes both staging defaults and deployed values to
`STAGING_LOG_OTP_CODES=false`.

## Background Jobs And Notification Reuse

- API background jobs are registered through `main.py:_background_job_factories` and filtered by
  `core/background_job_authority.py`, with Redis leader ownership.
- Registration reconciliation must be added there as foreign-only; OTP fallback must be added there
  as Iran-only. They are independently disabled by default and do not get new processes/pools.
- Telegram notification delivery already uses `telegram_notification_outbox`, its dedupe key,
  lease/status model, and the existing foreign bot worker in `run_bot.py`.
- Registration must enqueue side effects transactionally through that outbox. No second outbox or
  worker is permitted.

## Explicit Schema And Tooling Inventory

Every new table/field must be accounted for in the existing mechanisms below; no parallel registry
or deployment system is permitted:

- `core/sync_registry.py`
- `core/sync_field_policy.py`
- `core/sync_authority.py`
- `core/events.py`
- `api/routers/sync.py`
- `core/sync_worker.py:SYNC_OUTBOUND_TABLE_PRIORITY`
- `core/sync_parity.py:PARITY_QUICK_TABLES` and deep registry-derived comparison
- `scripts/seed_shared_sync_tables.py:DEFAULT_TABLES`
- `scripts/inspect_shared_sync_state.py:SHARED_SYNC_TABLES`
- `scripts/load_fixture_worker.py:SEQUENCE_ALIGNMENT_TABLES`
- `scripts/build_production_full_matrix_manifest.py:SYNC_TABLES`
- `scripts/production_deploy_online.sh:SHARED_SYNC_TABLES_SQL`
- staging reset/health/migration checks
- backup, restore, and recoverability verification scripts
- model imports and Alembic metadata discovery

No registration-intent, Iran command-receipt, global identity-reservation, registration
`sync_version`, or OTP fallback-request model exists at this baseline.

## Test And Coverage Baseline

### Results

| Command/suite | Result |
|---|---|
| Roadmap focused registration suite | 53 tests passed |
| Additional access/sync/outbox/runtime focused suite | 55 tests passed |
| Full backend discovery in sandbox | 2636 tests run; 3 environment-permission failures in deploy-surface checks |
| Deploy-surface module outside sandbox | 27 tests passed, including Dockerfile build checks and Nginx syntax/bind validation |
| Full frontend Vitest suite | 127 files, 1101 tests passed |
| `make test-gate` inventory gate | Passed: 439 Python test modules, 127 frontend unit files, 22 Playwright specs, 5 manual tools |

The three full-suite failures were not product failures: sandbox execution could not access
`/var/run/docker.sock` and could not bind the Nginx test socket. The exact failing module passed
27/27 with real host permissions.

### Coverage infrastructure inventory

- Python branch coverage already exists in `.github/workflows/coverage-report.yml` using
  `coverage run --branch` and JSON output.
- Frontend V8 coverage already exists through `@vitest/coverage-v8` and `vitest.config.ts`.
- `coverage` is installed in the current Python environment.
- `hypothesis`, `mutmut`, and `diff-cover` are not installed at this baseline.
- A registration-specific deterministic async checkpoint protocol, generated state-transition
  artifact, diff-coverage combiner, mutation target/ignore manifest, and traceability verifier do not
  yet exist.

Therefore the roadmap's 100% changed-code/branch/transition gate is not claimed as active in Stage
0. Stage 9 must add and prove these tools. `mutmut` must be configured with a runner matching this
repository's `unittest` invocation; missing tooling cannot become a waiver.

## Stage 0 Challenge Disposition

| IDs | Stage 0 evidence/decision | Owner/status |
|---|---|---|
| `DT-02` | Every standard, customer, accountant, Web, and bot invitation writer is inventoried; mobile-only active lookup and cross-kind collision risk confirmed | Product decision already approved; engineering implementation in Stages 1-3 |
| `DT-04` | Both unapproved foreign identity writers and the direct bot-admin invitation writer are identified | Single Iran writer is locked; implementation in Stages 2-5 |
| `DT-05` | Current broad User events/receiver behavior and required field authorities are recorded | Patch/version contract implementation in Stage 1 |
| `DN-01`, `DN-02` | No role-specific Telegram OTP or manager approval gate; existing inviter permissions remain | Closed by owner; test matrix in Stages 5/9 |
| `IT-05` | Legacy password fields/setup path inventoried; no new password behavior | Closed by owner; regression only |
| `DN-03` | One-day legacy and two-day central sources verified in code and deployed staging | Closed by owner; centralization in Stages 1-3 |
| `DT-17`, `DN-05` | All current SMS call sites and four approved categories are mapped | Closed by owner; flags/policy in Stages 1/3 |
| `DT-19`, `DN-06` | No registration manual-review implementation exists; unrelated generic sync-repair wording is not this feature | Closed by owner; forbidden-surface tests required |
| `DN-07` | Intent no-sync/current-retention and accepted-product sync boundaries recorded | Closed by owner; registry/backup proof in Stages 1/9/10 |
| `IT-04` | Model default and request-derived current writer confirmed unsafe; literal Iran owner is locked | Closed by owner; shared service in Stage 2 |
| `IT-11` | All known explicit schema/reset/fixture/parity/backup/deploy lists are recorded above | Closed by owner; updates verified per migration |
| `IT-12` | Existing and missing test infrastructure is explicitly separated; no 100% claim is made yet | Closed by owner; tooling implementation in Stage 9 |
| `IN-06` | Existing staging lifecycle, flags-off order, health, rollback boundary, and production gate are recorded | Closed by owner; execution in Stages 10-12 |

No product decision remains open for Stage 1. Engineering choices that remain intentionally local to
their implementation stage are the exact additive table/class names, advisory-lock key encoding,
version-compatibility flag name, and test-checkpoint interface. Their outcomes are constrained by
the approved contracts and must be recorded with Stage 1/9 evidence.

## Implementation File Map

The list below is the current expected blast radius. New files are additive and their final names
must follow repository conventions.

| Stage | Existing files/surfaces expected to change |
|---|---|
| 1 | Invitation/User models and model imports, Alembic migrations, `schemas.py`, `core/config.py`, environment examples/compose wiring, sync registry/field policy/authority/events/receiver/worker/parity, explicit seed/reset/fixture/backup/deploy lists, migration and sync contract tests |
| 2 | `api/routers/auth.py`, customer/accountant relation services, mandatory-membership integration, existing Telegram notification outbox service/model, shared authoritative registration service, transaction/idempotency tests |
| 3 | invitation/customer/accountant routers and services, bot admin/panel invitation entry points, URL/link builders, invitation frontend clients/components, SMS policy/adapter and contract tests |
| 4 | signed Iran command endpoint/client, foreign-local intent service/model, reconciliation loop, `main.py`, background-job authority, health metrics and outage/replay tests |
| 5 | `bot/handlers/start.py`, bot registration FSM/state handlers, link-account reuse points, onboarding/middleware/keyboards, bot role/contact/address/onboarding tests |
| 6 | auth OTP routes/services, Redis OTP state/claim logic, async SMS.ir adapter, Iran fallback job, `LoginView.vue`, staging deploy/env defaults, OTP concurrency/restart/security tests |
| 7 | Web registration and login UI contracts, invitation views, bot copy/UX integration, frontend unit and Playwright matrix tests |
| 8 | existing structured logging, audit, health, metrics, and alert integration plus no-PII tests |
| 9 | Python/frontend coverage workflows, property/fuzz/mutation/diff-coverage/checkpoint/traceability tooling and generated test matrices |
| 10 | existing staging deploy/health/parity/backup/restore/mixed-version/load scripts and evidence artifacts |
| 11 | owner acceptance evidence only; no production release |
| 12 | existing production readiness/release gates and rollback evidence; execution requires explicit owner approval |

## Preconditions Carried Forward

1. Do not enable a feature before its additive schema, registry, receiver, and old/new-version
   compatibility checks pass.
2. Refresh deep parity immediately before the first mutating staging rollout and require fresh clean
   parity afterward.
3. Correct staging release metadata before accepting Stage 10 evidence.
4. Set and enforce `STAGING_LOG_OTP_CODES=false` before testing real Telegram/SMS OTP delivery.
5. Keep all new flags off by default; enable one stage at a time and prove its independent rollback.
6. Do not deploy or release to production without the explicit Stage 12 owner gate.

## Exit-Criteria Record

- Branch/worktree guard: PASS.
- Baseline evidence recorded: PASS.
- Current implementation file map recorded: PASS.
- Repository and both staging migration heads agree: PASS.
- Every Stage 0 registry ID has an owner decision and evidence assignment: PASS.
- Unrelated code/runtime/configuration changes: NONE.
