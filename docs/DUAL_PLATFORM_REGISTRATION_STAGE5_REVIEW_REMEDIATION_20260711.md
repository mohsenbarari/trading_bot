# Dual-Platform Registration Stage 5 Review Remediation

Date: 2026-07-11

Status: source remediation complete; flags remain off; no migration or deployment performed.

## Scope

This change set remediates the findings in
`tmp/chatgpt/5-dual-platform-registration-stage5-independent-review.md` against Stage 5 commit
`9cc439ff99e034b6a03b58fb3542a4d5fb629088`. The review was treated as input, not authority: every
finding was checked against current source, the controlling roadmap, cross-server ownership, and
the existing Web-first path before code was changed.

## Accepted And Implemented

- `H-1`: activation now fails closed in Auth middleware, deep-link/plain start, channel join, and
  handoff until the exact durable intent has a successful status and a localized projected User.
- `H-2`: Iran's integer User id remains opaque evidence. A separate foreign-local
  `projected_user_id` binds handoff to the localized User; sync localizes Invitation and Relation
  User foreign keys through natural identity instead of assuming equal primary keys.
- `H-3`: token issue/reissue and consumption use the same lock order: canonical advisory identity,
  User row, then deterministically ordered Token rows.
- `H-4`: account-link receipt identity excludes volatile Telegram profile snapshots and contact
  timestamp, while mode, credential, mobile, Telegram id, and exact address remain protected.
  A lost response therefore replays the first durable result; changed business input is rejected.
- `M-1`: every direct-registration entry/state handler is private-chat-only and does not echo contact
  or address data. Only Registration-owned transient FSM state may be cleared.
- `M-3/M-4`: the global Redis TTL change was removed. Registration state/data are written together
  in one Redis transaction with Invitation-bounded expiry; read/write/clear interruption points fail
  closed or preserve an already committed intent. A short Redis claim suppresses duplicate handoff
  polling.
- `M-5`: a committed pending SMS delivery with no claim/provider attempt is claimable after restart;
  only a prior claim/attempt becomes ambiguous.
- `M-6`: worker failures now distinguish auth/configuration, mixed version/route, transport/server,
  invalid protocol, command mismatch, feature-disabled, and projection-pending states. Persistent
  incompatibilities emit operational errors after repeated attempts.
- `M-8`: the new scratch runner creates random allowlisted databases, uses guarded migrations,
  invokes Compose with `--no-deps`, removes every scratch database on failure/success, and refuses
  evidence if the active database snapshot changes.
- `L-1`: FSM TTL uses ceiling seconds so an Invitation state never expires early due to truncation.
- `L-2`: resuming an existing intent clears only one of the known Registration states. Trade,
  administration, and other bot FSM workflows are preserved.

## Modified Recommendations

- `M-2` was not implemented as an unconditional pending/unbound Relation rule. Telegram-first
  Tier-1 registration requires a current pending, unbound, unexpired Relation. Web-first account
  linking requires the already completed Web Invitation and its exact active Relation bound to the
  same registered User. Requiring pending/unbound in both cases would break the approved Web-first
  linking path and discard authoritative Web profile/address state.
- `M-6` uses durable retries plus classified health/error signals, not max-attempt terminalization or
  manual review. Authentication and mixed-version faults are operationally recoverable, and the
  owner explicitly disallowed a manual-review or force-create path. Valid terminal domain responses
  still finalize immediately.
- `H-4` was fixed by correcting canonical receipt identity rather than introducing a second durable
  foreign account-link intent/worker. The existing Iran receipt already owns idempotency and the
  smaller design preserves one transaction and one authority boundary.
- `L-2` was narrowed to Registration-owned state. Blindly clearing the current FSM when an invite is
  reopened could cancel an unrelated valid trade or administration workflow.

## Rejected Or Deferred Recommendations

- `L-4` (replace all legacy panel URLs with `public_webapp_url_for_links()`) was rejected for this
  remediation. It made the flags-off legacy `/start` path require a new `PUBLIC_WEBAPP_URL` setting
  and broke the full suite. Legacy shortcuts keep `settings.frontend_url`; only the new successful
  registration handoff uses the validated public URL owner. A broader URL migration needs its own
  compatibility stage.
- `L-3` (replace bounded handoff polling) is deferred to load validation. The poll is short,
  duplicate-suppressed, and not a correctness defect. Adding another notification mechanism or
  worker now would duplicate existing ownership before measured evidence justifies it.
- `M-7` (Web session response-loss recovery) remains an existing rollout-gate item outside the
  Stage 5 Telegram FSM. This remediation does not alter Web session issuance or claim that risk is
  closed.

## Verification Contract

- The feature remains disabled by default and requires both direct-registration and reconciliation
  flags.
- The active runtime database must remain `trading_bot_db` at `f7c8d9e0a1b2` with zero Stage 1
  objects until an explicitly approved migration stage.
- PostgreSQL migration/concurrency tests run only through
  `scripts/run_registration_scratch_suite.sh`.
- The final evidence package must contain focused/full test logs, scratch safety output, static
  checks, changed-file/diff evidence, and the final review prompt.

## Verification Results

- Focused registration, sync-localization, middleware, channel-join, Redis, runtime, and runner
  safety suite: `102` passed.
- Guarded real PostgreSQL scratch suites: Stage 1 migration `4` passed, Stage 3 concurrency `9`
  passed, and Stage 4 reconciliation/account-link `7` passed.
- Full backend, including Docker/nginx smoke coverage: `2858` passed and `38` opt-in tests skipped.
- Static validation: `git diff --check`, changed-module compilation, disabled-default flag check,
  scratch-wrapper policy check, and credential-pattern scan passed.
- Final active database evidence: `trading_bot_db`, Alembic `f7c8d9e0a1b2`, zero Stage 1 objects,
  and zero generated registration scratch databases.

## Remaining Gates

- Independent review of this exact remediation commit and evidence package.
- Stage 6 source work only after the Stage 5 remediation gate is accepted.
- Later mixed-version, real Redis restart, two-server sync/outage, backup/rollback, staging, and
  owner acceptance gates remain unchanged.
- No production release is authorized.
