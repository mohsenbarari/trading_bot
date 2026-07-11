# Dual-Platform Registration Stage 6: Synchronized Login OTP

Date: 2026-07-11

Status: source implementation complete; feature flags remain off; no migration or deployment performed.

## Purpose

Stage 6 keeps WebApp login authoritative on Iran while delivering the same one-time code through
Telegram on foreign and, if login is not completed, automatically through SMS after 40 seconds.
This is only WebApp login delivery. Telegram registration and Telegram bot access do not use OTP.

The owner explicitly authorized Stage 6 source work while independent review of the Stage 5
remediation was pending. That authorization does not accept the review gate, enable a flag, migrate
a runtime database, or authorize staging/production rollout.

## Implemented Contract

1. Iran generates one cryptographically secure five-digit code and stores it only in the existing
   `otp:{mobile}` key for 120 seconds.
2. Iran creates structured Redis request metadata that references that key without copying the code.
3. When a linked Telegram id exists, Iran first arms a conservative Redis recovery deadline, then
   sends one strict signed command to the foreign-only internal endpoint.
4. A valid foreign acknowledgement atomically records Telegram acceptance and moves the fallback
   deadline to exactly 40 seconds after acknowledgement. If the Iran API exits during transport,
   the pre-armed deadline remains recoverable by the background leader.
5. Foreign sends only through the central Telegram gateway. A short-lived Redis receipt makes exact
   retries idempotent and rejects a changed command using the same request id.
6. Timeout, malformed acknowledgement, explicit Telegram failure, inability to arm/reschedule, or
   ambiguous delivery uses immediate SMS with the same code.
7. The Iran-only fallback job runs inside the existing background leader. Redis sorted-set due
   state and an atomic claim allow one provider attempt across leader restart or duplicate runners.
8. SMS.ir uses an async bounded adapter. Accepted, explicit failure, and ambiguous outcomes are
   distinct. An ambiguous provider outcome is never retried automatically with the same OTP.
9. Successful verification atomically consumes the code, rate-limit keys, and pending fallback.
   Manual legacy resend claims the same state and cannot create or send a second code.
10. LoginView keeps its existing copy, shows the backend 40-second Telegram fallback countdown, and
    exposes no manual resend action when that countdown reaches zero.

## Safety Boundaries

- The signed endpoint is foreign-only and accepts only Iran as source. Its schema forbids unknown
  fields, signatures retain the existing 60-second replay window, and request-id receipts suppress
  repeated side effects.
- OTP, mobile, Telegram id, and signed command body are absent from new durable logs, sync rows,
  notification outboxes, responses, and structured delivery metadata.
- A crash before acknowledgement cannot lose the fallback: a 45-second conservative recovery
  deadline is written before the at-most-five-second transport, then corrected to ack plus 40.
- A verify/fallback boundary race is resolved through Redis scripts. If verification removes the
  due item first there is no SMS; if the provider claim wins first, no second claim can occur.
- Explicit SMS rejection invalidates the OTP. Ambiguous SMS leaves the code verifiable but records a
  terminal ambiguous delivery state and performs no automatic retry.
- No new container, worker service, database table, migration, sync registry entry, password path,
  session authority, or bot registration flow was added.
- `TELEGRAM_LOGIN_OTP_ENABLED` and `OTP_SMS_AUTO_FALLBACK_ENABLED` remain false by default and in the
  staging example. The active local staging env also remains flag-off.
- `STAGING_LOG_OTP_CODES` is false in the staging example, generated env, every-deploy enforcement,
  and the active local staging env. Existing dev-login automation remains separate.

## Verification Results

- Dedicated Stage 6 tests: `19` passed.
- Focused auth/registration/background-authority suite: `60` passed.
- Real Redis DB 15 lifecycle/concurrency/cancellation suite: `3` passed and flushed only DB 15.
- Full backend outside sandbox, including real Dockerfile and Nginx smoke checks: `2880` passed and
  `41` opt-in tests skipped.
- Focused LoginView suite: `21` passed.
- Full frontend: `127` files and `1101` tests passed.
- Production frontend build: passed; PWA service worker and precache generated successfully.
- Static validation: changed Python modules compile, `git diff --check` passes, active/default flags
  remain off, and no Stage 6 credential or raw-OTP logging pattern was found. `ruff` was not
  available in this environment and is not claimed as executed evidence.

## Remaining Gates

- Independent review of the exact Stage 6 commit, roadmap alignment, race behavior, tests, and
  evidence package.
- Stage 5 post-review corrections remain independently binding until the exact combined commit and
  evidence package receive review disposition.
- Stage 7 source work requires explicit owner authorization after review disposition.
- Real Redis restart/failover timing, mixed-version transport, provider allowlist/template,
  two-server outage, staging deploy, and owner acceptance remain later roadmap gates.
- No feature enablement, staging deployment, production deployment, push, or migration is included.

## Rollback

Revert the Stage 6 source commit. Because both feature flags remain false and no runtime migration or
deployment occurred, rollback has no data-plane step. Never restore raw OTP logging as rollback.

## Next Stage

Stage 7 completes truthful role-aware WebApp/bot UX around invitation and registration states. It
also makes Telegram-first OTP status visible without changing the Stage 6 delivery authority,
timer, existing onboarding sequence, or user-facing Web URL ownership.
