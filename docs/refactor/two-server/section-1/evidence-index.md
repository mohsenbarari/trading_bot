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
| Machine-readable inventory | `inventory/current-finland-runtime.json` | SHA-256 `d6fe17b2387fba087e40fce4a77179a1ca907bdfb97ad2c900e2bc35d53b31af` |
| Task Card | `stages/P1-00.md` | human gate pending |

## Evidence handling

- No database dump, complete environment, secret value, SSH private key, session,
  cookie, Telegram token or user-level parity record is tracked.
- Runtime commands were read-only. The parity comparison status was not posted to
  Redis or any operator endpoint.
- The Git commit containing these files is the integrity boundary for Markdown.
- Future raw test/log artifacts belong under
  `.local/test-results/two-server-refactor/P1-00/<run-id>/` with retention metadata,
  not under `docs/` or `tmp/`.
