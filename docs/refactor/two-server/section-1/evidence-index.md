# Evidence Index

Status: sanitised tracked index; raw runtime output is intentionally untracked

| Evidence | Path | Integrity/status |
| --- | --- | --- |
| Stage ledger | `00-stage-ledger.md` | tracked Markdown; final commit is integrity boundary |
| Current architecture | `01-current-finland-architecture.md` | tracked Markdown |
| Runtime inventory | `02-runtime-inventory.md` | tracked Markdown |
| Dataflow/ownership | `03-dataflow-and-ownership.md` | tracked Markdown |
| Surface policy | `04-surface-policy-matrix.md` | tracked Markdown |
| Feature parity | `05-feature-parity-contract.md` | tracked Markdown |
| Drift register | `06-current-drift-register.md` | tracked Markdown |
| Cleanup manifest | `07-repository-cleanup-manifest.md` | tracked Markdown |
| Runtime inventory | `inventory/current-finland-runtime.json` | SHA-256 `4dc0758b51a57cda8aa71214e889454c89540f6e1647523952b0a671486c2294` |
| Surface inventory index | `inventory/surface-behavior-inventory.json` | SHA-256 `633ec1ba4b0397ec97e89e02db4de9e506eaa47c3cef1798d7fc5781bb6899ee` |
| API surface inventory | `inventory/surface-api.json` | SHA-256 `da972232191c71cc7668ddafc4a3f1cfb94f647f99b5b605f72f09ffc441a89d` |
| Bot surface inventory | `inventory/surface-bot.json` | SHA-256 `ecab4ea012197e307758d9b4399de911d122832aef58d3b1dc77b045d2172a75` |
| Web route inventory | `inventory/surface-web.json` | SHA-256 `c3a13651720969c7920ad695b0cbc6fb8ff3b520ec46c2c4ab4cf57ffe646758` |
| Job authority inventory | `inventory/surface-jobs.json` | SHA-256 `a324637d0c2d62ca0dcb3243414144b286ec08d8089052b659dacb4dd7781645` |
| Runtime task ownership | `inventory/runtime-task-ownership.json` | SHA-256 `3290bc262460b682d35badd21efca59f0ca4f0c8629e13c295b3e55df521e7ac` |
| Task Card | `stages/P1-00.md` | evidence closure and Codex Final Review pending |

## Evidence handling

- No database dump, complete environment, secret value, SSH private key, session,
  cookie, Telegram token or user-level parity record is tracked.
- Runtime commands were read-only. The parity comparison status was not posted to
  Redis or any operator endpoint.
- The Git commit containing these files is the integrity boundary for Markdown.
- Future raw test/log artifacts belong under
  `.local/test-results/two-server-refactor/P1-00/<run-id>/` with retention metadata,
  not under `docs/` or `tmp/`.
