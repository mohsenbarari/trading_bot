"""Public source catalog and secret-safe runtime settings."""

from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path
import re
from typing import Mapping


@dataclass(frozen=True, slots=True)
class TelegramSource:
    code: str
    username: str
    compact_per_minute: bool = False


SOURCES = (
    TelegramSource("MELTED_AGGREGATE", "abshdh"),
    TelegramSource("MELTED_FLOW", "NaghdP"),
    TelegramSource("USD_HERAT", "ToofanHarirodOfficial"),
    TelegramSource("XAUUSD", "qheimat_ounce", compact_per_minute=True),
)
SOURCES_BY_CODE: Mapping[str, TelegramSource] = {
    source.code: source for source in SOURCES
}
SOURCES_BY_USERNAME: Mapping[str, TelegramSource] = {
    source.username.casefold(): source for source in SOURCES
}


def source_for_username(username: str) -> TelegramSource:
    key = username.lstrip("@").casefold()
    try:
        return SOURCES_BY_USERNAME[key]
    except KeyError as exc:
        raise ValueError("telegram_market_source_not_allowed") from exc


def source_for_code(source_code: str) -> TelegramSource:
    try:
        return SOURCES_BY_CODE[str(source_code).strip().upper()]
    except KeyError as exc:
        raise ValueError("telegram_market_source_not_allowed") from exc


@dataclass(frozen=True, slots=True)
class TelegramCredentials:
    api_id: int
    api_hash: str = field(repr=False)
    phone: str = field(repr=False)

    @classmethod
    def from_environment(
        cls,
        environment: Mapping[str, str] | None = None,
    ) -> "TelegramCredentials":
        values = os.environ if environment is None else environment
        raw_api_id = str(
            values.get("COIN_MARKET_TELEGRAM_API_ID", "")
        ).strip()
        api_hash = str(
            values.get("COIN_MARKET_TELEGRAM_API_HASH", "")
        ).strip()
        phone = str(
            values.get("COIN_MARKET_TELEGRAM_PHONE", "")
        ).strip()
        if not raw_api_id.isdigit() or int(raw_api_id) <= 0:
            raise ValueError("telegram_api_id_missing_or_invalid")
        if not re.fullmatch(r"[0-9a-fA-F]{32}", api_hash):
            raise ValueError("telegram_api_hash_missing_or_invalid")
        if not re.fullmatch(r"\+[1-9][0-9]{7,14}", phone):
            raise ValueError("telegram_phone_missing_or_invalid")
        return cls(
            api_id=int(raw_api_id),
            api_hash=api_hash,
            phone=phone,
        )


@dataclass(frozen=True, slots=True)
class TelegramCollectorSettings:
    credentials: TelegramCredentials = field(repr=False)
    database_path: Path
    session_path: Path

    def validate_paths(self, *, repository_root: Path) -> None:
        database = self.database_path.expanduser().resolve()
        session = self.session_path.expanduser().resolve()
        repository = repository_root.resolve()
        if database == session or database == session.with_suffix(".session"):
            raise ValueError("telegram_database_and_session_paths_overlap")
        for candidate in (database, session, session.with_suffix(".session")):
            if candidate == repository or repository in candidate.parents:
                raise ValueError("telegram_runtime_path_inside_repository")
        if self.session_path.is_symlink():
            raise ValueError("telegram_session_symlink_forbidden")
