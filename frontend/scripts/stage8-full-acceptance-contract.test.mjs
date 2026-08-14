import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'
import {
  BINDING_PATHS,
  OFFICIAL_COUNTS,
  OFFICIAL_PHASES,
  assertBindingUnchanged,
  assertCleanOfficialBinding,
  assertEnvironmentSemantics,
  assertInteractionSemantics,
  assertStateSemantics,
  evaluateOfficialPass,
  hasListStateSurface,
} from './lib/stage8-full-acceptance-contract.mjs'
import {
  ENVIRONMENTS,
  apiFixture,
  isErrorInjectablePath,
  isIdentityBootstrapPath,
  stateApplicability,
  interactionApplicability,
  environmentApplicability,
} from './lib/stage8-full-acceptance-runtime.mjs'

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..')
const browserSource = fs.readFileSync(
  path.join(repoRoot, 'frontend/scripts/stage8-full-acceptance-browser.mjs'),
  'utf8',
)
const runtimeSource = fs.readFileSync(
  path.join(repoRoot, 'frontend/scripts/lib/stage8-full-acceptance-runtime.mjs'),
  'utf8',
)

const ROUTES = [
  'home',
  'setup-password',
  'login',
  'market',
  'operations',
  'operations-customers',
  'operations-customers-detail',
  'operations-accountants',
  'operations-accountants-detail',
  'account',
  'account-security',
  'account-storage',
  'account-notifications',
  'messenger',
  'public-profile',
  'profile',
  'settings',
  'admin',
  'admin-invitations',
  'admin-channels',
  'admin-users',
  'admin-user-profile',
  'admin-commodities',
  'admin-messages',
  'admin-system',
  'invite-landing',
  'web-register',
  'notifications',
  'share-receive',
  'system-recovery',
]

function officialCounters() {
  return {
    accessCellsExpected: 270,
    accessCellsExecuted: 270,
    accessCellsPassed: 270,
    routeViewportExpected: 240,
    routeViewportExecuted: 240,
    routeViewportPassed: 240,
    stateTotal: 240,
    stateExecuted: 192,
    stateNotApplicable: 48,
    statePassed: 192,
    interactionTotal: 120,
    interactionExecuted: 116,
    interactionNotApplicable: 4,
    interactionPassed: 116,
    environmentTotal: 90,
    environmentExecuted: 87,
    environmentNotApplicable: 3,
    environmentPassed: 87,
  }
}

function officialInput(overrides = {}) {
  return {
    officialRun: true,
    failed: 0,
    sourceDriftCount: 0,
    uniqueIdCount: 960,
    duplicateIdCount: 0,
    counters: officialCounters(),
    server: { unknownApiRequests: 0, mutatingApiRequests: 0 },
    diagnosticTotals: {
      unexpectedConsole: 0,
      pageErrors: 0,
      externalRequests: 0,
      requestFailuresOutsideOffline: 0,
    },
    ...overrides,
  }
}

describe('Stage 8 official pass invariants', () => {
  it('passes only the exact official matrix', () => {
    expect(evaluateOfficialPass(officialInput())).toEqual({ passed: true, failures: [] })
  })

  it.each([
    ['accessCellsExecuted', 269],
    ['accessCellsPassed', 269],
    ['routeViewportExecuted', 239],
    ['routeViewportPassed', 239],
    ['stateTotal', 239],
    ['stateExecuted', 191],
    ['stateNotApplicable', 47],
    ['statePassed', 191],
    ['interactionTotal', 119],
    ['interactionExecuted', 115],
    ['interactionNotApplicable', 3],
    ['interactionPassed', 115],
    ['environmentTotal', 89],
    ['environmentExecuted', 86],
    ['environmentNotApplicable', 2],
    ['environmentPassed', 86],
  ])('fails when %s is mutated to %s', (key, value) => {
    const counters = officialCounters()
    counters[key] = value
    const result = evaluateOfficialPass(officialInput({ counters }))
    expect(result.passed).toBe(false)
    expect(result.failures.some((item) => item.includes(key))).toBe(true)
  })

  it('fails when any official phase is missing', () => {
    const result = evaluateOfficialPass(officialInput({ officialRun: false }))
    expect(result.passed).toBe(false)
    expect(result.failures.join(' ')).toMatch(/official phases/)
  })

  it('fails on sourceDrift', () => {
    const result = evaluateOfficialPass(officialInput({ sourceDriftCount: 1 }))
    expect(result.passed).toBe(false)
    expect(result.failures.join(' ')).toMatch(/sourceDrift/)
  })

  it('fails on duplicate or missing scenario ids', () => {
    expect(evaluateOfficialPass(officialInput({ uniqueIdCount: 959 })).passed).toBe(false)
    expect(evaluateOfficialPass(officialInput({ duplicateIdCount: 1 })).passed).toBe(false)
  })

  it('fails on failed scenarios', () => {
    expect(evaluateOfficialPass(officialInput({ failed: 1 })).passed).toBe(false)
  })

  it('fails on unknown, mutating, console, page, external, and non-offline request failures', () => {
    expect(
      evaluateOfficialPass(officialInput({ server: { unknownApiRequests: 1, mutatingApiRequests: 0 } }))
        .passed,
    ).toBe(false)
    expect(
      evaluateOfficialPass(officialInput({ server: { unknownApiRequests: 0, mutatingApiRequests: 1 } }))
        .passed,
    ).toBe(false)
    expect(
      evaluateOfficialPass(
        officialInput({
          diagnosticTotals: {
            unexpectedConsole: 1,
            pageErrors: 0,
            externalRequests: 0,
            requestFailuresOutsideOffline: 0,
          },
        }),
      ).passed,
    ).toBe(false)
    expect(
      evaluateOfficialPass(
        officialInput({
          diagnosticTotals: {
            unexpectedConsole: 0,
            pageErrors: 1,
            externalRequests: 0,
            requestFailuresOutsideOffline: 0,
          },
        }),
      ).passed,
    ).toBe(false)
    expect(
      evaluateOfficialPass(
        officialInput({
          diagnosticTotals: {
            unexpectedConsole: 0,
            pageErrors: 0,
            externalRequests: 1,
            requestFailuresOutsideOffline: 0,
          },
        }),
      ).passed,
    ).toBe(false)
    expect(
      evaluateOfficialPass(
        officialInput({
          diagnosticTotals: {
            unexpectedConsole: 0,
            pageErrors: 0,
            externalRequests: 0,
            requestFailuresOutsideOffline: 1,
          },
        }),
      ).passed,
    ).toBe(false)
  })

  it('locks official phase names and unique id total', () => {
    expect(OFFICIAL_PHASES).toEqual(['access', 'viewport', 'state', 'interaction', 'environment'])
    expect(OFFICIAL_COUNTS.uniqueScenarioIds).toBe(960)
  })
})

describe('Stage 8 clean-source binding', () => {
  it('rejects dirty porcelain, staged, or worktree diffs', () => {
    expect(assertCleanOfficialBinding({ porcelain: ' M file', stagedDiff: '', worktreeDiff: '' })).not.toEqual(
      [],
    )
    expect(assertCleanOfficialBinding({ porcelain: '', stagedDiff: 'diff', worktreeDiff: '' })).not.toEqual([])
    expect(assertCleanOfficialBinding({ porcelain: '', stagedDiff: '', worktreeDiff: 'diff' })).not.toEqual([])
    expect(assertCleanOfficialBinding({ porcelain: '', stagedDiff: '', worktreeDiff: '' })).toEqual([])
  })

  it('detects each binding field drift', () => {
    const before = {
      branch: 'main',
      commit: 'aaa',
      tree: 'bbb',
      porcelain: '',
      stagedDiff: '',
      worktreeDiff: '',
      hashes: Object.fromEntries(BINDING_PATHS.map((rel) => [rel, 'h1'])),
    }
    for (const key of ['branch', 'commit', 'tree', 'porcelain', 'stagedDiff', 'worktreeDiff']) {
      const after = { ...before, hashes: { ...before.hashes }, [key]: 'changed' }
      expect(assertBindingUnchanged(before, after).some((item) => item.includes(key))).toBe(true)
    }
    const hashAfter = { ...before, hashes: { ...before.hashes, [BINDING_PATHS[0]]: 'h2' } }
    expect(assertBindingUnchanged(before, hashAfter).join(' ')).toMatch(BINDING_PATHS[0])
  })

  it('binds UserProfile and authority sources', () => {
    expect(BINDING_PATHS).toContain('frontend/src/components/UserProfile.vue')
    expect(BINDING_PATHS).toContain('frontend/src/components/UserProfile.test.ts')
    expect(BINDING_PATHS).toContain('frontend/src/router/index.ts')
    expect(BINDING_PATHS).toContain('frontend/src/utils/auth.ts')
    expect(BINDING_PATHS).toContain('models/user.py')
    expect(BINDING_PATHS).toContain('docs/uiux-stage8-acceptance-rollout/ACCEPTANCE_MATRIX.json')
  })

  it('forbids official dirty-run allowance in the runner', () => {
    expect(browserSource).not.toMatch(/ALLOWED_DIRTY/)
    expect(browserSource).toMatch(/assertCleanOfficialBinding/)
    expect(browserSource).toMatch(/evaluateOfficialPass/)
    expect(browserSource).toMatch(/sourceDrift/)
  })
})

describe('Stage 8 applicability counts stay source-bound', () => {
  it('keeps 192/48 state, 116/4 interaction, and 87/3 environment cells', () => {
    let stateYes = 0
    let stateNo = 0
    let interactionYes = 0
    let interactionNo = 0
    let environmentYes = 0
    let environmentNo = 0
    for (const route of ROUTES) {
      for (const item of stateApplicability(route)) {
        if (item.applicable) stateYes += 1
        else stateNo += 1
      }
      for (const item of interactionApplicability(route)) {
        if (item.applicable) interactionYes += 1
        else interactionNo += 1
      }
      for (const environment of ENVIRONMENTS) {
        if (environmentApplicability(route, environment).applicable) environmentYes += 1
        else environmentNo += 1
      }
    }
    expect(ROUTES).toHaveLength(30)
    expect(stateYes).toBe(192)
    expect(stateNo).toBe(48)
    expect(interactionYes).toBe(116)
    expect(interactionNo).toBe(4)
    expect(environmentYes).toBe(87)
    expect(environmentNo).toBe(3)
  })
})

describe('Stage 8 state-specific assertions', () => {
  const settled = {
    loadingVisible: false,
    settledVisible: true,
    emptyVisible: false,
    emptyNamed: false,
    listItemCount: 0,
    lastItemAccessible: true,
    denseVisible: false,
    errorVisible: false,
    offlineVisible: false,
    landedRecovery: false,
    staleOldVisible: false,
    staleFreshVisible: true,
    identityBootstrapBroken: false,
    identityRequestCount: 1,
    staleOldCompletedAt: 20,
    staleNewCompletedAt: 10,
    documentOverflow: false,
    internalStripOverflow: false,
  }

  it('requires a real loading UI before settle and rejects remount storms', () => {
    expect(
      assertStateSemantics(settled, { loadingVisible: true, identityRequestCount: 1 }, 'loading', 'none', 'render-route', 'settings'),
    ).toEqual([])
    expect(
      assertStateSemantics(settled, { loadingVisible: false }, 'loading', 'none', 'render-route', 'settings'),
    ).toContain('loading UI not observed before settle')
    expect(
      assertStateSemantics(
        { ...settled, loadingVisible: true, settledVisible: false },
        { loadingVisible: true },
        'loading',
        'none',
        'render-route',
        'settings',
      ),
    ).toContain('loading did not settle')
    expect(
      assertStateSemantics(
        settled,
        { loadingVisible: true, identityRequestCount: 4 },
        'loading',
        'none',
        'render-route',
        'settings',
      ),
    ).toContain('loading remounted identity 4 times')
  })

  it('requires named empty UI without ordinary or dense rows', () => {
    expect(
      assertStateSemantics(
        { ...settled, emptyVisible: true, emptyNamed: true },
        null,
        'empty',
        'none',
        'render-route',
        'account-security',
      ),
    ).toEqual([])
    expect(assertStateSemantics(settled, null, 'empty', 'none', 'render-route', 'account-security')).toContain(
      'empty state UI not observed',
    )
    expect(
      assertStateSemantics(
        { ...settled, emptyVisible: true, emptyNamed: false },
        null,
        'empty',
        'none',
        'render-route',
        'account-security',
      ),
    ).toContain('empty state missing accessible name')
    expect(
      assertStateSemantics(
        { ...settled, emptyVisible: true, emptyNamed: true, listItemCount: 2 },
        null,
        'empty',
        'none',
        'render-route',
        'account-security',
      ),
    ).toContain('empty state still shows 2 items')
  })

  it('requires dense inventory, last-item access, and strip overflow isolation', () => {
    expect(
      assertStateSemantics(
        {
          ...settled,
          listItemCount: 24,
          lastItemAccessible: true,
          documentOverflow: true,
          internalStripOverflow: true,
        },
        null,
        'dense',
        'none',
        'render-route',
        'operations-customers',
      ),
    ).toEqual([])
    expect(
      assertStateSemantics(settled, null, 'dense', 'none', 'render-route', 'operations-customers'),
    ).toContain('dense list rendered 0 items')
    expect(
      assertStateSemantics(
        { ...settled, listItemCount: 24, lastItemAccessible: false },
        null,
        'dense',
        'none',
        'render-route',
        'operations-customers',
      ),
    ).toContain('dense last item is not accessible')
    expect(
      assertStateSemantics(
        {
          ...settled,
          listItemCount: 24,
          lastItemAccessible: true,
          documentOverflow: true,
          internalStripOverflow: false,
        },
        null,
        'dense',
        'none',
        'render-route',
        'operations-customers',
      ),
    ).toContain('dense document overflow is not confined to an internal strip')
  })

  it('requires error UI, healthy identity, and no recovery escape', () => {
    expect(
      assertStateSemantics(
        { ...settled, errorVisible: true },
        null,
        'error',
        'none',
        'render-route',
        'account-security',
      ),
    ).toEqual([])
    expect(assertStateSemantics(settled, null, 'error', 'none', 'render-route', 'settings')).toContain(
      'error UI not observed',
    )
    expect(
      assertStateSemantics(
        { ...settled, errorVisible: true, landedRecovery: true },
        null,
        'error',
        'none',
        'render-route',
        'settings',
      ),
    ).toContain('error state left the route for system recovery')
    expect(
      assertStateSemantics(
        { ...settled, errorVisible: true, identityBootstrapBroken: true },
        null,
        'error',
        'none',
        'render-route',
        'settings',
      ),
    ).toContain('error state broke identity bootstrap')
  })

  it('requires slow to show loading then a non-empty/error settle', () => {
    expect(
      assertStateSemantics(settled, { loadingVisible: true }, 'slow', 'none', 'render-route', 'settings'),
    ).toEqual([])
    expect(assertStateSemantics(settled, { loadingVisible: false }, 'slow', 'none', 'render-route', 'settings')).toContain(
      'slow state never showed loading',
    )
    expect(
      assertStateSemantics(
        { ...settled, emptyVisible: true, listItemCount: 0 },
        { loadingVisible: true },
        'slow',
        'none',
        'render-route',
        'account-security',
      ),
    ).toContain('slow settled into premature empty')
  })

  it('requires offline fallback without recovery', () => {
    expect(
      assertStateSemantics(
        { ...settled, offlineVisible: true },
        null,
        'offline',
        'none',
        'render-route',
        'settings',
      ),
    ).toEqual([])
    expect(assertStateSemantics(settled, null, 'offline', 'none', 'render-route', 'settings')).toContain(
      'offline/fallback UI not observed',
    )
    expect(
      assertStateSemantics(
        { ...settled, offlineVisible: true, landedRecovery: true },
        null,
        'offline',
        'none',
        'render-route',
        'settings',
      ),
    ).toContain('offline state left the route for system recovery')
  })

  it('requires a real stale race that cannot overwrite newer state', () => {
    expect(assertStateSemantics(settled, null, 'stale', 'none', 'render-route', 'account-security')).toEqual([])
    expect(
      assertStateSemantics(
        { ...settled, staleOldVisible: true },
        null,
        'stale',
        'none',
        'render-route',
        'account-security',
      ),
    ).toContain('stale response overwrote newer state')
    expect(
      assertStateSemantics(
        { ...settled, staleFreshVisible: false },
        null,
        'stale',
        'none',
        'render-route',
        'account-security',
      ),
    ).toContain('fresh stale-race marker not observed')
    expect(
      assertStateSemantics(
        { ...settled, staleOldCompletedAt: 5, staleNewCompletedAt: 10 },
        null,
        'stale',
        'none',
        'render-route',
        'account-security',
      ),
    ).toContain('stale race did not deliver the old response after the fresh one')
    expect(hasListStateSurface('account-security')).toBe(true)
    expect(hasListStateSurface('settings')).toBe(false)
  })
})

describe('Stage 8 interaction-specific assertions', () => {
  it('requires a real keyboard cycle, focus-visible, and no Escape mutation', () => {
    expect(
      assertInteractionSemantics(
        {
          focusInViewport: true,
          tabCycleObserved: true,
          focusVisible: true,
          escapeOpenedMutation: false,
          modalOpen: false,
        },
        'keyboard',
        'none',
      ),
    ).toEqual([])
    expect(
      assertInteractionSemantics({ focusInViewport: false, tabCycleObserved: true, focusVisible: true }, 'keyboard', 'none'),
    ).toContain('keyboard focus outside viewport')
    expect(
      assertInteractionSemantics({ focusInViewport: true, tabCycleObserved: false, focusVisible: true }, 'keyboard', 'none'),
    ).toContain('Tab/Shift+Tab cycle not observed')
    expect(
      assertInteractionSemantics({ focusInViewport: true, tabCycleObserved: true, focusVisible: false }, 'keyboard', 'none'),
    ).toContain('keyboard focus-visible was not observed')
    expect(
      assertInteractionSemantics(
        { focusInViewport: true, tabCycleObserved: true, focusVisible: true, modalOpen: true, focusInsideModal: false },
        'keyboard',
        'none',
      ),
    ).toContain('modal focus was not contained')
  })

  it('requires a real non-mutating pointer activation', () => {
    expect(assertInteractionSemantics({ touchActivated: true, mutatingProductRequest: false }, 'touch', 'none')).toEqual(
      [],
    )
    expect(assertInteractionSemantics({ touchActivated: false }, 'touch', 'none')).toContain(
      'touch/pointer did not activate a safe control',
    )
    expect(
      assertInteractionSemantics({ touchActivated: true, mutatingProductRequest: true }, 'touch', 'none'),
    ).toContain('touch activated a mutating product endpoint')
  })

  it('requires real 200% zoom geometry', () => {
    expect(
      assertInteractionSemantics(
        {
          visualScale: 2,
          documentOverflow: false,
          appOverflow: false,
          clippedControlCount: 0,
          clippedTextCount: 0,
          ctaAboveNav: true,
          bottomNavClipped: false,
          modalOpen: false,
        },
        'zoom-200',
        'none',
      ),
    ).toEqual([])
    expect(assertInteractionSemantics({ visualScale: 1, ctaAboveNav: true }, 'zoom-200', 'none')).toContain(
      'zoom scale 1 != 2',
    )
    expect(
      assertInteractionSemantics({ visualScale: 2, documentOverflow: true, ctaAboveNav: true }, 'zoom-200', 'none'),
    ).toContain('document overflow at 200% zoom')
  })

  it('requires Stage 7 reduced-motion tokens', () => {
    expect(
      assertInteractionSemantics({ reducedMotion: true, v2MotionMs: 1, protectedFadeMs: 200 }, 'reduced-motion', 'none'),
    ).toEqual([])
    expect(assertInteractionSemantics({ reducedMotion: false, v2MotionMs: 1 }, 'reduced-motion', 'none')).toContain(
      'prefers-reduced-motion is not reduce',
    )
    expect(
      assertInteractionSemantics({ reducedMotion: true, v2MotionMs: 180 }, 'reduced-motion', 'none'),
    ).toContain('NONE reduced-motion token 180ms')
    expect(
      assertInteractionSemantics({ reducedMotion: true, protectedFadeMs: 1 }, 'reduced-motion', 'full'),
    ).toContain('protected fade changed to 1ms')
    expect(
      assertInteractionSemantics({ reducedMotion: true, protectedFadeMs: 200 }, 'reduced-motion', 'mixed'),
    ).toEqual([])
  })
})

describe('Stage 8 environment separation', () => {
  it('keeps mobile-browser free of Telegram and standalone', () => {
    expect(
      assertEnvironmentSemantics(
        {
          environmentName: 'mobile-browser',
          hasTelegramBridge: false,
          standalone: false,
          serviceWorkerControlled: false,
        },
        'mobile-browser',
      ),
    ).toEqual([])
    expect(
      assertEnvironmentSemantics({ environmentName: 'mobile-browser', hasTelegramBridge: true }, 'mobile-browser'),
    ).toContain('mobile-browser injected a Telegram bridge')
    expect(
      assertEnvironmentSemantics({ environmentName: 'mobile-browser', standalone: true }, 'mobile-browser'),
    ).toContain('mobile-browser reported standalone display-mode')
  })

  it('names PWA as simulation and forbids claiming a blocked worker is installed', () => {
    expect(ENVIRONMENTS).toEqual(['mobile-browser', 'pwa-simulation', 'telegram-webview-non-messenger'])
    expect(
      assertEnvironmentSemantics(
        {
          environmentName: 'pwa-simulation',
          standalone: true,
          serviceWorkerControlled: false,
          hasTelegramBridge: false,
        },
        'pwa-simulation',
      ),
    ).toEqual([])
    expect(
      assertEnvironmentSemantics(
        { environmentName: 'pwa-simulation', standalone: true, serviceWorkerControlled: true },
        'pwa-simulation',
      ),
    ).toContain('pwa-simulation cannot claim an installed service worker while workers are blocked')
    expect(runtimeSource).not.toMatch(/ENVIRONMENTS = Object\.freeze\(\[\s*'mobile-browser',\s*'pwa',/)
  })

  it('injects Telegram only in the non-messenger WebView environment', () => {
    expect(
      assertEnvironmentSemantics(
        {
          environmentName: 'telegram-webview-non-messenger',
          hasTelegramBridge: true,
          telegramReadyCalled: true,
          userAgent: 'Mozilla Telegram WebView',
          standalone: false,
        },
        'telegram-webview-non-messenger',
      ),
    ).toEqual([])
    expect(
      assertEnvironmentSemantics(
        {
          environmentName: 'telegram-webview-non-messenger',
          hasTelegramBridge: false,
          telegramReadyCalled: false,
          userAgent: 'Mozilla',
        },
        'telegram-webview-non-messenger',
      ),
    ).not.toEqual([])
    expect(environmentApplicability('messenger', 'telegram-webview-non-messenger').applicable).toBe(false)
    expect(environmentApplicability('share-receive', 'telegram-webview-non-messenger').applicable).toBe(false)
    expect(environmentApplicability('admin-channels', 'telegram-webview-non-messenger').applicable).toBe(false)
    expect(environmentApplicability('settings', 'telegram-webview-non-messenger').applicable).toBe(true)
  })
})
