import { describe, expect, it } from 'vitest'
import {
  apiFixture,
  classifyConsole,
  diagnosticCounts,
  isErrorInjectablePath,
  isIdentityBootstrapPath,
} from './lib/stage8-full-acceptance-runtime.mjs'

const profile = { authenticated: true, role: 'عادی', userIdOffset: 1 }

describe('stage8 full-acceptance fixture contract', () => {
  it('keeps identity bootstrap healthy in error and offline modes', () => {
    for (const mode of ['error', 'offline']) {
      expect(apiFixture('/api/auth/me', 'GET', profile, mode).status).toBe(200)
      expect(apiFixture('/api/auth/refresh', 'POST', profile, mode).status).toBe(200)
      expect(apiFixture('/api/sessions/verify', 'POST', profile, mode).status).toBe(200)
      expect(isIdentityBootstrapPath('/api/auth/me', 'GET')).toBe(true)
    }
  })

  it('injects page-data error and offline without targeting identity', () => {
    expect(isErrorInjectablePath('/api/auth/me')).toBe(false)
    expect(apiFixture('/api/offers/page', 'GET', profile, 'error')).toMatchObject({
      status: 500,
      injectedError: true,
    })
    expect(apiFixture('/api/offers/page', 'GET', profile, 'offline')).toMatchObject({
      status: 503,
      offline: true,
    })
  })

  it('uses invitation 410 for lookup error instead of a generic 500', () => {
    expect(apiFixture('/api/invitations/lookup/Stg8Inv1', 'GET', profile, 'error')).toMatchObject({
      status: 410,
      injectedError: true,
    })
  })

  it('classifies injected network console as fixture diagnostics', () => {
    expect(
      classifyConsole({
        type: 'error',
        text: 'Failed to load resource: the server responded with a status of 500 ()',
      }),
    ).toBe('fixture-injected-state')
    expect(
      classifyConsole({
        type: 'error',
        text: 'Failed to load resource: the server responded with a status of 503 ()',
      }),
    ).toBe('fixture-injected-state')
    expect(classifyConsole({ type: 'error', text: 'Failed to load commodities' })).toBe(
      'fixture-injected-companion',
    )
    const counts = diagnosticCounts(
      {
        console: [
          { type: 'error', text: 'Failed to load resource: the server responded with a status of 500 ()' },
          { type: 'error', text: 'Failed to load settings' },
        ],
        pageErrors: [],
        requestFailures: [],
        externalRequests: [],
        mutatingRequests: [],
      },
      { allowInjected: true },
    )
    expect(counts.unexpectedConsole).toBe(0)
  })
})
