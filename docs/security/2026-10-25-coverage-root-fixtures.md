# Required root-owned maintenance fixtures in coverage

Run40151928684 preserved the full-suite failures. Hosted UID1001 could not
satisfy real root-owned maintenance file/parent guards; production ownership
checks were correct. Seven import-fence leaks were corrected separately.

Coverage now partitions unittest identities into the existing hosted lane and
eight explicitly named maintenance fixture modules in
`scripts/run_coverage_unittest.py`. The complete root identity list is captured
during hosted discovery, then required byte-for-byte in the isolated root run.
Duplicate root IDs, missing modules, different commits, skips, expected failures,
errors, wrong run counts or missing results fail the required root lane.
Hosted/API/Bot tests retain their original nonroot environment and full-suite
failure status. Their executed count must also equal the complete declared
hosted identity inventory, including when unittest stops early with no failure.
Existing duplicate hosted discovery instances retain their multiplicity; no
tests are silently deduplicated. Neither lane substitutes for the other.

The new image receives only `git -c tar.umask=0022 archive` of the exact checkout
commit and the public test identity expectation. The explicit archive mask
preserves Git content bytes with canonical secure0755 directories/0644 regular
files (0755 executable files); Git's default0002 mask would leave group-writable
0775/0664 fixtures. Docker COPY makes `/reviewed` genuinely root-owned.
Only the copied repository root enters Python's path; no bare tests-directory
path masks package import mistakes. The root filesystem is read-only; the runner copies this public tree
into private `/work` tmpfs0700 because fixtures create temporary directories
beside their source. It changes no production ownership check or synthetic stat.
The container has UID0, zero capabilities, no-new-privileges, network none,
2CPUs,4GiB memory with no additional swap,128 PIDs,1GiB private work and128MiB
temporary tmpfs, both nosuid/nodev/noexec. There are no host mounts, Docker socket,
runtime Env, sessions or application data. Build networking installs only pinned
test dependencies; [PyYAML6.0.3](https://pypi.org/project/PyYAML/6.0.3/) and the
existing coverage7.15.0 are test tooling. Runtime imports and each test are under
ambient command/network/native/signal/resource denials. An840s self-alarm and
900s client timeout plus25min step deadline bound this disposable job. Failed
clients do not prove cancellation; cleanup checks the exact random name, nonce
label and immutable image before its bounded best-effort request.

Only bounded public test logs and coverage bytes leave the container. The hosted
ingester verifies source/test/UID identity and coverage SHA before combining it
with hosted coverage using existing relative paths. The final gate requires
hosted, root and frontend exits all zero; root data cannot erase earlier failure.

C07 tests use an explicit24October historical fixture clock and independently
prove25October refuses before lock changes. The production day/window remains
unchanged. The ACL host-refusal test explicitly selects a non-CI environment.
The Telegram permission-negative fixture explicitly chmods0755 so umask077 does
not turn it into a valid private directory. The managed-config loader fixture
seals the current explicit public buffers under test-only pins; historical
production pins remain intact, including changed-source refusal before staging.

This is source-only validation. The new root image and complete coverage run have
not yet executed. No production image, service, data, authority or acceptance is
changed by this packet.
