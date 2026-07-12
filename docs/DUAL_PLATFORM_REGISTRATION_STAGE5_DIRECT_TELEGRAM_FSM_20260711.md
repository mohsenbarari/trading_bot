# Dual-Platform Registration Stage 5: Direct Telegram FSM

Date: 2026-07-11

Status: source implementation complete; post-review remediation documented separately; flags remain off.

## Purpose

Stage 5 lets an eligible invited user collect identity proof and address in Telegram without opening
the WebApp. It integrates only with the durable Stage 4 intent boundary. Iran remains the only User
authority, and normal sync plus the shared bot-access policy remains the only activation gate.

The owner explicitly authorized this source stage while the combined Stage 3/4 external review was
still pending. That authorization does not waive the review or rollout gates.

## Implemented Flow

1. A valid deep link enters direct registration only when both
   `TELEGRAM_DIRECT_REGISTRATION_ENABLED` and
   `TELEGRAM_REGISTRATION_RECONCILIATION_ENABLED` are true.
2. The bot stores token binding, canonical mobile, Telegram id, and Invitation expiry in foreign
   Redis. Both Redis keys are explicitly bounded by the Invitation expiry.
3. Contact must be sent by the same Telegram account. Forwarded, missing, malformed, or mismatched
   contact data cannot advance the FSM.
4. Address uses the existing exact, non-trimmed minimum-10-character contract and message.
5. Confirmation re-reads current Invitation, expiry, mobile, kind, role, and Tier-1 relation policy.
6. One ready intent commits in foreign PostgreSQL before the FSM is cleared. Failure before commit
   leaves the confirmation state retryable; exact replay reuses the same durable intent.
7. The existing Stage 4 worker performs signed reconciliation. A short handoff poll either observes
   the complete allowed projection and reuses the current panel/menu path, or reports that the
   registration is saved but not final. Reopening the same link resumes the durable intent.

## Safety Boundaries

- No `User`, Web session, JWT, password, refresh token, or OTP is created by the foreign FSM.
- Standard, police, middle-manager, super-admin, and Tier-1 customer invitations use the existing
  shared eligibility policy. Watch, accountant, Tier-2, legacy-ambiguous, revoked, and expired states
  cannot complete directly.
- Default flags are false. Either flag being false preserves the current Web-only redirect.
- Existing linked-account panel text, menu, channel join, two tutorials, acknowledgement callbacks,
  welcome message, channel link, and WebApp link are not reimplemented or reordered. Successful
  handoff activates the existing tutorial gate before panel exposure, independently of whether the
  Telegram user is already a channel member, admin, or owner.
- Logs contain only bounded reason/error codes and opaque intent ids. Token, mobile, Telegram id,
  address, profile text, contact payload, and command body are not logged by the new path.
- Post-commit polling failure returns the explicit pending-not-final state. It cannot roll back or
  duplicate the already durable intent.
- Registration state and data are written atomically with an Invitation-bounded Redis TTL. Unrelated
  bot FSM workflows keep their existing storage lifetime.

## Test Coverage

The dedicated Stage 5 suite covers:

- canonical `09`, `98`, `+98`, `0098`, bare `9`, Persian digits, edge whitespace, and malformed
  mobile values;
- both-feature gate, Standard direct flow, Tier-1 direct flow, Tier-2 Web-only flow, completed intent
  resume, malformed Invitation identity, and TTL setup failure;
- sender-owned, forwarded/other-user, mismatched, malformed, missing-phone, typed-contact, and wrong
  Telegram-id cases;
- empty/non-text/9/exact-10/over-10 exact address handling and missing contact proof;
- stale edit callback, edit TTL failure, current revocation/expiry/mobile/policy changes, changed
  payload replay, transient persistence failure, and commit-before-FSM-clear ordering;
- projection missing/mismatched/forbidden/allowed states, terminal and pending outcomes, bounded poll,
  and reuse of all three current success-panel variants;
- deterministic read-only intent lookup, foreign authority guard, identity-collision guard, and both
  Redis state/data expiry keys.

Verification results:

- Dedicated Stage 5: `29` tests passed.
- Relevant registration/bot focused suite: `86` tests passed.
- Real PostgreSQL Stage 4/5 persistence and reconciliation compatibility: `7` tests passed on guarded
  `stage4_registration_stage5_final`, migrated through the guarded runner and executed only with
  `docker compose run --no-deps`.
- Full backend outside the sandbox: `2841` tests passed; `38` opt-in tests skipped. This includes the
  complete `29`-test deployment-smoke module with real Dockerfile and nginx validation.
- Static checks: compile and `git diff --check` passed before packaging.
- Database final state: active `trading_bot_db` remained at `f7c8d9e0a1b2`; Stage 1 schema-object
  count is zero; scratch database count is zero.

## Open Gates

- Combined Stage 3 remediation plus Stage 4 review remains pending and may reopen dependencies.
- Stage 5 needs independent review of the exact commit, tests, logs, and roadmap alignment.
- Stage 1 migration and both direct-registration flags must remain off until review, staged mixed-
  version deployment, rollback, backup, real Redis restart, real two-server sync, and outage tests
  are approved in later stages.
- No staging or production behavior has changed. This stage is source-only.

## Rollback

Revert the single Stage 5 source commit. With flags still false, rollback has no data-plane step.
No migration or durable runtime data was introduced by this stage. Do not delete Stage 4 intent
rows as part of a later rollback after rollout; their durable evidence follows the roadmap policy.

## Next Stage

Stage 6 adds WebApp-login OTP delivery through Telegram with the same code automatically falling
back to SMS after 40 seconds. It introduces dedicated signed delivery acknowledgement and one Iran-
owned Redis state machine; it does not use Telegram OTP for bot login or registration.

## Post-Review Remediation Supersession

The verification counts and open findings above describe the original Stage 5 commit. The accepted,
modified, rejected, and deferred review findings are superseded by
`docs/DUAL_PLATFORM_REGISTRATION_STAGE5_REVIEW_REMEDIATION_20260711.md`. Stage 6 remains gated on
review of the exact remediation commit and its new evidence package.
