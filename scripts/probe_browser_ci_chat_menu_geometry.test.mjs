import assert from 'node:assert/strict'
import test from 'node:test'
import { readChatMenuGeometry, admitChatMenuGeometry, attachChatMenuGeometry } from '../frontend/e2e/helpers/chatMenuGeometry.ts'

// Pure layout models, not browser geometry evidence. Every private/textual DOM
// property throws if the projector tries to read it.
function element(top, height, extra = {}) {
  const node = {
    clientHeight: height, scrollHeight: height,
    getBoundingClientRect: () => ({ top, bottom: top + height, left: 100, right: 396, width: 296, height }),
    contains: target => target === node,
    matches: selector => selector === '.context-overlay' && extra.overlay === true,
    ...extra,
  }
  for (const name of ['textContent', 'innerHTML', 'outerHTML', 'className', 'id', 'attributes', 'value']) {
    Object.defineProperty(node, name, { get() { throw new Error('forbidden DOM read') } })
  }
  return node
}

async function withDOM(callback, { outside = false, hit = 'delete', count = 1, actionCount = 7 } = {}) {
  const menu = element(200, outside ? 560 : 500)
  const reactions = element(200, 102)
  const actions = element(308, outside ? 452 : 392)
  const button = element(outside ? 720 : 650, 48)
  menu.querySelector = selector => selector === '.reaction-picker-shell' ? reactions
    : selector === '.menu-actions-panel' ? actions : null
  const rows = Array.from({ length: actionCount }, (_, index) => element(310 + index * 48, 48))
  const selected = hit === 'delete' ? button : hit === 'menu' ? menu : element(0, 1, { overlay: hit === 'overlay' })
  const values = {
    window: { innerWidth: 1280, innerHeight: 720 },
    document: {
      querySelectorAll: selector => selector === '.context-menu' ? Array(count).fill(menu)
        : selector === '.context-menu .menu-item.is-danger' ? [button]
          : selector === '.context-menu .menu-item' ? rows : [],
      elementFromPoint: () => selected,
    },
    getComputedStyle: () => new Proxy({ overflowY: 'visible', minHeight: '48px' }, {
      get(target, key) {
        if (!['overflowY', 'minHeight'].includes(key)) throw new Error('forbidden style read')
        return target[key]
      },
    }),
  }
  const previous = new Map(Object.keys(values).map(key => [key, Object.getOwnPropertyDescriptor(globalThis, key)]))
  for (const [key, value] of Object.entries(values)) Object.defineProperty(globalThis, key, { configurable: true, value })
  try { return await callback() } finally {
    for (const [key, descriptor] of previous) {
      if (descriptor) Object.defineProperty(globalThis, key, descriptor)
      else delete globalThis[key]
    }
  }
}

test('off-screen model reports measured numeric geometry without reading DOM content', async () => {
  await withDOM(() => {
    const value = readChatMenuGeometry()
    admitChatMenuGeometry(value)
    assert.equal(value.delete_center_inside, false)
    assert.equal(value.center_hit, 'outside')
    assert.ok(value.menu.bottom > value.viewport.height)
    assert.deepEqual(value.action_min_heights, Array(7).fill(48))
    assert.ok(Buffer.byteLength(JSON.stringify(value)) <= 4096)
    assert.ok(!JSON.stringify(value).includes('forbidden'))
  }, { outside: true })
})

for (const hit of ['delete', 'menu', 'overlay', 'other']) {
  test(`center hit is the closed ${hit} enum only`, async () => {
    await withDOM(() => {
      const value = readChatMenuGeometry()
      admitChatMenuGeometry(value)
      assert.equal(value.delete_center_inside, true)
      assert.equal(value.center_hit, hit)
    }, { hit })
  })
}

test('DOM count bound refuses before converting row geometry', async () => {
  await withDOM(() => assert.throws(readChatMenuGeometry, /geometry_bound/), { actionCount: 17 })
  await withDOM(() => assert.throws(readChatMenuGeometry, /geometry_bound/), { count: 3 })
})

test('unknown fields, nonfinite numbers, invalid dimensions and free-form enums refuse', async () => {
  await withDOM(() => {
    const original = readChatMenuGeometry()
    for (const mutate of [
      value => { value.message = 'private sentinel' },
      value => { value.viewport.width = Infinity },
      value => { value.menu.top = NaN },
      value => { value.delete.width = -1 },
      value => { value.menu_count = true },
      value => { value.menu_count = 1.5 },
      value => { value.menu.overflow_y = 'private sentinel' },
      value => { value.center_hit = 'private sentinel' },
      value => { value.action_heights = Array(17).fill(1) },
      value => { value.action_min_heights.pop() },
    ]) {
      const value = structuredClone(original)
      mutate(value)
      assert.throws(() => admitChatMenuGeometry(value), /geometry_/)
    }
  })
})

test('attachment admits only the bounded projection and the fixed filename', async () => {
  await withDOM(async () => {
    const attachments = []
    await attachChatMenuGeometry({ evaluate: callback => callback() }, {
      async attach(name, value) { attachments.push({ name, ...value }) },
    })
    assert.equal(attachments.length, 1)
    assert.equal(attachments[0].name, 'chat-menu-geometry')
    assert.equal(attachments[0].contentType, 'application/json')
    admitChatMenuGeometry(JSON.parse(attachments[0].body))
  })
})

test('evaluation or validation faults emit only unavailable; failed attachment never replaces the real test action', async () => {
  for (const evaluate of [() => { throw new Error('private sentinel') }, () => ({ message: 'private sentinel' })]) {
    let body
    await attachChatMenuGeometry({ evaluate }, { async attach(_name, value) { body = value.body.toString() } })
    assert.deepEqual(JSON.parse(body), { schema: 'browser_ci_chat_menu_geometry/1', status: 'unavailable' })
    assert.ok(!body.includes('private'))
    await attachChatMenuGeometry({ evaluate }, { async attach() { throw new Error('private sentinel') } })
  }
})
