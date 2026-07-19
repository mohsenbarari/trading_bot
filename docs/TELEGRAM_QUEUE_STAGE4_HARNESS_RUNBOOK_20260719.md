# Telegram Queue Stage 4 Harness Runbook

## Scope and authorization

This runbook prepares the final `1,800 valid + 400 invalid` staging workload. It does not authorize staging execution, merge, production access, or deployment. `TELEGRAM_DELIVERY_QUEUE_IMPLEMENTATION_READY` remains `False` until the separate activation decision.

The harness has four commands:

1. `plan`: provider-free deterministic trace and ledger generation.
2. `verify-plan`: checksum and completeness verification without processes or network.
3. `execute`: staging only, requiring a clean pinned Git SHA, the literal `--authorize-live-staging` flag, a one-use Ed25519-signed authorization document bound to the run/trace/config/driver commands and driver binary digests, an independently provisioned `--trusted-authorization-public-key` trust-anchor file outside the run directory, a maximum one-hour validity window, and distinct staging/production fingerprints.
4. `verify-live`: fail-closed reconciliation of sender, receiver, cleanup, business and queue evidence.

## Production-deny configuration

The redacted config must contain:

- `schema_version=2`;
- `environment=synthetic-test` for provider-free planning or `staging` for live execution;
- `run_id` beginning with `stage4-`;
- `bot_mode=primary-only|primary-and-channel-editor`;
- SHA-256 fingerprints for database, Redis, primary bot, editor bot, channel, observer database and receiver session in both staging and production maps;
- inequality for every staging/production fingerprint pair;
- staging/default/production active-offer limits exactly `10/4/4` and expiry exactly `120` seconds;
- `provider_network_enabled=false` for planning and `true` only in the separately authorized execution config;
- absolute, non-symlinked, non-group/world-writable sender and observer executables for execution.
- a base64 Ed25519 public authorization key, whose bytes must equal the independently provisioned external trust anchor, and distinct sender/observer network-policy fingerprints.

Raw token, secret, password, database/Redis URL, bot/channel/chat/user ID fields are rejected before process creation. Fingerprints are `sha256:<64 lowercase hex>` and cannot be reversed to credentials. Every staging child input is independently rebound before launch using `sha256("telegram-stage4-bound-value-v1:" + exact_environment_value)`; a mislabeled production-like endpoint or credential therefore cannot pass merely because the config contains an unrelated staging fingerprint.

## Read-only production shape sampler

`scripts/sample_telegram_stage4_offer_shapes.py` is the only production-data helper in this workflow. It requires:

- `--environment production` and `--ack-production-read-only` together;
- the expected credential-free database fingerprint;
- a connection URL supplied only through the named environment variable;
- a new output path.

It begins `REPEATABLE READ READ ONLY`, sets a 15-second statement timeout, verifies `transaction_read_only=on`, reads deterministic recent offer shapes, rolls back, and exports only commodity, buy/sell, settlement, quantity, price and 2/3-lot numeric templates. It never selects or exports offer/user/Telegram/message/public/idempotency IDs, human names or notes. This sampler is not run by the staging harness and staging never connects to production.

## Deterministic workload contract

For every seed the generator enforces:

- exactly 600 seconds of input, 1,800 accepted-valid and 400 invalid attempts;
- 80 synthetic owners (minimum gate 70) and conservative maximum ten active offers per owner in any two-minute window;
- valid lot shapes `900 none + 450 two-lot + 450 three-lot`;
- 540 traded offers, including 162 concurrent offers with exact 2/3/4/5 requester quotas;
- 180 manual expiry requests, 18 synchronized trade/expiry races, 125 admin deliveries and both market open/close channel notices during peaks;
- all active commodities and every buy/sell × cash/tomorrow combination;
- repeated 3–8-second peaks with 8–12 valid submissions per second;
- unique event IDs and an immutable canonical trace SHA-256.

Every race has one expiry and at least two competing trade requests on one shared barrier. Planned release skew is at most 50 ms (the generated plan uses 25 ms), the live sender must prove observed skew at most 100 ms, and the independent business ledger must prove exactly one authoritative winner. Nine groups are trade-winner and nine are expiry-winner for every seed.

The technical fault catalog is separate from natural market traffic. It includes success-envelope defects, 400/401/403/404/409, 5xx, pre-write transport failure, unknown-write failure, response-received/close, migration and provider-fact database outage. The 429 matrix separately covers Integer `1`, signed-32 maximum, missing, bool, string, fraction, zero, negative, max+1 and huge integer, each with a sanitized shape hash and durable deadline-source contract. Fault injection is prohibited during capacity calibration.

## Generated plan artifacts

`plan` creates:

- `manifest.json`;
- `event_trace.jsonl`;
- `expected_business_ledger.jsonl`;
- `delivery_obligations.jsonl`;
- `fault_catalog.json`;
- `cleanup_plan.jsonl`;
- `quota_report.json`;
- `stop_thresholds.json`.

The manifest binds Git SHA, seed, run ID, environment, mode, fixture/config/trace hashes and every file hash/size. An interrupted plan retains `.incomplete` and is rejected.

## Live driver protocol

The observer is started first with `STAGE4_RUN_DIR`, `STAGE4_MANIFEST` and `STAGE4_EVENT_TRACE`. It must create `observer.ready.json` containing the exact run ID and trace hash before the sender may start. The sender and observer are separate executables; both content digests and commands are signature-bound. Shell execution is not used.

The runner constructs both child environments from scratch. It never forwards generic `DATABASE_URL`, `REDIS_URL`, `BOT_TOKEN` or the rest of the parent environment. Sender receives only the fixed `STAGE4_STAGING_*` database/Redis/bot/channel namespace; observer receives only its distinct read-only database, receiver session and the same fingerprint-bound staging channel ID, and never receives bot credentials. The signed authorization has an exact field set; additional policy-like fields are rejected even when signed. The verification key is loaded from an absolute, non-symlink, non-group/world-writable file outside the mutable run directory; the key copied into config is signature-bound but is never accepted as its own trust root. A per-profile network-policy fingerprint is passed and must match the infrastructure-applied staging egress policy. Source validation does not pretend to install host firewall policy; absent external policy attestation is a live preflight failure.

The drivers must produce:

- `preflight.json` binding the exact run/trace/mode, redacted routing-policy hash, runtime limits, production-collision count, role capability readback and a zero provider-call observer;
- `business_outcomes.jsonl` with exactly one row per expected input event;
- `telegram_results.jsonl` with exactly one row per code-owned delivery obligation, including unique delivery/obligation identity, source event/catalog, method, bot role, destination class, terminal outcome, enqueue/provider timestamps, provider observation hash and derived provider-side-effect count;
- `receiver_receipts.jsonl` from the independent receiver side, in exact one-to-one agreement with every result marked receiver-required;
- `queue_metrics.jsonl` with at least one sample for every elapsed second `0..600` plus the complete drain/cooldown interval and a zero-backlog final sample;
- `fault_results.jsonl` with exactly one observed row for every mandatory fault-catalog case, matching shape hash, disposition, durable state, provider-call count, retry source/deadline and zero duplicate provider side effects; a driver-authored `status=pass` has no authority;
- `cleanup_ledger.jsonl` covering every cleanup-plan identity as `completed` or `not_applicable`;
- `reconciliation.json` with `status=clean` and explicit zero counts for unresolved jobs, duplicate side effects, missing business/receiver rows, invalid-offer publication intents, route mutation and backlog-caused disablement;
- `acceptance.json` with exact `1800/400` input counts, no stop events, all criteria true and independently rechecked SLO metrics before `decision=pass` is accepted;
- `security_scan.json` proving a clean scan over the manifest, every plan artifact and every other live artifact.

No API `ok=true` is accepted as receiver evidence by itself. Test-DC user-client evidence covers response text, markup, callback and private delivery; normal Telegram staging covers capacity. Driver provisioning and credentials are Stage 4 infrastructure and are not embedded in Git.

The verifier does not trust a self-declared `pass`: it regenerates the delivery-obligation ledger from the immutable trace, requires exact one-to-one provider results, derives receiver requirements and side-effect counts, validates race release/outcome evidence, and recomputes publication/callback latency percentiles, deadline ratio, reconciliation totals and acceptance decision from raw timestamps and ledgers. It also verifies editor route restrictions, the 601-second metric floor, final drain state, every explicit fault shape, observer-backed cleanup and the real scanner schema. The scanner manifest is independently recomputed from every declared artifact byte; a well-shaped fabricated hash is rejected. Missing, duplicate, malformed, copied or merely empty evidence fails closed. The former fabricated pattern of one primary/private `sendMessage` per business event is an explicit negative test.

## Stop order

Any fingerprint collision, unexpected destination/role, secret/identity artifact, duplicate provider side effect, delivery obligation for an invalid offer, or persistent unbounded 429 stops the run. A failed capacity interval is cooled down and recorded; it is not retried at 100 ms and does not weaken expiry/SLO semantics. If no safe interval meets the fixed demand, the result is `NO-GO`.

Artifacts are exported, scanned and checksum-bound before cleanup. Cleanup occurs outside the measurement window through approved run-scoped paths; provider deletes use M7. Production remains untouched throughout.
