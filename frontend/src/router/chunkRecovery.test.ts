import { beforeEach, describe, expect, it, vi } from 'vitest'
import {
  CHUNK_RELOAD_MARKER_KEY,
  CHUNK_RELOAD_MARKER_TTL_MS,
  clearChunkReloadAttempt,
  decideChunkReload,
  sanitizeChunkReloadPath,
} from './chunkRecovery'

describe('chunk recovery policy', () => {
  beforeEach(() => {
    sessionStorage.clear()
  })

  it('returns only the pathname and strips every query string and fragment', () => {
    expect(sanitizeChunkReloadPath('/register?registration_token=raw-secret#otp-code')).toBe(
      '/register',
    )
    expect(sanitizeChunkReloadPath('/chat?user_id=7#message-8')).toBe('/chat')
    expect(sanitizeChunkReloadPath('/')).toBe('/')
  })

  it.each([
    'https://evil.example/market',
    '//evil.example/market',
    '/\\evil.example/market',
    '/market\u0000',
    '/%E0%A4%A',
    '',
    undefined,
  ])('rejects an unsafe reload target %s', (candidate) => {
    expect(sanitizeChunkReloadPath(candidate)).toBeNull()
  })

  it('shares one bounded attempt across routes and failure sources', () => {
    expect(decideChunkReload('/register?registration_token=secret', sessionStorage, 1_000)).toEqual(
      {
        kind: 'reload',
        path: '/register',
      },
    )
    expect(sessionStorage.getItem(CHUNK_RELOAD_MARKER_KEY)).toBe('1000')

    expect(decideChunkReload('/market?credential=secret', sessionStorage, 1_001)).toEqual({
      kind: 'recover',
    })
    expect(sessionStorage.length).toBe(1)
    expect(sessionStorage.key(0)).toBe(CHUNK_RELOAD_MARKER_KEY)
  })

  it('allows a new bounded incident after the marker expires or is cleared', () => {
    expect(decideChunkReload('/profile', sessionStorage, 1_000).kind).toBe('reload')
    expect(
      decideChunkReload('/profile', sessionStorage, 1_000 + CHUNK_RELOAD_MARKER_TTL_MS).kind,
    ).toBe('reload')

    clearChunkReloadAttempt(sessionStorage)
    expect(sessionStorage.getItem(CHUNK_RELOAD_MARKER_KEY)).toBeNull()
    expect(decideChunkReload('/profile', sessionStorage, 3_000_000).kind).toBe('reload')
  })

  it('fails closed when storage cannot prove or persist the bound', () => {
    const deniedStorage = {
      getItem: vi.fn(() => {
        throw new Error('storage denied')
      }),
      setItem: vi.fn(),
      removeItem: vi.fn(),
    } as unknown as Storage

    expect(decideChunkReload('/market', deniedStorage)).toEqual({ kind: 'recover' })
    expect(
      decideChunkReload('/market', {
        getItem: vi.fn(() => null),
        setItem: vi.fn(),
      } as unknown as Storage),
    ).toEqual({ kind: 'recover' })
    expect(decideChunkReload('/market', null)).toEqual({ kind: 'recover' })
    expect(decideChunkReload('https://evil.example', sessionStorage)).toEqual({ kind: 'recover' })
  })
})
