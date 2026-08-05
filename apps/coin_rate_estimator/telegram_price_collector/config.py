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
DEFAULT_PHONE = os.getenv("TELEGRAM_PHONE", "").strip()
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


def source_code_for_channel(username: str) -> str:
    key = username.lstrip("@").casefold()
    try:
        return SOURCE_CODES[key]
    except KeyError as exc:
        raise ValueError(f"No compact source code is configured for @{username.lstrip('@')}") from exc


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

        raw_api_id = os.getenv("TELEGRAM_API_ID", "").strip()
        if not raw_api_id and file_credentials.get("api_id") is not None:
            raw_api_id = str(file_credentials["api_id"]).strip()
        api_hash = os.getenv("TELEGRAM_API_HASH", "").strip()
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
