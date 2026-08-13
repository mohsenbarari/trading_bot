import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'
import {
  STAGE8B_APPROVED_PERSIAN_FAMILY,
  STAGE8B_TYPOGRAPHY_PATHS,
  assertStage8bTypographyContract,
  assertStage8bTypographyProductSourceBoundary,
} from './lib/stage8b-typography-guard.mjs'

const scriptDir = path.dirname(fileURLToPath(import.meta.url))
const repoRoot = path.resolve(scriptDir, '..', '..')

function currentSources() {
  return new Map(
    Object.values(STAGE8B_TYPOGRAPHY_PATHS).map((repoPath) => [
      repoPath,
      fs.readFileSync(path.join(repoRoot, repoPath), 'utf8'),
    ]),
  )
}

function currentProductSources() {
  const sourceRoot = path.join(repoRoot, 'frontend', 'src')
  const entries = []
  const walk = (directory) => {
    for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
      const filePath = path.join(directory, entry.name)
      if (entry.isDirectory()) {
        walk(filePath)
        continue
      }
      if (!/\.(?:css|[cm]?[jt]sx?|vue)$/.test(entry.name)) continue
      if (/\.(?:test|spec)\.[^/]+$/.test(entry.name) || entry.name.endsWith('.d.ts')) continue
      entries.push([
        path.relative(repoRoot, filePath).split(path.sep).join('/'),
        fs.readFileSync(filePath, 'utf8'),
      ])
    }
  }
  walk(sourceRoot)
  entries.push([
    'frontend/index.html',
    fs.readFileSync(path.join(repoRoot, 'frontend', 'index.html'), 'utf8'),
  ])
  return new Map(entries)
}

function withMutation(sources, repoPath, mutate) {
  const next = new Map(sources)
  const source = next.get(repoPath)
  const updated = mutate(source)
  if (updated === source) throw new Error(`fixture did not change ${repoPath}`)
  next.set(repoPath, updated)
  return next
}

describe('Stage 8B bounded typography guard', () => {
  it('accepts the single NONE-only route-local Vazirmatn bridge', () => {
    expect(assertStage8bTypographyContract(currentSources(), currentProductSources())).toEqual({
      eligibility: 'route-contract-none-only',
      legacyBaseClass: 'font-sans',
      scopedClass: 'app-route--persian-typography',
      family: STAGE8B_APPROVED_PERSIAN_FAMILY,
      fontSynthesis: 'none',
      bootstrap: 'local-vazirmatn-font-face',
    })
  })

  it('fails closed when protected routes can inherit the bridge or the legacy base changes', () => {
    const sources = currentSources()
    const widenedProtection = withMutation(sources, STAGE8B_TYPOGRAPHY_PATHS.app, (source) =>
      source.replace(
        /const usesApprovedPersianTypography = computed\(\n\s*\(\) => getUiRouteContractByName\(route\.name\)\?\.protection === UI_ROUTE_PROTECTION\.NONE,/,
        'const usesApprovedPersianTypography = computed(\n  () => getUiRouteContractByName(route.name)?.protection !== UI_ROUTE_PROTECTION.FULL,',
      ),
    )
    expect(() => assertStage8bTypographyContract(widenedProtection)).toThrow(/strict NONE-only/)

    const baseFamilyDrift = withMutation(sources, STAGE8B_TYPOGRAPHY_PATHS.app, (source) =>
      source.replace('font-sans text-gray-900', 'font-serif text-gray-900'),
    )
    expect(() => assertStage8bTypographyContract(baseFamilyDrift)).toThrow(/static class contract/)

    const rootLayoutDrift = withMutation(sources, STAGE8B_TYPOGRAPHY_PATHS.app, (source) =>
      source.replace('font-sans text-gray-900', 'font-sans p-96 text-gray-900'),
    )
    expect(() => assertStage8bTypographyContract(rootLayoutDrift)).toThrow(/static class contract/)
  })

  it('requires both route branches to bind the local marker through the approved computed', () => {
    const sources = currentSources()
    const inverseCondition = withMutation(sources, STAGE8B_TYPOGRAPHY_PATHS.app, (source) =>
      source.replace(
        'usesApprovedPersianTypography.value ? \'app-route--persian-typography\' : undefined',
        'usesApprovedPersianTypography.value ? \'app-route--persian-typography\' : \'app-route--persian-typography\'',
      ),
    )
    expect(() => assertStage8bTypographyContract(inverseCondition)).toThrow(/marker must be derived/)

    const missingUnscopedMarker = withMutation(sources, STAGE8B_TYPOGRAPHY_PATHS.app, (source) => {
      return source.replace(
        ':class="[reducedMotionRouteClass, persianTypographyRouteClass]"',
        ':class="reducedMotionRouteClass"',
      )
    })
    expect(() => assertStage8bTypographyContract(missingUnscopedMarker)).toThrow(/unscoped route vnode branch/)
  })

  it('limits App-local CSS to the approved literal family and font synthesis', () => {
    const sources = currentSources()
    const familyDrift = withMutation(sources, STAGE8B_TYPOGRAPHY_PATHS.app, (source) =>
      source.replace('font-family: Vazirmatn, Tahoma, Arial, sans-serif;', 'font-family: Arial, sans-serif;'),
    )
    expect(() => assertStage8bTypographyContract(familyDrift)).toThrow(/only the approved literal family/)

    const layoutLeak = withMutation(sources, STAGE8B_TYPOGRAPHY_PATHS.app, (source) =>
      source.replace('font-synthesis: none;', 'font-synthesis: none;\n  padding: 64px;'),
    )
    expect(() => assertStage8bTypographyContract(layoutLeak)).toThrow(/only the approved literal family/)

    const selectorLeak = withMutation(sources, STAGE8B_TYPOGRAPHY_PATHS.app, (source) =>
      source.replace(
        '.app-route--persian-typography',
        '.app-shell.app-route--persian-typography',
      ),
    )
    expect(() => assertStage8bTypographyContract(selectorLeak)).toThrow(/root shell must not receive/)

    const shellCascade = withMutation(sources, STAGE8B_TYPOGRAPHY_PATHS.app, (source) =>
      source.replace(
        'background: var(--ds-app-background);',
        'background: var(--ds-app-background);\n  font-family: Vazirmatn, Tahoma, Arial, sans-serif;',
      ),
    )
    expect(() => assertStage8bTypographyContract(shellCascade)).toThrow(/root shell must not receive/)
  })

  it('rejects global main.css typography/cascade drift while preserving legacy token roles', () => {
    const sources = currentSources()
    const bodyFamily = withMutation(sources, STAGE8B_TYPOGRAPHY_PATHS.mainCss, (source) =>
      `${source}\n@layer base { body { font-family: Vazirmatn, sans-serif; } }\n`,
    )
    expect(() => assertStage8bTypographyContract(bodyFamily)).toThrow(/global typography\/layout cascade/)

    const rootTailwindFamily = withMutation(sources, STAGE8B_TYPOGRAPHY_PATHS.mainCss, (source) =>
      `${source}\n:root { --font-sans: Vazirmatn, sans-serif; }\n`,
    )
    expect(() => assertStage8bTypographyContract(rootTailwindFamily)).toThrow(/global typography token baseline/)

    const legacyTokenDrift = withMutation(sources, STAGE8B_TYPOGRAPHY_PATHS.mainCss, (source) =>
      source.replace('--ds-font-base: 0.85rem;', '--ds-font-base: 1rem;'),
    )
    expect(() => assertStage8bTypographyContract(legacyTokenDrift)).toThrow(/global typography token baseline/)
  })

  it('locks the HTML and TypeScript bootstrap font boundary', () => {
    const sources = currentSources()
    const bootFamilyDrift = withMutation(sources, STAGE8B_TYPOGRAPHY_PATHS.indexHtml, (source) =>
      source.replace('font-family: Vazirmatn, system-ui', 'font-family: Arial, system-ui'),
    )
    expect(() => assertStage8bTypographyContract(bootFamilyDrift)).toThrow(/bootstrap typography baseline/)

    const stylesheetLink = withMutation(sources, STAGE8B_TYPOGRAPHY_PATHS.indexHtml, (source) =>
      source.replace('</head>', '<link rel="stylesheet" href="/unexpected-font.css">\n</head>'),
    )
    expect(() => assertStage8bTypographyContract(stylesheetLink)).toThrow(/stylesheet link/)

    const missingLocalFace = withMutation(sources, STAGE8B_TYPOGRAPHY_PATHS.mainTs, (source) =>
      source.replace("import 'vazirmatn/Vazirmatn-font-face.css'\n", ''),
    )
    expect(() => assertStage8bTypographyContract(missingLocalFace)).toThrow(/bootstrap CSS imports/)

    const runtimeMutation = withMutation(sources, STAGE8B_TYPOGRAPHY_PATHS.mainTs, (source) =>
      `${source}\ndocument.body.style.fontFamily = 'Arial'\n`,
    )
    expect(() => assertStage8bTypographyContract(runtimeMutation)).toThrow(/must not mutate root typography/)
  })

  it('rejects the route-local marker in every product source other than App.vue', () => {
    const productSources = currentProductSources()
    productSources.set(
      'frontend/src/components/UnexpectedTypographyLeak.vue',
      '<template><div class="app-route--persian-typography" /></template>',
    )
    expect(() => assertStage8bTypographyProductSourceBoundary(productSources)).toThrow(
      /must not appear outside App\.vue: frontend\/src\/components\/UnexpectedTypographyLeak\.vue/,
    )
  })
})
