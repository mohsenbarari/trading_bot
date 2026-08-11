# Stage 5 browser acceptance harness

This directory contains the isolated browser harness for the Customer and Accountant
owner workspaces. It is evidence tooling, not product runtime code.

## Frozen promotable evidence

The accepted Stage 5 browser freeze is run
`uiux-stage5-browser-20260811T100859948Z`. It is bound to implementation commit
`08c5ae1ea95b3087893146547bed8a220eb83d2b`, implementation tree
`96e2f32c46668f37a4753ccaee21216a2b500097`, source binding
`a4555fc55f40541c6f499f4ce5a0e9ddef6f2c9e0cb79d69762a20047d46c938`, and harness
`a183e21df2e34486d555a4d8a662bda1055d6744a34de68543a49574483057d3`.

- Machine-readable freeze manifest: `stage5-final-evidence-manifest.json`
- Acceptance metrics: `runs/uiux-stage5-browser-20260811T100859948Z/stage5-browser-acceptance-metrics.json`
- Final source binding: `runs/uiux-stage5-browser-20260811T100859948Z/stage5-final-source-binding.json`
- Screenshots: 54 SHA-bound PNG files in the same run directory; their canonical
  filename, byte length, and SHA-256 inventory is embedded in the metrics file.

The frozen run is `promotable: true`, covers all six suites in one browser process,
and must remain byte-for-byte immutable. Earlier runs in `runs/` are diagnostic or
failed investigation artifacts and are not closure evidence.

## Safety and source binding

- Browser execution is refused unless `STAGE5_BROWSER_AUTHORIZATION` exactly equals
  `STAGE5 CUSTOMER ACCOUNTANT SOURCE FINAL — RUN`.
- Execution also requires the exact source SHA and Git commit printed by the harness.
- The harness requires the complete nonignored worktree to be clean, the branch to be
  `condidate/webapp-ui-ux-redesign-v2`, and the source/harness/environment snapshots
  to remain byte-identical before and after the browser closes.
- Vite binds to an ephemeral `127.0.0.1` port with an isolated temporary cache that
  is removed during teardown. External requests are intercepted and blocked. Service
  workers and real realtime transport are replaced by deterministic in-browser fakes.
- A suite-filtered run is diagnostic and is never promotable. Do not copy or rename
  a diagnostic run as final evidence.
- Before the implementation commit exists, a dirty-tree focused diagnostic may be
  run only with both `STAGE5_BROWSER_DIAGNOSTIC=1` and `STAGE5_BROWSER_ONLY=<suite>`.
  It still requires the exact authorization, live source SHA, and current commit,
  and its metrics are permanently marked non-promotable.

## Operator sequence

1. Wait until all Stage 5 product edits are committed and the nonignored worktree is clean.
2. Print the live binding without starting a browser:

   ```sh
   node docs/uiux-stage5-customer-accountant-workspaces/assets/browser-evidence/stage5-browser-acceptance-harness.mjs --print-source-binding
   ```

3. Copy the printed `sourceBindingSha256` and `commit` values exactly, then run:

   ```sh
   STAGE5_BROWSER_AUTHORIZATION='STAGE5 CUSTOMER ACCOUNTANT SOURCE FINAL — RUN' \
   STAGE5_EXPECTED_SOURCE_SHA256='<printed sourceBindingSha256>' \
   STAGE5_EXPECTED_COMMIT='<printed commit>' \
   node docs/uiux-stage5-customer-accountant-workspaces/assets/browser-evidence/stage5-browser-acceptance-harness.mjs
   ```

The harness writes screenshots, `stage5-browser-acceptance-metrics.json`, and a source
binding artifact beneath `runs/<run-id>/`. Only a full run whose metrics contain
`"promotable": true` is eligible for closure evidence.

The `runs/` tree can contain failed or diagnostic artifacts from investigation. Do
not force-add the directory wholesale; select only the one promotable run after its
binding and metrics have been reviewed.

For focused diagnostics, set `STAGE5_BROWSER_ONLY` to one of `responsive`, `customer`,
`accountant`, `history`, `create-busy`, or `accessibility`. Focused runs remain
non-promotable even when every selected assertion passes.
