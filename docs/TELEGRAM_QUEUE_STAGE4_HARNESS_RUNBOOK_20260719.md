# Telegram Queue Stage 4 Harness Runbook

## Safety boundary

This runbook prepares and verifies the final `1,800 valid + 400 invalid` staging workload. It does not authorize execution, merge, production access, or deployment. `TELEGRAM_DELIVERY_QUEUE_IMPLEMENTATION_READY` remains `False`; a separate owner approval is required before `execute`.

The four commands are:

1. `plan`: generate a deterministic, provider-free plan.
2. `verify-plan`: regenerate the code-owned oracle and verify every plan byte.
3. `execute`: run only on staging after fixed-policy authorization.
4. `verify-live`: verify an already completed evidence directory without contacting Telegram.

The CLI intentionally has no option for a trust-policy path or a caller-selected authorization key. Live commands always use `/etc/trading-bot/stage4/stage4-trust-policy.json`.

## Deployment-owned trust policy

Provision the trust policy outside Git and outside every run directory. It must be a root-owned regular file whose parent is root-owned and not writable by group or other. Its exact schema is:

```json
{
  "schema_version": 1,
  "authorization_public_key": "BASE64_ED25519_PUBLIC_KEY",
  "sender_evidence_public_key": "BASE64_ED25519_PUBLIC_KEY",
  "observer_evidence_public_key": "BASE64_ED25519_PUBLIC_KEY",
  "sender_uid": 61001,
  "sender_gid": 61001,
  "observer_uid": 61002,
  "observer_gid": 61002,
  "sender_signing_key_path": "/etc/trading-bot/stage4/keys/sender.key",
  "observer_signing_key_path": "/etc/trading-bot/stage4/keys/observer.key",
  "authorization_registry_path": "/var/lib/trading-bot/stage4/used-authorizations.sqlite3",
  "evidence_workspace_root": "/var/lib/trading-bot/stage4/evidence-workspaces",
  "driver_root": "/opt/trading-bot/stage4-drivers",
  "host_identity_sha256": "sha256:REDACTED_64_LOWERCASE_HEX"
}
```

The three Ed25519 key pairs must be distinct. Sender and observer UIDs and GIDs must also be distinct and non-root. Each evidence private key is exactly 32 raw Ed25519 bytes, owned by its role and mode `0600`; its parent remains root-owned and non-writable. The runner derives its public key before any process or network action and rejects a mismatch. `host_identity_sha256` is the domain-separated fingerprint of the fixed host `/etc/machine-id`, preventing a copied trust policy and authorization registry from silently becoming valid on another host.

The registry parent, evidence workspace and driver root are root-owned, absolute, non-symlink directories and are not writable by group or other. Authorization consumption is an atomic primary-key insert in the fixed registry. Copying a plan/output directory cannot replay an authorization.

Each driver command contains exactly one absolute executable path. The executable is a regular non-symlink file under `driver_root`, root-owned, executable and has no owner/group/other write bit. Interpreter-plus-mutable-script commands and caller-provided arguments are rejected. The authorization binds both command and executable digests; the runner re-hashes binaries immediately before launch and the sender again before its first provider-capable process starts.

## Redacted run configuration

The schema version is `3`. Required controls are:

- environment `synthetic-test` for planning or `staging` for live execution;
- a unique `stage4-*` run ID and a fresh random 64-hex `plan_nonce`;
- `bot_mode=primary-only|primary-and-channel-editor`;
- SHA-256 fingerprints for staging and production database, Redis, both bots, channel, observer database and receiver session, with no staging/production collision;
- distinct sender/observer network-policy fingerprints;
- the exact trust-policy fingerprint;
- active-offer limits `10/4/4` for staging/default/production and expiry `120` seconds;
- provider network `false` while planning and `true` only for an authorized execution;
- one immutable driver executable per role.

Raw credential, identity, URL, token, password, chat/channel/user/message ID and secret fields are rejected. Live child inputs are rebound to the signed staging fingerprints before launch. The sender receives only the staging database, Redis, bot and channel credential namespace. The observer receives only the independent read-only database, receiver session and channel; it receives no bot credential. Neither child inherits generic credentials, `PYTHONPATH`, cloud variables or the parent `PATH`.

## Workload and reference state machine

Input traffic lasts exactly 600 seconds, followed by a bounded 120-second expiry/drain interval. Every seed contains exactly:

- 1,800 valid offers and 400 invalid attempts from 80 synthetic users;
- lot shapes `900 none + 450 two + 450 three`;
- 540 traded offers: 270 normal, 162 concurrent and 108 partial-then-complete;
- partial trades only on two- or three-lot offers;
- 180 manual expiries, of which only 18 explicitly share a trade/expiry barrier;
- explicit automatic-expiry events for every otherwise non-terminal offer;
- 125 admin deliveries and two notification-only market status notices;
- all sanitized active commodity keys and all buy/sell × cash/tomorrow combinations;
- natural peaks of 8–12 valid submissions per second while the ten-minute mean remains 3/s.

Ordinary manual expiry targets and trade targets are disjoint. The only overlap is the 18 declared races. Every 2–5-way trade group and every expiry/trade group has exactly one independently observed authoritative winner. The observer returns the complete raw state snapshot—status, remaining lots, monotonic state version and terminal event—and the verifier recomputes its digest. Terminal, partial and party-message obligations activate from committed outcomes, never from input labels or confirmation time. A partial edit reaching Telegram after completion is rejected unless it was recorded as superseded.

Market open/close entries in this workload are channel notices, not authoritative market mutations. Actual market-state transition behavior belongs to its separate live preflight scenario.

## Process and filesystem isolation

The root runner creates separate `0700` temporary output directories owned by the sender and observer UIDs. Each child receives only its own output path and common read-only root-owned plan files. Neither role can traverse or write the other role's directory. Processes launch with an empty supplemental-group list.

The observer starts first and writes mode-`0600`, observer-owned `observer.ready.json`. The ready envelope is Ed25519-signed and binds run ID, nonce, trace, trust policy, observer network policy, immutable executable digest, actual observer PID and a fresh timestamp after process start. Unsigned, stale, wrong-owner, permissive, symlinked or pre-created readiness never starts the sender.

After both processes exit, the runner requires the exact role inventory, verifies each role's signed attestation against its externally provisioned key, copies only allowlisted files into the root-owned final directory and makes them read-only. Extra files, directories and symlinks fail closed.

Role ownership is:

- sender: `preflight.json`, `telegram_results.jsonl`, `queue_metrics.jsonl`, `fault_executions.jsonl`;
- observer: `business_outcomes.jsonl`, `receiver_receipts.jsonl`, `fault_results.jsonl`, `cleanup_ledger.jsonl`, `reconciliation.json`, `acceptance.json`;
- runner: signed execution authorization, both role attestations and `security_scan.json`.

## Evidence contract

Provider results contain unique delivery/obligation IDs, source event/version, causal dependencies, method, role, destination, enqueue/completion times, canonical provider observation digest, canonical authoritative-state digest, actual payload digest and privacy-preserving fingerprints of actual provider/target message IDs. Raw Telegram message IDs are prohibited in artifacts.

The independent receiver must read back every `sent` and `sent_noop` result for which receiver evidence is required. Its signed receipt must match message fingerprint, payload digest and state digest. For channel edits it also records the visible offer status, remaining lots and whether action buttons remain. Thus a no-op or a correctly shaped hash cannot prove the wrong final text/buttons.

The 32-case fault matrix is split at the provenance boundary:

- sender-signed `fault_executions.jsonl` contains the exact code-owned sanitized injected request/provider shape, adapter classification, call count and duplicate-effect count;
- observer-signed `fault_results.jsonl` contains the independently read raw durable record, applied retry deadline source and resulting state.

The 429 cases separately cover integer `1`, signed-32 maximum, missing, bool, string, fraction, zero, negative, max+1 and huge integer. Malformed values remain retryable and use bounded fallback; a valid integer is preserved.

Metrics cover every second `0..720` and end with zero ready, leased and unresolved backlog. Reconciliation and acceptance are recomputed from raw ledgers; driver-authored `pass` values have no authority. Cleanup covers every run-scoped synthetic identity and occurs only after measurement.

The runner itself scans the exact final flat inventory. `security_scan.json` is the sole explicitly self-excluded file because it is generated from the other bytes. The verifier reruns the scanner and compares its full result. Any extra/missing file, directory, symlink, secret/PII finding, hand-authored clean report or manifest mismatch fails closed.

## Authorization and execution order

The authorization is Ed25519-signed by the fixed deployment authority, valid for at most one hour, and binds authorization ID, run ID, nonce, exact Git SHA, trace/config/trust-policy hashes, command hash and both executable hashes. The runner must have EUID 0 so it can enforce distinct UIDs. The repository must be clean and exactly at the planned SHA. Plan files must remain root-owned and non-writable by group/other and are reverified after driver exit.

Execution order is:

1. verify fixed trust policy, host identity and key pairs;
2. verify clean exact Git SHA, plan bytes and production-deny fingerprints;
3. verify short-lived signed authorization and atomically consume it;
4. create isolated role directories and allowlisted environments;
5. start observer and verify its signed ready receipt;
6. re-hash the sender executable and start sender;
7. wait for clean exits, reverify plan, role inventories and signatures;
8. promote read-only evidence, run the exact scanner and full verifier;
9. export/checksum AI-readable evidence before run-scoped cleanup.

Any failure consumes the authorization and requires a newly signed authorization for a retry. It never causes an automatic provider retry or a downgrade of safety controls.

## Stop and remaining live gates

Stop immediately on fingerprint collision, unexpected route/role, secret/identity artifact, duplicate provider effect, invalid-offer publication intent, missing receiver evidence, inconsistent authoritative state, stale terminal/partial edit, unbounded 429, observer/provider provenance failure or nonzero final backlog.

Fault injection is forbidden during capacity calibration. A failed interval is cooled down and recorded; it is not retried every 100 ms and does not weaken expiry or latency semantics. If repeated receiver-backed calibration cannot meet fixed demand safely, the decision is `NO-GO`.

Still-live gates are infrastructure egress attestation, real bot/channel permission readback, real publication/edit/private/callback smoke, repeated channel calibration, the final authorized workload, post-run cleanup evidence and rollback rehearsal. Production remains untouched and is a separately authorized stage.
