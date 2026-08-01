# Promotion upload cleanup contract

## Status

This is a default-dormant, transaction-scoped participant in the selected
`P0_UPLOAD_IN_FLIGHT = cancel_and_expire_unfinalized_uploads` policy.  It is
not a promotion coordinator, a deployment instruction, an authorization to
change traffic, or a filesystem/Object-Storage cleanup tool.

## Required coordinator order

After it has fenced normal write/upload admission and independently proved the
new local Writer Witness term, a root-controlled coordinator uses one
caller-owned PostgreSQL `AsyncSession` transaction:

1. build `PromotionSessionInvalidationBinding` from the active local term;
2. call `invalidate_sessions_on_promotion` for that exact binding;
3. call `cancel_and_expire_unfinalized_uploads_on_promotion` with the same
   binding; and
4. commit only after every other promotion participant and proof has passed.

The upload primitive rechecks the exact term before and after it acquires its
locks.  It requires the durable auth epoch staged by step 2 to match the
operation/site/term/lease/Witness transition exactly.  Thus a caller cannot
claim the upload P0 while skipping the chosen session P0.

`core.services.promotion_continuity_participants.stage_promotion_auth_and_upload_cleanup`
is the default local composition helper for steps 1--3.  It uses one supplied
binding and transaction, makes no transaction boundary, and rechecks the term
again after both participants have staged their state.  It still is not a
promotion coordinator.

## Exact mutation boundary

The primitive obtains PostgreSQL `SHARE ROW EXCLUSIVE` locks on
`upload_batches` and `upload_sessions`, so already-running SQL upload work
cannot create or advance a row between its inspection and mutation.  This is
not a substitute for the coordinator's admission fence: traffic must remain
blocked until the complete promotion transaction commits.

It changes only:

* batch rows in `collecting`, `uploading`, `uploaded`, or `failed` to
  `cancelled`; and
* session rows in `created`, `uploading`, `uploaded`, `finalizing`, or
  `failed` **only when** `final_chat_file_id` is null, to `cancelled`.

Timestamps are set to the exact durable authentication cutover timestamp.  It
does not delete a temporary path, change a `ChatFile`, detach a finalized
blob, perform network I/O, or commit/rollback.

## Fail-closed cases

The coordinator is stopped when it finds an active `committing` batch, a
`READY` session, a finalized (`final_chat_file_id` non-null) upload, an active
batch carrying a committed session, an invalid status, a missing/mismatched
auth epoch, or a changed/expired writer term.  Finalized blobs must instead be
covered by the verified physical blob frontier before promotion.

## Remaining boundary

This primitive is local Python/SQL scaffolding until a new Full Matrix
coordinator invokes it alongside live PostgreSQL recovery, Object-Storage
pull/replay, Witness CAS, traffic fencing, and destructive acceptance tests.
It must not be represented as Full Matrix readiness on its own.
