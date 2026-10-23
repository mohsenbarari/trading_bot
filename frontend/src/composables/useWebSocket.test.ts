import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const socketMocks = vi.hoisted(() => ({
  cleanDeletedSuffixes: vi.fn((value: unknown) => value),
}))

vi.mock('../utils/formatters', () => ({
  cleanDeletedSuffixes: socketMocks.cleanDeletedSuffixes,
}))

class MockWebSocket {
  static CONNECTING = 0
  static OPEN = 1
  static CLOSED = 3
  static instances: MockWebSocket[] = []

  readyState = MockWebSocket.CONNECTING
  url: string
  send = vi.fn()
  close = vi.fn(() => {
    this.readyState = MockWebSocket.CLOSED
    this.onclose?.(new CloseEvent('close'))
  })
  onopen: ((event: Event) => void) | null = null
  onmessage: ((event: MessageEvent) => void) | null = null
  onclose: ((event: CloseEvent) => void) | null = null
  onerror: ((event: Event) => void) | null = null

  constructor(url: string) {
    this.url = url
    MockWebSocket.instances.push(this)
  }

  emitOpen() {
    this.readyState = MockWebSocket.OPEN
    this.onopen?.(new Event('open'))
  }

  emitMessage(data: string) {
    this.onmessage?.(new MessageEvent('message', { data }))
  }

  emitError() {
    this.onerror?.(new Event('error'))
  }
}

describe('useWebSocket', () => {
  let originalLocation: Location

  async function importFreshModule() {
    vi.resetModules()
    return import('./useWebSocket')
  }

  beforeEach(() => {
    vi.useFakeTimers()
    MockWebSocket.instances = []
    socketMocks.cleanDeletedSuffixes.mockClear()
    localStorage.clear()
    vi.stubGlobal('WebSocket', MockWebSocket as any)
    originalLocation = window.location
    Object.defineProperty(window, 'location', {
      configurable: true,
      value: { protocol: 'https:', port: '', host: 'coin.test', hostname: 'coin.test' },
    })
  })

  afterEach(() => {
    Object.defineProperty(window, 'location', { configurable: true, value: originalLocation })
    vi.unstubAllGlobals()
    vi.runOnlyPendingTimers()
    vi.useRealTimers()
    localStorage.clear()
  })

  it('skips connection without an auth token', async () => {
    const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => undefined)
    const { useWebSocket } = await importFreshModule()

    useWebSocket().connect()

    expect(MockWebSocket.instances).toHaveLength(0)
    expect(warnSpy).toHaveBeenCalled()
    warnSpy.mockRestore()
  })

  it('connects, dispatches events, sends heartbeats, reconnects on close, and disconnects cleanly', async () => {
    localStorage.setItem('auth_token', 'token-123')
    const parseErrorSpy = vi.spyOn(console, 'error').mockImplementation(() => undefined)
    const { useWebSocket } = await importFreshModule()
    const ws = useWebSocket()
    const reconnectListener = vi.fn()
    const chatListener = vi.fn()
    const wildcardListener = vi.fn()

    ws.on('ws:reconnect', reconnectListener)
    ws.on('chat:message', chatListener)
    ws.on('*', wildcardListener)
    ws.connect()

    expect(MockWebSocket.instances).toHaveLength(1)
    expect(MockWebSocket.instances[0]!.url).toBe('wss://coin.test/api/realtime/ws?token=token-123')

    MockWebSocket.instances[0]!.emitOpen()
    expect(ws.isConnected.value).toBe(true)
    expect(reconnectListener).toHaveBeenCalledTimes(1)
    expect(ws.sendPresenceUpdate('/market', true)).toBe(true)
    expect(MockWebSocket.instances[0]?.send).toHaveBeenCalledWith(JSON.stringify({
      type: 'presence:update',
      data: {
        path: '/market',
        visible: true,
      },
    }))

    socketMocks.cleanDeletedSuffixes.mockImplementationOnce(() => ({
      type: 'chat:message',
      data: { text: 'hello' },
    }))
    MockWebSocket.instances[0]?.emitMessage('{"type":"chat:message","data":{"text":"hello"}}')
    expect(chatListener).toHaveBeenCalledWith({ text: 'hello' })

    MockWebSocket.instances[0]?.emitMessage('pong')
    socketMocks.cleanDeletedSuffixes.mockImplementationOnce(() => ({ type: 'other:event', data: { ok: true } }))
    MockWebSocket.instances[0]?.emitMessage('{"type":"other:event","data":{"ok":true}}')
    expect(wildcardListener).toHaveBeenCalledWith({ type: 'other:event', data: { ok: true } })

    MockWebSocket.instances[0]?.emitMessage('{invalid-json')
    expect(parseErrorSpy).toHaveBeenCalledWith('Error parsing WS message')

    vi.advanceTimersByTime(25000)
    expect(MockWebSocket.instances[0]?.send).toHaveBeenCalledWith('ping')

    MockWebSocket.instances[0]!.readyState = MockWebSocket.CLOSED
    MockWebSocket.instances[0]?.onclose?.(new CloseEvent('close'))
    expect(ws.isConnected.value).toBe(false)
    vi.advanceTimersByTime(3000)
    expect(MockWebSocket.instances).toHaveLength(2)

    MockWebSocket.instances[1]!.emitOpen()
    expect(reconnectListener).toHaveBeenCalledTimes(2)
    vi.advanceTimersByTime(3000)
    expect(MockWebSocket.instances).toHaveLength(2)

    ws.disconnect()
    expect(MockWebSocket.instances[1]?.close).toHaveBeenCalled()
    expect(ws.sendJson({ type: 'presence:update' })).toBe(false)
    parseErrorSpy.mockRestore()
  })

  it('uses the current dev-server host and closes the socket after websocket errors', async () => {
    localStorage.setItem('auth_token', 'dev-token')
    Object.defineProperty(window, 'location', {
      configurable: true,
      value: { protocol: 'http:', port: '5173', host: 'localhost:5173', hostname: 'localhost' },
    })
    const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => undefined)
    const { useWebSocket } = await importFreshModule()
    const ws = useWebSocket()

    ws.connect()
    expect(MockWebSocket.instances[0]?.url).toBe('ws://localhost:5173/api/realtime/ws?token=dev-token')

    MockWebSocket.instances[0]?.emitError()
    expect(errorSpy).toHaveBeenCalled()
    expect(MockWebSocket.instances[0]?.close).toHaveBeenCalled()
    errorSpy.mockRestore()
  })

  it('never logs credential URLs, raw error events or malformed payload bytes on any connection path', async () => {
    const token = 'fixture-secret/token?query&value'
    localStorage.setItem('auth_token', token)
    const logs = vi.spyOn(console, 'log').mockImplementation(() => undefined)
    const warnings = vi.spyOn(console, 'warn').mockImplementation(() => undefined)
    const errors = vi.spyOn(console, 'error').mockImplementation(() => undefined)
    try {
      const { useWebSocket } = await importFreshModule()
      const ws = useWebSocket()
      ws.connect()
      const first = MockWebSocket.instances[0]!
      expect(first.url).toBe(`wss://coin.test/api/realtime/ws?token=${encodeURIComponent(token)}`)
      first.emitOpen()
      first.emitMessage(`invalid-json-${token}`)
      first.close()
      vi.advanceTimersByTime(3000)
      const second = MockWebSocket.instances[1]!
      second.emitOpen()
      second.onerror?.({ target: { url: second.url }, message: token } as unknown as Event)
      ws.disconnect()
      localStorage.removeItem('auth_token')
      ws.connect()

      const output = JSON.stringify([...logs.mock.calls, ...warnings.mock.calls, ...errors.mock.calls], (_key, value) => (
        value instanceof Error ? { name: value.name, message: value.message, stack: value.stack } : value
      ))
      expect(output).not.toContain(token)
      expect(output).not.toContain(encodeURIComponent(token))
      expect(output).not.toContain('?token=')
      expect(output).not.toContain('wss://')
      expect(errors).toHaveBeenCalledWith('Error parsing WS message')
      expect(errors).toHaveBeenCalledWith('WebSocket Error')
      expect(warnings).toHaveBeenCalledWith('⚠️ No auth token found, skipping WebSocket connection')
    } finally {
      logs.mockRestore()
      warnings.mockRestore()
      errors.mockRestore()
    }
  })

  it('dispatches one callback per realtime event id and accepts a new id', async () => {
    localStorage.setItem('auth_token', 'token-123')
    const { useWebSocket } = await importFreshModule()
    const ws = useWebSocket()
    const listener = vi.fn()
    ws.on('offer:updated', listener)
    ws.connect()

    MockWebSocket.instances[0]!.emitOpen()
    MockWebSocket.instances[0]!.emitMessage('{"type":"offer:updated","event_id":"event-1","data":{"id":7}}')
    MockWebSocket.instances[0]!.emitMessage('{"type":"offer:updated","event_id":"event-1","data":{"id":7}}')
    MockWebSocket.instances[0]!.emitMessage('{"type":"offer:updated","event_id":"event-2","data":{"id":7}}')

    expect(listener).toHaveBeenCalledTimes(2)
    expect(listener).toHaveBeenNthCalledWith(1, { id: 7 })
    expect(listener).toHaveBeenNthCalledWith(2, { id: 7 })

    ws.disconnect()
    ws.connect()
    MockWebSocket.instances[1]!.emitOpen()
    MockWebSocket.instances[1]!.emitMessage('{"type":"offer:updated","event_id":"event-1","data":{"id":7}}')
    MockWebSocket.instances[1]!.emitMessage('{"type":"offer:updated","event_id":"event-3","data":{"id":7}}')
    expect(listener).toHaveBeenCalledTimes(3)
    ws.disconnect()
  })
})
