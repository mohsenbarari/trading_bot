import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'
import {
  ENVIRONMENTS,
  apiFixture,
  classifyConsole,
  diagnosticCounts,
  isErrorInjectablePath,
  isIdentityBootstrapPath,
  loadMatrix,
  shouldHoldLoadingPath,
} from './lib/stage8-full-acceptance-runtime.mjs'

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..')
const runtimeSource = fs.readFileSync(
  path.join(repoRoot, 'frontend/scripts/lib/stage8-full-acceptance-runtime.mjs'),
  'utf8',
)
const browserSource = fs.readFileSync(
  path.join(repoRoot, 'frontend/scripts/stage8-full-acceptance-browser.mjs'),
  'utf8',
)

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

  it('keeps sessions and invitations on the injectable page-data boundary', () => {
    expect(isErrorInjectablePath('/api/sessions/active')).toBe(true)
    expect(isErrorInjectablePath('/api/auth/me/offer-overtime')).toBe(true)
    expect(isErrorInjectablePath('/api/auth/me')).toBe(false)
    expect(isIdentityBootstrapPath('/api/auth/me', 'GET')).toBe(true)
    expect(apiFixture('/api/sessions/active', 'GET', profile, 'empty').body).toEqual([])
    expect(apiFixture('/api/sessions/active', 'GET', profile, 'dense').body).toHaveLength(24)
    expect(apiFixture('/api/sessions/active', 'GET', profile, 'stale-old').body[0].device_name).toBe(
      'کهنه-پذیرش',
    )
    expect(apiFixture('/api/sessions/active', 'GET', profile, 'stale-new').body[0].device_name).toBe(
      'تازه-پذیرش',
    )
    expect(apiFixture('/api/invitations/pending', 'GET', profile, 'dense').body).toHaveLength(24)
    expect(apiFixture('/api/commodities', 'GET', profile, 'dense').body).toHaveLength(24)
    expect(apiFixture('/api/commodities', 'GET', profile, 'normal').body[0]).toMatchObject({
      aliases: [],
    })
    expect(apiFixture('/api/offers/page', 'GET', profile, 'dense').body.items).toHaveLength(24)
    expect(apiFixture('/api/offers/page', 'GET', profile, 'dense').body.items[0]).toMatchObject({
      accepts_new_public_interaction: true,
      lifecycle_phase: 'normal',
    })
    expect(apiFixture('/api/offers/page', 'GET', profile, 'normal').body.items[1]).toMatchObject({
      lifecycle_phase: 'overtime',
    })
    expect(apiFixture('/api/offers/market-history', 'GET', profile, 'normal').body).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ history_state: 'expired' }),
        expect.objectContaining({ history_state: 'traded' }),
      ]),
    )
    expect(apiFixture('/api/chat/conversations', 'GET', profile, 'dense').body).toHaveLength(24)
    expect(apiFixture('/api/chat/conversations', 'GET', profile, 'empty').body).toEqual([])
    expect(apiFixture('/api/chat/conversations', 'GET', profile, 'error')).toMatchObject({
      status: 422,
      injectedError: true,
    })
    expect(apiFixture('/api/auth/me', 'GET', profile, 'error', { allowIdentityPageData: true })).toMatchObject({
      status: 422,
      injectedError: true,
    })
    expect(apiFixture('/api/notifications', 'GET', profile, 'dense').body).toHaveLength(24)
    expect(apiFixture('/api/notifications', 'GET', profile, 'normal').body[0]).toMatchObject({
      category: 'trade',
    })
    expect(apiFixture('/api/notifications', 'GET', profile, 'stale-old').body[0].title).toBe('کهنه-پذیرش')
  })

  it('names the local PWA lane as a simulation and never re-injects Telegram from the stub', () => {
    expect(ENVIRONMENTS).toContain('pwa-simulation')
    expect(ENVIRONMENTS).not.toContain('pwa')
    expect(runtimeSource).toMatch(/telegram script is environment-injected/)
    expect(runtimeSource).not.toMatch(/window\.Telegram=window\.Telegram\|\|/)
  })

  it('holds only the descriptor loading endpoint so companion requests can finish', () => {
    const controller = {
      mode: 'loading',
      holdEndpoint: '/api/sessions/active',
      releaseHeldRequest: false,
    }
    expect(shouldHoldLoadingPath('/api/sessions/active', controller)).toBe(true)
    expect(shouldHoldLoadingPath('/api/notifications/', {
      mode: 'loading',
      holdEndpoint: '/api/notifications',
    })).toBe(true)
    expect(shouldHoldLoadingPath('/api/chat/poll', controller)).toBe(false)
    expect(shouldHoldLoadingPath('/api/notifications/', controller)).toBe(false)
    expect(shouldHoldLoadingPath('/api/sessions/active', { mode: 'loading', holdEndpoint: '' })).toBe(
      false,
    )
    expect(shouldHoldLoadingPath('/api/sessions/active', { mode: 'normal', holdEndpoint: '/api/sessions/active' })).toBe(
      false,
    )
    expect(runtimeSource).toMatch(/shouldHoldLoadingPath\(pathname, controller\)/)
    expect(runtimeSource).toMatch(/allowIdentityPageData/)
    expect(runtimeSource).toMatch(/seedCurrentUserSummary/)
    expect(runtimeSource).toMatch(/marketLifecycle/)
    expect(runtimeSource).toMatch(/fixtureConversation/)
    expect(runtimeSource).not.toMatch(
      /\(controller\.mode === 'loading' \|\| controller\.mode === 'slow'\) && delayable\) \{/,
    )
    expect(browserSource).toMatch(/controller\.holdEndpoint/)
    expect(runtimeSource).toMatch(/recoverIdentityPageDataAfterHold/)
    expect(browserSource).toMatch(/recoverIdentityPageDataAfterHold\(page\)/)
  })

  it('does not restore the generic all-states applicability fallback', () => {
    expect(runtimeSource).not.toMatch(/return ALL_STATES\.map\(yes\)/)
    expect(runtimeSource).toMatch(/stateApplicabilityFromDescriptor/)
    expect(runtimeSource).toMatch(/\/__stage8\/status/)
    expect(runtimeSource).toMatch(/waitForMountedPendingMidProbe/)
    expect(browserSource).toMatch(/waitForMountedPendingMidProbe/)
    expect(browserSource).not.toMatch(/s8stale/)
  })

  it('fails closed when the matrix drifts from source and the runner sees sourceDrift', () => {
    const matrixPath = path.join(repoRoot, 'docs/uiux-stage8-acceptance-rollout/ACCEPTANCE_MATRIX.json')
    expect(() => loadMatrix(matrixPath)).not.toThrow()
    expect(browserSource).toMatch(/if \(expected\.sourceDrift\)/)
    expect(browserSource).toMatch(/evaluateOfficialPass/)
    expect(browserSource).not.toMatch(/ALLOWED_DIRTY/)
  })
})
