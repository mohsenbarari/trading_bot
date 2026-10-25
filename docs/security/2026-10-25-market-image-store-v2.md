# Market image-store worker v2

`deploy/maintenance/market_candidate_images_v2.py` is the OP06 worker source.
Root stages its actual bytes as `market_candidate_images.py` under the new
`security-market-candidate-images-20261025-06` directories on both sites. The
catalog remains SHA256 `c354498c3659fdf99613753d950416f134d81e0fe61fbdc80a32e7eec29b8d12`.
Controls and baselines must be fresh OP06 inputs, with the actual v2 worker hash.
Old OP05 controls, attempts and failed results remain historical evidence.

The Web processor exception concerns only container
`f2dbc15b9c9a2d14ef9669d1f6de37096161469a50c47bf6609f0383886b3a27`, image
`sha256:678aaf5cd8e954f368fc514430e5e63e638d84b2883194465ed0847287bc511c`.
Its health may alternate between `healthy` and `unhealthy` only while its typed
positive PID, start, restart count, full Host/execution/mount/network policy and
all other row fields remain identical. It must be running, not restarting,
paused or dead. The comparison uses a clone; actual health observations remain
unchanged. An immutable first deviation is bound to the current control SHA and
nonce, timestamped, and carried in receipts and strict success/failure/stream
protocols. The processor incident remains OPEN; neither health value grants
service or security acceptance.

Only the fixed existing F79 capture may wait up to 45 seconds for health to
become `healthy`. This issues read-only observations, checks capacity/deadline
and held source/fence FDs on every poll, and pins its first observed lifecycle
and complete network/static state through the wait. Other drift refuses
immediately. The final real healthy sample must satisfy the original immutable
baseline and at-most-one cached F79 restart policy. Readiness never retries
receive or image load. Snapshot collection still requires two equal final
snapshots. Fresh snapshots include explicit `paused` and `dead` fields.

This worker operates on image stores only. Config comparison remains in RAM;
archive/source FD, capacity, mutex, unknown-outcome and image identity checks
remain required. It performs no serving activation, capture source change,
Writer/DNS change or App guard adaptation. All acceptance claims remain false.
The closed c715 abort proof is a prerequisite for Root's fresh OP06 F365 receive;
the original c96 receive remains FAILED. The unexecuted rearm packet is unused.
