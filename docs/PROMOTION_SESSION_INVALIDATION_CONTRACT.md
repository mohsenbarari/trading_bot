# Promotion session invalidation contract

`core.services.promotion_session_invalidation_service` supplies one
default-dormant, transaction-scoped primitive for the instant immediately
before a separately authorized WebApp writer begins accepting traffic.  It is
not a promotion mechanism, a routing mechanism, a Witness client, or a
traffic authorization.

## Required coordinator ordering

1. Fence/stop old admission and establish all independent promotion evidence.
2. Acquire and validate the root-owned local Writer Witness lease.
3. Build `PromotionSessionInvalidationBinding` with
   `bind_promotion_session_invalidation(operation_id=...)`.
4. Open the coordinator's caller-owned `AsyncSession` transaction.
5. Call `require_active_promotion_session_invalidation_binding(binding)` at
   each of the coordinator's own pre/post-lock boundaries as needed.
6. Call `await invalidate_sessions_on_promotion(db, binding=binding)` in that
   same transaction, together with every other durable cutover participant.
7. Commit only after the entire coordinator has passed its own checks; on any
   failure, roll back the caller transaction and keep traffic fenced.

The invalidation function independently repeats the exact live Writer Witness
term check before its singleton-row lock and immediately before it performs
bulk mutation.  It never calls `commit`, `rollback`, Redis, Object Storage, a
remote host, a routing API, or the Witness.

## Durable effects

Migration `0promauth01` adds the singleton `promotion_auth_epochs` row and
`0promauthop01` adds its append-only consumed-operation ledger.  A successful
call, in one transaction, stages all of the following:

- marks every active `user_sessions` row inactive and non-primary;
- expires every pending `session_login_requests` row;
- cancels every active single-session recovery request and closes both action
  windows;
- inserts or advances the singleton auth epoch with the exact local
  site/epoch/lease/transition/operation identity and a strictly monotonic
  cutover;
- records `minimum_token_iat`, a logical whole-second JWT cutoff.

The immutable ledger retains every consumed operation ID and Writer Witness
term.  It closes the replay case that a singleton alone cannot represent: an
operation that belonged to a *past* term cannot be reused after a later term
has advanced the current singleton.

`operation_id` replay is idempotent only when the complete Writer Witness
term identity matches.  A reused operation against another term, a same term
with another operation, a lower term, or a mismatched active lease fails
closed.  A later valid writer term advances the cutoff again.

The singleton/ledger insert race is intentionally left to database unique
constraints: a concurrent first caller receives a transaction error and must
roll back/reconcile rather than silently treating an unknown epoch as valid.

## Token and realtime admission

No durable epoch means existing JWT behavior is preserved.  Once one exists:

- `create_access_token` emits a server-controlled integer `iat` NumericDate;
- HTTP auth calls `enforce_access_token_auth_epoch` after JWT signature
  verification and rejects missing, malformed, future, old, or non-access
  JWTs;
- WebSocket/SSE access-token verification uses the same epoch decision before
  accepting private traffic and rechecks it while a connection is live;
- sessionless legacy JWTs are rejected because they have no valid post-cutover
  `iat`;
- a post-cutover access JWT without `sid` remains valid if its `iat` passes;
- refresh remains unavailable for inactive sessions even if a repository
  accidentally returns one.

The cutoff uses `ceil(cutover_at.timestamp())`, so a token minted in the same
whole second *before* a sub-second cutover cannot pass.  A fresh token may
need to wait until the next NumericDate second; this is intentional and safer
than accepting an ambiguous pre-cutover token.

## Limits

This primitive cannot by itself drain an already-running request, stop a
former process, acquire a Witness term, prove replicated database state,
publish a physical-WAL receipt, or authorize traffic.  Those remain explicit
Full-Matrix coordinator and deployment gates.  The coordinator must invoke
this primitive while old traffic is fenced; otherwise a request that already
read older authorization state may complete outside this function's database
transaction.
