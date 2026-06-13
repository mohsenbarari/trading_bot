import http from 'k6/http';
import { check, sleep } from 'k6';
import exec from 'k6/execution';
import { Counter, Rate } from 'k6/metrics';

const BASE_URL = (__ENV.BASE_URL || 'https://coin.gold-trade.ir').replace(/\/+$/, '');
const API_PREFIX = (__ENV.API_PREFIX || '/api').replace(/\/+$/, '');
const TARGET_RPS = Number(__ENV.TARGET_RPS || '50');
const DURATION = __ENV.DURATION || '2m';
const LOAD_PROFILE = __ENV.LOAD_PROFILE || 'smoke';
const INCLUDE_MEDIA = (__ENV.INCLUDE_MEDIA || '0') === '1';
const INCLUDE_MUTATIONS = (__ENV.INCLUDE_MUTATIONS || '0') === '1';
const PRE_ALLOCATED_VUS = Number(__ENV.PRE_ALLOCATED_VUS || Math.max(20, Math.ceil(TARGET_RPS / 2)));
const MAX_VUS = Number(__ENV.MAX_VUS || Math.max(100, TARGET_RPS * 4));
const AUTH_POOL_PATH = __ENV.AUTH_POOL_PATH || '';

const stageRequestFailed = new Rate('stage_l_request_failed');
const stageBusinessRejections = new Counter('stage_l_business_rejections');
const stagePersonaRequests = new Counter('stage_l_persona_requests');
const stagePersonaIterations = new Counter('stage_l_persona_iterations');

http.setResponseCallback(http.expectedStatuses({ min: 200, max: 399 }, 409));

export const options = {
  scenarios: {
    realistic_mixed: {
      executor: 'constant-arrival-rate',
      rate: TARGET_RPS,
      timeUnit: '1s',
      duration: DURATION,
      preAllocatedVUs: PRE_ALLOCATED_VUS,
      maxVUs: MAX_VUS,
    },
  },
  thresholds: {
    http_req_failed: ['rate<0.02'],
    http_req_duration: ['p(95)<1000', 'p(99)<2500'],
    checks: ['rate>0.98'],
    stage_l_request_failed: ['rate<0.02'],
    'http_req_duration{persona:market_watcher}': ['p(95)<800'],
    'http_req_duration{persona:chat_texter}': ['p(95)<1200'],
    'http_req_duration{persona:profile_browser}': ['p(95)<1200'],
  },
  summaryTrendStats: ['avg', 'min', 'med', 'p(90)', 'p(95)', 'p(99)', 'max'],
  noConnectionReuse: false,
  userAgent: 'trading-bot-stage-l3-k6/1.0',
};

export const scenarioContract = [
  {
    persona: 'market_watcher',
    weight: 25,
    endpoints: [
      'GET /api/trading-settings',
      'GET /api/trading-settings/market-state',
      'GET /api/commodities',
      'GET /api/offers',
      'GET /api/trades/my',
      'GET /api/notifications/unread-count',
    ],
  },
  {
    persona: 'offer_maker',
    weight: 10,
    endpoints: ['GET /api/offers', 'GET /api/offers/my', 'POST /api/offers'],
  },
  {
    persona: 'trade_taker',
    weight: 10,
    endpoints: ['GET /api/offers', 'GET /api/trades/my', 'GET /api/trades/{trade_id}', 'POST /api/trades'],
  },
  {
    persona: 'chat_texter',
    weight: 20,
    endpoints: [
      'GET /api/chat/conversations',
      'GET /api/chat/messages/{user_id}',
      'GET /api/chat/rooms/{chat_id}/messages',
      'POST /api/chat/send',
      'POST /api/chat/rooms/{chat_id}/send',
      'GET /api/chat/poll',
    ],
  },
  {
    persona: 'chat_media_sender',
    weight: 8,
    endpoints: [
      'POST /api/chat/upload-batches',
      'POST /api/chat/upload-sessions',
      'PATCH /api/chat/upload-sessions/{session_id}/chunk',
      'POST /api/chat/upload-sessions/{session_id}/finalize',
      'POST /api/chat/upload-batches/{batch_id}/commit',
    ],
  },
  {
    persona: 'profile_browser',
    weight: 15,
    endpoints: [
      'GET /api/auth/me',
      'GET /api/users-public/search',
      'GET /api/users-public/{user_id}',
      'GET /api/users-public/{user_id}/project-users',
      'GET /api/customers/owner-relations',
      'GET /api/accountants/owner-relations',
    ],
  },
  {
    persona: 'notification_user',
    weight: 7,
    endpoints: [
      'GET /api/notifications',
      'GET /api/notifications/unread',
      'GET /api/notifications/unread-count',
      'PATCH /api/notifications/{notification_id}/read',
      'POST /api/notifications/mark-all-read',
    ],
  },
  {
    persona: 'admin_light_read',
    weight: 5,
    endpoints: [
      'GET /api/users',
      'GET /api/admin-messages/market/current',
      'GET /api/admin-messages/market/history',
      'GET /api/admin-messages/broadcasts/history',
      'GET /api/trading-settings/market-overrides',
    ],
  },
];

const weightedPersonas = [];
scenarioContract.forEach((item) => {
  for (let index = 0; index < item.weight; index += 1) {
    weightedPersonas.push(item.persona);
  }
});

const authPool = AUTH_POOL_PATH ? JSON.parse(open(AUTH_POOL_PATH)) : null;
const personaPoolKeys = {
  market_watcher: 'market_watchers',
  offer_maker: 'offer_makers',
  trade_taker: 'trade_takers',
  chat_texter: 'chat_texters',
  chat_media_sender: 'chat_media_senders',
  profile_browser: 'profile_browsers',
  notification_user: 'notification_users',
  admin_light_read: 'admin_light_read',
};

function randomItem(items, fallback = null) {
  if (!items || items.length === 0) {
    return fallback;
  }
  return items[Math.floor(Math.random() * items.length)];
}

function tokenEntry(persona) {
  const poolKey = personaPoolKeys[persona] || persona;
  const entries = authPool && authPool.personas ? authPool.personas[poolKey] || [] : [];
  return randomItem(entries);
}

function targetList(name) {
  return authPool && authPool.targets ? authPool.targets[name] || [] : [];
}

function directPairFor(entry) {
  const pairs = targetList('direct_pairs').filter(function (pair) {
    return pair.sender_id === entry.user_id || pair.receiver_id === entry.user_id;
  });
  return randomItem(pairs);
}

function directOtherUserId(entry, pair) {
  if (!pair) return null;
  return pair.sender_id === entry.user_id ? pair.receiver_id : pair.sender_id;
}

function roomFor(entry, lists) {
  const rooms = [];
  lists.forEach(function (name) {
    targetList(name).forEach(function (room) {
      const memberIds = room.member_ids || [];
      if (memberIds.indexOf(entry.user_id) !== -1) {
        rooms.push(room);
      }
    });
  });
  return randomItem(rooms);
}

function authHeaders(entry, extra) {
  const headers = {
    Authorization: `${entry.token_type || 'bearer'} ${entry.access_token}`,
    'Content-Type': 'application/json',
    'X-Load-Test': 'stage-l3',
  };
  Object.keys(extra || {}).forEach(function (key) {
    headers[key] = extra[key];
  });
  return {
    headers: headers,
  };
}

function requestParams(entry, persona, endpoint, extra) {
  const params = authHeaders(entry, extra);
  params.tags = { persona: persona, endpoint: endpoint };
  return params;
}

function url(path) {
  return `${BASE_URL}${API_PREFIX}${path}`;
}

function record(persona, endpoint, res, expected = [200], allowBusinessRejection = false) {
  const ok = expected.includes(res.status) || (allowBusinessRejection && res.status === 409);
  check(res, {
    [`${persona} ${endpoint} status accepted`]: () => ok,
  });
  stageRequestFailed.add(!ok, { persona, endpoint });
  stagePersonaRequests.add(1, { persona, endpoint });
  if (res.status === 409) {
    stageBusinessRejections.add(1, { persona, endpoint });
  }
  return ok;
}

function getJson(persona, entry, endpoint, path, expected = [200]) {
  const res = http.get(url(path), requestParams(entry, persona, endpoint));
  record(persona, endpoint, res, expected);
  return res;
}

function postJson(persona, entry, endpoint, path, payload, expected = [200, 201], allowBusinessRejection = false) {
  const res = http.post(url(path), JSON.stringify(payload), requestParams(entry, persona, endpoint));
  record(persona, endpoint, res, expected, allowBusinessRejection);
  return res;
}

function patchJson(persona, entry, endpoint, path, payload, expected = [200, 204]) {
  const res = http.patch(url(path), JSON.stringify(payload), requestParams(entry, persona, endpoint));
  record(persona, endpoint, res, expected);
  return res;
}

function marketWatcher() {
  const persona = 'market_watcher';
  const entry = tokenEntry(persona);
  if (!entry) return;
  const choices = [
    () => getJson(persona, entry, 'trading_settings', '/trading-settings/'),
    () => getJson(persona, entry, 'market_state', '/trading-settings/market-state'),
    () => getJson(persona, entry, 'commodities', '/commodities/'),
    () => getJson(persona, entry, 'offers_list', '/offers/?limit=30'),
    () => getJson(persona, entry, 'trades_my', '/trades/my?limit=20'),
    () => getJson(persona, entry, 'notifications_unread_count', '/notifications/unread-count'),
  ];
  randomItem(choices)();
}

function offerMaker() {
  const persona = 'offer_maker';
  const entry = tokenEntry(persona);
  if (!entry) return;
  if (!INCLUDE_MUTATIONS || Math.random() < 0.65) {
    randomItem([
      () => getJson(persona, entry, 'offers_list', '/offers/?limit=30'),
      () => getJson(persona, entry, 'offers_my', '/offers/my?limit=30'),
    ])();
    return;
  }
  const commodity = randomItem(targetList('commodities'), { id: 1 });
  const iterationId = exec.scenario.iterationInTest;
  postJson(
    persona,
    entry,
    'offers_create',
    '/offers',
    {
      offer_type: Math.random() < 0.5 ? 'buy' : 'sell',
      commodity_id: commodity.id,
      quantity: 5 + (iterationId % 20),
      price: 100000 + (iterationId % 5000),
      is_wholesale: true,
      notes: `loadtest offer ${authPool.prefix} ${iterationId}`,
      idempotency_key: `${authPool.prefix}k6_offer_${iterationId}`,
      warning_acknowledged: true,
    },
    [201],
    true,
  );
}

function tradeTaker() {
  const persona = 'trade_taker';
  const entry = tokenEntry(persona);
  if (!entry) return;
  const trade = randomItem(targetList('trades').filter(function (item) {
    return item.offer_user_id === entry.user_id || item.responder_user_id === entry.user_id;
  }));
  const offer = randomItem(targetList('offers'));
  if (!INCLUDE_MUTATIONS || !offer || Math.random() < 0.75) {
    randomItem([
      () => getJson(persona, entry, 'offers_list', '/offers/?limit=30'),
      () => getJson(persona, entry, 'trades_my', '/trades/my?limit=20'),
      () => (trade ? getJson(persona, entry, 'trade_detail', `/trades/${trade.trade_id}`) : getJson(persona, entry, 'trades_my', '/trades/my?limit=20')),
    ])();
    return;
  }
  postJson(
    persona,
    entry,
    'trades_create',
    '/trades',
    {
      offer_id: offer.offer_id,
      quantity: 1,
      idempotency_key: `${authPool.prefix}k6_trade_${exec.scenario.iterationInTest}`,
    },
    [201],
    true,
  );
}

function chatTexter() {
  const persona = 'chat_texter';
  const entry = tokenEntry(persona);
  if (!entry) return;
  const directPair = directPairFor(entry);
  const otherUserId = directOtherUserId(entry, directPair);
  const room = roomFor(entry, ['groups', 'channels']);
  if (!INCLUDE_MUTATIONS || Math.random() < 0.55) {
    randomItem([
      () => getJson(persona, entry, 'chat_conversations', '/chat/conversations'),
      () => getJson(persona, entry, 'chat_poll', '/chat/poll'),
      () => (otherUserId ? getJson(persona, entry, 'direct_messages', `/chat/messages/${otherUserId}?limit=30`) : getJson(persona, entry, 'chat_conversations', '/chat/conversations')),
      () => (room ? getJson(persona, entry, 'room_messages', `/chat/rooms/${room.chat_id}/messages?limit=30`) : getJson(persona, entry, 'chat_conversations', '/chat/conversations')),
    ])();
    return;
  }
  if (room && Math.random() < 0.55) {
    postJson(persona, entry, 'room_send', `/chat/rooms/${room.chat_id}/send`, {
      content: `loadtest room text ${authPool.prefix} ${exec.scenario.iterationInTest}`,
      message_type: 'text',
    });
    return;
  }
  if (otherUserId) {
    postJson(persona, entry, 'direct_send', '/chat/send', {
      receiver_id: otherUserId,
      content: `loadtest direct text ${authPool.prefix} ${exec.scenario.iterationInTest}`,
      message_type: 'text',
    });
  }
}

function chatMediaSender() {
  const persona = 'chat_media_sender';
  const entry = tokenEntry(persona) || tokenEntry('chat_texter');
  if (!entry) return;
  if (!INCLUDE_MEDIA || !INCLUDE_MUTATIONS) {
    chatTexter();
    return;
  }
  const room = roomFor(entry, ['groups']);
  const directPair = directPairFor(entry);
  const otherUserId = directOtherUserId(entry, directPair);
  const isRoom = Boolean(room && Math.random() < 0.5);
  const targetId = isRoom ? room.chat_id : otherUserId;
  if (!targetId) {
    chatTexter();
    return;
  }
  const roomKind = isRoom ? 'group' : 'direct';
  const iterationId = exec.scenario.iterationInTest;
  const batchRes = postJson(persona, entry, 'upload_batch_create', '/chat/upload-batches', {
    room_kind: roomKind,
    target_id: targetId,
    message_kind: 'single',
    expected_items: 1,
    caption_policy: 'none',
    idempotency_key: `${authPool.prefix}k6_batch_${iterationId}`,
  }, [201]);
  if (batchRes.status !== 201) return;
  const batch = batchRes.json();
  const body = 'stage-l3-synthetic-media';
  const sessionRes = postJson(persona, entry, 'upload_session_create', '/chat/upload-sessions', {
    batch_id: batch.batch_id,
    room_kind: roomKind,
    target_id: targetId,
    media_type: 'image',
    file_name: `${authPool.prefix}k6_image_${iterationId}.txt`,
    mime_type: 'text/plain',
    total_bytes: body.length,
    chunk_size: body.length,
    preview_metadata: { width: 1, height: 1, caption: `loadtest media ${iterationId}` },
  }, [201]);
  if (sessionRes.status !== 201) return;
  const session = sessionRes.json();
  const chunkRes = http.patch(url(`/chat/upload-sessions/${session.session_id}/chunk`), {
    resume_token: session.resume_token,
    offset: '0',
    is_last_chunk: 'true',
    chunk: http.file(body, 'stage-l3.txt', 'text/plain'),
  }, {
    headers: {
      Authorization: `${entry.token_type || 'bearer'} ${entry.access_token}`,
      'X-Load-Test': 'stage-l3',
    },
    tags: { persona, endpoint: 'upload_chunk' },
  });
  if (!record(persona, 'upload_chunk', chunkRes, [200])) return;
  const finalizeRes = postJson(persona, entry, 'upload_finalize', `/chat/upload-sessions/${session.session_id}/finalize`, {}, [200]);
  if (finalizeRes.status !== 200) return;
  postJson(persona, entry, 'upload_batch_commit', `/chat/upload-batches/${batch.batch_id}/commit`, {}, [200]);
}

function profileBrowser() {
  const persona = 'profile_browser';
  const entry = tokenEntry(persona);
  if (!entry) return;
  const publicUserId = randomItem(targetList('public_profile_user_ids'), entry.user_id);
  randomItem([
    () => getJson(persona, entry, 'auth_me', '/auth/me'),
    () => getJson(persona, entry, 'users_public_search', '/users-public/search?q=loadtest&limit=20'),
    () => getJson(persona, entry, 'users_public_detail', `/users-public/${publicUserId}`),
    () => getJson(persona, entry, 'project_users', `/users-public/${entry.user_id}/project-users?limit=30`),
    () => getJson(persona, entry, 'customer_relations', '/customers/owner-relations'),
    () => getJson(persona, entry, 'accountant_relations', '/accountants/owner-relations'),
  ])();
}

function notificationUser() {
  const persona = 'notification_user';
  const entry = tokenEntry(persona);
  if (!entry) return;
  if (!INCLUDE_MUTATIONS || Math.random() < 0.75) {
    randomItem([
      () => getJson(persona, entry, 'notifications_list', '/notifications/?limit=30'),
      () => getJson(persona, entry, 'notifications_unread', '/notifications/unread?limit=30'),
      () => getJson(persona, entry, 'notifications_unread_count', '/notifications/unread-count'),
    ])();
    return;
  }
  postJson(persona, entry, 'notifications_mark_all_read', '/notifications/mark-all-read', {}, [204]);
}

function adminLightRead() {
  const persona = 'admin_light_read';
  const entry = tokenEntry(persona);
  if (!entry) return;
  randomItem([
    () => getJson(persona, entry, 'admin_users', '/users/?limit=30'),
    () => getJson(persona, entry, 'admin_market_current', '/admin-messages/market/current', [200, 404]),
    () => getJson(persona, entry, 'admin_market_history', '/admin-messages/market/history?limit=20'),
    () => getJson(persona, entry, 'admin_broadcast_history', '/admin-messages/broadcasts/history?limit=20'),
    () => getJson(persona, entry, 'admin_market_overrides', '/trading-settings/market-overrides'),
  ])();
}

const personaHandlers = {
  market_watcher: marketWatcher,
  offer_maker: offerMaker,
  trade_taker: tradeTaker,
  chat_texter: chatTexter,
  chat_media_sender: chatMediaSender,
  profile_browser: profileBrowser,
  notification_user: notificationUser,
  admin_light_read: adminLightRead,
};

export function setup() {
  if (!authPool || !authPool.personas) {
    throw new Error('AUTH_POOL_PATH is required and must point to a Stage L2 auth-pool JSON file');
  }
  return {
    prefix: authPool.prefix || '',
    profile: LOAD_PROFILE,
    include_media: INCLUDE_MEDIA,
    include_mutations: INCLUDE_MUTATIONS,
    target_rps: TARGET_RPS,
  };
}

export default function () {
  const persona = randomItem(weightedPersonas, 'market_watcher');
  stagePersonaIterations.add(1, { persona });
  const handler = personaHandlers[persona] || marketWatcher;
  handler();
  sleep(Math.random() * 0.05);
}
