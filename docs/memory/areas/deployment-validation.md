# Deployment Validation

- 2026-09-03 | `DPL-9` approved: refactor the existing matrices into separate Business and Deployment layers, exhaustively covering critical state transitions across connectivity, Writer, component/risk class, operation, migration and failure phase; combine high-risk intersections and use pairwise coverage only for independent low-risk dimensions. No High/Critical scenario may lack a driver or evidence. Destructive faults run only in isolation; production receives controlled smoke and monthly recovery drills without an extra VM.

- 2026-09-03 | Required outcomes: local committed-data RPO 0; zero duplicate business events, unintended Writer/DNS changes, or duplicate Bot/Executor/Job owners; R0/R1 downtime <=30s, Web/API rollback <=2m and full R0/R1 rollback <=5m. A non-schema hotfix reaches `READY` <=10m, Finland healthy <=15m and, when connected, two-site convergence <=20m; a normal R0/R1 release reaches both healthy <=30m. Routine security gates/retries count against these clocks; R2/R3 receives a case-specific bound.

- 2026-09-03 | Run diff scope on PR, critical release matrix on `main`, full CI nightly, isolated two-host weekly, real recovery monthly, and the full post-activation matrix for hotfixes. Evidence is machine-readable, redacted, traceable and retained outside repository `tmp`.
