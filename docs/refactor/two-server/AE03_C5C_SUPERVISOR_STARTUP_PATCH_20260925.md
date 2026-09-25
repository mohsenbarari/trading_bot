# AE03-C5C Supervisor Startup Quota Wait Patch

```diff
--- a/scripts/two_site_codex_autopilot.py
+++ b/scripts/two_site_codex_autopilot.py
@@ -3025,6 +3025,80 @@
         except (KeyboardInterrupt, SystemExit):
             raise
         except Exception as exc:
+            if (
+                quota_wait_enabled
+                and type(exc) is AutopilotError
+                and str(exc).startswith("ambiguous: rescope intent already exists")
+            ):
+                try:
+                    head = repo_facts.get("head") if type(repo_facts) is dict else None
+                    if type(head) is not str or len(head) != 40 or not bool(re.fullmatch(r"[0-9a-f]{40}", head)):
+                        raise AutopilotError("repository facts head is missing or not a lowercase 40hex sha")
+                    try:
+                        from scripts.two_site_quota_wait_recovery import (
+                            recover_durable_initial_wait_locked,
+                        )
+                    except ModuleNotFoundError:
+                        from two_site_quota_wait_recovery import (
+                            recover_durable_initial_wait_locked,
+                        )
+                    try:
+                        from scripts.two_site_provider_broker import CYCLE_RUN_RE
+                    except ModuleNotFoundError:
+                        from two_site_provider_broker import CYCLE_RUN_RE
+
+                    recovered = recover_durable_initial_wait_locked(root)
+                    if recovered is None:
+                        raise AutopilotError("no durable quota wait record recovered")
+                    if type(recovered) is not tuple or len(recovered) != 2:
+                        raise AutopilotError("recovered quota wait result must be an exact tuple of length 2")
+                    cid, record = recovered
+                    if type(cid) is not str or not bool(CYCLE_RUN_RE.fullmatch(cid)):
+                        raise AutopilotError("recovered controller_run_id is invalid")
+                    if type(record) is not dict:
+                        raise AutopilotError("recovered quota wait record must be an exact dict")
+                    if record.get("state") != "WAITING_QUOTA":
+                        raise AutopilotError("recovered quota wait record state must be WAITING_QUOTA")
+                    if record.get("controller_run_id") != cid:
+                        raise AutopilotError("recovered quota wait record controller_run_id mismatch")
+                    req_id = record.get("request_id")
+                    if type(req_id) is not str or len(req_id) == 0:
+                        raise AutopilotError("recovered quota wait record request_id must be a nonempty str")
+                    sched = record.get("schedule")
+                    if type(sched) is not dict:
+                        raise AutopilotError("recovered quota wait record schedule must be an exact dict")
+                    if os.path.lexists(stop_path):
+                        if not stop_path.exists():
+                            raise AutopilotError("STOP path is a broken symlink")
+                        write_supervisor_state(state_root, "STOPPED", cycle=1)
+                        return 0
+                    current_repo = validate_repository(root, require_clean=True)
+                    if type(current_repo) is not dict or current_repo != repo_facts:
+                        raise AutopilotError("repository facts drifted or invalid during quota wait recovery")
+                    if os.path.lexists(stop_path):
+                        if not stop_path.exists():
+                            raise AutopilotError("STOP path is a broken symlink")
+                        write_supervisor_state(state_root, "STOPPED", cycle=1)
+                        return 0
+                    run_dir = state_root / "runs" / cid
+                    write_supervisor_state(
+                        state_root,
+                        "WAITING_QUOTA",
+                        cycle=1,
+                        evidence=str(run_dir),
+                        head=head,
+                        request_id=req_id,
+                        schedule=sched,
+                        summary="Validated terminal quota; awaiting explicit resume gate.",
+                        blockers=[],
+                    )
+                    return 2
+                except (KeyboardInterrupt, SystemExit):
+                    raise
+                except Exception as recovery_exc:
+                    blocker = str(recovery_exc)[:512] or type(recovery_exc).__name__
+                    write_supervisor_state(state_root, "BLOCKED", cycle=1, blockers=[blocker])
+                    return 2
             blocker = str(exc)[:512] or type(exc).__name__
             write_supervisor_state(state_root, "BLOCKED", cycle=1, blockers=[blocker])
             return 2
```
