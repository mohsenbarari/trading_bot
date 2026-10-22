"""Direct fixed-agent startup witnesses; no vendor CLI, auth, model or network.

CODEX_AUTHORED by /root/main_empty_sqlite_codex_author. Reuse the owned real
PTY/SQLite fixture as a module, never import/alias its TestCase for collection.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import unittest
from unittest import mock

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"

from scripts import two_site_proposal_runtime_profile as profile
from tests import test_two_site_profile_preparation_reselection as owned
from tests import test_two_site_runtime_proposal_profile as fixtures


class DirectAgentPreparationContracts(unittest.TestCase):
    def lab(self, **options):
        return owned.ProfilePreparationReselectionTests.lab(self, **options)

    def test_fixed_agent_argv_for_both_roles_and_optional_effort_without_prompt(self):
        for model, effort in (("gemini-4-argon-medium", "medium"),
                              ("gemini-4-argon-high", "high"),
                              ("gemini-4-argon-medium", None)):
            with self.subTest(model=model, effort=effort), self.lab(
                    model=model, effort=effort, fresh_panel=False) as trace:
                self.assertIsNone(trace.error)
                self.assertIs(type(trace.result), profile.PreparedProfile)
                self.assertEqual((trace.result.model, trace.result.effort), (model, effort))
                expected = ["/owned/fake-cli", "--mode", "plan", "--sandbox", "--model", model,
                            "--agent", "two-site-proposal-only", "--add-dir", str(trace.fixture.work)]
                if effort is not None:
                    expected += ["--effort", effort]
                self.assertEqual(trace.commands, [expected])
                self.assertEqual(len(trace.processes), 1)
                self.assertEqual(trace.writes, [])
                self.assertNotIn("--input-format", expected)
                self.assertNotIn("--print", expected)
                self.assertNotIn("--conversation", expected)
                self.assertNotIn("--dangerously-skip-permissions", expected)
                self.assertEqual(trace.fixture.source.read_text(), profile.DEFINITION)
                self.assertEqual(trace.fixture.hooks.read_bytes(), profile.HOOK_DEFINITION)
                self.assertEqual(trace.validations, [False])

    def test_readiness_is_observed_from_startup_not_after_old_age20_threshold(self):
        with self.lab(first_ready=2, fresh_panel=False) as trace:
            self.assertIsNone(trace.error)
            self.assertEqual(trace.result, trace.fixture.candidate)
            self.assertTrue(trace.processes[0].published)
            self.assertLess(trace.processes[0].terminated_at, 10)
            self.assertEqual(trace.writes, [])

    def test_actual_c004_count_shape_cannot_be_accepted_or_trigger_a_retry(self):
        # C004 exposed only2new/1empty/0final-valid; this invalid second blob
        # is a synthetic realization, not a claim about its historical DB.
        with self.lab(mode="mixed", fresh_panel=False) as trace:
            self.assertIsNone(trace.result)
            self.assertIs(type(trace.error), profile.ProposalProfileError)
            self.assertEqual(str(trace.error), "proposal runtime selected session is ambiguous or absent; LAST_HINT_INITIAL_METADATA_EMPTY new=2 empty=1 final_valid=0")
            self.assertEqual(trace.error.observation["last_initialization_readiness_hint"], {
                "reason": "INITIAL_METADATA_EMPTY", "new_sessions": 2,
                "valid_profiles": 0, "empty_profiles": 1,
                "invalid_profiles": 1, "sqlite_unavailable": 0,
            })
            self.assertEqual(trace.error.observation["final_new_sessions"], 2)
            self.assertEqual(trace.error.observation["final_valid_profiles"], 0)
            self.assertEqual(len(trace.commands), 1)
            self.assertEqual(len(trace.processes), 1)
            self.assertEqual(trace.writes, [])
            self.assertFalse(trace.processes[0].published)
            self.assertGreaterEqual(trace.processes[0].terminated_at, 30)
            self.assertLess(trace.processes[0].terminated_at, 31)

    def test_stale_ui_cannot_select_default_or_authorize_missing_metadata(self):
        for fragment in (False, "header_only", "foreign_name"):
            with self.subTest(fragment=fragment), self.lab(
                    mode="empty", fresh_panel=True, fragmented=fragment) as trace:
                self.assertIsNone(trace.result)
                self.assertIs(type(trace.error), profile.ProposalProfileError)
                self.assertEqual(trace.error.observation["final_valid_profiles"], 0)
                self.assertEqual(trace.writes, [])
                self.assertEqual(len(trace.commands), 1)


    def test_already_queued_foreign_trust_cannot_be_skipped_by_immediate_valid_metadata(self):
        with self.lab(synchronous_ready=True, startup_ui="foreign", fresh_panel=False) as trace:
            self.assertIsNone(trace.result)
            self.assertIs(type(trace.error), profile.ProposalProfileError)
            self.assertEqual(str(trace.error), "proposal runtime trust scope mismatch")
            self.assertTrue(trace.processes[0].published)
            self.assertEqual(trace.writes, [])
            self.assertEqual(trace.validations, [])

    def test_fragmented_foreign_trust_cannot_be_skipped_by_immediate_valid_metadata(self):
        with self.lab(synchronous_ready=True, startup_ui="partial_foreign", fresh_panel=False) as trace:
            self.assertIsNone(trace.result)
            self.assertIs(type(trace.error), profile.ProposalProfileError)
            self.assertEqual(str(trace.error), "proposal runtime trust scope mismatch")
            self.assertTrue(trace.processes[0].trust_completed)
            self.assertEqual(trace.writes, [])
            self.assertEqual(trace.validations, [])
            self.assertGreaterEqual(trace.processes[0].terminated_at, 2)

    def test_trust_queued_during_real_ready_hint_is_drained_before_acceptance(self):
        with self.lab(synchronous_ready=True, fresh_panel=False, after_hint_foreign=True) as trace:
            self.assertIsNone(trace.result)
            self.assertIs(type(trace.error), profile.ProposalProfileError)
            self.assertEqual(str(trace.error), "proposal runtime trust scope mismatch")
            self.assertEqual(trace.hint_injections, ["QUEUED_AFTER_REAL_READY_HINT"])
            self.assertEqual(trace.writes, [])
            self.assertEqual(trace.validations, [])

    def test_incomplete_trust_or_meaningful_header_prefix_is_not_metadata_authority(self):
        for fragment in ("incomplete", "prefix_only"):
            with self.subTest(fragment=fragment), self.lab(
                    synchronous_ready=True, startup_ui=fragment, fresh_panel=False) as trace:
                self.assertIsNone(trace.result)
                self.assertIs(type(trace.error), profile.ProposalProfileError)
                self.assertEqual(str(trace.error), "proposal runtime trust dialog incomplete")
                self.assertEqual(trace.writes, [])
                self.assertEqual(trace.validations, [])
                self.assertGreaterEqual(trace.processes[0].terminated_at, 30)
                self.assertLess(trace.processes[0].terminated_at, 31)

    def test_fragmented_owned_trust_is_answered_once_before_final_strict_validation(self):
        with self.lab(synchronous_ready=True, startup_ui="partial_owned", fresh_panel=False) as trace:
            self.assertIsNone(trace.error)
            self.assertEqual(trace.result, trace.fixture.candidate)
            self.assertTrue(trace.processes[0].trust_completed)
            self.assertEqual(trace.writes, [b"1\r"])
            self.assertEqual(trace.validations, [False])
            self.assertGreaterEqual(trace.processes[0].terminated_at, 2)
            self.assertLess(trace.processes[0].terminated_at, 20)

    def test_metadata_published_only_during_cleanup_never_creates_eligibility(self):
        with self.lab(mode="empty", fresh_panel=False, publish_on_terminate=True) as trace:
            self.assertIsNone(trace.result)
            self.assertIs(type(trace.error), profile.ProposalProfileError)
            self.assertEqual(str(trace.error), "proposal runtime profile was not ready during bounded preparation; LAST_HINT_INITIAL_METADATA_EMPTY new=1 empty=1 final_valid=1")
            self.assertEqual(trace.error.observation["final_valid_profiles"], 1)
            self.assertIs(trace.error.observation["ready_during_preparation"], False)
            self.assertEqual(trace.validations, [False])
            self.assertTrue(trace.processes[0].published)
            self.assertEqual(trace.writes, [])
            self.assertGreaterEqual(trace.processes[0].terminated_at, 30)

    def test_ordinary_banner_suffix_is_not_misclassified_as_partial_trust(self):
        with self.lab(synchronous_ready=True, startup_ui="ordinary_banner", fresh_panel=False) as trace:
            self.assertIsNone(trace.error)
            self.assertEqual(trace.result, trace.fixture.candidate)
            self.assertEqual(trace.writes, [])
            self.assertLess(trace.processes[0].terminated_at, 10)


    def test_truthy_but_empty_legacy_selector_never_reads_unready_fake_descriptor(self):
        fixture = fixtures.RuntimeProfileContracts("runTest")
        fixture.setUp()
        try:
            fixture.source.unlink()
            fixture.source.parent.rmdir()
            # Keep this READ-ONLY fixture's default MagicMock truthiness; only
            # iteration describes actual ready events, and that iterator is empty.
            with (
                fixture._preparation_mocks() as (_git, _popen, process, closed, selector),
                mock.patch.object(profile.os, "read", side_effect=AssertionError(
                    "unready fake descriptor must never be read")) as read,
            ):
                events = selector.return_value.select.return_value
                self.assertTrue(bool(events))
                self.assertEqual(list(events), [])
                terminate = mock.Mock()
                with self.assertRaises(profile.ProposalProfileError) as caught:
                    profile.prepare_profile(
                        Path("/owned/fake-cli"), fixture.work, fixtures.MODEL, "high",
                        terminate=terminate, cli_home=fixture.home,
                    )
                self.assertEqual(str(caught.exception), "proposal runtime fresh session discovery failed")
                read.assert_not_called()
                self.assertEqual(selector.return_value.select.call_args_list,
                                 [mock.call(0), mock.call(0.1)])
                terminate.assert_called_once_with(process)
                selector.return_value.register.assert_called_once_with(31, 1)
                selector.return_value.close.assert_called_once()
                self.assertEqual(closed, [32, 31])
            fixture.check_positive()
        finally:
            fixture.doCleanups()


    def test_failure_diagnostics_are_fixed_bounded_and_never_leak_ui_or_workspace(self):
        original_send = owned.OwnedReselectionCLI._send
        secret = "PRIVATE_DIAGNOSTIC_SENTINEL_NOT_A_REAL_CREDENTIAL"
        def send(cli, data):
            payload = ("\n" + secret + " " + str(cli.fixture.work) +
                       " Agent not found unknown agent " + profile.NAME +
                       " Enter sign in connection refused command not found\n").encode()
            return original_send(cli, data + payload)
        with mock.patch.object(owned.OwnedReselectionCLI, "_send", new=send), self.lab(
                mode="empty", startup_ui="ordinary_banner", fresh_panel=False) as trace:
            self.assertIsNone(trace.result)
            self.assertIs(type(trace.error), profile.ProposalProfileError)
            self.assertEqual(str(trace.error), "proposal runtime selected session is ambiguous or absent; LAST_HINT_INITIAL_METADATA_EMPTY new=1 empty=1 final_valid=0")
            facts = trace.error.preparation_observation
            self.assertEqual(set(facts), {
                "capture_state", "ui_bytes", "process_state_before_cleanup",
                "trust_answer_sent", "pending_trust", "ready_during_preparation",
                "agent_not_found_label", "unknown_agent_label", "not_found_label",
                "fixed_agent_name_present", "trust_marker_present",
                "owned_workspace_present", "enter_boundary_present",
                "authentication_prompt_present", "network_error_present",
                "dependency_missing_present",
            })
            self.assertEqual(facts["capture_state"], "CAPTURED")
            self.assertEqual(facts["process_state_before_cleanup"], "RUNNING")
            self.assertIs(type(facts["ui_bytes"]), int)
            self.assertGreater(facts["ui_bytes"], len(secret))
            self.assertLessEqual(facts["ui_bytes"], 65536)
            for name in set(facts) - {"capture_state", "ui_bytes", "process_state_before_cleanup"}:
                self.assertIs(type(facts[name]), bool)
            for name in ("agent_not_found_label", "unknown_agent_label", "not_found_label",
                         "fixed_agent_name_present", "owned_workspace_present",
                         "enter_boundary_present", "authentication_prompt_present",
                         "network_error_present", "dependency_missing_present"):
                self.assertIs(facts[name], True, name)
            for name in ("trust_answer_sent", "pending_trust", "ready_during_preparation",
                         "trust_marker_present"):
                self.assertIs(facts[name], False, name)
            serialized = json.dumps(trace.error.__dict__, sort_keys=True)
            for sensitive in (secret, str(trace.fixture.work), profile.NAME,
                              "Agent not found unknown agent", "connection refused"):
                self.assertNotIn(sensitive, serialized)
                self.assertNotIn(sensitive, str(trace.error))
            self.assertEqual(trace.error.observation["final_valid_profiles"], 0)
            self.assertEqual(trace.writes, [])
            self.assertEqual(len(trace.commands), 1)
            self.assertGreaterEqual(trace.processes[0].terminated_at, 30)
            self.assertLess(trace.processes[0].terminated_at, 31)
            # Detached facts survive actual cleanup; no buffer/prose is retained.
            self.assertFalse(trace.processes[0].thread.is_alive())
            self.assertEqual(facts["process_state_before_cleanup"], "RUNNING")

    def test_diagnostics_cannot_convert_valid_metadata_with_foreign_trust_into_success(self):
        with self.lab(synchronous_ready=True, startup_ui="foreign", fresh_panel=False) as trace:
            self.assertIsNone(trace.result)
            self.assertIs(type(trace.error), profile.ProposalProfileError)
            self.assertEqual(str(trace.error), "proposal runtime trust scope mismatch")
            self.assertEqual(trace.error.preparation_observation["capture_state"], "CAPTURED")
            self.assertIs(trace.error.preparation_observation["trust_marker_present"], True)
            self.assertIs(trace.error.preparation_observation["trust_answer_sent"], False)
            self.assertIs(trace.error.preparation_observation["ready_during_preparation"], False)
            self.assertTrue(trace.processes[0].published)
            self.assertEqual(trace.validations, [])
            self.assertEqual(trace.writes, [])
            self.assertEqual(len(trace.commands), 1)

    def test_diagnostic_capture_failure_preserves_profile_error_and_all_gates(self):
        diagnostic_error = RuntimeError("PRIVATE_DIAGNOSTIC_FAILURE_PROSE")
        with mock.patch.object(profile, "_preparation_failure_observation",
                               side_effect=diagnostic_error) as capture, self.lab(
                mode="empty", fresh_panel=False) as trace:
            self.assertIsNone(trace.result)
            self.assertIs(type(trace.error), profile.ProposalProfileError)
            self.assertEqual(str(trace.error), "proposal runtime selected session is ambiguous or absent; LAST_HINT_INITIAL_METADATA_EMPTY new=1 empty=1 final_valid=0")
            self.assertEqual(trace.error.preparation_observation, {"capture_state": "UNAVAILABLE"})
            self.assertNotIn(str(diagnostic_error), repr(trace.error.__dict__))
            capture.assert_called_once()
            self.assertEqual(trace.error.observation["final_valid_profiles"], 0)
            self.assertEqual(trace.writes, [])
            self.assertEqual(len(trace.commands), 1)
            self.assertGreaterEqual(trace.processes[0].terminated_at, 30)
            self.assertLess(trace.processes[0].terminated_at, 31)

    def test_diagnostic_failure_never_masks_original_cleanup_error_or_skips_cleanup(self):
        cleanup_error = OSError("owned primary cleanup error")
        original_terminate = owned.OwnedReselectionCLI.terminate_owned
        def terminate(cli):
            original_terminate(cli)
            raise cleanup_error
        with mock.patch.object(profile, "_preparation_failure_observation",
                               side_effect=RuntimeError("PRIVATE_DIAGNOSTIC_FAILURE_PROSE")) as capture, \
             mock.patch.object(owned.OwnedReselectionCLI, "terminate_owned", new=terminate), \
             self.lab(mode="empty", fresh_panel=False) as trace:
            self.assertIsNone(trace.result)
            self.assertIs(trace.error, cleanup_error)
            self.assertEqual(str(trace.error), "owned primary cleanup error")
            capture.assert_called_once()
            self.assertFalse(trace.processes[0].thread.is_alive())
            self.assertEqual(trace.validations, [])
            self.assertEqual(trace.writes, [])
            self.assertGreaterEqual(trace.processes[0].terminated_at, 30)
            self.assertLess(trace.processes[0].terminated_at, 31)


if __name__ == "__main__":
    unittest.main()
