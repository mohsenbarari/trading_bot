# Compose memory units in normal Market/datastore validation

The fresh F79 observation stopped before any apply at `web_parity` /
`normal_resource`. Its failed request remains retained. A separate read-only
comparison completed with actual SSH/systemd exit0: Memory and MemorySwap were
integer bytes at runtime, while the normal rendered service model contained
byte-unit strings. The other eight resource comparisons and all remaining host
predicates matched. No private configuration values or hashes were disclosed.

Docker documents `mem_limit` and `memswap_limit` as byte values:
[Compose services reference](https://docs.docker.com/reference/compose-file/services/#mem_limit).
The Market and datastore validators now parse only those present fields using
their existing strict byte parser on a validation-only service copy. Default
swap uses twice the parsed memory amount. Real models, HostConfig, limits,
images, commands, environment and source operands remain unchanged.

Invalid types, malformed/fractional-byte sizes, negative/unbounded amounts,
signed64-bit overflow, unsupported deploy features and even one-byte runtime
drift still refuse. Canonical App contract and ordinary App restore sources
remain byte-identical; their completed restoration evidence is not relabeled.

Independent source review and the affected non-chown suite passed. Root then
executed the complete133-test packet with genuine UID0/CAP_CHOWN, NNP, separate
init, no network, read-only root and bounded temporary storage:133passed,
0failures/errors/skips. Manifest:
`73d43f20cea318363a92349094c260565e93c831e4439947c33701a7e96b2f3b`;
result:`9f1d8eba7e3971d1fb65e5d54c1fa4e89265e8282e10881fedf84692acf933fb`.

Current Capture and Market-admission source pins follow the reviewed Market
validator. Fresh v9 public producer/observer helpers passed26owned tests; old
controls and staged versions remain preserved. Fresh observation and any
separately authorized diagnostic are still required. No serving activation or
global security acceptance is claimed by these source/fixture results.
