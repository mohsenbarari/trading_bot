# App snapshot mountpoints belong in the image

The two serving snapshot destinations already require empty directories at
`/app/runtime/coin-inference` and `/app/runtime/product-estimator`. The public
Dockerfiles did not create them. The packaging change adds exactly their
`/app/runtime` parent and both empty children as root:root0755 during the build.
It refuses an existing or redirected parent. GNU install's explicit mode and
ownership avoid dependence on the builder's umask. The official
[Coreutils9.7 install implementation](https://github.com/coreutils/coreutils/blob/v9.7/src/install.c)
separates ancestor creation from the requested target directory attributes;
the existing `/app` owner/mode must still be checked unchanged in the image proof.
No runtime file, product
code, configuration, dependency, startup command or mount policy is changed.

`Dockerfile` and `Dockerfile.iran` contain the same final offline creation step
for future ordinary builds. Rebuilding current main is not the validation path
for the existing1450-file/857-Python-file App payload: current main and its build
history are different inputs. The existing OS composition controller also
starts at3b4 and installs15DEBs, so it cannot establish an unchanged32-layer
8da parent by rebuilding that work. Its sealed source stays untouched.

The small child recipe `deploy/maintenance/app_mountpoint_image/Dockerfile`
accepts no default parent. A separately reviewed CI caller must admit the exact
existing8da artifact before supplying an owned local alias. The recipe has one
offline RUN and no COPY, ENV, USER, WORKDIR, CMD or ENTRYPOINT directive. Parent
provenance labels remain ancestry. Four explicitly named metadata labels bind
the parent Config identity, the new full40-character recipe commit, a new
64-character input signature, and `packaging_candidate_not_accepted`. They do
not claim that this new child was produced by the old CI run.

## Exact future CI validation path

The separate, explicit default-off `.github/workflows/app-mountpoint-image.yml`
and `scripts/compose_app_mountpoint_image.py` implement this build-only lane.
They reuse SHA-sealed bounded helpers without altering those helpers or any
runtime control. The original OS composition lane remains unchanged. Only Root
may publish/dispatch the reviewed new lane. Its required validation is:

1. SHA-admit the original8da image artifact12573279010 from run40127349844:
   ZIP183811369 bytes, SHA256
   `3424f8f038817e42fd7992761a588039c79bab5a6542571ee73ef2d3338cb807`.
   Bind its actual producer/export and
   public evidence controls. Retain its complete32-layer list whose compact
   JSON SHA is `6762bf592f90b011d8c356fa58051654234aeff72b33f0d2d825f525c3d4c203`.
   Config identity is
   `sha256:8da3e404d01cc239606d1c79a30c4701c5a2ef2d103787b68b9ec221ae723672`.
   Use the actual typed daemon-ID-to-Config binding; manifestc610 and Config8da
   are distinct identities. Unknown identity or declared OnBuild refuses.
2. Seal this exact child Dockerfile from the published commit. Form a canonical
   new input-signature document from its SHA, the full recipe commit, admitted
   parent Config identity, full ordered parent layer list, and the three fixed
   root:root0755 empty directory paths. Hash that public document and pass the
   full commit/signature build arguments. Bind an owned alias to the measured
   immutable parent ID and keep it through every consumer; no registry fallback.
3. Build from only this public recipe, with its offline RUN and the existing
   bounded CI builder conventions. Require exactly33 child RootFS entries with
   the original32 as the ordered prefix. Inspect the exported new layer itself:
   no regular file, symlink, hardlink, whiteout or other special entry may occur.
   Its only new paths are the three directories, root:root0755; an existing
   `/app` directory header is permitted only with unchanged mode/UID/GID. No
   other directory metadata change is accepted. Both mountpoint children are
   empty. The build-time directory layer is image content, not a COW exception.
4. Compare the complete parent/child runtime Config in RAM. Only the four
   explicit child-label additions are allowed; all other labels and Env, User,
   Cmd, Entrypoint, working directory, ports, volumes and other fields remain
   equal. The exact public330,066-byte build-input manifest is now a fixed
   fixture, SHA256`cd2e64577576b9053b926d2e65620a98ca77b0ad71cc3100d9145b9409077c7b`.
   Its1450 entries include frontend source and build recipes, so they must not
   all be reported as installed image files. Verify every COPY-installed source
   entry and all857Python paths (13,194,027 bytes) against the sealed manifest.
   Independently compare every existing regular file, symlink and directory
   under`/app` and`/usr/local`, including actual bytes, mode and UID/GID; only
   the three new empty directories may differ. This also preserves the compiled
   frontend distribution and honest build-only input distinction.
   Package inventory, libc, interpreter and `/usr/local` remain unchanged.
   The older directory-inclusive AUDIT digest changes honestly: record both
   values and prove the exact directory delta, without weakening its old gate.
5. Re-run the sealed56 historical focused tests, ordinary native trust/SQLite/
   psycopg2 checks, component-presence and static ACL-consumer receipts on this
   actual child. Produce a new complete unfiltered all-severity/unfixed Trivy
   report and SBOM for that same identity. Export and hash that same immutable
   image, and bind every new receipt/report/source/signature to an external
   child provenance receipt. Preserve raw findings and all NOT_TESTED limits;
   no old raw finding, disposition or full-matrix status is silently inherited.

Source-contract and negative graph/layer tests run with ambient native/network
operations denied before imports and throughout each case. The actual original
248,816-byte public evidence ZIP has also passed read-only admission locally,
including all bound evidence hashes and the full32-layer identity. No Docker,
SSH, image build/load or workflow dispatch was performed by the source author.
Every CLI output is bounded before accumulation; artifact downloads have an
exact byte/digest admission and a file-size limit, and no Go CLI inherits an
address-space cap. Precise static refusal code and phase are retained without
exception operands or runtime configuration values. New public receipts omit
Config/Env projections; the required complete raw scanner report stays intact.
The local focused suite passes58tests (47new lane tests plus11image/trust tests).
Coverage.py7.13.5 measures every one of542executable controller statements and
all82reported branches:100%, zero missing lines or partial branches. The tests
exercise owned ZIP/gzip/TAR/OCI/Moby fixtures, first-buffer source admission,
full Config/file parity, meaningful named-daemon build/test/export ordering,
failure-before-publication, and last-tag cleanup without assuming that the
parent's local image record survives. No coverage threshold is changed.

Actual build,33-layer proof, complete
payload parity, tests inside the new image and new scan/export are NOT_TESTED.
All runtime COW/security guards, consumer controls, ACL/disposition gates and
runtime allowlists remain unchanged. This packet grants no image admission,
deployment, COW exception or security acceptance.


## Failed first build: shared OCI blob references

Run `40164897246` at source `aa6e5e4ed80116a2eb69513aad88c1381f077d35`
failed at `parent_image_admission` with the closed reason `archive_layers`.
Public evidence artifact `12587826603` is993bytes, SHA256
`d28c3870e9926c6a6d795f2e9e6c09c4375286c5a6c7df41deae2858bd32a9bb`.
No candidate was built or activated. Preserve that original failed evidence.

The exact public parent Config contains32ordered DiffIDs but30distinct values:
empty-layer DiffID
`sha256:5f70bf18a086007016e948b04aed3b82103a36bea41755b6cddfaf10ace3c6ef`
occurs three times. OCI logical layer references may reuse one physical blob.
The parser incorrectly demanded uniqueness in the logical reference sequence.
The correction removes only that demand: physical TAR members remain unique,
and every ordered reference must still hash to its exact Config DiffID and
match the authenticated OCI descriptor. Closed inventory, archive/config SHA,
layer count/prefix and the directory-only last-layer gate remain unchanged.
Regressions cover a three-reference shared blob, a mismatched ordered DiffID,
and duplicate physical TAR members. This source fix is not an actual successful
build or security acceptance; the corrected published CI run must prove those.

Root then ran the corrected parser read-only against the actual retained
451,747,328-byte Web parent archive. Exact archive SHA7337, Config8da, all32
ordered references,30distinct layers and the closed physical graph passed,
with the same retained file identity before/after. The three SHA-sealed public
source modules ran within a finite1GiB/oneCPU/240s scope; no Docker, LOAD,
extraction, private operational data or runtime COW probe was used. The actual
[public receipt](evidence/2026-10-25-app-parent-archive-preflight.json) has SHA256
`a2447ae7126e0fce70bfd76761b799ab114a6466c7ef7d51f52a1d098c83e41a`.
Root independently reran all58focused tests successfully; controller statement
and branch coverage remain100%. This closes the observed parent-format defect,
not the still-required actual child build, scan/export or runtime acceptance.
