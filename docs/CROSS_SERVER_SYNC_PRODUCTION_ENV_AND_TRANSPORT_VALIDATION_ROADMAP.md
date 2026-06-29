# Cross-Server Sync Production Env And Transport Validation Roadmap

Created on: 2026-06-28
Branch: `candidate/sync-parity-hardening`
Related roadmap: `docs/CROSS_SERVER_SYNC_PARITY_FOLLOWUP_REMEDIATION_ROADMAP.md`

This roadmap documents the remaining production-safety work discovered after
the sync parity hardening investigation:

- Iran production runtime loaded a retired foreign peer URL
  (`https://kharej.362514.ir`) from a stale project-root env file.
- Foreign production sent a delayed Telegram market-open notice because the new
  market notice receipt table was empty at rollout time and the current-state
  repair logic treated an old transition as unsent work.
- Market schedule settings are synced, but the current foreign runtime behavior
  still waits for Iran `market_runtime_state` sync before closing the Telegram
  market surface. If the Iran/foreign link drops shortly before market close,
  Telegram can incorrectly remain open until sync resumes.
- Production deploy tooling does not yet have a hard release gate that proves
  runtime identity values match the canonical Iran/foreign topology before
  containers are recreated.
- The previous staging evidence proved receiver behavior and worker behavior,
  but one final production-like validation must still prove the real
  worker-to-remote-receiver path through the configured peer URL, TLS, and
  routing.

This document is intentionally operational. It must be completed before using
this branch for production rollout or strict production parity confidence.

## Non-Negotiable Guardrails

- Verify the branch before any code/documentation change:
  `git branch --show-current` must be `candidate/sync-parity-hardening` unless
  the user explicitly requests another branch.
- Do not store secrets, tokens, API keys, bot tokens, database passwords, or
  full env files in this document, commits, logs, or validation artifacts.
- Do not recreate, remove, or prune production DB/Redis containers while
  applying the env correction. Only stateless app/sync-worker containers may be
  recreated for the runtime env reload.
- Do not rely on project-root `.env` or `.env.iran` as production authority.
  Canonical production env files must live under the secure env directory.
- Iran must never connect to Telegram. Telegram channel side effects remain
  foreign-owned.
- A late market open/close notice must not be sent hours after the real
  transition. If a notice is too old, record it as intentionally suppressed or
  backfilled, not as a live Telegram message.
- Validation must distinguish three things:
  - DB row parity;
  - side-effect receipt parity/repair;
  - real transport reachability from worker to remote receiver.
- Final worker-to-remote-receiver validation must use normal TLS verification.
  `curl -k`, `verify=False`, or insecure TLS flags are not acceptable evidence
  for the final gate.
- A short Iran/foreign outage must not keep Telegram open past the already
  synced market close schedule. The foreign server must either act on the
  synced schedule independently for local Telegram-side closure, or enter a
  clearly defined short-outage local closed mode.

## Incident Evidence Already Collected

### Retired Foreign URL Loaded On Iran

Runtime evidence showed Iran production sync worker using:

- `FOREIGN_SERVER_URL=https://kharej.362514.ir`
- `FOREIGN_SERVER_DOMAIN=kharej.362514.ir`
- `IRAN_SERVER_URL=https://iran.362514.ir`
- `IRAN_SERVER_DOMAIN=iran.362514.ir`
- `FRONTEND_URL=https://iran.362514.ir`

That retired domain does not resolve, so Iran-to-foreign sync retry could not
deliver the market close transition.

Repository evidence showed the only tracked reference to `kharej.362514.ir` is
inside the deployment-surface guard as a retired identity. The bad runtime
values came from an untracked/stale project-root `.env.iran` that was copied to
Iran runtime during the DEV API key rotation. The secure env copy was correct,
but the runtime `.env` was not replaced from the canonical secure env before
container recreation.

### Delayed Telegram Market-Open Notice

Foreign production had candidate market notice receipt code while Iran
production was still on the main behavior, so production was split:

- foreign DB migration included `market_channel_notice_receipts`;
- Iran DB migration did not include that table;
- foreign market schedule loop had current-state market notice reconciliation;
- Iran market schedule loop did not.

Foreign runtime was already open from the real morning transition, but the
receipt table was empty on rollout. After a foreign app restart, the
current-state reconciliation saw the current `opened` state and sent the old
open notice at 21:14 Iran time. The actual transition timestamp was the morning
open, not 21:14.

Root cause: receipt backfill/rollout logic was missing. The repair path was
idempotent, but it did not apply a staleness guard or seed receipts for already
completed historical transitions.

## Implementation Status - 2026-06-29

Current branch: `candidate/sync-parity-hardening`.

Completed in code on this branch:

- Stage V0 evidence snapshot was collected under `tmp/` with secret values
  excluded from the committed repository.
- Stage V2 deployment-surface identity guard now validates production runtime
  env identity values for both Iran and foreign before production container
  recreation. It rejects retired identities, mismatched URL/domain pairs, and
  project-root env sources unless an explicit emergency flag is supplied.
- Stage V3 market channel notice staleness protection now records stale
  market open/close notices as `suppressed_stale` instead of sending them to
  Telegram.
- Stage V3A foreign short-outage autonomy now lets the foreign market schedule
  loop evaluate the already-synced schedule and run foreign-local Telegram
  side effects after the configured grace period without writing
  `market_runtime_state`.
- Telegram trade-request validation already uses schedule evaluation through
  `evaluate_current_market_schedule`, so close-time trade blocking is based on
  the synced schedule and does not wait for Iran `market_runtime_state`.

Focused tests added/updated:

- deployment-surface guard tests for valid identities, retired identities,
  project-root env rejection, mismatched domain/URL pairs, and secret-safe
  failure output;
- production deploy smoke tests proving the real release path invokes runtime
  identity validation and does not recreate stateful Iran services;
- market schedule tests for `current_transition_at`;
- market transition tests for stale notice suppression, fresh retry behavior,
  and foreign autonomy after the grace window;
- market schedule loop tests for the updated foreign path.

Important sequencing decision:

- Stage V1 runtime env correction must remain deferred until the Stage V2/V3/V3A
  code is deployed. The current Iran production backlog contains stale market
  runtime transitions from the retired peer URL incident; correcting routing
  before stale-notice suppression is active could replay old open/close channel
  notices as live Telegram messages.

## Stage V0 - Freeze And Evidence Snapshot

Goal: capture the exact pre-fix state before touching production runtime.

Deliverables:

- Record branch, local git status, local HEAD, and remote branch HEAD.
- On Iran production, record only non-secret identity fields from:
  - `/srv/trading-bot/current/.env`;
  - `/root/secure-envs/trading-bot/.env.iran.production`.
- On foreign production, record only non-secret identity fields from:
  - `/srv/trading-bot/current/.env`;
  - `/root/secure-envs/trading-bot/.env.foreign.production`.
- Record current app/sync-worker container names and image IDs on both servers.
- Record sync backlog:
  - Redis `sync:outbound`;
  - Redis `sync:retry`;
  - unsynced `change_log` count;
  - latest unsynced `market_runtime_state` row, if present.
- Record market runtime state and last market notice receipt rows on foreign.

Exit criteria:

- Evidence is saved under `tmp/` with secrets redacted.
- Evidence is not committed unless it contains no secrets and is intentionally
  summarized.

## Stage V1 - Immediate Iran Runtime Env Correction

Goal: restore Iran production peer routing without changing DB/Redis state.

Required actions:

1. Back up Iran runtime env in place with a timestamped filename under
   `/srv/trading-bot/current/`.
2. Copy the canonical secure Iran production env from
   `/root/secure-envs/trading-bot/.env.iran.production` to
   `/srv/trading-bot/current/.env`.
3. Set the runtime env file permission to owner-readable only.
4. Recreate only stateless Iran containers:
   - app;
   - sync worker.
5. Do not recreate DB, Redis, uploads, volumes, or reverse proxy unless a
   separate failure proves it is necessary.

Expected canonical Iran identity values:

- `SERVER_MODE=iran`
- `FOREIGN_SERVER_URL=https://coin.362514.ir`
- `FOREIGN_SERVER_DOMAIN=coin.362514.ir`
- `IRAN_SERVER_URL=https://coin.gold-trade.ir`
- `IRAN_SERVER_DOMAIN=coin.gold-trade.ir`
- `FRONTEND_URL=https://coin.gold-trade.ir`

Post-reload checks:

- Inside Iran app and sync-worker containers, routing helpers must resolve the
  foreign peer to `https://coin.362514.ir`.
- Iran host DNS must resolve `coin.362514.ir`.
- Iran host HTTPS request to the foreign peer must pass normal TLS verification.
- Iran sync retry queue must drain.
- The previously stuck Iran `market_runtime_state` close row must become
  `synced=true` and `verified=true`.
- Foreign `market_runtime_state` must converge to the Iran close state.

Expected user-facing side effect:

- Stage V1 must not be executed on production until Stage V3 stale-notice
  suppression is active. After sync resumes, any old close/open notice outside
  the freshness window must be recorded as `suppressed_stale`, not sent to the
  Telegram channel.

Exit criteria:

- Iran routing uses the canonical foreign peer.
- Sync backlog created by the bad retired URL is drained or has a documented
  non-routing blocker.
- No DB/Redis container was recreated.

## Stage V2 - Deployment-Surface Identity Guard

Goal: prevent retired or mismatched production identity values from reaching
runtime again.

Implementation direction:

- Add a production identity manifest to deployment tooling. The manifest must
  define allowed identity values for each production role:
  - Iran role;
  - foreign role.
- Validate these fields before any production container recreation:
  - `SERVER_MODE`;
  - `FOREIGN_SERVER_URL`;
  - `FOREIGN_SERVER_DOMAIN`;
  - `IRAN_SERVER_URL`;
  - `IRAN_SERVER_DOMAIN`;
  - `FRONTEND_URL`;
  - any sync peer override used by the worker.
- Reject retired identities anywhere in production env input:
  - `kharej.362514.ir`;
  - `iran.362514.ir`;
  - old retired direct IP identities already listed by the deployment-surface
    guard.
- Reject project-root env files as production sources unless an explicit
  emergency flag is supplied. Even in emergency mode, identity validation must
  still pass.
- Print only field names and redacted values in failure output. Do not print
  secrets or full env content.
- Add tests for:
  - valid Iran env passes;
  - valid foreign env passes;
  - Iran env with `kharej.362514.ir` fails;
  - project-root `.env.iran` source fails in production mode;
  - missing required identity field fails;
  - mismatched domain/URL pair fails;
  - secrets are not printed on failure.

Exit criteria:

- Production deploy cannot recreate app/sync-worker containers when identity
  values are stale or retired.
- The guard is called by the real production release path, not only by an
  optional manual script.

## Stage V3 - Market Channel Notice Rollout And Staleness Guard

Goal: prevent historical market notices from being sent as live Telegram
messages after a deploy/restart.

Problem to solve:

The current foreign reconciliation can send a missing receipt for the current
market state even when the transition happened many hours earlier and the
receipt table was empty only because the feature was newly deployed.

Implementation direction:

- Add an explicit receipt status for stale/backfilled notices, for example:
  - `suppressed_stale`;
  - or `backfilled_without_send`.
- Add a configurable staleness window for user-facing market channel notices.
  The default should match the short-disconnect policy, not medium/long outage
  recovery. Recommended default: no more than two minutes unless the user
  approves a larger window.
- During deployment/migration, seed a receipt for the current already-existing
  market runtime state without sending Telegram if the transition is older than
  the staleness window.
- During normal reconciliation:
  - send the notice if the transition is fresh and no sent receipt exists;
  - do not send if the transition is stale and no receipt exists;
  - create a durable suppressed/backfilled receipt so the same stale transition
    is not reconsidered forever;
  - never duplicate a sent notice on replay.
- Keep Telegram send attempts foreign-only.
- Keep Iran free of Telegram calls and Telegram credentials.

Tests:

- Fresh Iran-origin open sync on foreign sends exactly one Telegram notice.
- Fresh Iran-origin close sync on foreign sends exactly one Telegram notice.
- Duplicate sync replay does not send a duplicate notice.
- A stale current-state open after receipt-table rollout is suppressed and
  recorded, not sent.
- A stale current-state close after receipt-table rollout is suppressed and
  recorded, not sent.
- Telegram gateway failure leaves the receipt retryable when the transition is
  still fresh.
- Telegram gateway failure after the staleness window does not loop forever as
  a user-facing late message.

Exit criteria:

- Restarting foreign app/sync-worker cannot send an old open/close notice from
  hours earlier.
- A fresh synced transition still produces the expected Telegram notice.

## Stage V3A - Short-Outage Market Schedule Autonomy

Goal: ensure foreign independently applies Telegram-side market open/close
transitions during a short Iran/foreign outage when both servers already know
the same market schedule.

Problem scenario:

- The configured market close time is 22:00 Iran time.
- Iran/foreign connectivity drops at 21:59.
- Iran closes the WebApp market at 22:00 and writes the authoritative
  `market_runtime_state` close transition.
- Connectivity resumes at 22:04.
- With the current foreign behavior, Telegram can remain open between 22:00
  and 22:04 because foreign waits for the Iran runtime-state sync before
  applying local Telegram-side closure.

Why this is wrong:

`trading_settings` and `market_schedule_overrides` are already synced business
configuration. If foreign has a fresh copy of the schedule before the outage,
it has enough information to locally protect the Telegram surface at the close
time. Waiting for `market_runtime_state` sync is acceptable for final parity,
but not for preventing trades and active buttons after the known close time.

Recommended decision:

Keep Iran authoritative for `market_runtime_state`, but let foreign evaluate
the synced schedule for local Telegram-side transitions during short outages.
Foreign must independently act on both market close and market open when Iran's
runtime-state transition does not arrive within the configured grace period.
Foreign must not create an authoritative `market_runtime_state` transition. It
may create only foreign-local side-effect evidence such as market notice
receipts or local outage transition guard records.

Timeout decision:

- Add a configurable grace setting, recommended default:
  `TRADING_BOT_MARKET_FOREIGN_INDEPENDENT_GRACE_SECONDS=30`.
- The grace timer starts at the scheduled transition timestamp calculated from
  the last synced `trading_settings` and `market_schedule_overrides`.
- If foreign has not received an Iran `market_runtime_state` transition with
  `last_transition_at >= scheduled_transition_at` by
  `scheduled_transition_at + grace_seconds`, foreign must run the independent
  local Telegram-side transition.
- The default is 30 seconds because the current market schedule loop runs every
  15 seconds and normal sync is expected to complete within a few seconds. This
  gives Iran two loop opportunities plus normal sync time while keeping the
  Telegram risk window short.
- This value must be environment-configurable and validated in staging. If
  staging evidence proves the normal loop/sync path is consistently faster, it
  can be reduced later; it must not be increased silently in production.

Close behavior during the grace window:

- At the exact scheduled close time, Telegram trade validation should already
  fail closed based on the synced schedule. This prevents trades after the
  known close time even while foreign waits briefly for Iran's authoritative
  runtime-state sync.
- At `scheduled_close_at + grace_seconds`, if Iran's close transition still has
  not arrived, foreign must independently run the close side effects listed
  below.

Open behavior during the grace window:

- At the exact scheduled open time, foreign should wait for Iran's
  runtime-state sync until the grace period expires.
- At `scheduled_open_at + grace_seconds`, if Iran's open transition still has
  not arrived and the synced schedule snapshot is considered usable, foreign
  must independently run the open side effects listed below.
- If the schedule snapshot is stale or suspect, foreign should remain closed
  for Telegram trading and emit an operator-visible warning instead of opening
  on uncertain data.

Allowed foreign actions at the scheduled transition time during a short outage:

- stop accepting Telegram trade requests because the synced schedule says the
  market is closed;
- expire active offers where `Offer.home_server == "foreign"`;
- remove/update interactive Telegram offer buttons for expired foreign-home
  offers;
- send the Telegram channel notice for close or open if the transition is
  within the approved short-outage freshness window and no matching receipt
  exists;
- reopen Telegram trade actions after scheduled open plus grace if the synced
  schedule snapshot is usable and no newer Iran state contradicts it;
- record idempotent local evidence so reconnection/replay does not duplicate
  expiry or Telegram notices.

Forbidden foreign actions:

- do not write a foreign-authoritative `market_runtime_state` row;
- do not keep accepting Telegram trades after scheduled close merely because
  the last synced `market_runtime_state` still says open;
- do not reopen the market locally if Iran later syncs a newer closed state or
  a newer synced override contradicts the local open;
- do not push a foreign market-runtime transition back to Iran;

Alternative if independent schedule action is not selected:

- Add a short-outage mode detector. If peer connectivity is down and the synced
  schedule says closed, foreign enters `local_market_closed_due_to_outage`.
  This mode blocks Telegram trade actions and expires foreign-home offers
  locally until Iran sync confirms the authoritative close. This alternative
  still relies on the synced schedule; it only makes the outage state explicit.

Recommended implementation shape:

- Split market behavior into two concepts:
  - authoritative product state: `market_runtime_state`, Iran-owned;
  - local protective market surface state: derived from synced schedule and
    used by the server that owns that surface.
- On foreign market-schedule loop:
  - load synced `trading_settings` and `market_schedule_overrides`;
  - evaluate the schedule in `market_timezone`;
  - compute the scheduled transition timestamp and compare it with the newest
    synced Iran `market_runtime_state.last_transition_at`;
  - if the synced Iran runtime transition arrives before the grace expires, use
    the normal sync-reconciliation path;
  - if the grace expires without the Iran transition, run an idempotent local
    transition command for Telegram-side open or close;
  - for close, reject Telegram trade actions immediately at the scheduled close
    time, even before the side-effect command runs after grace;
  - do not call the Iran-authoritative runtime-state writer.
- On reconnect:
  - when Iran's close `market_runtime_state` arrives, reconcile it with the
    local foreign side effects;
  - if the local close already happened, skip duplicate expiry and duplicate
    Telegram notice;
  - when Iran's open `market_runtime_state` arrives, reconcile it with the
    local open-side evidence;
  - if the local transition already happened, skip duplicate Telegram notices,
    duplicate channel edits, and duplicate expiry;
  - if Iran sends a newer override/runtime state that contradicts the local
    transition, apply only the explicitly safe conflict policy documented for
    short outages and produce an operator alert.
- If schedule settings or overrides are stale/not synced, fail closed for
  Telegram trading after the last known close time rather than allowing trades
  during uncertainty.

Required tests:

- `21:59` connectivity loss, `22:00` scheduled close, `22:04` reconnect:
  foreign blocks Telegram trades at 22:00 without waiting for runtime sync.
- The same scenario expires foreign-home active offers after the configured
  grace if Iran's close transition did not arrive.
- The same scenario removes/updates Telegram buttons after the configured
  grace if Iran's close transition did not arrive.
- The same scenario sends the Telegram close notice after the configured grace
  and does not send a duplicate after reconnect.
- `08:59` connectivity loss, `09:00` scheduled open, no Iran runtime sync by
  `09:00 + grace`: foreign sends the Telegram open notice and opens Telegram
  trading if the synced schedule snapshot is usable.
- `08:59` connectivity loss, `09:00` scheduled open, stale/suspect schedule
  snapshot: foreign remains closed for Telegram trading and emits an alert.
- Reconnection at 22:04 applies Iran `market_runtime_state` without duplicating
  expiry, notice receipts, or channel edits.
- If Iran runtime sync arrives before `scheduled_transition_at + grace_seconds`,
  foreign uses the normal sync path and does not run the independent transition
  command.
- If the schedule was changed on Iran immediately before the outage but did not
  sync to foreign, foreign follows only its last confirmed synced schedule and
  records an explicit stale-schedule warning.
- If an override marks the day closed all day and is already synced, foreign
  never opens Telegram trading that day even if runtime sync is delayed.
- If an override changes custom hours and is already synced, foreign closes at
  the custom close time.
- If foreign restarts during the outage, it reconstructs the local protective
  state from durable local evidence and the synced schedule.
- If connectivity is normal, the behavior remains compatible with normal
  Iran-origin `market_runtime_state` sync and does not create duplicate local
  side effects.

Exit criteria:

- A short outage before close cannot leave Telegram trade actions enabled past
  the known synced close time.
- A short outage before open does not leave Telegram closed indefinitely when
  foreign has a usable synced schedule and Iran runtime sync is late.
- Foreign still does not become authoritative for `market_runtime_state`.
- Reconnect is idempotent and does not create duplicate side effects.
- The configured grace period and the schedule freshness policy are documented
  before coding and covered by tests.

## Stage V4 - Parity And Health Evidence After Env Correction

Goal: prove sync parity evidence is fresh after the bad peer URL is removed.

Required checks:

- Run parity snapshot/compare for both sides:
  - Iran as source vs foreign;
  - foreign as source vs Iran.
- Record the compare result in `/api/sync/parity/status` on the relevant side.
- Verify `/api/sync/health` no longer reports
  `parity_status.comparison_status=missing` after the compare has been recorded.
- Verify the compare is fresh and non-critical:
  - `business_drift_count=0`;
  - `critical_drift_count=0`;
  - `incomplete_count=0`;
  - `duplicate_identity_count=0`;
  - `truncated_table_count=0`.
- Verify sync backlog is not polluting the evidence:
  - `unsynced_change_log_count=0`, or all remaining rows are documented and
    intentionally terminal/non-applicable;
  - Redis sync queues are empty or documented.

Exit criteria:

- Both servers have fresh parity evidence.
- Any non-business difference is explicitly documented.
- Health endpoints are not using stale or missing parity status as proof.

## Stage V5 - Production-Like Worker-To-Remote-Receiver Validation Design

Goal: add a safe validation probe that exercises the real worker HTTP delivery
path, remote receiver auth, peer URL, TLS verification, and routing without
mutating business data.

Why a dedicated probe is needed:

- A direct signed POST to `/api/sync/receive` validates receiver auth and some
  receiver logic, but it does not prove the real worker uses the expected peer
  URL, TLS mode, DNS, reverse proxy, and route.
- A synthetic business-table `change_log` row can exercise the worker, but it
  risks production data pollution or unintended side effects.
- The safest path is a validation-only sync item handled by the normal receiver
  route and posted by the real worker delivery code, while the receiver returns
  a validation result without applying any model mutation.

Implementation direction:

- Add a validation-only table name or operation, for example:
  `__sync_transport_probe__`.
- The probe must be accepted only when:
  - request authentication passes;
  - source server is a known peer;
  - the request reaches `/api/sync/receive`;
  - TLS verification is enabled on the worker side;
  - the runtime peer URL matches the canonical identity manifest.
- The probe must not:
  - create business rows;
  - update market state;
  - send Telegram messages;
  - send WebApp notifications;
  - touch offers/trades/users;
  - require a DB migration unless a durable audit table is explicitly approved.
- The worker must send the probe through the same delivery implementation used
  for normal sync items. A special direct HTTP helper is not sufficient.
- The result must include enough non-secret evidence:
  - source role;
  - destination role;
  - peer URL host;
  - TLS verification mode;
  - receiver status;
  - request id/correlation id;
  - timestamp;
  - release commit.

Tests:

- Probe succeeds with a valid signed request.
- Probe fails with invalid signature.
- Probe fails when the runtime peer host does not match the manifest.
- Probe fails or refuses to run when TLS verification is disabled in production
  mode.
- Probe does not change row counts for business tables.
- Probe uses the worker delivery path, not only direct receiver test code.

Exit criteria:

- There is a repeatable command/script for the validation probe.
- The script produces a redacted artifact suitable for release evidence.
- The validation path is safe to run in staging and production with explicit
  operator approval.

## Stage V6 - Staging Production-Like Transport Validation

Goal: run the final validation design on a topology that mirrors production as
closely as possible before any production rollout.

Prerequisites:

- Staging foreign and staging Iran must use distinct hostnames and real HTTPS
  certificates.
- Staging Iran must route to staging foreign through the public/reverse-proxy
  URL, not through Docker-only service names.
- Staging foreign must route to staging Iran through the public/reverse-proxy
  URL.
- TLS verification must be enabled.
- Staging Telegram credentials, if present, must not be used by the transport
  probe.

Validation cases:

- Iran worker -> foreign receiver:
  - canonical peer URL;
  - valid TLS;
  - valid signed probe;
  - expected success.
- Foreign worker -> Iran receiver:
  - canonical peer URL;
  - valid TLS;
  - valid signed probe;
  - expected success.
- Negative case with retired peer host:
  - probe must refuse before delivery.
- Negative case with TLS verification disabled in production-like mode:
  - probe must refuse or fail the release gate.
- Negative case with invalid signature:
  - receiver must reject.

Exit criteria:

- Evidence artifact shows successful bidirectional worker-to-remote-receiver
  delivery through real staging HTTPS routes.
- Negative cases fail closed.
- No business data changed.

## Stage V7 - Production Preflight Transport Validation

Goal: run the same transport validation in production with minimal blast radius.

Prerequisites:

- Stage V1 has corrected Iran runtime env.
- Stage V2 guard is active in the release path.
- Stage V3 stale market notice suppression is active.
- Stage V3A short-outage market schedule autonomy is active.
- Stage V4 parity evidence is fresh.
- Stage V6 staging validation passed.
- User has explicitly approved production validation execution.

Required checks before running:

- Confirm production repo branch and release commit.
- Confirm production runtime identity values on both servers match the manifest.
- Confirm `SYNC_VERIFY_TLS` is enabled or the equivalent runtime TLS mode is
  secure.
- Confirm both peer URLs are reachable with normal certificate verification.
- Confirm no active repair apply is running.
- Confirm sync queues and unsynced backlog are empty or documented.

Validation cases:

- Iran worker -> foreign receiver validation probe succeeds through
  `https://coin.362514.ir`.
- Foreign worker -> Iran receiver validation probe succeeds through
  `https://coin.gold-trade.ir`.
- Probe artifacts confirm:
  - source role;
  - destination role;
  - peer host;
  - TLS verification enabled;
  - receiver accepted the validation probe;
  - no business table mutations;
  - no Telegram/WebApp side effects.

Exit criteria:

- Production-like worker-to-remote-receiver validation passes in both
  directions.
- Artifacts are saved with secrets redacted.
- No user-facing side effect occurs.

## Stage V8 - Release Gate And Runbook Updates

Goal: make the new checks mandatory for future releases.

Deliverables:

- Update production release docs to require:
  - env identity manifest validation;
  - retired identity rejection;
  - TLS verification evidence;
  - worker-to-remote-receiver validation artifact;
  - parity status evidence;
  - market notice receipt/backfill status evidence.
- Update operator runbook for:
  - correcting runtime env from canonical secure env;
  - recreating only stateless containers;
  - reading sync backlog safely;
  - interpreting stale/suppressed market notice receipts;
  - collecting transport validation artifacts.
- Add an explicit warning that key rotation validation must check identity
  fields, not only key hashes.

Exit criteria:

- Future production release cannot pass on key-hash validation alone.
- The release checklist has a clear evidence slot for every risk discovered in
  this incident.

## Completion Criteria

This roadmap is complete only when all of the following are true:

- Iran production no longer uses any retired peer or frontend identity.
- Production deploy tooling rejects retired/mismatched env identity before
  container recreation.
- Market notice reconciliation cannot send stale historical open/close notices.
- Fresh market transitions still send exactly one Telegram notice on foreign.
- Telegram cannot remain open past the already synced close schedule during a
  short Iran/foreign outage.
- Parity status is fresh and clean/non-critical after the env correction.
- A staging production-like worker-to-remote-receiver validation has passed in
  both directions.
- The final production preflight transport validation plan is executable and
  gated by explicit approval.
- Documentation/runbooks require these checks for future production releases.
