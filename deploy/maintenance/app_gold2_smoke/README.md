# Current gold2 App inventory and runtime proof

This cohort collects evidence for image `sha256:2389c900dbbf520bd7d7f723e8c48a5c258f1612c43e80d2b66c6d046f9f8434`, built from `4df2fed29859c0b82367666521f1a345f269f58a` in run `40844680297`. The proof-controller commit is a separate identity. The original scan contains 44 HIGH findings and zero CRITICAL findings; this collector grants no vulnerability disposition, deployment or global security acceptance.

The default-unarmed cohort reuses the existing AppR2 load, complete flattened-TAR inventory and unchanged smoke APIs. A separately pinned `export_controller_gold2.py` supplies the new cohort's never-started export. Historical entry points, globals, profiles and all 15 dependency bodies remain untouched. The sealer admits 19 repository inputs: those dependencies and four cohort inputs, through exact paths, sizes, modes, Git blobs and SHA256 pins. Development dependency snapshots are not repository additions.

The previous proof run `40861240998` stopped at the original 300-second export deadline after receiving 188,318,254 bytes. Its failure and cleanup evidence remain retained. The successor uses bounded 1 MiB pipe capacity/read/write operations and shares repeated name observations only within one metadata guard, with fresh name checks before that guard retires. Original FD metadata and flags remain fresh; no validation is cached across payload I/O. Total export, stderr, readback and inventory bounds remain unchanged. Local measurements are not a claim that the full hosted export will finish.

The hosted run requires genuine UID1001, an 8 GiB reserve and its original 1,800-second deadline. It admits the exact public evidence, SBOM and image archive, compares the complete daemon configuration, and exports a mount-free/network-none container that remains created/PID0/never-started. Every regular and hardlink ELF is inventoried without path exclusions. All rows and UNASSESSED results remain visible. The historical 835-path cardinality is checked after retaining the complete report; a changed count fails rather than being trimmed.

The same image must pass all eight runtime and 24 component assertions, migration and zero-skip/error checks. These isolated fixtures do not use production credentials or data. Owned containers and the internal network are checked by the unchanged cleanup implementation. Loaded images remain within the GitHub-hosted worker lifecycle; the inner receipt does not claim witnessed image or worker disposal.

Every new bootstrap/source read checks its original FD flags and metadata before and after I/O, including exceptions. Source-copy and completion writers retain original identity and known write prefixes, acquire a separate read-only reader before writer close, and retain that reader through physical finalization. Completion uses original named-source checks and the original clock after prior sources retire. Its JSON is conditional until successful final stdout/exit; same-byte replacement, unknown closes and cleanup failures cannot establish completion.

Each borrowed stream has its own admitted original descriptor, metadata, flags and physical-close ledger. A failed `fdopen` or a descriptor whose ownership cannot be established remains explicitly `HELD`; matching bytes or a reused descriptor number never authorizes closing another file. Actual collector failures latch on the original output. Read, guard and selector-close causes remain separately retained. Negative tests dispose only independently owned fixture resources; they do not claim that an uncertain component close succeeded.

Set final source modes before manifest admission and QA. Publishing receipts must not chmod, touch or rewrite an admitted input; even chmod to the same mode changes ctime. Preserve any failed physical-finalizer receipt and establish a fresh admission after the cause is corrected.

Validation before publication: the genuine UID/GID1001 fixture run passed 95 tests: 31 source/I/O, 18 metadata and 46 exporter tests. A separate Root fresh-copy run passed its 83 applicable tests with 60 original/copy readers finalized without errors; it does not count the 12 UID1001 collector cases. Independent review reproduced the prior failure families and verified the corrected descriptor-reuse behavior. The earlier Root run with changed source ctimes remains failed and preserved. Actual hosted inventory and the 32 product assertions remain separate required evidence.

Run the complete repository checks only with genuine UID/GID1001 and suitable private, accessible fixture parents:

```sh
APP_ENV_FILE=config/unit-test.env.example python3 -I -S -B -m unittest discover \
  -s deploy/maintenance/app_gold2_smoke -p 'test_*.py'
```

After the reviewed security-only commit is published on Main, explicitly dispatch `canonical-app-gold2-runtime-smoke.yml` with `run_proof=true`. Preserve the original reports, cleanup failures and `hosted-completion.json`; require the actual job exit and all finalizers before accepting that evidence. The workflow retains artifacts for ten days. The four bounded public test fixtures retire with this cohort after incident and rollback evidence no longer requires them.
