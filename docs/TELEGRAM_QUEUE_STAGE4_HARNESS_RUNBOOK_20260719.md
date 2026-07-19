# Telegram Queue Stage 4 Harness Runbook

## Scope and authorization

This runbook prepares the final `1,800 valid + 400 invalid` staging workload. It does not authorize staging execution, merge, production access, or deployment. `TELEGRAM_DELIVERY_QUEUE_IMPLEMENTATION_READY` remains `False` until the separate activation decision.

The harness has four commands:

1. `plan`: provider-free deterministic trace and ledger generation.
2. `verify-plan`: checksum and completeness verification without processes or network.
3. `execute`: staging only, requiring a clean pinned Git SHA, the literal `--authorize-live-staging` flag, a non-expired authorization document bound to the run/trace/config/driver commands, and distinct staging/production fingerprints.
4. `verify-live`: fail-closed reconciliation of sender, receiver, cleanup, business and queue evidence.

## Production-deny configuration

The redacted config must contain:

- `schema_version=1`;
- `environment=synthetic-test` for provider-free planning or `staging` for live execution;
- `run_id` beginning with `stage4-`;
- `bot_mode=primary-only|primary-and-channel-editor`;
- SHA-256 fingerprints for database, Redis, primary bot, editor bot and channel in both staging and production maps;
- inequality for every staging/production fingerprint pair;
- staging/default/production active-offer limits exactly `10/4/4` and expiry exactly `120` seconds;
- `provider_network_enabled=false` for planning and `true` only in the separately authorized execution config;
- absolute, non-symlinked sender and observer executables for execution.

Raw token, secret, password, database/Redis URL, bot/channel/chat/user ID fields are rejected before process creation. Fingerprints are `sha256:<64 lowercase hex>` and cannot be reversed to credentials.

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
- 180 manual expiry requests, 18 explicit trade/expiry races and 125 admin deliveries during peaks;
- all active commodities and every buy/sell × cash/tomorrow combination;
- repeated 3–8-second peaks with 8–12 valid submissions per second;
- unique event IDs and an immutable canonical trace SHA-256.

The technical fault catalog is separate from natural market traffic. It includes success-envelope defects, 400/401/403/404/409, valid and malformed 429, 5xx, pre-write transport failure, unknown-write failure, response-received/close, migration and provider-fact database outage. Fault injection is prohibited during capacity calibration.

## Generated plan artifacts

`plan` creates:

- `manifest.json`;
- `event_trace.jsonl`;
- `expected_business_ledger.jsonl`;
- `fault_catalog.json`;
- `cleanup_plan.jsonl`;
- `quota_report.json`;
- `stop_thresholds.json`.

The manifest binds Git SHA, seed, run ID, environment, mode, fixture/config/trace hashes and every file hash/size. An interrupted plan retains `.incomplete` and is rejected.

## Live driver protocol

The observer is started first with `STAGE4_RUN_DIR`, `STAGE4_MANIFEST` and `STAGE4_EVENT_TRACE`. It must create `observer.ready.json` containing the exact run ID and trace hash before the sender may start. The sender and observer are separate executables and are hash-bound in the authorization. Shell execution is not used.

The drivers must produce:

- `preflight.json` binding the exact run/trace/mode, redacted routing-policy hash, runtime limits, production-collision count, role capability readback and a zero provider-call observer;
- `business_outcomes.jsonl` with exactly one row per expected input event;
- `telegram_results.jsonl` with a unique delivery ID, source event/catalog, method, bot role, destination class, terminal outcome, latency, receiver requirement and provider-side-effect count for every delivery;
- `receiver_receipts.jsonl` from the independent receiver side, in exact one-to-one agreement with every result marked receiver-required;
- `queue_metrics.jsonl` with at least one sample for every elapsed second `0..600` plus the complete drain/cooldown interval and a zero-backlog final sample;
- `fault_results.jsonl` with exactly one passing row for every mandatory fault-catalog case and zero duplicate provider side effects;
- `cleanup_ledger.jsonl` covering every cleanup-plan identity as `completed` or `not_applicable`;
- `reconciliation.json` with `status=clean` and explicit zero counts for unresolved jobs, duplicate side effects, missing business/receiver rows, invalid-offer publication intents, route mutation and backlog-caused disablement;
- `acceptance.json` with exact `1800/400` input counts, no stop events, all criteria true and independently rechecked SLO metrics before `decision=pass` is accepted;
- `security_scan.json` proving a clean scan over the manifest, every plan artifact and every other live artifact.

No API `ok=true` is accepted as receiver evidence by itself. Test-DC user-client evidence covers response text, markup, callback and private delivery; normal Telegram staging covers capacity. Driver provisioning and credentials are Stage 4 infrastructure and are not embedded in Git.

The verifier does not trust a self-declared `pass`: it recomputes ledger identity/value equality, response-catalog coverage, editor route restrictions, receipt identity, the 601-second metric floor, final drain state, fault dispositions, cleanup coverage, zero reconciliation counters and every numeric SLO threshold. Missing, duplicate, malformed or merely empty evidence fails closed.

## Stop order

Any fingerprint collision, unexpected destination/role, secret/identity artifact, duplicate provider side effect, delivery obligation for an invalid offer, or persistent unbounded 429 stops the run. A failed capacity interval is cooled down and recorded; it is not retried at 100 ms and does not weaken expiry/SLO semantics. If no safe interval meets the fixed demand, the result is `NO-GO`.

Artifacts are exported, scanned and checksum-bound before cleanup. Cleanup occurs outside the measurement window through approved run-scoped paths; provider deletes use M7. Production remains untouched throughout.
