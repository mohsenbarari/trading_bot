import process from 'node:process'
import { defineConfig } from '@playwright/test'
import ordinary from './playwright.production-login.config'

if (process.env.CI !== 'true' || process.env.BROWSER_CI_PREVIEW_TLS !== '1') {
  throw new Error('browser_ci_https_fixture_required')
}

// Keep the separately built production-login test and its normal quality gates.
export default defineConfig({
  ...ordinary,
  use: {
    ...ordinary.use,
    baseURL: 'https://127.0.0.1:4174',
    ignoreHTTPSErrors: false,
  },
  webServer: {
    command: 'npm run preview -- --host 127.0.0.1 --strictPort --port 4174 --outDir dist/production-login',
    url: 'https://127.0.0.1:4174',
    reuseExistingServer: false,
    ignoreHTTPSErrors: false,
  },
})
