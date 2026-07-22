import unittest

from core.telegram_delivery_interaction_result_contract import (
    TelegramInteractionAnchorEffect,
    TelegramInteractionDependencyOutcome,
    TelegramInteractionResultOutcome,
    TelegramInteractionResultRequirement,
    TelegramInteractionTargetKind,
    TelegramInteractionTargetReference,
    apply_interaction_delivery_result,
    build_delivery_result_target,
    build_interaction_result_contract,
    build_known_message_target,
    parse_interaction_target_reference,
    parse_interaction_result_contract,
    resolve_interaction_target,
    serialize_interaction_target_reference,
    serialize_interaction_result_contract,
)
from core.telegram_delivery_queue_contract import (
    TelegramDeliveryState,
    TelegramDestinationClass,
    TelegramFlowExit,
)


def make_capture_contract(**overrides):
    values = {
        "logical_message_key": "user:11:panel:7",
        "method": "sendMessage",
        "destination_class": TelegramDestinationClass.PRIVATE,
        "result_requirement": TelegramInteractionResultRequirement.CAPTURE_MESSAGE_ID,
        "anchor_effect": TelegramInteractionAnchorEffect.NONE,
        "authenticated": True,
    }
    values.update(overrides)
    return build_interaction_result_contract(**values)


class TelegramInteractionResultContractTests(unittest.TestCase):
    def test_serialized_contract_round_trip_is_exact(self):
        contract = make_capture_contract(
            anchor_effect=TelegramInteractionAnchorEffect.SET_CURRENT,
            anchor_generation=3,
            persistent_menu_present=True,
            temporary_context_keyboard=True,
            flow_exit=TelegramFlowExit.SUCCESS,
        )

        parsed = parse_interaction_result_contract(
            serialize_interaction_result_contract(contract)
        )

        self.assertEqual(parsed, contract)

    def test_serialized_contract_rejects_unknown_keys_and_non_boolean_flags(self):
        payload = serialize_interaction_result_contract(make_capture_contract())
        with self.assertRaisesRegex(ValueError, "serialized_contract_invalid"):
            parse_interaction_result_contract({**payload, "unknown": True})
        with self.assertRaisesRegex(ValueError, "boolean_invalid"):
            parse_interaction_result_contract({**payload, "authenticated": 1})

    def test_authenticated_anchor_requires_real_send_result_and_menu(self):
        contract = make_capture_contract(
            anchor_effect=TelegramInteractionAnchorEffect.SET_CURRENT,
            anchor_generation=3,
            persistent_menu_present=True,
        )

        self.assertEqual(contract.anchor_generation, 3)
        self.assertEqual(contract.method, "sendMessage")

    def test_anchor_rejects_edit_unauthenticated_nonprivate_and_missing_menu(self):
        invalid_cases = (
            {"method": "editMessageText"},
            {"authenticated": False},
            {"destination_class": TelegramDestinationClass.CHANNEL},
            {"persistent_menu_present": False},
            {"anchor_generation": None},
        )
        for overrides in invalid_cases:
            values = {
                "anchor_effect": TelegramInteractionAnchorEffect.SET_CURRENT,
                "anchor_generation": 4,
                "persistent_menu_present": True,
            }
            values.update(overrides)
            with self.subTest(overrides=overrides), self.assertRaises(ValueError):
                make_capture_contract(**values)

    def test_non_anchor_cannot_carry_anchor_generation(self):
        with self.assertRaisesRegex(ValueError, "generation_without_set"):
            make_capture_contract(anchor_generation=1)

    def test_authenticated_persistent_menu_cannot_bypass_anchor_tracking(self):
        with self.assertRaisesRegex(ValueError, "persistent_menu_requires_anchor"):
            make_capture_contract(persistent_menu_present=True)

    def test_edit_cannot_claim_a_new_message_result(self):
        with self.assertRaisesRegex(ValueError, "edit_cannot_capture"):
            make_capture_contract(method="editMessageReplyMarkup")

    def test_authenticated_temporary_flow_exit_requires_persistent_menu(self):
        for flow_exit in TelegramFlowExit:
            with self.subTest(flow_exit=flow_exit), self.assertRaisesRegex(
                ValueError, "flow_exit_missing_persistent_menu"
            ):
                make_capture_contract(
                    temporary_context_keyboard=True,
                    flow_exit=flow_exit,
                    persistent_menu_present=False,
                )

    def test_unsupported_method_and_invalid_logical_key_fail_closed(self):
        with self.assertRaisesRegex(ValueError, "method_unsupported"):
            make_capture_contract(method="sendPhoto")
        with self.assertRaisesRegex(ValueError, "logical_message_key_invalid"):
            make_capture_contract(logical_message_key="")

    def test_document_send_captures_the_real_provider_message_id(self):
        contract = make_capture_contract(method="sendDocument")

        self.assertEqual(contract.method, "sendDocument")
        self.assertEqual(
            contract.result_requirement,
            TelegramInteractionResultRequirement.CAPTURE_MESSAGE_ID,
        )

    def test_known_message_target_is_ready_without_a_delivery_dependency(self):
        reference = build_known_message_target(chat_id=-10011, message_id=44)

        decision = resolve_interaction_target(reference)

        self.assertEqual(decision.outcome, TelegramInteractionDependencyOutcome.READY)
        self.assertEqual(decision.chat_id, -10011)
        self.assertEqual(decision.message_id, 44)

    def test_serialized_targets_round_trip_without_mixed_identity(self):
        references = (
            build_known_message_target(chat_id=11, message_id=44),
            build_delivery_result_target(chat_id=11, source_receipt_id=9),
        )

        for reference in references:
            with self.subTest(kind=reference.kind):
                self.assertEqual(
                    parse_interaction_target_reference(
                        serialize_interaction_target_reference(reference)
                    ),
                    reference,
                )

        known = serialize_interaction_target_reference(references[0])
        with self.assertRaisesRegex(ValueError, "receipt_forbidden"):
            parse_interaction_target_reference({**known, "source_receipt_id": 9})
        result = serialize_interaction_target_reference(references[1])
        with self.assertRaisesRegex(ValueError, "message_forbidden"):
            parse_interaction_target_reference({**result, "message_id": 44})
        malformed = TelegramInteractionTargetReference(
            kind=TelegramInteractionTargetKind.KNOWN_MESSAGE,
            chat_id=11,
            message_id=44,
            source_receipt_id=9,
        )
        with self.assertRaisesRegex(ValueError, "receipt_forbidden"):
            serialize_interaction_target_reference(malformed)

    def test_target_builders_reject_nonpositive_or_boolean_identifiers(self):
        invalid_calls = (
            lambda: build_known_message_target(chat_id=0, message_id=1),
            lambda: build_known_message_target(chat_id=1, message_id=True),
            lambda: build_delivery_result_target(chat_id=1, source_receipt_id=0),
        )
        for call in invalid_calls:
            with self.assertRaises(ValueError):
                call()

    def test_delivery_result_target_waits_for_all_nonterminal_states(self):
        reference = build_delivery_result_target(chat_id=11, source_receipt_id=9)
        states = (
            TelegramDeliveryState.PENDING,
            TelegramDeliveryState.LEASED,
            TelegramDeliveryState.PENDING_RETRY,
            TelegramDeliveryState.BLOCKED_DESTINATION,
            TelegramDeliveryState.AMBIGUOUS,
            TelegramDeliveryState.PENDING_RECONCILE,
            TelegramDeliveryState.AMBIGUOUS_UNRESOLVED,
        )

        for state in states:
            with self.subTest(state=state):
                decision = resolve_interaction_target(
                    reference,
                    source_state=state,
                )
                self.assertEqual(
                    decision.outcome,
                    TelegramInteractionDependencyOutcome.WAIT_DEPENDENCY,
                )

    def test_delivery_result_target_uses_only_a_real_sent_message_id(self):
        reference = build_delivery_result_target(chat_id=11, source_receipt_id=9)

        ready = resolve_interaction_target(
            reference,
            source_state=TelegramDeliveryState.SENT,
            source_telegram_message_id=81,
        )
        missing = resolve_interaction_target(
            reference,
            source_state=TelegramDeliveryState.SENT,
            source_telegram_message_id=None,
        )

        self.assertEqual(ready.outcome, TelegramInteractionDependencyOutcome.READY)
        self.assertEqual(ready.message_id, 81)
        self.assertEqual(
            missing.outcome,
            TelegramInteractionDependencyOutcome.QUARANTINED,
        )

    def test_terminal_source_supersedes_dependent_edit(self):
        reference = build_delivery_result_target(chat_id=11, source_receipt_id=9)

        terminal = resolve_interaction_target(
            reference,
            source_state=TelegramDeliveryState.TERMINAL_FAILED,
        )
        quarantined = resolve_interaction_target(
            reference,
            source_state=TelegramDeliveryState.QUARANTINED,
        )

        self.assertEqual(
            terminal.outcome,
            TelegramInteractionDependencyOutcome.SUPERSEDED,
        )
        self.assertEqual(
            quarantined.outcome,
            TelegramInteractionDependencyOutcome.QUARANTINED,
        )

    def test_success_captures_message_id_and_activates_matching_anchor(self):
        contract = make_capture_contract(
            anchor_effect=TelegramInteractionAnchorEffect.SET_CURRENT,
            anchor_generation=5,
            persistent_menu_present=True,
        )

        decision = apply_interaction_delivery_result(
            contract,
            delivery_state=TelegramDeliveryState.SENT,
            telegram_message_id=91,
            desired_anchor_generation=5,
        )

        self.assertEqual(decision.outcome, TelegramInteractionResultOutcome.APPLIED)
        self.assertEqual(decision.telegram_message_id, 91)
        self.assertTrue(decision.activate_anchor)

    def test_late_success_records_id_but_cannot_replace_newer_anchor(self):
        contract = make_capture_contract(
            anchor_effect=TelegramInteractionAnchorEffect.SET_CURRENT,
            anchor_generation=5,
            persistent_menu_present=True,
        )

        decision = apply_interaction_delivery_result(
            contract,
            delivery_state=TelegramDeliveryState.SENT,
            telegram_message_id=91,
            desired_anchor_generation=6,
        )

        self.assertEqual(
            decision.outcome,
            TelegramInteractionResultOutcome.APPLIED_STALE_ANCHOR,
        )
        self.assertEqual(decision.telegram_message_id, 91)
        self.assertFalse(decision.activate_anchor)

    def test_missing_required_message_id_quarantines_result(self):
        decision = apply_interaction_delivery_result(
            make_capture_contract(),
            delivery_state=TelegramDeliveryState.SENT,
            telegram_message_id=None,
        )

        self.assertEqual(
            decision.outcome,
            TelegramInteractionResultOutcome.QUARANTINED,
        )

    def test_ambiguous_send_never_activates_anchor(self):
        contract = make_capture_contract(
            anchor_effect=TelegramInteractionAnchorEffect.SET_CURRENT,
            anchor_generation=5,
            persistent_menu_present=True,
        )

        for state in (
            TelegramDeliveryState.AMBIGUOUS,
            TelegramDeliveryState.PENDING_RECONCILE,
            TelegramDeliveryState.AMBIGUOUS_UNRESOLVED,
        ):
            with self.subTest(state=state):
                decision = apply_interaction_delivery_result(
                    contract,
                    delivery_state=state,
                    telegram_message_id=91,
                    desired_anchor_generation=5,
                )
                self.assertEqual(
                    decision.outcome,
                    TelegramInteractionResultOutcome.WAIT_RECONCILIATION,
                )
                self.assertFalse(decision.activate_anchor)

    def test_nonterminal_and_terminal_failure_results_are_distinct(self):
        contract = make_capture_contract()

        pending = apply_interaction_delivery_result(
            contract,
            delivery_state=TelegramDeliveryState.PENDING_RETRY,
        )
        failed = apply_interaction_delivery_result(
            contract,
            delivery_state=TelegramDeliveryState.TERMINAL_FAILED,
        )

        self.assertEqual(pending.outcome, TelegramInteractionResultOutcome.WAIT_DELIVERY)
        self.assertEqual(
            failed.outcome,
            TelegramInteractionResultOutcome.TERMINAL_NO_RESULT,
        )


if __name__ == "__main__":
    unittest.main()
