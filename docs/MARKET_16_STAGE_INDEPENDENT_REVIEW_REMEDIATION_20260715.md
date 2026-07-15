# Market 16-Stage Independent Review Remediation - 2026-07-15

## Scope

This record adjudicates four independent reviews of
`candidate/market-16-stage-remediation@48e8238959dffa77013cda1b81aa575954368fad`.
Every accepted or rejected item below was checked against the branch source,
tests, retained artifacts, and the controlling roadmap. This document does not
authorize staging or production deployment.

| Reviewer | Source report | SHA-256 |
|---|---|---|
| ChatGPT Pro | `tmp/chatgpt/7-pro-market-16-stage-branch-independent-review.md` | `a62b19bebcc8e0c3f54273ebe7e66a2e4c2e7b97e39a2e91e8321e4f92ed83ff` |
| ChatGPT Ultra | `tmp/chatgpt/7-ultra-MARKET_16_STAGE_INDEPENDENT_REVIEW_20260715.md` | `ee79bd1a68abec2486d5e7ea32e2a2e81c8e18b0261b729018a6396e26552b82` |
| Gemini | `tmp/gemini/market-16-stage-full-branch-roadmap-review.md` | `dd72227b7bc65f288d88ef51656c680ac0438133d037a94d62c060a3fecb3dee` |
| Claude | `tmp/claude/market-16-stage-full-branch-roadmap-review.md` | `ba6ef948a46f34315c33c369f485a75c4018dd490ee1fc561bd93a59978ed854` |

## Accepted Source Findings

| Finding | Decision and remediation |
|---|---|
| Stage 1 blocked realtime recipient | Accepted. WebSocket admission, heartbeat, client-message handling, and private Redis delivery now revalidate session and global Web lock. SSE also revalidates before every private notification and stops fail-closed after a lock. Public events remain unaffected. |
| Stage 2 outer contention loses the only intent | Accepted. The outer gate now returns the exact machine code `TRADE_CONTENTION_BUSY`; only that coded `409` is ambiguous in the WebApp. Generic/business `409` remains definitive. Trade submission is also disabled until the authenticated user identity is stable. |
| Stage 5 source-row lock precedes the market fence | Accepted. Republish acquires the transaction market fence before locking the expired source Offer; the canonical creation service safely re-enters the same advisory lock. Unit call-order tests and real PostgreSQL winner-order tests protect the contract. |
| Stage 8 SQL-only settlement proof | Accepted as a test gap. A guarded real-PostgreSQL proof now mixes settlement, commodity, direction, inactive status, and warning-excluded rows and verifies both comparable prices and warning reference. |
| Stage 9 already-retried due candidate | Accepted as a test gap, not a runtime defect. Real PostgreSQL tests now cover due retried rows under 100 new failures for both publication repair and channel-state repair. |
| Stage 11 missing receipt acknowledgement | Accepted. A sender carrying `command_id` treats missing or mismatched acknowledgement on a `2xx` response as retryable `503`. A legacy sender with no command identity retains receiver-first rollout compatibility. |
| Stage 13 stale history response | Accepted. A monotonic request revision prevents an older response, error, or finalizer from overwriting a newer forced filter/reset request. |
| Stage 14 in-process-only proof | Accepted. A guarded localhost Redis DB test starts two independent OS processes, runs the real Redis listener in each, publishes one logical event twice, and requires exactly one copy per connection. |
| PostgreSQL proof suites silently skip | Accepted. Merge and pre-release workflows now provision PostgreSQL/Redis and run a mandatory scratch-database gate for Stages 6, 7, 8, 9, 10, 11, 12, 13, and 15. The runner rejects any skipped proof test. |

## Contract Corrections

### Stage 4

The proposed global quota reservation service is not adopted. The project
deliberately keeps both offer homes available during a partition, and the
existing policy decision already accepts temporary cross-home divergence. The
exit criterion is corrected to local authoritative-home data and reservations.
Strict global quota during a partition would require an Iran authority call and
would change the accepted availability model; that is a separate architecture
decision, not a low-risk bug fix.

### Stage 6

The local advisory fence correctly closes `create-vs-close` races in each
authoritative home. It cannot make an Iran manual close instantly visible to a
disconnected foreign server, so the roadmap no longer claims an instantaneous
global close. Foreign convergence remains the existing sync/autonomy contract.

A concurrent Trade can also trigger the pre-existing optimistic-lock rollback
of a close batch until the next scheduler cycle. This is self-healing and was
not introduced by Stage 6. Adding broad row locking or per-offer close commits
would change close/trade semantics and is deferred to a separately reviewed
finding. Stage 6's exit criterion is therefore explicitly limited to Offer
creation admission.

## Qualified Or Rejected Findings

| Finding | Disposition |
|---|---|
| Stage 3 replay remains behind the outer lease | Qualified. A busy lease may delay replay, but the new exact retryable code preserves the same idempotency key, so it cannot create a second trade. Moving authenticated ledger replay into the dependency layer is more invasive and is not required for safety. |
| Stage 2 full browser-context closure | Not part of the accepted Stage 2 storage contract, which explicitly uses per-user `sessionStorage` for refresh/remount safety. Persisting financial intent indefinitely in `localStorage` requires expiry, abandonment, logout, and shared-device product decisions. The boundary remains documented residual risk. |
| Gemini Stage 10 duplicate telemetry | Rejected as unsubstantiated. The report provides no source location, event identity, reproducible sequence, or failing assertion. Per-offer business outcomes remain independently committed and tested. |
| Gemini Stage 16 WebKit risk | Qualified as generic regression risk, not a source finding. The historical WebKit failure passed on exact rerun without enough trace to establish root cause, so it remains a possible flake and is not described as a proven navigation error. |
| Orphan `republished_offer_public_id` column | Confirmed schema residue. The already-applied migration must not be rewritten. A future additive cleanup migration may drop the column/index only after rollout and receipt/retry drain; no runtime depends on it. |

## Evidence Status

- Stage 1-4 historical staging logs were not retained. Fresh verbose tests are
  revalidation only, so historical evidence remains `PARTIAL` permanently.
- Stage 5 still lacks a cleanup-protected mutating two-server republish case
  with outage, exact replay, late provenance sync, and source immutability.
- Stage 10 still lacks retained mixed local/remote partial cancel-all staging
  evidence and exact Bot copy evidence.
- Stage 14 now has a real local multi-process Redis proof. A deployed
  multi-API-worker staging smoke remains a separate acceptance item.
- The old final staging runtime was `1a698b8d`, older than both reviewed HEAD
  `48e82389` and this remediation. Its results are historical regression
  evidence, not acceptance evidence for the new HEAD.
- The 5,611-row manifest is a coverage catalog. The 342 executed scenarios do
  not satisfy the runner's full release-evidence rule because there is no
  mandatory-group mapping and no retained disconnect/reconnect plus ambiguous
  response retry execution. The roadmap is reopened until a bounded new
  acceptance run supplies those proofs or narrows the release gate formally.
- The isolated delivery matrix used an injected Telegram gateway and did not
  bind branch/commit in its own result. It remains useful deterministic policy
  evidence but is not real-provider or immutable release evidence.

## Verification Gate

The post-review source gate consists of:

1. focused backend security, forwarding, lock-order, and pagination-race tests;
2. focused frontend intent and history race tests plus production build;
3. mandatory real PostgreSQL proofs with zero skips;
4. real two-process Redis delivery proof;
5. verbose current-HEAD Stage 1/3/4 revalidation with commands and test IDs;
6. YAML parse, `compileall`, and `git diff --check`.

Final counts, exact commands, branch/commit identity, SHA-256 hashes, and raw
logs belong in the post-review evidence package. Passing this source gate does
not close the open staging evidence items above.

## Second Independent Re-Review

Two independent re-reviews examined `805f9dcd`:

| Reviewer | Source report | SHA-256 |
|---|---|---|
| Claude | `tmp/claude/market-16-stage-postreview-remediation-805f9dcd-review.md` | `86a03c584e3850577d9425b02fcc9156d18bd5324eb112fc80d8302bd32eb947` |
| ChatGPT | `tmp/chatgpt/market-16-stage-postreview-independent-rereview (1).md` | `f39c48d600c530e1adfda830983d147bc8da12710d96f1f9ab234b9bff872edf` |

The following findings were independently reproduced and accepted:

1. SSE revalidated user locks but did not carry the session identity needed to
   detect revocation of an already-open stream.
2. A command-bearing Stage 11 response could retain its `2xx` status after JSON
   parsing failed, and a partially shaped receipt could pass command-id-only
   acknowledgement.
3. The Stage 14 workflow command returned success when its required Redis test
   was skipped.
4. The PostgreSQL gate could reuse/drop fixed-name databases, leave partial
   creation behind, inherit generic application database URLs, and pass while
   its declared Redis dependency was unreachable.
5. The corrected Stage 5 lock order held the global admission fence across
   read-only validation that did not need serialization.
6. Gemini's final-staging PASS was inconsistent with the retained execution
   limitation and failed first two-server driver summary.

The source remediation now:

- passes the bearer-token `sid` into SSE and tests revocation without mocking
  the final access-decision helper;
- requires the complete terminal receipt shape for command-bearing successful
  expiry responses and maps malformed/empty success to retryable `503`;
- keeps read-only republish checks outside the fence while retaining the fixed
  mutation lock order;
- makes the real Redis proof reject any positive unittest skip summary;
- confines the PostgreSQL runner to a clean exact checkout and disposable
  localhost services, uses run-unique scratch names, refuses pre-existing
  databases, tracks only databases it created, cleans up from the first create
  and on interruption, verifies Redis reachability, pins generic database URLs
  per module, and records lifecycle evidence.

ChatGPT's evidence-binding finding is accepted as an artifact issue rather than
a runtime defect. The next evidence archive must include a Git bundle containing
the merge base, pre-review commit, and final remediation commit; pushing is not
required for independent object verification.

The Stage 2 full-browser-context persistence and the already-applied Stage 5
orphan column remain documented residuals, not regressions. The Stage 4/6
global-partition proposals remain rejected because they change the accepted
availability and authority model.
