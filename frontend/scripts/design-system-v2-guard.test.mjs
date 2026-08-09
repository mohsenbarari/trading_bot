import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'
import {
  checkCatalogBoundary,
  checkRoutePolicy,
  checkV2Styles,
  hasV2CssMarker,
  isProductActivationSourcePath,
  parseRouterRoutes,
} from './lib/design-system-v2-guard.mjs'

const scriptDir = path.dirname(fileURLToPath(import.meta.url))
const frontendRoot = path.resolve(scriptDir, '..')
const manifestFixture = JSON.parse(
  fs.readFileSync(path.join(frontendRoot, 'src/design-system-v2/scope-manifest.json'), 'utf8'),
)
const routerFixture = fs.readFileSync(path.join(frontendRoot, 'src/router/index.ts'), 'utf8')
const tokenCssFixture = fs.readFileSync(
  path.join(frontendRoot, 'src/styles/design-system-v2.tokens.css'),
  'utf8',
)
const componentCssFixture = fs.readFileSync(
  path.join(frontendRoot, 'src/styles/design-system-v2.components.css'),
  'utf8',
)

function cloneManifest() {
  return structuredClone(manifestFixture)
}

function findingCodes(findings) {
  return findings.map(({ code }) => code)
}

describe('UIUX v2 CSS guard', () => {
  it('discovers mixed-case, presence, and operator V2 attribute selectors', () => {
    for (const source of [
      '[DATA-UI-SYSTEM="v2"] .ui-button {}',
      '[data-ui-system] .ui-button {}',
      '[data-ui-system^="v2"] .ui-button {}',
    ]) {
      expect(hasV2CssMarker(source)).toBe(true)
    }
    expect(hasV2CssMarker('/* [data-ui-system] */ .legacy {}')).toBe(false)
  })

  it('accepts scoped, self-contained V2 tokens and components', () => {
    const findings = checkV2Styles(
      [
        {
          path: 'src/styles/design-system-v2.tokens.css',
          source: '[data-ui-system="v2"] { --ui-v2-color-action: #2f6fed; }',
        },
        {
          path: 'src/styles/design-system-v2.components.css',
          source: '[data-ui-system="v2"] .ui-button { color: var(--ui-v2-color-action); }',
        },
      ],
      { enforceFrozenTokenContract: false },
    )

    expect(findings).toEqual([])
  })

  it('allows only the Stage 3 shell namespaces plus the registered public header', () => {
    const findings = checkV2Styles(
      [
        {
          path: 'src/styles/design-system-v2.components.css',
          source: [
            '[data-ui-system="v2"] .ui-v2-auth-flow {}',
            '[data-ui-system="v2"] .ui-v2-auth-password-toggle {}',
            '[data-ui-system="v2"] .ui-v2-connection-banner {}',
            '[data-ui-system="v2"] .ui-v2-pwa-section {}',
            '[data-ui-system="v2"] .ui-v2-pwa-actions {}',
            '[data-ui-system="v2-portal"] .ui-v2-session-dialog {}',
            '[data-ui-system="v2"] .ui-v2-toast-layer {}',
            '[data-ui-system="v2"] .ui-v2-public-header {}',
            '[data-ui-system="v2"] .ui-v2-profile-card {}',
          ].join('\n'),
        },
      ],
      { enforceFrozenTokenContract: false },
    )

    expect(findingCodes(findings).filter((code) => code === 'noncanonical-v2-class')).toHaveLength(
      1,
    )
  })

  it('accepts the duplicated scope attribute used to outrank legacy focus rules', () => {
    const findings = checkV2Styles(
      [
        {
          path: 'src/styles/design-system-v2.tokens.css',
          source: '[data-ui-system="v2"] { --ui-v2-color-focus: #2f6fed; }',
        },
        {
          path: 'src/styles/design-system-v2.components.css',
          source: [
            '[data-ui-system="v2"][data-ui-system="v2"]',
            '  :where(.ui-button, .ui-input):focus-visible {',
            '  outline: var(--ui-v2-color-focus);',
            '}',
          ].join('\n'),
        },
      ],
      { enforceFrozenTokenContract: false },
    )

    expect(findings).toEqual([])
  })

  it('rejects unscoped and global selectors only inside supplied V2 CSS', () => {
    const findings = checkV2Styles([
      {
        path: 'src/styles/design-system-v2.components.css',
        source: [
          '.ui-v2-card { color: black; }',
          ':root { --ui-v2-a: 1; }',
          'html [data-ui-system="v2"] { color: black; }',
          'body [data-ui-system="v2"] { color: black; }',
          '[data-ui-system="v2"] * { box-sizing: border-box; }',
          ':where([data-ui-system="v2"], .leaked-alternative) { color: black; }',
        ].join('\n'),
      },
    ])

    expect(findingCodes(findings)).toContain('unscoped-v2-selector')
    expect(findingCodes(findings).filter((code) => code === 'unscoped-v2-selector')).toHaveLength(5)
    expect(findingCodes(findings).filter((code) => code === 'global-v2-selector')).toHaveLength(4)
  })

  it('rejects sibling escapes and unsafe functional scope alternatives', () => {
    const findings = checkV2Styles([
      {
        path: 'src/styles/design-system-v2.components.css',
        source: [
          '[data-ui-system="v2"] + .ui-v2-leak { color: var(--ui-v2-color-action); }',
          '[data-ui-system="v2"] ~ .ui-v2-leak { color: var(--ui-v2-color-action); }',
          '[data-ui-system="v2"]:not(.ui-v2-x) + .ui-v2-leak { color: var(--ui-v2-color-action); }',
          ':where([data-ui-system="v2"], .ui-v2-leak:not(.is-safe)) { color: var(--ui-v2-color-action); }',
          '.ui-v2-parent [data-ui-system="v2"] { color: var(--ui-v2-color-action); }',
        ].join('\n'),
      },
      {
        path: 'src/styles/design-system-v2.tokens.css',
        source:
          ':where([data-ui-system="v2"], [data-ui-system="v2-portal"]) { --ui-v2-color-action: #2f6fed; }',
      },
    ])

    expect(findingCodes(findings).filter((code) => code === 'unscoped-v2-selector')).toHaveLength(5)
  })

  it('rejects raw colors, hardcoded design lengths, and noncanonical local components', () => {
    const findings = checkV2Styles([
      {
        path: 'src/styles/design-system-v2.tokens.css',
        source: '[data-ui-system="v2"] { --ui-v2-space-3: 12px; }',
      },
      {
        path: 'src/styles/design-system-v2.components.css',
        source: [
          '[data-ui-system="v2"] .local-button { color: rgb(255 0 255); min-height: 44px; }',
          '[data-ui-system="v2"] .ui-local-button { color: red; background: oklch(62% 0.2 20); }',
          '[data-ui-system="v2"] .ui-v2-button { width: 123px; font-size: 1rem; inset: 2vh; padding: .5em; border-radius: 10%; transform: translateX(5px); }',
          '[data-ui-system="v2"] .ui-v2-card { margin: -4px; border: 1px solid var(--ui-v2-color-action); }',
        ].join('\n'),
      },
    ])

    expect(findingCodes(findings)).toEqual(
      expect.arrayContaining([
        'raw-v2-color',
        'hardcoded-v2-design-length',
        'noncanonical-v2-class',
      ]),
    )
    expect(
      findingCodes(findings).filter((code) => code === 'hardcoded-v2-design-length'),
    ).toHaveLength(9)
    expect(findingCodes(findings).filter((code) => code === 'raw-v2-color')).toHaveLength(3)
    expect(findingCodes(findings).filter((code) => code === 'noncanonical-v2-class')).toHaveLength(
      4,
    )
  })

  it('rejects imported/global CSS and unapproved media queries', () => {
    const findings = checkV2Styles([
      {
        path: 'src/styles/design-system-v2.components.css',
        source: [
          '@import "./legacy-global.css";',
          '@keyframes ui-spin { from { opacity: 0 } to { opacity: 1 } }',
          '@media (min-width: 2000px) { [data-ui-system="v2"] { color: var(--ui-v2-color-text-primary); } }',
          '[data-ui-system="v2"] .ui-v2-catalog { color: var(--ui-v2-color-text-primary); }',
        ].join('\n'),
      },
    ])

    expect(findingCodes(findings)).toEqual(
      expect.arrayContaining([
        'v2-css-import',
        'unsupported-v2-global-at-rule',
        'unapproved-v2-media-query',
      ]),
    )
  })

  it('rejects canonical token overrides outside the reduced-motion allowlist', () => {
    const findings = checkV2Styles([
      {
        path: 'src/styles/design-system-v2.tokens.css',
        source: '[data-ui-system="v2"] { --ui-v2-color-text-primary: #12314a; }',
      },
      {
        path: 'src/styles/design-system-v2.components.css',
        source:
          '@media (min-width: 2000px) { [data-ui-system="v2"] { --ui-v2-color-text-primary: var(--ui-v2-neutral-white); } }',
      },
    ])

    expect(findingCodes(findings)).toEqual(
      expect.arrayContaining(['duplicate-v2-token', 'unapproved-v2-media-query']),
    )
  })

  it('rejects token laundering, hardcoded motion, and unofficial typography', () => {
    const findings = checkV2Styles([
      {
        path: 'src/styles/design-system-v2.tokens.css',
        source:
          '[data-ui-system="v2"] { --ui-v2-color-text-primary: #12314a; --ui-v2-font-family: Vazirmatn; }',
      },
      {
        path: 'src/styles/design-system-v2.components.css',
        source: [
          '[data-ui-system="v2"] { --ui-v2-local-gap: 13px; }',
          '[data-ui-system="v2"] .ui-v2-catalog {',
          '  padding: var(--ui-v2-local-gap);',
          '  transition-duration: 999ms;',
          '  animation-duration: 2s;',
          '  font-family: Arial, sans-serif;',
          '  font-weight: 650;',
          '}',
        ].join('\n'),
      },
    ])

    expect(findingCodes(findings)).toEqual(
      expect.arrayContaining([
        'v2-token-definition-outside-source',
        'hardcoded-v2-motion-duration',
        'hardcoded-v2-typography',
      ]),
    )
    expect(
      findingCodes(findings).filter((code) => code === 'hardcoded-v2-motion-duration'),
    ).toHaveLength(2)
    expect(
      findingCodes(findings).filter((code) => code === 'hardcoded-v2-typography'),
    ).toHaveLength(2)
  })

  it('rejects font shorthand and ordinary hardcodes hidden in the canonical token file', () => {
    const findings = checkV2Styles([
      {
        path: 'src/styles/design-system-v2.tokens.css',
        source: [
          '[data-ui-system="v2"] { --ui-v2-spacing-8: 8px; }',
          '[data-ui-system="v2"] .ui-button { color: red; width: 999px; font: 650 14px Arial, sans-serif; }',
        ].join('\n'),
      },
      {
        path: 'src/styles/design-system-v2.components.css',
        source: '[data-ui-system="v2"] .ui-button { font: 650 14px Arial, sans-serif; }',
      },
    ])

    expect(findingCodes(findings)).toEqual(
      expect.arrayContaining([
        'raw-v2-color',
        'hardcoded-v2-design-length',
        'hardcoded-v2-typography',
      ]),
    )
    expect(
      findingCodes(findings).filter((code) => code === 'hardcoded-v2-typography'),
    ).toHaveLength(2)
  })

  it('rejects foreign and case-spoofed custom-property definitions and usages', () => {
    const findings = checkV2Styles([
      {
        path: 'src/styles/design-system-v2.tokens.css',
        source: '[data-ui-system="v2"] { --ui-v2-spacing-8: 8px; }',
      },
      {
        path: 'src/styles/design-system-v2.components.css',
        source: [
          '[data-ui-system="v2"] .ui-button {',
          '  --foreign-space: var(--ui-v2-spacing-8);',
          '  --UI-V2-space: var(--ui-v2-spacing-8);',
          '  padding: var(--foreign-space);',
          '  margin: var(--UI-V2-space);',
          '}',
        ].join('\n'),
      },
    ])

    expect(
      findingCodes(findings).filter((code) => code === 'noncanonical-v2-custom-property'),
    ).toHaveLength(2)
    expect(
      findingCodes(findings).filter((code) => code === 'noncanonical-v2-custom-property-usage'),
    ).toHaveLength(2)
  })

  it('allows raw colors only in the exact canonical token source path', () => {
    const findings = checkV2Styles([
      {
        path: 'src/features/design-system-v2.tokens.css',
        source: '[data-ui-system="v2"] { --ui-v2-rogue: red; }',
      },
    ])

    expect(findingCodes(findings)).toContain('raw-v2-color')
  })

  it('rejects an empty style set and cyclic token aliases', () => {
    expect(findingCodes(checkV2Styles([]))).toContain('empty-v2-style-set')

    const findings = checkV2Styles([
      {
        path: 'src/styles/design-system-v2.tokens.css',
        source: [
          '[data-ui-system="v2"] {',
          '  --ui-v2-a: var(--ui-v2-b);',
          '  --ui-v2-b: var(--ui-v2-c);',
          '  --ui-v2-c: var(--ui-v2-a);',
          '}',
        ].join('\n'),
      },
    ])
    expect(findingCodes(findings)).toContain('v2-token-alias-cycle')
  })

  it('rejects legacy definitions/remaps plus duplicate and undefined V2 tokens', () => {
    const findings = checkV2Styles([
      {
        path: 'src/styles/design-system-v2.tokens.css',
        source: [
          '[data-ui-system="v2"] {',
          '  --ui-v2-a: 1;',
          '  --ui-v2-a: 2;',
          '  --ds-action: blue;',
          '  --ui-v2-bridge: var(--ds-action);',
          '}',
        ].join('\n'),
      },
      {
        path: 'src/styles/design-system-v2.components.css',
        source: '[data-ui-system="v2"] .button { color: var(--ui-v2-missing); }',
      },
    ])

    expect(findingCodes(findings)).toEqual(
      expect.arrayContaining([
        'duplicate-v2-token',
        'legacy-token-definition',
        'legacy-token-remap',
        'undefined-v2-token',
      ]),
    )
  })

  it('locks every canonical and implementation token definition to the frozen contract', () => {
    const actual = checkV2Styles([
      { path: 'src/styles/design-system-v2.tokens.css', source: tokenCssFixture },
      { path: 'src/styles/design-system-v2.components.css', source: componentCssFixture },
    ])
    expect(actual).toEqual([])

    const laundered = tokenCssFixture.replace(
      '/* canonical-figma-variables:end */',
      [
        '--ui-v2-rogue: #ff00ff;',
        '--ui-v2-rogue-space: 13px;',
        '/* canonical-figma-variables:end */',
      ].join('\n'),
    )
    expect(
      findingCodes(
        checkV2Styles([
          { path: 'src/styles/design-system-v2.tokens.css', source: laundered },
          { path: 'src/styles/design-system-v2.components.css', source: componentCssFixture },
        ]),
      ),
    ).toContain('frozen-v2-token-contract-drift')
  })

  it('rejects system colors, extended design lengths, and typography token decoration', () => {
    const findings = checkV2Styles(
      [
        {
          path: 'src/styles/design-system-v2.tokens.css',
          source:
            '[data-ui-system="v2"] { --ui-v2-font-family: Vazirmatn; --ui-v2-font-weight-medium: 500; }',
        },
        {
          path: 'src/styles/design-system-v2.components.css',
          source: [
            '[data-ui-system="v2"] .ui-v2-catalog {',
            'color: CanvasText;',
            'background-position: 13px 0;',
            'stroke-width: 2px;',
            'scroll-margin: 3px;',
            'grid: 4px / 5px;',
            'font-family: var(--ui-v2-font-family), fantasy;',
            'font-weight: calc(var(--ui-v2-font-weight-medium) + 99);',
            '}',
          ].join('\n'),
        },
      ],
      { enforceFrozenTokenContract: false },
    )
    expect(findingCodes(findings)).toEqual(
      expect.arrayContaining([
        'raw-v2-color',
        'hardcoded-v2-design-length',
        'hardcoded-v2-typography',
      ]),
    )
  })

  it('does not treat system-color words inside asset URLs or strings as colors', () => {
    const findings = checkV2Styles(
      [
        {
          path: 'src/styles/design-system-v2.tokens.css',
          source: '[data-ui-system="v2"] { --ui-v2-image: url(/icons/Canvas.svg); }',
        },
        {
          path: 'src/styles/design-system-v2.components.css',
          source: [
            '[data-ui-system="v2"] .ui-v2-catalog {',
            'background-image: url(/assets/20px-background.svg);',
            'content: "10px CanvasText background";',
            '}',
          ].join('\n'),
        },
      ],
      { enforceFrozenTokenContract: false },
    )
    expect(findingCodes(findings)).not.toContain('raw-v2-color')
    expect(findingCodes(findings)).not.toContain('hardcoded-v2-design-length')
  })

  it('rejects hardcoded lengths from every ordinary declaration shorthand', () => {
    const findings = checkV2Styles(
      [
        {
          path: 'src/styles/design-system-v2.tokens.css',
          source: '[data-ui-system="v2"] { --ui-v2-color: #ffffff; }',
        },
        {
          path: 'src/styles/design-system-v2.components.css',
          source: [
            '[data-ui-system="v2"] .ui-v2-catalog {',
            'background: url(/asset.svg) 13px 7px / 20px 30px no-repeat;',
            'flex: 0 0 220px;',
            'columns: 220px 2;',
            'column-rule: 2px solid var(--ui-v2-color);',
            'mask: url(/mask.svg) 10px / 20px;',
            'offset: path("M0 0") 12px;',
            'word-spacing: 2px;',
            'tab-size: 4px;',
            'shape-outside: circle(20px);',
            '}',
          ].join('\n'),
        },
      ],
      { enforceFrozenTokenContract: false },
    )
    expect(
      findingCodes(findings).filter((code) => code === 'hardcoded-v2-design-length'),
    ).toHaveLength(9)
  })
})

describe('UIUX v2 catalog boundary', () => {
  const catalogPath = path.join(frontendRoot, 'src/components/ui/AppDesignSystemCatalog.vue')
  const catalogSource = fs.readFileSync(catalogPath, 'utf8')

  it('accepts only the frozen public catalog import set', () => {
    expect(
      checkCatalogBoundary({
        path: 'src/components/ui/AppDesignSystemCatalog.vue',
        source: catalogSource,
      }),
    ).toEqual([])
  })

  it('rejects protected imports, route literals, and wording even when unused', () => {
    const protectedImport = catalogSource.replace(
      "import { ref } from 'vue'",
      "import { ref } from 'vue'\nimport OffersList from '../OffersList.vue'\nconst hidden = OffersList\nconst route = '/market'",
    )
    const codes = findingCodes(
      checkCatalogBoundary({
        path: 'src/components/ui/AppDesignSystemCatalog.vue',
        source: protectedImport,
      }),
    )

    expect(codes).toEqual(
      expect.arrayContaining(['catalog-import-boundary', 'catalog-protected-reference']),
    )
  })

  it('rejects every nonliteral dynamic import even when a protected name is fragmented', () => {
    const nonliteralImport = catalogSource.replace(
      'const portalProofOpen = ref(false)',
      [
        'const portalProofOpen = ref(false)',
        "const hiddenLoader = () => import(/* @vite-ignore */ '../' + 'Offers' + 'List.vue')",
      ].join('\n'),
    )

    expect(
      findingCodes(
        checkCatalogBoundary({
          path: 'src/components/ui/AppDesignSystemCatalog.vue',
          source: nonliteralImport,
        }),
      ),
    ).toContain('catalog-nonliteral-dynamic-import')
  })

  it('audits every script block and folds fragmented protected references', () => {
    const secondScript = `${catalogSource}\n<script lang="ts">\nconst hidden = () => import('../' + 'Market' + 'View.vue')\nconst route = \`/\${"mar"}ket\`\n</script>`
    expect(
      findingCodes(
        checkCatalogBoundary({
          path: 'src/components/ui/AppDesignSystemCatalog.vue',
          source: secondScript,
        }),
      ),
    ).toEqual(
      expect.arrayContaining(['catalog-nonliteral-dynamic-import', 'catalog-protected-reference']),
    )
  })
})

describe('UIUX v2 route policy guard', () => {
  it('accepts the Stage 3 fixture contract and the current production router', () => {
    expect(
      checkRoutePolicy({
        manifest: cloneManifest(),
        routerSource: routerFixture,
      }),
    ).toEqual([])
  })

  it('rejects V2 activation on a fully protected route', () => {
    const manifest = cloneManifest()
    manifest.routes.find(({ path }) => path === '/market').v2Scope = 'section'

    expect(findingCodes(checkRoutePolicy({ manifest, routerSource: routerFixture }))).toEqual(
      expect.arrayContaining(['protected-route-activation', 'stage3-v2-scope-contract-drift']),
    )
  })

  it('rejects whole-route scope on mixed routes', () => {
    const manifest = cloneManifest()
    manifest.routes.find(({ path }) => path === '/admin/messages').v2Scope = 'route'

    expect(findingCodes(checkRoutePolicy({ manifest, routerSource: routerFixture }))).toEqual(
      expect.arrayContaining(['mixed-route-whole-scope', 'stage3-v2-scope-contract-drift']),
    )
  })

  it('rejects shell-family drift and a catch-all that is not final', () => {
    const manifest = cloneManifest()
    manifest.routes.find(({ path }) => path === '/login').shellClass = 'standard-authenticated'
    const displacedCatchAll = manifest.routes.pop()
    manifest.routes.splice(1, 0, displacedCatchAll)

    expect(findingCodes(checkRoutePolicy({ manifest, routerSource: routerFixture }))).toEqual(
      expect.arrayContaining(['stage3-shell-contract-drift', 'recovery-catch-all-order']),
    )
  })

  it('rejects a production catalog route and route registry drift', () => {
    const routerSource = routerFixture.replace(
      /\n  \],\n\}\)/,
      "\n    { path: '/design-system-v2', component: Catalog, name: 'design-system-v2' },\n  ],\n})",
    )

    expect(
      findingCodes(
        checkRoutePolicy({
          manifest: cloneManifest(),
          routerSource,
        }),
      ),
    ).toEqual(expect.arrayContaining(['production-catalog-route', 'route-registry-drift']))
  })

  it('uses the createRouter AST and rejects comment/dynamic-expression spoofing', () => {
    const spoofed = routerFixture.replace(
      "path: '/market',",
      "/* path: '/market', name: 'market' */ path: ['/market-v2'][0],",
    )
    const routes = parseRouterRoutes(spoofed)

    expect(routes.some(({ path }) => path === '/market')).toBe(false)
    expect(routes.some(({ path }) => path === '/market-v2')).toBe(false)
    expect(
      findingCodes(checkRoutePolicy({ manifest: cloneManifest(), routerSource: spoofed })),
    ).toContain('route-registry-drift')
  })

  it('recursively rejects a nested production catalog route', () => {
    const nestedCatalog = routerFixture.replace(
      "path: '/',",
      "path: '/', children: [{ path: 'design-system-v2', name: 'catalog', component: Catalog }],",
    )
    const routes = parseRouterRoutes(nestedCatalog)

    expect(routes).toContainEqual({ path: '/design-system-v2', name: 'catalog' })
    expect(
      findingCodes(checkRoutePolicy({ manifest: cloneManifest(), routerSource: nestedCatalog })),
    ).toEqual(expect.arrayContaining(['production-catalog-route', 'route-registry-drift']))
  })

  it('fails closed on identifier and spread elements in the route array', () => {
    for (const injectedElement of ['hiddenCatalogRoute,', '...hiddenCatalogRoutes,']) {
      const dynamicRouter = routerFixture.replace('routes: [', `routes: [${injectedElement}`)

      expect(parseRouterRoutes(dynamicRouter)).toContainEqual({
        path: '__dynamic_route__',
        name: '__dynamic_route__',
      })
      expect(
        findingCodes(checkRoutePolicy({ manifest: cloneManifest(), routerSource: dynamicRouter })),
      ).toContain('route-registry-drift')
    }
  })

  it('fails closed on spread assignments inside route objects', () => {
    const spreadRouter = routerFixture
      .replace(
        'const router = createRouter',
        "const routeOverride = { path: '/design-system-v2', name: 'design-system-v2' }\n\nconst router = createRouter",
      )
      .replace("name: 'home',", "name: 'home',\n      ...routeOverride,")

    expect(parseRouterRoutes(spreadRouter)).toContainEqual({
      path: '__dynamic_route__',
      name: '__dynamic_route__',
    })
    expect(
      findingCodes(checkRoutePolicy({ manifest: cloneManifest(), routerSource: spreadRouter })),
    ).toContain('route-registry-drift')
  })

  it('fails closed on createRouter config spreads and computed route keys', () => {
    const spreadConfig = routerFixture.replace('createRouter({', 'createRouter({\n  ...injected,')
    expect(
      findingCodes(checkRoutePolicy({ manifest: cloneManifest(), routerSource: spreadConfig })),
    ).toContain('route-registry-drift')

    const computedKey = routerFixture.replace(
      "path: '/',",
      "path: '/', ['pa' + 'th']: '/design-system-v2',",
    )
    expect(
      findingCodes(checkRoutePolicy({ manifest: cloneManifest(), routerSource: computedKey })),
    ).toContain('route-registry-drift')
  })

  it('fails closed on duplicate overriding route registry keys', () => {
    const duplicatePath = routerFixture.replace(
      "path: '/',",
      "path: '/', path: '/design-system-v2',",
    )
    expect(
      findingCodes(checkRoutePolicy({ manifest: cloneManifest(), routerSource: duplicatePath })),
    ).toContain('route-registry-drift')

    const duplicateRoutes = routerFixture.replace(
      /\n\s*history:/,
      "\n  routes: [{ path: '/design-system-v2', name: 'catalog' }],\n  history:",
    )
    expect(
      findingCodes(checkRoutePolicy({ manifest: cloneManifest(), routerSource: duplicateRoutes })),
    ).toContain('route-registry-drift')
  })

  it('rejects runtime addRoute/removeRoute/clearRoutes mutations in product sources', () => {
    const routerMutation = `${routerFixture}\nrouter.addRoute({ path: '/debug-ui', name: 'debug-ui' })`
    expect(
      findingCodes(checkRoutePolicy({ manifest: cloneManifest(), routerSource: routerMutation })),
    ).toContain('runtime-route-registry-mutation')

    expect(
      findingCodes(
        checkRoutePolicy({
          manifest: cloneManifest(),
          routerSource: routerFixture,
          activationSources: [{ path: 'src/plugins/router.ts', source: 'router.clearRoutes()' }],
        }),
      ),
    ).toContain('runtime-route-registry-mutation')

    expect(
      findingCodes(
        checkRoutePolicy({
          manifest: cloneManifest(),
          routerSource: routerFixture,
          activationSources: [{ path: 'src/main.ts', source: "router.removeRoute('dashboard')" }],
        }),
      ),
    ).toContain('runtime-route-registry-mutation')

    for (const source of [
      "router['add' + 'Route']({ path: '/debug-ui', name: 'debug-ui' })",
      "router['remove' + 'Route']('dashboard')",
      "router['clear' + 'Routes']()",
    ]) {
      expect(
        findingCodes(
          checkRoutePolicy({
            manifest: cloneManifest(),
            routerSource: routerFixture,
            activationSources: [{ path: 'src/plugins/router.ts', source }],
          }),
        ),
      ).toContain('runtime-route-registry-mutation')
    }
  })

  it('rejects source-level activation in product views', () => {
    expect(
      findingCodes(
        checkRoutePolicy({
          manifest: cloneManifest(),
          routerSource: routerFixture,
          activationSources: [
            {
              path: 'src/views/ProfileView.vue',
              source: '<main :data-ui-system="\'v2\'" />',
            },
          ],
        }),
      ),
    ).toContain('stage3-product-source-activation')
  })

  it('rejects helper-based product activation without literal scope markup', () => {
    for (const source of [
      'const attrs = getUiDesignSystemScopeAttributes()',
      'const release = attachUiDesignSystemPortalScope(host)',
      "document.body.dataset.uiSystem = 'v2'",
      "host.dataset['uiSystem'] = 'v2-portal'",
    ]) {
      expect(
        findingCodes(
          checkRoutePolicy({
            manifest: cloneManifest(),
            routerSource: routerFixture,
            activationSources: [{ path: 'src/components/ProductCard.vue', source }],
          }),
        ),
      ).toContain('stage3-product-source-activation')
    }
  })

  it('rejects fragmented DOM, dataset, catalog import, and dynamic router activation', () => {
    const sources = [
      "host.setAttribute('data-' + 'ui-system', 'v' + '2')",
      "host.setAttributeNS(null, 'data-' + 'ui-system', 'v2')",
      "host.dataset['ui' + 'System'] = 'v2-portal'",
      "host.dataset['ui' + 'System'] ||= 'v2'",
      "Object.assign(host.dataset, { ['ui' + 'System']: 'v2' })",
      "Reflect.set(host.dataset, 'ui' + 'System', 'v2')",
      "const hidden = () => import('../components/ui/App' + 'DesignSystemCatalog.vue')",
      "import('../components/ui/ui' + 'DesignSystemScope.ts').then((m) => m['attachUi' + 'DesignSystemPortalScope'](host))",
      "resolveComponent('App' + 'DesignSystemCatalog')",
      "host.setAttribute(\`data-\${'ui'}-system\`, \`v\${2}\`)",
      "host.dataset[\`ui\${'System'}\`] = 'v2'",
      "const templateImport = () => import(\`../components/ui/App\${'DesignSystem'}Catalog.vue\`)",
      "const method = 'addRoute'; router[method]({ path: '/debug', name: 'debug' })",
    ]
    for (const source of sources) {
      expect(
        findingCodes(
          checkRoutePolicy({
            manifest: cloneManifest(),
            routerSource: routerFixture,
            activationSources: [{ path: 'src/plugins/runtime.ts', source }],
          }),
        ),
      ).toEqual(
        expect.arrayContaining([
          source.includes('router')
            ? 'runtime-route-registry-mutation'
            : 'stage3-product-source-activation',
        ]),
      )
    }
  })

  it('rejects direct catalog imports and rendering in product sources', () => {
    const source = [
      "import AppDesignSystemCatalog from '../components/ui/AppDesignSystemCatalog.vue'",
      'const view = h(AppDesignSystemCatalog)',
    ].join('\n')

    expect(
      findingCodes(
        checkRoutePolicy({
          manifest: cloneManifest(),
          routerSource: routerFixture,
          activationSources: [{ path: 'src/views/ProfileView.vue', source }],
        }),
      ),
    ).toContain('stage3-product-source-activation')
  })

  it('allows only the audited App and Home-section scope components', () => {
    const approvedSources = [
      {
        path: 'src/App.vue',
        source: [
          '<script setup>',
          "import AppDesignSystemScope from './components/ui/AppDesignSystemScope.vue'",
          "import { UI_ROUTE_SHELL, UI_V2_SCOPE } from './router/uiRouteContract'",
          'const shellClass = route.meta.uiShellClass',
          'const v2Scope = route.meta.uiV2Scope',
          '</script>',
          '<template><RouterView v-slot="{ Component }"><AppDesignSystemScope v-if="v2Scope === UI_V2_SCOPE.ROUTE"><component :is="Component" /></AppDesignSystemScope><component v-else :is="Component" /></RouterView><AppDesignSystemScope v-if="shellClass === UI_ROUTE_SHELL.STANDARD_AUTHENTICATED"><AuthenticatedShell /></AppDesignSystemScope></template>',
        ].join('\n'),
      },
      {
        path: 'src/views/DashboardView.vue',
        source: [
          '<script setup>',
          "import { AppDesignSystemScope } from '../components/ui'",
          '</script>',
          '<template><AppDesignSystemScope class="ui-v2-pwa-section"><PWAInstallOverlay /></AppDesignSystemScope></template>',
        ].join('\n'),
      },
      {
        path: 'src/components/SessionApprovalModal.vue',
        source: [
          '<script setup>',
          "import { UI_DESIGN_SYSTEM_PORTAL_SCOPE_VALUE } from './ui/uiDesignSystemScope'",
          'const props = withDefaults(defineProps<{ v2Portal?: boolean }>(), { v2Portal: false })',
          'const portalScopeValue = computed(() => props.v2Portal ? UI_DESIGN_SYSTEM_PORTAL_SCOPE_VALUE : undefined)',
          '</script>',
          '<template><Teleport to="body"><div v-if="showModal" :data-ui-system="portalScopeValue" /></Teleport></template>',
        ].join('\n'),
      },
    ]

    expect(
      checkRoutePolicy({
        manifest: cloneManifest(),
        routerSource: routerFixture,
        activationSources: approvedSources,
      }),
    ).toEqual([])

    for (const unsafeSource of [
      {
        path: 'src/App.vue',
        source:
          '<template><AppDesignSystemScope><main data-ui-system="v2" /></AppDesignSystemScope></template>',
      },
      {
        path: 'src/App.vue',
        source:
          '<script>const getUiRouteContractByName = () => ({ v2Scope: UI_V2_SCOPE.ROUTE })</script><template><AppDesignSystemScope><RouterView /></AppDesignSystemScope></template>',
      },
      {
        path: 'src/views/DashboardView.vue',
        source:
          '<template><AppDesignSystemScope class="ui-v2-dashboard"><main /></AppDesignSystemScope></template>',
      },
      {
        path: 'src/views/DashboardView.vue',
        source:
          '<template><AppDesignSystemScope class="ui-v2-pwa-section"><MarketHero /></AppDesignSystemScope></template>',
      },
      {
        path: 'src/views/DashboardView.vue',
        source:
          '<template><AppDesignSystemScope class="ui-v2-pwa-section" /><AppDesignSystemScope class="ui-v2-pwa-section" /></template>',
      },
      {
        path: 'src/components/SessionApprovalModal.vue',
        source:
          '<script>const props = withDefaults(defineProps<{ v2Portal?: boolean }>(), { v2Portal: false }); const portalScopeValue = computed(() => props.v2Portal ? UI_DESIGN_SYSTEM_PORTAL_SCOPE_VALUE : undefined)</script><template><div :data-ui-system="portalScopeValue" /></template>',
      },
    ]) {
      expect(
        findingCodes(
          checkRoutePolicy({
            manifest: cloneManifest(),
            routerSource: routerFixture,
            activationSources: [unsafeSource],
          }),
        ),
      ).toContain('stage3-product-source-activation')
    }
  })

  it('scans ordinary activation surfaces while excluding only V2 definitions and tests', () => {
    expect(isProductActivationSourcePath('src/main.ts')).toBe(true)
    expect(isProductActivationSourcePath('src/components/NavBar.vue')).toBe(true)
    expect(isProductActivationSourcePath('src/composables/useTheme.ts')).toBe(true)
    expect(isProductActivationSourcePath('src/stores/settings.ts')).toBe(true)
    expect(isProductActivationSourcePath('src/utils/enableV2.ts')).toBe(true)
    expect(isProductActivationSourcePath('src/services/portalTheme.ts')).toBe(true)
    expect(isProductActivationSourcePath('src/plugins/ui.ts')).toBe(true)
    expect(isProductActivationSourcePath('src/directives/scope.ts')).toBe(true)
    expect(isProductActivationSourcePath('src/layouts/ProductLayout.vue')).toBe(true)
    expect(isProductActivationSourcePath('index.html')).toBe(true)
    expect(isProductActivationSourcePath('src/components/ui/AppDesignSystemScope.vue')).toBe(false)
    expect(isProductActivationSourcePath('src/components/ui/AppDesignSystemCatalog.vue')).toBe(
      false,
    )
    expect(isProductActivationSourcePath('src/components/ui/uiDesignSystemScope.ts')).toBe(false)
    expect(isProductActivationSourcePath('src/router/uiRouteContract.ts')).toBe(false)
    expect(isProductActivationSourcePath('src/components/NavBar.test.ts')).toBe(false)
  })

  it('rejects whole-application activation from the HTML entrypoint', () => {
    for (const source of [
      '<body data-ui-system="v2"><div id="app"></div></body>',
      '<body DATA-UI-SYSTEM="v2"><div id="app"></div></body>',
      '<main Data-Ui-System="v2"></main>',
    ]) {
      expect(
        findingCodes(
          checkRoutePolicy({
            manifest: cloneManifest(),
            routerSource: routerFixture,
            activationSources: [{ path: 'index.html', source }],
          }),
        ),
      ).toContain('stage3-product-source-activation')
    }
  })
})
