# Release-0 candidate selection contract

`core.release0_candidate_selection` is a deliberately small, default-off
selection and proof boundary for the first safe Release-0 foundation. It was
implemented on a clean checkout of baseline
`6091a020b9c66753af135e3a4dcaa919e6bd049d`; the audited checkpoint supplied
only the five reviewed byte digests below. The module never reads the
checkpoint, creates a worktree, invokes Git, copies a file, connects to a
host, transfers an artifact, builds an image, changes DNS, deploys a service,
or grants writer authority.

Only these exact paths and hashes are selectable:

| Group | Path | SHA-256 |
| --- | --- | --- |
| writer-term-safety | `core/application_writer_term.py` | `000cc65c4ef1bf77e68e9f59be4d77b255ce3d26f0d066fbf7a1c191c29b1a6d` |
| writer-term-safety | `core/external_effect_execution_gate.py` | `c4bc72f956a684b9b7063a874f46393b6dc9bbe172c55ff3088e14e8ed57f082` |
| dark-standby-preflight | `core/webapp_ir_dark_snapshot_preflight.py` | `52148650f5d7f1f5c37b0b6724499f0fd17869d121ad053bf18107b4b2b9c926` |
| writer-term-safety | `scripts/preflight_fenced_fi_writer.py` | `009f8ef337d43cb099bc173e888761bbff19700c92ed9166401ad4ec1fd19ba8` |
| dark-standby-preflight | `scripts/preflight_webapp_ir_dark_snapshot_standby.py` | `2976ad7bc07a9964dbfa72b5a02c02d589797a53677c9caf6fe3f37794ce0ed4` |

The canonical selection-profile SHA-256 is
`be29acf7d8d32618b247c7865fd5506c7f9dc51d2e2c78ca3f2d7eb074e75146`.

Every other path is rejected. High-risk families receive explicit denial
codes: Full-Matrix V4, V2R, experimental migrations, retired two-site and
failback activation surfaces, and unresolved review-only writer-authority,
Object Delta, V2, and legacy Full-Matrix surfaces. A similar filename never
adds a file to the candidate.

## What the boundary proves

With explicit enablement and root-controlled, stable, no-follow source
observations, an inventory records the exact five paths, modes, sizes and
hashes in canonical JSON. Before any separate local copier runs, the source
must be re-read and match again. A destination must first prove it is a clean,
stable, root-controlled checkout of the fixed baseline. The module can then
prepare an exact-overlay plan. After an external copier has acted, readback
requires the target's complete changed-path set to equal these five paths and
re-hashes every file.

All inventory, plan and receipt objects have `release_authorized`,
`deployment_authorized`, and `full_matrix_authorized` permanently false. They
are evidence, not a deployment or promotion capability.

## Deliberately remaining integration work

This foundation is not a runnable Release-0. A later, separately reviewed
integration must still resolve the application DB/API/Bot write interception,
the durable Witness-term and writer-admission implementation, identity and
image pinning, migrations, Compose/service composition, dedicated-host
preflight, and the exact local copier plus release-seal gate. None of those
paths may be added by a glob or an ad-hoc copy; each needs a new literal
selection, digest review and focused tests.
