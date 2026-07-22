# Writer Witness Four-Database Drill

This topology is a destructive **test-only** failure drill. It creates four
temporary PostgreSQL containers for `bot_fi`, `webapp_fi`, `webapp_ir`, and the
dedicated writer witness. It publishes no host ports, uses an internal Docker
network, mounts no project `.env`, accepts only databases whose names start
with `stage4_writer_witness_drill_`, and removes the stack on exit.

Run from the repository root:

```bash
scripts/run_writer_witness_failure_drill.sh
```

The drill proves this bounded control-plane matrix:

- concurrent FI/IR acquisition has exactly one winner;
- an authenticated rejection remains rejected after lease expiry;
- a response lost after witness commit is exactly replayed after service
  recreation;
- an asymmetric FI-to-witness partition never extends FI's local proof and FI
  becomes ineligible at the safety deadline;
- IR activation requires a fresh higher witness epoch and leaves exactly one
  locally eligible writer;
- pausing the real witness PostgreSQL container does not extend the local
  lease; after unpause, witness state and receipts persist and renewal resumes;
- witness transitions neither create tables nor mutate the Bot-FI database.

This is independent state on one Docker host, not independent-host evidence.
It does not cover VM pause, host loss, real TLS/mTLS, multi-vantage clock
measurement, file/storage parity, product sync convergence, or CDN switching.
Those remain production blockers in the roadmap.
