# Evidence Index

Status: sanitised tracked index; raw runtime output is intentionally untracked
Refresh: executing Assignment `WA-P1-00B-FREEZE-20260904-001` (`base_sha` `40228b073882588e452cde6b0e6cb4cb09e7ffbd`); P1-00 complete commit `a0f22b7d476429974b9d4c8a7ae3d0d5e4a99be9`; historical inventory baseline `aa2c06a573dfd60051e142146f55d7965f2242a7`; P1-00 Assignment `WA-P1-00-SCOPE-20260903-002` (`base_sha` `492d896d67fe9a3625bd5784b795a4029b92b600`)

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
| Runtime inventory | `inventory/current-finland-runtime.json` | SHA-256 `2bd751622099d43f9cba02e423a972e5b198fa9930314cac65524afeebbb31e1` |
| Surface inventory index | `inventory/surface-behavior-inventory.json` | SHA-256 `e4cefff4063f7c4b200a5ae1462e3855d56a8560f9340b613f03ac0dd5d0c002` |
| API surface inventory | `inventory/surface-api.json` | SHA-256 `011f04a924dc3e2aa22fabf2e7f6bc8a40794c24fd9d23233aeadbc5f7f011fc` |
| Bot surface inventory | `inventory/surface-bot.json` | SHA-256 `6833165878539e71610b8cf8cd1d57821b9b08e23aa587a1d64312840a418b92` |
| Web route inventory | `inventory/surface-web.json` | SHA-256 `37b965a4b29050acab829a1018da5889bfd4c2eb8168ea4da1b4963d764f15d8` |
| Job authority inventory | `inventory/surface-jobs.json` | SHA-256 `479a9c13cb265e187394ac345fe2c6e3fbfc02a203f77d883314a2ca43c12b27` |
| Runtime task ownership | `inventory/runtime-task-ownership.json` | SHA-256 `ee6f5fe3e2151e7a8f353b25118637f487553cb40727f0b122acbcb57fdc7bee` |
| Scenario records | `inventory/scenario-records.json` | SHA-256 `cbe29056a24e43b43d828e440d672e0b17f8f1a4e364d4e875c2e238fc059277` |
| Task Card | `stages/P1-00.md` | `COMPLETE` on `a0f22b7d476429974b9d4c8a7ae3d0d5e4a99be9` |
| Task Card | `stages/P1-00b.md` | `IN_PROGRESS` under `WA-P1-00B-FREEZE-20260904-001`; not `COMPLETE` |

## Evidence handling

- No database dump, complete environment, secret value, SSH private key, session,
  cookie, Telegram token or user-level parity record is tracked.
- Static inventories were verified against historical inventory baseline
  `aa2c06a5...` and kept the original `generated_from_commit`. The P1-00
  Assignment `base_sha` is `492d896d...`. The executing leftover-freeze
  Assignment `base_sha` is `40228b07...`. Live host re-inspection was not
  authorized (`external_access: NONE`). Host facts stay `NOT_REOBSERVED`.
- Secret-pattern scan of tracked `section-1` Markdown/JSON: `NO_SECRET_VALUE / DETECTION_SIGNATURES_PRESENT`.
  Detection signatures exist only in characterization tests, not as secret values.
- The Git commit containing these files is the integrity boundary for Markdown.
- Future raw test/log artifacts belong under
  `.local/test-results/two-server-refactor/P1-00b/<run-id>/` with retention metadata,
  not under `docs/` or `tmp/`.
