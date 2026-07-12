from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from uuid import uuid4

from hypothesis import find, given, settings as hypothesis_settings, strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, precondition, rule
from pydantic import ValidationError

from core.registration_contracts import (
    TelegramRegistrationCommand,
    TelegramRegistrationOutcome,
)
from core.server_routing import SERVER_FOREIGN
from core.services import telegram_registration_intent_service as intents
from models.telegram_registration_intent import TelegramRegistrationIntentStatus


NOW = datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc)


def valid_command_payload() -> dict[str, object]:
    return {
        "command_id": str(uuid4()),
        "idempotency_key": "stage9-stateful-command",
        "invitation_token": "USER-stage9-stateful-token",
        "mobile_number": "09121112233",
        "telegram_id": 9121112233,
        "telegram_username": "stage9-user",
        "telegram_full_name": "Stage Nine User",
        "address": "Stage nine valid address",
        "contact_verified_at": NOW.isoformat(),
        "local_completed_at": (NOW + timedelta(seconds=1)).isoformat(),
        "invitation_expires_at_snapshot": (NOW + timedelta(days=2)).isoformat(),
    }


class _IntentResult:
    def __init__(self, intent):
        self.intent = intent

    def scalar_one_or_none(self):
        return self.intent

    def scalars(self):
        return self

    def all(self):
        return [self.intent]


class _IntentDB:
    def __init__(self, intent):
        self.intent = intent
        self.flush_count = 0

    async def execute(self, _statement):
        return _IntentResult(self.intent)

    async def flush(self):
        self.flush_count += 1


def _intent():
    command = TelegramRegistrationCommand.model_validate(valid_command_payload())
    return SimpleNamespace(
        id=command.command_id,
        idempotency_key=command.idempotency_key,
        invitation_token=command.invitation_token,
        normalized_mobile=command.mobile_number,
        telegram_id=command.telegram_id,
        telegram_username=command.telegram_username,
        telegram_full_name=command.telegram_full_name,
        address=command.address,
        contact_verified_at=command.contact_verified_at,
        completed_at=command.local_completed_at,
        invitation_expires_at_snapshot=command.invitation_expires_at_snapshot,
        status=TelegramRegistrationIntentStatus.READY,
        retry_count=0,
        next_retry_at=None,
        last_error_code=None,
        authoritative_user_id=None,
        projected_user_id=None,
        created_at=NOW,
    )


class RegistrationIntentStateMachine(RuleBasedStateMachine):
    def __init__(self):
        super().__init__()
        self.intent = _intent()
        self.db = _IntentDB(self.intent)
        self.terminal_status = None

    @precondition(
        lambda self: self.intent.status
        in {TelegramRegistrationIntentStatus.READY, TelegramRegistrationIntentStatus.RETRY_WAIT}
    )
    @rule()
    def claim(self):
        with patch.object(intents, "current_server", return_value=SERVER_FOREIGN):
            attempts = asyncio.run(
                intents.claim_due_registration_intents(
                    self.db,
                    limit=1,
                    lease_seconds=30,
                    now=NOW + timedelta(minutes=self.intent.retry_count),
                )
            )
        assert len(attempts) == 1
        assert self.intent.status == TelegramRegistrationIntentStatus.FORWARDING
        assert attempts[0].attempt == self.intent.retry_count

    @precondition(lambda self: self.intent.status == TelegramRegistrationIntentStatus.FORWARDING)
    @rule()
    def retry(self):
        applied = asyncio.run(
            intents.schedule_registration_intent_retry(
                self.db,
                intent_id=self.intent.id,
                attempt=self.intent.retry_count,
                error_code="peer_unavailable",
                next_retry_at=NOW + timedelta(minutes=self.intent.retry_count + 1),
            )
        )
        assert applied
        assert self.intent.status == TelegramRegistrationIntentStatus.RETRY_WAIT

    @precondition(lambda self: self.intent.status == TelegramRegistrationIntentStatus.FORWARDING)
    @rule(success=st.booleans())
    def finalize(self, success: bool):
        outcome = (
            TelegramRegistrationOutcome.CREATED
            if success
            else TelegramRegistrationOutcome.IDENTITY_CONFLICT
        )
        applied = asyncio.run(
            intents.finalize_registration_intent(
                self.db,
                intent_id=self.intent.id,
                attempt=self.intent.retry_count,
                outcome=outcome,
                authoritative_user_id=41 if success else None,
                projected_user_id=41 if success else None,
            )
        )
        assert applied
        self.terminal_status = self.intent.status

    @precondition(lambda self: self.terminal_status is not None)
    @rule()
    def terminal_replay_is_immutable(self):
        before = (
            self.intent.status,
            self.intent.authoritative_user_id,
            self.intent.projected_user_id,
            self.intent.last_error_code,
        )
        retry_applied = asyncio.run(
            intents.schedule_registration_intent_retry(
                self.db,
                intent_id=self.intent.id,
                attempt=self.intent.retry_count,
                error_code="late_retry",
                next_retry_at=NOW + timedelta(days=1),
            )
        )
        finalize_applied = asyncio.run(
            intents.finalize_registration_intent(
                self.db,
                intent_id=self.intent.id,
                attempt=self.intent.retry_count,
                outcome=TelegramRegistrationOutcome.IDENTITY_CONFLICT,
                authoritative_user_id=None,
            )
        )
        assert not retry_applied
        assert not finalize_applied
        assert before == (
            self.intent.status,
            self.intent.authoritative_user_id,
            self.intent.projected_user_id,
            self.intent.last_error_code,
        )

    @invariant()
    def terminal_and_retry_state_are_consistent(self):
        if self.intent.status in intents.TERMINAL_INTENT_STATUSES:
            assert self.intent.next_retry_at is None
        if self.intent.status == TelegramRegistrationIntentStatus.RETRY_WAIT:
            assert self.intent.next_retry_at is not None
        assert self.intent.retry_count >= 0


TestRegistrationIntentStateMachine = RegistrationIntentStateMachine.TestCase
TestRegistrationIntentStateMachine.settings = hypothesis_settings(
    max_examples=30,
    stateful_step_count=12,
    deadline=None,
)


INVALID_FIELD_VALUES = {
    "idempotency_key": st.sampled_from(["", "short", "contains space", "x" * 193]),
    "invitation_token": st.sampled_from(["", "short", "x" * 193]),
    "mobile_number": st.sampled_from(["", "123", "0912-control\x00", "not-a-mobile"]),
    "telegram_id": st.sampled_from([0, -1, "not-an-int"]),
    "address": st.sampled_from(["", "short", None, {"unexpected": True}]),
    "contact_verified_at": st.sampled_from([None, "not-a-date", "2026-07-12T12:00:00"]),
    "local_completed_at": st.sampled_from([None, "not-a-date", "2026-07-12T12:00:00"]),
    "invitation_expires_at_snapshot": st.sampled_from(
        [None, "not-a-date", "2026-07-12T12:00:00"]
    ),
}


class RegistrationContractFuzzTests(unittest.TestCase):
    @given(data=st.data(), field=st.sampled_from(sorted(INVALID_FIELD_VALUES)))
    @hypothesis_settings(max_examples=40, deadline=None)
    def test_malformed_command_fields_fail_closed(self, data, field: str):
        invalid_value = data.draw(INVALID_FIELD_VALUES[field], label=field)
        payload = valid_command_payload()
        payload[field] = invalid_value
        with self.assertRaises((ValidationError, ValueError, TypeError)):
            TelegramRegistrationCommand.model_validate(payload)

    @given(
        extra_key=st.text(
            alphabet=st.characters(blacklist_categories=("Cs",)),
            min_size=1,
            max_size=20,
        ).filter(lambda value: value not in valid_command_payload())
    )
    @hypothesis_settings(max_examples=30, deadline=None)
    def test_unknown_fields_fail_closed(self, extra_key: str):
        payload = valid_command_payload()
        payload[extra_key] = "unexpected"
        with self.assertRaises(ValidationError):
            TelegramRegistrationCommand.model_validate(payload)

    def test_hypothesis_shrinks_short_address_to_a_reproducible_fixture(self):
        minimal = find(st.text(max_size=9), lambda value: len(value) < 10)
        payload = valid_command_payload()
        payload["address"] = minimal
        with self.assertRaises(ValidationError):
            TelegramRegistrationCommand.model_validate(payload)


if __name__ == "__main__":
    unittest.main()
