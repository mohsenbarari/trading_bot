#!/usr/bin/env node
'use strict';

/**
 * Stage 4 Daily Core local evidence capture.
 *
 * Canonical inputs are byte/hash validated before Chromium starts. HTTP(S) is
 * blocked, the DOM and semantic audit are compared before/after capture, PNGs
 * are remeasured, and only an exact validated directory is promoted.
 */

const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const zlib = require('node:zlib');
const { pathToFileURL } = require('node:url');

const CONTRACT_DIR = __dirname;
const REPO_ROOT = path.resolve(CONTRACT_DIR, '..', '..');
const ASSETS_DIR = path.join(CONTRACT_DIR, 'assets');
const FIGMA_DIR = path.join(ASSETS_DIR, 'figma');
const BROWSER_DIR = path.join(ASSETS_DIR, 'browser-evidence');
const GATES_DIR = path.join(ASSETS_DIR, 'gates');
const PUBLISHED_DIR = path.join(ASSETS_DIR, 'local-evidence');
const HTML_FILE = 'stage4-daily-core-evidence.html';
const HTML_PATH = path.join(CONTRACT_DIR, HTML_FILE);
const FIGMA_MANIFEST_PATH = path.join(CONTRACT_DIR, 'FIGMA_SNAPSHOT_MANIFEST.json');
const EVIDENCE_MANIFEST_PATH = path.join(CONTRACT_DIR, 'EVIDENCE_MANIFEST.json');
const METRICS_FILE = 'local-stage4-daily-core-validation-metrics.json';

const BASE_COMMIT = '9dfa961000832c830729ce67e8a54357915c716a';
const BASE_TREE = '1540c2534d8052a3a8cfcffcdc2f65e4b85fc874';
const IMPLEMENTATION_COMMIT = '007f94d170cb02cd69911d9e1f122b83fbacd535';
const IMPLEMENTATION_TREE = '807a01c76c93489ccce1e5b72cea9c214fd52d31';
const PATHSET_SHA256 = '25a5773b2e3ca1f6e45bbf48800dcac4ce3cd8e8125f1913fee674529720739f';
const PATH_CONTENT_SHA256 = '517ae0b1d3d630f6fa086cdc208905fabb9a532035cec539f61f9cd5f67af35e';
const FIGMA_CAPTURE_AGGREGATE = '46e329154d226cc0ed6fb302b4c33b0215b29280a25d9d8abccfe1e6a266774a';
const FIGMA_INPUT_AGGREGATE = '1e53902f6abc88042874c5d71baf94ad319170de8e83b6bfde34e0823e9ff1fc';
const BROWSER_RUN_ID = 'uiux-stage4-browser-20260809T180340666Z';
const BROWSER_METRICS_SHA256 = '83445d91bd78fd0903f49833a5b72c5d49345d517d9e5ae05e2fdd42954cd01f';
const BROWSER_BINDING_ARTIFACT_SHA256 = '04f5c126cae096c0de3b6f738108aae18f239aae0310d119b0bb870e6f9e856b';
const BROWSER_HARNESS_SHA256 = '66ddbc5683f178633d37673ee6299d84863b29cdbafa66d9d764862622cb3a34';
const BROWSER_SOURCE_SHA256 = '1f8858264f0c52479c227bb84822a6c109f9b4fadb968500df596126acf099bf';
const BROWSER_FULL_AGGREGATE = 'ad9903fad3550eea196dbc8564d083a81dbbd608cd8153281ef04682d9322bef';
const GATES_FULL_AGGREGATE = '8a7b4ad4b7a2bf93e9a25c6eabcb929b8c85f2b96b82113e928a70e576391b0d';
const GATES_SUMMARY_SHA256 = 'f5f1b32ef85d010aa2134b3531f628ac941f91f9dc4adfb58ee46cfa39a86ac2';
const GATES_MANIFEST_SHA256 = 'ae5da32b7eec554cb25c3e167f9e17b80d63d69dd9bde812cc6fc89c817907af';
const FIGMA_AUDIT_SHA256 = '55601f8cb0db38a55835b936b1efe9163f2356ca8640f555116c06017cb62772';
const HOME_SHA256 = 'f25c01dac38db208517047ffc0f2458e2c89868e988a6d7f68749221db106860';
const MARKET_SHA256 = '162e9e618684a24f3db3298eb8ff2c62498b18753cd4e0b6d6b97650d0202058';
const MESSENGER_SHA256 = 'f66debf9809180d97b2bac98f5195ba24200d3b61b0d8e0e5cd423a8a7b97248';
const ADMIN_MESSAGES_SHA256 = '5572589b83a8a07776d5b983777a14a91e2104f9577fa76960df5a54562a431a';
const TRADING_SETTINGS_SHA256 = '509dd32235e1cb98aa164940cf7722604f16b6518f7387699554bf3a828ecfaa';

const EXPECTED_FIGMA_FILES = Object.freeze([
  'stage4-figma-account.png',
  'stage4-figma-canonical-mobile.png',
  'stage4-figma-component-matrix.png',
  'stage4-figma-contract.png',
  'stage4-figma-desktop-operations.png',
  'stage4-figma-direct-audit.json',
  'stage4-figma-home.png',
  'stage4-figma-notifications.png',
  'stage4-figma-operations.png',
  'stage4-figma-recovery-mutation.png',
  'stage4-figma-role-authority.png',
  'stage4-figma-root.png',
  'stage4-figma-security.png',
  'stage4-figma-storage.png',
]);

const EXPECTED_BROWSER_FILES = Object.freeze([
  'stage4-account-mobile-390-scroll-001.png',
  'stage4-account-mobile-390.png',
  'stage4-browser-acceptance-harness.mjs',
  'stage4-browser-acceptance-metrics.json',
  'stage4-final-source-binding.json',
  'stage4-home-desktop-1440.png',
  'stage4-home-mobile-390.png',
  'stage4-home-pwa-ready-mobile-390.png',
  'stage4-layer-coexistence-mobile-390.png',
  'stage4-notifications-desktop-1440.png',
  'stage4-notifications-mobile-390-scroll-001.png',
  'stage4-notifications-mobile-390.png',
  'stage4-operations-desktop-1440-scroll-001.png',
  'stage4-operations-desktop-1440.png',
  'stage4-operations-mobile-390-scroll-001.png',
  'stage4-operations-mobile-390.png',
  'stage4-private-catalog-mobile-390.png',
  'stage4-protected-market-mobile-390-scroll-001.png',
  'stage4-protected-market-mobile-390.png',
  'stage4-protected-messenger-mobile-390-scroll-001.png',
  'stage4-protected-messenger-mobile-390.png',
  'stage4-security-mobile-390-scroll-001.png',
  'stage4-security-mobile-390.png',
  'stage4-storage-mobile-390-scroll-001.png',
  'stage4-storage-mobile-390.png',
]);

const EXPECTED_GATE_FILES = Object.freeze([
  'stage4-eslint-base-final.json',
  'stage4-eslint-current-final.json',
  'stage4-eslint-delta-final.json',
  'stage4-final-backend.log',
  'stage4-final-build.log',
  'stage4-final-diff-check.log',
  'stage4-final-gate-manifest.md',
  'stage4-final-gates-summary.json',
  'stage4-final-guard-tests.json',
  'stage4-final-guard-ui.log',
  'stage4-final-protected-list.log',
  'stage4-final-stability.json',
  'stage4-final-vitest.json',
  'stage4-final-vue-tsc.log',
  'stage4-implementation-git-binding.json',
  'stage4-prettier-added-final.txt',
  'stage4-prettier-base-final.txt',
  'stage4-prettier-current-final.txt',
  'stage4-prettier-inherited-final.txt',
  'stage4-prettier-removed-final.txt',
]);

const EXPECTED_ASSERTIONS = Object.freeze([
  'claim-boundary-local-not-stage-or-sites',
  'implementation-commit-tree-parent-exact',
  'git-sixtyseven-path-binding-exact',
  'figma-authored-snapshot-identity-exact',
  'figma-thirteen-exports-hash-bound',
  'figma-audit-six-six-sixtysix-zero-one',
  'canonical-routes-six-redirects-two',
  'role-authority-matrix-truthful',
  'accountant-destinations-and-owner-actions-exact',
  'security-storage-route-exclusive-receipt-bound',
  'notifications-recovery-route-safety-exact',
  'push-nine-state-explicit-permission',
  'browser-run-49-of-49-promotable',
  'browser-twentytwo-screenshots-hash-bound',
  'browser-eight-widths-nine-suites',
  'browser-expected-failures-and-zero-unexpected',
  'browser-source-398-pre-post-identical',
  'vitest-34-450-passed',
  'guards-3-55-and-ui-passed',
  'backend-11-69-passed-with-caveat',
  'type-build-pwa-passed',
  'eslint-delta-zero-inherited-disclosed',
  'prettier-delta-zero-inherited-disclosed',
  'home-market-six-4553-hash-exact',
  'market-19-and-messenger-85-hashes-exact',
  'protected-seven-routes-admin-hashes-exact',
]);

const CAPTURE_SPECS = Object.freeze([
  { selector: '#stage4-overview', file: 'local-stage4-overview.png' },
  { selector: '#stage4-figma-proof', file: 'local-stage4-figma-proof.png' },
  { selector: '#stage4-route-authority', file: 'local-stage4-route-authority.png' },
  { selector: '#stage4-browser-proof', file: 'local-stage4-browser-proof.png' },
  { selector: '#stage4-recovery-mutations', file: 'local-stage4-recovery-mutations.png' },
  { selector: '#stage4-gates-protected', file: 'local-stage4-gates-protected.png' },
]);
const EXPECTED_OUTPUT_FILES = Object.freeze([...CAPTURE_SPECS.map(({ file }) => file), METRICS_FILE].sort());

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

function exact(actual, expected) {
  return JSON.stringify(actual) === JSON.stringify(expected);
}

function sha256Buffer(buffer) {
  return crypto.createHash('sha256').update(buffer).digest('hex');
}

function sha1Buffer(buffer) {
  return crypto.createHash('sha1').update(buffer).digest('hex');
}

function sha256File(filePath) {
  return sha256Buffer(fs.readFileSync(filePath));
}

function stableJson(value) {
  if (Array.isArray(value)) return `[${value.map(stableJson).join(',')}]`;
  if (value && typeof value === 'object') return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${stableJson(value[key])}`).join(',')}}`;
  return JSON.stringify(value);
}

function hashValue(value) {
  return sha256Buffer(Buffer.from(stableJson(value)));
}

function sortedEntries(directory) {
  return fs.readdirSync(directory, { withFileTypes: true }).map((entry) => entry.name).sort();
}

function readJson(filePath) {
  return JSON.parse(fs.readFileSync(filePath, 'utf8'));
}

function fileRecord(filePath, relativePath = path.relative(CONTRACT_DIR, filePath)) {
  const buffer = fs.readFileSync(filePath);
  return { path: relativePath.split(path.sep).join('/'), bytes: buffer.length, sha256: sha256Buffer(buffer) };
}

function recordsAggregate(records) {
  return sha256Buffer(Buffer.from(JSON.stringify(records)));
}

function pngDimensions(filePath) {
  const buffer = fs.readFileSync(filePath);
  assert(buffer.length >= 24 && buffer.subarray(0, 8).toString('hex') === '89504e470d0a1a0a', `Invalid PNG signature: ${filePath}`);
  assert(buffer.subarray(12, 16).toString('ascii') === 'IHDR', `Missing PNG IHDR: ${filePath}`);
  return { width: buffer.readUInt32BE(16), height: buffer.readUInt32BE(20) };
}

function pngVisualStats(filePath) {
  const buffer = fs.readFileSync(filePath);
  const width = buffer.readUInt32BE(16);
  const height = buffer.readUInt32BE(20);
  const bitDepth = buffer[24];
  const colorType = buffer[25];
  assert(bitDepth === 8 && (colorType === 2 || colorType === 6), `Unsupported PNG format: ${filePath}`);
  const bytesPerPixel = colorType === 2 ? 3 : 4;
  const idat = [];
  for (let offset = 8; offset + 12 <= buffer.length;) {
    const length = buffer.readUInt32BE(offset);
    const type = buffer.subarray(offset + 4, offset + 8).toString('ascii');
    if (type === 'IDAT') idat.push(buffer.subarray(offset + 8, offset + 8 + length));
    offset += 12 + length;
    if (type === 'IEND') break;
  }
  const raw = zlib.inflateSync(Buffer.concat(idat));
  const stride = width * bytesPerPixel;
  assert(raw.length === height * (stride + 1), `Unexpected PNG scanline size: ${filePath}`);
  const colors = new Set();
  let changedPixelCount = 0;
  let firstColor = null;
  let previous = Buffer.alloc(stride);
  let cursor = 0;
  const paeth = (a, b, c) => {
    const p = a + b - c;
    const pa = Math.abs(p - a);
    const pb = Math.abs(p - b);
    const pc = Math.abs(p - c);
    return pa <= pb && pa <= pc ? a : pb <= pc ? b : c;
  };
  for (let y = 0; y < height; y += 1) {
    const filter = raw[cursor++];
    const row = Buffer.allocUnsafe(stride);
    for (let x = 0; x < stride; x += 1) {
      const encoded = raw[cursor + x];
      const left = x >= bytesPerPixel ? row[x - bytesPerPixel] : 0;
      const up = previous[x];
      const upperLeft = x >= bytesPerPixel ? previous[x - bytesPerPixel] : 0;
      if (filter === 0) row[x] = encoded;
      else if (filter === 1) row[x] = (encoded + left) & 255;
      else if (filter === 2) row[x] = (encoded + up) & 255;
      else if (filter === 3) row[x] = (encoded + Math.floor((left + up) / 2)) & 255;
      else if (filter === 4) row[x] = (encoded + paeth(left, up, upperLeft)) & 255;
      else throw new Error(`Unsupported PNG filter ${filter}: ${filePath}`);
    }
    cursor += stride;
    for (let x = 0; x < stride; x += bytesPerPixel) {
      const color = `${row[x]},${row[x + 1]},${row[x + 2]}`;
      if (firstColor === null) firstColor = color;
      if (color !== firstColor) changedPixelCount += 1;
      if (colors.size <= 512) colors.add(color);
    }
    previous = row;
  }
  return { uniqueColorCount: colors.size, changedPixelCount };
}

function validatePng(filePath, expected = {}) {
  assert(fs.existsSync(filePath) && fs.statSync(filePath).isFile(), `PNG missing: ${filePath}`);
  const record = fileRecord(filePath, expected.path || path.basename(filePath));
  const dimensions = pngDimensions(filePath);
  const visualStats = pngVisualStats(filePath);
  if (expected.bytes !== undefined) assert(record.bytes === expected.bytes, `PNG byte drift: ${filePath}`);
  if (expected.sha256) assert(record.sha256 === expected.sha256, `PNG hash drift: ${filePath}`);
  if (expected.width !== undefined) assert(dimensions.width === expected.width, `PNG width drift: ${filePath}`);
  if (expected.height !== undefined) assert(dimensions.height === expected.height, `PNG height drift: ${filePath}`);
  assert(visualStats.uniqueColorCount >= 8 && visualStats.changedPixelCount >= 100, `PNG blank or degenerate: ${filePath}`);
  return { ...record, ...dimensions, visualStats, validated: true };
}

function resolveGitObjectBinding() {
  const dotGit = path.join(REPO_ROOT, '.git');
  const dotGitStat = fs.statSync(dotGit);
  const gitDir = dotGitStat.isDirectory()
    ? dotGit
    : path.resolve(REPO_ROOT, fs.readFileSync(dotGit, 'utf8').trim().replace(/^gitdir:\s*/, ''));
  const commonDirFile = path.join(gitDir, 'commondir');
  const commonDir = fs.existsSync(commonDirFile)
    ? path.resolve(gitDir, fs.readFileSync(commonDirFile, 'utf8').trim())
    : gitDir;
  const head = fs.readFileSync(path.join(gitDir, 'HEAD'), 'utf8').trim();
  let commit = head;
  let ref = null;
  if (head.startsWith('ref: ')) {
    ref = head.slice(5);
    const refPath = path.join(commonDir, ref);
    assert(fs.existsSync(refPath), `Git ref is not loose: ${ref}`);
    commit = fs.readFileSync(refPath, 'utf8').trim();
  }
  assert(commit === IMPLEMENTATION_COMMIT, `Git HEAD mismatch: ${commit}`);
  const loosePath = path.join(commonDir, 'objects', commit.slice(0, 2), commit.slice(2));
  assert(fs.existsSync(loosePath), `Implementation commit loose object unavailable: ${loosePath}`);
  const inflated = zlib.inflateSync(fs.readFileSync(loosePath));
  assert(sha1Buffer(inflated) === commit, 'Git commit object SHA-1 mismatch');
  const nul = inflated.indexOf(0);
  const header = inflated.subarray(0, nul).toString('utf8');
  const body = inflated.subarray(nul + 1).toString('utf8');
  assert(header === `commit ${Buffer.byteLength(body)}`, `Git commit object header mismatch: ${header}`);
  const tree = body.match(/^tree ([0-9a-f]{40})$/m)?.[1];
  const parent = body.match(/^parent ([0-9a-f]{40})$/m)?.[1];
  assert(tree === IMPLEMENTATION_TREE && parent === BASE_COMMIT, 'Git tree/parent mismatch');
  return { gitDir, commonDir, ref, commit, tree, parent, looseObjectSha1Verified: true };
}

function validateFigmaInputs() {
  assert(exact(sortedEntries(FIGMA_DIR), EXPECTED_FIGMA_FILES), 'Figma exact file set mismatch');
  const manifest = readJson(FIGMA_MANIFEST_PATH);
  assert(manifest.schemaVersion === 1 && manifest.stage === 4 && manifest.status === 'authored_snapshot_hash_bound', 'Figma manifest header mismatch');
  assert(manifest.fileKey === 'z8jgJxST4O2APzWnlyP9gv' && manifest.pageId === '283:18' && manifest.rootNodeId === '283:19' && manifest.provenanceNodeId === '291:554', 'Figma identity/provenance mismatch');
  assert(manifest.stage4FreezeCreated === true && manifest.figmaMutationPerformed === true, 'Figma authored freeze boundary mismatch');
  assert(manifest.implementationCommit === IMPLEMENTATION_COMMIT && manifest.implementationTree === IMPLEMENTATION_TREE && manifest.browserSourceBindingSha256 === BROWSER_SOURCE_SHA256, 'Figma source binding mismatch');
  assert(manifest.captures.length === 13 && manifest.captureAggregate.count === 13 && manifest.captureAggregate.bytes === 1118391, 'Figma capture cardinality mismatch');
  const projection = manifest.captures.map(({ nodeId, path: filePath, width, height, bytes, sha256 }) => ({ nodeId, path: filePath, width, height, bytes, sha256 }));
  assert(recordsAggregate(projection) === FIGMA_CAPTURE_AGGREGATE && manifest.captureAggregate.sha256 === FIGMA_CAPTURE_AGGREGATE, 'Figma capture aggregate mismatch');
  const captures = manifest.captures.map((capture) => validatePng(path.join(CONTRACT_DIR, capture.path), capture));
  const auditRecord = fileRecord(path.join(FIGMA_DIR, 'stage4-figma-direct-audit.json'), 'assets/figma/stage4-figma-direct-audit.json');
  assert(auditRecord.bytes === 2030 && auditRecord.sha256 === FIGMA_AUDIT_SHA256, 'Figma audit byte/hash mismatch');
  const audit = readJson(path.join(FIGMA_DIR, 'stage4-figma-direct-audit.json'));
  assert(audit.status === 'passed' && audit.fileKey === manifest.fileKey && audit.page?.id === manifest.pageId && audit.root?.id === manifest.rootNodeId, 'Figma audit identity mismatch');
  assert(audit.sourceBinding?.implementationCommit === IMPLEMENTATION_COMMIT && audit.sourceBinding?.implementationTree === IMPLEMENTATION_TREE && audit.sourceBinding?.browserSourceBinding === BROWSER_SOURCE_SHA256 && audit.sourceBinding?.provenanceNodeId === '291:554', 'Figma audit provenance mismatch');
  assert(audit.sections?.unique === 6 && audit.canonicalScreens?.unique === 6 && audit.linkedInstances?.count === 66 && audit.linkedInstances?.detachedCount === 0 && audit.imageNodes?.length === 1 && audit.errors?.length === 0, 'Figma audit structural mismatch');
  const inputRecords = EXPECTED_FIGMA_FILES.map((name) => fileRecord(path.join(FIGMA_DIR, name), `assets/figma/${name}`));
  assert(inputRecords.reduce((sum, item) => sum + item.bytes, 0) === 1120421 && recordsAggregate(inputRecords) === FIGMA_INPUT_AGGREGATE, 'Figma full input aggregate mismatch');
  return { manifest: fileRecord(FIGMA_MANIFEST_PATH, 'FIGMA_SNAPSHOT_MANIFEST.json'), captures, audit: auditRecord, auditFacts: audit, inputRecords, captureAggregateSha256: FIGMA_CAPTURE_AGGREGATE, inputAggregateSha256: FIGMA_INPUT_AGGREGATE };
}

function expectedBrowserDimensions(name) {
  if (name === 'stage4-private-catalog-mobile-390.png') return { width: 390, height: 7463 };
  if (name.includes('-desktop-1440')) return { width: 1440, height: 900 };
  return { width: 390, height: 844 };
}

function validateBrowserInputs() {
  assert(exact(sortedEntries(BROWSER_DIR), EXPECTED_BROWSER_FILES), 'Browser exact file set mismatch');
  const metricsPath = path.join(BROWSER_DIR, 'stage4-browser-acceptance-metrics.json');
  const bindingPath = path.join(BROWSER_DIR, 'stage4-final-source-binding.json');
  const harnessPath = path.join(BROWSER_DIR, 'stage4-browser-acceptance-harness.mjs');
  assert(sha256File(metricsPath) === BROWSER_METRICS_SHA256 && fs.statSync(metricsPath).size === 790083, 'Browser metrics byte/hash mismatch');
  assert(sha256File(bindingPath) === BROWSER_BINDING_ARTIFACT_SHA256 && fs.statSync(bindingPath).size === 90454, 'Browser binding byte/hash mismatch');
  assert(sha256File(harnessPath) === BROWSER_HARNESS_SHA256 && fs.statSync(harnessPath).size === 198607, 'Browser harness byte/hash mismatch');
  const metrics = readJson(metricsPath);
  const binding = readJson(bindingPath);
  assert(metrics.schemaVersion === 1 && metrics.stage === 4 && metrics.status === 'passed' && metrics.promotable === true && metrics.runId === BROWSER_RUN_ID && metrics.failure === null, 'Browser metrics header mismatch');
  assert(metrics.assertionSummary?.total === 49 && metrics.assertionSummary?.passed === 49 && metrics.assertionSummary?.failed === 0 && metrics.assertions?.every((item) => item.passed === true), 'Browser assertion mismatch');
  assert(metrics.selectedSuites?.length === 9 && metrics.requiredViewports?.map(({ width }) => width).join(',') === '360,375,390,414,430,768,1024,1440', 'Browser suite/viewport mismatch');
  assert(metrics.screenshots?.length === 22, 'Browser screenshot count mismatch');
  const screenshotRecords = metrics.screenshots.map(({ file, bytes, sha256 }) => ({ path: file, bytes, sha256 })).sort((a, b) => a.path.localeCompare(b.path));
  const expectedPngNames = EXPECTED_BROWSER_FILES.filter((name) => name.endsWith('.png'));
  assert(exact(screenshotRecords.map(({ path: filePath }) => filePath), expectedPngNames), 'Browser screenshot registry mismatch');
  const captures = screenshotRecords.map((record) => validatePng(path.join(BROWSER_DIR, record.path), { ...record, ...expectedBrowserDimensions(record.path) }));
  const diagnostics = metrics.diagnostics;
  assert(diagnostics.consoleErrors?.length === 17 && diagnostics.expectedConsoleErrors?.length === 17 && diagnostics.unexpectedConsoleErrors?.length === 0, 'Browser console diagnostic disposition mismatch');
  assert(diagnostics.httpFailures?.length === 16 && diagnostics.expectedHttpFailures?.length === 16 && diagnostics.unexpectedHttpFailures?.length === 0, 'Browser HTTP diagnostic disposition mismatch');
  assert(diagnostics.pageErrors?.length === 0 && diagnostics.unexpectedRequestFailures?.length === 0 && diagnostics.blockedExternalRequests?.length === 0 && diagnostics.unexpectedApiRequests?.length === 0 && diagnostics.unexpectedWebSockets?.length === 0, 'Browser unexpected diagnostics mismatch');
  assert(metrics.sourceBinding?.sha256 === BROWSER_SOURCE_SHA256 && metrics.sourceBinding?.identical === true && metrics.sourceBinding?.pre?.length === 398 && exact(metrics.sourceBinding.pre, metrics.sourceBinding.post), 'Browser source binding mismatch');
  assert(metrics.protectedBinding?.identical === true && exact(metrics.protectedBinding.pre, metrics.protectedBinding.post), 'Browser protected pre/post mismatch');
  assert(binding.status === 'passed' && binding.promotable === true && binding.runId === BROWSER_RUN_ID && binding.sourceBindingSha256 === BROWSER_SOURCE_SHA256 && binding.sourceIdentical === true && binding.protectedIdentical === true && binding.harnessSha256 === BROWSER_HARNESS_SHA256 && binding.source?.length === 398, 'Browser final binding mismatch');
  assert(exact(binding.source, metrics.sourceBinding.pre), 'Browser metrics/final source list mismatch');
  for (const source of binding.source) {
    const absolutePath = path.join(REPO_ROOT, source.path);
    assert(fs.existsSync(absolutePath) && fs.statSync(absolutePath).isFile(), `Browser-bound source missing: ${source.path}`);
    assert(fs.statSync(absolutePath).size === source.bytes && sha256File(absolutePath) === source.sha256, `Browser-bound source drift: ${source.path}`);
  }
  const fullRecords = EXPECTED_BROWSER_FILES.map((name) => fileRecord(path.join(BROWSER_DIR, name), name));
  assert(fullRecords.reduce((sum, item) => sum + item.bytes, 0) === 2806569 && recordsAggregate(fullRecords) === BROWSER_FULL_AGGREGATE, 'Browser full aggregate mismatch');
  return { metrics: fileRecord(metricsPath, 'assets/browser-evidence/stage4-browser-acceptance-metrics.json'), binding: fileRecord(bindingPath, 'assets/browser-evidence/stage4-final-source-binding.json'), harness: fileRecord(harnessPath, 'assets/browser-evidence/stage4-browser-acceptance-harness.mjs'), facts: metrics, captures, fullRecords, fullAggregateSha256: BROWSER_FULL_AGGREGATE };
}

function validateGateInputs() {
  assert(exact(sortedEntries(GATES_DIR), EXPECTED_GATE_FILES), 'Gate exact file set mismatch');
  const fullRecords = EXPECTED_GATE_FILES.map((name) => fileRecord(path.join(GATES_DIR, name), name));
  assert(fullRecords.reduce((sum, item) => sum + item.bytes, 0) === 969799 && recordsAggregate(fullRecords) === GATES_FULL_AGGREGATE, 'Gate full aggregate mismatch');
  const summaryPath = path.join(GATES_DIR, 'stage4-final-gates-summary.json');
  const gateManifestPath = path.join(GATES_DIR, 'stage4-final-gate-manifest.md');
  assert(sha256File(summaryPath) === GATES_SUMMARY_SHA256 && sha256File(gateManifestPath) === GATES_MANIFEST_SHA256, 'Gate summary/manifest hash mismatch');
  const summary = readJson(summaryPath);
  assert(summary.schemaVersion === 1 && summary.stage === 4 && summary.status === 'passed' && summary.comparisonBase === BASE_COMMIT && summary.comparisonTree === BASE_TREE, 'Gate summary header mismatch');
  assert(summary.source?.changedPaths === 67 && summary.source?.trackedModified === 60 && summary.source?.untrackedAdded === 7 && summary.source?.pathsetSha256 === PATHSET_SHA256 && summary.source?.pathContentSha256 === PATH_CONTENT_SHA256, 'Gate source binding mismatch');
  assert(summary.source?.browserBinding?.runId === BROWSER_RUN_ID && summary.source?.browserBinding?.fileCount === 398 && summary.source?.browserBinding?.sha256 === BROWSER_SOURCE_SHA256 && summary.source?.browserBinding?.bytesMtimeHashMismatches === 0, 'Gate/browser binding mismatch');
  assert(summary.frontend?.files === 34 && summary.frontend?.tests === 450 && summary.frontend?.passed === 450 && summary.frontend?.failed === 0, 'Gate frontend mismatch');
  assert(summary.guards?.selfTestFiles === 3 && summary.guards?.selfTests === 55 && summary.guards?.passed === 55 && summary.guards?.aggregateGuard === 'passed' && summary.guards?.protected === 'passed', 'Gate guard mismatch');
  assert(summary.backend?.modules === 11 && summary.backend?.tests === 69 && summary.backend?.passed === 69 && summary.backend?.failed === 0 && summary.backend?.caveats?.length === 3, 'Gate backend mismatch');
  assert(summary.typecheck?.exitCode === 0 && summary.build?.exitCode === 0 && summary.build?.modules === 2162 && summary.build?.pwaEntries === 161, 'Gate type/build mismatch');
  assert(summary.quality?.eslint?.added === 0 && summary.quality?.eslint?.inherited === 121 && summary.quality?.prettier?.added === 0 && summary.quality?.prettier?.inherited === 22, 'Gate quality delta mismatch');
  assert(summary.protected?.homeMarket?.sections === 6 && summary.protected?.homeMarket?.bytes === 4553 && summary.protected?.homeMarket?.sha256 === HOME_SHA256, 'Gate Home protected mismatch');
  assert(summary.protected?.market?.files === 19 && summary.protected?.market?.sha256 === MARKET_SHA256 && summary.protected?.messenger?.files === 85 && summary.protected?.messenger?.sha256 === MESSENGER_SHA256, 'Gate Market/Messenger mismatch');
  assert(summary.protected?.adminMessagesSha256 === ADMIN_MESSAGES_SHA256 && summary.protected?.tradingSettingsSha256 === TRADING_SETTINGS_SHA256 && summary.protected?.routes?.manifestRuntime === '7/7', 'Gate admin/route protected mismatch');
  for (const [artifactPath, expected] of Object.entries(summary.artifacts)) {
    const localPath = path.join(GATES_DIR, path.basename(artifactPath));
    assert(fs.existsSync(localPath), `Gate summary artifact missing: ${artifactPath}`);
    const actual = fileRecord(localPath);
    assert(actual.bytes === expected.bytes && actual.sha256 === expected.sha256, `Gate artifact drift: ${artifactPath}`);
  }
  const gitBindingPath = path.join(GATES_DIR, 'stage4-implementation-git-binding.json');
  const gitBinding = readJson(gitBindingPath);
  assert(gitBinding.status === 'bound_from_git' && gitBinding.comparisonBaseCommit === BASE_COMMIT && gitBinding.comparisonBaseTree === BASE_TREE && gitBinding.implementationCommit === IMPLEMENTATION_COMMIT && gitBinding.implementationTree === IMPLEMENTATION_TREE && gitBinding.implementationParent === BASE_COMMIT, 'Git binding identity mismatch');
  assert(gitBinding.exactPathCount === 67 && gitBinding.trackedModifiedCount === 60 && gitBinding.addedCount === 7 && gitBinding.deletedCount === 0 && gitBinding.pathSetSha256 === PATHSET_SHA256 && gitBinding.pathContentSha256 === PATH_CONTENT_SHA256, 'Git binding cardinality/hash mismatch');
  const gateManifest = fs.readFileSync(gateManifestPath, 'utf8');
  const block = gateManifest.match(/## 7\.[\s\S]*?```text\n([\s\S]*?)\n```/);
  assert(block, 'Gate manifest exact 67-path block missing');
  const changedPaths = block[1].split('\n').filter(Boolean);
  assert(changedPaths.length === 67 && sha256Buffer(Buffer.from(`${changedPaths.slice().sort().join('\n')}\n`)) === PATHSET_SHA256, 'Gate manifest pathset mismatch');
  const gitObject = resolveGitObjectBinding();
  return { summary: fileRecord(summaryPath, 'assets/gates/stage4-final-gates-summary.json'), manifest: fileRecord(gateManifestPath, 'assets/gates/stage4-final-gate-manifest.md'), gitBinding: fileRecord(gitBindingPath, 'assets/gates/stage4-implementation-git-binding.json'), facts: summary, changedPaths, gitObject, fullRecords, fullAggregateSha256: GATES_FULL_AGGREGATE };
}

function expectedInputPaths() {
  return [
    'FIGMA_SNAPSHOT_MANIFEST.json',
    ...EXPECTED_FIGMA_FILES.map((name) => `assets/figma/${name}`),
    ...EXPECTED_BROWSER_FILES.map((name) => `assets/browser-evidence/${name}`),
    ...EXPECTED_GATE_FILES.map((name) => `assets/gates/${name}`),
    'capture-evidence.cjs',
    HTML_FILE,
  ].sort();
}

function validateEvidenceManifest() {
  const manifest = readJson(EVIDENCE_MANIFEST_PATH);
  assert(manifest.schemaVersion === 1 && manifest.stage === 4 && manifest.status === 'local_evidence_inputs_frozen', 'Evidence manifest header mismatch');
  assert(manifest.stageCompleteAuthority === false && manifest.sitesProven === false && manifest.comparisonBaseCommit === BASE_COMMIT && manifest.implementationCommit === IMPLEMENTATION_COMMIT && manifest.implementationTree === IMPLEMENTATION_TREE, 'Evidence manifest claim/source boundary mismatch');
  assert(manifest.figmaSnapshot?.captureAggregateSha256 === FIGMA_CAPTURE_AGGREGATE && manifest.browserEvidence?.fullAggregateSha256 === BROWSER_FULL_AGGREGATE && manifest.technicalGates?.fullAggregateSha256 === GATES_FULL_AGGREGATE, 'Evidence manifest aggregate declaration mismatch');
  assert(manifest.localCapture?.outputPolicy === 'exact' && exact(manifest.localCapture?.expectedFiles, EXPECTED_OUTPUT_FILES), 'Evidence manifest local output contract mismatch');
  assert(manifest.inputCount === 62 && manifest.inputs?.length === 62, 'Evidence manifest input count mismatch');
  assert(exact(manifest.inputs.map(({ path: filePath }) => filePath).slice().sort(), expectedInputPaths()), 'Evidence manifest input path set mismatch');
  for (const expected of manifest.inputs) {
    const absolutePath = path.join(CONTRACT_DIR, expected.path);
    assert(fs.existsSync(absolutePath) && fs.statSync(absolutePath).isFile(), `Manifest input missing: ${expected.path}`);
    const actual = fileRecord(absolutePath, expected.path);
    assert(actual.bytes === expected.bytes && actual.sha256 === expected.sha256, `Manifest input drift: ${expected.path}`);
  }
  const projection = manifest.inputs.map(({ path: filePath, bytes, sha256 }) => ({ path: filePath, bytes, sha256 }));
  assert(manifest.inputBytes === projection.reduce((sum, item) => sum + item.bytes, 0) && recordsAggregate(projection) === manifest.inputProjectionAggregateSha256, 'Evidence manifest input aggregate mismatch');
  return { ...fileRecord(EVIDENCE_MANIFEST_PATH, 'EVIDENCE_MANIFEST.json'), inputCount: 62, inputBytes: manifest.inputBytes, inputProjectionAggregateSha256: manifest.inputProjectionAggregateSha256, inputs: projection };
}

function validateCanonicalInputs() {
  assert(fs.existsSync(HTML_PATH) && fs.statSync(HTML_PATH).isFile(), 'Evidence HTML missing');
  const figma = validateFigmaInputs();
  const browser = validateBrowserInputs();
  const gates = validateGateInputs();
  const evidenceManifest = validateEvidenceManifest();
  return { html: fileRecord(HTML_PATH, HTML_FILE), script: fileRecord(__filename, 'capture-evidence.cjs'), figma, browser, gates, evidenceManifest };
}

function resolvePlaywright() {
  const candidates = [process.env.UIUX_PLAYWRIGHT_MODULE, path.join(REPO_ROOT, 'frontend', 'node_modules', 'playwright'), '/root/trading-bot/trading_bot/frontend/node_modules/playwright', 'playwright'].filter(Boolean);
  const errors = [];
  for (const candidate of candidates) {
    try { return { module: require(candidate), resolvedFrom: require.resolve(candidate) }; } catch (error) { errors.push(`${candidate}: ${error.code || error.message}`); }
  }
  throw new Error(`Playwright unavailable: ${errors.join(' | ')}`);
}

function resolveFonts() {
  const roots = [process.env.UIUX_VAZIRMATN_FONT_ROOT, path.join(REPO_ROOT, 'frontend', 'node_modules', 'vazirmatn', 'fonts', 'webfonts'), '/root/trading-bot/trading_bot/frontend/node_modules/vazirmatn/fonts/webfonts'].filter(Boolean);
  const weights = [{ weight: 400, file: 'Vazirmatn-Regular.woff2' }, { weight: 500, file: 'Vazirmatn-Medium.woff2' }, { weight: 600, file: 'Vazirmatn-SemiBold.woff2' }, { weight: 700, file: 'Vazirmatn-Bold.woff2' }];
  for (const root of roots) {
    const files = weights.map((item) => ({ ...item, path: path.join(root, item.file) }));
    if (files.every((item) => fs.existsSync(item.path))) {
      return { root, fonts: files.map((item) => { const buffer = fs.readFileSync(item.path); return { ...item, bytes: buffer.length, sha256: sha256Buffer(buffer), data: buffer.toString('base64') }; }) };
    }
  }
  throw new Error(`Vazirmatn font bundle unavailable: ${roots.join(', ')}`);
}

async function canonicalDomSnapshot(page) {
  return page.evaluate(() => {
    const serialize = (node) => {
      if (node.nodeType === Node.TEXT_NODE) return { type: 'text', value: node.nodeValue };
      if (node.nodeType === Node.COMMENT_NODE) return { type: 'comment', value: node.nodeValue };
      if (node.nodeType !== Node.ELEMENT_NODE) return { type: `node-${node.nodeType}` };
      return { type: node.tagName.toLowerCase(), attributes: [...node.attributes].map((attribute) => [attribute.name, attribute.value]).sort((a, b) => a[0].localeCompare(b[0])), children: [...node.childNodes].map(serialize) };
    };
    return JSON.stringify(serialize(document.documentElement));
  });
}

async function auditPage(page, context) {
  return page.evaluate(({ expectedAssertionIds, inputFacts }) => {
    const q = (selector, root = document) => root.querySelector(selector);
    const qa = (selector, root = document) => [...root.querySelectorAll(selector)];
    const number = (value) => Number(value);
    const body = document.body;
    const assertions = [];
    const record = (id, passed, details) => assertions.push({ id, passed: Boolean(passed), details });
    const overview = q('#stage4-overview');
    const figma = q('#stage4-figma-proof');
    const routes = q('#stage4-route-authority');
    const browser = q('#stage4-browser-proof');
    const recovery = q('#stage4-recovery-mutations');
    const gates = q('#stage4-gates-protected');
    const registry = qa('[data-assertion-id]').map((item) => item.dataset.assertionId);
    const loadedImages = [...document.images].every((image) => image.complete && image.naturalWidth > 0 && image.naturalHeight > 0);
    const pushStates = q('[data-push-state-registry]')?.dataset.pushStateRegistry.split(',') || [];
    record(expectedAssertionIds[0], body.dataset.stage === '4' && body.dataset.stageCompleteAuthority === 'false' && body.dataset.sitesProven === 'false', { stage: body.dataset.stage, sites: body.dataset.sitesProven });
    record(expectedAssertionIds[1], inputFacts.git.objectVerified && body.dataset.implementationCommit === inputFacts.git.commit && body.dataset.implementationTree === inputFacts.git.tree, inputFacts.git);
    record(expectedAssertionIds[2], inputFacts.git.pathCount === 67 && inputFacts.git.pathSetSha256 === inputFacts.expected.pathSetSha256 && number(gates?.dataset.gitPaths) === 67, { count: inputFacts.git.pathCount, sha256: inputFacts.git.pathSetSha256 });
    record(expectedAssertionIds[3], body.dataset.figmaRole === 'authored-snapshot' && body.dataset.stage4FigmaFreezeCreated === 'true' && body.dataset.figmaFileKey === inputFacts.figma.fileKey && body.dataset.figmaPageId === '283:18' && body.dataset.figmaRootId === '283:19', { fileKey: body.dataset.figmaFileKey, page: body.dataset.figmaPageId, root: body.dataset.figmaRootId });
    record(expectedAssertionIds[4], inputFacts.figma.captureCount === 13 && inputFacts.figma.captureAggregate === inputFacts.expected.figmaAggregate && number(body.dataset.figmaCaptureCount) === 13 && body.dataset.figmaCaptureAggregateSha256 === inputFacts.expected.figmaAggregate && loadedImages, { captureCount: inputFacts.figma.captureCount, aggregate: inputFacts.figma.captureAggregate, loadedImages });
    record(expectedAssertionIds[5], number(figma?.dataset.sectionCount) === 6 && number(figma?.dataset.screenCount) === 6 && number(figma?.dataset.linkedInstanceCount) === 66 && number(figma?.dataset.detachedCount) === 0 && number(figma?.dataset.protectedImageCount) === 1 && number(figma?.dataset.auditErrors) === 0 && inputFacts.figma.auditPassed, { audit: inputFacts.figma });
    record(expectedAssertionIds[6], number(overview?.dataset.canonicalRoutes) === 6 && number(overview?.dataset.legacyRedirects) === 2 && number(routes?.dataset.routeCount) === 6 && number(routes?.dataset.redirectCount) === 2 && qa('[data-route]', routes).length === 8, { canonical: number(routes?.dataset.routeCount), redirects: number(routes?.dataset.redirectCount) });
    record(expectedAssertionIds[7], number(routes?.dataset.ownerActions) === 2 && number(routes?.dataset.middleAdminActions) === 2 && number(routes?.dataset.seniorAdminActions) === 5, { owner: number(routes?.dataset.ownerActions), middle: number(routes?.dataset.middleAdminActions), senior: number(routes?.dataset.seniorAdminActions) });
    record(expectedAssertionIds[8], number(routes?.dataset.accountantOwnerActions) === 0, { accountantOwnerActions: number(routes?.dataset.accountantOwnerActions) });
    record(expectedAssertionIds[9], recovery?.dataset.securityReceiptBound === 'true' && recovery?.dataset.storageSizeErrorDistinct === 'true', { security: recovery?.dataset.securityReceiptBound, storage: recovery?.dataset.storageSizeErrorDistinct });
    record(expectedAssertionIds[10], recovery?.dataset.notificationRetainedError === 'true' && recovery?.dataset.routeLessInteractive === 'false', { retained: recovery?.dataset.notificationRetainedError, routeLess: recovery?.dataset.routeLessInteractive });
    record(expectedAssertionIds[11], number(recovery?.dataset.pushStateCount) === 9 && pushStates.join(',') === 'checking,unsupported,insecure,server-disabled,permission-blocked,permission-default,subscribed,unsubscribed,error', { pushStates });
    record(expectedAssertionIds[12], inputFacts.browser.status === 'passed' && inputFacts.browser.promotable && inputFacts.browser.assertions === 49 && number(browser?.dataset.browserAssertionCount) === 49 && number(browser?.dataset.browserFailedCount) === 0, inputFacts.browser);
    record(expectedAssertionIds[13], inputFacts.browser.screenshots === 22 && number(browser?.dataset.browserScreenshotCount) === 22, { screenshots: inputFacts.browser.screenshots });
    record(expectedAssertionIds[14], inputFacts.browser.viewports === 8 && inputFacts.browser.suites === 9 && number(browser?.dataset.responsiveWidthCount) === 8 && number(browser?.dataset.suiteCount) === 9, { viewports: inputFacts.browser.viewports, suites: inputFacts.browser.suites });
    record(expectedAssertionIds[15], inputFacts.browser.expectedHttp === 16 && inputFacts.browser.expectedConsole === 17 && inputFacts.browser.unexpected === 0 && number(browser?.dataset.unexpectedDiagnostics) === 0, { expectedHttp: inputFacts.browser.expectedHttp, expectedConsole: inputFacts.browser.expectedConsole, unexpected: inputFacts.browser.unexpected });
    record(expectedAssertionIds[16], inputFacts.browser.sourceCount === 398 && inputFacts.browser.sourceIdentical && inputFacts.browser.protectedIdentical && number(browser?.dataset.sourceCount) === 398 && browser?.dataset.sourceIdentical === 'true' && browser?.dataset.protectedIdentical === 'true', { sourceCount: inputFacts.browser.sourceCount });
    record(expectedAssertionIds[17], inputFacts.gates.frontendFiles === 34 && inputFacts.gates.frontendTests === 450 && number(gates?.dataset.vitestFiles) === 34 && number(gates?.dataset.vitestTests) === 450, inputFacts.gates);
    record(expectedAssertionIds[18], inputFacts.gates.guardFiles === 3 && inputFacts.gates.guardTests === 55 && number(gates?.dataset.guardFiles) === 3 && number(gates?.dataset.guardTests) === 55, inputFacts.gates);
    record(expectedAssertionIds[19], inputFacts.gates.backendModules === 11 && inputFacts.gates.backendTests === 69 && inputFacts.gates.backendCaveats === 3 && number(gates?.dataset.backendModules) === 11 && number(gates?.dataset.backendTests) === 69, inputFacts.gates);
    record(expectedAssertionIds[20], inputFacts.gates.typecheck && inputFacts.gates.build && inputFacts.gates.buildModules === 2162 && inputFacts.gates.pwaEntries === 161 && number(gates?.dataset.buildModules) === 2162 && number(gates?.dataset.pwaEntries) === 161, inputFacts.gates);
    record(expectedAssertionIds[21], inputFacts.gates.eslintAdded === 0 && inputFacts.gates.eslintInherited === 121 && number(gates?.dataset.eslintStage4New) === 0, inputFacts.gates);
    record(expectedAssertionIds[22], inputFacts.gates.prettierAdded === 0 && inputFacts.gates.prettierInherited === 22 && number(gates?.dataset.prettierStage4New) === 0, inputFacts.gates);
    record(expectedAssertionIds[23], number(gates?.dataset.homeSections) === 6 && number(gates?.dataset.homeBytes) === 4553 && gates?.dataset.homeSha256 === inputFacts.expected.homeSha256, { sections: number(gates?.dataset.homeSections), bytes: number(gates?.dataset.homeBytes), sha256: gates?.dataset.homeSha256 });
    record(expectedAssertionIds[24], number(gates?.dataset.marketFiles) === 19 && gates?.dataset.marketSha256 === inputFacts.expected.marketSha256 && number(gates?.dataset.messengerFiles) === 85 && gates?.dataset.messengerSha256 === inputFacts.expected.messengerSha256, { market: gates?.dataset.marketSha256, messenger: gates?.dataset.messengerSha256 });
    record(expectedAssertionIds[25], number(gates?.dataset.protectedRoutes) === 7 && inputFacts.gates.adminHashesExact && registry.length === 26 && registry.join(',') === expectedAssertionIds.join(','), { protectedRoutes: number(gates?.dataset.protectedRoutes), registryCount: registry.length });
    return { assertions, passed: assertions.filter(({ passed }) => passed).length, failed: assertions.filter(({ passed }) => !passed).length, measurements: { loadedImageCount: document.images.length, routes: qa('[data-route]', routes).length, pushStates, registry } };
  }, { expectedAssertionIds: EXPECTED_ASSERTIONS, inputFacts: context });
}

function residueDirectories() {
  if (!fs.existsSync(ASSETS_DIR)) return [];
  return fs.readdirSync(ASSETS_DIR, { withFileTypes: true })
    .filter((entry) => entry.isDirectory() && (entry.name.startsWith('.local-evidence-staging-') || entry.name.startsWith('.local-evidence-backup-')))
    .map((entry) => path.join(ASSETS_DIR, entry.name)).sort();
}

function removeDirectory(directory) {
  fs.rmSync(directory, { recursive: true, force: true });
}

function validateEvidenceDirectory(directory) {
  assert(fs.existsSync(directory) && fs.statSync(directory).isDirectory(), `Local evidence directory missing: ${directory}`);
  const actual = sortedEntries(directory);
  assert(exact(actual, EXPECTED_OUTPUT_FILES), `Local evidence exact output mismatch: ${actual.join(', ')}`);
  const metrics = readJson(path.join(directory, METRICS_FILE));
  assert(metrics.schemaVersion === 1 && metrics.stage === 4 && metrics.status === 'passed' && metrics.localEvidenceStatus === 'frozen', 'Local metrics header mismatch');
  assert(metrics.claimBoundary?.stageCompleteAuthority === false && metrics.claimBoundary?.sitesProven === false && metrics.claimBoundary?.figmaAuthoredSnapshot === true, 'Local metrics claim boundary mismatch');
  assert(metrics.inputs?.evidenceManifest?.inputCount === 62 && metrics.inputs?.implementation?.commit === IMPLEMENTATION_COMMIT && metrics.inputs?.implementation?.tree === IMPLEMENTATION_TREE, 'Local metrics input/Git mismatch');
  assert(metrics.inputs?.browser?.fullAggregateSha256 === BROWSER_FULL_AGGREGATE && metrics.inputs?.figma?.captureAggregateSha256 === FIGMA_CAPTURE_AGGREGATE && metrics.inputs?.gates?.fullAggregateSha256 === GATES_FULL_AGGREGATE, 'Local metrics canonical aggregate mismatch');
  assert(metrics.integrity?.domEqual === true && metrics.integrity?.auditEqual === true && metrics.integrity?.postCaptureRemeasurement === true, 'Local pre/post integrity mismatch');
  assert(exact(metrics.integrity?.consoleErrors, []) && exact(metrics.integrity?.pageErrors, []) && exact(metrics.integrity?.failedRequests, []) && exact(metrics.integrity?.blockedNetworkRequests, []), 'Local Chromium diagnostics mismatch');
  assert(exact(metrics.assertions?.map(({ id }) => id), EXPECTED_ASSERTIONS) && metrics.assertions.every(({ passed }) => passed === true), 'Local assertion mismatch');
  assert(metrics.assertionSummary?.total === 26 && metrics.assertionSummary?.passed === 26 && metrics.assertionSummary?.failed === 0, 'Local assertion summary mismatch');
  assert(exact(metrics.outputSet?.files, EXPECTED_OUTPUT_FILES) && metrics.outputSet?.pngCount === 6 && metrics.outputSet?.metricsCount === 1, 'Local output declaration mismatch');
  assert(Array.isArray(metrics.captures) && metrics.captures.length === 6, 'Local capture count mismatch');
  for (const capture of metrics.captures) {
    assert(CAPTURE_SPECS.some((spec) => spec.file === capture.file && spec.selector === capture.selector), `Unexpected local capture: ${capture.file}`);
    const checked = validatePng(path.join(directory, capture.file), capture);
    assert(exact(checked.visualStats, capture.visualStats), `Local capture visual stats mismatch: ${capture.file}`);
  }
  return metrics;
}

function outputRecords(directory) {
  return EXPECTED_OUTPUT_FILES.map((name) => fileRecord(path.join(directory, name), `assets/local-evidence/${name}`));
}

function frozenPackageRecords(evidenceManifest) {
  const local = outputRecords(PUBLISHED_DIR);
  const manifestRecord = fileRecord(EVIDENCE_MANIFEST_PATH, 'EVIDENCE_MANIFEST.json');
  return [manifestRecord, ...evidenceManifest.inputs, ...local].sort((a, b) => a.path.localeCompare(b.path));
}

async function main() {
  for (const residue of residueDirectories()) removeDirectory(residue);
  const prior = fs.existsSync(PUBLISHED_DIR) && sortedEntries(PUBLISHED_DIR).length > 0 ? validateEvidenceDirectory(PUBLISHED_DIR) : null;
  if (fs.existsSync(PUBLISHED_DIR) && sortedEntries(PUBLISHED_DIR).length === 0) removeDirectory(PUBLISHED_DIR);
  const inputs = validateCanonicalInputs();
  const runId = `stage4-local-${inputs.evidenceManifest.inputProjectionAggregateSha256.slice(0, 20)}`;
  const stagingDir = path.join(ASSETS_DIR, `.local-evidence-staging-${runId}`);
  if (fs.existsSync(stagingDir)) removeDirectory(stagingDir);
  fs.mkdirSync(stagingDir, { recursive: false });
  const playwright = resolvePlaywright();
  const fonts = resolveFonts();
  let browser;
  try {
    const consoleErrors = [];
    const pageErrors = [];
    const failedRequests = [];
    const blockedNetworkRequests = [];
    browser = await playwright.module.chromium.launch({ headless: true });
    const browserVersion = browser.version();
    const browserContext = await browser.newContext({ viewport: { width: 1500, height: 1200 }, deviceScaleFactor: 1, locale: 'fa-IR', colorScheme: 'light', reducedMotion: 'reduce' });
    const page = await browserContext.newPage();
    page.on('console', (message) => { if (message.type() === 'error') consoleErrors.push(message.text()); });
    page.on('pageerror', (error) => pageErrors.push(error.message));
    page.on('requestfailed', (request) => failedRequests.push({ url: request.url(), error: request.failure()?.errorText || 'unknown' }));
    await page.route(/^https?:\/\//, async (route) => { blockedNetworkRequests.push(route.request().url()); await route.abort('blockedbyclient'); });
    await page.goto(pathToFileURL(HTML_PATH).href, { waitUntil: 'load' });
    const fontCss = fonts.fonts.map((font) => `@font-face{font-family:'Vazirmatn';font-style:normal;font-weight:${font.weight};font-display:block;src:url(data:font/woff2;base64,${font.data}) format('woff2');}`).join('\n');
    await page.addStyleTag({ content: fontCss });
    await page.evaluate(async () => {
      await Promise.all([400, 500, 600, 700].map((weight) => document.fonts.load(`${weight} 16px Vazirmatn`, 'آزمون')));
      await document.fonts.ready;
      if (![...document.images].every((image) => image.complete && image.naturalWidth > 0)) throw new Error('One or more evidence images failed to load');
    });
    const fontChecks = await page.evaluate(() => [400, 500, 600, 700].map((weight) => ({ weight, loaded: document.fonts.check(`${weight} 16px Vazirmatn`, 'آزمون') })));
    assert(fontChecks.every(({ loaded }) => loaded), `Font checks failed: ${JSON.stringify(fontChecks)}`);
    const context = {
      expected: { pathSetSha256: PATHSET_SHA256, figmaAggregate: FIGMA_CAPTURE_AGGREGATE, homeSha256: HOME_SHA256, marketSha256: MARKET_SHA256, messengerSha256: MESSENGER_SHA256 },
      git: { objectVerified: inputs.gates.gitObject.looseObjectSha1Verified, commit: inputs.gates.gitObject.commit, tree: inputs.gates.gitObject.tree, parent: inputs.gates.gitObject.parent, pathCount: inputs.gates.changedPaths.length, pathSetSha256: PATHSET_SHA256 },
      figma: { fileKey: inputs.figma.auditFacts.fileKey, captureCount: inputs.figma.captures.length, captureAggregate: inputs.figma.captureAggregateSha256, auditPassed: inputs.figma.auditFacts.status === 'passed' && inputs.figma.auditFacts.errors.length === 0, sections: 6, screens: 6, linked: 66, detached: 0, protectedImages: 1 },
      browser: { status: inputs.browser.facts.status, promotable: inputs.browser.facts.promotable, assertions: inputs.browser.facts.assertionSummary.passed, screenshots: inputs.browser.captures.length, viewports: inputs.browser.facts.requiredViewports.length, suites: inputs.browser.facts.selectedSuites.length, expectedHttp: inputs.browser.facts.diagnostics.expectedHttpFailures.length, expectedConsole: inputs.browser.facts.diagnostics.expectedConsoleErrors.length, unexpected: 0, sourceCount: inputs.browser.facts.sourceBinding.pre.length, sourceIdentical: inputs.browser.facts.sourceBinding.identical, protectedIdentical: inputs.browser.facts.protectedBinding.identical },
      gates: { frontendFiles: inputs.gates.facts.frontend.files, frontendTests: inputs.gates.facts.frontend.tests, guardFiles: inputs.gates.facts.guards.selfTestFiles, guardTests: inputs.gates.facts.guards.selfTests, backendModules: inputs.gates.facts.backend.modules, backendTests: inputs.gates.facts.backend.tests, backendCaveats: inputs.gates.facts.backend.caveats.length, typecheck: inputs.gates.facts.typecheck.exitCode === 0, build: inputs.gates.facts.build.exitCode === 0, buildModules: inputs.gates.facts.build.modules, pwaEntries: inputs.gates.facts.build.pwaEntries, eslintAdded: inputs.gates.facts.quality.eslint.added, eslintInherited: inputs.gates.facts.quality.eslint.inherited, prettierAdded: inputs.gates.facts.quality.prettier.added, prettierInherited: inputs.gates.facts.quality.prettier.inherited, adminHashesExact: inputs.gates.facts.protected.adminMessagesSha256 === ADMIN_MESSAGES_SHA256 && inputs.gates.facts.protected.tradingSettingsSha256 === TRADING_SETTINGS_SHA256 },
    };
    const preAudit = await auditPage(page, context);
    assert(preAudit.passed === 26 && preAudit.failed === 0, `Pre-capture assertions failed: ${JSON.stringify(preAudit.assertions.filter(({ passed }) => !passed), null, 2)}`);
    const preDom = await canonicalDomSnapshot(page);
    const preDomSha256 = sha256Buffer(Buffer.from(preDom));
    const preAuditSha256 = hashValue(preAudit);
    const captures = [];
    for (const spec of CAPTURE_SPECS) {
      const locator = page.locator(spec.selector);
      assert(await locator.count() === 1, `Capture selector count mismatch: ${spec.selector}`);
      const box = await locator.evaluate((element) => { const rect = element.getBoundingClientRect(); return { x: rect.left + window.scrollX, y: rect.top + window.scrollY, width: rect.width, height: rect.height, documentWidth: document.documentElement.scrollWidth, documentHeight: document.documentElement.scrollHeight }; });
      assert(box.width > 0 && box.height > 0 && box.x >= 0 && box.y >= 0 && box.x + box.width <= box.documentWidth + 0.01 && box.y + box.height <= box.documentHeight + 0.01, `Capture geometry invalid: ${spec.file}`);
      const width = Math.ceil(box.width);
      const height = Math.ceil(box.height);
      const outputPath = path.join(stagingDir, spec.file);
      const originalStyle = await locator.getAttribute('style');
      try {
        await page.setViewportSize({ width, height });
        await locator.evaluate((element, geometry) => {
          element.style.setProperty('position', 'fixed', 'important');
          element.style.setProperty('inset', '0 auto auto 0', 'important');
          element.style.setProperty('margin', '0', 'important');
          element.style.setProperty('transform', 'none', 'important');
          element.style.setProperty('width', `${geometry.width}px`, 'important');
          element.style.setProperty('height', `${geometry.height}px`, 'important');
          element.style.setProperty('z-index', '2147483647', 'important');
        }, { width, height });
        await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
        const fixed = await locator.boundingBox();
        assert(fixed && fixed.x === 0 && fixed.y === 0 && Math.round(fixed.width) === width && Math.round(fixed.height) === height, `Fixed capture geometry mismatch: ${spec.file}`);
        await page.screenshot({ path: outputPath, animations: 'disabled', caret: 'hide', clip: { x: 0, y: 0, width, height } });
      } finally {
        await locator.evaluate((element, style) => { if (style === null) element.removeAttribute('style'); else element.setAttribute('style', style); }, originalStyle);
        await page.setViewportSize({ width: 1500, height: 1200 });
      }
      const checked = validatePng(outputPath, { path: spec.file, width, height });
      captures.push({ file: spec.file, selector: spec.selector, width, height, bytes: checked.bytes, sha256: checked.sha256, visualStats: checked.visualStats });
    }
    const postAudit = await auditPage(page, context);
    const postDom = await canonicalDomSnapshot(page);
    const postDomSha256 = sha256Buffer(Buffer.from(postDom));
    const postAuditSha256 = hashValue(postAudit);
    assert(postAudit.passed === 26 && postAudit.failed === 0, `Post-capture assertions failed: ${JSON.stringify(postAudit.assertions.filter(({ passed }) => !passed), null, 2)}`);
    assert(preAuditSha256 === postAuditSha256 && stableJson(preAudit) === stableJson(postAudit), 'Pre/post audit changed');
    assert(preDomSha256 === postDomSha256 && preDom === postDom, 'Pre/post canonical DOM changed');
    assert(consoleErrors.length === 0 && pageErrors.length === 0 && failedRequests.length === 0 && blockedNetworkRequests.length === 0, `Local Chromium diagnostics: ${JSON.stringify({ consoleErrors, pageErrors, failedRequests, blockedNetworkRequests })}`);
    const metrics = {
      schemaVersion: 1,
      stage: 4,
      status: 'passed',
      localEvidenceStatus: 'frozen',
      runId,
      recordedDate: '2026-08-09',
      comparisonBaseCommit: BASE_COMMIT,
      claimBoundary: { localDerivative: true, stageCompleteAuthority: false, sitesProven: false, figmaAuthoredSnapshot: true, runtimeEvidenceHashBound: true },
      inputs: {
        evidenceManifest: { path: inputs.evidenceManifest.path, bytes: inputs.evidenceManifest.bytes, sha256: inputs.evidenceManifest.sha256, inputCount: inputs.evidenceManifest.inputCount, inputBytes: inputs.evidenceManifest.inputBytes, inputProjectionAggregateSha256: inputs.evidenceManifest.inputProjectionAggregateSha256 },
        implementation: { commit: IMPLEMENTATION_COMMIT, tree: IMPLEMENTATION_TREE, parent: BASE_COMMIT, pathCount: 67, pathSetSha256: PATHSET_SHA256, pathContentSha256: PATH_CONTENT_SHA256, looseObjectSha1Verified: true },
        html: inputs.html,
        script: inputs.script,
        figma: { manifest: inputs.figma.manifest, audit: inputs.figma.audit, captureCount: 13, captureBytes: 1118391, captureAggregateSha256: FIGMA_CAPTURE_AGGREGATE, inputCount: 14, inputBytes: 1120421, inputAggregateSha256: FIGMA_INPUT_AGGREGATE },
        browser: { metrics: inputs.browser.metrics, binding: inputs.browser.binding, harness: inputs.browser.harness, runId: BROWSER_RUN_ID, assertions: 49, screenshots: 22, sourceCount: 398, sourceBindingSha256: BROWSER_SOURCE_SHA256, fullCount: 25, fullBytes: 2806569, fullAggregateSha256: BROWSER_FULL_AGGREGATE },
        gates: { summary: inputs.gates.summary, manifest: inputs.gates.manifest, gitBinding: inputs.gates.gitBinding, fileCount: 20, bytes: 969799, fullAggregateSha256: GATES_FULL_AGGREGATE },
      },
      dependencies: { node: process.version, platform: `${process.platform}-${process.arch}`, playwright: playwright.resolvedFrom, browser: { engine: 'chromium', version: browserVersion, viewport: { width: 1500, height: 1200 }, deviceScaleFactor: 1 }, fonts: { family: 'Vazirmatn', root: fonts.root, checks: fontChecks, files: fonts.fonts.map(({ file, weight, bytes, sha256 }) => ({ file, weight, bytes, sha256 })) } },
      integrity: { preDomSha256, postDomSha256, domEqual: true, preAuditSha256, postAuditSha256, auditEqual: true, postCaptureRemeasurement: true, consoleErrors, pageErrors, failedRequests, blockedNetworkRequests },
      assertions: postAudit.assertions,
      assertionSummary: { total: 26, passed: 26, failed: 0, exactOrder: true },
      measurements: postAudit.measurements,
      captures,
      outputSet: { policy: 'exact', pngCount: 6, metricsCount: 1, files: EXPECTED_OUTPUT_FILES },
      publication: { strategy: 'atomic-directory-rename', partialPromotionAllowed: false, validationBeforePromotion: true, validationAfterPromotion: true, residueAllowed: false, deterministicRunId: true },
    };
    fs.writeFileSync(path.join(stagingDir, METRICS_FILE), `${JSON.stringify(metrics, null, 2)}\n`, { encoding: 'utf8', flag: 'wx' });
    validateEvidenceDirectory(stagingDir);
    await browserContext.close();
    await browser.close();
    browser = null;
    let deterministicRerun = false;
    if (prior) {
      const priorRecords = outputRecords(PUBLISHED_DIR);
      const stagingRecords = outputRecords(stagingDir);
      assert(exact(priorRecords, stagingRecords), `Deterministic rerun output drift: ${JSON.stringify({ priorRecords, stagingRecords }, null, 2)}`);
      deterministicRerun = true;
      removeDirectory(stagingDir);
    } else {
      fs.renameSync(stagingDir, PUBLISHED_DIR);
    }
    const published = validateEvidenceDirectory(PUBLISHED_DIR);
    assert(residueDirectories().length === 0, 'Local evidence publication residue found');
    const packageRecords = frozenPackageRecords(inputs.evidenceManifest);
    assert(packageRecords.length === 70, `Frozen package file count mismatch: ${packageRecords.length}`);
    const packageBytes = packageRecords.reduce((sum, item) => sum + item.bytes, 0);
    const packageAggregateSha256 = recordsAggregate(packageRecords);
    process.stdout.write(`${JSON.stringify({
      status: 'FROZEN',
      runId: published.runId,
      assertions: '26/26',
      deterministicRerun,
      domSha256: published.integrity.postDomSha256,
      auditSha256: published.integrity.postAuditSha256,
      inputCount: inputs.evidenceManifest.inputCount,
      inputBytes: inputs.evidenceManifest.inputBytes,
      inputProjectionAggregateSha256: inputs.evidenceManifest.inputProjectionAggregateSha256,
      outputDirectory: path.relative(REPO_ROOT, PUBLISHED_DIR),
      outputFiles: outputRecords(PUBLISHED_DIR),
      frozenPackage: { fileCount: 70, bytes: packageBytes, aggregateAlgorithm: 'sha256(JSON.stringify(records sorted by path; key order path,bytes,sha256))', aggregateSha256: packageAggregateSha256 },
      residueDirectories: residueDirectories(),
    }, null, 2)}\n`);
  } catch (error) {
    if (browser) await browser.close().catch(() => {});
    if (fs.existsSync(stagingDir)) removeDirectory(stagingDir);
    throw error;
  }
}

main().catch((error) => {
  process.stderr.write(`Stage 4 evidence capture failed: ${error.stack || error.message}\n`);
  process.exitCode = 1;
});
