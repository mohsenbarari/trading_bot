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
| `DR-013` | high | all 217 FastAPI decorators, 203 Bot handlers, 30 Web routes and 15 authority jobs have unique family seeds, but 1 API and 5 Bot items lack direct static test evidence and scenario variants remain open | manually link evidence or add characterization; complete persona/tier/time/failure records before implementation stages |
| `DR-014` | high | Market archive is ~3.95 GiB with million-row outbox/revision/fact tables and no approved consolidation retention/replay contract | design Market migration separately from app DB merge |
| `DR-015` | medium | no observed deploy/restore timing baseline was produced because this audit was read-only | collect in controlled rehearsal; do not estimate it from script length |
| `DR-016` | high | current child pollers/workers/Market processes/timers are inventoried, but exact target compose/image/credential/readiness binding is not designed | close in `P1-03`; any unknown or overlapping owner blocks activation |
| `DR-017` | low | incremental `src/interfaces/http_api/routers/user_router.py` declares four routes but `main.py` does not mount it | retain as dormant code until `P1-01` reference/cleanup review; never count it as a live API contract |

## Interpretation

- `blocker` prevents the relevant implementation Stage.
- `blocker-implementation` means the design decision is closed but implementation
  evidence is still mandatory before the dependent Stage.
- `blocker-op` prevents provisioning/staging/cutover but not documentation.
- `high` requires a named resolution and test before its dependent gate.
- `medium` may be scheduled, but cannot be hidden if it changes parity or
  operational safety.
- `resolved-doc` means only the documentation mismatch is resolved; it does not
  assert the server is production-ready.

No entry is authorization to fix production, delete an artifact or change data.
Resolution must point to an approved Stage, commit, test and human gate receipt.
