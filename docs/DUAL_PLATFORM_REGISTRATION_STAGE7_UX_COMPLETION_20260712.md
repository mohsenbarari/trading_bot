# Dual-Platform Registration Stage 7 UX Completion

Date: 2026-07-12

Branch: `candidate/bot-webapp-integration`

Controlling roadmap:
`docs/DUAL_PLATFORM_REGISTRATION_AND_SYNCHRONIZED_OTP_ROADMAP_20260710.md`, Stage 7 near line 1667.

## Scope And Safety Boundary

Stage 7 completes the source-level WebApp and bot-facing UX contract for dual-platform
registration. It does not create a new registration authority, OTP authority, worker, outbox,
sync path, session path, or manual-review mechanism. Iran remains authoritative for invitation and
User decisions; foreign remains the Telegram runtime. Existing direct-registration and
reconciliation services from Stages 4-5 and the synchronized OTP state machine from Stage 6 are
reused without changing their ownership.

All new feature flags remain disabled. This stage performs no migration, deployment, staging flag
enablement, push, or production action.

## Implemented Behavior

### Public invitation state

- Added a strict, PII-bounded public contract with `valid`, derived `state`, invitation `kind`,
  stored expiry, and role-aware `bot_available`/`web_available` values.
- Pending public responses contain only the existing token plus masked mobile and bounded account
  metadata. Completed, expired, and revoked responses contain no token, account name, mobile, or
  registration action.
- Legacy lookup/validate behavior is unchanged while `INVITATION_CONTRACT_V2_ENABLED=false`.
- With contract V2 enabled, completed invitations return a terminal state that the WebApp can route
  to login instead of rendering a second registration form.
- Admin contract links are suppressed after an invitation becomes terminal, preventing dead
  registration links while preserving full pending links for authorized manual sharing.

### Web invitation and registration UX

- `InviteLanding` consumes explicit V2 fields first and still tolerates legacy `token` responses.
- Telegram and Web actions render only when the contract allows them. Accountant, Tier-2, and any
  other Web-only invitation cannot display a Telegram action.
- Failure to load Telegram config hides only the Telegram action; an available Web path remains
  usable.
- The stored invitation expiry is displayed through the existing Iran timezone formatter.
- Completed invitations route to the existing `/login?registration=complete` surface. LoginView
  explains that registration was already completed and keeps the normal mobile OTP login form.
- `WebRegister` applies the same terminal routing and never renders registration fields for a
  completed or Web-ineligible invitation.

### Admin links and invitation SMS truth

- A shared frontend normalizer prefers `bot_link`, `web_short_link`, availability, state, and
  `sms_status`, while retaining temporary `link`/`short_link` compatibility.
- Standard, customer, and accountant pending invitation views expose only the links permitted by
  the contract. Tier-1 customers can show both Telegram and Web links; accountant and Tier-2
  invitations remain Web-only.
- Canonical API Web links are preserved. The frontend no longer rewrites an arbitrary response
  origin to the browser origin, so a foreign URL cannot be disguised as a local Iran link.
- Durable invitation SMS outcomes are read without sending again. `disabled`, `pending`,
  `accepted`, `failed`, and `ambiguous` each have truthful bounded copy. An ambiguous outcome never
  causes an automatic resend.
- Existing enabled accountant/Tier-2 SMS templates and disabled Standard/admin/Tier-1 policy are
  unchanged.

### OTP and canonical WebApp URL

- LoginView uses the backend method and one existing countdown. For Telegram delivery it states
  that the same code will be sent automatically by SMS after 40 seconds and never shows the legacy
  manual resend action at zero.
- No polling, second timer, code generation, fallback scheduling, or provider call was added to the
  frontend. The Stage 6 Iran background job remains the only automatic fallback owner.
- A shared backend helper selects the validated Iran `PUBLIC_WEBAPP_URL` whenever any new
  registration flow is enabled. With all new flags off it preserves the legacy `frontend_url`.
  Bot start, account linking, and panel menus reuse this helper instead of duplicating URL policy.

## Compatibility And No-Change Guarantees

- Contract V1 clients continue to receive their existing behavior while the V2 flag is off.
- V2 admin responses retain temporary `link` and `short_link` aliases.
- Existing bot contact, address, tutorial, welcome, channel, and linked-account sequences are not
  changed.
- Existing Web OTP code TTL remains 120 seconds; the 40-second UI value remains a delivery fallback
  delay only.
- Existing pending-invitation management, cancellation, PWA/service-worker behavior, and session
  approval/recovery paths remain in place. No support dashboard, force-create/link action, or
  manual-review state was added.

## Verification Evidence

- Focused backend registration/router/bot tests: 95 passed.
- Focused frontend Stage 7 tests: 9 files and 96 tests passed.
- Full backend suite: 2,901 passed, 49 skipped. Dockerfile smoke was executed with build-only Docker
  daemon access; no deploy occurred.
- Full frontend unit suite: 128 files and 1,114 tests passed.
- Production frontend build passed; only the pre-existing large-chunk and stale Browserslist data
  warnings remained.
- UI design guards passed: design tokens, trade-side colors, and modal-overlay guards.
- Focused Stage 7 Playwright matrix: 15 passed across Chromium, Firefox, and WebKit.
- The Playwright matrix covered 390x844 and 1440x1000 viewports, both-link and Web-only invitation
  states, completed-invitation OTP routing, Telegram-first countdown text, absence of manual resend,
  and horizontal-overflow assertions.
- Mobile and desktop screenshots were inspected for clipping, overlap, readable RTL text, and
  stable control layout.
- `git diff --check` passed. No env, migration, compose, or deployment file changed.

## Deferred Gates

- Stage 8 owns redacted observability and job-health reporting. Stage 7 adds no dashboard or health
  architecture.
- Stage 9 owns mixed-version, restart, real two-server, provider, and infrastructure validation.
- Stage 10 owns staging deployment and narrow flag enablement.
- Stage 11 owns manual product acceptance with real Telegram and SMS delivery.
- Stage 12 owns production readiness and the explicit production gate.

This record is source-completion evidence only. It does not declare the full roadmap complete and
does not authorize flag enablement, migration, staging deployment, production deployment, or push.
