# Current-candidate Coreutils provider smoke

This is a separate, default-UNARMED CI capsule. It neither builds an image nor
activates production, changes scanner findings, or accepts a security/CVE result.
Root and independent source review are required before its workflow is dispatched.
The historical gold2/App32/inventory profiles and their default entrypoints stay
unchanged.

The image is exactly
`sha256:2389c900dbbf520bd7d7f723e8c48a5c258f1612c43e80d2b66c6d046f9f8434`.
Its **original** source run is 40844680297: image artifact 12885065260 is
192672061 bytes, SHA256
`81e23bc7effc8f5185b95a8eb07371b1474f8cdb7b9f5585eb4863aa0dd4044f`;
evidence artifact 12884133769 is 359336 bytes, SHA256
`91c00cf3445b1cc56516ae3a2c8bfcedec0c06741271696a9308c8089f980842`.
The existing `app_gold2_smoke/intake.json`, evidence admission, canonicalization,
transport and owned-container cleanup are reused. The original ZIP/compressed
member pins are not replaced by the later 193210687-byte re-encoded gzip pin.
The admitted canonical TAR remains 476681728 bytes, SHA256
`5f98cb3c408e8b58e8ed576ac8cf459e25cb409a87f4c559fed9e890ea708194`.

The hosted entry seals the public source cohort, verifies its actual tracked Git
bytes in the checkout commit, and retains source/writer/readback originals to
physical finality. It admits the original image through the unchanged admission
APIs. A fresh owned container then uses real UID/GID 10001, a read-only root,
network none, all capabilities dropped, no-new-privileges, finite resources, no
host mounts and exactly two bounded owned tmpfs mounts. Source staging uses the
already reviewed configure-recovery writer/readback/finalizer helpers. The inner
program is also UNARMED when called directly.

Four actual installed CLI invocations exercise:

1. `cp -a`: directory access/default ACL plus a regular file ACL and user xattr.
2. `install -m 0640`: ordinary explicit-mode semantics and an exact basic ACL.
3. `mv` across two positively different tmpfs devices: the copy path, including
   directory and regular metadata, followed by source removal.
4. `cp --parents --preserve=mode`: the current parent-handle path and parent ACLs.

The CLI's own bounded loader diagnostics use explicit CI-only `LD_BIND_NOW=1`
alongside `LD_DEBUG=libs,versions,bindings`. This eagerly resolves imported ABI
symbols under the same library search route, so every case can attribute the
required imports without pretending that an uninvoked function was called.
The four functional cases separately verify real operation results. This mode
does not prove ordinary lazy-binding timing or the production loader context.
The diagnostics must attribute required ACL1.3 and
ATTR1.1 bindings to the exact admitted provider bodies, including libc,
libselinux, the interpreter and relevant PCRE2 transitive mapping. Search-path
candidates are not accepted as loaded mappings; a binding to an unknown absolute
provider refuses. There is no generic `ldd`, preload shim, compiler, app stack or
claim that the fixture helper's ctypes mappings prove the CLI mappings. Owned
fixture verification uses real ACL APIs on held object descriptors and exact
ACL/xattr/mode outcomes. Logs and receipts have finite caps before accumulation.

Local source QA is:

```
/usr/bin/python3 -I -S -B deploy/maintenance/coreutils_provider_smoke/test_provider.py
```

Those tests use public owned files, synthetic loader traces and isolated Python
children only. They do not instantiate CDLL or invoke cp/install/mv, Docker,
network or an actual provider. A genuine runtime result can only come from the
separately reviewed hosted action. Unknown source/FD custody fails without
adopting another descriptor or retrying a failed container. Failure evidence and
original causes remain visible; a failed capsule never publishes completion.

Successful output is candidate-image CI compatibility pending Root acceptance.
Production kernel/provider identity and all wider advisory/global decisions
remain separate.
