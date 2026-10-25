// Disposable synthetic CI trust only; never edit a personal browser database.
// Chromium M146+ uses ~/.local/share/pki/nssdb (the old ~/.pki DB takes precedence):
// https://chromium.googlesource.com/chromium/src/+/main/docs/linux/cert_management.md
// Playwright 1.59.1 supports PLAYWRIGHT_FIREFOX_POLICIES_JSON at browser startup:
// https://github.com/microsoft/playwright/blob/v1.59.1/browser_patches/firefox/preferences/playwright.cfg
// https://firefox-admin-docs.mozilla.org/reference/policies/certificates/
import fs from 'node:fs'
import path from 'node:path'
import { spawnSync } from 'node:child_process'
import { pathToFileURL } from 'node:url'

const FAILURE = 'browser_ci_trust_fixture_invalid'
const fail = () => { throw new Error(FAILURE) }
const identity = value => ['dev', 'ino', 'mode', 'uid', 'gid'].map(key => value[key]).join(':')
const same = (a, b) => ['dev', 'ino', 'mode', 'uid', 'gid', 'nlink', 'size', 'mtimeMs', 'ctimeMs']
  .every(key => a[key] === b[key])
const outside = (child, parent) => {
  const relative = path.relative(parent, child)
  return relative === '..' || relative.startsWith(`..${path.sep}`) || path.isAbsolute(relative)
}

function directory(value, mode) {
  if (typeof value !== 'string' || !path.isAbsolute(value) || /[\r\n]/.test(value)) fail()
  const before = fs.lstatSync(value)
  if (!before.isDirectory() || before.uid !== process.getuid() || (before.mode & 0o022)
      || (mode !== undefined && (before.mode & 0o777) !== mode) || fs.realpathSync(value) !== value) fail()
  return before
}

function scope(env) {
  if (env.CI !== 'true' || env.GITHUB_REPOSITORY !== 'mohsenbarari/trading_bot'
      || env.BROWSER_CI_PREVIEW_TLS !== '1') fail()
  directory(env.RUNNER_TEMP)
  if (!outside(env.RUNNER_TEMP, fs.realpathSync(env.GITHUB_WORKSPACE))) fail()
}

function writeOwned(filename, raw) {
  const descriptor = fs.openSync(filename, fs.constants.O_WRONLY | fs.constants.O_CREAT
    | fs.constants.O_EXCL | fs.constants.O_NOFOLLOW, 0o600)
  try {
    fs.writeFileSync(descriptor, raw)
    fs.fsyncSync(descriptor)
  } finally { fs.closeSync(descriptor) }
}

function readCa(env) {
  const root = env.BROWSER_CI_TLS_ROOT
  const beforeRoot = directory(root, 0o700)
  if (!root.startsWith(path.join(env.RUNNER_TEMP, 'browser-ci-https-'))
      || env.BROWSER_CI_TLS_CA !== path.join(root, 'ca.crt')) fail()
  const before = fs.lstatSync(env.BROWSER_CI_TLS_CA)
  if (!before.isFile() || before.uid !== process.getuid() || before.nlink !== 1
      || (before.mode & 0o777) !== 0o600 || before.size < 1 || before.size > 32768) fail()
  const descriptor = fs.openSync(env.BROWSER_CI_TLS_CA, fs.constants.O_RDONLY | fs.constants.O_NOFOLLOW)
  try {
    if (!same(before, fs.fstatSync(descriptor))) fail()
    const bytes = Buffer.alloc(before.size + 1)
    const count = fs.readSync(descriptor, bytes, 0, bytes.length, 0)
    if (count !== before.size || !same(before, fs.fstatSync(descriptor))
        || !same(before, fs.lstatSync(env.BROWSER_CI_TLS_CA)) || !same(beforeRoot, directory(root, 0o700))) fail()
    return bytes.subarray(0, count)
  } finally { fs.closeSync(descriptor) }
}

export function prepareBrowserTrust(env, run = spawnSync) {
  let root
  let createdIdentity
  try {
    scope(env)
    directory(env.HOME)
    // Browser installations remain in the original runner's public cache.
    // Changing HOME without this binding would hide the installed browsers.
    const cache = env.PLAYWRIGHT_BROWSERS_PATH || path.join(env.HOME, '.cache', 'ms-playwright')
    directory(cache)
    const ca = readCa(env)
    root = fs.mkdtempSync(path.join(env.RUNNER_TEMP, 'browser-ci-trust-'))
    fs.chmodSync(root, 0o700)
    createdIdentity = identity(directory(root, 0o700))
    const home = path.join(root, 'home')
    for (const relative of ['home', 'home/.local', 'home/.local/share', 'home/.local/share/pki',
      'home/.local/share/pki/nssdb', 'home/.config', 'home/tmp']) {
      fs.mkdirSync(path.join(root, relative), { mode: 0o700 })
    }
    const caPath = path.join(root, 'ca.crt')
    writeOwned(caPath, ca)
    const policyPath = path.join(root, 'firefox-policies.json')
    writeOwned(policyPath, JSON.stringify({ policies: { Certificates: { Install: [caPath] } } }))
    const database = path.join(home, '.local/share/pki/nssdb')
    const command = args => {
      const result = run('/usr/bin/certutil', args, {
        env: { PATH: '/usr/bin:/bin', LANG: 'C.UTF-8', HOME: home },
        timeout: 10000, stdio: 'ignore',
      })
      if (result.error || result.status !== 0 || result.signal) fail()
    }
    command(['-N', '--empty-password', '-d', `sql:${database}`])
    command(['-A', '-d', `sql:${database}`, '-n', 'synthetic-browser-ci', '-t', 'C,,', '-i', caPath])
    const names = fs.readdirSync(database).sort()
    if (JSON.stringify(names) !== JSON.stringify(['cert9.db', 'key4.db', 'pkcs11.txt'])) fail()
    for (const name of names) {
      const filename = path.join(database, name)
      const before = fs.lstatSync(filename)
      if (!before.isFile() || before.uid !== process.getuid() || before.nlink !== 1
          || (before.mode & 0o022) || before.size < 1 || before.size > 1024 * 1024) fail()
      fs.chmodSync(filename, 0o600)
    }
    if (identity(directory(root, 0o700)) !== createdIdentity) fail()
    return { root, home, cache, policyPath, rootIdentity: createdIdentity }
  } catch {
    if (root && createdIdentity) {
      try {
        if (identity(directory(root, 0o700)) === createdIdentity) fs.rmSync(root, { recursive: true, force: false })
      } catch { /* Refuse adoption; an uncertain owned scratch root remains for runner disposal. */ }
    }
    fail()
  }
}

export function cleanupBrowserTrust(env) {
  try {
    scope(env)
    const root = env.BROWSER_CI_TRUST_ROOT
    if (typeof root !== 'string' || path.dirname(root) !== env.RUNNER_TEMP
        || !path.basename(root).startsWith('browser-ci-trust-')
        || identity(directory(root, 0o700)) !== env.BROWSER_CI_TRUST_IDENTITY) fail()
    // rm does not follow child symlinks. Only this fresh, identity-bound root is removed.
    fs.rmSync(root, { recursive: true, force: false })
  } catch { fail() }
}

function exportPaths(env, value) {
  if (typeof env.GITHUB_ENV !== 'string' || !path.isAbsolute(env.GITHUB_ENV)) fail()
  const before = fs.lstatSync(env.GITHUB_ENV)
  if (!before.isFile() || before.uid !== process.getuid() || before.nlink !== 1 || (before.mode & 0o022)) fail()
  const descriptor = fs.openSync(env.GITHUB_ENV, fs.constants.O_WRONLY | fs.constants.O_APPEND | fs.constants.O_NOFOLLOW)
  try {
    if (!same(before, fs.fstatSync(descriptor))) fail()
    fs.writeSync(descriptor, `BROWSER_CI_TRUST_ROOT=${value.root}\nBROWSER_CI_TRUST_IDENTITY=${value.rootIdentity}\n`
      + `BROWSER_CI_BROWSER_HOME=${value.home}\nBROWSER_CI_PLAYWRIGHT_BROWSERS_PATH=${value.cache}\n`
      + `BROWSER_CI_FIREFOX_POLICIES_JSON=${value.policyPath}\n`)
    fs.fsyncSync(descriptor)
  } finally { fs.closeSync(descriptor) }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  try {
    if (process.argv.length !== 3) fail()
    if (process.argv[2] === 'prepare') exportPaths(process.env, prepareBrowserTrust(process.env))
    else if (process.argv[2] === 'cleanup') cleanupBrowserTrust(process.env)
    else fail()
    process.stdout.write('browser_ci_trust_complete\n')
  } catch {
    process.stderr.write(`${FAILURE}\n`)
    process.exitCode = 1
  }
}
