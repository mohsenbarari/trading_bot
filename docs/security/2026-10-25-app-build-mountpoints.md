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

This packet does not wire or dispatch a new CI lane. The existing
`.github/workflows/security-os-candidate-compose.yml` supplies the normal scan,
SBOM and export pattern. A future explicit default-off child lane must do this:

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
   equal. Preserve all1450 regular payload paths/bytes/modes/UID/GID and all857
   Python source paths (13,194,027 bytes) against the sealed parent manifests.
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

Only source-contract tests have run here. Actual build,33-layer proof, complete
payload parity, tests inside the new image and new scan/export are NOT_TESTED.
All runtime COW/security guards, consumer controls, ACL/disposition gates and
runtime allowlists remain unchanged. This packet grants no image admission,
deployment, COW exception or security acceptance.
