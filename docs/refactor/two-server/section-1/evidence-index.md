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
| Runtime inventory | `inventory/current-finland-runtime.json` | SHA-256 `598e56c602a101cb61168a1468b0a7a7bebae50da14458298214e91dcaa12ce6` |
| Surface inventory index | `inventory/surface-behavior-inventory.json` | SHA-256 `be55cc0809eaa73de42d0562433d03d1274ed3f82a8e58e296773911e63fa8c7` |
| API surface inventory | `inventory/surface-api.json` | SHA-256 `764de4d1fbf549dbc691b459cf138c728813356283a76cd6c07a8f46e8d8b17e` |
| Bot surface inventory | `inventory/surface-bot.json` | SHA-256 `b4251f52cb14ac61205394346214766db3c429f175014aed2e3204720e1a7ecb` |
| Web route inventory | `inventory/surface-web.json` | SHA-256 `bda6f6eb401b8213d8ca494caf5a3a1d987f970e03204eae92b59ed2a218d2b1` |
| Job authority inventory | `inventory/surface-jobs.json` | SHA-256 `35dda75326daa4e2bb38a8d09b7164df447dcd07493049afeb3316d12239732b` |
| Runtime task ownership | `inventory/runtime-task-ownership.json` | SHA-256 `071e273e5168db736bf9330aab93233adcd32cd03a7033b89e5e17d84f47ef14` |
| Scenario records | `inventory/scenario-records.json` | SHA-256 `9ea5ed5d2780f4053ba04c0d177d84e5f3885ffeda2d1deeafa7ae89df24298b` |
| Task Card | `stages/P1-00.md` | correction candidate after `CHANGES_REQUIRED`; Codex Final Review pending |

## Evidence handling

- No database dump, complete environment, secret value, SSH private key, session,
  cookie, Telegram token or user-level parity record is tracked.
- This refresh verified static inventories against the Assignment `base_sha`
  and kept the original `generated_from_commit`. Live host re-inspection was
  not authorized (`external_access: NONE`).
- Secret-pattern scan of tracked `section-1` Markdown/JSON: `NO_SECRET_VALUE / DETECTION_SIGNATURES_PRESENT`.
  Detection signatures exist only in characterization tests, not as secret values.
- The Git commit containing these files is the integrity boundary for Markdown.
- Future raw test/log artifacts belong under
  `.local/test-results/two-server-refactor/P1-00/<run-id>/` with retention metadata,
  not under `docs/` or `tmp/`.
