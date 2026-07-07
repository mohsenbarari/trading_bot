import { execFileSync } from 'child_process'

const DEFAULT_LOCAL_BACKEND_BASE_URL = 'http://127.0.0.1:8000'
const DEFAULT_LOCAL_APP_CONTAINER_NAME = 'trading_bot_app'
const DEFAULT_LOCAL_REDIS_CONTAINER_NAME = 'trading_bot_redis'
const STAGING_MUTATION_CONFIRM = 'role-trading-staging-only'
const LOCAL_MUTATION_CONFIRM = 'local-dev-only'

export function getE2EBackendBaseUrl() {
  return (process.env.E2E_BACKEND_BASE_URL || DEFAULT_LOCAL_BACKEND_BASE_URL).trim()
}

function getTargetEnvironment() {
  return (process.env.E2E_TARGET_ENV || '').trim().toLowerCase()
}

function discoverLocalAppContainerName() {
  const stdout = execFileSync('docker', ['ps', '--format', '{{.Names}}'], {
    encoding: 'utf8',
  })

  const names = stdout
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean)

  const exactAppName = names.find((name) => name === DEFAULT_LOCAL_APP_CONTAINER_NAME)
  if (exactAppName) return exactAppName

  const appName = names.find((name) => name.includes('trading_bot') && /(^|_)app($|_|-)/.test(name))
  if (!appName) {
    throw new Error('Could not find a running trading bot app container')
  }
  return appName
}

export function getE2EAppContainerName() {
  const explicitContainerName = (process.env.E2E_APP_CONTAINER_NAME || '').trim()
  if (explicitContainerName) return explicitContainerName

  if (getTargetEnvironment() === 'staging') {
    throw new Error('E2E_APP_CONTAINER_NAME is required for staging mutation tests')
  }

  return discoverLocalAppContainerName()
}

export function getE2ERedisContainerName() {
  const explicitContainerName = (process.env.E2E_REDIS_CONTAINER_NAME || '').trim()
  if (explicitContainerName) return explicitContainerName

  if (getTargetEnvironment() === 'staging') {
    throw new Error('E2E_REDIS_CONTAINER_NAME is required for staging mutation tests')
  }

  return DEFAULT_LOCAL_REDIS_CONTAINER_NAME
}

function assertStagingMutationRuntime(containerName: string, backendBaseUrl: string) {
  if (process.env.E2E_ALLOW_STAGING_MUTATION !== STAGING_MUTATION_CONFIRM) {
    throw new Error(
      `staging mutation e2e requires E2E_ALLOW_STAGING_MUTATION=${STAGING_MUTATION_CONFIRM}`,
    )
  }
  if (containerName === DEFAULT_LOCAL_APP_CONTAINER_NAME || !/staging/i.test(containerName)) {
    throw new Error(
      `staging mutation e2e must target an explicit staging app container, got "${containerName}"`,
    )
  }
  if (backendBaseUrl === DEFAULT_LOCAL_BACKEND_BASE_URL || /:8000\b/.test(backendBaseUrl)) {
    throw new Error(
      `staging mutation e2e must not target the production/default backend URL, got "${backendBaseUrl}"`,
    )
  }
  if (!(/staging/i.test(backendBaseUrl) || /:8100\b/.test(backendBaseUrl))) {
    throw new Error(
      `staging mutation e2e backend URL must visibly point at staging, got "${backendBaseUrl}"`,
    )
  }
}

function assertStagingRedisTarget(containerName: string) {
  if (containerName === DEFAULT_LOCAL_REDIS_CONTAINER_NAME || !/staging/i.test(containerName)) {
    throw new Error(
      `staging mutation e2e must target an explicit staging Redis container, got "${containerName}"`,
    )
  }
}

function assertLocalMutationRuntime() {
  if (process.env.E2E_ALLOW_LOCAL_MUTATION !== LOCAL_MUTATION_CONFIRM) {
    throw new Error(
      `local mutation e2e requires E2E_TARGET_ENV=local and E2E_ALLOW_LOCAL_MUTATION=${LOCAL_MUTATION_CONFIRM}`,
    )
  }
}

function assertMutatingRuntime(containerName: string, backendBaseUrl: string) {
  const targetEnvironment = getTargetEnvironment()
  if (targetEnvironment === 'staging') {
    assertStagingMutationRuntime(containerName, backendBaseUrl)
    return
  }
  if (targetEnvironment === 'local') {
    assertLocalMutationRuntime()
    return
  }
  throw new Error(
    'mutating e2e tests require E2E_TARGET_ENV=staging or E2E_TARGET_ENV=local with the matching explicit confirmation env',
  )
}

export function runPythonInApp<T>(script: string, helperName = 'e2e mutation helper'): T {
  const containerName = getE2EAppContainerName()
  assertMutatingRuntime(containerName, getE2EBackendBaseUrl())

  const stdout = execFileSync('docker', ['exec', '-i', containerName, 'python', '-'], {
    input: script,
    encoding: 'utf8',
  })

  const lastLine = stdout
    .split(/\r?\n/)
    .map((line: string) => line.trim())
    .filter(Boolean)
    .at(-1)

  if (!lastLine) {
    throw new Error(`No JSON output returned from ${helperName}`)
  }

  return JSON.parse(lastLine) as T
}

export function runRedisCli(args: string[], helperName = 'e2e Redis helper') {
  const redisContainerName = getE2ERedisContainerName()
  assertMutatingRuntime(getE2EAppContainerName(), getE2EBackendBaseUrl())
  if (getTargetEnvironment() === 'staging') {
    assertStagingRedisTarget(redisContainerName)
  }

  return execFileSync('docker', ['exec', redisContainerName, 'redis-cli', ...args], {
    encoding: 'utf8',
  })
}
