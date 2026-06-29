# Validated Security Findings From Claude Audit

- Source report: `tmp/claude/deep-security-bug-audit-20260628-1544.md`
- Validation date: 2026-06-28
- Repository branch at validation time: `candidate/sync-parity-hardening`
- Validation mode: read-only code inspection plus read-only production DB checks. No exploit request, data mutation, deploy, commit, or git-state change was performed.
- Secret policy: no secret, token, key, OTP, password, or full env value is reproduced in this document.

## Executive Summary

Most of Claude's security concerns are valid. The most urgent confirmed risks are:

1. `POST /api/auth/dev-login` is present in the production API router, has no production/staging environment gate, trusts spoofable `X-Forwarded-For` for its local-request bypass, and can create/use a persistent `SUPER_ADMIN` developer user with a 365-day refresh session.
2. A production `DEV_API_KEY` value is present in a git-tracked documentation file and matches the local production `.env` value at validation time.
3. Chat file download authorizes only by JWT validity and does not verify that the requester is allowed to access the target chat file.
4. OTP verification has no failed-attempt counter or lockout and the OTP code is only 5 digits.
5. Several API-key/HMAC comparisons use regular equality instead of `hmac.compare_digest`.

Two corrections to Claude's report are important:

- The production DB check did **not** find an existing user with mobile `09999999999` on either foreign or Iran production DB at validation time. The code path can create it, but it was not currently present.
- Claude's F6 wording says `X-Forwarded-For` can influence `_login_home_server`; the current code does not do that directly. `_login_home_server()` delegates to `server_from_request()`, which is host/header based. However, that host resolver itself trusts `X-Forwarded-Host` / `X-Original-Host` before `Host`, so the broader header-trust concern remains valid and should be fixed together with client-IP parsing.

## Confirmed Findings

### VF1 - Production `dev-login` Can Grant SUPER_ADMIN Access Through Spoofable Local-IP Logic

- Severity: Critical
- Status: Confirmed by code and proxy config. Not exploit-tested to avoid mutating production.
- Area: Auth, production/staging boundary
- Files:
  - `api/routers/auth.py:207-217`
  - `api/routers/auth.py:800-855`
  - `main.py:440-445`
  - `nginx.conf:19-28`
  - `deploy/production/nginx-iran-online.conf.template:25-33`
  - `deploy/production/nginx-iran-online-https.conf.template:40-48`

Evidence:

- `dev_login` is mounted under `/api/auth/dev-login` through the auth router.
- There is no `settings.environment` gate in `dev_login`.
- `_extract_request_real_ip()` takes the first comma-separated value from `x-forwarded-for`.
- `_is_local_dev_request()` treats loopback and private ranges as local.
- `dev_login` allows access if the request is considered local or if `X-DEV-API-KEY` matches.
- The Nginx configs proxy all `/api/` requests and set `X-Forwarded-For` with `$proxy_add_x_forwarded_for`, which appends the real IP after any client-supplied value.
- On success, the endpoint uses mobile `09999999999`, role `SUPER_ADMIN`, device `Dev Bypass Terminal`, and a refresh session expiring after 365 days.

Impact:

- A remote caller may be able to spoof `X-Forwarded-For: 127.0.0.1` and satisfy the local-development condition.
- If the dev key is also leaked, this endpoint is accessible even without relying on IP spoofing.
- Successful access grants administrative tokens.

Recommended fix:

- Make `dev-login` unavailable in production, preferably 404/disabled unless `settings.environment in {"staging", "development"}`.
- Remove the IP-only bypass. If any emergency dev path remains, require an environment-scoped secret and use `hmac.compare_digest`.
- Use a centralized trusted-proxy client-IP resolver for all security decisions.

Suggested tests:

- In production config, `/api/auth/dev-login` returns 404/403 for every request.
- Spoofed `X-Forwarded-For` never makes a remote request local.
- Staging-only behavior stays available only when explicitly enabled.

### VF2 - Production `DEV_API_KEY` Is Present In Tracked Documentation And Matches Local `.env`

- Severity: Critical
- Status: Confirmed. Secret value intentionally omitted.
- Area: Secrets hygiene, admin bypass, sync resync
- Remediation execution log: `docs/SECURITY_DEV_API_KEY_HISTORY_REWRITE_ROADMAP_20260628.md`
- Files:
  - `.github/copilot-instructions.md:107`
  - `api/deps.py:189-224`
  - `api/routers/sync.py:43-47`
  - `api/routers/sync.py:3246-3265`
  - `api/routers/auth.py:806-808`

Evidence:

- `.github/copilot-instructions.md` is tracked by git.
- Line 107 contains a backtick-wrapped `DEV_API_KEY` value.
- A safe comparison confirmed this documented value matched the local production `.env` value at validation time.
- `verify_super_admin_or_dev_key()` and `verify_admin_or_dev_key()` return system/admin access when this key matches.
- `/api/sync/resync` is guarded by this dev key.
- `/api/auth/dev-login` also accepts this key.

Impact:

- Anyone with access to repository history or the document can possess a production admin bypass credential.
- This affects admin endpoints, commodity/admin routes using the dev-key dependency, sync resync, and dev-login.

Recommended fix:

- Rotate production `DEV_API_KEY` immediately.
- Remove the literal from tracked documentation and replace it with an env-var reference only.
- Treat the old value as compromised even after removal because it exists in git history.
- Add a secret-lint guard to prevent future tracked `DEV_API_KEY` values.

Suggested tests:

- Static check fails if tracked files contain a literal `DEV_API_KEY` value.
- Dev-key comparison uses `hmac.compare_digest`.

### VF3 - Chat File Download Lacks Per-File Authorization

- Severity: High
- Status: Remediated with backend-only authorization gate
- Area: Messenger, privacy, file access control
- Files:
  - `api/routers/chat.py:2486-2517`
  - `models/chat_file.py:7-29`

Evidence:

- `GET /api/chat/files/{file_id}` requires a `token` query parameter.
- The handler only calls `jwt.decode(...)` to check token validity.
- It does not read `sub`, does not load the requester user, and does not verify direct/group/channel membership.
- It then loads `ChatFile` by `file_id` and returns `FileResponse`.
- `ChatFile` itself stores `id`, `uploader_id`, file path/name/mime/size/thumbnail, but no direct room/chat ownership relation.

Impact:

- Any user with any valid JWT may download a file if they know or obtain the `file_id`.
- UUID ids reduce guessing risk, but this is still broken object-level authorization.

Recommended fix:

- Decode and validate the access-token subject.
- Load the requester and reject deleted/inactive users.
- Link file access to message/chat/upload-session ownership and verify direct/group/channel membership before streaming.
- Reject refresh tokens or non-access token types if token type can be distinguished.

Suggested tests:

- Uploader/participant can download.
- Non-participant gets 403.
- Deleted/inactive user gets 403.
- Expired/invalid token gets 401.

Remediation update:

- `GET /api/chat/files/{file_id}` now decodes and requires a valid numeric token subject before file lookup/streaming.
- Access is allowed for the uploader, visible user avatars, active chat avatars, and messages visible to the requester.
- Message visibility covers legacy/direct messages via sender/receiver and room messages via active group/channel membership.
- The frontend URL contract stayed unchanged: `/api/chat/files/{file_id}?token=...`.
- This pass intentionally avoided a schema migration. Existing media is resolved through `messages.content` JSON references for `file_id` and `snapshot_id`.
- Added focused regression tests for uploader success, user-avatar visibility, direct-message recipient access, active group-member access, and nonparticipant denial.

### VF4 - OTP Verification Has No Failed-Attempt Limit

- Severity: High
- Status: Remediated in follow-up hardening
- Area: Auth
- Files:
  - `api/routers/auth.py:916-930`
  - `api/routers/auth.py:1057-1070`

Evidence:

- OTP generation uses a 5-digit numeric code.
- OTP is stored for 120 seconds.
- `verify_otp` compares the stored code to user input and immediately returns a generic invalid/expired error on mismatch.
- No Redis failure counter, per-mobile verify throttle, per-IP verify throttle, lockout, or OTP invalidation-after-N-failures is present in the verification path.

Impact:

- A targeted attacker can brute-force a 5-digit OTP during its validity window if request volume is not otherwise controlled by infrastructure.

Recommended fix:

- Add Redis counters for failed OTP verification by mobile and by client IP.
- Invalidate OTP after a small number of failures.
- Add cooldown/429 behavior.
- Consider moving to a 6-digit OTP.

Suggested tests:

- After N wrong attempts, correct code no longer succeeds.
- Counter expires with the OTP.
- Successful verification clears failure counters.

Remediation notes:

- Added Redis-backed failed verification counters for login OTP by subject/mobile digest and client-IP digest.
- Added the same subject-level protection for registration OTP tokens so invitation registration cannot repeatedly brute-force a live code.
- After 5 failed attempts for the same login/registration subject, the live OTP is invalidated and a 5-minute lockout is set.
- A broader per-IP counter is also tracked for login OTP verification and locks the IP after excessive failures in the OTP validity window.
- Successful login OTP verification clears the subject failure counter before continuing session creation or registration handoff.
- OTP comparison now uses the shared constant-time secret comparison helper.
- The OTP length was intentionally left unchanged in this pass to avoid SMS template and UX churn while the brute-force window is now bounded.

### VF5 - OTP Digits Are Partially Logged

- Severity: Low individually; increases risk when combined with VF4.
- Status: Remediated in follow-up hardening
- Area: Logging, auth privacy
- Files:
  - `api/routers/auth.py:921-927`

Evidence:

- Active/generated OTP logs include the first two digits and mask only the remaining digits.

Impact:

- Operators or log readers see 2 of 5 OTP digits, reducing the effective search space.

Recommended fix:

- Log only state such as "OTP exists" or "OTP generated", never any OTP digit.

Remediation notes:

- Login OTP request logs no longer include any OTP prefix or masked digit.
- Login OTP resend logs no longer include any OTP prefix or masked digit.
- Focused tests assert that generated/resend OTP values do not appear in captured auth logs.
- The staging-only `STAGING_AUTH_VALUE_FOR_TEST_ONLY` path remains intentionally allowed only when `settings.environment == "staging"` and `staging_log_otp_codes` is enabled.

### VF6 - Secret And Signature Comparisons Use Regular Equality

- Severity: Medium
- Status: Remediated in follow-up hardening
- Area: Auth, sync
- Files:
  - `api/deps.py:189-224`
  - `api/routers/sync.py:43-47`
  - `api/routers/sync.py:400-448`
  - `api/routers/auth.py:806-808`
  - `main.py`
  - `api/routers/commodities.py`
  - `core/session_authority.py`
  - `core/trade_forwarding.py`

Original evidence:

- `DEV_API_KEY`, sync API key, and HMAC signature comparisons used `==` or `!=` before the follow-up hardening pass.
- `core/session_authority.py` and `core/trade_forwarding.py` already use `hmac.compare_digest`, so there is an established safer local pattern.

Impact:

- Timing attacks are usually hard over the network, but this is a standard cryptographic hygiene issue and easy to fix.

Recommended fix:

- Use `hmac.compare_digest` for all API-key and signature comparisons.
- Normalize missing values safely before comparison.

Remediation update:

- Added `core.security.constant_time_secret_equals()` as the shared API-key comparison helper.
- Replaced direct secret comparisons for dev-key, sync API key, observability key, session-authority key, trade-forwarding key, and sync HMAC signature checks.
- Hardened commodities request-source detection so an arbitrary `X-DEV-API-KEY` header is no longer enough to classify a request as bot-origin.
- Added focused unit coverage and a tracked-file secret-lint test for future `DEV_API_KEY` regressions.

### VF7 - Header Trust Is Inconsistent And Should Be Centralized

- Severity: Medium
- Status: Remediated in follow-up hardening
- Area: Auth, routing, audit attribution
- Files:
  - `api/routers/auth.py:203-217`
  - `core/request_logging.py:102-139`
  - `core/server_routing.py:49-63`

Evidence:

- `auth.py` has its own `_extract_request_real_ip()` and trusts the first `X-Forwarded-For` entry.
- A trusted-proxy parser already exists in `core/request_logging.py`.
- `_login_home_server()` does not directly use `X-Forwarded-For`; Claude's wording is not exact here.
- However, `server_from_request()` prefers `X-Forwarded-Host` / `X-Original-Host` before `Host`, and those headers should also be trusted only when they come from a trusted proxy.

Impact:

- Spoofed `X-Forwarded-For` directly affects `dev-login` and stored `device_ip`.
- Spoofed host-forwarding headers may affect server-affinity/home-server inference if passed through by the proxy.

Recommended fix:

- Centralize request-origin parsing into one utility.
- Only honor forwarded IP/host headers when the direct peer is a configured trusted proxy.
- Ensure production `TRUSTED_PROXY_CIDRS` includes the actual Nginx/container hop.

Remediation notes:

- Reused `core.request_logging.client_ip_from_request()` as the shared request IP parser for auth device attribution, dev-login locality checks, and login OTP throttling.
- Added `core.request_logging.trusted_forwarded_host_from_request()` and connected `core.server_routing._host_from_request()` to it.
- `X-Forwarded-Host` and `X-Original-Host` are now ignored unless the direct peer matches `TRUSTED_PROXY_CIDRS`.
- Added focused tests for trusted/untrusted forwarded host behavior and kept existing forwarded-IP trust tests active.

### VF8 - Localhost CORS Origins Are Always Included

- Severity: Low
- Status: Confirmed
- Area: CORS, frontend/backend surface
- Files:
  - `core/deployment_surface.py:6-11`
  - `core/deployment_surface.py:76-91`
  - `main.py:379-384`

Evidence:

- `allowed_cors_origins()` always starts from `LOCAL_DEVELOPMENT_ORIGINS`.
- CORS middleware allows credentials.
- No environment check removes localhost origins in production.

Impact:

- Lower severity than the auth issues, but production should not trust dev origins.

Recommended fix:

- Include localhost origins only in development/staging.

### VF9 - `dev-login` Persistent SUPER_ADMIN Design Risk Exists, But No Current Production User Was Found

- Severity: Code design risk; not an active data finding at validation time
- Status: Partially confirmed
- Area: Auth, data hygiene
- Files:
  - `api/routers/auth.py:810-855`

Evidence:

- Code can create mobile `09999999999` as role `SUPER_ADMIN`.
- Code creates a 365-day session.
- Read-only DB checks on both foreign and Iran production DBs found no current user with mobile `09999999999` and no sessions for that user.

Impact:

- The current database is not polluted by this dev user, but the endpoint can create it if called.

Recommended fix:

- Close VF1 first. Then add a production reconciliation check that this user cannot exist.

## Findings Not Confirmed Or Corrected

### NC1 - Existing Production Dev SUPER_ADMIN

- Claude asked for human confirmation on whether the `09999999999` dev superadmin exists.
- Read-only checks found it does not exist on foreign or Iran production DB at validation time.

### NC2 - F6 Exact Home-Server Claim

- Claude's broad concern about spoofable forwarding headers is valid.
- The specific statement that `X-Forwarded-For` influences `_login_home_server` is not exact in current code.
- Home-server selection is host-based through `server_from_request()`, but it still trusts forwarded host headers.

## Recommended Fix Order

1. Rotate `DEV_API_KEY` in production and remove the literal from tracked documentation.
2. Disable `dev-login` in production and remove the IP-only bypass.
3. Add authorization checks to chat file download.
4. Add OTP verify throttling/lockout and stop logging OTP digits. Completed; keep regression tests active.
5. Replace secret/signature equality checks with `hmac.compare_digest`. Completed; keep regression tests active.
6. Centralize trusted proxy handling for IP and host headers. Completed; keep regression tests active.
7. Remove localhost CORS origins in production.
8. Add production data hygiene checks for dev/test users and fixture prefixes.

## Suggested Verification Gates

- Unit tests for production-disabled `dev-login`.
- Unit tests for spoofed `X-Forwarded-For` and `X-Forwarded-Host`.
- Unit/integration tests for chat file authorization.
- OTP lockout tests.
- Static secret scan for tracked docs/config.
- Production read-only check: no `09999999999`, no `dev_` superadmin, no test fixture prefixes.
