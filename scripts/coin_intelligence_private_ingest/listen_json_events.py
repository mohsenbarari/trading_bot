#!/usr/bin/env python3
"""Forward-only capture for the three private Telegram event channels.

Each event kind has its own channel so Telegram delivery in one stream cannot
delay the others.  A channel's anchor message is a forward-only boundary: no
message at or before it is imported.  State and deduplication are deliberately
scoped to ``(event_channel, telegram_message_id)`` because Telegram message
IDs are only unique inside one channel.
"""
from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from core.market_intelligence.telegram_collector.config import TelegramCredentials
from scripts.coin_intelligence_private_ingest.runtime_paths import (
    PRIVATE_ROOT,
    ensure_runtime_directories,
)
ALLOWED_CHANNEL_KEYS = frozenset({'offer', 'trade', 'coin'})


def load_channel_config(
    environment: Mapping[str, str] | None = None,
) -> dict[str, dict[str, int]]:
    """Load private channel identities without placing them in source control."""

    values = os.environ if environment is None else environment
    raw = str(values.get('COIN_PRIVATE_EVENT_CHANNELS_JSON', '')).strip()
    if not raw:
        raise ValueError('COIN_PRIVATE_EVENT_CHANNELS_JSON is required')
    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError('COIN_PRIVATE_EVENT_CHANNELS_JSON is invalid JSON') from exc
    if not isinstance(decoded, dict) or set(decoded) != ALLOWED_CHANNEL_KEYS:
        raise ValueError('private event channels must define offer, trade, and coin')
    result: dict[str, dict[str, int]] = {}
    seen_ids: set[int] = set()
    for key in sorted(ALLOWED_CHANNEL_KEYS):
        spec = decoded.get(key)
        if not isinstance(spec, dict):
            raise ValueError(f'invalid {key} channel specification')
        try:
            channel_id = int(spec['id'])
            anchor = int(spec['anchor_message_id'])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f'invalid {key} channel id or boundary') from exc
        if channel_id >= 0 or anchor < 0 or channel_id in seen_ids:
            raise ValueError(f'unsafe {key} channel id or boundary')
        seen_ids.add(channel_id)
        result[key] = {'id': channel_id, 'anchor_message_id': anchor}
    return result


CHANNELS: dict[str, dict[str, int]] = {}
SYNC_BATCH_SIZE = 500
SYNC_IDLE_SECONDS = 8
ROOT = PRIVATE_ROOT
STATE_PATH = ROOT / 'state.json'
SESSION_PATH = ROOT / 'telegram_listener.session'
LOG_PATH = ROOT / 'listener.log'
TEHRAN = ZoneInfo('Asia/Tehran')
SEEN_MESSAGE_IDS: set[tuple[str, int]] = set()


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def write_log(event: str, **data: object) -> None:
    payload = {'event': event, 'at_utc': utc_now().isoformat().replace('+00:00', 'Z'), **data}
    line = json.dumps(payload, ensure_ascii=False, separators=(',', ':'))
    print(line, flush=True)
    with LOG_PATH.open('a', encoding='utf-8') as handle:
        handle.write(line + '\n')
        handle.flush()
        os.fsync(handle.fileno())


def blank_channel_state(key: str) -> dict[str, object]:
    spec = CHANNELS[key]
    return {
        'channel_id': spec['id'],
        'last_message_id': spec['anchor_message_id'],
        'sync_cursor_message_id': spec['anchor_message_id'],
        'started_at_utc': None,
        'stored_count': 0,
    }


def load_state() -> dict[str, object]:
    if not STATE_PATH.exists():
        return {'schema_version': 2, 'channels': {key: blank_channel_state(key) for key in CHANNELS}}
    with STATE_PATH.open(encoding='utf-8') as handle:
        state: dict[str, Any] = json.load(handle)
    if 'channels' not in state:
        # Migrate the former single-channel state losslessly to ``offer``.
        legacy = dict(state)
        if int(legacy.get('channel_id', CHANNELS['offer']['id'])) != CHANNELS['offer']['id']:
            raise RuntimeError('legacy state belongs to an unexpected channel')
        legacy['channel_id'] = CHANNELS['offer']['id']
        legacy.setdefault('sync_cursor_message_id', legacy.get('last_message_id', CHANNELS['offer']['anchor_message_id']))
        state = {'schema_version': 2, 'channels': {'offer': legacy}}
    state['schema_version'] = 2
    channels = state['channels']
    if not isinstance(channels, dict):
        raise RuntimeError('invalid multi-channel listener state')
    for key, spec in CHANNELS.items():
        current = channels.setdefault(key, blank_channel_state(key))
        if int(current.get('channel_id', spec['id'])) != spec['id']:
            raise RuntimeError(f'state channel id does not match configured {key} channel')
        current.setdefault('sync_cursor_message_id', current.get('last_message_id', spec['anchor_message_id']))
        current.setdefault('stored_count', 0)
        current.setdefault('started_at_utc', None)
    return state


def channel_state(state: dict[str, object], key: str) -> dict[str, object]:
    return state['channels'][key]  # type: ignore[index,return-value]


def load_seen_message_ids() -> None:
    for path in ROOT.glob('events-*.jsonl'):
        with path.open(encoding='utf-8') as handle:
            for line in handle:
                if not line.strip():
                    continue
                record = json.loads(line)
                key = str(record.get('event_channel_key') or 'offer')
                SEEN_MESSAGE_IDS.add((key, int(record['telegram_message_id'])))


def save_state(state: dict[str, object]) -> None:
    temporary = STATE_PATH.with_suffix('.json.tmp')
    with temporary.open('w', encoding='utf-8') as handle:
        json.dump(state, handle, ensure_ascii=False, sort_keys=True, indent=2)
        handle.write('\n')
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, STATE_PATH)


def append_event(key: str, message_id: int, published_at: datetime, raw_text: str, state: dict[str, object]) -> bool:
    spec = CHANNELS[key]
    per_channel = channel_state(state, key)
    event_identity = (key, message_id)
    if message_id <= spec['anchor_message_id'] or event_identity in SEEN_MESSAGE_IDS:
        write_log('duplicate_or_prestart_ignored', channel=key, message_id=message_id)
        return False
    published_utc = published_at.astimezone(timezone.utc).replace(microsecond=0)
    day = published_utc.astimezone(TEHRAN).date().isoformat()
    try:
        json.loads(raw_text)
        parse_status = 'valid_json'
    except json.JSONDecodeError:
        parse_status = 'non_json_text'
    record = {
        'event_channel_key': key,
        'event_channel_id': spec['id'],
        'telegram_message_id': message_id,
        'published_at_utc': published_utc.isoformat().replace('+00:00', 'Z'),
        'published_at_tehran': published_utc.astimezone(TEHRAN).isoformat(),
        'payload_text': raw_text,
        'payload_format': parse_status,
    }
    destination = ROOT / f'events-{day}.jsonl'
    with destination.open('a', encoding='utf-8') as handle:
        handle.write(json.dumps(record, ensure_ascii=False, separators=(',', ':')) + '\n')
        handle.flush()
        os.fsync(handle.fileno())
    SEEN_MESSAGE_IDS.add(event_identity)
    per_channel['last_message_id'] = max(int(per_channel.get('last_message_id', spec['anchor_message_id'])), message_id)
    per_channel['stored_count'] = int(per_channel.get('stored_count', 0)) + 1
    per_channel['updated_at_utc'] = utc_now().isoformat().replace('+00:00', 'Z')
    save_state(state)
    write_log('message_stored', channel=key, message_id=message_id, bytes=len(raw_text.encode('utf-8')), payload_format=parse_status, file=destination.name, stored_count=per_channel['stored_count'])
    return True


async def sync_missing_messages(client: TelegramClient, key: str, entity: object, state: dict[str, object]) -> tuple[int, int]:
    per_channel = channel_state(state, key)
    started_at_raw = per_channel.get('started_at_utc')
    if not started_at_raw:
        return 0, 0
    started_at = datetime.fromisoformat(str(started_at_raw).replace('Z', '+00:00'))
    cursor = int(per_channel.get('sync_cursor_message_id', CHANNELS[key]['anchor_message_id']))
    candidates = []
    async for message in client.iter_messages(entity, min_id=cursor, reverse=True, limit=SYNC_BATCH_SIZE):
        candidates.append(message)
    recovered = 0
    for message in candidates:
        if message.date.astimezone(timezone.utc) >= started_at and append_event(key, int(message.id), message.date, getattr(message, 'message', None) or '', state):
            recovered += 1
    if candidates:
        per_channel['sync_cursor_message_id'] = max(int(message.id) for message in candidates)
        per_channel['sync_cursor_updated_at_utc'] = utc_now().isoformat().replace('+00:00', 'Z')
        save_state(state)
    write_log('sync_page_complete', channel=key, cursor_before=cursor, cursor_after=per_channel['sync_cursor_message_id'], examined=len(candidates), stored=recovered)
    return len(candidates), recovered


async def continuous_sync(client: TelegramClient, key: str, entity: object, state: dict[str, object]) -> None:
    while True:
        try:
            examined, _ = await sync_missing_messages(client, key, entity, state)
            await asyncio.sleep(0.5 if examined == SYNC_BATCH_SIZE else SYNC_IDLE_SECONDS)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            write_log('sync_page_failed', channel=key, error_type=type(exc).__name__, error=str(exc))
            await asyncio.sleep(SYNC_IDLE_SECONDS)


async def main() -> int:
    try:
        from telethon import TelegramClient, events
    except ImportError as exc:  # pragma: no cover - deployment dependency gate
        raise RuntimeError(
            'Telethon is required; install requirements-market-intelligence.txt'
        ) from exc
    global CHANNELS
    CHANNELS = load_channel_config()
    ensure_runtime_directories()
    os.chmod(ROOT, 0o700)
    state = load_state()
    load_seen_message_ids()
    save_state(state)
    credentials = TelegramCredentials.from_environment()
    client = TelegramClient(
        str(SESSION_PATH),
        credentials.api_id,
        credentials.api_hash,
        flood_sleep_threshold=60,
        sequential_updates=True,
    )
    await client.start(phone=credentials.phone)
    entities: dict[str, object] = {}
    chat_to_key: dict[int, str] = {}
    for key, spec in CHANNELS.items():
        entity = await client.get_entity(spec['id'])
        if int(getattr(entity, 'id')) != abs(spec['id']) - 1000000000000:
            raise RuntimeError(f'resolved unexpected {key} channel id')
        entities[key] = entity
        chat_to_key[spec['id']] = key
        per_channel = channel_state(state, key)
        if not per_channel.get('started_at_utc'):
            per_channel['started_at_utc'] = utc_now().isoformat().replace('+00:00', 'Z')
            save_state(state)
        await sync_missing_messages(client, key, entity, state)
        write_log('listener_ready', channel=key, title=getattr(entity, 'title', None), start_after_message_id=per_channel['last_message_id'])

    @client.on(events.NewMessage(chats=list(entities.values())))
    async def on_new_message(event: events.NewMessage.Event) -> None:
        key = chat_to_key.get(int(event.chat_id or 0))
        if key:
            message = event.message
            append_event(key, int(message.id), message.date, getattr(message, 'message', None) or '', state)

    tasks = [asyncio.create_task(continuous_sync(client, key, entity, state)) for key, entity in entities.items()]
    try:
        await client.run_until_disconnected()
    finally:
        for task in tasks: task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(asyncio.run(main()))
    except KeyboardInterrupt:
        write_log('listener_stopped')
        raise SystemExit(0)
    except Exception as exc:
        write_log('listener_failed', error_type=type(exc).__name__, error=str(exc))
        raise
