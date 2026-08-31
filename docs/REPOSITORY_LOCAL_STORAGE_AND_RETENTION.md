# Repository-local storage and retention policy

Status: initial policy for the repository cleanup/refactor
Owner: project owner
Last verified: 2026-08-31

## Canonical local root

The only Git working tree is the project root. Reproducible working copies,
virtual environments, test output, logs, generated packages, and temporary
artifacts must not be kept as sibling project directories.

New host-local artifacts belong under the ignored `.local/` root:

```text
.local/
  logs/
  test-results/
  deploy/releases/
  backups/staging/
  data/telegram/raw/
  data/telegram/normalized/
  caches/
  quarantine/
```

Live databases, Docker volumes, credentials, and the only durable backup copy
must not be placed in Git. A local backup under `.local/backups/staging/` is only
a short-lived transfer/restore artifact; a durable backup needs a separate,
access-controlled failure domain.

## Retention classes

| Class | Default retention | Space rule | Deletion guard |
| --- | --- | --- | --- |
| Application/operation logs | 14 days | rotate at 100 MiB per file; compress closed files | never log secrets; keep an active incident window |
| Local test results | 7 days | maximum 2 GiB total | retain an explicitly referenced failing run until closure |
| Deploy artifacts | last 2 successful releases; failed runs 7 days | keep one known-good rollback release | never delete the active or rollback release |
| Local backup staging | 7 days after off-host verification | maximum 2 complete local sets | never delete the last verified restorable/off-host copy |
| Quarantine | 7 days | no automatic extension | restore only with an identified owner and active reference |
| Telegram raw events | 14 days hot; up to 90 days compressed when replay is required | partition, deduplicate, and compress by source/day | retain events referenced by an open incident or unreconciled replay |
| Telegram normalized facts | product/data policy, not raw-log policy | deduplicate by stable event identity | delete only through an approved data migration |
| Dependency/build caches | current lock/digest plus one previous, or 30 days | rebuildable; remove first under disk pressure | never remove an artifact required by the active offline deploy |
| Offline map data | current version plus one verified rollback version | immutable versioned files | verify the active tileserver reference before deletion |

## Cleanup scenarios

### Routine cleanup

1. Produce a dry-run inventory with path, class, size, age, and reason.
2. Exclude active releases, open incidents, current locks, and referenced data.
3. Delete expired items only inside an approved retention root.
4. Record reclaimed bytes and failed deletions without logging content.

### Disk pressure

1. Stop generation of nonessential evidence.
2. Remove expired test output and caches first.
3. Remove failed deploy artifacts older than seven days.
4. Compress eligible Telegram raw partitions.
5. Never remove the active release, last rollback release, or last restorable backup.

### Telegram ingestion growth

1. Store raw input by source and UTC day.
2. Deduplicate by stable source/event identity before normalization.
3. Mark replay/high-water checkpoints independently from file age.
4. Compress closed daily partitions.
5. Remove raw partitions after their replay window only when no incident or gap
   references them; normalized facts follow their own data lifecycle.

### Backup expiry

1. Verify a newer off-host backup and restore receipt.
2. Keep the most recent known-good recovery point.
3. Delete expired local staging copies.
4. Apply daily/weekly/monthly retention to the durable store; backups are not
   permanent merely because they were once used for a release gate.

## Required implementation follow-up

- Route host-side logs and test reports to `.local/`.
- Move deploy artifacts from `tmp/production-release` only after deploy scripts
  accept the new path and rollback tests pass.
- Add a dry-run-first cleanup command and a scheduled timer with a disk high-water
  alert; no cleanup job may follow arbitrary symlinks or delete outside declared roots.
- Add retention metrics for bytes, oldest item, last cleanup, last verified backup,
  Telegram raw backlog, and current rollback release.

## Initial cleanup baseline — 2026-08-31

- Only `/root/trading-bot/trading_bot` remains as a working tree under
  `/root/trading-bot`; sibling worktrees, the external virtualenv, old coverage
  archive, and empty backup directories were removed.
- `tmp/production-release` remains about 1.2 GiB because it contains the current
  release bundle and rollback evidence. It is the first deploy path to migrate.
- `map_data` remains about 616 MiB because Compose serves the active offline map.
- `frontend/node_modules` remains about 571 MiB as the current lockfile cache;
  it is rebuildable and should be removed first when not actively developing.
- `pip_packages` remains about 55 MiB because offline deployment currently
  depends on its wheelhouse.
- Tracked documentation is about 44 MiB: roughly 27.8 MiB PNG and 9.2 MiB JSON.
  UI/UX raw screenshots, browser metrics, lint logs, and duplicate execution
  receipts require a manifest-level review before moving large evidence out of
  Git; current protected-surface evidence must not be deleted blindly.
- No growing raw Telegram event directory was found in the current checkout.
  Existing estimator runtime review artifacts are below 300 KiB. The new raw
  event layout and retention gate must exist before new ingestion is enabled.
- Cleanup quarantine expires on 2026-09-07. The archived Offer Overtime input
  expires after canonical contract extraction, targeted for 2026-09-30.
