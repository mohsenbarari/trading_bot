# E46 runtime package composition candidate

This default-off `market_e46_os` lane in the existing OS composition workflow
reuses one tested E46 candidate, without rebuilding its product or Python
dependencies. The first actual composition run refused before loading an image;
the second built, tested and scanned but refused before export. The third run
completed composition, tests, both scans and export; the raw HIGH gate still
refuses acceptance. This lane grants no deployment,
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
and the50-minute job are finite. No image file was written on Native.

Actual [run40143991326](https://github.com/mohsenbarari/trading_bot/actions/runs/40143991326),
source `398a86fd63568276450d0763e1315d954ae54963`, failed closed at
`admit_image/archive_config_changed`. The fixed image ZIP/export/gzip seals and
the raw config's exact c22 SHA passed. No Docker load, package install, alias or
scan occurred. Its1542-byte public failure ZIP12579897715, SHA256
`fb7550d1f92c0daaf4c634c59e6c0141c9e45143fd26f1efbdc34be40a602dcc`,
remains immutable. The old comparison incorrectly required full raw OCI metadata
to equal Trivy's daemon-derived metadata.

One separately authorized normal download of the same59,814,206-byte image ZIP
went only to a sealed64MiB memfd, never a Native image file. Its outside SHA was
verified; bounded streaming skipped layer bodies and selected the12,989-byte
public config whose content SHA equals c22. The complete runtime Config and
ordered RootFS matched exactly. Only nine `history[1..9].created` strings differed.
The exact official Python base digest `b95327ce…` leads to public config
`b4403f3b7acf5e0700a5dd4e7ab01456d62b557a208a04f8a42d3470d93b5730`.
Its inherited timestamps contain `.788094843`, `.239258151` or `.372959533`
fractions; the original raw scanner records only their whole seconds.

This is a lossy scanner projection, not equal nanosecond instants. Trivy0.76.1's
[daemon configHistory](https://github.com/aquasecurity/trivy/blob/v0.76.1/pkg/fanal/image/daemon/image.go#L213)
explicitly uses `time.Unix(h.Created,0).UTC()` from Docker history. The correction
applies only that named whole-second UTC projection to raw `history.created`
for the scanner comparison, then requires exact recursive key/type/value
equality everywhere else. Invalid RFC3339 nanosecond timestamps, changed seconds,
other history fields, unknown keys, runtime Config or RootFS changes refuse.
The original raw config remains bound by its unchanged exact c22 SHA, including
every nanosecond and subsecond change; no source or scanner bytes are rewritten.
The admission receipt explicitly reports lossy history projection and whether
the full metadata was identical. Legacy compatibility JSON is still derived
from the authenticated actual raw config. No generic time normalization or
unknown-field omission is added, and no second large ZIP or image layer download
was made. That projection passed in the next actual run below.

Actual [run40145037090](https://github.com/mohsenbarari/trading_bot/actions/runs/40145037090)
at source `ed86e9da46b726b2dd7c0493a172a7b7e1552bdc` admitted all17 actual
legacy compatibility configs, installed exactly the fifteen DEBs and built
`sha256:673144af965c8c51d53995bd67cd43ecb64ca17000af2717f666e5d2f90af8dc`.
The complete Config, protected three-tree fingerprint and libc stayed equal;
all85 historical cases passed with zero skips, as did native checks and both
observational probes. Both raw scans finished. Their untouched counts are
0CRITICAL/44HIGH/69MEDIUM/83LOW/1UNKNOWN, including the same eight HIGH advisory
IDs. Those rows remain unwaived. The239,874-byte public evidence ZIP12579794692
has SHA256 `a31821921ff2ac2fb25d4ec9c52bf39d898f650a77dc0efa259f4a18be6def43`.
All19 composition evidence hashes, package deltas, raw-image/Config/RootFS and
SBOM package pins independently replayed successfully from that evidence.

Finalization refused `raw_reports/input_metadata` before export. The recipe had
omitted the existing helper's exclusive0600 precreation of raw/SBOM files. Later
scanner actions run under separate process umasks and can create new0644 files,
which the intentionally strict reader refuses. The failure artifact does not
retain the original inode mode, so it is not presented as a direct mode
observation. The source integration flaw is reproduced with real077-to-022
file writes. The correction exclusively creates both empty0600 report files
before handoff; ordinary scanner truncation then preserves those owned inodes.
Existing files, symlinks and aliases are never adopted. The strict reader is
unchanged. An executable controller fixture now covers compose, separate-umask
report writes, final validation/export and only-then alias cleanup. The candidate
from this failed run was not exported or made deployable; the publication
correction was independently reviewed and exercised in the actual run below.

Actual [run40145507410](https://github.com/mohsenbarari/trading_bot/actions/runs/40145507410)
at source `e2948eefc6b4e186b13895d298bca38eb6a1a53b` completed composition,
all85 tests without skips, native/component checks, raw scan, SBOM, finalization,
image export, both artifact uploads and owned-alias cleanup. Only the unchanged
raw HIGH/CRITICAL gate failed. The exported candidate is
`sha256:2c6ddd332f0aac17613208e66980e0154933bcb23cf2b94bd3d228c3ad5fd4e7`.
Its public evidence artifact12580311665 is241,567 bytes, SHA256
`04e051e29ad84c34180317033f7ab3210c8629b9c6bc14d6b037a14544121280`.
Independent reconciliation verified all19 evidence hashes, unchanged complete
Config/protected product trees/libc, exact15-package delta, ordered17-to18-layer
prefix, the original17 compatibility configs, nine explicit lossy scanner-history
entries, native checks, all85 test IDs,760 ELF observations and component absence.
The root reviewer separately checked the artifact/export/raw/SBOM hash chain.

Untouched raw counts remain0CRITICAL/44HIGH/69MEDIUM/83LOW/1UNKNOWN. No row is
filtered or waived. The image artifact12580172031 is63,183,253 bytes, SHA256
`88f4a81f387f357d7e7dc4c4812cbce324ec209606e565d153dd261af13c1a9d`;
its immutable gzip is63,555,322 bytes, SHA256
`0ffeb9ca7d3ae73928040cfe7eb611826063e5ce44eed81562dbadd8b62f82be`,
with188,604,928 uncompressed bytes. Export-receipt SHA256 is
`c241819a46f6a8cbebda4f60937655b90544312e0e7a1f31ff6a99d22da2a812`.
Only metadata and the small evidence ZIP were retrieved on Native; no image
file was written there. The source-specific HIGH assessments and exact-role ACL
predicates, runtime matrix and controlled activation remain separate requirements.
This is a usable unaccepted candidate, not a deployment or global security closure.
