# Evidence Index

Status: sanitised tracked index; raw runtime output is intentionally untracked
Refresh: `WA-P1-00-REFRESH-20260903-001` against `aa2c06a573dfd60051e142146f55d7965f2242a7`

| Evidence | Path | Integrity/status |
| --- | --- | --- |
| Stage ledger | `00-stage-ledger.md` | tracked Markdown; final commit is integrity boundary |
| Current architecture | `01-current-finland-architecture.md` | tracked Markdown; live host facts `NOT_REOBSERVED` |
| Runtime inventory | `02-runtime-inventory.md` | tracked Markdown; live host facts `NOT_REOBSERVED` |
| Dataflow/ownership | `03-dataflow-and-ownership.md` | tracked Markdown |
| Surface policy | `04-surface-policy-matrix.md` | tracked Markdown |
| Feature parity | `05-feature-parity-contract.md` | tracked Markdown |
| Drift register | `06-current-drift-register.md` | tracked Markdown |
| Cleanup manifest | `07-repository-cleanup-manifest.md` | tracked Markdown |
| Runtime inventory | `inventory/current-finland-runtime.json` | SHA-256 `8c89cee8d8546dadaf80ccab7d1e6fbbe3ed3b60d5994e2735311a8c4e233d5f` |
| Surface inventory index | `inventory/surface-behavior-inventory.json` | SHA-256 `f8e8c5137407a32db62ce40dbfd66e5967a5405d31eb6d4886e98b8b96bcb0c3` |
| API surface inventory | `inventory/surface-api.json` | SHA-256 `024f1404d4baf7575c95a4925e10d8856306750e18f8e25ae3ce3ff27f9116f2` |
| Bot surface inventory | `inventory/surface-bot.json` | SHA-256 `0b9cd333f0780096f7edb8edd5f9ab03115d533145b189d7fdbb3e005b8d76cf` |
| Web route inventory | `inventory/surface-web.json` | SHA-256 `b0c1c0b31c892c29aa93c4dedcd7d4bef5bd3b023df2223c212485a1e3940ff8` |
| Job authority inventory | `inventory/surface-jobs.json` | SHA-256 `90864b780f51e46eb83fdaad69b0ee06c00639c3c172fc468eb1146229aa50ab` |
| Runtime task ownership | `inventory/runtime-task-ownership.json` | SHA-256 `59b71c6d9d8d3ad28f498538befa3ef2476ec92cea5e5b361b541192d278943c` |
| Scenario records | `inventory/scenario-records.json` | SHA-256 `667698e80254861ad233c7470a862018ada432f487bba3f5e089e90c7214cf9d` |
| Task Card | `stages/P1-00.md` | evidence refresh candidate; Codex Final Review pending |

## Evidence handling

- No database dump, complete environment, secret value, SSH private key, session,
  cookie, Telegram token or user-level parity record is tracked.
- This refresh regenerated static inventories from repository code at the
  Assignment `base_sha`. Live host re-inspection was not authorized
  (`external_access: NONE`).
- The Git commit containing these files is the integrity boundary for Markdown.
- Future raw test/log artifacts belong under
  `.local/test-results/two-server-refactor/P1-00/<run-id>/` with retention metadata,
  not under `docs/` or `tmp/`.
