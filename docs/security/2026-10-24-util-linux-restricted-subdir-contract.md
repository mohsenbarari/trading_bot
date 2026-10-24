# Independent restricted-subdirectory source contract

This source packet has not compiled or executed a program locally or in CI.
Its hermetic tests use inert source/archive data and named CI-command mocks.
The existing `source-tests.json` reconciliation remains OPEN and unchanged.
The proposed result is a separate source-contract receipt, not a waiver, raw
scanner change, production action, or claim about all libmount vulnerabilities.

The [upstream CVE-2026-78409 advisory](https://github.com/util-linux/util-linux/security/advisories/GHSA-8f2p-47x3-43mv)
identifies the restricted-user detached-tree fast path introduced in >=2.42.
The actual pinned 2.41.6 archive's `hook_mount_pre` instead returns `-ENOTSUP`
when the real `mnt_context_target_fd_required` returns restricted status.
The proof extracts those two real functions and `mnt_context_is_restricted`
directly from the archive, without rewriting their positive-control bodies.

All five offline inputs have the same size/SHA pins as the existing build:
the official 2.41.6 source archive, three upstream follow-up patches, and the
Trixie Debian packaging archive. All three follow-ups and all nine active
Debian patches are checked for their actual affected file paths. None changes
`libmount/src/hook_subdir.c` or `libmount/src/context.c`. The two already
integrated Debian patches remain excluded exactly as in the historical recipe.
No patch application or vendor rebuild is needed for this independent check.

Actual source-member hashes from the pinned archive are:

| Member | SHA256 |
| --- | --- |
| libmount/src/hook_subdir.c | 5a832d08ed196100f1f53f4badb2b9c09fe79f371c9b1bcf5ee273d852c302bc |
| libmount/src/context.c | 77c2ba367a3c21dea0e73d5a76448780fb8e69726547ffec763beb522426a4a7 |

The helper also requires the exact existing build receipt SHA256
`3a799ad92549c9d3c5e18013463743d9bc2b721eb83557e5ee3a0273d4514dde`
and libmount1 DEB SHA256
`111baa6313bd1e72002d99725bf2f82e07d8dafd282269e005d5600aa616aaca`
(210776 bytes). It checks the receipt's input mapping, package row and existing
OPEN status. The historical receipt reports maintainer-signature authentication
false; this packet validates fixed hashes and does not upgrade that assertion.
This connects the source contract to the already compiled package lineage,
without executing or loading that package's code.

Harmless stubs replace all namespace/filesystem effects. A restricted context
must return `-ENOTSUP` before even a stub effect or allocation. A no-subdirectory
context must remain a no-op, and an unrestricted context must reach the stubs,
confirming the harness is not an unconditional success check. A controlled
negative variant removes only the real refusal block and must compile normally
then exit exactly10 at the restricted assertion. A compilation error, crash,
timeout, or a different exit is failure. Both mountfd macro settings are checked;
this is not a full build of either libmount variant or a kernel regression.
No privileged mounts, namespace changes, syscall reproducer, malformed path,
procfs escape, credential change, or package binary execution occurs.

## Existing-workflow opt-in, source only

The existing `trixie-util-linux-build.yml` now has the boolean input
`verify_restricted_subdir`, default false. It is permitted only with
`reuse_verified_packages: true`; an invalid combination fails before recipe
sealing, downloads or Docker work. No workflow has been dispatched with it yet.
The four sealed vendor-recipe files and old package/receipt artifact stay
unchanged. The added step follows successful reuse and real installation; it
does not rebuild util-linux or substitute a source-contract result for those
existing installation/scanner gates.

That single five-minute step seals exact helper SHA256
`61a9d508b092fcfd6174c12d0ea398b5a2cf7f03f0aed137b9b195c933081bde`
from the published commit's Git blob and compares the current tree through the
same retained descriptor. It uses a new private runner-temporary directory
outside the four-file context. Five fixed official HTTPS inputs are downloaded
with size+1 bounds, a120-second download allowance and complete SHA checks.
Ambient proxy configuration is excluded; redirects must remain on the fixed
HTTPS vendor domains. The helper itself still performs no download.

After the normal reuse preparation, the additional CI invocation is:

```text
/usr/bin/python3 -I -B <sealed-tools>/restricted_subdir_contract.py
  --inputs <fresh-private-inputs>
  --receipt <admitted-artifacts>/build-receipt.json
  --package <admitted-artifacts>/libmount1_2.41.6-0+deb13u0+gold1_amd64.deb
  --output <fresh-private-result>/restricted-subdir-proof
  --source-commit <actual-published-commit>
  --verifier-sha256 <sealed-helper-sha256>
```

The helper refuses outside nonroot Linux GitHub CI, before source reads or
output creation. Inputs, fixed receipt/package names and fresh output must be
under the canonical runner temporary directory. The outer controller owns the
published-source and workflow identity proof. The outer five-minute step
deadline contains a130-second helper subprocess bound; the helper has its own
120-second compile/run budget and20-second command
limits. Its children have a512MiB address-space cap,15 CPU seconds, core dumps
disabled and256KiB per-file limits. Each compiler and inert fixture runs in its
own process group with bounded owned cleanup. Outputs use a new0700 directory
and exclusive0600 files; partial failures never produce `proof.json` and may
not be retried by adopting the same directory.

Only the fixed `.json`, `.c` and `.log` files are copied into a separate public
artifact named `trixie-util-linux-restricted-subdir-contract-<run>-<attempt>`.
Package binaries, input archives and the original package artifact are excluded.
Per-file bounds, a4-MiB collection bound and a closed output-name inventory
apply. Bounded partial diagnostics may be retained after failure; absent or
unadmitted `proof.json` is not a passing contract. Admission requires the actual
published commit/helper identity, unchanged receipt/package lineage, four
successful integer compiler exits, original exits0, missing-guard exits10,
all input/source/function/package hashes, and all acceptance/mutation flags
false. Record it separately alongside the original OPEN receipt. Its legitimate
use is evidence for an independently reviewed, source-specific disposition of
CVE-2026-78409 on these exact package bytes. It cannot clear the Trixie database
row, prove privileged kernel behavior, or establish general image acceptance.
