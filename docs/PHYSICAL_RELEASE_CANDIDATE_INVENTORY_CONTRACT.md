# Physical release-candidate inventory contract

`core.physical_release_candidate_inventory` is the deliberately narrow bridge
between the active dirty engineering worktree and a later clean physical
release candidate.  It is disabled by default and performs no Git write,
worktree creation, copy, archive, image build, network operation, deployment,
or Full-Matrix action.

It is anchored to baseline commit `6091a020b9c66753af135e3a4dcaa919e6bd049d`
and its exact Git tree.  It holds a literal, grouped list of reviewed runtime
paths for writer fencing, object-delta data plane, Object Storage/WAL,
PostgreSQL, dedicated-host preflight, the separately named V4 Witness
execution boundary, and source-stage integration.  It never uses a directory
glob or `git add -A`.

The V4 boundary is its own literal allow-list.  Every direct `core` import of
an active V4 module must already be reviewed in the same candidate, and none
may point at a retired paired-credential or V1 activation runtime.  The
reviewed narrow Witness adapter and root-owned immutable ledger are explicitly
selected only after their focused restart, replay, rollback, and identity-pin
tests passed; filename similarity never promotes a future module into a
candidate.

The V4 allow-list also has an explicit legacy-execution deny-list.  The old
two-server fence, historical campaign-readiness aggregate, V1 execution
driver/journal, and V3 driver may remain in the repository for containment,
but an active V4 module may not import them merely because their files happen
to be in another reviewed group.

When a root-controlled future adapter is explicitly enabled, the inventory
reads only those literal regular files.  It records each path's group,
`100644`/`100755` mode, byte size, and SHA-256 in canonical JSON.  Symlinks,
path traversal, non-root ownership, unsafe modes, unknown/missing paths,
changed files, changed source observations, wrong baseline identities, and
noncanonical or incomplete manifests fail closed.

The default mode requires a clean source.  A dirty source can be inventoried
only with explicit `allow_dirty_staging_source=True`; that produces status
`draft-unsealed-staging-inventory-not-materialized` and all authorization fields are
hard-coded false.  This explicit staging exception exists only to freeze a
reviewed allowlist; it does not make the current worktree a release.

Before any later copier is allowed to run, it must separately call
`verify_clean_physical_release_candidate_base` on a distinct root-controlled,
clean checkout of the fixed baseline.  The copier must transfer exactly the
manifested bytes, re-check their hashes/modes, run focused tests and secret
scanning, commit the result, and then pass the independent
`physical_release_seal_admission` gate.  This module intentionally cannot do
those actions itself.

Tests, docs, ignored runtime material, secrets, `.env*`, `/tmp/`, historical
release artifacts, and unrelated working-tree files are intentionally outside
the runtime selection.  They must never be swept into Git merely because they
coexist with the reviewed paths.
