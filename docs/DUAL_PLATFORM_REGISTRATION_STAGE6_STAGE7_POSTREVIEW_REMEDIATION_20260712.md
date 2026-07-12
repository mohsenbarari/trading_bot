# Stage 6/7 Final Independent Review Remediation

Date: 2026-07-12

Branch: `candidate/bot-webapp-integration`

Reviewed baseline: `cf1d728ae03df94a4f79dba952718b7f1300236b`

Stage 8 source commit: `fe35285e`

Review input:
`tmp/chatgpt/6-dual-platform-registration-stage6-remediation-stage7-independent-review.md`

Controlling roadmap:
`docs/DUAL_PLATFORM_REGISTRATION_AND_SYNCHRONIZED_OTP_ROADMAP_20260710.md`

## Review Method

The report was not accepted or rejected as a whole. Each finding was checked against the exact
Stage 6/7 source retained in the current branch and then against the later Stage 8 changes. Four
current defects were confirmed. One rotation observation is retained as an operational gate. The
Low observations do not justify new product authority or unrelated scope.

## Confirmed And Corrected

### H-1: corrupt pending state could poison the due prefix

Confirmed. The old selector checked only `status=pending`, and one exception from state parsing or
delivery-target decryption could abort the whole gathered cycle while leaving the member due.

Correction:

- `select_due_otp_requests` now validates the complete state contract and authenticated delivery
  target before returning work;
- invalid UUID, missing/terminal state, malformed contract, invalid ciphertext, HMAC mismatch, and
  old/partial state are removed from the due set;
- an atomic isolation script changes only a still-pending hash to terminal `expired`, writes one
  bounded `terminal_reason`, and removes the due member;
- selector cleanup reports only bounded reason counts; audit/log/metrics contain no identity;
- each selected worker item has its own pre-provider exception boundary, so one late corruption
  cannot abort unrelated work;
- no invalid/unverifiable target can reach provider I/O.

Real Redis evidence covers 101 partial hashes before valid work, malformed timestamp, invalid
ciphertext, HMAC mismatch, old/partial schema, secret rotation, concurrent isolation, more than the
500-item scan budget, and continued valid progress. Cleanup remains bounded: a prefix larger than
the budget advances across cycles rather than turning one cycle into unbounded Redis/CPU work.

### M-1: provider-started near-expiry claim was mislabeled cancelled

Confirmed. The minimum-code-TTL branch preceded stale-claim/provider-start inspection.

Correction:

- the Lua script now resolves live/stale claim ownership and provider-start evidence first;
- an expired lease with a provider-start marker becomes terminal delivery `ambiguous` and is never
  resent, even when code TTL is below 15 seconds or the code has just expired;
- minimum-TTL `cancelled` applies only to work that has not crossed provider I/O.

Real Redis combines provider-started, expired lease, and five-second code TTL. It proves ambiguous,
no due recurrence, and code availability until natural Redis expiry.

### M-2: staging shared env exposed the Iran-only state secret

Confirmed. `.env.staging` is both Compose interpolation input and a shared service `env_file`, so a
secret under either source or runtime variable name would reach foreign containers without explicit
overrides.

Correction:

- the staging input is `IRAN_OTP_DELIVERY_STATE_SECRET`;
- only the Iran `app` maps it to `OTP_DELIVERY_STATE_SECRET`;
- the source alias is cleared inside the Iran app after interpolation;
- both source and runtime names are explicitly empty in every other service that consumes the
  shared env file, including foreign app/worker/bot and non-owner Iran utility services;
- a Compose-structure test enumerates every service instead of checking one example string.

No real secret is added to source or evidence.

### M-3: flags-off legacy OTP was presented as automatic fallback

Confirmed. An unstructured legacy `method=telegram` response did not schedule automatic SMS, but
the client invented a 40-second deadline and hid the existing manual SMS resend. Legacy 429 could
also strand a valid code after refresh or response loss.

Correction:

- automatic fallback UI now requires the authoritative absolute `sms_fallback_at` field;
- structured Stage 6 Telegram responses retain the 40-second countdown and no manual resend;
- legacy Telegram responses retain the previous 30-second manual resend and reuse the existing
  `/api/auth/resend-otp-sms` endpoint;
- flags-off active OTP returns a no-store structured 429 with `code=otp_active`, legacy capability,
  remaining TTL, and absolute expiry, but never the OTP value;
- the new client enters OTP without parsing localized text; the numeric detail also preserves the
  bounded old-client recovery behavior;
- refresh/lost-response recovery keeps verification on the existing mobile-owned legacy path.

Frontend tests cover truthful legacy copy, manual resend, structured automatic fallback, zero-state
behavior, structured flags-off 429 recovery, and refusal to derive authority from localized copy.

## Accepted As Operational Gate

### M-4: active-request state-secret rotation

The observation is valid, but current+previous key support is not proportionate for an Iran-local
state with a fixed 120-second lifetime. Poison isolation now prevents old-key rows from starving
unrelated work, but active old-key requests would still lose request-ID recovery/fallback.

Stage 10 must enforce a rotation drain gate:

1. stop new Stage 6 OTP requests at the existing maintenance/traffic gate;
2. keep the current worker and key running for at least 120 seconds plus the 30-second claim lease;
3. verify the fallback due queue is empty and no claim is live;
4. rotate the secret and restart Iran;
5. restore traffic only after health and one real delivery acceptance pass.

No in-place secret rotation while Stage 6 traffic is active is authorized. If future operations need
zero-drain rotation, a separately reviewed key-ID/current+previous-key state version is required.

## Low Finding Disposition

- More than 500 stale rows: retained as bounded multi-cycle cleanup and now proven with real Redis;
  an unbounded cycle would be the larger availability risk.
- Session-storage typing: no source change. Storage is non-authoritative, contains no mobile/OTP,
  malformed/expired tuples clear locally, and backend UUID/schema/rate-limit checks remain final.
- Narrow near-expiry delivery window: retained with the existing minimum 15-second pre-provider
  guard. The confirmed provider-started misclassification was fixed separately.
- Historical migration semantics: unchanged; versioned history is not rewritten.

## Stage 8 Interaction

Stage 8 was committed independently before this review was processed. The remediation reuses its
bounded OTP event metrics and audit path for corruption-isolation counts. It does not change Stage 8
job ownership, alert thresholds, health API, Redis snapshot schema, dashboard scope, or its explicit
open questions about passive `otp.expired` evidence and process-local event counters.

## Verification Plan

Required final evidence for the exact remediation commit:

- focused Stage 6/7/8 backend matrix;
- isolated real Redis DB 15 suite and zero DB size after teardown;
- focused LoginView suite;
- full frontend unit suite and production build;
- full backend suite;
- Compose/YAML, Python compile, and `git diff --check`;
- secret/PII static scan.

No migration, feature enablement, provider call, staging deploy, production deploy, push, or merge is
part of this remediation.

## Final Evidence

Evidence for the exact source immediately before the remediation commit:

- focused Stage 6/7/8 backend, deployment, health, and alert matrix: 243 passed;
- isolated real Redis DB 15: 15 passed, with DB size zero after teardown;
- full backend: 2,940 passed, 62 skipped opt-in/integration cases;
- focused LoginView: 27 passed;
- full frontend: 128 files and 1,119 tests passed;
- production frontend build: passed;
- Playwright Stage 7/8 compatibility matrix: 21 passed across Chromium, Firefox, and WebKit on
  390x844 mobile and 1440x1000 desktop viewports;
- generated legacy mobile/desktop screenshots were visually checked for copy, fit, overflow, and
  overlap;
- Python compile, staging Compose secret-boundary parse, and `git diff --check`: passed.

The first Playwright attempt could not bind its local dev server inside the filesystem/network
sandbox and ran zero tests. The approved local-binding rerun passed all 21 cases; no failed product
case is hidden by the rerun. Raw logs and screenshots are included in the combined review package.
