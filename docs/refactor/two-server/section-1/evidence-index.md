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
| API surface inventory | `inventory/surface-api.json` | SHA-256 `0d35861a158e71bf1695cf6f8e8cd3b480c677211ad3686941994d1c24099dc3` |
| Bot surface inventory | `inventory/surface-bot.json` | SHA-256 `17fe5cbafc318d1037e7675d1a688361efe8d88b09ef57d5da424364c8761ebc` |
| Web route inventory | `inventory/surface-web.json` | SHA-256 `92a6fbafbfc9bac42bf169261bd447f50676005d0326cf9c497120829a3c754e` |
| Job authority inventory | `inventory/surface-jobs.json` | SHA-256 `7be5d42699e89df1b9398caaf0d8853c4d797863f6050d0b46e3d9beb95d9327` |
| Runtime task ownership | `inventory/runtime-task-ownership.json` | SHA-256 `071e273e5168db736bf9330aab93233adcd32cd03a7033b89e5e17d84f47ef14` |
| Scenario records | `inventory/scenario-records.json` | SHA-256 `500701e135c9c4151dc54e20c459465edea69eac10ee28e4559d9d6111d7b31e` |
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
