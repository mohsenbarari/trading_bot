import assert from 'node:assert/strict'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import vue from '@vitejs/plugin-vue'
import { chromium } from 'playwright'
import { createServer } from 'vite'

const scriptDir = path.dirname(fileURLToPath(import.meta.url))
const frontendRoot = path.resolve(scriptDir, '..')
const virtualEntryId = 'virtual:ui-v2-catalog-browser-entry'
const resolvedVirtualEntryId = `\0${virtualEntryId}`
const catalogPath = '/src/components/ui/AppDesignSystemCatalog.vue'
const previewPath = '/__ui-v2-catalog'
const referenceWidths = [360, 375, 390, 414, 430, 1440]

const previewPlugin = {
  name: 'ui-v2-catalog-browser-preview',
  configureServer(server) {
    server.middlewares.use((request, response, next) => {
      const pathname = new URL(request.url ?? '/', 'http://127.0.0.1').pathname
      if (pathname !== previewPath) return next()

      response.statusCode = 200
      response.setHeader('Content-Type', 'text/html; charset=utf-8')
      response.end(
        [
          '<!doctype html>',
          '<html lang="fa" dir="rtl">',
          '<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>',
          '<body><div id="app"></div>',
          `<script type="module" src="/@id/${virtualEntryId}"></script>`,
          '</body></html>',
        ].join(''),
      )
    })
  },
  resolveId(id) {
    if (id === virtualEntryId) return resolvedVirtualEntryId
    return null
  },
  load(id) {
    if (id !== resolvedVirtualEntryId) return null
    return [
      "import '/src/assets/main.css'",
      "import { createApp } from 'vue'",
      `import Catalog from '${catalogPath}'`,
      "createApp(Catalog).mount('#app')",
    ].join('\n')
  },
}

function closeEnough(actual, expected, tolerance = 0.02) {
  return Math.abs(actual - expected) <= tolerance
}

function contrastRatio(foreground, background) {
  const luminance = (hex) => {
    const channels = hex
      .slice(1)
      .match(/../g)
      .map((value) => Number.parseInt(value, 16) / 255)
      .map((value) => (value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4))
    return channels[0] * 0.2126 + channels[1] * 0.7152 + channels[2] * 0.0722
  }
  const first = luminance(foreground)
  const second = luminance(background)
  return (Math.max(first, second) + 0.05) / (Math.min(first, second) + 0.05)
}

let server
let browser

try {
  server = await createServer({
    root: frontendRoot,
    configFile: false,
    logLevel: 'error',
    plugins: [vue(), previewPlugin],
    server: {
      host: '127.0.0.1',
      port: 0,
      strictPort: false,
    },
  })
  await server.listen()

  const address = server.httpServer?.address()
  if (!address || typeof address === 'string') throw new Error('Vite did not expose a TCP port.')
  const url = `http://127.0.0.1:${address.port}${previewPath}`

  browser = await chromium.launch({ headless: true })
  const page = await browser.newPage()
  const responsive = []

  for (const width of referenceWidths) {
    await page.setViewportSize({ width, height: width === 1440 ? 900 : 1000 })
    await page.goto(url, { waitUntil: 'networkidle' })
    await page.locator('[data-test="ui-v2-catalog"]').waitFor()

    const measurement = await page.evaluate(() => {
      const root = document.querySelector('[data-test="ui-v2-catalog"]')
      if (!(root instanceof HTMLElement)) throw new Error('Catalog root is missing.')

      const visible = (element) => {
        const style = getComputedStyle(element)
        const rect = element.getBoundingClientRect()
        return (
          style.display !== 'none' &&
          style.visibility !== 'hidden' &&
          rect.width > 0 &&
          rect.height > 0
        )
      }

      const targets = [
        ...root.querySelectorAll('button, input, select, textarea, a[href], [tabindex]'),
      ]
        .filter((element) => element instanceof HTMLElement && visible(element))
        .map((element) => {
          const rect = element.getBoundingClientRect()
          return { tag: element.tagName, width: rect.width, height: rect.height }
        })

      const ctas = [...root.querySelectorAll('.ui-button')]
        .filter((element) => element instanceof HTMLElement && visible(element))
        .map((element) => element.getBoundingClientRect().height)

      const overflow = [root, ...root.querySelectorAll('[data-overflow-contract]')].map(
        (element) => ({
          name: element.getAttribute('data-overflow-contract') ?? 'root',
          clientWidth: element.clientWidth,
          scrollWidth: element.scrollWidth,
        }),
      )

      return {
        viewportWidth: window.innerWidth,
        documentClientWidth: document.documentElement.clientWidth,
        documentScrollWidth: document.documentElement.scrollWidth,
        rootClientWidth: root.clientWidth,
        rootScrollWidth: root.scrollWidth,
        targets,
        ctas,
        overflow,
      }
    })

    assert.equal(measurement.viewportWidth, width)
    assert.ok(
      measurement.documentScrollWidth <= measurement.documentClientWidth + 1,
      `Document overflows at ${width}px.`,
    )
    assert.ok(
      measurement.rootScrollWidth <= measurement.rootClientWidth + 1,
      `Catalog root overflows at ${width}px.`,
    )
    assert.ok(measurement.targets.length > 0, `No interactive targets measured at ${width}px.`)
    assert.ok(
      measurement.targets.every(
        ({ width: targetWidth, height }) => targetWidth >= 44 && height >= 44,
      ),
      `A target is smaller than 44x44 at ${width}px.`,
    )
    assert.ok(
      measurement.ctas.length > 0 && measurement.ctas.every((height) => height >= 48),
      `A catalog CTA is shorter than 48px at ${width}px.`,
    )
    assert.ok(
      measurement.overflow.every(({ clientWidth, scrollWidth }) => scrollWidth <= clientWidth + 1),
      `A catalog landmark overflows at ${width}px.`,
    )
    responsive.push(measurement)
  }

  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto(url, { waitUntil: 'networkidle' })
  const focusTarget = page.locator('[data-test="focus-proof"] .ui-button')
  await focusTarget.focus()
  await page.waitForTimeout(250)
  const focus = await focusTarget.evaluate((element) => {
    const style = getComputedStyle(element)
    return {
      active: document.activeElement === element,
      outlineWidth: Number.parseFloat(style.outlineWidth),
      outlineStyle: style.outlineStyle,
      outlineColor: style.outlineColor,
      outlineOffset: Number.parseFloat(style.outlineOffset),
      borderColor: style.borderColor,
      boxShadow: style.boxShadow,
      height: element.getBoundingClientRect().height,
    }
  })

  assert.equal(focus.active, true)
  assert.ok(
    closeEnough(focus.outlineWidth, 3),
    `Expected 3px focus outline, got ${focus.outlineWidth}.`,
  )
  assert.equal(focus.outlineStyle, 'solid')
  assert.equal(focus.outlineColor, 'rgb(47, 111, 237)')
  assert.ok(
    closeEnough(focus.outlineOffset, 2),
    `Expected 2px focus offset, got ${focus.outlineOffset}.`,
  )
  assert.equal(focus.borderColor, 'rgb(47, 111, 237)')
  assert.equal(focus.boxShadow, 'none')
  assert.ok(focus.height >= 48)

  const tokenValues = await page.locator('[data-test="ui-v2-catalog"]').evaluate((root) => {
    const style = getComputedStyle(root)
    return {
      placeholder: style.getPropertyValue('--ui-v2-color-text-placeholder').trim(),
      border: style.getPropertyValue('--ui-v2-color-border-default').trim(),
      placeholderPrimitive: style.getPropertyValue('--ui-v2-neutral-ink-700').trim(),
      borderPrimitive: style.getPropertyValue('--ui-v2-neutral-border-300').trim(),
      pageSurface: style.getPropertyValue('--ui-v2-neutral-surface-100').trim(),
      cardSurface: style.getPropertyValue('--ui-v2-neutral-white').trim(),
    }
  })
  assert.equal(tokenValues.placeholder, '#52697b')
  assert.equal(tokenValues.border, '#8091a3')
  assert.equal(tokenValues.placeholderPrimitive, '#52697b')
  assert.equal(tokenValues.borderPrimitive, '#8091a3')
  const borderContrast = {
    onCard: contrastRatio(tokenValues.border, tokenValues.cardSurface),
    onPage: contrastRatio(tokenValues.border, tokenValues.pageSurface),
  }
  assert.ok(borderContrast.onCard >= 3)
  assert.ok(borderContrast.onPage >= 3)

  const implementationContracts = await page
    .locator('[data-test="ui-v2-catalog"]')
    .evaluate((root) => {
      const icons = [...root.querySelectorAll('[data-icon-size]')].map((proof) => {
        const svg = proof.querySelector('svg')
        if (!(svg instanceof SVGElement)) throw new Error('Icon proof SVG is missing.')
        const rect = svg.getBoundingClientRect()
        return {
          role: proof.getAttribute('data-icon-size'),
          token: proof.getAttribute('data-icon-token'),
          width: rect.width,
          height: rect.height,
        }
      })
      const list = root.querySelector('[data-test="list-contract"] ul')
      if (!(list instanceof HTMLUListElement)) throw new Error('Semantic list proof is missing.')
      const rows = [...list.children]
      return {
        icons,
        list: {
          tag: list.tagName,
          rowTags: rows.map((row) => row.tagName),
          childTags: rows.map((row) => row.firstElementChild?.tagName ?? null),
          childRoles: rows.map((row) => row.firstElementChild?.getAttribute('role') ?? null),
        },
      }
    })
  assert.deepEqual(implementationContracts.icons, [
    { role: 'small', token: '--ui-v2-icon-size-small', width: 16, height: 16 },
    { role: 'control', token: '--ui-v2-icon-size-control', width: 20, height: 20 },
    { role: 'large', token: '--ui-v2-icon-size-large', width: 24, height: 24 },
  ])
  assert.deepEqual(implementationContracts.list, {
    tag: 'UL',
    rowTags: ['LI', 'LI'],
    childTags: ['BUTTON', 'ARTICLE'],
    childRoles: [null, null],
  })

  await page.emulateMedia({ reducedMotion: 'reduce' })
  await page.reload({ waitUntil: 'networkidle' })
  const reducedMotion = await page
    .locator('[data-ui-v2-motion="essential"]')
    .evaluate((element) => getComputedStyle(element).transitionDuration)
  assert.equal(reducedMotion, '0.001s')

  process.stdout.write(
    `${JSON.stringify(
      {
        status: 'passed',
        widths: referenceWidths,
        targetMinimum: 44,
        ctaMinimum: 48,
        focus,
        tokenValues,
        borderContrast,
        implementationContracts,
        reducedMotion,
        responsive: responsive.map(({ viewportWidth, targets, ctas, overflow }) => ({
          viewportWidth,
          targetCount: targets.length,
          minimumTargetWidth: Math.min(...targets.map(({ width }) => width)),
          minimumTargetHeight: Math.min(...targets.map(({ height }) => height)),
          minimumCtaHeight: Math.min(...ctas),
          overflow,
        })),
      },
      null,
      2,
    )}\n`,
  )
} finally {
  await browser?.close()
  await server?.close()
}
