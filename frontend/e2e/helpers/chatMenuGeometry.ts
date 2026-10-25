import type { Page, TestInfo } from '@playwright/test'

const SCHEMA = 'browser_ci_chat_menu_geometry/1'
const OVERFLOW = new Set(['visible', 'hidden', 'clip', 'scroll', 'auto', 'unclassified'])
const HIT = new Set(['delete', 'menu', 'overlay', 'other', 'outside', 'missing'])
const BOX_KEYS = ['top', 'right', 'bottom', 'left', 'width', 'height', 'client_height', 'scroll_height', 'overflow_y']

// Serialized into the synthetic browser. Read only these fixed selectors and
// numeric layout properties; never return text, DOM markup, attributes or IDs.
export function readChatMenuGeometry() {
  const round = (value: number) => Math.round(value * 100) / 100
  const box = (element: Element | null) => {
    if (!element) return null
    const rect = element.getBoundingClientRect()
    const overflow = getComputedStyle(element).overflowY
    return {
      top: round(rect.top), right: round(rect.right), bottom: round(rect.bottom),
      left: round(rect.left), width: round(rect.width), height: round(rect.height),
      client_height: element.clientHeight, scroll_height: element.scrollHeight,
      overflow_y: ['visible', 'hidden', 'clip', 'scroll', 'auto'].includes(overflow) ? overflow : 'unclassified',
    }
  }
  const menus = document.querySelectorAll('.context-menu')
  const buttons = document.querySelectorAll('.context-menu .menu-item.is-danger')
  const rows = document.querySelectorAll('.context-menu .menu-item')
  if (menus.length > 2 || buttons.length > 2 || rows.length > 16) throw new Error('geometry_bound')
  const menu = menus.length === 1 ? menus[0] || null : null
  const button = buttons.length === 1 ? buttons[0] || null : null
  const target = box(button)
  let hit = 'missing'
  let inside = false
  if (target) {
    const x = (target.left + target.right) / 2
    const y = (target.top + target.bottom) / 2
    inside = x >= 0 && y >= 0 && x < window.innerWidth && y < window.innerHeight
    if (!inside) hit = 'outside'
    else {
      const element = document.elementFromPoint(x, y)
      hit = element && button?.contains(element) ? 'delete'
        : element && menu?.contains(element) ? 'menu'
          : element?.matches('.context-overlay') ? 'overlay' : 'other'
    }
  }
  return {
    schema: 'browser_ci_chat_menu_geometry/1', status: 'observed',
    viewport: { width: window.innerWidth, height: window.innerHeight },
    menu_count: menus.length, delete_count: buttons.length,
    menu: box(menu), reactions: box(menu?.querySelector('.reaction-picker-shell') || null),
    actions: box(menu?.querySelector('.menu-actions-panel') || null), delete: target,
    action_heights: Array.from(rows, element => round(element.getBoundingClientRect().height)),
    action_min_heights: Array.from(rows, element => round(parseFloat(getComputedStyle(element).minHeight))),
    delete_center_inside: inside, center_hit: hit,
  }
}

export function admitChatMenuGeometry(value: unknown): void {
  const keys = (value: unknown, expected: string[]) => {
    if (!value || typeof value !== 'object' || Array.isArray(value)
        || Object.keys(value).sort().join(',') !== [...expected].sort().join(',')) throw new Error('geometry_shape')
  }
  const number = (value: unknown, minimum = -100000, maximum = 100000) => {
    if (typeof value !== 'number' || !Number.isFinite(value) || value < minimum || value > maximum) {
      throw new Error('geometry_number')
    }
  }
  keys(value, ['schema', 'status', 'viewport', 'menu_count', 'delete_count', 'menu', 'reactions', 'actions',
    'delete', 'action_heights', 'action_min_heights', 'delete_center_inside', 'center_hit'])
  const row = value as ReturnType<typeof readChatMenuGeometry>
  if (row.schema !== SCHEMA || row.status !== 'observed' || typeof row.delete_center_inside !== 'boolean'
      || !HIT.has(row.center_hit)) throw new Error('geometry_enum')
  keys(row.viewport, ['width', 'height'])
  number(row.viewport.width, 1); number(row.viewport.height, 1)
  for (const count of [row.menu_count, row.delete_count]) {
    number(count, 0, 2)
    if (!Number.isInteger(count)) throw new Error('geometry_count')
  }
  for (const item of [row.menu, row.reactions, row.actions, row.delete]) {
    if (item === null) continue
    keys(item, BOX_KEYS)
    for (const name of BOX_KEYS.filter(name => name !== 'overflow_y')) {
      number(item[name as keyof typeof item], ['width', 'height', 'client_height', 'scroll_height'].includes(name) ? 0 : -100000)
    }
    if (!OVERFLOW.has(item.overflow_y)) throw new Error('geometry_enum')
  }
  for (const heights of [row.action_heights, row.action_min_heights]) {
    if (!Array.isArray(heights) || heights.length > 16) throw new Error('geometry_bound')
    for (const height of heights) number(height, 0, 10000)
  }
  if (row.action_heights.length !== row.action_min_heights.length) throw new Error('geometry_shape')
}

export async function attachChatMenuGeometry(page: Page, testInfo: TestInfo): Promise<void> {
  let body: string
  try {
    const observation = await page.evaluate(readChatMenuGeometry)
    admitChatMenuGeometry(observation)
    body = JSON.stringify(observation)
    if (Buffer.byteLength(body) > 4096) throw new Error('geometry_bound')
  } catch {
    body = JSON.stringify({ schema: SCHEMA, status: 'unavailable' })
  }
  try {
    await testInfo.attach('chat-menu-geometry', { body: Buffer.from(body), contentType: 'application/json' })
  } catch {
    // Diagnostic storage cannot replace the unchanged real click/assertions.
  }
}
