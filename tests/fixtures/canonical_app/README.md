# Canonical App public evidence fixtures

These are the exact authenticated public evidence artifacts used by the real
canonical-image tests. Tests verify their sizes and SHA256 before copying them
into owned fixtures. Repository-local files make the required isolated Root lane
independent of an operator's ignored `.local` directory and network access.

| File | Actual run | Bytes | SHA256 |
| --- | --- | ---: | --- |
| `canonical-build-665220a2-run40191605131.zip` | 40191605131 | 336678 | `eac66d11595ae9bd7a4478f5b126ac77e4d36b81dab25613f0d951c833d7b653` |
| `canonical-smoke-665220a2-run40196208692.zip` | 40196208692 | 392398 | `5cd1ab49b517bcd04c7adc1e883ecf351020f96b16ccfec70278037c5c013e99` |

The original provenance, failed raw finding gate and `security_accepted:false`
values remain intact. A fixture update requires authentic replacement evidence
and an explicit source review of its identity and assertions.
