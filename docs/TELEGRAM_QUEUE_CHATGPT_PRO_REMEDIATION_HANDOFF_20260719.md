# Telegram Queue Remediation Handoff for ChatGPT Pro

## Review scope

This document describes the remediation successor to the independently reviewed candidate commit `78df2e14230f80ee968e3c20a94fce8a96769eaa`. The earlier review compared that commit with `main@2c08da14bfa0ef94d9c788e478d30ddc3f31a3c5` and reported findings `TQ-F001` through `TQ-F012`.

Review the exact commit supplied in the external review prompt and evidence manifest, not a moving branch. The commit containing this document is still deliberately runtime-disabled. It has not been merged or deployed, and no live Telegram or staging workload is claimed by this handoff.

## Safety boundaries that remain unchanged

- `TELEGRAM_DELIVERY_QUEUE_IMPLEMENTATION_READY` remains `False`.
- There was no merge to `main`, staging deployment, production deployment, live provider request, production mutation, or staging mutation during remediation.
- The primary bot remains the only publisher/private/callback role.
- The optional channel-editor bot remains edit-only, credential-bound, and has no fallback to the primary credential.
- Bot and destination pacing remain Redis-authoritative. Adding an editor does not assume that one channel gains twice the provider capacity.
- Live Stage 4 execution still requires an explicit owner authorization bound to exact Git SHA, run ID, trace hash, config binding, driver commands, and expiry time.

## Architecture after remediation

Business transactions and source-specific outboxes/feeders create immutable queue intents. `telegram_delivery_jobs` remains the common M0–M7 execution state machine. PostgreSQL owns durable scheduling, eligibility, promotion, lease, dispatch-fence, retry, reconciliation, and recovery time. Redis owns atomic bot cadence, shared destination cadence, cooldown, and probe time.

The primary role now runs a bounded configurable slot pool. Its default four slots consist of three general slots and one M0-reserved slot. The reserved claim is constrained in SQL by effective priority, including the five-second trade-result promotion. The editor role has an independent bounded pool, defaulting to one slot. Redis remains the admission authority after a job is claimed, so concurrency does not bypass bot or shared-channel pacing.

After Telegram returns a definitive response, the worker first writes one immutable provider-outcome fact fenced by `(job_id, lease_token)`, then atomically applies queue/domain feedback. Initial provider-fact persistence now retries transient PostgreSQL/storage failures without a small attempt cap while the process remains alive. A same-role in-memory barrier prevents new claims and all local lease recovery until that fact commits. Cancellation is deferred until the fact is durable. Replay then finishes feedback without a second provider call.

The Stage 4 source contract now includes a deterministic ten-minute workload generator, a read-only redacted production-shape sampler, production-deny fingerprints, exact plan hashes, a technical fault catalog, independent expected ledgers, cleanup planning, stop thresholds, sender/observer orchestration, and a strict live-evidence verifier. Actual sender/observer credentials and live receipts remain conditional Stage 4 infrastructure and are intentionally absent from Git.

## Finding dispositions and changes

### TQ-F001 — fractional or oversized `retry_after`

Disposition: **P1 claim rejected; defensive hardening accepted.**

Telegram's official Bot API defines `ResponseParameters.retry_after` as `Integer`. The same official type section says signed 32-bit storage is safe for Bot API Integer fields unless a field is explicitly documented otherwise; `retry_after` has no exception. Therefore fractional values and integers above signed 32-bit are malformed provider data, not valid facts that require a decimal or arbitrary-precision schema.

The earlier code was still unsafe because immediate classification accepted float-coercible values while persistence used `int()`, allowing truncation and disagreement. Both paths now use the same strict parser:

- accept only a non-boolean Python integer in `1..2_147_483_647`;
- reject strings, floats, fractions, booleans, zero, negatives, and oversized integers;
- route malformed `429` data to the bounded retry fallback;
- never make a `429` terminal merely because `retry_after` is malformed.

Review the contract/service changes and malformed-value matrix. Do not require decimal persistence unless newer official Telegram documentation contradicts the cited Integer contract.

### TQ-F002 — host-clock-dependent durable scheduling

Disposition: **accepted and remediated locally.**

`telegram_delivery_database_now()` samples PostgreSQL `clock_timestamp()` and validates an aware timestamp. Production transitions call it when no explicit deterministic test time is supplied. This now covers claim eligibility, trade promotion, stale-edit ordering, lease creation, dispatch fence, provider-fact application, retry/cooldown transitions, durable preflight/runtime gates and resume, reconciliation, replay, and expired-lease recovery. The lease start is re-sampled at the locked-row linearization boundary, and dispatch time is re-sampled after scope lock and lifecycle guard waits. The Redis preflight cooldown receives the absolute deadline persisted from database time rather than rebuilding it from host time.

Redis continues to use Redis `TIME`; host time is not an admission authority. Real PostgreSQL tests cover a future job and unexpired lease under a simulated far-future host clock, plus lock-wait linearization.

### TQ-F003 — definitive provider response lost during initial outcome-insert outage

Disposition: **accepted with a deliberately narrowed guarantee.**

The fixed three-attempt loop was removed. Transient `SQLAlchemyError`, `OSError`, and `TimeoutError` now retry indefinitely with capped exponential delay while the process remains alive. The outcome ID is accepted only after commit succeeds; unique fencing makes an uncertain commit retry idempotent. Cancellation after the provider response is remembered and deferred until commit. A same-role persistence barrier blocks later local claims, and local lease recovery pauses while any provider fact is retained in memory. Validation/fence conflict fails the supervisor with the barrier still installed rather than being downgraded to an ordinary cycle error.

Real PostgreSQL coverage injects four consecutive initial insert failures for both a successful `sendMessage` (including its real message ID) and a `429`, then proves exactly one applied provider fact and the correct final queue state. Unit coverage also injects commit failure, cancellation, recovery, and same-role claim pressure.

The following broader claim was **not accepted**: guaranteed survival of an abrupt process/host loss before any durable outcome write. Telegram Bot API provides no idempotency token that can atomically bridge a local write with Telegram's external side effect. An encrypted local spool would introduce its own multi-host ownership, durability, key-management, replay, and atomicity contract and still cannot prove Telegram-side exactly-once semantics. Such a crash remains honestly `AMBIGUOUS`. Evaluate whether the narrowed process-alive guarantee matches the declared single execution-owner topology; do not report full crash survival as implemented.

### TQ-F004 — editor `403` blocks primary publication

Disposition: **accepted and remediated locally.**

Hard destination permission/capability state is now keyed and queried as `(bot_identity, destination)`. An editor `403` gates editor edits for that channel but does not prevent primary publication to the same channel. Shared destination cadence and evidence-backed `429` cooldown remain cross-bot. Full destination resume clears both role-specific hard keys only through the existing preflight/resume path.

Real PostgreSQL and Redis tests prove editor isolation, primary same-channel progress, retained editor work, shared `429` cadence, and no credential fallback.

### TQ-F005 — one slow primary request blocks later M0 work

Disposition: **accepted and remediated locally.**

The role loop now supervises bounded slots, each executing one job per cycle. Primary defaults to three general slots and one M0-reserved slot; editor defaults to one independent slot. The M0 slot uses a database effective-priority ceiling, so it cannot consume lower-class work while callbacks or promoted trade results need reserved capacity. General slots remain work-conserving and may also process M0. The configured worker interval is an idle-poll delay; processed slots make only a short cancellable yield and Redis enforces actual cadence.

Real PostgreSQL coverage holds a lower-priority gateway call open, enqueues a later M0 callback, and proves the reserved slot completes M0 before the slow call is released. Other tests cover the effective-priority SQL filter, supervisor slot plan, and editor progress with 300 primary plus 100 editor jobs. Live SLO and 300/100 resource isolation remain Stage 4 gates.

### TQ-F006 — missing executable Stage 4 harness

Disposition: **accepted; source contract closed, live drivers conditional.**

New source surfaces:

- `core/telegram_queue_stage4_workload.py`
- `core/telegram_queue_stage4_harness.py`
- `scripts/run_telegram_queue_stage4_harness.py`
- `scripts/sample_telegram_stage4_offer_shapes.py`
- `docs/TELEGRAM_QUEUE_STAGE4_HARNESS_RUNBOOK_20260719.md`

The generator enforces exactly 600 seconds, 1,800 valid submissions, 400 invalid attempts, 80 synthetic owners, maximum ten conservative active offers per owner, exact lot quotas, 540 traded offers, 162 concurrent trade targets with exact 2/3/4/5-request quotas, 180 manual expiry requests, 18 trade/expiry races, 125 peak admin deliveries, all active commodities, all buy/sell and cash/tomorrow combinations, and repeated 8–12/s peaks. A remediation self-review found and fixed a late-timeline causal bug: lifecycle targets are now selected only when enough time remains, and the validator independently rejects confirmations, trades, or manual expiry at/before their submission. Twenty-five additional seeds exercise this property.

Plan/live configuration shares one binding hash while allowing the single required transition `provider_network_enabled=false` during planning to `true` only after authorization. All other fields remain exact. This fixes an otherwise impossible execution contract where a valid plan and valid live config could never have the same literal hash.

The live verifier no longer trusts file presence or a self-declared `pass`. It checks exact business outcome identity and values, invalid-offer zero Offer/publication-intent deltas, response-catalog coverage, unique Telegram delivery identities, editor route/method restrictions, provider-side-effect bounds, exact receiver receipts, one queue metric for every second `0..600`, final zero-backlog drain, every technical fault result, complete cleanup, explicit zero reconciliation counters, numeric SLO thresholds, and clean security-scan scope.

No live sender/observer claim is made. Driver binaries, staging/Test-DC credentials, permission readback, live receipts, calibration, cleanup, and endurance remain authorization-bound Stage 4 work.

### TQ-F007 — contradictory roadmap status

Disposition: **logical contradiction rejected; presentation remediated.**

The prior roadmap already had a single authoritative stage table and explicit supersession language, so historical checkpoint prose was not a second authoritative current status. To eliminate reasonable misreading, every earlier closure statement is now labeled historical/superseded, the current remediation status is stated near the beginning, and a consistency test requires exactly one authoritative table, one current status marker, seven stage rows, and one disposition row for every `TQ-F001..TQ-F012`.

The current status is `Stage 3 = REMEDIATED-LOCAL / RE-REVIEW-REQUIRED / RUNTIME-OFF`; Stage 4 is blocked by re-review and explicit authorization.

### TQ-F008 — count-only evidence and no exact-SHA CI

Disposition: **accepted locally; CI availability remains conditional.**

The local evidence runner now records schema version, exact test ID, outcome and duration, discovered count, inventory hash, inventory completeness, redacted invocation, exact Git commit/branch/dirty state when Git is available, Python/platform/dependency versions, warning categories, resource-warning enforcement, unraisable exceptions, event-loop failures, file-descriptor snapshots/growth, zero skips, isolated scratch fingerprints, and forced-empty provider credentials. It simultaneously produces a raw verbose log and structured JSON.

The final evidence bundle must be generated from the clean remediation SHA. A Git-enabled disposable test image is used so Git-dependent tests and runner metadata are included. If this repository has no applicable GitHub workflow/check, the handoff must state `not_available`; it must not manufacture a green CI claim.

### TQ-F009 — scanner scope narrower than implied security claim

Disposition: **accepted as a scope limitation and split into local/live gates.**

The scanner now supports two explicit surfaces:

1. exact committed Git `HEAD` via `git ls-tree`/`git cat-file`, recording commit, tree and blob-manifest hash and applying high-confidence token/private-key/bearer/JWT patterns;
2. evidence/log/archive inputs, recursively scanning bounded ZIP/TAR/GZIP members with the stricter secret, credential URL, email, mobile and sensitive-assignment rules.

It fails closed for missing/unreadable/oversized/deep inputs and never echoes matched values. This does not claim to scan staging database rows, external centralized logs, container logs, uncommitted blobs, or every possible secret grammar. Those remain aggregate read-only Stage 4/production gates, ideally with an independent scanner.

### TQ-F010 — unreproducible main patch equivalence

Disposition: **accepted and remediated locally.**

`scripts/report_telegram_queue_git_reconciliation.py` resolves exact refs and emits merge base, divergence, every main-side commit, subject, affected paths, stable patch ID, all candidate counterpart SHAs/subjects, raw `git cherry`, and an all-mapped decision. It fails closed when any main patch lacks a counterpart. A synthetic test proves mapping across differently worded commits with identical patches.

The exact report must be regenerated after the remediation commit and any `origin/main` movement. It is evidence, not permission to merge.

### TQ-F011 — no live Stage 4 evidence

Disposition: **not a source defect; remains an explicit live gate.**

The earlier review correctly noted that absence was expected because live execution was not authorized. No local test or dry-run is presented as a substitute. The exact-SHA preflight, primary send/editor edit readback, receiver observation, provider response ledger, fault run, cleanup, repeated calibration, final workload, endurance, rollback rehearsal, and approvals remain open.

### TQ-F012 — modeled channel demand exceeds documented pacing

Disposition: **accepted as a live capacity risk; no source-side number invented.**

The product semantics remain one channel, one independent offer message, two-minute offer life, no batching, no second publisher, no silent SLO weakening, and no assumption that editor credentials multiply one chat's capacity. The harness and ADR require repeated receiver-backed interval calibration for primary-only and primary+editor modes. Failure to find a safe interval is `NO-GO` and requires an explicit product architecture decision.

## Tests and evidence the reviewer must inspect

The external bundle supplied with the review prompt should include:

- full verbose Telegram-prefixed test log and structured per-test JSON;
- full verbose Bot-prefixed test log and structured per-test JSON;
- real PostgreSQL/Redis integration log;
- Stage 4 no-provider plan, manifest, quota report, fault catalog, expected ledger, cleanup plan, thresholds, and verification output;
- exact Git reconciliation report;
- exact committed-source plus evidence-archive security scan;
- test/audit summaries, Git metadata, manifest, and `SHA256SUMS`;
- the original independent review and this remediation handoff.

Reconcile every reported test total with its exact ID inventory. Treat skipped tests, warning/resource leaks, dirty Git metadata, mismatched SHA, failed security scan, or missing checksums as evidence failure.

## Required independent-review output

Produce one English Markdown report suitable for another AI agent. Begin with an explicit verdict for:

1. source-local closure;
2. readiness to begin authorization-bound Stage 4 preflight;
3. readiness for the final 1,800-valid + 400-invalid run;
4. production readiness.

For every original finding, state `closed`, `partially closed`, `rejected with rationale accepted`, `rejected with rationale not accepted`, or `still open`. Verify code paths and tests rather than accepting this handoff's labels. List any new finding with severity, confidence, exact file/line evidence, realistic failure scenario, impact, minimal remediation, and exact closure test. Clearly separate source defects from missing live evidence and product capacity decisions.
