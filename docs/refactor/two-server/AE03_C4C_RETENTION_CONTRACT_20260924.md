# AE03 C4c: terminal quota receipt retention contract

Status: **DESIGN / NO PRUNER ACTIVATED**. C4b2 can read a strict receipt without mutation, but neither the live Broker nor Supervisor runs that new code. A time limit alone is not authority to delete a receipt: it may be the only durable proof that a Provider invocation returned quota rather than an ambiguous effect.

## Scenario-based rule

| Situation | Required handling |
| --- | --- |
| A receipt has no durably accepted Supervisor wait/disposition | Keep it. The gap between Broker publication and Supervisor acceptance is not an acknowledgement. |
| A matching wait is `WAITING_QUOTA`, `READY_TO_CHECK`, `RESUME_CLAIMED`, or outcome is ambiguous | Keep it regardless of age; report capacity pressure, never silently evict. |
| A matching wait is durably resolved and acknowledged, but the old request/claim/response or another state still references the receipt | Keep it and hard-stop pruning until references are reconciled. |
| A resolved, acknowledged, unreferenced receipt is less than 10 days past the durable acknowledgement time | Keep it for diagnosis. Use acknowledgement time, not file mtime or Provider reset time. |
| A resolved, acknowledged, unreferenced receipt is at least 10 days past acknowledgement | It may be pruned only by a single maintenance owner after strict identity, reference and filesystem checks, unlink followed by directory fsync. Record bounded deletion evidence; a crash may safely leave it intact, never remove an active receipt. |
| The 64-record directory cap is reached before safe pruning is possible | Stop new quota receipt publication with an explicit capacity blocker. Do not raise the cap, choose oldest, or discard an unresolved receipt. |

The 10-day policy applies only to *resolved and acknowledged* diagnostic evidence. Incident-held and ambiguous evidence has no time-based expiry until a separate explicit disposition resolves it. This is not a backup/log cleanup rule and does not permit deleting unrelated files.

## Sequencing and trust boundary

1. **C4c-a, pure classifier:** create a bounded read-only classification from strict receipt + a future typed Supervisor disposition. It returns `KEEP_ACTIVE`, `KEEP_UNACKNOWLEDGED`, `KEEP_AMBIGUOUS`, `KEEP_RECENT`, `ELIGIBLE` or fail-closed `INVALID`; it never unlinks. Fake-clock and malformed/symlink/hardlink tests prove time cannot override references. If C5's disposition schema is not yet frozen, only this document is accepted; do not invent a parallel state authority.
2. **C5 first, durable authority:** the Supervisor persists the seven-field reservation and receipt/request digest, wait lifecycle and final acknowledgement under its existing lock with fsync. It must make STOP and unresolved claims win. A reader must validate the exact receipt and Supervisor identity rather than infer acknowledgement from task success or a missing file.
3. **C4c-b, deletion last:** implement the pruner only after C5's state reader and reference index are accepted. Run it at a proven quiescent ownership boundary, never concurrently with an unbounded Supervisor wake or Broker publication. Strictly scan all bounded entries; check the selected file by no-follow descriptor and full stat/bytes before unlink, check queue and Supervisor references, unlink exact basename via pinned directory descriptor, fsync that directory, and report the exact request ID and acknowledgement digest. A failed check deletes nothing.

The current Broker `_prune_orphan_responses` removes old *response queue files*, not terminal receipts. It must not be re-used as receipt retention authority. C4c acceptance requires tests for publication→wait crash, STOP and restart, active wait older than 10 days, acknowledged receipt younger/older than 10 days, duplicate/invalid references, clock rollback, 64-entry pressure, path swaps, unlink/fsync faults and idempotent restart. No live cleanup starts from this design alone.
