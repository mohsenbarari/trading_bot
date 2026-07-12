import { fileURLToPath } from 'node:url'
import { mergeConfig, defineConfig, configDefaults } from 'vitest/config'
import viteConfig from './vite.config'

export default mergeConfig(
  viteConfig,
  defineConfig({
    test: {
      environment: 'jsdom',
      exclude: [...configDefaults.exclude, 'e2e/**'],
      root: fileURLToPath(new URL('./', import.meta.url)),
      coverage: {
        provider: 'v8',
        reportsDirectory: fileURLToPath(new URL('./coverage', import.meta.url)),
        reporter: ['text-summary', 'json-summary', 'json', 'html', 'lcov'],
        include: ['src/**/*.{ts,vue}'],
      },
    },
  }),
)
