# Release 0 emergency operating boundary

## Purpose

Release 0 is the smallest safe production posture for the three-site design.
It makes the normal writer explicit and fenced, preserves an independently
verified recovery point at WA-IR, and fails closed when the required safety
proof is absent.  It is deliberately not a multi-writer design.

## Permitted topology

* WA-FI is the sole application writer.
* WA-FI may accept or renew writer authority only while its local writer
  guard has a valid, current lease from an independent Witness.
* The Witness is a separate failure domain.  Object Storage, WA-FI, and
  WA-IR are not substitutes for the Witness and cannot grant writer
  authority.
* WA-IR remains dark.  It receives and verifies the approved recovery
  snapshot material, but it does not start application, bot, worker,
  migration, or writer processes.
* WA-IR's snapshot is recovery material only.  It is not evidence that
  WA-IR has replicated every committed transaction, and it must not be
  represented as a hot standby.

## Explicitly forbidden in Release 0

* Promoting WA-IR to writer, automatically or manually.
* Treating an Object Storage object, a snapshot freshness timestamp, or an
  operator acknowledgement as a Witness lease or a fencing proof.
* Operating WA-FI and WA-IR as concurrent writers.
* Bypassing a failed, expired, malformed, or unverifiable Witness lease.
* Starting WA-IR application-side processes merely because WA-FI or Iran
  connectivity is unavailable.

If WA-FI cannot prove current Witness authority, its safe action is to fence
and stop writes.  In an Iranian Internet-cut scenario, Release 0 therefore
provides split-brain safety and a bounded-RPO recovery point; it does **not**
provide Iran-writer continuity.

## Readiness gates

Release 0 is ready for use only when all of the following are true:

1. The deployed WA-FI release identity, compose material, and immutable
   application/bot image digests have been verified by the release controls.
2. The WA-FI writer guard has a valid, renewable lease from the independent
   Witness, and an expired or unavailable lease demonstrably fences writes.
3. The Witness's identity, authority key, durable state, and reachability
   are independently checked; no shared host or Object Storage authority is
   accepted in its place.
4. WA-IR has a successfully verified dark recovery snapshot whose observed
   age is within the declared Release 0 RPO bound.
5. WA-IR is verified dark: no application, bot, worker, migration, or writer
   service can accept writes there.
6. The snapshot restore procedure has been exercised against an isolated
   target and its result recorded without changing production authority.
7. Monitoring and alerts cover Writer-lease expiry, Writer-guard fencing,
   Witness loss, snapshot verification failure, and RPO-bound breach.

Any failed gate is a not-ready result.  It must not be converted into a
writer override or an WA-IR promotion.

## Operating and rollback boundary

Normal operation is WA-FI writer with a current Witness lease and WA-IR dark
snapshot verification.  On loss of lease, missing proof, or release-identity
mismatch, WA-FI must cease writes.  WA-IR remains dark; operators may use the
verified recovery material only through the separately approved restore
procedure.

Rollback means restoring the previously verified WA-FI fenced release and
confirming that exactly one writer guard again holds a valid Witness lease.
Rollback never consists of enabling WA-IR writes, copying authority state by
hand, or weakening the guard.  If a safe rollback cannot be proved, retain
the fail-closed state and investigate from preserved evidence.

## Relationship to later work

This document does not authorize or claim completion of either of the
following:

* **Iran-writer continuity:** WA-IR promotion requires a separate,
  independently verifiable design for durable Witness fencing, replicated
  database state and ordered replay, application/commit fencing, and
  external-side-effect handling.
* **Full Matrix:** Full Matrix is a separate physical verification campaign
  with real phase adapters, evidence collectors, destructive-test controls,
  and post-effect verification.  Passing Release 0 readiness gates is not a
  Full Matrix result.

