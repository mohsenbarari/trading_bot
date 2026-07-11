# Dual-Platform Registration Stage 3 Review-2 Remediation And Stage 4 Follow-up

Date: 2026-07-11

Status: source remediation complete; combined independent review required before Stage 5.

## Scope And Authority

This change accepts the findings in
`tmp/chatgpt/dual-platform-registration-stage3-independent-review-2.md` against Stage 3 commit
`0004d7a3`, preserves Stage 4 commit `61ed0b52`, and remediates the shared contracts on top. Iran
remains authoritative for User, Invitation, accountant/customer Relation, and Invitation SMS result
state. Foreign remains authoritative only for Telegram-local intent/FSM state and submits signed
commands. Ordinary non-messenger sync projects Iran truth back to foreign.

No migration is applied, no runtime flag is enabled, and no push, deploy, staging action, or
production release is part of this source change.

## Finding Disposition

| Finding | Disposition | Evidence |
|---|---|---|
| `H-1` peer-local numeric requester authority | Fixed | Strict canonical account/mobile/Telegram principal envelope; Iran locks all matches, requires one exact row, and re-checks deletion, active status, role, customer/accountant policy, and capacity. Numeric peer IDs are not transported or used in idempotency. |
| `H-2` create/transition lock inversion | Fixed | Transition pre-read captures the complete advisory key set before refresh, acquires globally sorted locks, then locks and freshly reloads the Invitation. Changed identity fails closed. Creation exact retry uses the same advisory-before-row order. |
| `H-3` stale Relation overwrite during deletion | Fixed | Deletion performs no candidate mutation, locks Invitation/advisories before Relation, reloads Relation with `populate_existing`, verifies its token, and rejects missing/nonpending Invitation state before mutation. Real PostgreSQL covers completion versus owner closure. |
| `H-4` foreign expiry mutation | Fixed | Foreign pending lookup filters effective expiry without sweep/commit; expiry sweeps fail outside Iran; sync receiver and worker reject foreign-origin Invitation and Relation writes. |
| `M-1` nondurable SMS result | Fixed | One Iran-local NO_SYNC row is prepared in the Invitation/Relation transaction. A claim is committed before provider I/O, attempt count is bounded to one, exact retry returns the durable result, and no blind resend occurs. Explicit provider rejection is `failed`; timeout, transport loss, malformed response, or accepted-without-message-id is `ambiguous`. Existing templates and category flags are reused. |
| `M-2` unknown signed customer fields | Fixed | The customer command is strict with `extra=forbid`; body/source/idempotency checks remain mandatory. |
| `M-3` evidence breadth | Fixed for changed behavior | Unit and PostgreSQL tests now cover canonical principal drift/split/inactive state, strict schema, foreign read-only lookup, source authority, replay-stable SMS outcomes, lock order, and deletion/completion consistency. Broader rollout load/soak remains assigned to later stages. |
| `L-1` retry HTTP 201 | Documented | The additive `created`/`already_pending` contract is unambiguous; changing status-code convention is not required for correctness and is deferred. |
| `L-2` static proof limits | Retained gate | Single-constructor scanning is combined with route, transaction, concurrency, and PostgreSQL behavior tests. |
| `L-3` UTC warnings | No change | Existing project UTC behavior is retained; a broad time rewrite is outside this stage. |

## SMS Crash And Replay Contract

- Policy-disabled Standard/admin and Tier-1 invitations persist `disabled` and never call SMS.ir.
- Enabled accountant and Tier-2 creation persists `pending` atomically with Invitation/Relation.
- The only attempt is claimed and committed before provider I/O.
- Provider acceptance with a usable message id persists `accepted`.
- Explicit provider/configuration rejection persists `failed`.
- Timeout, connection loss, non-JSON response, uncertain server response, or success without a usable
  message id persists `ambiguous`.
- Crash after claim is converted to `ambiguous` on replay; it is never resent automatically.
- An exact API retry returns the prior durable status and preserves the existing SMS templates.

This bounded design intentionally does not add a worker or outbox. It chooses duplicate prevention
and honest ambiguity over an unsafe retry after an unknown provider outcome.

## Verification

- Focused changed-surface suite: `166` passed.
- Full backend: `2812` passed, `38` expected opt-in skips.
- Real PostgreSQL Stage 3: `9` passed.
- Real PostgreSQL Stage 4 compatibility: `7` passed.
- Static: diff check, compile, authority scans, one Invitation constructor, no manual-review state,
  no test-only service branch, and local-state registry checks passed.
- Final database state: active database `trading_bot_db`, Alembic `f7c8d9e0a1b2`, zero Stage 1 tables,
  and zero Stage 3/4 scratch databases.

Both PostgreSQL runs used allowlisted scratch names, guarded Alembic preflight, and
`docker compose run --no-deps`. The earlier Stage 4 verification incident remains recorded in the
Stage 4 document; this remediation did not touch the active schema.

## Remaining Gates

- Combined independent review must examine Stage 3 original range, Stage 4 commit, and this
  remediation range and return `GO` before Stage 5.
- The known Stage 2 post-Web-session-commit response-loss contract remains a rollout blocker and is
  not claimed fixed here.
- Schema rollout, mixed-version proof, backup/restore, real two-server ordering, load/soak, clock
  evidence, retention sizing, and operator rollback remain later-stage gates.
- All registration/Sync-v2 flags remain off.
