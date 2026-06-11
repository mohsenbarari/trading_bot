# Production Release Readiness Report

- Status: `passed_with_warnings`
- Started at: `2026-06-11T14:18:30.198711Z`
- Finished at: `2026-06-11T14:18:48.465090Z`
- Artifact dir: `tmp/production-benchmark/20260611T141830Z/final-release-p11`
- Required checks: `16/16`
- Required pass rate: `100.0%`
- Warning-adjusted score: `92.0/100`
- Accepted warnings: `4`

## Stage Evidence

| Stage | Status | Evidence |
| --- | --- | --- |
| `P0` Baseline freeze and safety inventory | `passed` | `tmp/production-benchmark/20260611T080030Z/baseline/commands.json`: commands=28, failed=0 |
| `P1` Full-product benchmark harness | `passed` | `tmp/production-benchmark/20260611T083854Z/results.json`: failed_required=0, task_count=11 |
| `P2` PostgreSQL production tuning | `passed` | `tmp/production-benchmark/20260611T092543Z/results.json`: failed_required=0, task_count=3 |
| `P3` Redis durability and queue safety | `passed` | `tmp/production-benchmark/20260611T094159Z/results.json`: failed_required=0, task_count=1 |
| `P4` Nginx/static/media delivery | `passed` | `tmp/production-benchmark/20260611T095946Z/results.json`: failed_required=0, task_count=1 |
| `P5` Worker and pool recalibration | `passed` | `tmp/production-benchmark/20260611T105109Z/results.json`: failed_required=0, task_count=1<br>`tmp/production-benchmark/20260611T105109Z/workers/results.json`: recommendation={'decision': 'keep_baseline', 'reason': 'Higher worker counts did not clear the latency/throughput gates without tradeoffs.', 'recommended_workers': 8, 'rejected_candidates': []}, candidates=3 |
| `P6` Cross-server sync and recovery | `passed` | `tmp/production-benchmark/20260611T113726Z/results.json`: failed_required=0, task_count=3<br>`tmp/production-benchmark/20260611T113726Z/sync-p6/results.json`: status=passed |
| `P7` Trading core, market, and bot workloads | `passed` | `tmp/production-benchmark/20260611T121537Z/results.json`: failed_required=0, task_count=1<br>`tmp/production-benchmark/20260611T121537Z/trading-p7/results.json`: status=passed |
| `P8` Frontend UX beyond Messenger | `passed` | `tmp/production-benchmark/20260611T131305Z/results.json`: failed_required=0, task_count=1<br>`tmp/production-benchmark/20260611T131305Z/frontend-p8/results.json`: status=passed |
| `P9` Observability, audit, and alert readiness | `passed` | `tmp/production-benchmark/20260611T133318Z/results.json`: failed_required=0, task_count=2<br>`tmp/production-benchmark/20260611T133318Z/observability-p9/results.json`: status=passed |
| `P10` Deployment, restart, backup, and rollback | `passed` | `tmp/production-benchmark/20260611T134805Z/results.json`: failed_required=0, task_count=1<br>`tmp/production-benchmark/20260611T134805Z/deployment-p10/results.json`: status=passed |

## Live Gates

- Manifest: `passed`
- Live health/sync: `passed`
- Messenger: `passed`; surfaces `14/14`, blocked `0`
- Observability: `passed`; logging overhead `329.12us` / budget `1000.0us`
- Backup/rollback: `passed`; backup `/srv/trading-bot/backups/p10-deployment-20260611T134805Z.sql`

## Key Benchmark Scores

- Messenger list ready: improved `9/12`, worse `3`, average delta `-66.958`
- Messenger chat ready: improved `12/12`, worse `0`, average delta `-229.758`
- Messenger context menu: improved `1/12`, worse `11`, average delta `152.333`
- Messenger DOM nodes: improved `12/12`, worse `0`, average delta `-181.167`
- Messenger JS heap: improved `7/12`, worse `3`, average delta `-0.25`
- P10 release duration: `474.41s`
- P10 final health phase: `14.0s`

## Accepted Warnings

| Warning | Owner | Release note |
| --- | --- | --- |
| `messenger_context_menu_latency_debt` | Messenger performance | Messenger context-menu latency is accepted as post-release performance debt; chat-ready, DOM, and heap metrics improved broadly and all Messenger surfaces are release-ready. |
| `messenger_raw_performance_artifact_contains_synthetic_tokens` | Release engineering | Do not publish raw tmp/messenger-benchmark/performance-results.json; publish only redacted comparison-summary and surface-status artifacts. |
| `metrics_memory_backend_is_not_aggregate` | Observability/monitoring | Metrics remain process-local under the memory backend; use Loki logs and sync-health samplers for cross-service production diagnosis until an explicit aggregator/exporter is approved. |
| `audit_anchor_sink_not_immutable_retention` | Observability/ops | Audit anchoring has export and shipper paths, but compliance-grade immutable remote retention is deferred until a dedicated external sink is provisioned. |

## Failures

- None
