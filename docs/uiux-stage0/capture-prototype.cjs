const path = require('node:path')
const { pathToFileURL } = require('node:url')

let chromium
try {
  ({ chromium } = require('playwright'))
} catch {
  ({ chromium } = require(path.resolve(__dirname, '../../frontend/node_modules/playwright')))
}

async function main() {
  const root = __dirname
  const htmlPath = path.join(root, 'visual-directions.html')
  const assetsDir = path.join(root, 'assets')
  const browser = await chromium.launch({ headless: true })

  try {
    const page = await browser.newPage({
      viewport: { width: 1600, height: 1200 },
      deviceScaleFactor: 1,
    })

    await page.goto(pathToFileURL(htmlPath).href, { waitUntil: 'load' })
    await page.evaluate(() => document.fonts.ready)

    await page.screenshot({
      path: path.join(assetsDir, 'stage0-visual-directions.png'),
      fullPage: true,
    })

    await page.locator('.directions-grid').screenshot({
      path: path.join(assetsDir, 'stage0-mobile-comparison.png'),
    })

    const captures = [
      ['#direction-calm', 'direction-a-calm-premium.png'],
      ['#direction-modern', 'direction-b-modern-finance.png'],
      ['#direction-iranian', 'direction-c-contemporary-iranian.png'],
      ['#adaptive-desktop', 'adaptive-desktop-calm-premium.png'],
    ]

    for (const [selector, filename] of captures) {
      await page.locator(selector).screenshot({ path: path.join(assetsDir, filename) })
    }

    const metrics = await page.evaluate(() => ({
      document: {
        scrollWidth: document.documentElement.scrollWidth,
        scrollHeight: document.documentElement.scrollHeight,
      },
      phones: [...document.querySelectorAll('.phone')].map((node) => ({
        width: node.getBoundingClientRect().width,
        height: node.getBoundingClientRect().height,
        scrollWidth: node.scrollWidth,
        scrollHeight: node.scrollHeight,
      })),
      font: getComputedStyle(document.body).fontFamily,
    }))

    process.stdout.write(`${JSON.stringify(metrics, null, 2)}\n`)
  } finally {
    await browser.close()
  }
}

main().catch((error) => {
  console.error(error)
  process.exitCode = 1
})
