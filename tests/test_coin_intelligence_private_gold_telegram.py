"""Offline guards for the optional private-gold Telegram transport."""
from __future__ import annotations
from pathlib import Path
import tempfile
import unittest
from core.market_intelligence.private_gold_telegram import PrivateGoldEventChannels, PrivateGoldTelegramSettings
from core.market_intelligence.public_telegram.transport import PublicTelegramCredentials

class PrivateGoldTelegramTests(unittest.TestCase):
    def test_channel_environment_requires_two_distinct_private_ids(self) -> None:
        channels=PrivateGoldEventChannels.from_environment({"COIN_INTELLIGENCE_PRIVATE_GOLD_OFFER_EVENT_CHANNEL_ID":"-1002144100062","COIN_INTELLIGENCE_PRIVATE_GOLD_TRADE_EVENT_CHANNEL_ID":"-1002336335490"})
        self.assertNotEqual(channels.offer_channel_id,channels.trade_channel_id)
        with self.assertRaisesRegex(ValueError,"must_differ"):
            PrivateGoldEventChannels.from_environment({"COIN_INTELLIGENCE_PRIVATE_GOLD_OFFER_EVENT_CHANNEL_ID":"-1002144100062","COIN_INTELLIGENCE_PRIVATE_GOLD_TRADE_EVENT_CHANNEL_ID":"-1002144100062"})

    def test_runtime_paths_cannot_be_inside_repository_or_overlap(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root=Path(raw); settings=PrivateGoldTelegramSettings(PublicTelegramCredentials(1,"a"*32,"+15551234567"),PrivateGoldEventChannels(-1002144100062,-1002336335490),root/"s.sqlite",root/"m.sqlite",root/"session")
            settings.validate_paths(repository_root=Path("/root/trading-bot/coin-commodity-inference-promotion"))
            with self.assertRaisesRegex(ValueError,"overlap"):
                PrivateGoldTelegramSettings(settings.credentials,settings.channels,root/"s.sqlite",root/"s.sqlite",root/"session").validate_paths(repository_root=Path("/root/trading-bot/coin-commodity-inference-promotion"))

if __name__ == "__main__": unittest.main()
