# Strict remote-ack to writer-response boundary

`core.physical_strict_remote_ack_writer_response` is a root-owned, local-only
admission boundary for one normal-direction continuity point:

```text
webapp_fi writer transaction
  -> verified FI request / IR signed durable-replay receipt
  -> verified IR recovery + durable-ledger result
  -> active FI writer fence + current Witness term
  -> one local durable writer response
```

It is disabled by default. Its configuration requires an existing absolute,
root-owned `0700` state directory, pinned source/destination remote-ack,
fence, and local-commit signer keys (the source/destination route keys must
differ), and one exact `webapp_fi -> webapp_ir` binding. Reverse direction,
booleans in integer fields, unpinned keys, unsafe state, stale/future evidence,
and a changed term all fail closed.

## Required sequence

1. A caller presents an opaque live FI Witness term, opaque signed remote-ack
   evidence, opaque request-bound IR recovery evidence, a typed IR durable
   ledger result, and an opaque signed active FI fence.
2. The boundary revalidates every input against one campaign/release/schema,
   baseline/stream/frontier, Object-version set, route recipient, Writer
   epoch/lease/transition/proof, request IDs/nonces/hashes, receipt IDs/nonces
   and hashes, recovery replay frontier, and the exact durable receipt.
3. Only then does it mint an opaque, short-lived commit permit. Its root-owned
   local ledger rejects any prior consumption of the request or receipt
   identity.
4. `commit_physical_strict_remote_ack_writer_response` revalidates the permit
   again while holding that replay lock. Only after that point can it call the
   injected `PhysicalStrictRemoteAckWriterCommitBoundary`.
5. The injected boundary must atomically persist the application response and
   its unique remote-receipt-consumption record in its own local writer
   transaction before returning a canonical, pinned-key signed receipt. That
   receipt binds the exact configuration digest, permit digest, request and
   receipt identities/hashes/nonces, recovery evidence/replay, full binding,
   commit ID, local durable commit ID, and local response ID.
6. The boundary writes its independent root-only anti-replay ledger only after
   the signed local receipt validates. It can then mint the opaque
   `VerifiedPhysicalStrictRemoteAckWriterResponseObservation` consumed by the
   Full-Matrix readiness oracle.

No acknowledgement, stale/changed Witness term, absent/expired fence, exact
identity mismatch, replay, or invalid permit can reach the injected writer
callback. A malformed callback receipt cannot mint commit evidence; it does
not consume the boundary ledger, so the caller can retry with a new valid
local transaction after diagnosing the callback failure.

## What this boundary does not do

This module does not open PostgreSQL, connect to a peer, Object Storage, or a
Witness, send a request, fetch a receipt, invoke SSH/Docker/shell, change
routing, fence a process, promote a standby, or execute Full-Matrix. It does
not itself prove a network or Object-Storage roundtrip.

The FI request/IR receipt objects must already have arrived through a separate
reviewed transport adapter. Object Storage request/receipt transport is an
explicit follow-up; the local signed writer receipt is not a substitute for
that transport evidence. Likewise, the injected transaction adapter is a
contract only: it must implement its documented atomic database response and
receipt-consumption transaction before any production writer path may use it.

The resulting permit, commit evidence, and oracle observation are all less
than writer, route, promotion, transport, or external-effect authority.

## Oracle integration

`core.physical_full_matrix_campaign_readiness` projects the opaque observation
without opening the boundary ledger. It rechecks the signatures, term,
receipt/recovery/binding/fence chain and the signed local commit receipt, then
compares the projection with its campaign binding. The readiness report remains
non-authorizing even when every slot is observed.

The legacy `PhysicalFullMatrixStrictRemoteAckWriterResponseObservation` has
boolean policy fields and is intentionally no longer accepted by the readiness
oracle. It cannot demonstrate an actual local transaction waited for the exact
remote receipt.
