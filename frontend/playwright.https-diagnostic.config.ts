import process from 'node:process'
import { defineConfig } from '@playwright/test'
import ordinary from './playwright.config'

if (process.env.CI !== 'true' || process.env.BROWSER_CI_PREVIEW_TLS !== '1') {
  throw new Error('browser_ci_https_fixture_required')
}

// Synthetic browser CI only. The owned CA is trusted by the hosted OS and
// NODE_EXTRA_CA_CERTS; certificate errors remain fatal in both clients.
export default defineConfig({
  ...ordinary,
  use: {
    ...ordinary.use,
    baseURL: 'https://127.0.0.1:4173',
    ignoreHTTPSErrors: false,
  },
  webServer: {
    command: 'npm run preview -- --host 127.0.0.1 --strictPort --port 4173',
    url: 'https://127.0.0.1:4173',
    reuseExistingServer: false,
    ignoreHTTPSErrors: false,
  },
})
