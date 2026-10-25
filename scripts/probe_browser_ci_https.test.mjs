import assert from 'node:assert/strict'
import fs from 'node:fs'
import https from 'node:https'
import os from 'node:os'
import path from 'node:path'
import { spawnSync } from 'node:child_process'
import test from 'node:test'
import { createRequire } from 'node:module'
import { prepareHttps, previewHttps, trustedHttpsProxy } from '../frontend/scripts/browser-ci-https.mjs'

const FAILURE = /browser_ci_https_fixture_invalid/
function fixture(t) {
  const base = fs.mkdtempSync(path.join(os.tmpdir(), 'owned-browser-tls-test-'))
  const temporary = path.join(base, 'runner-temp')
  const workspace = path.join(base, 'workspace')
  fs.mkdirSync(temporary, { mode: 0o700 }); fs.mkdirSync(workspace, { mode: 0o700 })
  t.after(() => fs.rmSync(base, { recursive: true, force: true }))
  return { base, env: { CI: 'true', GITHUB_REPOSITORY: 'mohsenbarari/trading_bot',
    RUNNER_TEMP: temporary, GITHUB_WORKSPACE: workspace } }
}
function enabled(t) {
  const value = fixture(t)
  value.env.BROWSER_CI_TLS_ROOT = prepareHttps(value.env)
  value.env.BROWSER_CI_PREVIEW_TLS = '1'
  return value
}

test('ordinary Vite configuration does not load any certificate', () => {
  assert.equal(previewHttps({ BROWSER_CI_TLS_ROOT: '/nonexistent/private-sentinel' }), undefined)
})

test('actual pinned Playwright config replaces the ordinary server rather than concatenating two servers', () => {
  const require = createRequire(path.join(process.cwd(), 'frontend/package.json'))
  const { defineConfig } = require('@playwright/test')
  const ordinary = { use: { baseURL: 'http://127.0.0.1:4173' }, webServer: { port: 4173 },
    workers: 1, retries: 2, projects: [{ name: 'webkit' }] }
  const source = fs.readFileSync(new URL('../frontend/playwright.https-diagnostic.config.ts', import.meta.url), 'utf8')
    .replace(/^import .*$/gm, '').replace('export default', 'return')
  const config = new Function('process', 'defineConfig', 'ordinary', source)(
    { env: { CI: 'true', BROWSER_CI_PREVIEW_TLS: '1' } }, defineConfig, ordinary)
  assert.equal(Array.isArray(config.webServer), false)
  assert.equal(config.webServer.url, 'https://127.0.0.1:4173')
  assert.equal(config.webServer.port, undefined)
  assert.equal(config.use.ignoreHTTPSErrors, false)
  assert.equal(config.webServer.ignoreHTTPSErrors, false)
  assert.equal(config.workers, ordinary.workers)
  assert.equal(config.retries, ordinary.retries)
  assert.equal(ordinary.webServer.port, 4173)
})

test('full-matrix production login keeps its exact test selection and quality gates with one strict HTTPS server', () => {
  const require = createRequire(path.join(process.cwd(), 'frontend/package.json'))
  const { defineConfig } = require('@playwright/test')
  const base = { timeout: 30000, expect: { timeout: 5000 }, forbidOnly: true, retries: 2, workers: 1,
    grepInvert: /login stays keyboard-usable and hides developer shortcut/,
    use: { baseURL: 'http://127.0.0.1:4173', actionTimeout: 0, trace: 'on-first-retry', headless: true },
    projects: [{ name: 'chromium' }, { name: 'firefox' }, { name: 'webkit' }] }
  const evaluate = (filename, bindings) => {
    const source = fs.readFileSync(new URL(`../frontend/${filename}`, import.meta.url), 'utf8')
      .replace(/^import .*$/gm, '').replace('export default', 'return')
    return new Function('defineConfig', ...Object.keys(bindings), source)(defineConfig, ...Object.values(bindings))
  }
  const ordinary = evaluate('playwright.production-login.config.ts', { base })
  const fixtureProcess = { env: { CI: 'true', BROWSER_CI_PREVIEW_TLS: '1' } }
  const config = evaluate('playwright.https-production-login.config.ts', { process: fixtureProcess, ordinary })
  for (const key of Object.keys(ordinary).filter(key => !['use', 'webServer'].includes(key))) {
    assert.deepEqual(config[key], ordinary[key])
  }
  assert.equal(config.webServer.command, ordinary.webServer.command)
  assert.equal(Array.isArray(config.webServer), false)
  assert.equal(config.webServer.port, undefined)
  assert.equal(config.webServer.url, 'https://127.0.0.1:4174')
  assert.equal(config.webServer.ignoreHTTPSErrors, false)
  assert.equal(config.webServer.reuseExistingServer, false)
  assert.deepEqual(config.use, { ...ordinary.use, baseURL: 'https://127.0.0.1:4174', ignoreHTTPSErrors: false })
  assert.equal(ordinary.use.baseURL, 'http://127.0.0.1:4174')
  for (const env of [{}, { CI: 'true' }, { CI: 'false', BROWSER_CI_PREVIEW_TLS: '1' }]) {
    assert.throws(() => evaluate('playwright.https-production-login.config.ts', { process: { env }, ordinary }),
      /browser_ci_https_fixture_required/)
  }
})

test('synthetic keys and certificates live only in the owned runner-temp namespace', t => {
  const { env } = enabled(t)
  const root = env.BROWSER_CI_TLS_ROOT
  assert.ok(root.startsWith(path.join(env.RUNNER_TEMP, 'browser-ci-https-')))
  assert.equal(fs.statSync(root).mode & 0o777, 0o700)
  assert.deepEqual(fs.readdirSync(root).sort(), ['ca.crt', 'ca.key', 'server.crt', 'server.csr', 'server.ext', 'server.key'])
  for (const name of fs.readdirSync(root)) {
    const value = fs.lstatSync(path.join(root, name))
    assert.equal(value.uid, process.getuid())
    assert.equal(value.mode & 0o777, 0o600)
    assert.equal(value.nlink, 1)
  }
  const tls = previewHttps(env)
  assert.ok(Buffer.isBuffer(tls.key) && Buffer.isBuffer(tls.cert))
  assert.deepEqual(fs.readdirSync(env.GITHUB_WORKSPACE), [])
})

test('real owned TLS handshake requires the synthetic CA and matching loopback identity', async t => {
  const { env } = enabled(t)
  const server = https.createServer(previewHttps(env), (_request, response) => response.end('owned'))
  await new Promise((resolve, reject) => { server.once('error', reject); server.listen(0, '127.0.0.1', resolve) })
  const get = options => new Promise((resolve, reject) => {
    const request = https.get({ host: '127.0.0.1', port: server.address().port, rejectUnauthorized: true,
      timeout: 3000, ...options }, response => { response.resume(); response.once('end', () => resolve(response.statusCode)) })
    request.once('error', reject)
    request.once('timeout', () => request.destroy(new Error('owned_timeout')))
  })
  try {
    const ca = fs.readFileSync(path.join(env.BROWSER_CI_TLS_ROOT, 'ca.crt'))
    assert.equal(await get({ ca }).then(status => status === 200, () => false), true)
    assert.equal(await get({}).then(() => false, () => true), true)
    assert.equal(await get({ ca, servername: 'untrusted.invalid' })
      .then(() => false, error => error.code === 'ERR_TLS_CERT_ALTNAME_INVALID'), true)
  } finally {
    server.closeAllConnections()
    await new Promise((resolve, reject) => server.close(error => error ? reject(error) : resolve()))
  }
})

test('non-CI, foreign repository and workspace temp roots refuse before certificate creation', t => {
  for (const patch of [{ CI: 'false' }, { GITHUB_REPOSITORY: 'untrusted/repository' },
    { RUNNER_TEMP: null }, { RUNNER_TEMP: undefined }]) {
    const { env } = fixture(t)
    assert.throws(() => prepareHttps({ ...env, ...patch }), FAILURE)
    assert.deepEqual(fs.readdirSync(env.RUNNER_TEMP), [])
  }
  const { env } = fixture(t)
  assert.throws(() => prepareHttps({ ...env, RUNNER_TEMP: env.GITHUB_WORKSPACE }), FAILURE)
  assert.deepEqual(fs.readdirSync(env.GITHUB_WORKSPACE), [])
})

test('wrong key mode is rejected before any payload open; symlink roots and files refuse', t => {
  const { env } = enabled(t)
  const key = path.join(env.BROWSER_CI_TLS_ROOT, 'server.key')
  fs.chmodSync(key, 0o644)
  const original = fs.openSync
  let opened = false
  fs.openSync = function (filename, ...args) {
    if (filename === key) opened = true
    return original.call(this, filename, ...args)
  }
  try { assert.throws(() => previewHttps(env), FAILURE) } finally { fs.openSync = original }
  assert.equal(opened, false)
  fs.chmodSync(key, 0o600)
  fs.renameSync(key, `${key}.retained`)
  fs.symlinkSync(`${key}.retained`, key)
  assert.throws(() => previewHttps(env), FAILURE)
  const alias = path.join(env.RUNNER_TEMP, 'browser-ci-https-alias')
  fs.symlinkSync(env.BROWSER_CI_TLS_ROOT, alias)
  assert.throws(() => previewHttps({ ...env, BROWSER_CI_TLS_ROOT: alias }), FAILURE)
})

test('same-byte replacement between metadata admission and open refuses', t => {
  const { env } = enabled(t)
  const cert = path.join(env.BROWSER_CI_TLS_ROOT, 'server.crt')
  const bytes = fs.readFileSync(cert)
  const replacement = `${cert}.replacement`
  fs.writeFileSync(replacement, bytes, { mode: 0o600, flag: 'wx' })
  const original = fs.openSync
  fs.openSync = function (filename, ...args) {
    if (filename === cert) fs.renameSync(replacement, cert)
    return original.call(this, filename, ...args)
  }
  try { assert.throws(() => previewHttps(env), FAILURE) } finally { fs.openSync = original }
})

test('only a real HTTPS listener supplies proxy scheme; foreign forwarded Host is removed', () => {
  const handlers = new Map()
  trustedHttpsProxy({ on(name, callback) { handlers.set(name, callback) } })
  assert.deepEqual([...handlers.keys()], ['proxyReq', 'proxyReqWs'])
  for (const handler of handlers.values()) {
    const headers = new Map([['host', '127.0.0.1:4173'], ['x-forwarded-host', 'untrusted.invalid'], ['x-forwarded-proto', 'http']])
    let refused = false
    const request = { removeHeader: key => headers.delete(key), setHeader: (key, value) => headers.set(key, value),
      destroy: () => { refused = true } }
    handler(request, { socket: { encrypted: true } })
    assert.equal(headers.get('host'), '127.0.0.1:4173')
    assert.equal(headers.get('x-forwarded-proto'), 'https')
    assert.equal(headers.has('x-forwarded-host'), false)
    assert.equal(refused, false)
    for (const encrypted of [false, undefined, 'true']) {
      refused = false
      handler(request, { socket: { encrypted } })
      assert.equal(refused, true)
    }
  }
})

test('CLI exports only owned file paths and keeps synthetic key material out of stdout', t => {
  const { env } = fixture(t)
  env.GITHUB_ENV = path.join(env.RUNNER_TEMP, 'owned-github-env')
  fs.writeFileSync(env.GITHUB_ENV, '', { mode: 0o600, flag: 'wx' })
  const result = spawnSync(process.execPath, [new URL('../frontend/scripts/browser-ci-https.mjs', import.meta.url).pathname, 'prepare'], {
    env: { PATH: '/usr/bin:/bin', ...env }, timeout: 35000, encoding: 'utf8', maxBuffer: 1024,
  })
  assert.equal(result.status, 0)
  assert.equal(result.stdout === 'browser_ci_https_prepared\n', true)
  assert.equal(result.stderr === '', true)
  const values = Object.fromEntries(fs.readFileSync(env.GITHUB_ENV, 'utf8').trim().split('\n').map(line => line.split('=')))
  assert.equal(Object.keys(values).sort().join(',') === 'BROWSER_CI_PREVIEW_TLS,BROWSER_CI_TLS_CA,BROWSER_CI_TLS_ROOT,NODE_EXTRA_CA_CERTS', true)
  assert.equal(values.BROWSER_CI_PREVIEW_TLS, '1')
  assert.equal(values.NODE_EXTRA_CA_CERTS, path.join(values.BROWSER_CI_TLS_ROOT, 'ca.crt'))
  assert.ok(values.BROWSER_CI_TLS_ROOT.startsWith(path.join(env.RUNNER_TEMP, 'browser-ci-https-')))
})
