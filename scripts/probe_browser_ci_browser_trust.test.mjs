import assert from 'node:assert/strict'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { spawnSync } from 'node:child_process'
import test from 'node:test'
import { cleanupBrowserTrust, prepareBrowserTrust } from '../frontend/scripts/browser-ci-trust.mjs'

const FAILURE = /browser_ci_trust_fixture_invalid/
function fixture(t) {
  const base = fs.mkdtempSync(path.join(os.tmpdir(), 'owned-browser-trust-test-'))
  const temporary = path.join(base, 'runner-temp')
  const workspace = path.join(base, 'workspace')
  const home = path.join(base, 'original-home')
  for (const value of [temporary, workspace, home]) fs.mkdirSync(value, { mode: 0o700 })
  fs.mkdirSync(path.join(home, '.cache/ms-playwright'), { recursive: true, mode: 0o700 })
  const tls = fs.mkdtempSync(path.join(temporary, 'browser-ci-https-'))
  fs.writeFileSync(path.join(tls, 'ca.crt'), 'synthetic-public-ca-only', { flag: 'wx', mode: 0o600 })
  t.after(() => fs.rmSync(base, { recursive: true, force: true }))
  const env = { CI: 'true', GITHUB_REPOSITORY: 'mohsenbarari/trading_bot', RUNNER_TEMP: temporary,
    GITHUB_WORKSPACE: workspace, HOME: home, BROWSER_CI_PREVIEW_TLS: '1', BROWSER_CI_TLS_ROOT: tls,
    BROWSER_CI_TLS_CA: path.join(tls, 'ca.crt'), PRIVATE_SENTINEL: 'never-in-child-or-output' }
  const commands = []
  // This is an argv/filesystem unit fixture, not an NSS or actual browser proof.
  const run = (executable, args, options) => {
    commands.push({ executable, args, options })
    const database = args[args.indexOf('-d') + 1].slice(4)
    if (args[0] === '-N') {
      for (const name of ['cert9.db', 'key4.db', 'pkcs11.txt']) {
        fs.writeFileSync(path.join(database, name), 'owned-db-fixture', { flag: 'wx', mode: 0o600 })
      }
    }
    return { status: 0, signal: null }
  }
  return { base, env, run, commands }
}
function cleanupEnvironment(env, value) {
  return { ...env, BROWSER_CI_TRUST_ROOT: value.root, BROWSER_CI_TRUST_IDENTITY: value.rootIdentity }
}

test('Chromium uses a fresh NSS database and the original browser cache is preserved', t => {
  const { env, run, commands } = fixture(t)
  const original = JSON.stringify(env)
  fs.mkdirSync(path.join(env.HOME, '.pki'), { mode: 0o700 })
  fs.writeFileSync(path.join(env.HOME, '.pki/personal-sentinel'), 'unchanged')
  const value = prepareBrowserTrust(env, run)
  const database = path.join(value.home, '.local/share/pki/nssdb')
  assert.equal(value.cache, path.join(env.HOME, '.cache/ms-playwright'))
  assert.equal(fs.existsSync(path.join(value.home, '.pki')), false)
  assert.equal(fs.readFileSync(path.join(env.HOME, '.pki/personal-sentinel'), 'utf8'), 'unchanged')
  assert.deepEqual(commands.map(row => row.args), [
    ['-N', '--empty-password', '-d', `sql:${database}`],
    ['-A', '-d', `sql:${database}`, '-n', 'synthetic-browser-ci', '-t', 'C,,', '-i', path.join(value.root, 'ca.crt')],
  ])
  for (const row of commands) {
    assert.equal(row.executable, '/usr/bin/certutil')
    assert.deepEqual(row.options.env, { PATH: '/usr/bin:/bin', LANG: 'C.UTF-8', HOME: value.home })
    assert.equal(row.options.stdio, 'ignore')
    assert.equal(row.options.timeout, 10000)
  }
  for (const filename of ['ca.crt', 'firefox-policies.json', 'home/.local/share/pki/nssdb/cert9.db',
    'home/.local/share/pki/nssdb/key4.db', 'home/.local/share/pki/nssdb/pkcs11.txt']) {
    const metadata = fs.lstatSync(path.join(value.root, filename))
    assert.equal(metadata.mode & 0o777, 0o600)
    assert.equal(metadata.uid, process.getuid())
    assert.equal(metadata.nlink, 1)
  }
  assert.equal(JSON.stringify(env), original)
  assert.equal(fs.lstatSync(value.home).mode & 0o777, 0o700)
  assert.deepEqual(fs.readdirSync(env.GITHUB_WORKSPACE), [])
})

test('Firefox policy installs only the owned synthetic CA without disabling validation', t => {
  const { env, run } = fixture(t)
  const value = prepareBrowserTrust(env, run)
  assert.deepEqual(JSON.parse(fs.readFileSync(value.policyPath, 'utf8')),
    { policies: { Certificates: { Install: [path.join(value.root, 'ca.crt')] } } })
  assert.equal(fs.readFileSync(path.join(value.root, 'ca.crt'), 'utf8'), 'synthetic-public-ca-only')
  const source = fs.readFileSync(new URL('../frontend/scripts/browser-ci-trust.mjs', import.meta.url), 'utf8')
  for (const forbidden of ['ignoreHTTPSErrors', 'NODE_TLS_REJECT_UNAUTHORIZED', 'ImportEnterpriseRoots',
    'security.enterprise_roots.enabled', 'security.cert_pinning']) assert.equal(source.includes(forbidden), false)
})

test('only an explicit existing canonical browser cache can override the original default', t => {
  const { env, run, base } = fixture(t)
  const cache = path.join(base, 'explicit-browser-cache')
  fs.mkdirSync(cache, { mode: 0o700 })
  assert.equal(prepareBrowserTrust({ ...env, PLAYWRIGHT_BROWSERS_PATH: cache }, run).cache, cache)
  for (const value of ['relative-cache', '0', path.join(base, 'missing-cache')]) {
    assert.throws(() => prepareBrowserTrust({ ...env, PLAYWRIGHT_BROWSERS_PATH: value }, run), FAILURE)
  }
})

test('ordinary, foreign repository and workspace-root requests refuse without any trust command', t => {
  const { env, run, commands } = fixture(t)
  for (const patch of [{ CI: 'false' }, { GITHUB_REPOSITORY: 'foreign/repo' },
    { BROWSER_CI_PREVIEW_TLS: undefined }, { RUNNER_TEMP: env.GITHUB_WORKSPACE }]) {
    assert.throws(() => prepareBrowserTrust({ ...env, ...patch }, run), FAILURE)
  }
  assert.deepEqual(commands, [])
})

test('wrong CA mode is refused before payload open and alias files are not followed', t => {
  const { env, run, commands } = fixture(t)
  fs.chmodSync(env.BROWSER_CI_TLS_CA, 0o644)
  const original = fs.openSync
  let opened = false
  fs.openSync = function (filename, ...args) {
    if (filename === env.BROWSER_CI_TLS_CA) opened = true
    return original.call(this, filename, ...args)
  }
  try { assert.throws(() => prepareBrowserTrust(env, run), FAILURE) } finally { fs.openSync = original }
  assert.equal(opened, false)
  fs.chmodSync(env.BROWSER_CI_TLS_CA, 0o600)
  fs.renameSync(env.BROWSER_CI_TLS_CA, `${env.BROWSER_CI_TLS_CA}.retained`)
  fs.symlinkSync(`${env.BROWSER_CI_TLS_CA}.retained`, env.BROWSER_CI_TLS_CA)
  assert.throws(() => prepareBrowserTrust(env, run), FAILURE)
  assert.deepEqual(commands, [])
})

test('same-byte CA replacement between metadata admission and open refuses before certutil', t => {
  const { env, run, commands } = fixture(t)
  const replacement = `${env.BROWSER_CI_TLS_CA}.replacement`
  fs.copyFileSync(env.BROWSER_CI_TLS_CA, replacement, fs.constants.COPYFILE_EXCL)
  const original = fs.openSync
  fs.openSync = function (filename, ...args) {
    if (filename === env.BROWSER_CI_TLS_CA) fs.renameSync(replacement, filename)
    return original.call(this, filename, ...args)
  }
  try { assert.throws(() => prepareBrowserTrust(env, run), FAILURE) } finally { fs.openSync = original }
  assert.deepEqual(commands, [])
})

test('failed or timed-out NSS commands refuse and remove only their own unstarted scratch root', t => {
  const { env } = fixture(t)
  for (const result of [{ status: 1 }, { status: null, signal: 'SIGTERM' }, { error: new Error('PRIVATE_SENTINEL') }]) {
    assert.throws(() => prepareBrowserTrust(env, () => result), FAILURE)
    assert.deepEqual(fs.readdirSync(env.RUNNER_TEMP), [path.basename(env.BROWSER_CI_TLS_ROOT)])
  }
})

test('foreign or oversized NSS files fail closed without changing the original home', t => {
  const { env, run } = fixture(t)
  for (const mutate of [database => fs.symlinkSync(env.BROWSER_CI_TLS_CA, path.join(database, 'foreign')),
    database => fs.truncateSync(path.join(database, 'cert9.db'), 1024 * 1024 + 1)]) {
    assert.throws(() => prepareBrowserTrust(env, (executable, args, options) => {
      const result = run(executable, args, options)
      if (args[0] === '-A') mutate(args[args.indexOf('-d') + 1].slice(4))
      return result
    }), FAILURE)
    assert.deepEqual(fs.readdirSync(env.HOME), ['.cache'])
  }
})

test('cleanup is identity-bound and never follows child symlinks into a foreign directory', t => {
  const { env, run, base } = fixture(t)
  const value = prepareBrowserTrust(env, run)
  const foreign = path.join(base, 'foreign')
  fs.mkdirSync(foreign, { mode: 0o700 })
  fs.writeFileSync(path.join(foreign, 'sentinel'), 'retained')
  fs.symlinkSync(foreign, path.join(value.home, 'foreign-alias'))
  assert.throws(() => cleanupBrowserTrust({ ...cleanupEnvironment(env, value), BROWSER_CI_TRUST_ROOT: foreign }), FAILURE)
  assert.throws(() => cleanupBrowserTrust({ ...cleanupEnvironment(env, value), BROWSER_CI_TRUST_IDENTITY: 'wrong' }), FAILURE)
  cleanupBrowserTrust(cleanupEnvironment(env, value))
  assert.equal(fs.existsSync(value.root), false)
  assert.equal(fs.readFileSync(path.join(foreign, 'sentinel'), 'utf8'), 'retained')
  assert.equal(fs.existsSync(env.BROWSER_CI_TLS_ROOT), true)
})

test('same-name replaced cleanup roots are retained instead of adopted', t => {
  const { env, run } = fixture(t)
  const value = prepareBrowserTrust(env, run)
  fs.renameSync(value.root, `${value.root}.retained`)
  fs.mkdirSync(value.root, { mode: 0o700 })
  assert.throws(() => cleanupBrowserTrust(cleanupEnvironment(env, value)), FAILURE)
  assert.equal(fs.existsSync(value.root), true)
  assert.equal(fs.existsSync(`${value.root}.retained`), true)
})

test('real owned cleanup CLI emits only a fixed status and no certificate material or caller environment', t => {
  const { env, run } = fixture(t)
  const value = prepareBrowserTrust(env, run)
  const script = new URL('../frontend/scripts/browser-ci-trust.mjs', import.meta.url).pathname
  const result = spawnSync(process.execPath, [script, 'cleanup'], { env: { PATH: '/usr/bin:/bin',
    ...cleanupEnvironment(env, value) }, encoding: 'utf8', timeout: 3000, maxBuffer: 1024 })
  assert.equal(result.status, 0)
  assert.equal(result.stdout, 'browser_ci_trust_complete\n')
  assert.equal(result.stderr, '')
  assert.equal(fs.existsSync(value.root), false)
  const refused = spawnSync(process.execPath, [script, 'PRIVATE_SENTINEL'], { env: { PATH: '/usr/bin:/bin', ...env },
    encoding: 'utf8', timeout: 3000, maxBuffer: 1024 })
  assert.equal(refused.status, 1)
  assert.equal(refused.stdout, '')
  assert.equal(refused.stderr, 'browser_ci_trust_fixture_invalid\n')
})
