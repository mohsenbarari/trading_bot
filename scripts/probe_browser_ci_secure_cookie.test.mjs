import assert from 'node:assert/strict'
import { once } from 'node:events'
import { spawn, spawnSync } from 'node:child_process'
import test from 'node:test'
import { bounded, loopback, observe } from './probe_browser_ci_secure_cookie.mjs'

const VALUE = 'public-synthetic-cookie'
const CONTROL = 'ci_cookie_control'
const SECURE = '__Host-ci_cookie_probe'
const FAILURE = /browser_cookie_probe_failed/

// These injected contexts model both allowed observations; they do not claim
// browser cookie behavior. The server and the owned child below are real.
function fixture(t, { accepted = true, retransmitted = accepted, failure, cleanupFailure, mutateRows } = {}) {
  const state = { closed: [], child: null, denied: false, origin: null }
  t.after(async () => {
    if (state.child?.exitCode === null && state.child?.signalCode === null) {
      const ended = once(state.child, 'exit')
      state.child.kill('SIGKILL')
      await bounded(ended)
    }
  })
  const browserType = {
    async launchServer(options) {
      assert.deepEqual(options, { host: '127.0.0.1', headless: true, timeout: 10000 })
      state.child = spawn(process.execPath, ['-e', 'setInterval(()=>{},1000)'], {
        env: { PATH: '/usr/bin:/bin' }, stdio: 'ignore',
      })
      await once(state.child, 'spawn')
      return {
        wsEndpoint: () => 'owned-fixture-endpoint',
        process: () => state.child,
        async close() {
          state.closed.push('server')
          if (cleanupFailure === 'server') throw new Error('private cleanup sentinel')
          if (state.child.exitCode === null && state.child.signalCode === null) {
            const ended = once(state.child, 'exit')
            state.child.kill('SIGTERM')
            await ended
          }
        },
        async kill() {
          state.closed.push('kill')
          const ended = once(state.child, 'exit')
          state.child.kill('SIGKILL')
          await ended
        },
      }
    },
    async connect() {
      if (failure === 'connect') throw new Error('private connection sentinel')
      return {
        async close() { state.closed.push('browser') },
        async newContext(options) {
          assert.deepEqual(options, { serviceWorkers: 'block', acceptDownloads: false })
          if (failure === 'context') throw new Error('private context sentinel')
          return {
            async close() { state.closed.push('context') },
            async route(pattern, handler) {
              assert.equal(pattern, '**/*')
              await handler({
                request: () => ({ url: () => 'https://unreachable.invalid/private' }),
                abort: () => { state.denied = true },
                continue: () => { throw new Error('external request permitted') },
              })
            },
            async newPage() {
              return {
                async goto(url) {
                  state.origin = new URL(url).origin
                  const response = await fetch(url)
                  assert.equal(response.status, 200)
                  await response.text()
                },
                async evaluate() {
                  if (failure === 'evaluate') throw new Error('private header sentinel')
                  const cookies = [`${CONTROL}=${VALUE}`]
                  if (retransmitted) cookies.push(`${SECURE}=${VALUE}`)
                  const response = await fetch(`${state.origin}/check`, { headers: { Cookie: cookies.join('; ') } })
                  return response.json()
                },
              }
            },
            async cookies(...urls) {
              assert.equal(urls.length, 0, 'inspect the whole fresh store, not HTTP-applicable cookies')
              const row = name => ({ name, value: VALUE, domain: '127.0.0.1', path: '/',
                httpOnly: true, sameSite: 'Strict', secure: name === SECURE })
              let rows = [row(CONTROL)]
              if (accepted) rows.push(row(SECURE))
              if (mutateRows) rows = mutateRows(rows)
              return rows
            },
          }
        },
      }
    },
  }
  return { state, browserType }
}

test('owned HTTP endpoint has the exact secure attributes and bounded fixed projections', async () => {
  const site = await loopback()
  try {
    const response = await fetch(`${site.origin}/set`)
    const headers = response.headers.getSetCookie()
    assert.equal(headers.length, 2)
    assert.ok(headers.includes(`${SECURE}=${VALUE}; Path=/; Secure; HttpOnly; SameSite=Strict`))
    assert.ok(headers.includes(`${CONTROL}=${VALUE}; Path=/; HttpOnly; SameSite=Strict`))
    await response.text()
    assert.deepEqual(await (await fetch(`${site.origin}/check`)).json(), { control: false, secure: false })
    assert.equal((await fetch(`${site.origin}/unknown`)).status, 404)
    assert.equal((await fetch(`${site.origin}/set`, { method: 'POST' })).status, 404)
    for (let index = 0; index < 13; index++) await (await fetch(`${site.origin}/favicon.ico`)).arrayBuffer()
    assert.equal((await fetch(`${site.origin}/set`)).status, 404)
  } finally { await site.close() }
  await assert.rejects(fetch(`${site.origin}/check`))
})

for (const [accepted, retransmitted] of [[false, false], [true, false], [true, true]]) {
  const label = accepted && !retransmitted ? 'stored Secure cookie can be accepted but unsent'
    : `observation faithfully reports ${accepted}/${retransmitted}`
  test(`${label} and closes owned child`, async t => {
    const { state, browserType } = fixture(t, { accepted, retransmitted })
    const value = await observe('webkit', browserType)
    assert.deepEqual(value, { browser: 'webkit', accepted, retransmitted })
    assert.ok(JSON.stringify(value).length < 128)
    assert.ok(state.denied)
    assert.deepEqual(state.closed, ['context', 'browser', 'server'])
    assert.ok(state.child.exitCode !== null || state.child.signalCode !== null)
    await assert.rejects(fetch(`${state.origin}/check`))
  })
}

for (const failure of ['connect', 'context', 'evaluate']) {
  test(`failure at ${failure} closes owned resources and emits no private detail`, async t => {
    const { state, browserType } = fixture(t, { failure })
    await assert.rejects(observe('chromium', browserType), error => error.message === 'browser_cookie_probe_failed')
    assert.ok(state.closed.includes('server'))
    assert.ok(state.child.exitCode !== null || state.child.signalCode !== null)
  })
}

test('cleanup failure kills and reaps only its owned child and refuses observation', async t => {
  const { state, browserType } = fixture(t, { cleanupFailure: 'server' })
  await assert.rejects(observe('firefox', browserType), FAILURE)
  assert.ok(state.closed.includes('kill'))
  assert.ok(state.child.exitCode !== null || state.child.signalCode !== null)
})

test('missing control, wrong secure attributes, duplicate or foreign cookies refuse', async t => {
  for (const mutateRows of [() => [], rows => rows.map(row => ({ ...row, httpOnly: false })),
    rows => [rows[0], rows[0]], rows => [...rows, { name: 'private' }]]) {
    const { browserType } = fixture(t, { mutateRows })
    await assert.rejects(observe('webkit', browserType), FAILURE)
  }
  const { browserType } = fixture(t, { accepted: false, retransmitted: true })
  await assert.rejects(observe('webkit', browserType), FAILURE)
})

test('bounded pending action rejects promptly', async () => {
  const began = performance.now()
  await assert.rejects(bounded(new Promise(() => {}), 25), FAILURE)
  assert.ok(performance.now() - began < 1000)
})

test('invalid CLI input prints only one fixed refusal without browser import', () => {
  const result = spawnSync(process.execPath, [new URL('./probe_browser_ci_secure_cookie.mjs', import.meta.url).pathname,
    'private-input-sentinel'], { env: { PATH: '/usr/bin:/bin' }, timeout: 5000, encoding: 'utf8' })
  assert.equal(result.status, 1)
  assert.equal(result.stdout, '')
  assert.equal(result.stderr, 'browser_cookie_probe_failed\n')
})
