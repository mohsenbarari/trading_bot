# Physical release-candidate materializer contract

`core.physical_release_candidate_materializer` is the narrow, root-only,
default-off successor to the inventory boundary. It contains no concrete Git,
worktree, filesystem-copy, network, Object Storage, SSH, Docker, image-build,
release-seal, deployment, promotion, or Full-Matrix implementation. This
change does not invoke it against a worktree.

It accepts only a canonical, parsed `physical_release_candidate_inventory`
object whose literal reviewed selection and every SHA-256 still verify against
the source. The source configuration must exactly match the inventory's
captured dirty/clean state. A target must be a distinct, independently
inspected root-controlled clean checkout of fixed commit
`6091a020b9c66753af135e3a4dcaa919e6bd049d` and its fixed tree before any
transfer adapter is called.

The required injected adapters are all local-only boundaries:

- a source Git inspector and no-follow source reader to re-verify the frozen
  inventory;
- a distinct target Git inspector to prove the clean baseline and bind it to
  the transfer;
- a writer-quiescence observer; and
- an opaque, already-verified writer-quiescence receipt signed by a
  root-pinned Ed25519 authority;
- an atomic no-follow file-transfer adapter; and
- independent target reader and complete target-Git-overlay inspector.

The module does not manufacture any adapter or endpoint. Production adapter
review must reject remote shell/copy paths, direct FI→IR transport, recursive
copy, globbing, `git add -A`, symlink following, and a transfer implementation
that cannot atomically commit the entire overlay.

## Required quiescent sequence

Use this boundary only after a separate root-owned writer fence has put the
source into an actual quiescent window:

1. Pause all source writers and active source-changing agents; obtain a fresh
   local observation with `writers_active: false`, `source_stable: true`, and
   `writer_lease_state: quiesced-no-writers`.
2. Have the separate root-owned writer fence issue a short-lived, canonical
   signed receipt. It must bind the configured source-root-policy digest, the
   frozen inventory manifest SHA-256, quiescence generation SHA-256, evidence
   SHA-256, writer-lease ID/state, expiry, and exactly the root-pinned
   authority identity. The materializer accepts only the opaque result of its
   local verifier; it never derives quiescence from Git state, mtime,
   filesystem shape, or an unsigned caller object.
3. Freeze/re-verify the reviewed inventory from that source. For a dirty
   staging inventory, the explicit dirty-source flag must remain exactly the
   same; it is not a release permit.
4. Create and independently inspect a separate clean baseline target outside
   this module. The materializer rejects source/target aliases and a shared
   source/target Git inspector.
5. Invoke the materializer with only the reviewed inventory and injected
   local adapters. It consumes the signed receipt before any target action and
   again after transfer. The transfer is bound to the quiescence generation,
   the source evidence hash, and a clean-target baseline binding.
6. Independently rehash every target path, inspect the complete target delta,
   and keep both quiescence generation and evidence hash unchanged through the
   operation.

The atomic adapter must use no-follow source reads and target writes, stage
only the literal manifest paths in a private transaction, and make either the
whole overlay visible or none of it. It returns the exact materialized-path
tuple; the materializer rejects any omitted, reordered, traversal, symlink, or
extra path. The independent target overlay inspector must report both tracked
and untracked changes. Any changed path outside the manifest fails closed.

## Refusals and limits

Before the transfer is called, the module refuses disabled/non-root use,
noncanonical or changed inventory, incorrect baseline, dirty/unsafe target,
source instability, active writers, stale or changed quiescence generation,
missing, expired, wrong-root, wrong-inventory, wrong-generation, forged, or
unverifiable signed quiescence receipt; conflated adapters; aliases; and
source/target path traversal. After a
transfer observation it refuses non-atomic/no-follow claims, unexpected paths,
target hash/mode/type/ownership mismatch, symlinks, incomplete target-delta
observation, a target commit, a release seal, or any extra target delta.

If a quiescence proof changes after an adapter reports a transfer, the
materializer emits no success receipt. The target must then be treated as an
untrusted candidate and reconciled manually; this boundary never retries or
silently continues it.

Success produces only canonical redacted evidence: hashes, counts, booleans,
and timestamp. It contains no source/target paths, bytes, credentials,
endpoints, raw adapter output, or authority. The receipt hard-codes
`target_git_commit_created`, `release_seal_created`, image-build authorization,
release authorization, and execution authorization to `false`.

The resulting target remains an uncommitted candidate. Focused tests, secret
scanning, intentional normal Git review/commit, and the separate
`physical_release_seal_admission` gate remain required before any image build,
deployment, promotion, or Full-Matrix readiness use. This controller tooling
is deliberately not added to the runtime release-path allowlist.
