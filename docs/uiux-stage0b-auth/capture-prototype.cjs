const fs = require('node:fs')
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
  const htmlPath = path.join(root, 'auth-scenarios.html')
  const assetsDir = path.join(root, 'assets')
  fs.mkdirSync(assetsDir, { recursive: true })

  const browser = await chromium.launch({ headless: true })
  try {
    const page = await browser.newPage({
      viewport: { width: 2000, height: 1200 },
      deviceScaleFactor: 1,
    })

    await page.goto(pathToFileURL(htmlPath).href, { waitUntil: 'load' })
    await page.evaluate(() => document.fonts.ready)

    await page.screenshot({
      path: path.join(assetsDir, 'stage0b-auth-overview.png'),
      fullPage: true,
    })

    const groupCaptures = [
      ['#mobile-scenarios', 'stage0b-auth-mobile-scenarios.png'],
      ['#keyboard-states', 'stage0b-auth-keyboard-states.png'],
      ['#state-atlas', 'stage0b-auth-state-atlas.png'],
      ['#adaptive-desktop', 'stage0b-auth-adaptive-desktop.png'],
    ]
    for (const [selector, filename] of groupCaptures) {
      await page.locator(selector).screenshot({ path: path.join(assetsDir, filename) })
    }

    const frameCaptures = [
      ['#auth-login-mobile', 'auth-01-login-mobile.png'],
      ['#auth-login-otp', 'auth-02-login-otp.png'],
      ['#auth-device-approval', 'auth-03-device-approval.png'],
      ['#auth-recovery-waiting', 'auth-04-recovery-waiting.png'],
      ['#auth-invite-valid', 'auth-05-invite-valid.png'],
      ['#auth-register-review', 'auth-06-register-review.png'],
      ['#auth-register-otp', 'auth-07-register-otp.png'],
      ['#auth-register-address', 'auth-08-register-address.png'],
      ['#auth-register-telegram', 'auth-09-register-telegram.png'],
      ['#auth-recovery-identity', 'auth-10-recovery-identity.png'],
      ['#auth-setup-password', 'auth-11-setup-password.png'],
    ]
    for (const [selector, filename] of frameCaptures) {
      await page.locator(selector).screenshot({ path: path.join(assetsDir, filename) })
    }

    const responsiveWidths = []
    for (const width of [360, 375, 390, 414, 430]) {
      responsiveWidths.push(await page.evaluate((targetWidth) => {
        const phones = [...document.querySelectorAll('.phone')]
        phones.forEach((node) => { node.style.width = `${targetWidth}px` })
        return {
          width: targetWidth,
          framesWithHorizontalOverflow: phones
            .filter((node) => node.scrollWidth > node.clientWidth)
            .map((node) => node.closest('.scenario')?.id || ''),
          maximumScrollWidth: Math.max(...phones.map((node) => node.scrollWidth)),
        }
      }, width))
    }
    await page.evaluate(() => {
      document.querySelectorAll('.phone').forEach((node) => { node.style.width = '' })
    })

    const metrics = await page.evaluate(() => {
      const touchTargets = [...document.querySelectorAll('.touch-target')].map((node) => {
        const rect = node.getBoundingClientRect()
        return { width: Math.round(rect.width), height: Math.round(rect.height), label: node.textContent?.trim().slice(0, 48) || '' }
      })
      return {
        document: {
          scrollWidth: document.documentElement.scrollWidth,
          scrollHeight: document.documentElement.scrollHeight,
        },
        phones: [...document.querySelectorAll('.phone')].map((node) => ({
          id: node.closest('.scenario')?.id || '',
          width: Math.round(node.getBoundingClientRect().width),
          height: Math.round(node.getBoundingClientRect().height),
          scrollWidth: node.scrollWidth,
          scrollHeight: node.scrollHeight,
        })),
        touchTargets: {
          count: touchTargets.length,
          minimumWidth: Math.min(...touchTargets.map((item) => item.width)),
          minimumHeight: Math.min(...touchTargets.map((item) => item.height)),
          below44: touchTargets.filter((item) => item.width < 44 || item.height < 44),
        },
        font: getComputedStyle(document.body).fontFamily,
      }
    })

    metrics.responsiveWidths = responsiveWidths

    fs.writeFileSync(
      path.join(assetsDir, 'stage0b-auth-validation-metrics.json'),
      `${JSON.stringify(metrics, null, 2)}\n`,
      'utf8',
    )

    process.stdout.write(`${JSON.stringify(metrics, null, 2)}\n`)
  } finally {
    await browser.close()
  }
}

main().catch((error) => {
  console.error(error)
  process.exitCode = 1
})
