# V1–V2 writer bridge contract

This is the mandatory bridge between the operational V1 writer-admission
receipt and the V2 Witness-roundtrip strict-writer response.  It exists to
preserve the three-site rule: one active writer, no direct FI↔IR control path,
and no fabricated synchronous cross-site database transaction.

It is default-off and is not a deployment, promotion, traffic, transport, or
Full-Matrix authorization.

## Why a new generation is required

The historical V2 strict-writer row can compare scalar V1 site/epoch/lease
facts, but its signed runtime receipt does not bind either a V1 parent receipt
or a cryptographic V1–V2 bridge.  Scalar equality is not a proof that the
V1 current-term evidence and the V2 Witness term describe the same authority.
It therefore remains historical evidence only.  A Gen2 receipt, table, and
adapter must never silently accept, convert, or fall back to that generation.

## Pre-transaction bridge certificate

Before opening PostgreSQL, the root-owned caller must obtain and verify all
of the following:

1. a fresh opaque V1 `transaction_commit` admission and its identity-bound
   current-term provenance;
2. an opaque V2 prepared strict-writer instruction; and
3. a short-lived canonical Ed25519 **bridge intent certificate** signed by a
   dedicated Witness bridge key.

The certificate binds the exact V1 admission intent/current-term provenance
to the exact V2 instruction, V2 term, activation, configuration, and
attestation pins.  Its signer key is distinct from every V1 current-term or
promotion key, V2 Witness/relay key, and local V2 commit signer.  The
certificate has an ID, configuration digest, intent digest, issue/expiry
timestamps, and canonical signed bytes.

The certificate intentionally does **not** contain the final V1 parent UUID
or parent hashes.  Those values do not exist before the local transaction.
Requesting a parent-bearing certificate after V1 persistence could put remote
Witness or HSM I/O inside an open PostgreSQL transaction, which is forbidden.

## One short local transaction

The root-owned Gen2 adapter must own the following short, PostgreSQL-only
sequence.  It must not perform network, Object Storage, peer, remote-HSM, or
other external I/O.

1. Take an advisory transaction lock derived from the V2 attestation identity
   and look up the Gen2 row under `FOR UPDATE` **before** persisting V1.
2. If an exact row already exists, it must be handled as a **durable
   reconciliation**, not as a recreated live Gen2 observation.  Revalidate
   its stored Gen2 receipt, certificate, bridge intent, and V1 parent
   projection under the row lock, then return only a typed,
   non-authorizing historical/reconciliation result without advancing the V1
   head.  The opaque live Gen2 observation is deliberately process-local and
   must never be reconstructed from a database row after a restart.  If that
   durable verification is unavailable, mismatched, or incomplete, the
   adapter must return `reconciliation_required` and request an independent
   hard fence; it must not claim an idempotent live success.
3. Otherwise, persist **and flush, without committing** the opaque V1
   transaction admission with the reviewed V1 adapter, yielding one verified
   immutable parent receipt.
4. Bind that parent receipt to the pre-issued intent without external I/O:
   `parent_binding_sha256 = H(certificate_sha256 || v2_commit_id ||
   parent_id || parent_commit_sha256 || parent_receipt_sha256)`.
5. Build and locally sign the Gen2 V2 runtime receipt **after that parent
   flush/bind and before the Gen2 row is inserted**.  Its signature covers
   the full V1 parent projection, certificate bytes/digest/intent digest, and
   `parent_binding_sha256`, together with all original V2 pins.
6. Insert one append-only Gen2 row containing the signed V2 receipt, exact
   certificate bytes, V1 parent fields, and one-time attestation consumption;
   then flush.  The outer owner alone commits or rolls back.

The Gen1 and Gen2 strict-writer tables share an additional immutable
PostgreSQL attestation-consumption registry.  A `BEFORE INSERT` trigger on
each source table claims the canonical `attestation_sha256` in that registry
inside the same transaction.  The registry primary key is deliberately the
digest alone, rather than a generation-qualified key: a Gen1 and Gen2 attempt
for the same attestation therefore conflict even if they race.  Backfill of
historical rows rejects a cross-generation overlap rather than selecting a
winner.  The adapter still looks up an existing Gen2 row first for an
idempotent retry; a Gen1 registry entry is not a valid Gen2 retry or fallback
and must keep the new path blocked/fenced.

A local signer used in step 5 must be bounded and local to that transaction
boundary.  Its signature is a local bounded intent/attestation, **not proof
that either row is durable**; only the later known outer commit establishes
durability.  If it requires a remote call, the transaction cannot claim atomic
strict response semantics and must remain disabled.

## Post-commit and recovery

Only after a successful, known commit may the caller finalize the bound
prepared response.  Finalization freshly rechecks V2 liveness and the bridge
capability, then releases an opaque observation.  If finalization fails,
withhold the application response and issue an independent durable hard fence;
never claim that a rollback can undo an already committed transaction.

An unknown commit outcome is reconciled by a fresh locked lookup of the exact
Gen2 row.  That lookup can yield only the same non-authorizing durable
reconciliation result described above, never a rebuilt process-local
observation or writer/readiness capability.  Blind retry is prohibited.  The
durable V2 attestation/consumption uniqueness and V1 head CAS are the replay
authorities; the bridge adds no process-local replay store.

## Runtime integration boundary

The bridge does not make existing application writes safe by itself.  Actual
API, bot, startup, worker, and external-effect paths must use an explicit
fresh-session writer transaction runner.  SQLAlchemy event hooks cannot fetch
the pre-transaction Witness/bridge evidence safely, and external effects need
their own post-commit/outbox boundary.
