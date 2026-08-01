# Physical PostgreSQL promotion coordinator contract

`core/physical_postgres_promotion_coordinator.py` is a local, pure,
default-off composition gate for an FI↔IR physical PostgreSQL promotion. It
does not run a promotion. It never opens a filesystem path, reaches a network
endpoint, uses Docker, contacts the Writer Witness, opens PostgreSQL, fetches
Object Storage, changes traffic, or invokes an injected adapter.

Its purpose is deliberately narrow: bind the old writer's archived lineage,
the new live successor term, physical-WAL continuity evidence, and one
authority-signed *pre-CAS* Blob acceptance into a short-lived local
preparation capability. A successful preparation is not a writer-start
permit.

## Required ordering

The v2 Blob requirement needs a live former Writer-Witness term. A valid
promotion normally cannot both retain that former term and use a successor
term forever, so the verifier must not be weakened or retried after expiry.
The mandatory order is:

1. While the former term is still live, validate the prior activation,
   physical-WAL source receipt, v2 Blob requirement, and exact object version
   at `persist_physical_blob_pre_cas_acceptance(...)`.
2. Have the injected durable authority append and read back the exact
   canonical acceptance. Its pinned signature is checked before the caller
   receives a capability.
3. Only afterwards issue the successor Witness term / attempt its future CAS.
4. Pass only the verified durable acceptance to
   `prepare_physical_postgres_promotion(...)`.

The coordinator mechanically rejects an acceptance whose `accepted_at` or
authority-signed readback `issued_at` is later than the successor term's signed `issued_at`
(`PRE_CAS_BLOB_ACCEPTANCE_AFTER_SUCCESSOR_TERM`). It never calls the v2 Blob
verifier and never asks whether the former term is still live. An expired
former term is therefore inspected only as signed archived lineage; it cannot
be used as a current writer permit.

The durable acceptance's canonical record and signed authority readback are
specified in [the pre-CAS acceptance contract](PHYSICAL_BLOB_PRE_CAS_ACCEPTANCE_CONTRACT.md).
They bind the exact physical-WAL source-receipt schema and SHA-256, former
writer epoch/lease/transition/proof, FI/IR route, campaign/release/stream,
baseline generation/manifest/LSN, destination age recipient, mapping object
key and immutable version, mapping and inventory/receipt hashes, Blob
timeline, and acceptance timestamp. This makes a later coordinator check
independent of a live former source without accepting generic, v1, or
unrelated Blob material.

## Inputs and preparation

`prepare_physical_postgres_promotion(...)` accepts only opaque, previously
verified capabilities:

| Input | Required binding |
| --- | --- |
| `prior_activation` | Verified FI/IR role matrix and archived former-writer term. It is checked as lineage, not as a currently live writer. |
| `current_witnessed_term` | Locally unexpired signed successor term held by the prior standby. |
| `supplied_physical_wal_eligibility` | Existing eligible physical-WAL assessment. |
| `verified_physical_wal_evidence` + `verified_remote_ack` | Opaque signed WAL and strict pull-plane acknowledgement evidence used to recompute the assessment for the exact successor term. |
| `verified_pre_cas_blob_acceptance` + `pre_cas_acceptance_config` | Authority-signed durable acceptance and the independently pinned authority public key/freshness policy. |

The coordinator is disabled unless
`PhysicalPostgresPromotionCoordinatorConfig(enabled=True)` is passed. Raw
JSON, raw signed receipts, a generic Blob receipt, a v1 inventory receipt, a
v2 requirement, and a Blob binding cannot satisfy the post-CAS input.

The supplied physical-WAL assessment is not trusted merely because its status
says `eligible`. The coordinator rechecks the opaque WAL evidence and remote
ack, calls the physical-WAL gate again with the exact archived prior
activation and current successor term, and rejects a changed, stale, blocked,
or candidate-term-mismatched result.

The durable acceptance must agree with the physical-WAL source evidence and
prior route on every common field: source/target, campaign, release, stream,
base generation/manifest/LSN, former epoch/lease/transition/proof,
destination age recipient, and exact source evidence schema/hash. Its mapping
replay LSN must equal its mapping baseline LSN. It is not a PostgreSQL replay
receipt or a claim of Blob coverage after that baseline.

The resulting `PreparedPhysicalPostgresPromotion` is a recheckable local
capability. Its `require_*` helper reruns every underlying verifier and
rejects a changed projection, stale proof, disabled configuration, raw
replacement, `dataclasses.replace` forgery, or boolean in an integer field.

`live` has a deliberately limited meaning here: the already signed successor
Witness proof is inside its local expiry/safety window. It does **not** mean
that the coordinator queried the live Witness, observed a CAS result, or
proved that a partitioned former writer has stopped.

## Explicit runtime boundary

`prepare_physical_postgres_promotion_execution_boundary(...)` is a second
local check. It does not execute anything. It hard-fails unless explicit
objects expose all five future runtime methods:

| Required adapter | Required method | Runtime responsibility not implemented here |
| --- | --- | --- |
| Witness CAS | `consume_promotion_term` | Fresh Witness query, durable compare-and-swap/term consumption and lineage serialization. |
| Former-writer fence | `fence_former_writer` | Stop and verify all old-writer SQL, worker, bot, and provider-visible effects. |
| Target recovery | `recover_and_promote_target` | Pull immutable objects, decrypt, restore the base, replay ordered WAL, verify timeline/frontiers, and promote the target. |
| Traffic fence | `switch_fenced_traffic` | Change ingress/DNS/service routing only after exclusive writer fencing. |
| Promotion DB transaction | `run_promotion_transaction` | Atomically persist the promotion lineage and run the session/upload continuity work. |

All adapters default to `None`. Missing or malformed interfaces produce exact
hard error codes such as `RUNTIME_ADAPTER_WITNESS_CAS_MISSING`; no boolean or
default can bypass the boundary. The boundary does not call these methods, so
it must not be presented as a live CAS, fence, recovery, or successful
promotion.

## Still required before Full Matrix

This contract closes only the local composition gap. The following remain
runtime and destructive-test work:

1. A root-controlled implementation of the durable acceptance authority and
   a Witness CAS/promotion-lineage store.
2. Former-writer fencing verified across application writes, PostgreSQL,
   workers, bot processes, traffic, and externally visible effects.
3. Real private Object-Storage pull, decrypt, base restore, ordered WAL
   replay, target timeline validation, and PostgreSQL promotion.
4. Enforcement of strict remote durable/replay acknowledgement at the source
   writer's response boundary.
5. Atomic integration with promotion auth/session invalidation and unfinished
   upload cleanup, plus route and external-effect gates.
6. An observed three-server destructive failover/failback campaign with
   immutable evidence.

Until those actions and their evidence exist, a successful local preparation
is deliberately not Full Matrix readiness and must not start a writer.

## Focused verification

```bash
PYTHONDONTWRITEBYTECODE=1 /usr/bin/python3 -B -m unittest \
  tests.test_physical_blob_pre_cas_acceptance \
  tests.test_physical_postgres_promotion_coordinator -v
git diff --check
```
