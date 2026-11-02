# Datastore image stores, operation02

`deploy/maintenance/datastore_candidate_images.py` stages exactly the PostgreSQL
15.19 config `49a811b2…` and Redis 7.4.11 config `98c4c0ab…` into image stores.
It does not activate containers, change a service, touch data, or establish
serving/security acceptance. Existing Market health incidents and capture
stability remain OPEN. The sampled health of every container is preserved in
the fresh baseline; there is no new all-running-healthy prerequisite.

The JSON catalog is the canonical deterministic projection of the sealed
`datastore_candidate_contract.PROFILES`. The adapter pins the actual runs,
artifacts, ZIP lengths/digests and config IDs, while the contract binds report,
SBOM, source, Config, rootfs and archive provenance. PostgreSQL's one UNKNOWN
advisory and Redis's zero counts use that datastore contract; Market report
assumptions are not used. Config stays in RAM for complete typed comparison.

The adapter loads a separate ModuleType from the immutable Market05 source
`cd8b11fb…`. It reuses guarded transfer, ZIP/gzip/TAR validation, capacity,
daemon measurement, framing and closed diagnostics. It never invokes that
source's entrypoint or changes the original module/files. The a37 legacy
receipt/TAR adapter is disabled. Old operation01 App-handoff/parking contracts
remain sealed and are not prerequisites or authorities for operation02.

Root stages these public files exclusively under
`security-datastore-candidate-images-20261025-02` on both hosts: the adapter and
catalog at the root, pinned Market05 plus the catalog's four helpers in
`source/`, and exact `postgres.zip`/`redis.zip` public evidence in `evidence/`.
`controls/` and `attempts/` start empty. Files are genuine root-owned 0600,
directories 0700, with canonical protected parents. Stage no credentials.

Use the adapter's `--site bot|web --phase snapshot --source-sha256 <adapter-pin>`
to obtain actual current public baselines. Both baseline schemas are
`datastore_image_store_baseline/2`, operation-bound, fresh, and complete:
Native 15 containers/15 running; Web 27 containers/16 running. Engine snapshot
collection must be stable. During the operation Native stays exact; Web uses
only the inherited single fixed healthy F79 natural-restart observation from
the immutable baseline, with the exact cached successor. All other changes
refuse. This policy provides no capture-stability or serving proof.

Root creates a fresh nonce and a `datastore_image_store_control/2` object with
exactly `schema, operation, phase, profile, nonce, not_before, deadline,
worker_sha256, baselines, fences, admission, admission_sha256`. The operation is
`security-datastore-candidate-images-20261025-02`; profile is `postgres` or
`redis`; validity is at most 900 seconds. Each baseline identifies this adapter
pin and operation. Fences contain current full-nine metadata for M and Native's
existing empty SOURCE. Never read/hash/write their payloads. Admission names
are `<profile>-admission.json`, sealed by exact SHA for load controls.

Phases remain `receive-web`, `load-web`, `load-native`, and the internal
`stream-web`; the phase determines the target. Each profile is received once
on Web and its retained `attempts/<profile>-receive-web/source.tar` serves both
loads. Root mirrors the sealed archive admission into both `controls/`
directories. Fresh controls and baselines are required for each later load.
The inherited archive/framing wire schemas retain their engine names; the new
operation/control/baseline namespace is authoritative. Loaded image receipts
use `datastore_preloaded_image/2` and remain explicitly unaccepted.

Run both sites' NOAPPLY preflights in finite scopes before one requested phase.
Image operations require `--apply DATASTORE-IMAGE-STORES-ONLY-20261025-02` and the
inherited exact `market-images-<site>-<phase>-<nonce>.scope` binding: CPU at most
one core, MemoryMax 2 GiB, swap 0, TasksMax 64, RuntimeMax 900 seconds,
TimeoutStop 5 seconds and control-group SIGKILL. Strict SSH and normal artifact
authentication are inherited. Floors remain store 8 GiB, `/` and `/tmp` 2 GiB,
plus margin/output/full prospective import budget; distinct temporary storage
also requires the same budget above 2 GiB MemAvailable. No Go RLIMIT_AS.

Root must persist each bounded result before advancing. An unknown RPC retains
owned guards and immutable attempts for manual reconciliation; never retry or
infer daemon cancellation. No existing control, receipt, attempt or namespace
is overwritten/adopted. Do not reuse old operation nonces or substitute future
images, authority receipts, App completion, parking, activation or data proof.
