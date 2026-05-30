import { createServer, request as httpRequest } from 'node:http'
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
    'import json',
    'import uuid',
    'from datetime import datetime, timedelta, timezone',
    '',
    'from sqlalchemy import select',
    '',
    'from core.db import AsyncSessionLocal',
    'from core.enums import ChatMemberRole, ChatMembershipStatus, ChatType, MessageType',
    'from core.security import create_access_token, create_refresh_token',
    'from core.services.chat_room_service import ensure_mandatory_channel_rollout',
    'from core.services.session_service import hash_token',
    'from models.chat import Chat',
    'from models.chat_member import ChatMember',
    'from models.conversation import Conversation',
    'from models.message import Message',
    'from models.session import Platform, UserSession',
    'from models.user import User, UserRole',
    '',
    `fixture = json.loads(${JSON.stringify(JSON.stringify(fixture))})`,
    'run_id = uuid.uuid4().hex[:10]',
    '',
    'def make_mobile(offset):',
    "    seed = (int(run_id[:8], 16) + offset) % 1000000000",
    "    return f\"09{seed:09d}\"",
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
    'async def seed_scenario(db, scenario_key, scenario, offset):',
    '    scenario_type = scenario.get("scenarioType") or "direct"',
    '    if scenario_type == "boot_empty":',
    '        return await seed_boot_empty_scenario(db, scenario_key, scenario, offset)',
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

async function triggerRealtimeBurst(page, seeded, backendConfig, baseUrl) {
  if (!seeded.realtimeBurst) return null
  const backendBaseUrl = `http://${backendConfig.host}:${backendConfig.port}`
  console.log('[benchmark] realtime burst: posting events')
  await Promise.all([
    postJson(`${backendBaseUrl}/api/chat/send`, seeded.realtimeBurst.directSenderToken, {
      receiver_id: seeded.actor.userId,
      content: `realtime direct burst ${Date.now()}`,
      message_type: 'text',
    }),
    postJson(`${backendBaseUrl}/api/chat/rooms/${seeded.realtimeBurst.groupChatId}/send`, seeded.realtimeBurst.groupSenderToken, {
      content: `group burst ${Date.now()}`,
      message_type: 'text',
    }),
    postJson(`${backendBaseUrl}/api/chat/rooms/${seeded.realtimeBurst.channelChatId}/send`, seeded.realtimeBurst.channelSenderToken, {
      content: `channel burst ${Date.now()}`,
      message_type: 'text',
    }),
  ])
  console.log('[benchmark] realtime burst: events posted')
  const directAppendMs = null
  await backToConversationList(page, baseUrl).catch(() => null)
  const unreadStart = performance.now()
  await waitForUnreadBadges(page, 2)
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
  console.log(`[benchmark] ${version.key}/${scenario.id}: realtime probe start`)
  const realtime = await triggerRealtimeBurst(page, seeded, backendConfig, baseUrl)
  console.log(`[benchmark] ${version.key}/${scenario.id}: realtime probe done`)
  if (realtime) {
    await openConversationFromList(page, baseUrl, {
      routeUserId: seeded.activeTargetId,
      title: seeded.activeTargetName,
    }).catch(() => null)
  }
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
    realtime,
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
  rows.push('| نسخه | تست | داده seed شده | list ready | chat first paint | پیام‌های render | DOM nodes | FPS اسکرول | jank | context menu | heap JS | API conv/msg | Bundle JS gzip |')
  rows.push('| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | ---: |')
  for (const result of results) {
    const build = buildMetrics[result.version]
    rows.push(
      `| ${result.versionLabel} (${result.commit}) | ${result.scenarioId} | ${result.seededConversations} conv / ${result.seededActiveMessages} msg | ${result.listReadyMs} ms | ${result.chatReadyMs} ms | ${result.chatMessageBubbles} | ${result.chatDomNodes} | ${result.scroll?.fps ?? 'n/a'} | ${result.scroll?.jankyFrames ?? 'n/a'} | ${result.contextMenuMs} ms | ${result.counters.jsHeapUsedMb} MB | ${result.api.conversationsMs ?? 'n/a'} / ${result.api.messagesMs ?? 'n/a'} ms | ${(((build?.messengerJs?.gzipBytes ?? 0) / 1024).toFixed(1))} KB |`,
    )
  }
  return rows.join('\n')
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