# Dual-Platform Registration Stage 5 Post-Review Remediation

Date: 2026-07-11

Status: source remediation complete; independent review pending; all feature flags remain off; no
migration, deployment, push, or runtime database change was performed.

## Review Boundary

This record addresses the independent `NO-GO` review of Stage 5 remediation commit
`5bc95fd1826609e47aadea7fb6762c2184f95753`. Every finding was checked against the current branch,
the controlling roadmap, the existing sync receiver/event producers, the account-link transaction,
Aiogram runtime construction, and the completed Stage 6 source commit before any change was made.

## Accepted And Implemented

- `H-1`: activation no longer uses the latest intent as global authorization. An exact successful
  intent must be bound to the current foreign-local User id, historical terminal attempts do not
  shadow a valid User, and current bot-access policy is evaluated before onboarding or dispatch.
- `H-2`: every registration product User foreign key now carries natural identity metadata and is
  localized on receipt: Invitation creator/registered User, Customer/Accountant Relation
  owner/child/creator, and TelegramLinkToken User. Missing or conflicting identity defers safely;
  source numeric ids are never accepted as localized identity while Sync-v2 is active.
- `H-3`: the bot Dispatcher now uses Aiogram's Redis event isolation. Updates for one FSM key are
  serialized while unrelated users retain independent progress.
- `M-1`: Invitation SMS claims have a bounded lease. A live competing retry returns `pending`
  without provider I/O or state mutation; a stale claim becomes `ambiguous`; the current owner can
  persist its exact accepted/failed/ambiguous result for the same generation.
- `M-2`: the source lock-order fix now has a bounded real PostgreSQL issue/reissue-versus-consume
  race with fresh-session assertions and no deadlock.
- `M-3`: a handler-level lost-response replay rebuilds the command with changed volatile Telegram
  profile data, reuses one Iran receipt, and rejects changed business input.
- `M-4`: one shared fail-closed runtime policy requires local Sync-v2 for reconciliation, and direct
  collection additionally requires both direct-registration and reconciliation flags. The internal
  Iran endpoint, foreign worker, bot handlers, and background-job registration use this policy.
- `M-5`: feature-disabled/mixed-version retries are included in repeated persistent-error
  escalation without terminalizing the intent or adding manual review.

## Modified Recommendations

- A synchronous startup probe of the remote peer was not added for `M-4`. Startup network
  availability is not capability identity and would couple foreign bot availability to an Iran
  request. Local capability is enforced before collection; signed peer `FEATURE_DISABLED` or
  protocol mismatch remains a durable retry plus persistent operational error. Exact old/new peer
  compatibility is still a mandatory Stage 10 rollout gate before enablement.
- `M-2` evidence uses the real public writers and a strict timeout instead of test-only hooks inside
  production lock code. Existing service-order tests plus the PostgreSQL race prove the supported
  writers share advisory identity, User, then Token order without adding instrumentation to the
  transaction.
- `H-3` is proven at the Dispatcher construction and real Redis isolation layer. The existing
  handler/state matrix remains the behavioral owner; production handlers were not duplicated in a
  test-only dispatcher.

## Rejected Or Deferred

- `M-6` Web-session response-loss recovery is unchanged. It is an explicit Web registration
  deployment blocker, not a reason to alter synchronized login OTP or the Telegram FSM in this
  remediation.
- `M-7` unsupported manual Compose commands remain a procedural risk. The maintained registration
  evidence path is the fail-closed `scripts/run_registration_scratch_suite.sh`; this change does
  not attempt to prevent arbitrary shell commands.
- `L-1` additional intent database constraints are deferred. Current service transitions and poison
  isolation enforce the tuple; a schema-hardening migration requires its own rollback review.
- `L-2` polling load, `L-3` legacy URL compatibility, and `L-4` broad UTC cleanup remain the already
  documented later gates. None justifies unrelated behavior changes here.

## Verification Added

- Unit/focused tests cover exact activation relevance/current policy, all FK localization fields,
  feature-flag dependency matrices, repeated incompatibility escalation, event-isolation wiring,
  and SMS live/stale claim behavior.
- Real Redis proves same-key serialization and unrelated-key concurrency.
- Real PostgreSQL proves accepted and failed in-flight SMS outcomes, stale claim handling, token
  issue/reissue-versus-consume, handler lost-response replay and changed-business conflict, every
  registration User FK under colliding source ids, and exact activation despite newer rejected
  history.
- The official scratch runner again proves the active database remains `trading_bot_db` at
  `f7c8d9e0a1b2` with zero Stage 1 objects after all scratch databases are removed.

## Remaining Gates

- Independent review must assess the exact final commit and combined Stage 5/Stage 6 evidence.
- Real two-server mixed-version, delivery-order, outage/recovery, Redis restart/eviction, and staged
  load matrices remain mandatory before feature enablement.
- Web-session response-loss recovery remains mandatory before migrated Web registration rollout.
- Stage 7 requires separate owner authorization. No staging or production release is authorized.

## Rollback

Revert the post-review remediation commit. All related flags remain false and no runtime migration
or deployment occurred, so rollback has no data-plane action.
