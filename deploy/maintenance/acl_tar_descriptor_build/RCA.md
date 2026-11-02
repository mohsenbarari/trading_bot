# Native02 disk stop and separate hosted producer

The actual Root Native02 request `73be9459a1ae4d5db4d1ca70b76e1c85` failed in
`offline_build`. The retained public `offline_build.log` is 619255 bytes. Its
last active vendor case is group173, `tests/sparse05.at:21`, listing large sparse
files. It prints one `0 =2560` map row and 999 `10M =2560` rows, then invokes
`genfile` with a 4KiB block size. Source interpretation of the unchanged GNU1.35
generator writes the requested count of blocks per fragment; see the
[Debian-hosted GNU source, make_fragment](https://sources.debian.org/src/tar/1.35%2Bdfsg-3.1/tests/genfile.c).
That independently accessible page is packaging3.1; the exact-5 page was
unavailable. The actual case name, map and command come from the preserved-5 log.

The file requires 1000 ×2560 ×4096 =10485760000 bytes of real data (9.766GiB),
plus 999 ×10485760 =10475274240 bytes of holes, for a 20961034240-byte apparent
file. Sparse holes do not consume equivalent physical disk, but these data
fragments do. The 7.9GiB initially free on the production-attached volume could
not accommodate the data and retain the independent2GiB floor. The disk guard
stopped the owned builder at that floor before a completed vendor result.
This is a resource admission failure, not evidence that the production C patch
failed. It is also not evidence that the unfinished vendor test passed or skipped.

Root reports actual request58262 exited0 after checking and deleting only the
exact stopped failed builder, returning volume free space to7.9GiB. The failed
request's logs, context, source and failure receipt remain. No Native retry is
proposed. No disk cleanup or Docker command was performed by this source author.

The separate hosted profile retains the exact Native02 recipe, default vendor
test selection, C patch, SDK/old-runtime binding and mandatory native/ordinary
checks. It admits a fresh GitHub-hosted Ubuntu24.04 VM with at least40GiB free:
32GiB maximum growth plus an8GiB live floor. It does not change test sizes or
set a vendor skip option. In addition to package-build exit0 it requires the
real sparse05 success line in the completed, bound public build output.

[GitHub documents standard runner storage as14GB](https://docs.github.com/en/actions/reference/runners/github-hosted-runners),
so ordinary runner labels alone cannot establish our free-space requirement.
The producer measures real capacity and refuses before artifact acquisition,
image pull or compilation when insufficient. It deletes no preinstalled SDKs.
Root may separately review explicit unused SDK cleanup on that fresh hosted VM,
or select a suitable hosted runner; no such cleanup or runner availability is
claimed here. [Larger hosted runners have separately documented capacities](https://docs.github.com/en/actions/reference/runners/larger-runners).

Hosted02 additionally closes a source-review finding in the initial hosted
proposal: the inner builder had finalized its exports, but a later outer source
close could change one after that check. The outer caller now holds all twelve
actual host exports against their declared byte/hash pins, and checks their named
full metadata and parents after every outer physical close. A real owned
late-close mutation regression proves refusal. The original01 is retained.
