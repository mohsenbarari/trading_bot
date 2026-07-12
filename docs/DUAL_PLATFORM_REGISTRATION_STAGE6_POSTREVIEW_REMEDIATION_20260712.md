# Dual-Platform Registration Stage 6 Post-Review Remediation

Date: 2026-07-12

Branch: `candidate/bot-webapp-integration`

Independent input:
`tmp/chatgpt/5dual-platform-registration-stage5-postreview-stage6-independent-review.md`

## Review Boundary

The independent report reviewed Stage 5/6 through `bb9b1c6c` and correctly accepted the final
Stage 5 remediation while rejecting the initial Stage 6 implementation. Stage 7 had subsequently
been completed in commit `b08383f0`; history was not rewritten. This remediation changes the Stage
6 owners underneath that already-completed Stage 7 UX and revalidates both together.

All registration and OTP feature flags remain off. No migration, deployment, push, staging action,
provider call, or production action is part of this remediation.

## Finding Disposition

### High findings

- `H-1` fixed: the existing foreign application guard now permits only the exact internal Telegram
  OTP prefix. ASGI tests prove foreign reaches endpoint validation, Iran rejects the foreign-only
  endpoint, and unrelated foreign Web APIs remain blocked.
- `H-2` fixed: SMS claim state now has a random generation ID, claim/lease timestamps, durable
  provider-start boundary, and same-generation result finalization. A stale pre-provider claim is
  reclaimable. After provider I/O starts, restart/result loss resolves to explicit ambiguous state
  without a blind resend.
- `H-3` fixed: due selection removes malformed, missing, and terminal prefix entries while scanning
  forward with a bounded budget. Real Redis coverage places 101 missing entries before valid work
  and proves that valid work is returned and the stale prefix is removed.

### Medium findings

- `M-1` fixed: new structured Redis keys and values contain no raw mobile, Telegram ID, or
  mobile-bearing code-key field. Mobile lookup uses HMAC and the recoverable SMS delivery target is
  authenticated-encrypted with a dedicated Iran-only secret. The legacy `otp:{mobile}` code of
  record remains unchanged as explicitly permitted by the review.
- `M-2` fixed: startup and request-level validation fail closed unless OTP lifetime is exactly 120
  seconds and enabled fallback is exactly 40 seconds. The Iran state secret is also mandatory when
  the feature is enabled.
- `M-3` fixed: request/429 responses expose structured absolute `expires_at` and
  `sms_fallback_at`. LoginView no longer parses localized error copy or decrements an authority
  counter. It computes from wall clock, resynchronizes on visibility changes, and restores an
  opaque request ID plus deadlines from session storage without persisting mobile PII.
- `M-4` fixed: verification captures request state before atomic consume and uses that immutable
  request ID for the success audit after consume removes the mobile pointer.
- `M-5` clarified and tested: explicit immediate SMS rejection invalidates the OTP because no
  channel accepted it. Explicit scheduled fallback rejection preserves the code because Telegram
  already accepted delivery. Ambiguous provider state always preserves verification but forbids
  automatic same-code retry.

### Low findings

- `L-1` retained with documented rationale: the inherited legacy staging-only log harness remains
  outside Stage 6. Stage 6 refuses to run with it enabled; maintained staging generation/deploy
  enforcement keeps it false. Removing unrelated legacy registration/login test behavior is not
  required for this remediation and raw logging remains forbidden for rollout.
- `L-2` remains assigned to Stage 8: dedicated lag, oldest-age, stale-cleanup, claim-age, and
  heartbeat metrics are observability work. The state defects that those metrics would expose are
  fixed here; no parallel metrics owner is introduced early.
- `L-3` fixed: a provider claim is cancelled when the code has less than 15 seconds remaining, so a
  severely delayed job does not send an effectively expired code.
- `L-4` intentionally unchanged: the historical migration and its versioned identity semantics are
  immutable. Future identity changes require a new migration rather than editing history.

## Safety Properties

- Iran remains the only OTP, User, Web session, and verification authority.
- Foreign remains only the Telegram delivery runtime.
- API and fallback job share one provider-attempt protocol; no second worker or provider path was
  added.
- Provider I/O never occurs before the durable start marker.
- A stale generation cannot finalize another generation's result.
- Verification still atomically consumes the one code and removes due work.
- Session storage contains only opaque request ID, method, and deadlines; no mobile or OTP.
- The dedicated state secret is rendered only into the Iran runtime.
- Stage 7 remains a status/UX consumer and never becomes a code, timer, SMS, or fallback authority.

## Verification Scope

Completed source evidence:

- focused Stage 6/config/ASGI backend: 40 passed;
- broader affected registration/auth/backend surface: 162 passed;
- isolated real Redis DB 15 lifecycle, privacy, lease, starvation, rejection, and minimum-TTL
  matrix: 8 passed, with DB size zero after teardown;
- focused LoginView timing/refresh suite: 25 passed;
- full backend: 2,913 passed, 54 opt-in skips, explicit exit code zero;
- full frontend: 128 files and 1,117 tests passed;
- production frontend build and UI guards passed;
- Stage 7 Playwright matrix: 15 passed across Chromium, Firefox, and WebKit;
- Python compile, `git diff --check`, flags-off/timing-default checks, and added-line raw-OTP-log
  scan passed. `ruff` was unavailable and is not claimed.

Real provider, proxy/TLS, mixed-version, true two-server restart, staging, and owner acceptance
remain later roadmap gates and are not claimed by source tests.

## Operational Gate

This remediation is source-only. It does not authorize Stage 8 rollout work, feature enablement,
migration, deployment, push, staging validation, production validation, or production release.
