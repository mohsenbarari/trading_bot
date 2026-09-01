# Repository and Runtime Cleanup Manifest

Status: inventory/proposal only — no deletion authorized

The manifest deliberately separates discover/classify from execution. Sizes are
point-in-time observations; every execution Task Card must re-resolve an exact,
root-bounded path and re-check references, locks and protection metadata.

| Path/class | Tracked | Size/state | Proposed action | Required proof / retention |
| --- | --- | --- | --- | --- |
| repository worktree | mixed | one canonical worktree | `KEEP` | remain clean; no extra worktree |
| `candidate/wa-ir-standby-v1` | Git branch | local-only candidate | `KEEP` | owner explicitly deferred deletion; never push/merge as source |
| `tmp/production-release/` | ignored | ~1.2 GiB / 6,106 files | `QUARANTINE` then `DELETE` candidate | prove no active release/rollback reference; set expiry and reversible quarantine receipt |
| `frontend/node_modules/` | ignored | ~571 MiB | `KEEP` as reproducible cache or `DELETE` under cache quota | lockfile install must reproduce it; never treat as source/backup |
| `mutants/` | ignored | ~22 MiB | `DELETE` candidate | test tooling/reference scan; results needed for an open review move to `.local/test-results` first |
| `.local/` | ignored | ~4.4 MiB | `KEEP` canonical root, reorganize by policy | owner, quota and expiry for logs/test/deploy/backups/data/caches/quarantine |
| tracked `docs/**` evidence | tracked | many screenshots/JSON reports; largest files ~1.8 MiB | `BLOCKED` pending reference audit | retain decision evidence; move disposable generated output only with link/history plan |
| Bot host `/srv/trading-bot/backups/` | external | ~2.2 GiB | `BLOCKED` pending backup manifest | preserve latest restore-tested backup, active rollback and incident/legal holds; expiry required for all others |
| Bot host release directories | external | ~760 MiB observed | `QUARANTINE` old releases | always preserve active + last known rollback; verify digest/reference before expiry |
| production-host staging containers/volumes | external | active for days/weeks | `BLOCKED` pending traffic/dependency audit | separate drain/stop/delete approvals; snapshot/restore where data-bearing |
| Web host obsolete three-site stack | external | active for about five weeks | `BLOCKED` pending ownership audit | prove zero traffic/write/scheduler/credential use before decommission |
| Market staging-shadow paths | external | production consumers observed | `MOVE` only in later migration | content hash, consumer map, replay test and rollback mount required |

## Retention contract to approve in `P1-01`

Every backup, release, log, test result, raw Telegram partition and quarantine
entry must carry:

```text
created_at, owner, purpose, source_commit_or_release, data_class,
restore_or_replay_status, protected_until, expires_at, size, checksum
```

Minimum safety rules:

1. active release, last verified rollback and latest restore-tested backup cannot
   be selected by cleanup;
2. raw Telegram/Market data is partitioned by source/date, deduplicated by stable
   event ID, compressed after its hot window and kept only for an approved
   replay/audit/model-training purpose;
3. logs have size/time rotation and incident holds; “keep forever” is not a
   default retention value;
4. quarantine is recoverable and expiring, not a permanent second trash pile;
5. cleanup is allowlist/root-bound, symlink-safe, lock-aware, dry-run first and
   idempotent;
6. any target not present in the human-approved manifest requires a new gate.

## Execution order after approval

1. add metadata/retention tooling and test it against fixtures;
2. re-scan exact paths and classify references/locks/protection;
3. move approved items to bounded quarantine, recording before/after size and
   recovery command;
4. run repository/reference checks and relevant tests;
5. wait through the approved protection window;
6. permanently delete only expired, unreferenced manifest entries with a second
   receipt;
7. verify a second cleanup run makes no additional change.

This file is not an `rm` list. It is the input to the owner review required before
`P1-01` can execute.
