# E46 runtime package composition candidate

This default-off `market_e46_os` lane in the existing OS composition workflow
reuses one tested E46 candidate, without rebuilding its product or Python
dependencies. This source packet has not run in CI. It grants no deployment,
scanner exception or acceptance. App and datastore jobs keep their own inputs;
requesting a datastore profile together with this lane refuses admission.

The parent is exactly
`sha256:c22d9d459ab4ca0586066d87e2b2456958309fa12e47dfaac2651a1743e0fde4`,
from [run40140063747](https://github.com/mohsenbarari/trading_bot/actions/runs/40140063747)
at source `88299eb42bfa05361f8b8ed82358873336a6480e`. Its image artifact
12578501302 is 59,814,206 bytes, SHA256
`da7ee559c4815113ed3ff74be845443b48185c410cc4f8b474cabb0b7efdd236`.
The separate 169,181-byte evidence artifact12578306625 has SHA256
`474360da7f53997b30e1dd8f754b46ba1ae8a811f55432c084a0aec756cf9f49`.
That actual candidate passed native checks and all85 historical cases, but its
unmodified scan still reported44 HIGH rows. These original records remain intact.

The exact outer image ZIP seal is checked before extraction. Only the two regular
members `candidate-image.tar.gz` and `image-export.json` are admitted. The bounded
export receipt must match the independently pinned original payload, source,
native/test receipts, raw report and SBOM. Its inner gzip bytes/hash and expansion
size are then checked. A physical TAR walker rejects duplicate names, links,
PAX/GNU extension headers, unsafe paths, nonzero trailers and undeclared members.
Every config, OCI descriptor and ordered layer is hashed; repeated references to
one physical layer remain legitimate. Optional Moby compatibility JSON blobs must
equal the complete reconstructed per-layer chain, including the exact final
public Config projection. They are not an open-ended extra-blob allowance.
The [official exporter](https://github.com/moby/moby/blob/v28.5.1/image/tarexport/save.go)
and [Config declaration](https://github.com/moby/moby/blob/v28.5.1/api/types/container/config.go)
define that projection and Go field ordering. The retained TAR FD is rehashed and
passed directly to normal `docker image load`; actual ID/Config/layers must match.

The lane seals the unchanged `10ccbe21…` command/package/installer helper and
`9131fe76…` Market test helper plus the unchanged component/ACL probes. App image
admission, App tests and App source constants are never called. It admits exactly
the same fifteen runtime DEBs from util40112666640, ncurses40112668891 and
ACL/tar40115339187, with each family receipt and every package byte/version pin.
The original c22 SBOM confirms all fifteen packages already exist and libc is
`2.41-12+deb13u4`. Normal APT simulation must name precisely those15 upgrades,
without removals, additions, downloads, force flags or libc changes. Installation
runs with network-none and the reviewed setpriv mask. The short Dockerfile sets
`USER0:0` for that phase and restores `USER10001:10001`; the complete resulting
Docker Config, including public defaults and labels, must equal the parent.
No new image label pretends that the package overlay was original product source.
Separate receipts bind the composition recipe and package provenance.

Before/after complete package inventories and bounded file/node fingerprints
protect `/app`, `/usr/local` and `/opt`, including `/opt/market-python`. Directory
read failures propagate. Libc package and bytes stay exact; only the fifteen
declared package versions may differ. The entire original17-file test capsule is
reconstructed from its pinned Git blobs and original/effective SHA values,
including the previously reviewed security-base assertion. Public directories
remain0755 under umask077, files retain Git modes, and the outer host namespace
remains0700. The actual candidate must again pass all93 product-file/directory
checks, trust/system-libpq/SQLite/Pydantic native checks and all85 case IDs:
84 under UID10001 and the unchanged root-owned metadata fixture under UID0.
Both use read-only roots, network-none, no capabilities, no-new-privileges,
1GiB memory,2 CPUs,128 PIDs, a private128MiB tmpfs and finite execution. Ambiguous
Docker creation remains a failed UNKNOWN outcome after bounded owned cleanup.
No client cancellation or absence observation proves daemon completion.

The new image also receives the sealed static ACL consumer and component probes.
They are observational and preserve their limited scopes and false acceptance
flags. All severities/unfixed findings are retained in the raw Trivy0.76.1 report;
the same immutable image/config/layers and exact fifteen versions must appear in
the SBOM. Source, inputs, inventories, native/test/probe/log hashes chain into the
composition and final export receipts. The temporary baseline alias survives
through final verification/export and is removed only by exact current-run
ownership afterward. Image upload requires both export and alias cleanup success.
The unchanged raw HIGH/CRITICAL gate still fails on any such row, after evidence
is retained. Source-level vendor dispositions and ACL caller adoption require
separate review; this packet does not claim zero findings, whole-library ACL
repair or deployment eligibility. Capture2/F79 and its functional layers remain
outside this E46-only lane. Full role/runtime/Telegram/database integration and
activation remain NOT_TESTED. BuildKit cgroup limits are not asserted; commands
and the50-minute job are finite. No image was downloaded to Native for this work.
