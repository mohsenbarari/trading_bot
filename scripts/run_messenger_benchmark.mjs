import { createServer, request as httpRequest } from 'node:http'
import { Buffer } from 'node:buffer'
import { mkdirSync, existsSync, readFileSync, statSync, writeFileSync, createReadStream, readdirSync } from 'node:fs'
import { createRequire } from 'node:module'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { spawnSync } from 'node:child_process'
import { gzipSync } from 'node:zlib'

const __filename = fileURLToPath(import.meta.url)
const __dirname = path.dirname(__filename)
const repoRoot = path.resolve(__dirname, '..')
const frontendRequire = createRequire(path.join(repoRoot, 'frontend', 'package.json'))
const { chromium } = frontendRequire('@playwright/test')

function parseArgs(argv) {
  const options = {
    config: 'scripts/messenger_benchmark_config.json',
    skipWarmup: false,
  }
  for (let index = 0; index < argv.length; index += 1) {
    const token = argv[index]
    if (token === '--config' && argv[index + 1]) {
      options.config = argv[index + 1]
      index += 1
      continue
    }
    if (token === '--skip-warmup') {
      options.skipWarmup = true
    }
  }
  return options
}

function resolvePath(rawPath) {
  if (path.isAbsolute(rawPath)) return rawPath
  return path.resolve(repoRoot, rawPath)
}

function loadConfig(configPath) {
  const raw = readFileSync(resolvePath(configPath), 'utf8')
  return JSON.parse(raw)
}

function resolveAppContainerName() {
  const inspect = spawnSync('docker', ['compose', 'ps', '--format', 'json', 'app'], {
    cwd: repoRoot,
    encoding: 'utf8',
  })
  if (inspect.status !== 0 || !inspect.stdout.trim()) {
    return 'trading_bot_app'
  }
  const lines = inspect.stdout
    .trim()
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean)
  for (const line of lines) {
    try {
      const parsed = JSON.parse(line)
      if (parsed.Service === 'app' && parsed.Name) return parsed.Name
    } catch {
      continue
    }
  }
  return 'trading_bot_app'
}

const appContainerName = resolveAppContainerName()

function resolveVersionCommit(version) {
  const gitRoot = resolvePath(version.git_root ?? '.')
  const commitRef = version.commit_ref ?? version.commit ?? 'HEAD'
  const inspect = spawnSync('git', ['-C', gitRoot, 'rev-parse', '--short', commitRef], {
    cwd: repoRoot,
    encoding: 'utf8',
  })
  if (inspect.status === 0 && inspect.stdout.trim()) {
    return inspect.stdout.trim()
  }
  return String(commitRef)
}

function runPythonInApp(code) {
  const command = ['exec', appContainerName, 'python', '-c', code]
  const result = spawnSync('docker', command, { cwd: repoRoot, encoding: 'utf8' })
  if (result.status !== 0) {
    throw new Error(result.stderr || result.stdout || 'docker exec python failed')
  }
  return result.stdout
}

function seedBenchmarkFixture(scenarios) {
  const fixture = scenarios.reduce((accumulator, scenario) => {
    accumulator[scenario.runner_key] = {
      conversationCount: Number(scenario.conversation_count),
      activeMessageCount: Number(scenario.active_message_count),
      fillerLength: Number(scenario.filler_length),
      scenarioId: scenario.id,
      label: scenario.label,
      surfaceIds: scenario.surface_ids,
      scenarioType: scenario.scenario_type ?? 'direct',
      searchTerm: typeof scenario.search_term === 'string' ? scenario.search_term : null,
      searchMatchEvery: Number(scenario.search_match_every ?? 0),
      switchIterations: Number(scenario.switch_iterations ?? 0),
    }
    return accumulator
  }, {})

  const python = [
    'import asyncio',
    'import base64',
    'import io',
    'import json',
    'import uuid',
    'import wave',
    'from datetime import datetime, timedelta, timezone',
    'from pathlib import Path',
    '',
    'from sqlalchemy import select',
    '',
    'from core.db import AsyncSessionLocal',
    'from core.enums import ChatMemberRole, ChatMembershipStatus, ChatType, MessageType',
    'from core.security import create_access_token, create_refresh_token',
    'from core.services.chat_room_service import ensure_mandatory_channel_rollout',
    'from core.services.session_service import hash_token',
    'from models.accountant_relation import AccountantRelation, AccountantRelationStatus',
    'from models.chat import Chat',
    'from models.chat_file import ChatFile',
    'from models.chat_member import ChatMember',
    'from models.conversation import Conversation',
    'from models.customer_relation import CustomerRelation, CustomerRelationStatus, CustomerTier',
    'from models.message import Message',
    'from models.session import Platform, UserSession',
    'from models.user import User, UserRole',
    'from models.user_block import UserBlock',
    '',
    `fixture = json.loads(${JSON.stringify(JSON.stringify(fixture))})`,
    'run_id = uuid.uuid4().hex[:10]',
    "thumbnail_data_url = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO0pW7QAAAAASUVORK5CYII='",
    'png_bytes = base64.b64decode(thumbnail_data_url.split(\',\', 1)[1])',
    "mp4_bytes = bytes.fromhex('000000206674797069736f6d0000020069736f6d69736f3261766331')",
    '',
    'def make_mobile(offset):',
    "    seed = (int(run_id[:8], 16) + offset) % 1000000000",
    "    return f\"09{seed:09d}\"",
    '',
    'def build_wav_bytes():',
    '    buffer = io.BytesIO()',
    "    with wave.open(buffer, 'wb') as wav:",
    '        wav.setnchannels(1)',
    '        wav.setsampwidth(2)',
    '        wav.setframerate(8000)',
    "        wav.writeframes(b'\\x00\\x00' * 800)",
    '    return buffer.getvalue()',
    '',
    'wav_bytes = build_wav_bytes()',
    "doc_bytes = b'Messenger benchmark document payload'",
    "media_root = Path('/tmp/messenger-benchmark-media') / run_id",
    'media_root.mkdir(parents=True, exist_ok=True)',
    '',
    'async def make_user(db, label, offset, role=UserRole.STANDARD):',
    '    user = User(',
    '        account_name=f"bench_{label}_{run_id}_{offset}",',
    '        mobile_number=make_mobile(offset),',
    '        full_name=f"Benchmark {label} {offset}",',
    "        address='Messenger benchmark fixture',",
    '        role=role,',
    '        has_bot_access=True,',
    '        max_sessions=1,',
    '    )',
    '    db.add(user)',
    '    await db.flush()',
    '    return user',
    '',
    'async def attach_session(db, user, label):',
    '    refresh_token = create_refresh_token(subject=str(user.id))',
    '    session = UserSession(',
    '        user_id=user.id,',
    '        device_name=f"Messenger Benchmark {label}",',
    "        device_ip='127.0.0.1',",
    '        platform=Platform.WEB,',
    '        refresh_token_hash=hash_token(refresh_token),',
    '        is_primary=True,',
    '        is_active=True,',
    '        expires_at=None,',
    '    )',
    '    db.add(session)',
    '    await db.flush()',
    '    access_token = create_access_token(subject=str(user.id), session_id=str(session.id))',
    '    return access_token, refresh_token',
    '',
    'async def create_chat_file(db, uploader, *, label, file_name, mime_type, data, thumbnail=None):',
    '    file_path = media_root / f"{label}_{uuid.uuid4().hex}_{file_name}"',
    '    file_path.write_bytes(data)',
    '    chat_file = ChatFile(',
    '        uploader_id=uploader.id,',
    '        s3_key=str(file_path),',
    '        file_name=file_name,',
    '        mime_type=mime_type,',
    '        size=len(data),',
    '        thumbnail=thumbnail,',
    '    )',
    '    db.add(chat_file)',
    '    await db.flush()',
    '    return chat_file',
    '',
    'def build_message_content(label, index, filler, search_term=None, search_match_every=0):',
    '    content = f"{label} benchmark message {index + 1:04d} {filler}"',
    '    if search_term and search_match_every > 0 and index % search_match_every == 0:',
    '        content = f"{content} {search_term}"',
    '    return content',
    '',
    'async def mark_room_members_read(db, chat_id, user_ids, message_id, message_created_at):',
    '    if not user_ids:',
    '        return',
    '    result = await db.execute(select(ChatMember).where(ChatMember.chat_id == chat_id, ChatMember.user_id.in_(list(user_ids))))',
    '    for member in result.scalars().all():',
    '        member.last_read_message_id = message_id',
    '        member.last_read_at = message_created_at',
    '        member.updated_at = message_created_at',
    '',
    'async def append_room_messages(db, *, chat, senders, label, filler_length, minute_offset, message_count, search_term=None, search_match_every=0, mark_read_user_ids=None):',
    '    if message_count <= 0 or not senders:',
    '        return None',
    '    created_base = datetime.now(timezone.utc) - timedelta(days=1, minutes=minute_offset)',
    "    filler = 'y' * filler_length",
    '    messages = []',
    '    for index in range(message_count):',
    '        sender = senders[index % len(senders)]',
    '        content = build_message_content(label, index, filler, search_term=search_term, search_match_every=search_match_every)',
    '        messages.append(Message(',
    '            chat_id=chat.id,',
    '            sender_id=sender.id,',
    '            receiver_id=sender.id,',
    '            content=content,',
    '            message_type=MessageType.TEXT,',
    '            is_read=True,',
    '            created_at=created_base + timedelta(seconds=index * 15),',
    '        ))',
    '    db.add_all(messages)',
    '    await db.flush()',
    '    last_message = messages[-1]',
    '    chat.last_message_id = last_message.id',
    '    chat.last_message_at = last_message.created_at',
    '    chat.updated_at = last_message.created_at',
    '    await mark_room_members_read(db, chat.id, mark_read_user_ids or [], last_message.id, last_message.created_at)',
    '    return last_message',
    '',
    'async def create_thread(db, actor, peer, message_count, label, filler_length, minute_offset, search_term=None, search_match_every=0):',
    '    user1_id, user2_id = sorted([actor.id, peer.id])',
    '    conversation = Conversation(',
    '        user1_id=user1_id,',
    '        user2_id=user2_id,',
    '        unread_count_user1=0,',
    '        unread_count_user2=0,',
    '    )',
    '    db.add(conversation)',
    '    await db.flush()',
    '',
    '    created_base = datetime.now(timezone.utc) - timedelta(days=2, minutes=minute_offset)',
    "    filler = 'x' * filler_length",
    '    messages = []',
    '    for index in range(message_count):',
    '        sender = actor if index % 2 else peer',
    '        receiver = peer if sender.id == actor.id else actor',
    '        content = build_message_content(label, index, filler, search_term=search_term, search_match_every=search_match_every)',
    '        messages.append(Message(',
    '            sender_id=sender.id,',
    '            receiver_id=receiver.id,',
    '            content=content,',
    '            message_type=MessageType.TEXT,',
    '            is_read=True,',
    '            created_at=created_base + timedelta(seconds=index * 8),',
    '        ))',
    '    db.add_all(messages)',
    '    await db.flush()',
    '    conversation.last_message_id = messages[-1].id',
    '    conversation.last_message_at = messages[-1].created_at',
    '    return conversation, messages[-1]',
    '',
    'async def append_direct_media_mix(db, actor, peer, label, minute_offset):',
    '    image_file = await create_chat_file(db, actor, label=f"{label}_image", file_name="bench-image.png", mime_type="image/png", data=png_bytes, thumbnail=thumbnail_data_url)',
    '    video_file = await create_chat_file(db, actor, label=f"{label}_video", file_name="bench-video.mp4", mime_type="video/mp4", data=mp4_bytes, thumbnail=thumbnail_data_url)',
    '    voice_file = await create_chat_file(db, actor, label=f"{label}_voice", file_name="bench-voice.wav", mime_type="audio/wav", data=wav_bytes)',
    '    document_file = await create_chat_file(db, actor, label=f"{label}_document", file_name="bench-notes.txt", mime_type="text/plain", data=doc_bytes)',
    '    snapshot_file = await create_chat_file(db, actor, label=f"{label}_snapshot", file_name="bench-map.png", mime_type="image/png", data=png_bytes, thumbnail=thumbnail_data_url)',
    '    created_base = datetime.now(timezone.utc) - timedelta(hours=8, minutes=minute_offset)',
    '    album_id = f"{label}_album_{run_id}"',
    '    rows = [',
    '        (peer, actor, MessageType.IMAGE, json.dumps({"file_id": image_file.id, "file_name": image_file.file_name, "thumbnail": thumbnail_data_url, "width": 720, "height": 960, "album_id": album_id, "album_index": 0})),',
    '        (actor, peer, MessageType.VIDEO, json.dumps({"file_id": video_file.id, "file_name": video_file.file_name, "thumbnail": thumbnail_data_url, "width": 1280, "height": 720, "album_id": album_id, "album_index": 1})),',
    '        (peer, actor, MessageType.DOCUMENT, json.dumps({"file_id": document_file.id, "file_name": document_file.file_name, "mime_type": document_file.mime_type, "size": document_file.size})),',
    '        (actor, peer, MessageType.VOICE, json.dumps({"file_id": voice_file.id, "file_name": voice_file.file_name, "mime_type": voice_file.mime_type, "size": voice_file.size, "durationMs": 1000})),',
    '        (peer, actor, MessageType.LOCATION, json.dumps({"lat": 35.6892, "lng": 51.389, "snapshot_id": snapshot_file.id})),',
    '    ]',
    '    messages = []',
    '    for index, (sender, receiver, message_type, content) in enumerate(rows):',
    '        messages.append(Message(',
    '            sender_id=sender.id,',
    '            receiver_id=receiver.id,',
    '            content=content,',
    '            message_type=message_type,',
    '            is_read=True,',
    '            created_at=created_base + timedelta(seconds=index * 20),',
    '        ))',
    '    db.add_all(messages)',
    '    await db.flush()',
    '    return messages[-1]',
    '',
    'async def create_room(db, *, chat_type, title, members, admin_ids, label, filler_length, minute_offset, message_count=1, max_members=None, is_system=False, is_mandatory=False, search_term=None, search_match_every=0):',
    '    creator = members[0] if members else None',
    '    now = datetime.now(timezone.utc)',
    '    chat = Chat(',
    '        type=chat_type,',
    '        title=title,',
    "        description='Messenger benchmark room',",
    '        created_by_id=creator.id if creator is not None else None,',
    '        is_system=is_system,',
    '        is_mandatory=is_mandatory,',
    '        max_members=max_members,',
    '        updated_at=now,',
    '    )',
    '    db.add(chat)',
    '    await db.flush()',
    '    seen_user_ids = set()',
    '    member_ids = []',
    '    for member in members:',
    '        if member.id in seen_user_ids:',
    '            continue',
    '        seen_user_ids.add(member.id)',
    '        member_ids.append(member.id)',
    '        db.add(ChatMember(',
    '            chat_id=chat.id,',
    '            user_id=member.id,',
    '            role=ChatMemberRole.ADMIN if member.id in admin_ids else ChatMemberRole.MEMBER,',
    '            membership_status=ChatMembershipStatus.ACTIVE,',
    '            joined_at=now,',
    '            left_at=None,',
    '            updated_at=now,',
    '        ))',
    '    await db.flush()',
    '    senders = [member for member in members if member.id in admin_ids] or list(members)',
    '    last_message = await append_room_messages(',
    '        db,',
    '        chat=chat,',
    '        senders=senders,',
    '        label=label,',
    '        filler_length=filler_length,',
    '        minute_offset=minute_offset,',
    '        message_count=message_count,',
    '        search_term=search_term,',
    '        search_match_every=search_match_every,',
    '        mark_read_user_ids=member_ids,',
    '    )',
    '    return chat, last_message',
    '',
    'def room_target(chat, kind):',
    '    return {',
    "        'kind': kind,",
    "        'routeUserId': -chat.id,",
    "        'title': chat.title,",
    '    }',
    '',
    'async def seed_direct_scenario(db, scenario_key, scenario, offset):',
    '    actor = await make_user(db, f"{scenario_key}_actor", offset)',
    '    access_token, refresh_token = await attach_session(db, actor, scenario_key)',
    '    active_peer = await make_user(db, f"{scenario_key}_active_peer", offset + 1)',
    '    await create_thread(',
    '        db,',
    '        actor,',
    '        active_peer,',
    '        int(scenario["activeMessageCount"]),',
    '        scenario_key,',
    '        int(scenario["fillerLength"]),',
    '        offset,',
    '        search_term=scenario.get("searchTerm"),',
    '        search_match_every=int(scenario.get("searchMatchEvery") or 0),',
    '    )',
    '    for peer_index in range(max(0, int(scenario["conversationCount"]) - 1)):',
    '        peer = await make_user(db, f"{scenario_key}_peer_{peer_index}", offset + 10 + peer_index)',
    '        await create_thread(',
    '            db,',
    '            actor,',
    '            peer,',
    '            1,',
    '            f"{scenario_key}_list_{peer_index}",',
    '            10,',
    '            offset + 100 + peer_index,',
    '        )',
    '    return {',
    "        'actor': {",
    "            'userId': actor.id,",
    "            'accountName': actor.account_name,",
    "            'accessToken': access_token,",
    "            'refreshToken': refresh_token,",
    '        },',
    "        'scenarioType': scenario.get('scenarioType', 'direct'),",
    "        'activeTargetId': active_peer.id,",
    "        'activeTargetName': active_peer.account_name,",
    "        'activeMessagesApiPath': f'/api/chat/messages/{active_peer.id}',",
    "        'conversationCount': int(scenario['conversationCount']),",
    "        'activeMessageCount': int(scenario['activeMessageCount']),",
    "        'searchTerm': scenario.get('searchTerm'),",
    '    }',
    '',
    'async def seed_media_direct_scenario(db, scenario_key, scenario, offset):',
    '    actor = await make_user(db, f"{scenario_key}_actor", offset)',
    '    access_token, refresh_token = await attach_session(db, actor, scenario_key)',
    '    active_peer = await make_user(db, f"{scenario_key}_active_peer", offset + 1)',
    '    base_message_count = max(16, int(scenario["activeMessageCount"]) - 5)',
    '    conversation, _ = await create_thread(',
    '        db,',
    '        actor,',
    '        active_peer,',
    '        base_message_count,',
    '        scenario_key,',
    '        int(scenario["fillerLength"]),',
    '        offset,',
    '    )',
    '    media_last_message = await append_direct_media_mix(db, actor, active_peer, scenario_key, offset + 15)',
    '    conversation.last_message_id = media_last_message.id',
    '    conversation.last_message_at = media_last_message.created_at',
    '    for peer_index in range(max(0, int(scenario["conversationCount"]) - 1)):',
    '        peer = await make_user(db, f"{scenario_key}_peer_{peer_index}", offset + 10 + peer_index)',
    '        await create_thread(',
    '            db,',
    '            actor,',
    '            peer,',
    '            1,',
    '            f"{scenario_key}_list_{peer_index}",',
    '            10,',
    '            offset + 100 + peer_index,',
    '        )',
    '    return {',
    "        'actor': {",
    "            'userId': actor.id,",
    "            'accountName': actor.account_name,",
    "            'accessToken': access_token,",
    "            'refreshToken': refresh_token,",
    '        },',
    "        'scenarioType': 'media_direct',",
    "        'activeTargetId': active_peer.id,",
    "        'activeTargetName': active_peer.account_name,",
    "        'activeMessagesApiPath': f'/api/chat/messages/{active_peer.id}',",
    "        'conversationCount': int(scenario['conversationCount']),",
    "        'activeMessageCount': int(scenario['activeMessageCount']),",
    '    }',
    '',
    'async def seed_boot_empty_scenario(db, scenario_key, scenario, offset):',
    '    actor = await make_user(db, f"{scenario_key}_actor", offset)',
    '    access_token, refresh_token = await attach_session(db, actor, scenario_key)',
    '    admin = await make_user(db, f"{scenario_key}_admin", offset + 1, role=UserRole.SUPER_ADMIN)',
    '    await attach_session(db, admin, f"{scenario_key}_admin")',
    '    mandatory_chat = await ensure_mandatory_channel_rollout(db, users=[admin, actor])',
    '    await append_room_messages(',
    '        db,',
    '        chat=mandatory_chat,',
    '        senders=[admin],',
    '        label=f"{scenario_key}_mandatory",',
    '        filler_length=int(scenario["fillerLength"]),',
    '        minute_offset=offset,',
    '        message_count=int(scenario["activeMessageCount"]),',
    '        mark_read_user_ids=[admin.id, actor.id],',
    '    )',
    '    return {',
    "        'actor': {",
    "            'userId': actor.id,",
    "            'accountName': actor.account_name,",
    "            'accessToken': access_token,",
    "            'refreshToken': refresh_token,",
    '        },',
    "        'scenarioType': 'boot_empty',",
    "        'activeTargetId': -mandatory_chat.id,",
    "        'activeTargetName': mandatory_chat.title,",
    "        'activeMessagesApiPath': f'/api/chat/rooms/{mandatory_chat.id}/messages',",
    "        'conversationCount': int(scenario['conversationCount']),",
    "        'activeMessageCount': int(scenario['activeMessageCount']),",
    '    }',
    '',
    'async def seed_room_mix_scenario(db, scenario_key, scenario, offset):',
    '    actor = await make_user(db, f"{scenario_key}_actor", offset)',
    '    access_token, refresh_token = await attach_session(db, actor, scenario_key)',
    '    active_peer = await make_user(db, f"{scenario_key}_active_peer", offset + 1)',
    '    active_peer_access, _ = await attach_session(db, active_peer, f"{scenario_key}_active_peer")',
    '    await create_thread(',
    '        db,',
    '        actor,',
    '        active_peer,',
    '        int(scenario["activeMessageCount"]),',
    '        scenario_key,',
    '        int(scenario["fillerLength"]),',
    '        offset,',
    '    )',
    '    mandatory_admin = await make_user(db, f"{scenario_key}_mandatory_admin", offset + 2, role=UserRole.SUPER_ADMIN)',
    '    await attach_session(db, mandatory_admin, f"{scenario_key}_mandatory_admin")',
    '    mandatory_chat = await ensure_mandatory_channel_rollout(db, users=[mandatory_admin, actor])',
    '    await append_room_messages(',
    '        db,',
    '        chat=mandatory_chat,',
    '        senders=[mandatory_admin],',
    '        label=f"{scenario_key}_mandatory",',
    '        filler_length=10,',
    '        minute_offset=offset + 10,',
    '        message_count=2,',
    '        mark_read_user_ids=[mandatory_admin.id, actor.id],',
    '    )',
    '    group_sender = await make_user(db, f"{scenario_key}_group_sender", offset + 3)',
    '    group_sender_access, _ = await attach_session(db, group_sender, f"{scenario_key}_group_sender")',
    '    group_room, _ = await create_room(',
    '        db,',
    '        chat_type=ChatType.GROUP,',
    '        title=f"{scenario_key} group room",',
    '        members=[actor, active_peer, group_sender],',
    '        admin_ids={actor.id, group_sender.id},',
    '        label=f"{scenario_key}_group",',
    '        filler_length=12,',
    '        minute_offset=offset + 20,',
    '        message_count=5,',
    '        max_members=200,',
    '    )',
    '    channel_admin = await make_user(db, f"{scenario_key}_channel_admin", offset + 4)',
    '    channel_admin_access, _ = await attach_session(db, channel_admin, f"{scenario_key}_channel_admin")',
    '    channel_room, _ = await create_room(',
    '        db,',
    '        chat_type=ChatType.CHANNEL,',
    '        title=f"{scenario_key} channel room",',
    '        members=[channel_admin, actor, active_peer],',
    '        admin_ids={channel_admin.id},',
    '        label=f"{scenario_key}_channel",',
    '        filler_length=12,',
    '        minute_offset=offset + 30,',
    '        message_count=4,',
    '    )',
    '    base_conversation_count = 4',
    '    for peer_index in range(max(0, int(scenario["conversationCount"]) - base_conversation_count)):',
    '        peer = await make_user(db, f"{scenario_key}_peer_{peer_index}", offset + 50 + peer_index)',
    '        await create_thread(',
    '            db,',
    '            actor,',
    '            peer,',
    '            1,',
    '            f"{scenario_key}_list_{peer_index}",',
    '            10,',
    '            offset + 100 + peer_index,',
    '        )',
    '    return {',
    "        'actor': {",
    "            'userId': actor.id,",
    "            'accountName': actor.account_name,",
    "            'accessToken': access_token,",
    "            'refreshToken': refresh_token,",
    '        },',
    "        'scenarioType': scenario.get('scenarioType', 'realtime_stress'),",
    "        'activeTargetId': active_peer.id,",
    "        'activeTargetName': active_peer.account_name,",
    "        'activeMessagesApiPath': f'/api/chat/messages/{active_peer.id}',",
    "        'conversationCount': int(scenario['conversationCount']),",
    "        'activeMessageCount': int(scenario['activeMessageCount']),",
    "        'realtimeBurst': {",
    "            'directSenderToken': active_peer_access,",
    "            'groupSenderToken': group_sender_access,",
    "            'groupChatId': group_room.id,",
    "            'channelSenderToken': channel_admin_access,",
    "            'channelChatId': channel_room.id,",
    '        },',
    "        'switchTargets': [",
    "            {'kind': 'direct', 'routeUserId': active_peer.id, 'title': active_peer.account_name},",
    "            room_target(group_room, 'group'),",
    "            room_target(channel_room, 'channel'),",
    "            room_target(mandatory_chat, 'channel'),",
    '        ],',
    '    }',
    '',
    'async def seed_group_matrix_scenario(db, scenario_key, scenario, offset):',
    '    actor = await make_user(db, f"{scenario_key}_actor", offset)',
    '    access_token, refresh_token = await attach_session(db, actor, scenario_key)',
    '    active_peer = await make_user(db, f"{scenario_key}_active_peer", offset + 1)',
    '    active_peer_access, _ = await attach_session(db, active_peer, f"{scenario_key}_active_peer")',
    '    await create_thread(db, actor, active_peer, 8, f"{scenario_key}_direct", 10, offset)',
    '    group_sender = await make_user(db, f"{scenario_key}_group_sender", offset + 2)',
    '    group_sender_access, _ = await attach_session(db, group_sender, f"{scenario_key}_group_sender")',
    '    writable_group, _ = await create_room(',
    '        db,',
    '        chat_type=ChatType.GROUP,',
    '        title=f"{scenario_key} writable group",',
    '        members=[actor, active_peer, group_sender],',
    '        admin_ids={actor.id},',
    '        label=f"{scenario_key}_writable_group",',
    '        filler_length=int(scenario["fillerLength"]),',
    '        minute_offset=offset + 20,',
    '        message_count=int(scenario["activeMessageCount"]),',
    '        max_members=200,',
    '    )',
    '    readonly_admin = await make_user(db, f"{scenario_key}_readonly_admin", offset + 3)',
    '    await attach_session(db, readonly_admin, f"{scenario_key}_readonly_admin")',
    '    readonly_group, _ = await create_room(',
    '        db,',
    '        chat_type=ChatType.GROUP,',
    '        title=f"{scenario_key} readonly group",',
    '        members=[readonly_admin, actor, active_peer],',
    '        admin_ids={readonly_admin.id},',
    '        label=f"{scenario_key}_readonly_group",',
    '        filler_length=12,',
    '        minute_offset=offset + 40,',
    '        message_count=8,',
    '        max_members=200,',
    '    )',
    '    base_conversation_count = 3',
    '    for peer_index in range(max(0, int(scenario["conversationCount"]) - base_conversation_count)):',
    '        peer = await make_user(db, f"{scenario_key}_peer_{peer_index}", offset + 50 + peer_index)',
    '        await create_thread(db, actor, peer, 1, f"{scenario_key}_list_{peer_index}", 10, offset + 100 + peer_index)',
    '    return {',
    "        'actor': {",
    "            'userId': actor.id,",
    "            'accountName': actor.account_name,",
    "            'accessToken': access_token,",
    "            'refreshToken': refresh_token,",
    '        },',
    "        'scenarioType': 'group_matrix',",
    "        'activeTargetId': -writable_group.id,",
    "        'activeTargetName': writable_group.title,",
    "        'activeMessagesApiPath': f'/api/chat/rooms/{writable_group.id}/messages',",
    "        'conversationCount': int(scenario['conversationCount']),",
    "        'activeMessageCount': int(scenario['activeMessageCount']),",
    "        'realtimeBurst': {",
    "            'directSenderToken': active_peer_access,",
    "            'groupSenderToken': group_sender_access,",
    "            'groupChatId': writable_group.id,",
    '        },',
    "        'switchTargets': [",
    "            room_target(writable_group, 'group'),",
    "            room_target(readonly_group, 'group'),",
    "            {'kind': 'direct', 'routeUserId': active_peer.id, 'title': active_peer.account_name},",
    '        ],',
    '    }',
    '',
    'async def seed_channel_matrix_scenario(db, scenario_key, scenario, offset):',
    '    actor = await make_user(db, f"{scenario_key}_actor", offset)',
    '    access_token, refresh_token = await attach_session(db, actor, scenario_key)',
    '    active_peer = await make_user(db, f"{scenario_key}_active_peer", offset + 1)',
    '    active_peer_access, _ = await attach_session(db, active_peer, f"{scenario_key}_active_peer")',
    '    await create_thread(db, actor, active_peer, 8, f"{scenario_key}_direct", 10, offset)',
    '    channel_admin = await make_user(db, f"{scenario_key}_channel_admin", offset + 2)',
    '    channel_admin_access, _ = await attach_session(db, channel_admin, f"{scenario_key}_channel_admin")',
    '    writable_channel, _ = await create_room(',
    '        db,',
    '        chat_type=ChatType.CHANNEL,',
    '        title=f"{scenario_key} writable channel",',
    '        members=[actor, active_peer, channel_admin],',
    '        admin_ids={actor.id, channel_admin.id},',
    '        label=f"{scenario_key}_writable_channel",',
    '        filler_length=int(scenario["fillerLength"]),',
    '        minute_offset=offset + 20,',
    '        message_count=int(scenario["activeMessageCount"]),',
    '    )',
    '    mandatory_admin = await make_user(db, f"{scenario_key}_mandatory_admin", offset + 3, role=UserRole.SUPER_ADMIN)',
    '    await attach_session(db, mandatory_admin, f"{scenario_key}_mandatory_admin")',
    '    mandatory_chat = await ensure_mandatory_channel_rollout(db, users=[mandatory_admin, actor])',
    '    await append_room_messages(',
    '        db,',
    '        chat=mandatory_chat,',
    '        senders=[mandatory_admin],',
    '        label=f"{scenario_key}_mandatory",',
    '        filler_length=10,',
    '        minute_offset=offset + 40,',
    '        message_count=6,',
    '        mark_read_user_ids=[mandatory_admin.id, actor.id],',
    '    )',
    '    base_conversation_count = 3',
    '    for peer_index in range(max(0, int(scenario["conversationCount"]) - base_conversation_count)):',
    '        peer = await make_user(db, f"{scenario_key}_peer_{peer_index}", offset + 50 + peer_index)',
    '        await create_thread(db, actor, peer, 1, f"{scenario_key}_list_{peer_index}", 10, offset + 100 + peer_index)',
    '    return {',
    "        'actor': {",
    "            'userId': actor.id,",
    "            'accountName': actor.account_name,",
    "            'accessToken': access_token,",
    "            'refreshToken': refresh_token,",
    '        },',
    "        'scenarioType': 'channel_matrix',",
    "        'activeTargetId': -writable_channel.id,",
    "        'activeTargetName': writable_channel.title,",
    "        'activeMessagesApiPath': f'/api/chat/rooms/{writable_channel.id}/messages',",
    "        'conversationCount': int(scenario['conversationCount']),",
    "        'activeMessageCount': int(scenario['activeMessageCount']),",
    "        'realtimeBurst': {",
    "            'directSenderToken': active_peer_access,",
    "            'channelSenderToken': channel_admin_access,",
    "            'channelChatId': writable_channel.id,",
    '        },',
    "        'switchTargets': [",
    "            room_target(writable_channel, 'channel'),",
    "            room_target(mandatory_chat, 'channel'),",
    "            {'kind': 'direct', 'routeUserId': active_peer.id, 'title': active_peer.account_name},",
    '        ],',
    '    }',
    '',
    'async def seed_identity_matrix_scenario(db, scenario_key, scenario, offset):',
    '    actor = await make_user(db, f"{scenario_key}_actor", offset, role=UserRole.SUPER_ADMIN)',
    '    access_token, refresh_token = await attach_session(db, actor, scenario_key)',
    '    owner = await make_user(db, f"{scenario_key}_owner", offset + 1)',
    '    owner_access, _ = await attach_session(db, owner, f"{scenario_key}_owner")',
    '    accountant = await make_user(db, f"{scenario_key}_accountant", offset + 2)',
    '    accountant_access, _ = await attach_session(db, accountant, f"{scenario_key}_accountant")',
    '    customer = await make_user(db, f"{scenario_key}_customer", offset + 3)',
    '    await attach_session(db, customer, f"{scenario_key}_customer")',
    '    blocked_peer = await make_user(db, f"{scenario_key}_blocked", offset + 4)',
    '    relation_expires_at = datetime.now(timezone.utc) + timedelta(days=30)',
    '    db.add(AccountantRelation(',
    '        owner_user_id=owner.id,',
    '        accountant_user_id=accountant.id,',
    '        created_by_user_id=actor.id,',
    '        invitation_token=f"{scenario_key}_acct_{run_id}",',
    '        global_account_name=accountant.account_name,',
    '        relation_display_name=f"{owner.account_name} accountant",',
    '        duty_description="Benchmark accountant",',
    '        mobile_number=accountant.mobile_number,',
    '        status=AccountantRelationStatus.ACTIVE,',
    '        expires_at=relation_expires_at,',
    '        activated_at=datetime.now(timezone.utc),',
    '    ))',
    '    db.add(CustomerRelation(',
    '        owner_user_id=owner.id,',
    '        customer_user_id=customer.id,',
    '        created_by_user_id=actor.id,',
    '        invitation_token=f"{scenario_key}_cust_{run_id}",',
    '        management_name=f"{owner.account_name} customer",',
    '        customer_tier=CustomerTier.TIER_1,',
    '        commission_rate=0,',
    '        status=CustomerRelationStatus.ACTIVE,',
    '        expires_at=relation_expires_at,',
    '        activated_at=datetime.now(timezone.utc),',
    '    ))',
    '    db.add(UserBlock(blocker_id=blocked_peer.id, blocked_id=actor.id))',
    '    await db.flush()',
    '    await create_thread(',
    '        db,',
    '        actor,',
    '        accountant,',
    '        int(scenario["activeMessageCount"]),',
    '        scenario_key,',
    '        int(scenario["fillerLength"]),',
    '        offset,',
    '    )',
    '    await create_thread(db, actor, owner, 10, f"{scenario_key}_owner", 12, offset + 20)',
    '    await create_thread(db, actor, customer, 8, f"{scenario_key}_customer", 12, offset + 40)',
    '    await create_thread(db, actor, blocked_peer, 6, f"{scenario_key}_blocked", 10, offset + 60)',
    '    group_room, _ = await create_room(',
    '        db,',
    '        chat_type=ChatType.GROUP,',
    '        title=f"{scenario_key} identity group",',
    '        members=[actor, owner, accountant, customer],',
    '        admin_ids={actor.id, owner.id},',
    '        label=f"{scenario_key}_group",',
    '        filler_length=12,',
    '        minute_offset=offset + 80,',
    '        message_count=10,',
    '        max_members=50,',
    '    )',
    '    channel_room, _ = await create_room(',
    '        db,',
    '        chat_type=ChatType.CHANNEL,',
    '        title=f"{scenario_key} identity channel",',
    '        members=[actor, owner, accountant],',
    '        admin_ids={actor.id, owner.id},',
    '        label=f"{scenario_key}_channel",',
    '        filler_length=12,',
    '        minute_offset=offset + 100,',
    '        message_count=8,',
    '    )',
    '    base_conversation_count = 6',
    '    for peer_index in range(max(0, int(scenario["conversationCount"]) - base_conversation_count)):',
    '        peer = await make_user(db, f"{scenario_key}_peer_{peer_index}", offset + 140 + peer_index)',
    '        await create_thread(db, actor, peer, 1, f"{scenario_key}_list_{peer_index}", 10, offset + 200 + peer_index)',
    '    return {',
    "        'actor': {",
    "            'userId': actor.id,",
    "            'accountName': actor.account_name,",
    "            'accessToken': access_token,",
    "            'refreshToken': refresh_token,",
    '        },',
    "        'scenarioType': 'identity_matrix',",
    "        'activeTargetId': accountant.id,",
    "        'activeTargetName': accountant.account_name,",
    "        'activeMessagesApiPath': f'/api/chat/messages/{accountant.id}',",
    "        'conversationCount': int(scenario['conversationCount']),",
    "        'activeMessageCount': int(scenario['activeMessageCount']),",
    "        'realtimeBurst': {",
    "            'directSenderToken': accountant_access,",
    "            'groupSenderToken': owner_access,",
    "            'groupChatId': group_room.id,",
    "            'channelSenderToken': owner_access,",
    "            'channelChatId': channel_room.id,",
    '        },',
    "        'switchTargets': [",
    "            {'kind': 'direct', 'routeUserId': accountant.id, 'title': accountant.account_name},",
    "            {'kind': 'direct', 'routeUserId': owner.id, 'title': owner.account_name},",
    "            {'kind': 'direct', 'routeUserId': customer.id, 'title': customer.account_name},",
    "            room_target(group_room, 'group'),",
    "            room_target(channel_room, 'channel'),",
    '        ],',
    "        'identityProbe': {",
    "            'expectedProfileUserId': owner.id,",
    "            'expectedProfileLabel': owner.account_name,",
    '        },',
    '    }',
    '',
    'async def seed_upload_persistence_scenario(db, scenario_key, scenario, offset):',
    '    actor = await make_user(db, f"{scenario_key}_actor", offset)',
    '    access_token, refresh_token = await attach_session(db, actor, scenario_key)',
    '    active_peer = await make_user(db, f"{scenario_key}_active_peer", offset + 1)',
    '    other_peer = await make_user(db, f"{scenario_key}_other_peer", offset + 2)',
    '    other_peer_access, _ = await attach_session(db, other_peer, f"{scenario_key}_other_peer")',
    '    base_message_count = max(12, int(scenario["activeMessageCount"]) - 1)',
    '    conversation, _ = await create_thread(',
    '        db,',
    '        actor,',
    '        active_peer,',
    '        base_message_count,',
    '        scenario_key,',
    '        int(scenario["fillerLength"]),',
    '        offset,',
    '    )',
    '    document_file = await create_chat_file(',
    '        db,',
    '        active_peer,',
    '        label=f"{scenario_key}_document",',
    '        file_name=f"{scenario_key}-{run_id}-resume.txt",',
    '        mime_type="text/plain",',
    '        data=doc_bytes,',
    '    )',
    '    document_message = Message(',
    '        sender_id=active_peer.id,',
    '        receiver_id=actor.id,',
    '        content=json.dumps({',
    '            "file_id": document_file.id,',
    '            "file_name": document_file.file_name,',
    '            "mime_type": document_file.mime_type,',
    '            "size": document_file.size,',
    '        }),',
    '        message_type=MessageType.DOCUMENT,',
    '        is_read=True,',
    '        created_at=datetime.now(timezone.utc) - timedelta(hours=6, minutes=offset),',
    '    )',
    '    db.add(document_message)',
    '    await db.flush()',
    '    conversation.last_message_id = document_message.id',
    '    conversation.last_message_at = document_message.created_at',
    '    await create_thread(db, actor, other_peer, 6, f"{scenario_key}_switch", 12, offset + 30)',
    '    base_conversation_count = 2',
    '    for peer_index in range(max(0, int(scenario["conversationCount"]) - base_conversation_count)):',
    '        peer = await make_user(db, f"{scenario_key}_peer_{peer_index}", offset + 60 + peer_index)',
    '        await create_thread(db, actor, peer, 1, f"{scenario_key}_list_{peer_index}", 10, offset + 100 + peer_index)',
    '    return {',
    "        'actor': {",
    "            'userId': actor.id,",
    "            'accountName': actor.account_name,",
    "            'accessToken': access_token,",
    "            'refreshToken': refresh_token,",
    '        },',
    "        'scenarioType': 'upload_persistence',",
    "        'activeTargetId': active_peer.id,",
    "        'activeTargetName': active_peer.account_name,",
    "        'activeMessagesApiPath': f'/api/chat/messages/{active_peer.id}',",
    "        'conversationCount': int(scenario['conversationCount']),",
    "        'activeMessageCount': int(scenario['activeMessageCount']),",
    "        'realtimeBurst': {",
    "            'directSenderToken': other_peer_access,",
    '        },',
    "        'switchTargets': [",
    "            {'kind': 'direct', 'routeUserId': active_peer.id, 'title': active_peer.account_name},",
    "            {'kind': 'direct', 'routeUserId': other_peer.id, 'title': other_peer.account_name},",
    '        ],',
    "        'persistenceProbe': {",
    "            'documentFileName': document_file.file_name,",
    "            'switchTarget': {'kind': 'direct', 'routeUserId': other_peer.id, 'title': other_peer.account_name},",
    '        },',
    '    }',
    '',
    'async def seed_scenario(db, scenario_key, scenario, offset):',
    '    scenario_type = scenario.get("scenarioType") or "direct"',
    '    if scenario_type == "media_direct":',
    '        return await seed_media_direct_scenario(db, scenario_key, scenario, offset)',
    '    if scenario_type == "boot_empty":',
    '        return await seed_boot_empty_scenario(db, scenario_key, scenario, offset)',
    '    if scenario_type == "group_matrix":',
    '        return await seed_group_matrix_scenario(db, scenario_key, scenario, offset)',
    '    if scenario_type == "channel_matrix":',
    '        return await seed_channel_matrix_scenario(db, scenario_key, scenario, offset)',
    '    if scenario_type == "identity_matrix":',
    '        return await seed_identity_matrix_scenario(db, scenario_key, scenario, offset)',
    '    if scenario_type == "upload_persistence":',
    '        return await seed_upload_persistence_scenario(db, scenario_key, scenario, offset)',
    '    if scenario_type in {"realtime_stress", "long_session"}:',
    '        return await seed_room_mix_scenario(db, scenario_key, scenario, offset)',
    '    return await seed_direct_scenario(db, scenario_key, scenario, offset)',
    '',
    'async def main():',
    '    async with AsyncSessionLocal() as session:',
    "        result = {'runId': run_id}",
    '        for index, (key, scenario) in enumerate(fixture.items(), start=1):',
    '            result[key] = await seed_scenario(session, key, scenario, index * 1000)',
    '        await session.commit()',
    '        print(json.dumps(result))',
    '',
    'asyncio.run(main())',
  ].join('\n')
  return JSON.parse(runPythonInApp(python))
}

function contentType(filePath) {
  if (filePath.endsWith('.html')) return 'text/html; charset=utf-8'
  if (filePath.endsWith('.js')) return 'application/javascript; charset=utf-8'
  if (filePath.endsWith('.css')) return 'text/css; charset=utf-8'
  if (filePath.endsWith('.json')) return 'application/json; charset=utf-8'
  if (filePath.endsWith('.svg')) return 'image/svg+xml'
  if (filePath.endsWith('.png')) return 'image/png'
  if (filePath.endsWith('.jpg') || filePath.endsWith('.jpeg')) return 'image/jpeg'
  if (filePath.endsWith('.webp')) return 'image/webp'
  if (filePath.endsWith('.woff2')) return 'font/woff2'
  return 'application/octet-stream'
}

function serveDist(version, backendHost, backendPort) {
  const distDir = resolvePath(version.dist_dir)
  return new Promise((resolve) => {
    const server = createServer((req, res) => {
      const url = new URL(req.url, `http://${req.headers.host}`)
      if (url.pathname.startsWith('/api/') || url.pathname.startsWith('/ws')) {
        const proxyReq = httpRequest(
          {
            hostname: backendHost,
            port: backendPort,
            path: `${url.pathname}${url.search}`,
            method: req.method,
            headers: req.headers,
          },
          (targetRes) => {
            res.writeHead(targetRes.statusCode ?? 502, targetRes.headers)
            targetRes.pipe(res)
          },
        )
        proxyReq.on('error', (error) => {
          res.writeHead(502, { 'content-type': 'text/plain; charset=utf-8' })
          res.end(error.message)
        })
        req.pipe(proxyReq)
        return
      }

      let filePath = path.join(distDir, url.pathname)
      if (url.pathname === '/' || !existsSync(filePath) || statSync(filePath).isDirectory()) {
        filePath = path.join(distDir, 'index.html')
      }
      res.writeHead(200, { 'content-type': contentType(filePath) })
      createReadStream(filePath).pipe(res)
    })
    server.on('upgrade', (req, socket, head) => {
      const url = new URL(req.url, `http://${req.headers.host}`)
      const proxyReq = httpRequest({
        hostname: backendHost,
        port: backendPort,
        path: `${url.pathname}${url.search}`,
        method: req.method,
        headers: req.headers,
      })
      proxyReq.on('upgrade', (proxyRes, proxySocket, proxyHead) => {
        const closeSockets = () => {
          if (!socket.destroyed) socket.destroy()
          if (!proxySocket.destroyed) proxySocket.destroy()
        }
        proxySocket.on('error', closeSockets)
        socket.on('error', closeSockets)
        socket.write(
          `HTTP/1.1 101 Switching Protocols\r\n${Object.entries(proxyRes.headers)
            .map(([key, value]) => `${key}: ${value}`)
            .join('\r\n')}\r\n\r\n`,
        )
        if (head?.length) proxySocket.write(head)
        if (proxyHead?.length) socket.write(proxyHead)
        proxySocket.pipe(socket).pipe(proxySocket)
      })
      proxyReq.on('error', () => socket.destroy())
      proxyReq.end()
    })
    server.listen(version.port, '127.0.0.1', () => resolve(server))
  })
}

function findAssetMetrics(distDir, prefix, suffix) {
  const assetsDir = path.join(resolvePath(distDir), 'assets')
  if (!existsSync(assetsDir)) return null
  const asset = readdirSync(assetsDir).find((name) => name.startsWith(prefix) && name.endsWith(suffix))
  if (!asset) return null
  const filePath = path.join(assetsDir, asset)
  const buffer = readFileSync(filePath)
  return {
    file: asset,
    rawBytes: buffer.length,
    gzipBytes: gzipSync(buffer).length,
  }
}

async function collectBrowserCounters(client) {
  const metrics = await client.send('Performance.getMetrics').catch(() => ({ metrics: [] }))
  const map = new Map(metrics.metrics.map((item) => [item.name, item.value]))
  return {
    jsHeapUsedMb: Number(((map.get('JSHeapUsedSize') ?? 0) / (1024 * 1024)).toFixed(2)),
    domNodes: Math.round(map.get('Nodes') ?? 0),
    layoutCount: Math.round(map.get('LayoutCount') ?? 0),
    recalcStyleCount: Math.round(map.get('RecalcStyleCount') ?? 0),
  }
}

async function collectDomSnapshot(page) {
  return page.evaluate(() => ({
    conversationCards: document.querySelectorAll('.conversation-card, .conversation-item').length,
    messageBubbles: document.querySelectorAll('.message-bubble, .message-row').length,
    totalNodes: document.querySelectorAll('*').length,
    metrics: Array.from(document.querySelectorAll('[data-perf-mark]')).map((node) => node.getAttribute('data-perf-mark')),
  }))
}

async function runScrollProbe(page) {
  return page.evaluate(async () => {
    const container = document.querySelector('.messages-container')
    if (!container) return null
    const frames = []
    let previous = performance.now()
    const start = previous
    while (performance.now() - start < 1200) {
      container.scrollTop = container.scrollHeight
      await new Promise((resolve) => requestAnimationFrame(resolve))
      const current = performance.now()
      frames.push(current - previous)
      previous = current
    }
    const avgFrame = frames.reduce((sum, value) => sum + value, 0) / Math.max(frames.length, 1)
    const fps = avgFrame > 0 ? 1000 / avgFrame : 0
    return {
      fps: Number(fps.toFixed(1)),
      jankyFrames: frames.filter((value) => value > 50).length,
      sampleCount: frames.length,
    }
  })
}

async function runContextMenuProbe(page) {
  const target = page.locator('.message-row, .message-bubble').first()
  const start = performance.now()
  await target.click({ button: 'right', timeout: 30000 }).catch(async () => {
    await target.dispatchEvent('contextmenu')
  })
  await page.locator('.context-menu, .chat-context-menu, [role="menu"]').first().waitFor({ timeout: 30000 })
  return performance.now() - start
}

function summarizeApiTimings(timings, matcher) {
  const matches = timings.filter((entry) => matcher(entry.url))
  if (!matches.length) return null
  const total = matches.reduce((sum, entry) => sum + entry.ms, 0)
  return Number((total / matches.length).toFixed(1))
}

async function postJson(url, token, body) {
  const response = await fetch(url, {
    method: 'POST',
    headers: {
      authorization: `Bearer ${token}`,
      'content-type': 'application/json',
    },
    body: JSON.stringify(body),
  })
  if (!response.ok) {
    const text = await response.text()
    throw new Error(`${response.status} ${text}`)
  }
  const responseContentType = response.headers.get('content-type') ?? ''
  if (responseContentType.includes('application/json')) {
    return response.json()
  }
  return response.text()
}

async function waitForConversationList(page) {
  await page.locator('.conversation-list-wrapper').waitFor({ timeout: 60000 })
  await page.locator('.conversation-card, .conversation-item').first().waitFor({ timeout: 60000 })
}

async function waitForConversationListReady(page, timeoutMs = 15000) {
  const wrapperReady = await page
    .locator('.conversation-list-wrapper')
    .waitFor({ timeout: timeoutMs })
    .then(() => true)
    .catch(() => false)
  if (!wrapperReady) {
    return false
  }
  return page
    .locator('.conversation-card, .conversation-item')
    .first()
    .waitFor({ timeout: timeoutMs })
    .then(() => true)
    .catch(() => false)
}

async function waitForRoomReady(page) {
  await page.locator('.messages-container').waitFor({ timeout: 60000 })
  await page.locator('.message-bubble, .message-row').first().waitFor({ timeout: 60000 })
}

async function waitForCountAtLeast(page, selector, minimum, timeoutMs = 15000) {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    const count = await page.locator(selector).count().catch(() => 0)
    if (count >= minimum) {
      return true
    }
    await page.waitForTimeout(250).catch(() => null)
  }
  return false
}

async function backToConversationList(page, baseUrl) {
  const backButton = page.locator('.chat-header .back-btn').first()
  if (await backButton.count()) {
    await backButton.click().catch(() => null)
    const listReady = await waitForConversationListReady(page, 10000)
    if (listReady && !page.url().includes('/chat?user_id=')) {
      return
    }
  }
  await page.goto(`${baseUrl}/chat`, { waitUntil: 'domcontentloaded', timeout: 60000 })
  await waitForConversationListReady(page, 15000).catch(() => null)
}

async function openConversationFromList(page, baseUrl, target) {
  const locator = page
    .locator('.conversation-card, .conversation-item')
    .filter({ hasText: target.title })
    .first()
  if (await locator.count()) {
    await locator.scrollIntoViewIfNeeded().catch(() => null)
    await locator.click({ timeout: 30000 }).catch(() => null)
    if (page.url().includes('/chat?user_id=')) {
      const ready = await waitForRoomReady(page).then(() => true).catch(() => false)
      if (ready) {
        return true
      }
    }
  }
  const navigated = await page
    .goto(
      `${baseUrl}/chat?user_id=${target.routeUserId}&user_name=${encodeURIComponent(target.title ?? '')}`,
      { waitUntil: 'domcontentloaded', timeout: 60000 },
    )
    .then(() => true)
    .catch(() => false)
  if (!navigated) {
    return false
  }
  return waitForRoomReady(page).then(() => true).catch(() => false)
}

async function waitForUnreadBadges(page, expectedBadges) {
  return waitForCountAtLeast(
    page,
    '.conversation-card .unread-badge, .conversation-item .unread-badge',
    expectedBadges,
    15000,
  )
}

async function runHeaderMenuProbe(page) {
  const menuButton = page.locator('.chat-header .header-menu-container .header-btn').first()
  if (!(await menuButton.count())) {
    return null
  }
  const menuStart = performance.now()
  await menuButton.click({ timeout: 30000 }).catch(() => null)
  const opened = await page.locator('.header-dropdown-menu').first().waitFor({ timeout: 10000 }).then(() => true).catch(() => false)
  if (!opened) {
    return null
  }
  const durationMs = performance.now() - menuStart
  const overlay = page.locator('.menu-overlay').first()
  if (await overlay.count()) {
    await overlay.click({ timeout: 10000 }).catch(() => null)
  } else {
    await page.keyboard.press('Escape').catch(() => null)
  }
  return durationMs
}

async function runSearchProbe(page, seeded) {
  if (!seeded.searchTerm) return null
  const menuButton = page.locator('.chat-header .header-menu-container .header-btn').first()
  if (!(await menuButton.count())) return null
  const start = performance.now()
  await menuButton.click({ timeout: 30000 })
  await page.getByText('جستجو', { exact: true }).first().click({ timeout: 30000 })
  await page.locator('#search-input').waitFor({ timeout: 30000 })
  await page.locator('#search-input').fill(seeded.searchTerm)
  await page.waitForFunction(
    (term) => document.querySelectorAll('mark').length > 0 || Array.from(document.querySelectorAll('.message-bubble, .message-row')).some((node) => (node.textContent || '').includes(term)),
    seeded.searchTerm,
    { timeout: 30000 },
  ).catch(() => null)
  const readyMs = performance.now() - start
  const resultCount = await page.locator('mark').count().catch(() => 0)
  const closeButton = page.locator('.search-bar-container .mobile-back-btn').first()
  if (await closeButton.count()) {
    await closeButton.click().catch(() => null)
  }
  return {
    readyMs: Number(readyMs.toFixed(1)),
    resultCount,
  }
}

async function getDocumentBubbleTransferState(documentBubble) {
  const className = (await documentBubble.getAttribute('class').catch(() => '')) || ''
  if (/is-busy/.test(className)) return 'busy'
  if ((await documentBubble.locator('.doc-icon.doc-uploading').count().catch(() => 0)) > 0) return 'busy'
  if ((await documentBubble.locator('.doc-download-icon').count().catch(() => 0)) === 0) return 'completed'
  return 'idle'
}

async function waitForDocumentBubbleState(page, documentBubble, matcher, timeoutMs = 30000) {
  const deadline = Date.now() + timeoutMs
  let lastState = 'idle'
  while (Date.now() < deadline) {
    lastState = await getDocumentBubbleTransferState(documentBubble).catch(() => 'idle')
    if (matcher(lastState)) return lastState
    await page.waitForTimeout(200).catch(() => null)
  }
  return lastState
}

async function waitForFlag(page, predicate, timeoutMs = 10000) {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    if (predicate()) return true
    await page.waitForTimeout(100).catch(() => null)
  }
  return predicate()
}

function formatBenchmarkError(error) {
  const message = error instanceof Error ? error.message : String(error)
  return message.split('\n')[0].slice(0, 240)
}

async function runIdentityMatrixProbe(page, seeded) {
  if (!seeded.identityProbe?.expectedProfileUserId) return null
  const headerInfo = page.locator('.chat-header .header-user-info').last()
  if (!(await headerInfo.count().catch(() => 0))) return null
  const originalUrl = page.url()
  const probeTimeoutMs = 8000
  const start = performance.now()
  await headerInfo.click({ timeout: probeTimeoutMs }).catch(() => null)
  const expectedUrl = new RegExp(`/users/${seeded.identityProbe.expectedProfileUserId}`)
  const resolved = await page.waitForURL(expectedUrl, { timeout: probeTimeoutMs }).then(() => true).catch(() => false)
  const profileOpenMs = performance.now() - start
  const profileVisible = await page.locator('.public-profile-view').first().isVisible().catch(() => false)
  const profileText = profileVisible ? await page.locator('.public-profile-view').first().textContent().catch(() => '') : ''
  const labelMatched = seeded.identityProbe.expectedProfileLabel
    ? String(profileText || '').includes(String(seeded.identityProbe.expectedProfileLabel))
    : null
  if (resolved || profileVisible || page.url() !== originalUrl) {
    const backButton = page.locator('.public-profile-view .back-button').first()
    if (await backButton.count().catch(() => 0)) {
      await backButton.click({ timeout: probeTimeoutMs }).catch(() => null)
    } else {
      await page.goBack().catch(() => null)
    }
    await waitForRoomReady(page).catch(() => null)
  }
  return {
    profileOpenMs: Number(profileOpenMs.toFixed(1)),
    resolvedProfile: resolved,
    labelMatched,
  }
}

async function runDocumentUploadPersistenceProbe(page, seeded, baseUrl, scenario) {
  const activeTarget = {
    routeUserId: seeded.activeTargetId,
    title: seeded.activeTargetName,
  }
  const switchTarget = seeded.persistenceProbe?.switchTarget ?? null
  const uploadFileName = `bench-upload-${Date.now()}.txt`
  const uploadSizeBytes = Math.max(1024, Number(scenario.upload_probe_size_bytes ?? 786432))
  let releaseUpload = null
  let heldUploadRequest = false
  let uploadTransport = 'none'
  const holdUpload = new Promise((resolve) => {
    releaseUpload = resolve
  })
  const routeHandler = async (route) => {
    const requestUrl = route.request().url()
    if (!heldUploadRequest) {
      heldUploadRequest = true
      uploadTransport = requestUrl.includes('/upload-sessions/') ? 'resumable' : 'legacy'
      await holdUpload
    }
    await route.continue()
  }

  await page.route('**/api/chat/upload-sessions/*/chunk', routeHandler)
  await page.route('**/api/chat/upload-media', routeHandler)
  const uploadStart = performance.now()
  try {
    const attachButton = page.locator('button.attach-btn').first()
    const canAttach = await attachButton.waitFor({ timeout: 15000 }).then(() => true).catch(() => false)
    if (!canAttach) {
      return {
        documentFileName: uploadFileName,
        sizeBytes: uploadSizeBytes,
        transport: uploadTransport,
        requestHeld: heldUploadRequest,
        failed: true,
        errorMessage: 'attach button unavailable',
        elapsedMs: Number((performance.now() - uploadStart).toFixed(1)),
      }
    }

    await attachButton.click({ timeout: 30000 })
    await page.locator('.attachment-sheet').waitFor({ timeout: 30000 })
    await page.getByRole('button', { name: 'فایل' }).first().click({ timeout: 30000 })
    const fileInput = page.locator('.attachment-sheet').last().locator('input[type="file"][accept="*"]').last()
    await fileInput.waitFor({ state: 'attached', timeout: 30000 })
    await fileInput.setInputFiles({
      name: uploadFileName,
      mimeType: 'text/plain',
      buffer: Buffer.alloc(uploadSizeBytes, 'u'),
    })

    const uploadBubble = page.locator('.messages-container .msg-document').filter({ hasText: uploadFileName }).first()
    await uploadBubble.waitFor({ timeout: 30000 })
    const firstVisibleMs = performance.now() - uploadStart
    const initialState = await waitForDocumentBubbleState(page, uploadBubble, (state) => state !== 'idle', 10000)
    const requestHeld = await waitForFlag(page, () => heldUploadRequest, 10000)
    if (!requestHeld && releaseUpload) {
      releaseUpload()
      releaseUpload = null
    }

    const leaveStart = performance.now()
    await backToConversationList(page, baseUrl).catch(() => null)
    if (switchTarget) {
      await openConversationFromList(page, baseUrl, switchTarget).catch(() => false)
      await backToConversationList(page, baseUrl).catch(() => null)
    }
    await openConversationFromList(page, baseUrl, activeTarget).catch(() => false)
    const resumedBubble = page.locator('.messages-container .msg-document').filter({ hasText: uploadFileName }).first()
    await resumedBubble.waitFor({ timeout: 30000 }).catch(() => null)
    const resumedState = await waitForDocumentBubbleState(page, resumedBubble, (state) => state !== 'idle', 30000)
    const leaveReturnMs = performance.now() - leaveStart

    if (releaseUpload) {
      releaseUpload()
      releaseUpload = null
    }

    const settleStart = performance.now()
    const completedState = await waitForDocumentBubbleState(page, resumedBubble, (state) => state !== 'busy', 60000)
    const completionMs = performance.now() - settleStart

    return {
      documentFileName: uploadFileName,
      sizeBytes: uploadSizeBytes,
      transport: uploadTransport,
      requestHeld,
      initialState,
      resumedState,
      completedState,
      firstVisibleMs: Number(firstVisibleMs.toFixed(1)),
      leaveReturnMs: Number(leaveReturnMs.toFixed(1)),
      completionMs: Number(completionMs.toFixed(1)),
    }
  } catch (error) {
    return {
      documentFileName: uploadFileName,
      sizeBytes: uploadSizeBytes,
      transport: uploadTransport,
      requestHeld: heldUploadRequest,
      failed: true,
      errorMessage: formatBenchmarkError(error),
      elapsedMs: Number((performance.now() - uploadStart).toFixed(1)),
    }
  } finally {
    if (releaseUpload) {
      releaseUpload()
    }
    await page.unroute('**/api/chat/upload-sessions/*/chunk', routeHandler).catch(() => null)
    await page.unroute('**/api/chat/upload-media', routeHandler).catch(() => null)
  }
}

async function runUploadPersistenceProbe(page, seeded, baseUrl, scenario) {
  if (!seeded.persistenceProbe?.documentFileName) return null
  const documentFileName = seeded.persistenceProbe.documentFileName
  const activeTarget = {
    routeUserId: seeded.activeTargetId,
    title: seeded.activeTargetName,
  }
  const switchTarget = seeded.persistenceProbe.switchTarget ?? null
  let releaseDownload = null
  const holdDownload = new Promise((resolve) => {
    releaseDownload = resolve
  })
  const routeHandler = async (route) => {
    await holdDownload
    await route.continue()
  }
  await page.route('**/api/chat/files/**', routeHandler)
  try {
    const documentBubble = page.locator('.messages-container .msg-document').filter({ hasText: documentFileName }).first()
    const visible = await documentBubble.waitFor({ timeout: 30000 }).then(() => true).catch(() => false)
    if (!visible) return null
    const downloadStart = performance.now()
    await documentBubble.click({ timeout: 30000 }).catch(() => null)
    const initialState = await waitForDocumentBubbleState(page, documentBubble, (state) => state !== 'idle', 30000)
    const downloadStartMs = performance.now() - downloadStart

    const leaveStart = performance.now()
    await backToConversationList(page, baseUrl).catch(() => null)
    if (switchTarget) {
      await openConversationFromList(page, baseUrl, switchTarget).catch(() => false)
      await backToConversationList(page, baseUrl).catch(() => null)
    }
    await openConversationFromList(page, baseUrl, activeTarget).catch(() => false)
    const resumedBubble = page.locator('.messages-container .msg-document').filter({ hasText: documentFileName }).first()
    await resumedBubble.waitFor({ timeout: 30000 }).catch(() => null)
    const resumedState = await waitForDocumentBubbleState(page, resumedBubble, (state) => state !== 'idle', 30000)
    const leaveReturnMs = performance.now() - leaveStart

    if (releaseDownload) {
      releaseDownload()
      releaseDownload = null
    }

    const settleStart = performance.now()
    const completedState = await waitForDocumentBubbleState(page, resumedBubble, (state) => state === 'completed', 60000)
    const completionMs = performance.now() - settleStart

    const reloadStart = performance.now()
    await page.reload({ waitUntil: 'domcontentloaded', timeout: 60000 }).catch(() => null)
    await waitForRoomReady(page).catch(() => null)
    const reloadedBubble = page.locator('.messages-container .msg-document').filter({ hasText: documentFileName }).first()
    await reloadedBubble.waitFor({ timeout: 30000 }).catch(() => null)
    const reloadedState = await waitForDocumentBubbleState(page, reloadedBubble, (state) => state !== 'idle', 15000)
    const reloadMs = performance.now() - reloadStart
    const upload = await runDocumentUploadPersistenceProbe(page, seeded, baseUrl, scenario)

    return {
      documentFileName,
      initialState,
      resumedState,
      completedState,
      reloadedState,
      downloadStartMs: Number(downloadStartMs.toFixed(1)),
      leaveReturnMs: Number(leaveReturnMs.toFixed(1)),
      completionMs: Number(completionMs.toFixed(1)),
      reloadMs: Number(reloadMs.toFixed(1)),
      upload,
    }
  } finally {
    if (releaseDownload) {
      releaseDownload()
    }
    await page.unroute('**/api/chat/files/**', routeHandler).catch(() => null)
  }
}

async function triggerRealtimeBurst(page, seeded, backendConfig, baseUrl) {
  if (!seeded.realtimeBurst) return null
  const backendBaseUrl = `http://${backendConfig.host}:${backendConfig.port}`
  const burstRequests = []

  if (seeded.realtimeBurst.directSenderToken) {
    burstRequests.push(
      postJson(`${backendBaseUrl}/api/chat/send`, seeded.realtimeBurst.directSenderToken, {
        receiver_id: seeded.actor.userId,
        content: `realtime direct burst ${Date.now()}`,
        message_type: 'text',
      }),
    )
  }
  if (seeded.realtimeBurst.groupSenderToken && seeded.realtimeBurst.groupChatId) {
    burstRequests.push(
      postJson(`${backendBaseUrl}/api/chat/rooms/${seeded.realtimeBurst.groupChatId}/send`, seeded.realtimeBurst.groupSenderToken, {
        content: `group burst ${Date.now()}`,
        message_type: 'text',
      }),
    )
  }
  if (seeded.realtimeBurst.channelSenderToken && seeded.realtimeBurst.channelChatId) {
    burstRequests.push(
      postJson(`${backendBaseUrl}/api/chat/rooms/${seeded.realtimeBurst.channelChatId}/send`, seeded.realtimeBurst.channelSenderToken, {
        content: `channel burst ${Date.now()}`,
        message_type: 'text',
      }),
    )
  }
  if (!burstRequests.length) return null

  console.log('[benchmark] realtime burst: posting events')
  await Promise.all(burstRequests)
  console.log('[benchmark] realtime burst: events posted')
  const directAppendMs = null
  await backToConversationList(page, baseUrl).catch(() => null)
  const unreadStart = performance.now()
  await waitForUnreadBadges(page, Math.min(2, burstRequests.length)).catch(() => null)
  const unreadBadgeCount = await page.locator('.conversation-card .unread-badge, .conversation-item .unread-badge').count().catch(() => 0)
  return {
    directAppendMs: directAppendMs !== null ? Number(directAppendMs.toFixed(1)) : null,
    unreadRefreshMs: Number((performance.now() - unreadStart).toFixed(1)),
    unreadBadgeCount,
  }
}

async function runLongSessionProbe(page, seeded, baseUrl, scenario) {
  const targets = Array.isArray(seeded.switchTargets) ? seeded.switchTargets : []
  if (!targets.length) return null
  const durations = []
  const menuDurations = []
  const iterations = Math.max(1, Number(scenario.switch_iterations ?? seeded.switchIterations ?? 1))
  for (let iteration = 0; iteration < iterations; iteration += 1) {
    await backToConversationList(page, baseUrl)
    for (const target of targets) {
      const roomStart = performance.now()
      const opened = await openConversationFromList(page, baseUrl, target).catch(() => false)
      if (!opened) {
        continue
      }
      durations.push(performance.now() - roomStart)
      const menuDuration = await runHeaderMenuProbe(page)
      if (menuDuration !== null) {
        menuDurations.push(menuDuration)
      }
    }
  }
  return {
    iterations,
    switchCount: durations.length,
    averageSwitchMs: Number((durations.reduce((sum, value) => sum + value, 0) / Math.max(durations.length, 1)).toFixed(1)),
    maxSwitchMs: Number(Math.max(...durations, 0).toFixed(1)),
    averageMenuMs: menuDurations.length
      ? Number((menuDurations.reduce((sum, value) => sum + value, 0) / menuDurations.length).toFixed(1))
      : null,
  }
}

async function applyScenarioEmulation(client, scenario) {
  const cpuThrottleRate = Number(scenario.cpu_throttle_rate ?? 0)
  if (cpuThrottleRate > 1) {
    await client.send('Emulation.setCPUThrottlingRate', { rate: cpuThrottleRate }).catch(() => null)
  }
  const latencyMs = Number(scenario.network_latency_ms ?? 0)
  const downloadKbps = Number(scenario.download_kbps ?? 0)
  const uploadKbps = Number(scenario.upload_kbps ?? 0)
  if (latencyMs > 0 || downloadKbps > 0 || uploadKbps > 0) {
    await client.send('Network.enable').catch(() => null)
    await client.send('Network.emulateNetworkConditions', {
      offline: false,
      latency: latencyMs,
      downloadThroughput: downloadKbps > 0 ? (downloadKbps * 1024) / 8 : -1,
      uploadThroughput: uploadKbps > 0 ? (uploadKbps * 1024) / 8 : -1,
      connectionType: 'cellular3g',
    }).catch(() => null)
  }
}

async function runScenario(browser, version, scenario, fixture, browserConfig, backendConfig) {
  const seeded = fixture[scenario.runner_key]
  const baseUrl = `http://127.0.0.1:${version.port}`
  const timings = []
  const requestStarts = new Map()
  const context = await browser.newContext({
    viewport: browserConfig.viewport,
    locale: browserConfig.locale,
    colorScheme: browserConfig.color_scheme,
    reducedMotion: scenario.reduced_motion ? 'reduce' : 'no-preference',
  })
  const page = await context.newPage()
  const client = await context.newCDPSession(page)
  await client.send('Performance.enable').catch(() => null)
  await applyScenarioEmulation(client, scenario)

  page.on('request', (request) => {
    if (request.url().includes('/api/')) requestStarts.set(request, performance.now())
  })
  page.on('requestfinished', (request) => {
    const start = requestStarts.get(request)
    if (start !== undefined) {
      timings.push({ url: request.url(), ms: performance.now() - start })
      requestStarts.delete(request)
    }
  })
  page.on('requestfailed', (request) => {
    requestStarts.delete(request)
  })

  await page.addInitScript(({ actor, uiVersion }) => {
    localStorage.setItem('auth_token', actor.accessToken)
    localStorage.setItem('refresh_token', actor.refreshToken)
    localStorage.setItem('messenger_ui_version', uiVersion)
    localStorage.setItem(
      'current_user_summary',
      JSON.stringify({
        id: actor.userId,
        account_name: actor.accountName,
        full_name: actor.accountName,
        role: 'عادی',
      }),
    )
    localStorage.removeItem('suspended_refresh_token')
  }, { actor: seeded.actor, uiVersion: version.ui_version })

  const listStart = performance.now()
  await page.goto(`${baseUrl}/chat`, { waitUntil: 'domcontentloaded', timeout: 60000 })
  await waitForConversationList(page)
  const listReadyMs = performance.now() - listStart
  await page.waitForTimeout(700)
  const listSnapshot = await collectDomSnapshot(page)

  const chatStart = performance.now()
  await page.goto(`${baseUrl}/chat?user_id=${seeded.activeTargetId}&user_name=${encodeURIComponent(seeded.activeTargetName ?? '')}`, { waitUntil: 'domcontentloaded', timeout: 60000 })
  await waitForRoomReady(page)
  const chatReadyMs = performance.now() - chatStart
  await page.waitForTimeout(900)
  const chatSnapshot = await collectDomSnapshot(page)
  console.log(`[benchmark] ${version.key}/${scenario.id}: search probe start`)
  const search = await runSearchProbe(page, seeded)
  console.log(`[benchmark] ${version.key}/${scenario.id}: search probe done`)
  const scroll = await runScrollProbe(page)
  console.log(`[benchmark] ${version.key}/${scenario.id}: scroll probe done`)
  const contextMenuMs = await runContextMenuProbe(page)
  console.log(`[benchmark] ${version.key}/${scenario.id}: context probe done`)
  const identity = await runIdentityMatrixProbe(page, seeded)
  console.log(`[benchmark] ${version.key}/${scenario.id}: identity probe done`)
  console.log(`[benchmark] ${version.key}/${scenario.id}: realtime probe start`)
  const realtime = await triggerRealtimeBurst(page, seeded, backendConfig, baseUrl)
  console.log(`[benchmark] ${version.key}/${scenario.id}: realtime probe done`)
  if (realtime) {
    await openConversationFromList(page, baseUrl, {
      routeUserId: seeded.activeTargetId,
      title: seeded.activeTargetName,
    }).catch(() => null)
  }
  const persistence = await runUploadPersistenceProbe(page, seeded, baseUrl, scenario)
  console.log(`[benchmark] ${version.key}/${scenario.id}: persistence probe done`)
  console.log(`[benchmark] ${version.key}/${scenario.id}: long-session probe start`)
  const longSession = await runLongSessionProbe(page, seeded, baseUrl, scenario)
  console.log(`[benchmark] ${version.key}/${scenario.id}: long-session probe done`)
  const counters = await collectBrowserCounters(client)
  const finalUrl = page.url()

  await context.close()

  return {
    version: version.key,
    versionLabel: version.label,
    commit: version.resolvedCommit,
    scenarioId: scenario.id,
    scenario: scenario.runner_key,
    scenarioLabel: scenario.label,
    surfaceIds: scenario.surface_ids,
    seededConversations: seeded.conversationCount,
    seededActiveMessages: seeded.activeMessageCount,
    listReadyMs: Number(listReadyMs.toFixed(1)),
    chatReadyMs: Number(chatReadyMs.toFixed(1)),
    search,
    listConversationCards: listSnapshot.conversationCards,
    chatMessageBubbles: chatSnapshot.messageBubbles,
    chatDomNodes: chatSnapshot.totalNodes,
    contextMenuMs: Number(contextMenuMs.toFixed(1)),
    identity,
    realtime,
    persistence,
    longSession,
    scroll,
    counters,
    api: {
      authMeMs: summarizeApiTimings(timings, (url) => url.includes('/api/auth/me')),
      conversationsMs: summarizeApiTimings(timings, (url) => url.includes('/api/chat/conversations')),
      messagesMs: summarizeApiTimings(timings, (url) => url.includes(seeded.activeMessagesApiPath)),
      totalRequests: timings.length,
    },
    stage2MetricCount: chatSnapshot.metrics.length,
    finalUrl,
  }
}

function makeMarkdownTable(results, buildMetrics) {
  const rows = []
  rows.push('| نسخه | تست | داده seed شده | list ready | chat first paint | پیام‌های render | DOM nodes | FPS اسکرول | jank | context menu | persistence | heap JS | API conv/msg | Bundle JS gzip |')
  rows.push('| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | ---: | --- | ---: |')
  for (const result of results) {
    const build = buildMetrics[result.version]
    rows.push(
      `| ${result.versionLabel} (${result.commit}) | ${result.scenarioId} | ${result.seededConversations} conv / ${result.seededActiveMessages} msg | ${result.listReadyMs} ms | ${result.chatReadyMs} ms | ${result.chatMessageBubbles} | ${result.chatDomNodes} | ${result.scroll?.fps ?? 'n/a'} | ${result.scroll?.jankyFrames ?? 'n/a'} | ${result.contextMenuMs} ms | ${formatPersistenceSummary(result.persistence)} | ${result.counters.jsHeapUsedMb} MB | ${result.api.conversationsMs ?? 'n/a'} / ${result.api.messagesMs ?? 'n/a'} ms | ${(((build?.messengerJs?.gzipBytes ?? 0) / 1024).toFixed(1))} KB |`,
    )
  }
  return rows.join('\n')
}

function formatPersistenceSummary(persistence) {
  if (!persistence) return 'n/a'
  const download = typeof persistence.downloadStartMs === 'number'
    ? `dl ${persistence.downloadStartMs}/${persistence.completionMs}/${persistence.reloadMs} ms`
    : 'dl n/a'
  const upload = persistence.upload && typeof persistence.upload.completionMs === 'number'
    ? `up ${persistence.upload.firstVisibleMs}/${persistence.upload.completionMs} ms ${persistence.upload.transport}${persistence.upload.requestHeld ? '' : ' no-hold'}`
    : persistence.upload?.errorMessage
      ? `up error ${persistence.upload.transport ?? 'none'}${persistence.upload.requestHeld ? '' : ' no-hold'}`
    : 'up n/a'
  return `${download}; ${upload}`
}

async function main() {
  const options = parseArgs(process.argv.slice(2))
  const config = loadConfig(options.config)
  const performance = config.performance
  const versions = performance.versions.map((version) => ({
    ...version,
    resolvedCommit: resolveVersionCommit(version),
  }))
  const outputPath = resolvePath(performance.output_file)
  mkdirSync(path.dirname(outputPath), { recursive: true })

  for (const version of versions) {
    if (!existsSync(path.join(resolvePath(version.dist_dir), 'index.html'))) {
      throw new Error(`Missing built dist for ${version.key}: ${resolvePath(version.dist_dir)}`)
    }
  }

  console.log('Seeding benchmark fixture...')
  const fixture = seedBenchmarkFixture(performance.scenarios)
  console.log(`Fixture run id: ${fixture.runId}`)

  const servers = []
  try {
    for (const version of versions) {
      servers.push(await serveDist(version, performance.backend.host, performance.backend.port))
      console.log(`Serving ${version.key} on http://127.0.0.1:${version.port}`)
    }

    const browser = await chromium.launch({ headless: true, args: ['--disable-dev-shm-usage'] })
    const results = []
    if (!options.skipWarmup) {
      for (let runIndex = 0; runIndex < performance.warmup_runs; runIndex += 1) {
        for (const version of versions) {
          for (const scenario of performance.scenarios) {
            console.log(`Warming ${version.key} / ${scenario.id} (${runIndex + 1}/${performance.warmup_runs})...`)
            await runScenario(browser, version, scenario, fixture, performance.browser, performance.backend)
          }
        }
      }
    }
    for (let runIndex = 0; runIndex < performance.measured_runs; runIndex += 1) {
      for (const version of versions) {
        for (const scenario of performance.scenarios) {
          console.log(`Running ${version.key} / ${scenario.id} (${runIndex + 1}/${performance.measured_runs})...`)
          results.push(await runScenario(browser, version, scenario, fixture, performance.browser, performance.backend))
        }
      }
    }
    await browser.close()

    const buildMetrics = {}
    for (const version of versions) {
      buildMetrics[version.key] = {
        messengerJs: await findAssetMetrics(version.dist_dir, 'MessengerView-', '.js'),
        messengerCss: await findAssetMetrics(version.dist_dir, 'MessengerView-', '.css'),
      }
    }
    const output = {
      generatedAt: new Date().toISOString(),
      browser: 'Playwright Chromium',
      backend: `http://${performance.backend.host}:${performance.backend.port}`,
      config: resolvePath(options.config),
      fixture,
      buildMetrics,
      results,
      markdown: makeMarkdownTable(results, buildMetrics),
    }
    writeFileSync(outputPath, JSON.stringify(output, null, 2), 'utf8')
    console.log(`\nSaved JSON: ${outputPath}\n`)
    console.log(output.markdown)
  } finally {
    await Promise.all(servers.map((server) => new Promise((resolve) => server.close(resolve))))
  }
}

main().catch((error) => {
  console.error(error)
  process.exit(1)
})
