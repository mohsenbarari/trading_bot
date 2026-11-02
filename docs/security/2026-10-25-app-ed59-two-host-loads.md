# App ed59: actual two-host image-store admission and LOAD

The [actual receipt summary](evidence/2026-10-25-app-ed59-two-host-loads.json)
records completed archive admission, Web LOAD and direct Web-to-Native LOAD.
All three operations returned successful terminal results. The five App roles
still serve their original images; these operations replaced no container and
wrote no application data.

Repository checkout3641d39f supplies the versioned parser helpers. The actual
one-operation launcher/worker/snapshot are separately reviewed local buffers,
bound by their own SHA pins and31 tests from both Root and the independent
reviewer. They are not claimed to be source files contained in that Git commit.
The evidence also binds the fresh post-LOAD metadata check: owned temporary
guards are absent and permanent M/SOURCE identities remain unchanged.

| Verified fact | Actual value |
| --- | --- |
| CI build/source | 40165530395 / 9a4bec1e34f831a279388f62461a4d2f54770b49 |
| Config | ed59f14519b7329306f334e05d8666f808ac2c37da78afa922ddd21c60f3cedd |
| Web daemon image | e477ada2bb698f1dfaf7a5db9d40777ac135fdda768d6df9789e67aaf3c7e494 |
| Native daemon image | same ed59 Config identity, without querying Descriptor |
| RootFS | all33 ordered layers verified on both hosts |
| Admitted source TAR | 451755520bytes; SHA256 6b7dc2de34b8feed051f6e4c24761afecee85dab81b80b4f557b485e7d7156fa |
| Preserved runtime | Native15 containers and Web27, unchanged |

The immutable public Config from CI, admitted TAR Config and daemon Config were
compared completely in RAM. No Config/Env values were published. The physical
archive graph, every ordered layer and the directory-only last layer were
validated; Docker import did not substitute for archive admission.

The Native transfer created no large ZIP/TAR. The one retained Web TAR stayed
open through source checks and the operation's final checkpoint. Header/body/
footer/EOF and both client exits were bound before the Native receipt. Full
prospective import headroom remained checked on data and possible OS-root
temporary storage throughout. The measured budget is1422730396bytes; it is
neither a quota nor a claim that shared layers consume no temporary space.

Root first removed259 independently audited expired standard caches, one
rebuildable Trivy download archive and three specifically audited obsolete build
images. The active scanner, evidence, signatures, source, session/IDE history,
backups, all data/volumes and security/Market/rollback image families remain.
The three-image removal reclaimed993239040bytes as observed by filesystem free
space, rather than summing shared image sizes. Immediately after Native LOAD,
root had5195313152bytes and data11551678464bytes free; these are observations,
not reservations. One slow metadata-reference audit was stopped before any
removal after all259 original trees were reverified, then replaced with an
equivalent prefix-bound,120s-limited audit. The completed cleanup checked actual
host processes, Docker mount overlap and31 Git roots.

Owned temporary guards were released only after verified success. Permanent
Native M/SOURCE metadata and the empty SOURCE inode were preserved without
reading or hashing SOURCE payload. No runtime/COW guard was modified, no COW
probe ran, and the old Web rollback override remains required by its active
container. Historical failed attempts and cleanup evidence stay retained.

This record grants no serving authority. The existing limited consumer contract
still binds8da/32layers and old receipts; the ordinary current-main release path
requires genuine canonical release provenance that this mixed historical product
artifact does not have. Neither relabeling nor a fabricated prebuild receipt
would satisfy it. A compatible reviewed serving path remains unresolved.

Raw44HIGH findings, ACL caller/dynamic closure, F79 capture integrity and the full
functional/runtime matrix remain open. The new hosted image smoke work is
separate evidence with explicit exclusions. Global security acceptance stays
false, refactor automation stays STOP and the30-clean-unit streak has not begun.
