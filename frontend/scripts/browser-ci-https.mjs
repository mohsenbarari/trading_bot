// Synthetic selected-job TLS only. Never load application configuration or
// production certificates, and never print certificates, keys or their hashes.
import fs from 'node:fs'
import path from 'node:path'
import { spawnSync } from 'node:child_process'
import { pathToFileURL } from 'node:url'

const FAILURE = 'browser_ci_https_fixture_invalid'
const FILES = ['ca.key', 'ca.crt', 'server.key', 'server.csr', 'server.crt', 'server.ext']
const fail = () => { throw new Error(FAILURE) }
const same = (a, b) => ['dev', 'ino', 'mode', 'uid', 'gid', 'nlink', 'size', 'mtimeMs', 'ctimeMs']
  .every(key => a[key] === b[key])

function directory(value, mode) {
  if (typeof value !== 'string' || !path.isAbsolute(value) || /[\r\n]/.test(value)) fail()
  const metadata = fs.lstatSync(value)
  if (!metadata.isDirectory() || metadata.uid !== process.getuid() || (metadata.mode & 0o022)
      || (mode !== undefined && (metadata.mode & 0o777) !== mode) || fs.realpathSync(value) !== value) fail()
  return metadata
}

function scope(env) {
  if (env.CI !== 'true' || env.GITHUB_REPOSITORY !== 'mohsenbarari/trading_bot') fail()
  directory(env.RUNNER_TEMP)
  if (typeof env.GITHUB_WORKSPACE !== 'string' || !path.isAbsolute(env.GITHUB_WORKSPACE)) fail()
}

function outside(root, workspace) {
  const relative = path.relative(workspace, root)
  return relative === '..' || relative.startsWith(`..${path.sep}`) || path.isAbsolute(relative)
}

export function prepareHttps(env) {
  try {
    scope(env)
    if (!outside(env.RUNNER_TEMP, fs.realpathSync(env.GITHUB_WORKSPACE))) fail()
    const root = fs.mkdtempSync(path.join(env.RUNNER_TEMP, 'browser-ci-https-'))
    fs.chmodSync(root, 0o700)
    const descriptors = new Map()
    try {
      for (const name of FILES) descriptors.set(name, fs.openSync(path.join(root, name),
        fs.constants.O_RDWR | fs.constants.O_CREAT | fs.constants.O_EXCL | fs.constants.O_NOFOLLOW, 0o600))
      fs.writeSync(descriptors.get('server.ext'),
        'basicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature,keyEncipherment\n'
        + 'extendedKeyUsage=serverAuth\nsubjectAltName=IP:127.0.0.1,DNS:localhost\n')
      const command = (args, keyName, outputName) => {
        const result = spawnSync('/usr/bin/openssl', args, {
          env: { PATH: '/usr/bin:/bin', LANG: 'C.UTF-8' }, timeout: 10000,
          stdio: ['ignore', 'ignore', 'ignore', descriptors.get(keyName), descriptors.get(outputName)],
        })
        if (result.error || result.status !== 0 || result.signal) fail()
      }
      command(['req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-sha256', '-days', '1',
        '-subj', '/CN=Synthetic browser CI CA', '-addext', 'basicConstraints=critical,CA:TRUE',
        '-addext', 'keyUsage=critical,keyCertSign,cRLSign', '-keyout', '/proc/self/fd/3', '-out', '/proc/self/fd/4'],
      'ca.key', 'ca.crt')
      command(['req', '-new', '-newkey', 'rsa:2048', '-nodes', '-sha256', '-subj', '/CN=localhost',
        '-keyout', '/proc/self/fd/3', '-out', '/proc/self/fd/4'], 'server.key', 'server.csr')
      command(['x509', '-req', '-sha256', '-days', '1', '-in', path.join(root, 'server.csr'),
        '-CA', path.join(root, 'ca.crt'), '-CAkey', path.join(root, 'ca.key'), '-set_serial', '1',
        '-extfile', path.join(root, 'server.ext'), '-out', '/proc/self/fd/4'], 'server.key', 'server.crt')
      for (const [name, descriptor] of descriptors) {
        const metadata = fs.fstatSync(descriptor)
        if (!metadata.isFile() || metadata.uid !== process.getuid() || metadata.nlink !== 1
            || (metadata.mode & 0o777) !== 0o600 || metadata.size < 1 || metadata.size > 32768
            || !same(metadata, fs.lstatSync(path.join(root, name)))) fail()
        fs.fsyncSync(descriptor)
      }
      return root
    } finally {
      for (const descriptor of descriptors.values()) fs.closeSync(descriptor)
    }
  } catch { fail() }
}

function readOwned(root, name) {
  const filename = path.join(root, name)
  const before = fs.lstatSync(filename)
  if (!before.isFile() || before.uid !== process.getuid() || before.nlink !== 1
      || (before.mode & 0o777) !== 0o600 || before.size < 1 || before.size > 32768) fail()
  const descriptor = fs.openSync(filename, fs.constants.O_RDONLY | fs.constants.O_NOFOLLOW)
  try {
    if (!same(before, fs.fstatSync(descriptor))) fail()
    const bytes = Buffer.alloc(before.size + 1)
    const length = fs.readSync(descriptor, bytes, 0, bytes.length, 0)
    if (length !== before.size || !same(before, fs.fstatSync(descriptor))
        || !same(before, fs.lstatSync(filename))) fail()
    return bytes.subarray(0, length)
  } finally { fs.closeSync(descriptor) }
}

export function previewHttps(env) {
  if (env.BROWSER_CI_PREVIEW_TLS !== '1') return undefined
  try {
    scope(env)
    const root = env.BROWSER_CI_TLS_ROOT
    const before = directory(root, 0o700)
    if (!root.startsWith(path.join(env.RUNNER_TEMP, 'browser-ci-https-'))
        || !outside(root, fs.realpathSync(env.GITHUB_WORKSPACE))) fail()
    const key = readOwned(root, 'server.key')
    const cert = readOwned(root, 'server.crt')
    if (!same(before, directory(root, 0o700))) fail()
    return { key, cert }
  } catch { fail() }
}

export function trustedHttpsProxy(proxy) {
  const bind = (request, incoming) => {
    if (incoming.socket?.encrypted !== true) {
      request.destroy(new Error(FAILURE))
      return
    }
    request.removeHeader('x-forwarded-proto')
    request.removeHeader('x-forwarded-host')
    request.setHeader('x-forwarded-proto', 'https')
  }
  proxy.on('proxyReq', bind)
  proxy.on('proxyReqWs', bind)
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  try {
    if (process.argv.length !== 3 || process.argv[2] !== 'prepare') fail()
    const env = process.env
    const root = prepareHttps(env)
    if (typeof env.GITHUB_ENV !== 'string' || !path.isAbsolute(env.GITHUB_ENV)) fail()
    const before = fs.lstatSync(env.GITHUB_ENV)
    if (!before.isFile() || before.uid !== process.getuid() || before.nlink !== 1 || (before.mode & 0o022)) fail()
    const descriptor = fs.openSync(env.GITHUB_ENV, fs.constants.O_WRONLY | fs.constants.O_APPEND | fs.constants.O_NOFOLLOW)
    try {
      if (!same(before, fs.fstatSync(descriptor))) fail()
      fs.writeSync(descriptor, `BROWSER_CI_PREVIEW_TLS=1\nBROWSER_CI_TLS_ROOT=${root}\n`
        + `BROWSER_CI_TLS_CA=${path.join(root, 'ca.crt')}\nNODE_EXTRA_CA_CERTS=${path.join(root, 'ca.crt')}\n`)
      fs.fsyncSync(descriptor)
    } finally { fs.closeSync(descriptor) }
    process.stdout.write('browser_ci_https_prepared\n')
  } catch {
    process.stderr.write(`${FAILURE}\n`)
    process.exitCode = 1
  }
}
