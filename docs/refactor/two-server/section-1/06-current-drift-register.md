# Current Drift Register

Status: open findings; no implicit fixes authorized

| ID | Severity | Finding | Impact / required resolution |
| --- | --- | --- | --- |
| `DR-001` | resolved-doc | Target inventory doc said SSH auth failed | Updated with verified access/capacity; target remains unprovisioned |
| `DR-002` | high | Both production roles report release `e533d415`, 20 commits behind current `main` at audit | classify those commits and choose an exact migration baseline; do not silently deploy `main` |
| `DR-003` | high | Logical roles are encoded as historical `iran`/`foreign` server labels | replace capability checks deliberately; preserve true business `home_site` authority |
| `DR-004` | blocker-implementation | Offer lacks an immutable creation surface independent of `home_server`, and Trade lacks the approved complete provenance snapshot | semantic contract approved؛ implement ADR/schema/backfill/sync/parity before co-location/topology rewrite |
| `DR-005` | medium | Deep read-only parity is complete with zero business drift, but persisted operator parity status is `missing`/not fresh | add non-mutating inspection plus explicit operator receipt workflow; do not forge status |
| `DR-006` | high | Bot-Finland root disk is 89% full; ignored release output is ~1.2 GiB and backups ~2.2 GiB | approve retention/cleanup manifest before migration; protect active rollback/restore assets |
| `DR-007` | high | staging and obsolete three-site containers run on production hosts | identify dependencies/traffic, then drain and decommission under separate approval |
| `DR-008` | high | production Market services use paths named `staging-shadow`/`market-data-staging-shadow` | prove data class and consumers, then migrate/rename with replay and rollback evidence |
| `DR-009` | blocker-op | New Finland target has no firewall baseline, Docker, proxy, app/data layout, monitoring or backup; SSH password auth/X11 are enabled and no swap exists | approve and apply idempotent provisioning/hardening before staging |
| `DR-010` | medium | local assistant/project route summary is older than the observed 30-route frontend | regenerate canonical docs from code and add staleness checks |
| `DR-011` | blocker | Background jobs are authorized by old server names although the target combines roles | introduce explicit job capabilities/one-owner matrix and test every job before enabling it |
| `DR-012` | medium | role-specific app image IDs differ while release SHA matches | artifact manifest must bind role, commit, digest, config and schema |
| `DR-013` | resolved-doc | all 217 FastAPI decorators, 203 Bot handlers, 30 Web routes and 15 authority jobs have unique family seeds; the six empty `test_refs` now link to `tests/test_p1_00_characterization_six_gaps.py` and scenario records exist | keep the handlers; implementation Stages must not treat characterization as a product change |
| `DR-014` | high | Market archive is ~3.95 GiB with million-row outbox/revision/fact tables and no approved consolidation retention/replay contract | design Market migration separately from app DB merge |
| `DR-015` | deferred-blocker | no already-observed deploy/restart/backup/restore duration exists; this refresh cannot manufacture timing and live re-inspection is unauthorized | Codex decision 2026-09-03: `DEFERRED_BLOCKER`, `resolution_stage: DPL-1`. Required evidence is measured deploy, independent API/Bot restart, backup, and restore+verification durations. Forbidden: estimate from script length, treat RTO/RPO as observation, or run an unauthorized production rehearsal. Absence of timing does not block closing `P1-00`. Because of this dependency, `P1-02` must not start before `DPL-1` completes. |
| `DR-016` | high | current child pollers/workers/Market processes/timers are inventoried, but exact target compose/image/credential/readiness binding is not designed | close in `P1-03`; any unknown or overlapping owner blocks activation |
| `DR-017` | low | incremental `src/interfaces/http_api/routers/user_router.py` declares four routes but `main.py` does not mount it | retain as dormant code until `P1-01` reference/cleanup review; never count it as a live API contract |
| `DR-018` | residual | Earlier P1-00 account-status characterization runs, before the fail-closed suite fence, may have contacted localhost Redis | `NOT_REINSPECTED`. Possible localhost contact. Live Redis was not read or mutated to check. Current characterization glob denies TCP to `127.0.0.1:6379` / `localhost:6379` and forbids constructing a live Redis client. |

## `P1-00` failure-boundary registry

The compact machine source is `inventory/scenario-records.json`.
ELIGIBLE family×boundary collision cells are REQUEST plus the P1-00b
isolated-sqlite overlap and child-process reload for AUTH, IDENTITY, OFFER,
TRADE, MARKET, NOTIFY, MSG, TG, ADMIN and SYNC. MSG overlap before commit
and child-process replay create two independent rows and duplicate threading
side effects because persist has no command identity. Seed handler assertions
remain a High blocker for 411 seeds; fifty-four exact handlers are evidenced.
Channel-trade confirmation with a mocked execute does not evidence those
TRADE seeds. Sibling family membership does not clear remaining MSG/TRADE CRUD seeds.
Origin reject/permission/retry/concurrent/expiry/commit-failure and Telegram
`UNLINKED-BOT` are evidenced. Origin SUCCESS/policy/notification/side-effect-retry
stay `BLOCKED_EXPLICIT`. Durable sqlite Trade rows and live overtime persist do
not close those cells. They are not owned by `P1-04` or `P1-06`. The
amended `P1-00` exit gate has zero unresolved High. `P1-00b` stays
`IN_PROGRESS`.

| ID | Family / boundary | Disposition |
| --- | --- | --- |
| REQUEST concurrent / restart | isolated sqlite overlap with post-lookup pre-commit yield + child-process reload | evidenced |
| AUTH / IDENTITY / OFFER / TRADE / MARKET / NOTIFY / MSG / TG / ADMIN / SYNC concurrent / restart | isolated sqlite overlap after lookup or before commit + child-process reload | evidenced in P1-00b |
| MSG concurrent / restart | no command identity; two independent rows and duplicated threading | evidenced in P1-00b |
| cells without a mutating path | `NOT_APPLICABLE` with a behavior-specific reason | closed on P1-00 |
| `P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE` | every seed except the fifty-four executed handlers | `BLOCKED_EXPLICIT`; 411 seeds remain |
| `P1-00-HIGH-ORIGIN-MATRIX-UNPROVEN` | SUCCESS, policy-snapshot, notification, side-effect-retry | `BLOCKED_EXPLICIT`; 24 cells |
| Telegram-request `UNLINKED-BOT` | `_handle_channel_trade` rejects missing or locally denied users before execute | evidenced in P1-00b |
| `DR-018` | possible prior localhost Redis contact | `NOT_REINSPECTED`; do not read live Redis |
| `DR-015` | deploy/restart/backup/restore duration | already `DEFERRED_BLOCKER` / `DPL-1` |

## Interpretation

- `blocker` prevents the relevant implementation Stage.
- `blocker-implementation` means the design decision is closed but implementation
  evidence is still mandatory before the dependent Stage.
- `blocker-op` prevents provisioning/staging/cutover but not documentation.
- `blocker-codex` is an explicit Codex Final Reviewer decision; Cursor must not
  close it with estimate, fixture or unauthorized rehearsal.
- `deferred-blocker` is a Codex decision that keeps the evidence gap open in a
  named later Stage; it does not authorize estimate, fixture or rehearsal, and
  it does not by itself close `P1-00`.
- `high` requires a named resolution and test before its dependent gate.
- `medium` may be scheduled, but cannot be hidden if it changes parity or
  operational safety.
- `resolved-doc` means only the documentation mismatch is resolved; it does not
  assert the server is production-ready.

No entry is authorization to fix production, delete an artifact or change data.
Resolution must point to an approved Stage, commit, test and Codex Final Review receipt.
