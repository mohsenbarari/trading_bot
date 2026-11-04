"""Offline host dispatch/monitor tests; adapters and process effects are doubles."""
from contextlib import contextmanager
import copy
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

from scripts import two_site_qoder_host as host
from scripts import two_site_qoder_maintenance as maintenance
from scripts import two_site_codex_autopilot as ap
from scripts import two_site_maintenance_transition_startup as startup


class TestHostDispatch(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="qoder-host-offline-")
        self.addCleanup(self.tmp.cleanup); self.root = Path(self.tmp.name)
        self.held = False
        @contextmanager
        def ap_hold(_path):
            self.assertFalse(self.held); self.held = True
            try: yield
            finally: self.held = False
        self.hold = patch.object(ap, "exclusive_supervisor_lock", side_effect=ap_hold)
        self.hold.start(); self.addCleanup(self.hold.stop)
        self.business = types.ModuleType("scripts.two_site_qoder_business_reconcile")
        self.business.pending_business_reconciliation = lambda _root: None
        self.business.classify_business_reconciliation = lambda *_args: None
        self.business.recover_business_reconciliation = self.effect("business")
        self.authority = types.ModuleType("scripts.two_site_qoder_authority")
        self.authority.classify_lifecycle_action = lambda _root: None
        self.authority.dispatch_lifecycle = self.effect("authority")
        self.modules = patch.dict(sys.modules, {self.business.__name__: self.business,
                                               self.authority.__name__: self.authority})
        self.modules.start(); self.addCleanup(self.modules.stop)

    def effect(self, name):
        def run(root):
            self.assertEqual(root, self.root)
            self.assertFalse(self.held, "dispatcher must release AP before adapter acquires MON")
            return {"state": name}
        return run

    def test_pending_business_has_priority_without_descriptor_authority(self):
        self.business.pending_business_reconciliation = lambda _root: {"untrusted": "not forwarded"}
        with patch.object(maintenance, "monitor_action", side_effect=AssertionError("not selected")):
            self.assertEqual(host.dispatch_host_action(self.root), {"state": "business"})

    def test_failed_business_classifier_then_authority_and_maintenance(self):
        state = self.root / maintenance.STATE_REL; state.mkdir(parents=True)
        maintenance.write_new(self.root, maintenance.STATE_REL / "status.json", {"state": "FAILED", "evidence": "unused"})
        self.business.classify_business_reconciliation = lambda root, evidence: {"closed": True}
        self.assertEqual(host.dispatch_host_action(self.root), {"state": "business"})
        self.business.classify_business_reconciliation = lambda *_args: None
        self.authority.classify_lifecycle_action = lambda _root: {"closed": True}
        self.assertEqual(host.dispatch_host_action(self.root), {"state": "authority"})
        self.authority.classify_lifecycle_action = lambda _root: None
        with patch.object(maintenance, "monitor_action", return_value="CONTINUE_OWNED_PROVIDER"), \
             patch.object(maintenance, "recover", side_effect=self.effect("maintenance")):
            self.assertEqual(host.dispatch_host_action(self.root), {"state": "maintenance"})

    def test_no_action_stop_and_classifier_failure_have_no_effect(self):
        with patch.object(maintenance, "monitor_action", return_value="NOT_ELIGIBLE"), \
             patch.object(maintenance, "recover", side_effect=AssertionError("no authority")):
            self.assertEqual(host.dispatch_host_action(self.root), {"state": "NO_ACTION"})
        (self.root / "STOP").touch()
        with self.assertRaisesRegex(maintenance.QoderMaintenanceError, "STOP"):
            host.dispatch_host_action(self.root)
        (self.root / "STOP").unlink()
        self.authority.classify_lifecycle_action = lambda _root: (_ for _ in ()).throw(ValueError("PRIVATE fixture"))
        output = io.StringIO()
        with patch("sys.stdout", output):
            self.assertEqual(host.main(["--root", str(self.root), "--recover"]), 2)
        self.assertEqual(json.loads(output.getvalue()), {"state": "BLOCKED", "error_type": "ValueError"})
        self.assertNotIn("PRIVATE", output.getvalue())


class TestLifecycleOrdinaryHandoff(unittest.TestCase):
    def test_host_replays_authority_before_ordinary_start_and_preserves_lock_order(self):
        with tempfile.TemporaryDirectory(prefix="lifecycle-handoff-offline-") as temp:
            root = Path(temp); locks = []
            @contextmanager
            def mon(_root):
                self.assertEqual(locks, []); locks.append("MON")
                try: yield
                finally: self.assertEqual(locks.pop(), "MON")
            @contextmanager
            def ap_hold(_path):
                self.assertEqual(locks, ["MON"]); locks.append("AP")
                try: yield
                finally: self.assertEqual(locks.pop(), "AP")
            current = {"head": "a" * 40}
            def authority(*_args):
                self.assertEqual(locks, ["MON", "AP"]); return True
            def launched(command, **_kwargs):
                self.assertEqual(locks, ["MON"])
                self.assertEqual(command, ["/usr/bin/bash", str(root / "scripts/two_site_autopilot_tmux.sh"),
                                           "start", "--quota-wait-enabled"])
            value = {"state": "AUTHORITY_TRANSITION_READY", "resume_ordinary": True,
                     "transition": {"new_repository": current}}
            with patch.object(maintenance, "monitor_hold", side_effect=mon), \
                 patch.object(ap, "exclusive_supervisor_lock", side_effect=ap_hold), \
                 patch.object(maintenance, "repository", return_value=current), \
                 patch.object(startup, "authority_startup_locked", side_effect=authority), \
                 patch.object(host.subprocess, "run", side_effect=launched) as launch:
                self.assertEqual(host.resume_after_lifecycle(root, value)["ordinary_handoff"], "STARTED")
                launch.assert_called_once()
                value["transition"]["new_repository"] = {"head": "b" * 40}
                with self.assertRaisesRegex(maintenance.QoderMaintenanceError, "HOST_AUTHORITY_RESULT_BINDING"):
                    host.resume_after_lifecycle(root, value)
                self.assertEqual(launch.call_count, 1)
                value["state"] = "COMPLETE"
                with self.assertRaisesRegex(maintenance.QoderMaintenanceError, "HOST_LIFECYCLE_RESULT"):
                    host.resume_after_lifecycle(root, value)
                self.assertEqual(launch.call_count, 1)



class TestAuthorityStartupComposition(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="authority-startup-offline-")
        self.addCleanup(self.tmp.cleanup); self.root = Path(self.tmp.name)
        policy = self.root / "docs/refactor/two-server/templates/automation-authority-policy.json"
        policy.parent.mkdir(parents=True); policy.write_text("{}")
        self.state = self.root / startup.STATE_ROOT_REL; self.state.mkdir(parents=True)
        self.authority = types.ModuleType("scripts.two_site_qoder_authority")
        self.authority.validate_authority_transition_for_startup = lambda *_args: None
        self.modules = patch.dict(sys.modules, {self.authority.__name__: self.authority})
        self.modules.start(); self.addCleanup(self.modules.stop)

    def test_none_preserves_legacy_and_valid_marker_rechecks_actual_repository(self):
        self.assertFalse(startup.authority_startup_locked(self.root, self.state, {}))
        repository = {"head": "a" * 40}
        self.authority.validate_authority_transition_for_startup = lambda *_args: {"new_repository": copy.deepcopy(repository)}
        with patch.object(startup, "validate_accepted_repository", side_effect=lambda item: item), \
             patch.object(startup, "validate_repository", return_value=copy.deepcopy(repository)) as live:
            self.assertTrue(startup.authority_startup_locked(self.root, self.state, repository))
            live.assert_called_once_with(self.root, require_clean=True)
            (self.state / "STOP").touch()
            with self.assertRaises(startup.MaintenanceTransitionStartupError):
                startup.authority_startup_locked(self.root, self.state, repository)

    def test_known_invalid_marker_or_live_drift_never_falls_back(self):
        with patch.object(startup, "validate_accepted_repository", side_effect=lambda item: item), \
             patch.object(startup, "validate_repository", return_value={"head": "b" * 40}):
            for result in [False, {"new_repository": {"head": "a" * 40}}]:
                self.authority.validate_authority_transition_for_startup = lambda *_args: result
                with self.assertRaises(startup.MaintenanceTransitionStartupError):
                    startup.authority_startup_locked(self.root, self.state, {"head": "a" * 40})


class TestMonitorDispatchFragment(unittest.TestCase):
    def run_fragment(self, *, state="BLOCKED", action="BUSINESS_RECONCILIATION", stop=False,
                     live=False, unsafe=False, hardlink_log=False):
        with tempfile.TemporaryDirectory(prefix="host-monitor-shell-") as temp:
            root = Path(temp); state_root = root / "state"; state_root.mkdir()
            reports = root / "reports"; reports.mkdir()
            entry = root / "launcher.py"
            entry.write_text("import json\nprint(json.dumps({'action':" + repr(action) + "}))\n")
            entry.chmod(0o600)
            if unsafe: entry.chmod(0o644)
            if stop: (state_root / "STOP").touch()
            if hardlink_log:
                original = root / "must-preserve"; original.write_text("original")
                os.link(original, reports / "maintenance-preflight.latest.log")
            tmux = root / "tmux"
            tmux.write_text("#!/bin/sh\nif [ \"$1\" = has-session ]; then exit 1; fi\nprintf called > \"" + str(root / "spawned") + "\"\n")
            tmux.chmod(0o700)
            source = (Path(host.__file__).parent / "two_site_autopilot_monitor.sh").read_text()
            fragment = source[source.index("# Qoder maintenance is a separate typed action."):source.index("# Accepted work remains accepted")]
            fragment = fragment.replace("/var/lib/two-site-qoder-maintenance/launcher.py", str(entry)).replace("/usr/bin/tmux", str(tmux))
            setup = ("set -u -o pipefail\n" + "\n".join(key + "=" + repr(str(value)) for key, value in {
                "integration_root": root, "state_root": state_root, "report_root": reports,
                "tmux_observation": "absent", "supervisor_count": "1" if live else "0", "state": state,
                "restart_eligible": "true", "provider_probe": "true", "action": "none"}.items()) + "\n")
            result = subprocess.run(["/usr/bin/bash", "-c", setup + fragment + '\nprintf "%s:%s" "$action" "$restart_eligible"'], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            if hardlink_log: self.assertEqual((root / "must-preserve").read_text(), "original")
            return result.stdout, (root / "spawned").exists()

    def test_finite_host_actions_are_dispatched_without_ordinary_replay(self):
        for action in ["BUSINESS_RECONCILIATION", "AUTHORITY_LIFECYCLE", "FINISH_OWNED_MAINTENANCE"]:
            with self.subTest(action=action):
                self.assertEqual(self.run_fragment(action=action), ("qoder_maintenance_started:false", True))

    def test_stop_complete_live_unknown_or_unsafe_entry_never_spawns(self):
        for args in [{"stop": True}, {"state": "COMPLETE"}, {"live": True},
                     {"action": "UNKNOWN"}, {"unsafe": True}]:
            with self.subTest(args=args): self.assertEqual(self.run_fragment(**args), ("none:true", False))

    def test_evidence_publication_does_not_follow_or_mutate_hardlinks(self):
        self.assertEqual(self.run_fragment(hardlink_log=True), ("qoder_maintenance_started:false", True))


if __name__ == "__main__":
    unittest.main()
