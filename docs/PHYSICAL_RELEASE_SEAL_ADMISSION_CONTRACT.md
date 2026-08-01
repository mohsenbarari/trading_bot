# Physical release-seal admission contract

`core.physical_release_seal_admission` is the local-only gate for making a
fresh source/image descriptor usable as input to the WA-IR bootstrap bundle
preparation path. It does not package source, archive a Git repository,
build/pull/push/load images, contact Docker, SSH, Object Storage, a registry,
or a remote host. It performs no deployment, publish, route, Writer, or
Full-Matrix action.

The gate is disabled by default and requires a root runtime. Its Git and
filesystem interactions are injected interfaces, so a future host adapter can
perform the fixed local read-only inspection while unit tests never touch a
real worktree.

## Clean root-controlled source requirement

The caller supplies one absolute worktree path and one exact 40-character
release SHA. The injected filesystem observation must prove all of these
before Git is queried:

- the worktree and its `.git` metadata are real directories, root-owned,
  non-symlinked, below root-controlled ancestors, and not group/world
  writable;
- the only accepted Git executable is `/usr/bin/git`, a root-owned,
  non-symlinked, non-group/world-writable executable below root-controlled
  ancestors.

The only Git requests emitted are fixed local observations:

1. `HEAD^{commit}` before inspection;
2. porcelain status with `--untracked-files=all --ignored=matching`;
3. the exact commit's tree ID and recursive NUL-delimited tree listing;
4. the same status and `HEAD^{commit}` a second time;
5. the same filesystem observation a second time.

Any changed/staged/deleted/untracked **or ignored** path is rejected. The
second HEAD and filesystem observations reject an unstable worktree. No
ignored-file exception exists for a physical release seal.

The NUL-delimited Git tree is reduced to a canonical sorted list of safe,
ordinary tracked blob paths (`100644` or `100755` only). Symlinks,
submodules, non-ASCII/unsafe paths, duplicate paths, malformed entries, and
an empty tree fail closed. `tracked_tree_sha256` is the SHA-256 of that
canonical list, not a mutable filesystem walk.

## Complete immutable image set

An explicit digest-pinned `@sha256:` reference is mandatory for every
physical role:

```text
webapp_fi_app
webapp_ir_app
bot_fi
postgres_15
redis_7
witness
```

Several roles may deliberately use the same digest, but all six role entries
must be present exactly once. A tag-only ref, an image path with a mutable
tag, a missing ref, duplicate role, foreign role, or incomplete set is
rejected before the worktree is inspected. The gate checks reference syntax
and canonical image-set identity only; it does not claim a local or remote
image exists.

## Canonical non-authorizing descriptor

A successful admission returns opaque `SealedPhysicalReleaseDescriptor` and
canonical newline-terminated JSON with only public metadata:

- campaign, exact release SHA, and matching control release SHA;
- Git tree ID and canonical tracked-tree digest;
- explicit role/image list and `image_set_sha256`;
- `release_bundle_sha256`, derived from the exact Git tree identity;
- `release_provenance_sha256`, derived from source/image/campaign identity;
- a caller-supplied nonzero UUID and fresh UTC seal time.

For compatibility with the WA-IR bootstrap binding, `release_bundle_sha256`
names the canonical **source-tree release identity**. It is not a claim that
this gate created or verified a tarball, Git bundle, encrypted archive, or
Object Storage object. A later package stage must independently bind any
actual artifact bytes to this descriptor.

The descriptor has hard-coded `direct_fi_to_ir_control: false` and all of
`publish_authorized`, `deployment_authorized`, and `execution_authorized` set
to `false`. It cannot be used as a launch or Full-Matrix authorization.

Its parser rejects duplicate fields, non-ASCII/noncanonical JSON, missing or
extra fields, malformed timestamps/UUIDs, stale seals, changed hashes, or any
inconsistent source/image/release binding.

## WA-IR bootstrap handoff

`project_physical_release_seal_for_wa_ir_bootstrap` rechecks the opaque
descriptor and returns a raw `WaIrBootstrapExactReleaseBinding` containing
the exact fields required by the downstream builder:

```text
campaign_id
release_sha
control_release_sha
release_bundle_sha256
image_set_sha256
release_provenance_sha256
source_site=webapp_fi
destination_site=webapp_ir
seal_id
sealed_at
```

It does not call the downstream `seal_wa_ir_bootstrap_exact_release_binding`
function itself, publish any artifact, or contact WA-IR. The downstream fresh
sealed binding and encrypted private Object-Storage bootstrap artifact remain
separate gates.
