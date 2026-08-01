#!/usr/bin/env python3
"""Extract Telegram Desktop HTML messages aligned with market-source history."""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from zipfile import BadZipFile, ZipFile

from core.market_intelligence.group_offer_parser import enrich_records


DEFAULT_ARCHIVE = Path(os.environ.get("COIN_GROUP_EXPORT_ARCHIVE", "chat-export.zip"))
DEFAULT_MARKET_DB = Path(os.environ.get("COIN_MARKET_DB", "market_prices.sqlite3"))
DEFAULT_OUTPUT = Path(os.environ.get("COIN_GROUP_EXPORT_JSON", "group_messages.json"))

DATE_TITLE_RE = re.compile(
    r"^(\d{2})\.(\d{2})\.(\d{4}) (\d{2}):(\d{2}):(\d{2}) UTC([+-])(\d{2}):(\d{2})$"
)
MESSAGE_ID_RE = re.compile(r"^message(-?\d+)$")
REPLY_ID_RE = re.compile(r"(?:go_to_message|message)(-?\d+)")
SPACE_RE = re.compile(r"[ \t\f\v]+")
MULTI_NEWLINE_RE = re.compile(r"\n{3,}")


def clean_text(value: str) -> str:
    lines = [SPACE_RE.sub(" ", line).strip() for line in value.replace("\r", "").split("\n")]
    return MULTI_NEWLINE_RE.sub("\n\n", "\n".join(lines).strip())


def parse_export_datetime(value: str) -> datetime:
    match = DATE_TITLE_RE.fullmatch(value.strip())
    if not match:
        raise ValueError(f"Unsupported Telegram export datetime: {value!r}")
    day, month, year, hour, minute, second, sign, offset_hour, offset_minute = match.groups()
    offset = timedelta(hours=int(offset_hour), minutes=int(offset_minute))
    if sign == "-":
        offset = -offset
    return datetime(
        int(year),
        int(month),
        int(day),
        int(hour),
        int(minute),
        int(second),
        tzinfo=timezone(offset),
    )


def iso_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def html_page_order(name: str) -> tuple[int, str]:
    match = re.fullmatch(r"messages(\d*)\.html", Path(name).name)
    if not match:
        return (10**9, name)
    return (int(match.group(1) or "1"), name)


class TelegramHtmlParser(HTMLParser):
    def __init__(self, source_file: str) -> None:
        super().__init__(convert_charrefs=False)
        self.source_file = source_file
        self.messages: list[dict[str, Any]] = []
        self.current: dict[str, Any] | None = None
        self.div_stack: list[dict[str, Any]] = []
        self.message_root_depth: int | None = None

    def _append_raw(self, value: str) -> None:
        if self.current is not None:
            self.current["_raw"].append(value)

    def handle_starttag(self, tag: str, attrs_list: list[tuple[str, str | None]]) -> None:
        attrs = {key: value or "" for key, value in attrs_list}
        raw = self.get_starttag_text() or f"<{tag}>"

        if tag == "div":
            classes = set(attrs.get("class", "").split())
            message_match = MESSAGE_ID_RE.fullmatch(attrs.get("id", ""))
            if self.current is None and "message" in classes and message_match:
                self.current = {
                    "message_id": int(message_match.group(1)),
                    "message_classes": sorted(classes - {"message"}),
                    "source_html_file": self.source_file,
                    "date_title": None,
                    "from_names": [],
                    "text_parts": [],
                    "reply_to_message_id": None,
                    "media_types": [],
                    "media_titles": [],
                    "media_descriptions": [],
                    "media_statuses": [],
                    "file_references": [],
                    "_raw": [raw],
                }
                self.message_root_depth = len(self.div_stack)
                self.div_stack.append(
                    {"classes": classes, "attrs": attrs, "text": [], "in_media": False}
                )
                return

        self._append_raw(raw)
        if self.current is None:
            return

        if tag == "div":
            classes = set(attrs.get("class", "").split())
            in_media = any(context["in_media"] for context in self.div_stack) or "media" in classes
            self.div_stack.append(
                {"classes": classes, "attrs": attrs, "text": [], "in_media": in_media}
            )
            if "date" in classes and "details" in classes and attrs.get("title"):
                self.current["date_title"] = attrs["title"]
            for class_name in classes:
                if class_name.startswith("media_") and class_name not in self.current["media_types"]:
                    self.current["media_types"].append(class_name)
        elif tag == "br":
            for context in self.div_stack:
                context["text"].append("\n")
        elif tag == "a":
            href = attrs.get("href", "")
            if "reply_to" in {item for context in self.div_stack for item in context["classes"]}:
                match = REPLY_ID_RE.search(href)
                if match:
                    self.current["reply_to_message_id"] = int(match.group(1))
            if href and not href.startswith("#") and href not in self.current["file_references"]:
                self.current["file_references"].append(href)
        elif tag in {"img", "video", "audio", "source"}:
            reference = attrs.get("src", "")
            if reference and reference not in self.current["file_references"]:
                self.current["file_references"].append(reference)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in {"br", "img", "source"}:
            self.handle_endtag(tag)

    def handle_data(self, data: str) -> None:
        self._append_raw(data)
        if self.current is not None:
            for context in self.div_stack:
                context["text"].append(data)

    def handle_entityref(self, name: str) -> None:
        raw = f"&{name};"
        self._append_raw(raw)
        decoded = html.unescape(raw)
        if self.current is not None:
            for context in self.div_stack:
                context["text"].append(decoded)

    def handle_charref(self, name: str) -> None:
        raw = f"&#{name};"
        self._append_raw(raw)
        decoded = html.unescape(raw)
        if self.current is not None:
            for context in self.div_stack:
                context["text"].append(decoded)

    def handle_comment(self, data: str) -> None:
        self._append_raw(f"<!--{data}-->")

    def handle_endtag(self, tag: str) -> None:
        self._append_raw(f"</{tag}>")
        if self.current is None or tag != "div":
            return
        if not self.div_stack:
            return

        context = self.div_stack.pop()
        classes = context["classes"]
        text = clean_text("".join(context["text"]))
        if "from_name" in classes and text:
            self.current["from_names"].append(text)
        if "text" in classes and text:
            self.current["text_parts"].append(text)
        if context["in_media"]:
            if "title" in classes and "bold" in classes and text:
                self.current["media_titles"].append(text)
            if "description" in classes and text:
                self.current["media_descriptions"].append(text)
            if "status" in classes and "details" in classes and text:
                self.current["media_statuses"].append(text)

        if self.message_root_depth is not None and len(self.div_stack) == self.message_root_depth:
            self.current["raw_html"] = "".join(self.current.pop("_raw"))
            self.messages.append(self.current)
            self.current = None
            self.message_root_depth = None


def source_cutoff(market_db: Path) -> tuple[datetime, dict[str, Any]]:
    if not market_db.is_file():
        raise FileNotFoundError(f"Market database not found: {market_db}")
    connection = sqlite3.connect(f"file:{market_db.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        row = connection.execute(
            "SELECT MIN(published_at_utc) AS first_post_utc, "
            "MAX(published_at_utc) AS last_post_utc, COUNT(*) AS post_count FROM raw_posts"
        ).fetchone()
        channels = [
            dict(item)
            for item in connection.execute(
                """
                SELECT c.username,
                       MIN(r.published_at_utc) AS first_post_utc,
                       MAX(r.published_at_utc) AS last_post_utc,
                       COUNT(*) AS post_count
                FROM raw_posts r
                JOIN channels c ON c.id = r.channel_id
                GROUP BY c.id, c.username
                ORDER BY c.username
                """
            )
        ]
    finally:
        connection.close()
    if row is None or row["first_post_utc"] is None:
        raise RuntimeError("Market database contains no raw Telegram posts")
    cutoff = datetime.fromisoformat(str(row["first_post_utc"]).replace("Z", "+00:00"))
    return cutoff, {
        "market_first_post_utc": row["first_post_utc"],
        "market_last_post_utc_at_extraction": row["last_post_utc"],
        "market_post_count_at_extraction": int(row["post_count"]),
        "channels": channels,
    }


def parse_archive(archive: Path) -> tuple[list[dict[str, Any]], list[str]]:
    if not archive.is_file():
        raise FileNotFoundError(f"Telegram export archive not found: {archive}")
    messages: list[dict[str, Any]] = []
    try:
        with ZipFile(archive) as zipped:
            html_files = sorted(
                (
                    name
                    for name in zipped.namelist()
                    if re.fullmatch(r"(?:.*/)?messages\d*\.html", name)
                ),
                key=html_page_order,
            )
            if not html_files:
                raise RuntimeError("No messages*.html files found in Telegram export")
            for name in html_files:
                parser = TelegramHtmlParser(name)
                parser.feed(zipped.read(name).decode("utf-8", errors="replace"))
                parser.close()
                if parser.current is not None:
                    raise RuntimeError(f"Unclosed message block in {name}")
                messages.extend(parser.messages)
    except BadZipFile as exc:
        raise RuntimeError(f"Invalid ZIP archive: {archive}") from exc
    return messages, html_files


def normalize_messages(
    messages: list[dict[str, Any]], cutoff: datetime
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    result: list[dict[str, Any]] = []
    last_sender: str | None = None
    counts = {
        "html_message_blocks": len(messages),
        "blocks_without_timestamp": 0,
        "timestamped_before_cutoff": 0,
        "selected": 0,
        "selected_without_sender": 0,
    }
    for message in messages:
        from_names = list(dict.fromkeys(message.pop("from_names")))
        if from_names:
            sender = from_names[0]
            last_sender = sender
        else:
            sender = last_sender

        date_title = message.pop("date_title")
        if not date_title:
            counts["blocks_without_timestamp"] += 1
            continue
        local_datetime = parse_export_datetime(date_title)
        utc_datetime = local_datetime.astimezone(timezone.utc)
        if utc_datetime < cutoff:
            counts["timestamped_before_cutoff"] += 1
            continue

        text_parts = list(dict.fromkeys(message.pop("text_parts")))
        record = {
            "message_id": message.pop("message_id"),
            "date_utc": iso_utc(utc_datetime),
            "date_tehran": local_datetime.isoformat(timespec="seconds"),
            "from_name": sender,
            "text": "\n".join(text_parts).strip(),
            "reply_to_message_id": message.pop("reply_to_message_id"),
            "message_classes": message.pop("message_classes"),
            "media_types": list(dict.fromkeys(message.pop("media_types"))),
            "media_titles": list(dict.fromkeys(message.pop("media_titles"))),
            "media_descriptions": list(
                dict.fromkeys(message.pop("media_descriptions"))
            ),
            "media_statuses": list(dict.fromkeys(message.pop("media_statuses"))),
            "file_references": list(dict.fromkeys(message.pop("file_references"))),
            "source_html_file": message.pop("source_html_file"),
            "raw_html": message.pop("raw_html"),
        }
        if sender is None:
            counts["selected_without_sender"] += 1
        result.append(record)

    result.sort(key=lambda item: (item["date_utc"], item["message_id"]))
    counts["selected"] = len(result)
    return result, counts


def write_json_atomic(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    os.chmod(temporary, 0o600)
    temporary.replace(path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, default=DEFAULT_ARCHIVE)
    parser.add_argument("--market-db", type=Path, default=DEFAULT_MARKET_DB)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    cutoff, market_metadata = source_cutoff(args.market_db)
    raw_messages, html_files = parse_archive(args.archive)
    messages, counts = normalize_messages(raw_messages, cutoff)
    if not messages:
        raise RuntimeError("No group messages matched the market-source cutoff")

    ids = [item["message_id"] for item in messages]
    duplicate_ids = len(ids) - len(set(ids))
    payload = enrich_records([
        {
            "date": message["date_tehran"],
            "text": message["text"],
        }
        for message in messages
    ])
    write_json_atomic(args.output, payload)
    print(
        json.dumps(
            {
                "output": str(args.output.resolve()),
                "cutoff_utc_inclusive": iso_utc(cutoff),
                "first_selected_message_utc": messages[0]["date_utc"],
                "last_selected_message_utc": messages[-1]["date_utc"],
                "message_count": len(payload),
                "duplicate_message_ids": duplicate_ids,
                "counts": counts,
                "output_bytes": args.output.stat().st_size,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
