# Current gold2 App inventory and runtime proof

This cohort collects evidence for image `sha256:2389c900dbbf520bd7d7f723e8c48a5c258f1612c43e80d2b66c6d046f9f8434`, built from `4df2fed29859c0b82367666521f1a345f269f58a` in run `40844680297`. The proof-controller commit is a separate identity. The original scan contains 44 HIGH findings and zero CRITICAL findings; this collector grants no vulnerability disposition, deployment or global security acceptance.

The new default-unarmed cohort reuses the existing parameterized AppR2 load, never-started export, complete flattened-TAR inventory and unchanged smoke APIs. Historical entry points, globals and profiles remain untouched. Dependency snapshots used during development are not repository additions: the sealer admits the 15 existing repository files through exact paths, sizes, modes, Git blobs and SHA256 pins.

The hosted run requires genuine UID1001, an 8 GiB reserve and its original 1,800-second deadline. It admits the exact public evidence, SBOM and image archive, compares the complete daemon configuration, and exports a mount-free/network-none container that remains created/PID0/never-started. Every regular and hardlink ELF is inventoried without path exclusions. All rows and UNASSESSED results remain visible. The historical 835-path cardinality is checked after retaining the complete report; a changed count fails rather than being trimmed.

The same image must pass all eight runtime and 24 component assertions, migration and zero-skip/error checks. These isolated fixtures do not use production credentials or data. Owned containers and the internal network are checked by the unchanged cleanup implementation. Loaded images remain within the GitHub-hosted worker lifecycle; the inner receipt does not claim witnessed image or worker disposal.

Every new bootstrap/source read checks its original FD flags and metadata before and after I/O, including exceptions. Source-copy and completion writers retain original identity and known write prefixes, acquire a separate read-only reader before writer close, and retain that reader through physical finalization. Completion uses original named-source checks and the original clock after prior sources retire. Its JSON is conditional until successful final stdout/exit; same-byte replacement, unknown closes and cleanup failures cannot establish completion.

Validation before publication: 31 fresh copied owned tests passed with 79 original/copy/log readers finalized without errors. The same 31 tests passed in the actual repository layout after correcting one fixture-only dependency-path fallback; that fallback was independently reviewed. Actual hosted inventory and smoke results remain separate required evidence.

Run the repository checks with:

```sh
APP_ENV_FILE=config/unit-test.env.example python3 -I -S -B -m unittest discover \
  -s deploy/maintenance/app_gold2_smoke -p 'test_*.py'
```

After the reviewed security-only commit is published on Main, explicitly dispatch `canonical-app-gold2-runtime-smoke.yml` with `run_proof=true`. Preserve the original reports, cleanup failures and `hosted-completion.json`; require the actual job exit and all finalizers before accepting that evidence. The workflow retains artifacts for ten days. The four bounded public test fixtures retire with this cohort after incident and rollback evidence no longer requires them.
