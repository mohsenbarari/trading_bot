#!/usr/bin/env python3
"""Run the closed registry of epoch-bound WebApp effect handlers."""

import asyncio

from core.dark_standby import assert_not_dark_standby
from core.dr_effects import dr_effect_loop
from core.web_push import execute_web_push_effect
from core.sms import execute_sms_bulk_effect


async def main() -> None:
    assert_not_dark_standby("dr_effect_worker")
    await dr_effect_loop(
        {
            ("webpush", "webpush_user"): execute_web_push_effect,
            ("smsir", "recovery_sms"): execute_sms_bulk_effect,
        }
    )


if __name__ == "__main__":
    asyncio.run(main())
