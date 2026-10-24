# Existing App OS composition: source packet, not acceptance

This manual CI-only recipe composes the already exported image
`sha256:3b4f58b071f3a7c51778e1f6a1ae66d03beaa401fdefee6f8fd2af69999512cf`
with exactly fifteen previously built runtime DEBs. It does not rebuild the
App, frontend, Python dependencies or vendor packages. Product baseline remains
`ba1f7616ade57558f51bb3a7fbb903243a33aab4`, security overlay remains
`19d4c2da778f8f1edc567db4c9616c2d153980c9`. No execution result exists yet.

| Packages | Run / public artifact | ZIP bytes / SHA256 | Build receipt SHA256 |
|---|---|---|---|
| util-linux nine |40112666640 /12567076825|2362423 /a53a204c2ad0b04d2526811eaeb2fa40e0799d720c3d57a9650db9ada4279e8b|3a799ad92549c9d3c5e18013463743d9bc2b721eb83557e5ee3a0273d4514dde|
| ncurses four |40112668891 /12567123180|1319510 /5d4f181112b2965d177c10e3923b2ba2ff4d2b0ee6716818acc9b7f784bfee08|0903c9fbcd69d5dbc6e381f2c84d2718f85899e23c375de2e1c8ec57e81483fd|
| ACL and tar two |40115339187 /12567979094|933904 /d367fb3c1b92f069538a6e90a77ea110efec6dccd9286a8f0e1359b285bc1765|c0114c344ec6103c1d8053421f5e63dc06721b16d8e318f21cde95608d9b465c|

Package admission checks ZIP bytes/hash, bounded closed member names, pinned
receipts and all fifteen exact archive hashes/versions. The ACL/tar receipt
also binds the actual ACL compiler receipt. The public installation capsule
contains only those DEBs and its checked manifest, using0755/0644 permissions.
The existing image admission API checks the exact original image archive;
its historical component-only receipt is not reinterpreted as composition
acceptance. Missing or changed inputs fail without compiling a replacement.

The generated Dockerfile has only FROM and RUN. It uses the admitted base's
owned local alias under the built-in Docker builder, without registry pulls.
Read-only build-context mounts avoid storing a duplicate package capsule in
new image layers. RUN has network=none. Existing setpriv drops to exactly
CHOWN/FOWNER/FSETID/SETUID/SETGID and no-new-privileges before the sealed installer;
the installer checks the actual kernel capability masks. BuildKit installer
CPU/memory/PID cgroup caps are **NOT_ASSERTED**. The job, client commands,
outputs and artifacts have finite bounds; test/probe containers have explicit
memory/CPU/PID limits, read-only roots, no network and no capabilities.

Normal APT first simulates exactly fifteen upgrades and no removals, then
installs the same absolute local paths with normal Pre-Depends ordering. Only
simulation uses --no-download; the real command must acquire local files.
Before/after complete package inventories reject any additional package,
unrelated upgrade, removal or libc change. App and Python trees and libc bytes
are fingerprinted before and after. Clean dpkg audit remains required.

No USER, ENV, CMD, ENTRYPOINT, LABEL or SHELL directive changes the original
runtime configuration. Whole Docker Config equality and the ordered complete
baseline RootFS prefix are mandatory. Full original OCI config is additionally
compared with the actual composed-image scanner metadata. This avoids the
[Docker commit merge behavior](https://github.com/moby/moby/blob/v28.5.1/daemon/commit.go)
that can retain temporary installer environment variables or labels.

The original public19d evidence ZIP is artifact12553191740,190111 bytes,
SHA256976a32a2c5284b27cf1f007992495d1a2353f0ca957133916f2fd27871d7d30a.
Its test-sources proof is SHA256
39826fc608153f0ce34cd0a48cfe5e6712914e2d157c8c5475c4262445319593.
CI fetches only six pinned Git blobs, checking Git blob identity, SHA256 and
size. Three security modules and the example Env come from19d; two Market
modules come fromba1. The older coin-rate-engine test is absent from the current
checkout, so current tests are never substituted. Exactly56 tests in five
modules must pass without skips inside the new image. Existing native trust,
psycopg2/SQLite/libmagic checks and the sealed component probe also run. The full
browser/API/Bot runtime matrix remains **NOT_TESTED**.

Completed composition/export receipts bind the native/component/focused results,
both complete inventories and bounded public build/test logs by SHA256. The
component receipt must identify the same image, source and verifier and retain
its false acceptance/suppression flags. A create timeout or malformed returned
ID triggers bounded cleanup by the exact generated name, owner label and image;
only its resolved immutable owned ID can be removed. The original job still
fails and records daemon outcome **UNKNOWN**, even if that best-effort removal
returns successfully. Client termination never proves daemon cancellation.

All severities and unfixed rows remain in the raw Trivy report, with an empty
scan config and no ignore file. Raw JSON and SBOM must identify the exact new
image and all fifteen installed versions. Export retains the original baseline
and overlay provenance in a separate composition receipt bound to the new
image/config/layers/package/test/report hashes. Scanner collection exit0 never
means admission. Existing HIGH/CRITICAL gates, real util-linux privileged-test
gaps, ncurses interactive coverage and ACL legacy-path API questions remain
open. Security accepted, gate waived, deployment and production installation
remain false. No VEX, policy exception, production mount or private data is used.
