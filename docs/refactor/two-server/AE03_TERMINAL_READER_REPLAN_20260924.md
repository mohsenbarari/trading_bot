# AE03 terminal receipt reader: bounded restart plan (2026-09-24)

Status: **DESIGN ONLY / NO READER APPLIED**. The current implementation baseline is `fd990e177ff4012a9408833d427694a63bf40377`. Supervisor remains STOPPED; no runtime quota recovery, response custody, wait or replay is authorized.

## Why task 102 stopped

The Gemini Worker returned three proposals, but each pre-review test pass failed; the Pipeline then enforced its two-correction limit. Reviewer did not run, Codex applied zero proposal bytes, and Git stayed clean. None of the three Runner statuses contained `quota_evidence`. The user's report that the broader weekly Gemini credit is exhausted is therefore an external constraint, not a quota conclusion inferred from this unit.

| Attempt | Immediate test failure | Fixture lesson |
| --- | --- | --- |
| Worker 0 | A hardlink was created *inside* the scanned directory under an invalid filename, so filename rejection correctly happened before link-count rejection. A global `lstat` mock tripped the existing scanner rather than the later reader check. | Put the second hardlink outside the scanned directory. Target the intended phase with a scoped hook, not an unqualified call count. |
| Worker 1 | A global `fstat` fault fired in the scanner/directory phase while the test asserted a file-phase error. | Match the exact opened file FD or a narrow pathname/phase; never assume the Nth global call belongs to a new helper. |
| Worker 2 | The test referred to undefined `failing_dir_fstat` instead of its defined `failing_fstat_dir`. | Run local syntax/name checks and split fault cases into separate small tests before expensive full-suite execution. |

Static Codex inspection of the unapplied Worker 2 code found another gap: file post-read comparison checked inode/size/mode/owner/link count but not `st_mtime_ns` and `st_ctime_ns` (or an equivalent full fingerprint). Passing the visible tests after fixing the typo would not alone justify acceptance.

## Smaller independent units after Gemini credit returns

1. **R1: descriptor-bound bounded bytes only.** Implement one read-only helper for the exact existing `terminal-quota/<32-hex>.json` path. The directory must already exist. Use the accepted 64-entry scanner, then pin a no-follow `O_DIRECTORY` FD and open the exact basename with `dir_fd`. Before/after `fstat` and directory-relative `stat(follow_symlinks=False)` must agree on device, inode, size, mode, owner, link count, `mtime_ns` and `ctime_ns`; enforce 0700 directory, 0600 regular single-link file and <=32768 bytes. Read at most 32769 bytes, close **both** FDs on every exit, reject any path replacement. Return bytes only; no JSON or quota interpretation.
2. **R2: strict payload validation only.** Given already bounded bytes and the expected request ID, parse UTF-8 with unique keys and require byte-for-byte canonical JSON (`allow_nan=False`). Verify exact receipt/request/response keys, schema, filename/request ID, finite timestamps, canonical SHA256 of full request and response, `COMPLETED` exit 1, and exact `detect_quota_response` evidence. This is still observation, not replay authority. Test pure corruptions separately from filesystem faults.
3. **R3: one composition test.** A thin `read_terminal_quota_record` composes R1 and R2; prove real publisher-to-reader roundtrip and one missing/current-task-drift case. Do not retest every R1/R2 fault in a monolithic method.

Each unit gets a fresh, narrower semantic identity and assignment **only after** the failed task 102 is explicitly recorded as exhausted and no code applied; it must not be a renamed replay of task 102. Gemini Worker and independent Reviewer must both finish, then Codex checks semantic safety, exact bytes and independent regression tests before commit. No provider call is needed while weekly credit is exhausted.

## Negative tests that must be unambiguous

Use one fixture and one scoped fault per test. Place extra hardlinks outside the scanned directory. For a post-scan directory replacement, inject after the scanner returns; for a post-read replacement, inject at the file read; do not disturb the scanner itself when asserting the reader's later error. A targeted `os.fstat` fault should distinguish directory FD from file FD by `stat.S_ISDIR` and the exact FD captured from the intended `os.open`. Restore mocks via context managers, not `monkeypatch.undo()` mid-test. For FD closure assertions, restore the real `os.fstat` before checking `EBADF`, or spy on exact close calls. Under root, mode 000 is not proof of unreadability; assert mode bits explicitly.

Until R1–R3 and later serve/recovery tests pass, the existing publisher remains unwired and no terminal receipt may be treated as a resume permit.
