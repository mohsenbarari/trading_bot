from __future__ import annotations

import os
from dataclasses import dataclass
from getpass import getpass
import json
from pathlib import Path


DEFAULT_CHANNELS = (
    "abshdh",
    "NaghdP",
    "ToofanHarirodOfficial",
    "qheimat_ounce",
)
DEFAULT_PHONE = os.getenv(
    "TELEGRAM_PHONE", os.getenv("COIN_MARKET_TELEGRAM_PHONE", "")
).strip()
SOURCE_CODES = {
    "abshdh": "MELTED_AGGREGATE",
    "naghdp": "MELTED_FLOW",
    "toofanharirodofficial": "USD_HERAT",
    "qheimat_ounce": "XAUUSD",
}
SOURCE_PARSER_CHANNELS = {
    "MELTED_AGGREGATE": "abshdh",
    "MELTED_FLOW": "NaghdP",
    "USD_HERAT": "ToofanHarirodOfficial",
    "XAUUSD": "qheimat_ounce",
}
SOURCE_PEER_IDS = {
    "MELTED_AGGREGATE": -1001491344050,
    "MELTED_FLOW": -1001209799291,
    "USD_HERAT": -1001419309254,
    "XAUUSD": -1001459105711,
}


def source_code_for_channel(username: str) -> str:
    key = username.lstrip("@").casefold()
    try:
        return SOURCE_CODES[key]
    except KeyError as exc:
        raise ValueError(f"No compact source code is configured for @{username.lstrip('@')}") from exc


def _expected_channel_id(peer_id: int) -> int:
    value = abs(int(peer_id))
    return value - 1_000_000_000_000 if value > 1_000_000_000_000 else value


async def resolve_configured_channel(
    client: object,
    requested: str,
) -> tuple[object, str, str, str]:
    """Resolve one configured Telegram channel, preferring authoritative peer_id."""

    configured_source_code: str | None = None
    try:
        configured_source_code = source_code_for_channel(requested)
    except ValueError:
        configured_source_code = None

    if configured_source_code is not None and configured_source_code in SOURCE_PEER_IDS:
        peer_id = SOURCE_PEER_IDS[configured_source_code]
        expected_id = _expected_channel_id(peer_id)
        entity = None
        peer_error: Exception | None = None
        try:
            candidate = await client.get_entity(peer_id)  # type: ignore[attr-defined]
            if getattr(candidate, "id", None) is not None:
                entity = candidate
        except Exception as exc:  # noqa: BLE001
            peer_error = exc
        if entity is None:
            try:
                entity = await client.get_entity(requested)  # type: ignore[attr-defined]
            except Exception:
                if peer_error is not None:
                    raise peer_error
                raise
            if type(entity).__module__.startswith("telethon"):
                observed_id = getattr(entity, "id", None)
                if observed_id is None or int(observed_id) != expected_id:
                    raise ValueError(
                        f"Telegram peer mismatch for @{requested.lstrip('@')}"
                    )
        parser_channel = SOURCE_PARSER_CHANNELS[configured_source_code]
        title = (
            getattr(entity, "title", None)
            or getattr(entity, "username", None)
            or parser_channel
        )
        return entity, configured_source_code, parser_channel, str(title)

    entity = await client.get_entity(requested)  # type: ignore[attr-defined]
    username = getattr(entity, "username", None) or requested
    source_code = source_code_for_channel(username)
    parser_channel = SOURCE_PARSER_CHANNELS.get(source_code, username)
    title = getattr(entity, "title", None) or parser_channel
    return entity, source_code, parser_channel, str(title)


@dataclass(frozen=True, slots=True)
class Settings:
    api_id: int
    api_hash: str
    phone: str
    db_path: Path
    session_path: Path

    @classmethod
    def from_environment(cls, *, require_credentials: bool = True) -> "Settings":
        base_dir = Path(__file__).resolve().parent.parent
        runtime_dir = Path(
            os.getenv("COIN_RATE_ESTIMATOR_RUNTIME_DIR", base_dir / "runtime")
        ).expanduser()
        credentials_path = Path(
            os.getenv(
                "TELEGRAM_CREDENTIALS_FILE",
                str(runtime_dir / "private" / "telegram_credentials.json"),
            )
        ).expanduser()
        file_credentials: dict[str, object] = {}
        if credentials_path.exists():
            if credentials_path.stat().st_mode & 0o077:
                raise ValueError("Telegram credentials file permissions must be 600")
            with credentials_path.open("r", encoding="utf-8") as handle:
                loaded = json.load(handle)
            if not isinstance(loaded, dict):
                raise ValueError("Telegram credentials file must contain a JSON object")
            file_credentials = loaded

        raw_api_id = os.getenv(
            "TELEGRAM_API_ID", os.getenv("COIN_MARKET_TELEGRAM_API_ID", "")
        ).strip()
        if not raw_api_id and file_credentials.get("api_id") is not None:
            raw_api_id = str(file_credentials["api_id"]).strip()
        api_hash = os.getenv(
            "TELEGRAM_API_HASH", os.getenv("COIN_MARKET_TELEGRAM_API_HASH", "")
        ).strip()
        if not api_hash and file_credentials.get("api_hash") is not None:
            api_hash = str(file_credentials["api_hash"]).strip()

        if require_credentials and (not raw_api_id or not api_hash):
            raise ValueError(
                "TELEGRAM_API_ID and TELEGRAM_API_HASH must be supplied as environment variables"
            )
        if require_credentials and not DEFAULT_PHONE:
            raise ValueError("TELEGRAM_PHONE must be supplied as an environment variable")

        if raw_api_id and not raw_api_id.isdigit():
            raise ValueError("TELEGRAM_API_ID must be an integer")

        api_id = int(raw_api_id) if raw_api_id else 0
        db_path = Path(
            os.getenv(
                "TELEGRAM_PRICE_DB",
                os.getenv(
                    "COIN_RATE_ESTIMATOR_MARKET_DB",
                    str(runtime_dir / "market_prices.sqlite3"),
                ),
            )
        ).expanduser()
        session_path = Path(
            os.getenv(
                "TELEGRAM_SESSION_PATH",
                str(runtime_dir / "private" / "telegram_reader"),
            )
        ).expanduser()

        return cls(
            api_id=api_id,
            api_hash=api_hash,
            phone=DEFAULT_PHONE,
            db_path=db_path,
            session_path=session_path,
        )

    @classmethod
    def with_interactive_credentials(cls) -> "Settings":
        settings = cls.from_environment(require_credentials=False)
        api_id = settings.api_id
        api_hash = settings.api_hash

        if not api_id:
            raw_api_id = input("Telegram API ID: ").strip()
            if not raw_api_id.isdigit():
                raise ValueError("Telegram API ID must be an integer")
            api_id = int(raw_api_id)
        if not api_hash:
            api_hash = getpass("Telegram API hash (hidden): ").strip()
        if not api_hash:
            raise ValueError("Telegram API hash is required")

        return cls(
            api_id=api_id,
            api_hash=api_hash,
            phone=settings.phone,
            db_path=settings.db_path,
            session_path=settings.session_path,
        )
