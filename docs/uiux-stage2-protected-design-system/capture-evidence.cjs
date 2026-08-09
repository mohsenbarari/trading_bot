#!/usr/bin/env node
'use strict';

/**
 * Stage 2 protected design-system local evidence harness.
 *
 * Figma is canonical. The HTML and screenshots are secondary derivatives.
 * This harness is deliberately fail-closed: canonical inputs are hash-bound,
 * capture is remeasured, and only a complete validated directory is promoted.
 */

const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const zlib = require('node:zlib');
const { pathToFileURL } = require('node:url');

const CONTRACT_DIR = __dirname;
const REPO_ROOT = path.resolve(CONTRACT_DIR, '..', '..');
const HTML_FILE = 'stage2-protected-design-system-evidence.html';
const HTML_PATH = path.join(CONTRACT_DIR, HTML_FILE);
const ASSETS_DIR = path.join(CONTRACT_DIR, 'assets');
const PUBLISHED_DIR = path.join(ASSETS_DIR, 'local-evidence');
const METRICS_FILE = 'local-stage2-protected-design-system-validation-metrics.json';
const DIRECT_AUDIT_FILE = 'figma-stage2-direct-audit.json';
const DIRECT_AUDIT_SHA256 = '7361d43e2f3c9437997663cb313a03b87a5d505f3046f4406d97bd82b6ddacc5';
const DIRECT_EXPORT_AGGREGATE_SHA256 = '5af685f38703408f10618a1e87397386dbe98e5a8c6df0f8967cdff6da38dbf5';
const CANONICAL_TREE_SHA256 = '4df00cbcb8734865367e41fa28c8936d39a49e1c6578af3d5979f79857365a22';
const CODE_CONTRACT_AGGREGATE_SHA256 = '46206b37e220ead294598ad142cc657d40f1e804a479c486a26689942858a5a6';

const CODE_CONTRACT_SOURCES = Object.freeze([
  { path: 'frontend/scripts/lib/design-system-v2-guard.mjs', bytes: 51914, sha256: 'ef57cf1fd8fdb5bd50da7e67af03e8170e3a3f4bc4b241553899c3cf11bc90d8' },
  { path: 'frontend/scripts/design-system-v2-guard.test.mjs', bytes: 30090, sha256: '36ff408a722bd03c9fe4f939dfba9d80af2b415a21e08e43f67ba6764c705122' },
  { path: 'frontend/src/design-system-v2/canonical-token-contract.json', bytes: 7436, sha256: 'a0c3f3560acaa8c4fddc123ec042657d7db73d0599698e49eb172f647227cf66' },
  { path: 'frontend/scripts/check-design-system-v2-guards.mjs', bytes: 4326, sha256: '554a91b4d18842b081493266fcef4dfa0a3058c52dfa0a8aa371a60d289f5dc6' },
  { path: 'frontend/src/styles/design-system-v2.tokens.css', bytes: 5522, sha256: '8dc088a5d4064ee2aa01fbc86e56e436fa557b4fd215c2e726dba7bb21346130' },
  { path: 'frontend/src/styles/design-system-v2.components.css', bytes: 1599, sha256: '47a370be179d2a474c14c55e948236cd62b5cc9717c0e5816dec4ff4aaad9857' },
  { path: 'frontend/src/components/ui/AppDesignSystemCatalog.vue', bytes: 42299, sha256: '0208d421aaf1ebed7bfc6dcc1ef6370ec10bd3d4ac2ba2602dbecc364734486d' },
]);

const EXECUTABLE_GUARD_VERIFICATION = Object.freeze({
  focusedCommand: 'npm exec vitest run -- scripts/design-system-v2-guard.test.mjs src/components/ui/AppDesignSystemCatalog.test.ts src/components/ui/AppDesignSystemScope.test.ts src/components/ui/uiDesignSystemScope.test.ts src/router/uiRouteContract.test.ts src/styles/designSystemV2.test.ts',
  focusedTestFiles: 6,
  focusedTests: 68,
  focusedDurationSeconds: 14.27,
  focusedExitCode: 0,
  focusedStatus: 'passed',
  guardTests: 39,
  guardCommand: 'cd frontend && npm run guard:ui',
  guardStatus: 'passed',
  guardCssFiles: 3,
  guardRoutes: 29,
  serialTestFiles: 41,
  serialTests: 452,
  serialVitestDurationSeconds: 124.87,
  serialWallMs: 126229,
  serialExitCode: 0,
  guardSourcePrePostStable: true,
  canonicalTokens: 65,
  implementationTuples: 43,
  definitions: 108,
  uniqueDefinitions: 106,
  browserStatus: 'passed',
  targetedVueTscStatus: 'passed',
  targetedVueTscExitCode: 0,
  lintStatus: 'passed',
  prettierStatus: 'passed',
  buildStatus: 'passed',
  buildModules: 2153,
  buildDurationSeconds: 26.07,
  protectedDiffCount: 0,
  protectedDiffSha256: 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855',
  protectedDiffUpstreamStatus: 'passed',
  localHarnessClaim: false,
  provenance: 'settled upstream fresh-gate execution; locally hash-bound, not re-inferred from screenshots or rerun by this harness',
});

const FIGMA_FREEZE = Object.freeze({
  fileKey: 'z8jgJxST4O2APzWnlyP9gv',
  pageId: '208:2',
  rootId: '208:3',
  frozenAt: '2026-08-09T01:27:37.567Z',
  auditedAt: '2026-08-09T01:24:59.000Z',
  preCaptureRereadAt: '2026-08-09T01:31:58.397Z',
  postCaptureRereadAt: '2026-08-09T01:36:18.907Z',
  inventoryDigestRereadAt: '2026-08-09T01:38:01.728Z',
  canonicalRole: 'primary',
  localEvidenceRole: 'secondary-derivative',
});

const EXPECTED_ASSERTION_IDS = Object.freeze([
  'freeze-metadata-exact',
  'canonical-tree-pre-post-equal',
  'root-section-geometry-exact',
  'foundation-collections-20-26-19',
  'variable-inventory-65-web-syntax-exact',
  'semantic-aliases-26-broken-zero',
  'variable-scopes-all-scope-zero',
  'text-styles-10-avatar-bindings-2',
  'effect-styles-2',
  'border-default-8091a3-contrast-pass',
  'component-sets-12-variants-56',
  'catalog-56-top-6-nested-linked-detached-zero',
  'bottom-navigation-six-height-80-bound',
  'icon-scale-16-20-24-token-bound-no-text-glyph',
  'route-registry-29-all-off',
  'full-protected-routes-exact',
  'mixed-protected-routes-exact',
  'responsive-mobile-360-375-390-414-430',
  'desktop-proof-1440x900',
  'accessibility-66-targets-10-focus-zero-failures',
  'geometry-overflow-clipping-zero',
  'product-activation-protected-interior-zero',
  'reviewer-failed-draft-placeholder-zero',
  'direct-export-set-8-exact-nonblank-hash-bound',
  'opt-in-selectors-exact',
]);

const EXPECTED_ROUTES = Object.freeze([
  { path: '/', protection: 'mixed' },
  { path: '/setup-password', protection: 'none' },
  { path: '/login', protection: 'none' },
  { path: '/market', protection: 'full' },
  { path: '/operations', protection: 'none' },
  { path: '/operations/customers', protection: 'none' },
  { path: '/operations/customers/:relationId', protection: 'none' },
  { path: '/operations/accountants', protection: 'none' },
  { path: '/operations/accountants/:relationId', protection: 'none' },
  { path: '/account', protection: 'none' },
  { path: '/account/security', protection: 'none' },
  { path: '/account/storage', protection: 'none' },
  { path: '/account/notifications', protection: 'none' },
  { path: '/chat', protection: 'full' },
  { path: '/users/:id', protection: 'none' },
  { path: '/profile', protection: 'none' },
  { path: '/settings', protection: 'none' },
  { path: '/admin', protection: 'none' },
  { path: '/admin/invitations', protection: 'none' },
  { path: '/admin/channels', protection: 'full' },
  { path: '/admin/users', protection: 'none' },
  { path: '/admin/users/:id', protection: 'none' },
  { path: '/admin/commodities', protection: 'none' },
  { path: '/admin/messages', protection: 'mixed' },
  { path: '/admin/system', protection: 'mixed' },
  { path: '/i/:code', protection: 'none' },
  { path: '/register', protection: 'none' },
  { path: '/notifications', protection: 'none' },
  { path: '/share-receive', protection: 'full' },
]);

const DIRECT_EXPORTS = Object.freeze([
  { nodeId: '208:3', path: 'figma-stage2-contract-root.png', width: 1440, height: 13028, bytes: 1065674, sha256: 'b5c0ef30e0a4a0f774613c341b98cbe35bbfa754e9728400c92aa7f876a0e8cf', entropy: 2.329698 },
  { nodeId: '211:2', path: 'figma-stage2-color-foundations.png', width: 1280, height: 1792, bytes: 139052, sha256: '60c701a957e15bfed876c8bbbc0acbffd7b60993aa9bc3e6404269a91db8aefc', entropy: 2.554644 },
  { nodeId: '213:2', path: 'figma-stage2-type-geometry-motion.png', width: 1280, height: 2092, bytes: 221551, sha256: '87e9f115af5d753cefc9661083231948ad2a2c2da6cfdc49775d7d38b5769b0d', entropy: 1.986713 },
  { nodeId: '215:2', path: 'figma-stage2-component-state-catalog.png', width: 1280, height: 4488, bytes: 353191, sha256: 'e719d215744e723309fda3eaa45c0f76ad6105ef3c9745a03827d7bccabc6c54', entropy: 1.551923 },
  { nodeId: '221:2', path: 'figma-stage2-scope-route-contract.png', width: 1280, height: 1208, bytes: 127580, sha256: 'bd8a54b903ae2b161b5bed31c429e57d263d2976a736ebd928fdda86018a0291', entropy: 1.857534 },
  { nodeId: '223:2', path: 'figma-stage2-responsive-acceptance.png', width: 1280, height: 1428, bytes: 78246, sha256: 'e5badb1aaba8d1d25fc1309dfca98322532193b8fc87677aa3d9d20ded6e1e3b', entropy: 2.122881 },
  { nodeId: '224:2', path: 'figma-stage2-desktop-catalog-1440x900.png', width: 1440, height: 900, bytes: 62492, sha256: '6a0830f432a074b70bc543548fd1300cf8b698dc4122e5e8dcbc29a2d4b35a7b', entropy: 2.373797 },
  { nodeId: '226:18', path: 'figma-stage2-guard-rollback-evidence.png', width: 1280, height: 992, bytes: 99086, sha256: '37c7f4defe94adbc857063376ae9782eac4e70e16d7c3e16326d3f93507c913f', entropy: 2.022405 },
]);

const CAPTURE_SPECS = Object.freeze([
  { selector: '#stage2-overview', file: 'local-stage2-overview.png' },
  { selector: '#stage2-foundations', file: 'local-stage2-foundations.png' },
  { selector: '#stage2-component-catalog', file: 'local-stage2-component-catalog.png' },
  { selector: '#stage2-scope-routes', file: 'local-stage2-scope-routes.png' },
  { selector: '#stage2-responsive', file: 'local-stage2-responsive.png' },
  { selector: '#stage2-guard-rollback', file: 'local-stage2-guard-rollback.png' },
]);

const EXACT_OUTPUT_FILES = Object.freeze([...CAPTURE_SPECS.map((item) => item.file), METRICS_FILE].sort());

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

function sha256Buffer(buffer) {
  return crypto.createHash('sha256').update(buffer).digest('hex');
}

function sha256File(filePath) {
  return sha256Buffer(fs.readFileSync(filePath));
}

function stableJson(value) {
  if (Array.isArray(value)) return `[${value.map(stableJson).join(',')}]`;
  if (value && typeof value === 'object') {
    return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${stableJson(value[key])}`).join(',')}}`;
  }
  return JSON.stringify(value);
}

function hashValue(value) {
  return sha256Buffer(Buffer.from(stableJson(value)));
}

function exact(actual, expected) {
  return JSON.stringify(actual) === JSON.stringify(expected);
}

function sortedDirectoryEntries(directory) {
  return fs.readdirSync(directory, { withFileTypes: true }).map((entry) => entry.name).sort();
}

function pngDimensions(filePath) {
  const buffer = fs.readFileSync(filePath);
  assert(buffer.length >= 24 && buffer.subarray(0, 8).toString('hex') === '89504e470d0a1a0a' && buffer.subarray(12, 16).toString('ascii') === 'IHDR', `Invalid PNG: ${filePath}`);
  return { width: buffer.readUInt32BE(16), height: buffer.readUInt32BE(20) };
}

function pngVisualStats(filePath) {
  const buffer = fs.readFileSync(filePath);
  const width = buffer.readUInt32BE(16);
  const height = buffer.readUInt32BE(20);
  const bitDepth = buffer[24];
  const colorType = buffer[25];
  assert(bitDepth === 8 && (colorType === 2 || colorType === 6), `Unsupported PNG format: ${filePath} depth=${bitDepth} type=${colorType}`);
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
      const color = bytesPerPixel === 3
        ? `${row[x]},${row[x + 1]},${row[x + 2]}`
        : `${row[x]},${row[x + 1]},${row[x + 2]},${row[x + 3]}`;
      if (firstColor === null) firstColor = color;
      if (color !== firstColor) changedPixelCount += 1;
      if (colors.size <= 256) colors.add(color);
    }
    previous = row;
  }
  return { uniqueColorCount: colors.size, changedPixelCount };
}

function validateEvidenceDirectory(directory) {
  assert(fs.existsSync(directory) && fs.statSync(directory).isDirectory(), `Evidence directory missing: ${directory}`);
  const actual = sortedDirectoryEntries(directory);
  assert(exact(actual, EXACT_OUTPUT_FILES), `Evidence output set mismatch in ${directory}: ${actual.join(', ')}`);
  const metricsPath = path.join(directory, METRICS_FILE);
  const metrics = JSON.parse(fs.readFileSync(metricsPath, 'utf8'));
  assert(metrics.schemaVersion === 1 && metrics.stage === '2' && metrics.status === 'passed', `Invalid Stage 2 metrics: ${directory}`);
  assert(metrics.canonicalFigma?.fileKey === FIGMA_FREEZE.fileKey && metrics.canonicalFigma?.pageId === FIGMA_FREEZE.pageId && metrics.canonicalFigma?.rootId === FIGMA_FREEZE.rootId, `Canonical identity mismatch: ${directory}`);
  assert(metrics.canonicalFigma?.frozenAt === FIGMA_FREEZE.frozenAt && metrics.canonicalFigma?.auditedAt === FIGMA_FREEZE.auditedAt, `Canonical timestamp mismatch: ${directory}`);
  assert(metrics.localEvidence?.role === FIGMA_FREEZE.localEvidenceRole && metrics.localEvidence?.canonical === false && metrics.localEvidence?.runtimeBehaviorProven === false, `Local evidence boundary mismatch: ${directory}`);
  assert(metrics.directFigma?.auditJson?.sha256 === DIRECT_AUDIT_SHA256 && metrics.directFigma?.exportAggregateSha256 === DIRECT_EXPORT_AGGREGATE_SHA256, `Direct evidence binding mismatch: ${directory}`);
  assert(metrics.executableGuard?.sourceAggregateSha256 === CODE_CONTRACT_AGGREGATE_SHA256 && exact(metrics.executableGuard?.sources, CODE_CONTRACT_SOURCES), `Executable guard source binding mismatch: ${directory}`);
  assert(metrics.executableGuard?.verification?.focusedTests === 68 && metrics.executableGuard?.verification?.guardTests === 39 && metrics.executableGuard?.verification?.guardStatus === 'passed', `Executable guard verification mismatch: ${directory}`);
  assert(metrics.executableGuard?.verification?.definitions === 108 && metrics.executableGuard?.verification?.uniqueDefinitions === 106, `Executable token definition contract mismatch: ${directory}`);
  assert(metrics.executableGuard?.verification?.serialTestFiles === 41 && metrics.executableGuard?.verification?.serialTests === 452 && metrics.executableGuard?.verification?.serialExitCode === 0, `Executable serial test gate mismatch: ${directory}`);
  assert(metrics.executableGuard?.verification?.guardSourcePrePostStable === true && metrics.executableGuard?.verification?.targetedVueTscStatus === 'passed' && metrics.executableGuard?.verification?.browserStatus === 'passed', `Executable stability/browser/type gate mismatch: ${directory}`);
  assert(metrics.executableGuard?.verification?.buildModules === 2153 && metrics.executableGuard?.verification?.buildStatus === 'passed' && metrics.executableGuard?.verification?.protectedDiffCount === 0 && metrics.executableGuard?.verification?.protectedDiffSha256 === 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855' && metrics.executableGuard?.verification?.localHarnessClaim === false, `Executable build/protected-diff boundary mismatch: ${directory}`);
  assert(metrics.integrity?.domEqual === true && metrics.integrity?.auditEqual === true && metrics.integrity?.postCaptureRemeasurement === true, `Pre/post integrity mismatch: ${directory}`);
  assert(exact(metrics.assertions?.map((item) => item.id), EXPECTED_ASSERTION_IDS) && metrics.assertions.every((item) => item.passed === true), `Assertion registry/result mismatch: ${directory}`);
  assert(metrics.assertionSummary?.total === 25 && metrics.assertionSummary?.passed === 25 && metrics.assertionSummary?.failed === 0, `Assertion summary mismatch: ${directory}`);
  assert(exact(metrics.outputSet?.files, EXACT_OUTPUT_FILES) && metrics.outputSet?.pngCount === 6 && metrics.outputSet?.metricsCount === 1, `Declared output set mismatch: ${directory}`);
  assert(Array.isArray(metrics.captures) && metrics.captures.length === CAPTURE_SPECS.length, `Capture count mismatch: ${directory}`);
  for (const capture of metrics.captures) {
    const filePath = path.join(directory, capture.file);
    assert(CAPTURE_SPECS.some((spec) => spec.file === capture.file && spec.selector === capture.selector), `Unexpected capture: ${capture.file}`);
    const stat = fs.statSync(filePath);
    const dimensions = pngDimensions(filePath);
    const visualStats = pngVisualStats(filePath);
    assert(stat.size === capture.bytes && sha256File(filePath) === capture.sha256, `Capture hash/byte mismatch: ${capture.file}`);
    assert(dimensions.width === capture.width && dimensions.height === capture.height, `Capture dimensions mismatch: ${capture.file}`);
    assert(visualStats.uniqueColorCount >= 8 && visualStats.changedPixelCount >= 100 && exact(visualStats, capture.visualStats), `Capture blank or visual stats mismatch: ${capture.file}`);
  }
  return metrics;
}

function removeDirectory(directory) {
  fs.rmSync(directory, { recursive: true, force: true });
}

function residueDirectories() {
  if (!fs.existsSync(ASSETS_DIR)) return [];
  return fs.readdirSync(ASSETS_DIR, { withFileTypes: true })
    .filter((entry) => entry.isDirectory() && (entry.name.startsWith('.local-evidence-staging-') || entry.name.startsWith('.local-evidence-backup-')))
    .map((entry) => path.join(ASSETS_DIR, entry.name))
    .sort();
}

function validateStrictlyStaleEvidenceForSupersession(directory) {
  assert(fs.existsSync(directory) && fs.statSync(directory).isDirectory(), `Stale evidence directory missing: ${directory}`);
  assert(exact(sortedDirectoryEntries(directory), EXACT_OUTPUT_FILES), `Stale evidence output set mismatch: ${directory}`);
  const metrics = JSON.parse(fs.readFileSync(path.join(directory, METRICS_FILE), 'utf8'));
  assert(metrics.schemaVersion === 1 && metrics.stage === '2' && metrics.status === 'passed', `Stale evidence schema/status mismatch: ${directory}`);
  assert(metrics.canonicalFigma?.fileKey === FIGMA_FREEZE.fileKey && metrics.canonicalFigma?.pageId === FIGMA_FREEZE.pageId && metrics.canonicalFigma?.rootId === FIGMA_FREEZE.rootId && metrics.canonicalFigma?.frozenAt === FIGMA_FREEZE.frozenAt, `Stale evidence canonical identity mismatch: ${directory}`);
  assert(metrics.directFigma?.auditJson?.sha256 === DIRECT_AUDIT_SHA256 && metrics.directFigma?.exportAggregateSha256 === DIRECT_EXPORT_AGGREGATE_SHA256, `Stale evidence direct binding mismatch: ${directory}`);
  assert(exact(metrics.assertions?.map((item) => item.id), EXPECTED_ASSERTION_IDS) && metrics.assertions.every((item) => item.passed === true), `Stale evidence assertions mismatch: ${directory}`);
  assert(metrics.integrity?.domEqual === true && metrics.integrity?.preDomSha256 === metrics.integrity?.postDomSha256 && metrics.integrity?.auditEqual === true && metrics.integrity?.preAuditSha256 === metrics.integrity?.postAuditSha256, `Stale evidence pre/post integrity mismatch: ${directory}`);
  assert(exact(metrics.integrity?.consoleErrors, []) && exact(metrics.integrity?.pageErrors, []) && exact(metrics.integrity?.failedRequests, []) && exact(metrics.integrity?.blockedNetworkRequests, []), `Stale evidence browser error boundary mismatch: ${directory}`);
  assert(exact(metrics.outputSet?.files, EXACT_OUTPUT_FILES) && metrics.outputSet?.pngCount === 6 && metrics.outputSet?.metricsCount === 1, `Stale evidence declared output set mismatch: ${directory}`);
  assert(Array.isArray(metrics.captures) && metrics.captures.length === CAPTURE_SPECS.length, `Stale evidence capture count mismatch: ${directory}`);
  for (const capture of metrics.captures) {
    const filePath = path.join(directory, capture.file);
    const dimensions = pngDimensions(filePath);
    const visualStats = pngVisualStats(filePath);
    assert(fs.statSync(filePath).size === capture.bytes && sha256File(filePath) === capture.sha256, `Stale evidence capture hash/byte mismatch: ${capture.file}`);
    assert(dimensions.width === capture.width && dimensions.height === capture.height && exact(visualStats, capture.visualStats), `Stale evidence capture geometry/visual mismatch: ${capture.file}`);
    assert(visualStats.uniqueColorCount >= 8 && visualStats.changedPixelCount >= 100, `Stale evidence capture blank: ${capture.file}`);
  }
  const currentHtmlSha256 = sha256File(HTML_PATH);
  const recordedHtmlSha256 = metrics.localEvidence?.sourceHtml?.sha256;
  const completedMs = Date.parse(metrics.completedAt);
  const sourceMtimeMs = Math.max(fs.statSync(HTML_PATH).mtimeMs, fs.statSync(__filename).mtimeMs);
  assert(typeof recordedHtmlSha256 === 'string' && recordedHtmlSha256 !== currentHtmlSha256, `Published evidence is invalid but not stale by HTML hash: ${directory}`);
  assert(Number.isFinite(completedMs) && completedMs < sourceMtimeMs, `Published evidence is invalid but not stale by source chronology: ${directory}`);
  return { runId: metrics.runId, completedAt: metrics.completedAt, recordedHtmlSha256, currentHtmlSha256, sourceMtime: new Date(sourceMtimeMs).toISOString() };
}

function recoverEvidenceBeforeDependencyResolution() {
  fs.mkdirSync(ASSETS_DIR, { recursive: true });
  const entries = fs.readdirSync(ASSETS_DIR, { withFileTypes: true });
  const stagingDirs = entries.filter((entry) => entry.isDirectory() && entry.name.startsWith('.local-evidence-staging-')).map((entry) => path.join(ASSETS_DIR, entry.name));
  const backupDirs = entries.filter((entry) => entry.isDirectory() && entry.name.startsWith('.local-evidence-backup-')).map((entry) => path.join(ASSETS_DIR, entry.name));
  for (const staging of stagingDirs) removeDirectory(staging);

  let currentValid = false;
  let currentError = null;
  if (fs.existsSync(PUBLISHED_DIR)) {
    try { validateEvidenceDirectory(PUBLISHED_DIR); currentValid = true; } catch (error) { currentError = error; }
  }
  const validBackups = [];
  const invalidBackups = [];
  for (const backup of backupDirs) {
    try { validateEvidenceDirectory(backup); validBackups.push(backup); } catch (error) { invalidBackups.push({ backup, error }); }
  }
  if (currentValid) {
    for (const backup of backupDirs) removeDirectory(backup);
    return { action: stagingDirs.length || backupDirs.length ? 'cleaned-with-valid-current' : 'clean', recovered: false };
  }
  assert(validBackups.length <= 1, `Fail-closed recovery found multiple valid backups: ${validBackups.join(', ')}`);
  if (validBackups.length === 1) {
    const backup = validBackups[0];
    const invalidCurrent = fs.existsSync(PUBLISHED_DIR) ? `${PUBLISHED_DIR}.invalid-recovery-${process.pid}` : null;
    if (invalidCurrent) fs.renameSync(PUBLISHED_DIR, invalidCurrent);
    fs.renameSync(backup, PUBLISHED_DIR);
    try { validateEvidenceDirectory(PUBLISHED_DIR); } catch (error) {
      if (fs.existsSync(PUBLISHED_DIR)) fs.renameSync(PUBLISHED_DIR, backup);
      if (invalidCurrent && fs.existsSync(invalidCurrent)) fs.renameSync(invalidCurrent, PUBLISHED_DIR);
      throw new Error(`Fail-closed recovery validation failed: ${error.message}`);
    }
    if (invalidCurrent) removeDirectory(invalidCurrent);
    for (const item of invalidBackups) removeDirectory(item.backup);
    return { action: 'restored-valid-backup', recovered: true };
  }
  if (fs.existsSync(PUBLISHED_DIR)) {
    assert(invalidBackups.length === 0, `Fail-closed recovery: stale current cannot be superseded while invalid backups exist: ${invalidBackups.map((item) => item.backup).join(', ')}`);
    let stale;
    try { stale = validateStrictlyStaleEvidenceForSupersession(PUBLISHED_DIR); } catch (error) {
      throw new Error(`Fail-closed recovery: invalid published evidence is not a strictly validated stale derivative: ${currentError?.message || 'unknown error'}; stale check: ${error.message}`);
    }
    removeDirectory(PUBLISHED_DIR);
    assert(!fs.existsSync(PUBLISHED_DIR), `Strictly stale evidence could not be removed: ${PUBLISHED_DIR}`);
    return { action: 'removed-strictly-validated-stale-current', recovered: false, stale };
  }
  assert(invalidBackups.length === 0, `Fail-closed recovery: only invalid backups exist: ${invalidBackups.map((item) => item.error.message).join(' | ')}`);
  return { action: stagingDirs.length ? 'removed-stale-staging' : 'nothing-to-recover', recovered: false };
}

function resolvePlaywright() {
  const candidates = [
    process.env.UIUX_PLAYWRIGHT_MODULE,
    path.join(REPO_ROOT, 'frontend', 'node_modules', 'playwright'),
    '/root/trading-bot/trading_bot/frontend/node_modules/playwright',
    'playwright',
  ].filter(Boolean);
  const errors = [];
  for (const candidate of candidates) {
    try { return { module: require(candidate), resolvedFrom: require.resolve(candidate) }; } catch (error) { errors.push(`${candidate}: ${error.code || error.message}`); }
  }
  throw new Error(`Playwright dependency unavailable after recovery: ${errors.join(' | ')}`);
}

function resolveFonts() {
  const roots = [
    process.env.UIUX_VAZIRMATN_FONT_ROOT,
    path.join(REPO_ROOT, 'frontend', 'node_modules', 'vazirmatn', 'fonts', 'webfonts'),
    '/root/trading-bot/trading_bot/frontend/node_modules/vazirmatn/fonts/webfonts',
  ].filter(Boolean);
  const weights = [
    { weight: 400, file: 'Vazirmatn-Regular.woff2' },
    { weight: 500, file: 'Vazirmatn-Medium.woff2' },
    { weight: 600, file: 'Vazirmatn-SemiBold.woff2' },
    { weight: 700, file: 'Vazirmatn-Bold.woff2' },
  ];
  for (const root of roots) {
    const files = weights.map((item) => ({ ...item, path: path.join(root, item.file) }));
    if (files.every((item) => fs.existsSync(item.path))) {
      return {
        root,
        fonts: files.map((item) => {
          const buffer = fs.readFileSync(item.path);
          return { ...item, bytes: buffer.length, sha256: sha256Buffer(buffer), data: buffer.toString('base64') };
        }),
      };
    }
  }
  throw new Error(`Vazirmatn 400/500/600/700 unavailable after recovery; checked ${roots.join(', ')}`);
}

function directExportAggregate(records) {
  const projection = records.map(({ nodeId, path: filePath, width, height, bytes, sha256 }) => ({ nodeId, path: filePath, width, height, bytes, sha256 }));
  return sha256Buffer(Buffer.from(JSON.stringify(projection)));
}

function codeContractAggregate(records) {
  return sha256Buffer(Buffer.from(JSON.stringify(records.map(({ path: filePath, bytes, sha256 }) => ({ path: filePath, bytes, sha256 })))));
}

function validateCanonicalInputs() {
  assert(fs.existsSync(HTML_PATH) && fs.statSync(HTML_PATH).isFile(), `Evidence HTML missing: ${HTML_PATH}`);
  const actualTopLevelPngs = fs.readdirSync(ASSETS_DIR, { withFileTypes: true }).filter((entry) => entry.isFile() && entry.name.endsWith('.png')).map((entry) => entry.name).sort();
  const expectedTopLevelPngs = DIRECT_EXPORTS.map((item) => item.path).sort();
  assert(exact(actualTopLevelPngs, expectedTopLevelPngs), `Direct Figma PNG set mismatch: ${actualTopLevelPngs.join(', ')}`);

  const validatedExports = DIRECT_EXPORTS.map((expected) => {
    const filePath = path.join(ASSETS_DIR, expected.path);
    const dimensions = pngDimensions(filePath);
    const stat = fs.statSync(filePath);
    const sha256 = sha256File(filePath);
    const visualStats = pngVisualStats(filePath);
    assert(dimensions.width === expected.width && dimensions.height === expected.height, `Direct export dimensions mismatch: ${expected.path}`);
    assert(stat.size === expected.bytes && sha256 === expected.sha256, `Direct export bytes/hash mismatch: ${expected.path}`);
    assert(visualStats.uniqueColorCount >= 8 && visualStats.changedPixelCount >= 100, `Direct export blank or degenerate: ${expected.path}`);
    return { ...expected, visualStats, validated: true };
  });
  const aggregate = directExportAggregate(validatedExports);
  assert(aggregate === DIRECT_EXPORT_AGGREGATE_SHA256, `Direct export aggregate mismatch: ${aggregate}`);

  const auditPath = path.join(ASSETS_DIR, DIRECT_AUDIT_FILE);
  assert(fs.existsSync(auditPath) && fs.statSync(auditPath).isFile(), `Direct Figma audit missing: ${auditPath}`);
  const auditBuffer = fs.readFileSync(auditPath);
  const auditSha256 = sha256Buffer(auditBuffer);
  assert(auditSha256 === DIRECT_AUDIT_SHA256, `Direct audit hash mismatch: ${auditSha256}`);
  const audit = JSON.parse(auditBuffer.toString('utf8'));
  assert(audit.schema === 3 && audit.stage === '2' && audit.artifactKind === 'post-freeze-direct-figma-audit' && audit.status === 'passed', 'Direct audit schema/stage/status mismatch');
  assert(audit.fileKey === FIGMA_FREEZE.fileKey && audit.pageId === FIGMA_FREEZE.pageId && audit.rootId === FIGMA_FREEZE.rootId, 'Direct audit canonical identity mismatch');
  assert(audit.frozenAt === FIGMA_FREEZE.frozenAt && audit.auditedAt === FIGMA_FREEZE.auditedAt, 'Direct audit freeze/audit timestamp mismatch');
  assert(audit.preCaptureRereadAt === FIGMA_FREEZE.preCaptureRereadAt && audit.postCaptureRereadAt === FIGMA_FREEZE.postCaptureRereadAt && audit.inventoryDigestRereadAt === FIGMA_FREEZE.inventoryDigestRereadAt, 'Direct audit reread timestamp mismatch');
  assert(audit.freezeMetadata?.status === 'figma_frozen_external_evidence_pending' && audit.freezeMetadata?.exact === true, 'Direct audit freeze metadata mismatch');
  assert(exact(audit.freezeMetadata?.pageKeys, ['stage2_freeze']) && exact(audit.freezeMetadata?.rootKeys, ['stage2_freeze', 'contract']), 'Direct audit metadata key mismatch');
  assert(audit.captureImmutability?.equal === true && audit.captureImmutability?.pre?.canonicalBytes === 612614 && audit.captureImmutability?.post?.canonicalBytes === 612614, 'Direct audit canonical byte equality mismatch');
  assert(audit.captureImmutability?.pre?.sha256 === CANONICAL_TREE_SHA256 && audit.captureImmutability?.post?.sha256 === CANONICAL_TREE_SHA256, 'Direct audit canonical tree hash mismatch');
  assert(audit.inventoryDigests?.combined?.bytes === 78068 && audit.inventoryDigests?.combined?.sha256 === '9c9fa984188bee28217a1052f42186b2815ec3a9d91c431c8d0649c8e637c829', 'Direct audit inventory aggregate mismatch');
  assert(audit.metrics?.root?.width === 1440 && audit.metrics?.root?.height === 13028 && audit.metrics?.root?.sections?.length === 7, 'Direct audit root geometry mismatch');
  assert(exact(audit.metrics?.foundations?.collections?.map((item) => item.variables), [20, 26, 19]) && audit.metrics?.foundations?.variables === 65, 'Direct audit foundation inventory mismatch');
  assert(audit.metrics?.foundations?.semanticAliases === 26 && audit.metrics?.foundations?.brokenAliases === 0 && audit.metrics?.foundations?.webCodeSyntaxExact === 65 && audit.metrics?.foundations?.allScopeViolations === 0, 'Direct audit variable integrity mismatch');
  assert(audit.metrics?.foundations?.textStyles === 10 && audit.metrics?.foundations?.effectStyles === 2 && exact(audit.metrics?.foundations?.avatarInitial?.boundNodeIds, ['51:16', '51:27']), 'Direct audit style inventory mismatch');
  assert(audit.metrics?.foundations?.borderDefault?.hex === '#8091A3' && audit.metrics?.foundations?.borderDefault?.contrastOnWhite === 3.232 && audit.metrics?.foundations?.borderDefault?.contrastOnPage === 3.006, 'Direct audit border contrast mismatch');
  assert(audit.metrics?.components?.sets === 12 && audit.metrics?.components?.variants === 56 && exact(audit.metrics?.components?.sourceSets?.map((item) => item.variants), [6, 4, 3, 2, 6, 12, 3, 2, 2, 2, 8, 6]), 'Direct audit component inventory mismatch');
  assert(audit.metrics?.components?.catalog?.topLevelReferences === 56 && audit.metrics?.components?.catalog?.nestedReferences === 6 && audit.metrics?.components?.catalog?.linkedReferences === 62 && audit.metrics?.components?.catalog?.detachedReferences === 0, 'Direct audit catalog linkage mismatch');
  assert(audit.metrics?.components?.bottomNavigation?.height === 80 && audit.metrics?.components?.bottomNavigation?.heightVariableId === 'VariableID:39:40' && audit.metrics?.components?.bottomNavigation?.exactBoundVariantCount === 6, 'Direct audit Bottom Navigation mismatch');
  assert(exact(audit.metrics?.iconScale?.glyphs?.map((item) => item.size), [16, 20, 24]) && audit.metrics?.iconScale?.textGlyphCount === 0, 'Direct audit icon scale mismatch');
  assert(audit.metrics?.routes?.total === 29 && audit.metrics?.routes?.enabled === 0 && audit.metrics?.routes?.v2ScopeOff === 29 && exact(audit.metrics?.routes?.fullProtected, ['/market', '/chat', '/admin/channels', '/share-receive']) && exact(audit.metrics?.routes?.mixedProtected, ['/', '/admin/messages', '/admin/system']), 'Direct audit route contract mismatch');
  assert(exact(audit.metrics?.responsive?.mobile?.map((item) => item.width), [360, 375, 390, 414, 430]) && audit.metrics?.responsive?.desktop?.width === 1440 && audit.metrics?.responsive?.desktop?.height === 900, 'Direct audit responsive mismatch');
  const qa = audit.metrics?.qa;
  assert(qa?.touchTargetsChecked === 66 && qa?.touchTargetFailures === 0 && qa?.focusStates === 10 && qa?.focusStrokePx === 3 && qa?.overflowCount === 0 && qa?.clippingCount === 0 && qa?.productActivationCount === 0 && qa?.protectedInteriorCount === 0 && qa?.reviewerResidueCount === 0 && qa?.failedDraftCount === 0 && qa?.placeholderCount === 0, 'Direct audit QA mismatch');
  assert(audit.passedCount === 25 && audit.failedCount === 0 && audit.figmaAssertionStatus === '25/25 passed', 'Direct audit assertion summary mismatch');
  assert(exact(audit.assertions?.map((item) => item.id), EXPECTED_ASSERTION_IDS) && audit.assertions.every((item, index) => item.order === index + 1 && item.status === 'passed'), 'Direct audit assertion IDs/order/status mismatch');
  assert(exact(audit.directEvidence, DIRECT_EXPORTS) && audit.directEvidenceAggregateSha256 === DIRECT_EXPORT_AGGREGATE_SHA256, 'Direct audit export declaration mismatch');
  assert(exact(audit.claimBoundary, { figmaCanonical: true, localDerivativeCanonical: false, runtimeBehaviorProven: false, protectedGitDiffProven: false, sitesProven: false, externalTechnicalGatesRequired: true }), 'Direct audit claim boundary mismatch');

  const validatedCodeSources = CODE_CONTRACT_SOURCES.map((expected) => {
    const filePath = path.join(REPO_ROOT, expected.path);
    assert(fs.existsSync(filePath) && fs.statSync(filePath).isFile(), `Executable guard source missing: ${expected.path}`);
    const stat = fs.statSync(filePath);
    const sha256 = sha256File(filePath);
    assert(stat.size === expected.bytes && sha256 === expected.sha256, `Executable guard source drift: ${expected.path} expected ${expected.bytes}/${expected.sha256}, received ${stat.size}/${sha256}`);
    return { ...expected };
  });
  const codeAggregate = codeContractAggregate(validatedCodeSources);
  assert(codeAggregate === CODE_CONTRACT_AGGREGATE_SHA256, `Executable guard aggregate mismatch: ${codeAggregate}`);
  const tokenContract = JSON.parse(fs.readFileSync(path.join(REPO_ROOT, 'frontend/src/design-system-v2/canonical-token-contract.json'), 'utf8'));
  const canonicalNames = Object.keys(tokenContract.canonicalTokens || {});
  const implementationNames = (tokenContract.implementationDefinitions || []).map((item) => item.name);
  const definitionNames = [...canonicalNames, ...implementationNames];
  assert(tokenContract.schemaVersion === 1 && tokenContract.stage === 2 && tokenContract.canonicalTokenCount === 65, 'Executable canonical token contract header mismatch');
  assert(exact(tokenContract.canonicalCategoryCounts, { primitives: 20, semantic: 26, dimensions: 19 }) && canonicalNames.length === 65, 'Executable canonical category/token count mismatch');
  assert(implementationNames.length === 43 && definitionNames.length === 108 && new Set(definitionNames).size === 106, 'Executable definition/tuple count mismatch');
  const guardTestSource = fs.readFileSync(path.join(REPO_ROOT, 'frontend/scripts/design-system-v2-guard.test.mjs'), 'utf8');
  const guardTestCount = (guardTestSource.match(/^  it\(/gm) || []).length;
  assert(guardTestCount === EXECUTABLE_GUARD_VERIFICATION.guardTests, `Executable guard test count mismatch: ${guardTestCount}`);

  return {
    html: { path: HTML_FILE, bytes: fs.statSync(HTML_PATH).size, sha256: sha256File(HTML_PATH) },
    directAudit: { path: `assets/${DIRECT_AUDIT_FILE}`, bytes: auditBuffer.length, sha256: auditSha256, parsed: audit },
    directExports: validatedExports,
    directExportAggregateSha256: aggregate,
    executableGuard: { sources: validatedCodeSources, sourceAggregateSha256: codeAggregate, verification: EXECUTABLE_GUARD_VERIFICATION },
  };
}

async function canonicalDomSnapshot(page) {
  return page.evaluate(() => {
    const serialize = (node) => {
      if (node.nodeType === Node.TEXT_NODE) return { type: 'text', value: node.nodeValue };
      if (node.nodeType === Node.COMMENT_NODE) return { type: 'comment', value: node.nodeValue };
      if (node.nodeType !== Node.ELEMENT_NODE) return { type: `node-${node.nodeType}` };
      return {
        type: node.tagName.toLowerCase(),
        attributes: [...node.attributes].map((attribute) => [attribute.name, attribute.value]).sort((a, b) => a[0].localeCompare(b[0])),
        children: [...node.childNodes].map(serialize),
      };
    };
    return JSON.stringify(serialize(document.documentElement));
  });
}

async function auditPage(page, context) {
  return page.evaluate(({ expectedAssertionIds, expectedRoutes, directExports, freeze, directAuditSha256, aggregateSha256, canonicalTreeSha256, canonicalInputsValid, codeContractSources, codeAggregateSha256, guardVerification }) => {
    const q = (selector, root = document) => root.querySelector(selector);
    const qa = (selector, root = document) => [...root.querySelectorAll(selector)];
    const exact = (actual, expected) => JSON.stringify(actual) === JSON.stringify(expected);
    const number = (value) => Number(value);
    const body = document.body;
    const assertions = [];
    const record = (id, passed, evidence) => assertions.push({ id, passed: Boolean(passed), evidence });

    const registry = qa('[data-assertion-id]').map((node) => node.dataset.assertionId);
    if (!exact(registry, expectedAssertionIds) || new Set(registry).size !== expectedAssertionIds.length) throw new Error(`Assertion registry mismatch: ${JSON.stringify(registry)}`);

    const guardContract = q('#stage2-guard-rollback');
    const renderedCodeSources = qa('[data-code-contract-source]').map((node) => ({ path: node.dataset.codeContractSource, bytes: number(node.dataset.sourceBytes), sha256: node.dataset.sourceSha256 }));
    const codeBindingEvidence = {
      sourceCount: number(body.dataset.codeContractSourceCount),
      aggregateSha256: body.dataset.codeContractAggregateSha256,
      sources: renderedCodeSources,
      canonicalTokens: number(guardContract?.dataset.canonicalTokenCount),
      implementationTuples: number(guardContract?.dataset.implementationTupleCount),
      definitions: number(guardContract?.dataset.contractDefinitionCount),
      uniqueDefinitions: number(guardContract?.dataset.contractUniqueDefinitionCount),
      focusedTestFiles: number(guardContract?.dataset.focusedTestFileCount),
      focusedTests: number(guardContract?.dataset.focusedTestCount),
      focusedDurationSeconds: number(guardContract?.dataset.focusedDurationSeconds),
      focusedExitCode: number(guardContract?.dataset.focusedExitCode),
      guardTests: number(guardContract?.dataset.guardTestCount),
      guardCssFiles: number(guardContract?.dataset.guardCssFileCount),
      guardRoutes: number(guardContract?.dataset.guardRouteCount),
      serialTestFiles: number(guardContract?.dataset.serialTestFileCount),
      serialTests: number(guardContract?.dataset.serialTestCount),
      serialVitestDurationSeconds: number(guardContract?.dataset.serialVitestDurationSeconds),
      serialWallMs: number(guardContract?.dataset.serialWallMs),
      serialExitCode: number(guardContract?.dataset.serialExitCode),
      guardSourcePrePostStable: guardContract?.dataset.guardSourcePrePostStable === 'true',
      focusedStatus: guardContract?.dataset.focusedTestsStatus,
      guardStatus: guardContract?.dataset.guardUiStatus,
      browserStatus: guardContract?.dataset.browserStatus,
      targetedVueTscStatus: guardContract?.dataset.targetedVueTscStatus,
      targetedVueTscExitCode: number(guardContract?.dataset.targetedVueTscExitCode),
      lintStatus: guardContract?.dataset.lintStatus,
      prettierStatus: guardContract?.dataset.prettierStatus,
      buildStatus: guardContract?.dataset.buildStatus,
      buildModules: number(guardContract?.dataset.buildModuleCount),
      buildDurationSeconds: number(guardContract?.dataset.buildDurationSeconds),
      protectedDiffCount: number(guardContract?.dataset.protectedDiffCount),
      protectedDiffSha256: guardContract?.dataset.protectedDiffSha256,
      protectedDiffUpstreamStatus: guardContract?.dataset.protectedDiffUpstreamStatus,
      localHarnessClaim: guardContract?.dataset.externalGateLocalHarnessClaim === 'true',
      focusedCommand: guardContract?.dataset.focusedCommand,
      guardCommand: guardContract?.dataset.guardCommand,
    };
    const expectedCodeBinding = {
      sourceCount: codeContractSources.length,
      aggregateSha256: codeAggregateSha256,
      sources: codeContractSources,
      canonicalTokens: guardVerification.canonicalTokens,
      implementationTuples: guardVerification.implementationTuples,
      definitions: guardVerification.definitions,
      uniqueDefinitions: guardVerification.uniqueDefinitions,
      focusedTestFiles: guardVerification.focusedTestFiles,
      focusedTests: guardVerification.focusedTests,
      focusedDurationSeconds: guardVerification.focusedDurationSeconds,
      focusedExitCode: guardVerification.focusedExitCode,
      guardTests: guardVerification.guardTests,
      guardCssFiles: guardVerification.guardCssFiles,
      guardRoutes: guardVerification.guardRoutes,
      serialTestFiles: guardVerification.serialTestFiles,
      serialTests: guardVerification.serialTests,
      serialVitestDurationSeconds: guardVerification.serialVitestDurationSeconds,
      serialWallMs: guardVerification.serialWallMs,
      serialExitCode: guardVerification.serialExitCode,
      guardSourcePrePostStable: guardVerification.guardSourcePrePostStable,
      focusedStatus: guardVerification.focusedStatus,
      guardStatus: guardVerification.guardStatus,
      browserStatus: guardVerification.browserStatus,
      targetedVueTscStatus: guardVerification.targetedVueTscStatus,
      targetedVueTscExitCode: guardVerification.targetedVueTscExitCode,
      lintStatus: guardVerification.lintStatus,
      prettierStatus: guardVerification.prettierStatus,
      buildStatus: guardVerification.buildStatus,
      buildModules: guardVerification.buildModules,
      buildDurationSeconds: guardVerification.buildDurationSeconds,
      protectedDiffCount: guardVerification.protectedDiffCount,
      protectedDiffSha256: guardVerification.protectedDiffSha256,
      protectedDiffUpstreamStatus: guardVerification.protectedDiffUpstreamStatus,
      localHarnessClaim: guardVerification.localHarnessClaim,
      focusedCommand: guardVerification.focusedCommand,
      guardCommand: guardVerification.guardCommand,
    };
    if (!canonicalInputsValid || !exact(codeBindingEvidence, expectedCodeBinding)) throw new Error(`Executable guard HTML/source binding mismatch: ${JSON.stringify(codeBindingEvidence)}`);

    const freezeEvidence = {
      fileKey: body.dataset.canonicalFileKey,
      pageId: body.dataset.canonicalPageId,
      rootId: body.dataset.canonicalRootId,
      frozenAt: body.dataset.frozenAt,
      auditedAt: body.dataset.auditedAt,
      status: q('[data-freeze-status]')?.dataset.freezeStatus,
      directAuditSha256: body.dataset.directAuditSha256,
    };
    record(expectedAssertionIds[0], freezeEvidence.fileKey === freeze.fileKey && freezeEvidence.pageId === freeze.pageId && freezeEvidence.rootId === freeze.rootId && freezeEvidence.frozenAt === freeze.frozenAt && freezeEvidence.auditedAt === freeze.auditedAt && freezeEvidence.status === 'figma_frozen_external_evidence_pending' && freezeEvidence.directAuditSha256 === directAuditSha256, freezeEvidence);

    const treeEvidence = { htmlTreeSha256: body.dataset.canonicalTreeSha256, expected: canonicalTreeSha256, canonicalInputsValid };
    record(expectedAssertionIds[1], treeEvidence.htmlTreeSha256 === canonicalTreeSha256 && canonicalInputsValid, treeEvidence);

    const overview = q('#stage2-overview');
    const rootEvidence = { width: number(overview?.dataset.rootWidth), height: number(overview?.dataset.rootHeight), sections: number(overview?.dataset.sectionCount), captureSections: qa('[data-capture-section]').length };
    record(expectedAssertionIds[2], rootEvidence.width === 1440 && rootEvidence.height === 13028 && rootEvidence.sections === 7 && rootEvidence.captureSections === 6, rootEvidence);

    const foundations = q('#stage2-foundations');
    const collectionCounts = q('[data-foundation-collection-counts]')?.dataset.foundationCollectionCounts.split(',').map(Number);
    record(expectedAssertionIds[3], exact(collectionCounts, [20, 26, 19]), { collectionCounts });
    record(expectedAssertionIds[4], number(foundations?.dataset.variableCount) === 65 && number(foundations?.dataset.webSyntaxExactCount) === 65, { variables: number(foundations?.dataset.variableCount), webSyntaxExact: number(foundations?.dataset.webSyntaxExactCount) });
    record(expectedAssertionIds[5], collectionCounts?.[1] === 26 && number(foundations?.dataset.brokenAliasCount) === 0, { semanticAliases: collectionCounts?.[1], brokenAliases: number(foundations?.dataset.brokenAliasCount) });
    record(expectedAssertionIds[6], number(foundations?.dataset.allScopeViolationCount) === 0, { allScopeViolations: number(foundations?.dataset.allScopeViolationCount) });

    const avatar = q('[data-avatar-style-id]');
    const avatarEvidence = { textStyles: number(foundations?.dataset.textStyleCount), styleId: avatar?.dataset.avatarStyleId, boundNodeIds: avatar?.dataset.avatarBoundNodeIds.split(',') };
    record(expectedAssertionIds[7], avatarEvidence.textStyles === 10 && avatarEvidence.styleId === 'S:b2c168d4aaf57efabc1e01386680792f7c983c42,' && exact(avatarEvidence.boundNodeIds, ['51:16', '51:27']), avatarEvidence);
    record(expectedAssertionIds[8], number(foundations?.dataset.effectStyleCount) === 2, { effectStyles: number(foundations?.dataset.effectStyleCount) });

    const border = q('[data-border-token]');
    const contrast = q('[data-border-contrast-white]');
    const borderEvidence = { hex: border?.dataset.borderToken, white: number(contrast?.dataset.borderContrastWhite), page: number(contrast?.dataset.borderContrastPage) };
    record(expectedAssertionIds[9], borderEvidence.hex === '#8091A3' && borderEvidence.white === 3.232 && borderEvidence.page === 3.006, borderEvidence);

    const components = q('#stage2-component-catalog');
    const sourceSets = qa('[data-component-set-id]').map((node) => ({ id: node.dataset.componentSetId, variants: number(node.dataset.variantCount) }));
    const expectedSourceSets = [
      ['48:14', 6], ['49:14', 4], ['50:26', 3], ['51:33', 2], ['52:46', 6], ['77:610', 12],
      ['78:566', 3], ['80:574', 2], ['81:566', 2], ['121:14', 2], ['122:1327', 8], ['123:1330', 6],
    ].map(([id, variants]) => ({ id, variants }));
    const componentEvidence = { sets: number(components?.dataset.componentSets), variants: number(components?.dataset.componentVariants), sourceSets };
    record(expectedAssertionIds[10], componentEvidence.sets === 12 && componentEvidence.variants === 56 && exact(sourceSets, expectedSourceSets) && sourceSets.reduce((sum, item) => sum + item.variants, 0) === 56, componentEvidence);

    const catalogEvidence = { top: number(components?.dataset.catalogTopLevel), nested: number(components?.dataset.catalogNested), linked: number(components?.dataset.catalogLinked), detached: number(components?.dataset.catalogDetached) };
    record(expectedAssertionIds[11], catalogEvidence.top === 56 && catalogEvidence.nested === 6 && catalogEvidence.linked === 62 && catalogEvidence.detached === 0, catalogEvidence);

    const bottomNav = q('[data-bottom-nav-variant-ids]');
    const bottomNavEvidence = { ids: bottomNav?.dataset.bottomNavVariantIds.split(','), height: number(bottomNav?.dataset.bottomNavHeight), variableId: bottomNav?.dataset.bottomNavHeightVariableId };
    record(expectedAssertionIds[12], exact(bottomNavEvidence.ids, ['52:8', '52:29', '127:14', '127:35', '170:14', '170:35']) && bottomNavEvidence.height === 80 && bottomNavEvidence.variableId === 'VariableID:39:40', bottomNavEvidence);

    const iconContent = q('[data-icon-content-id]');
    const glyphs = qa('[data-icon-id]').map((node) => ({ id: node.dataset.iconId, size: number(node.dataset.iconSize), sizeVariableId: node.dataset.sizeVariableId, colorVariableId: node.dataset.colorVariableId, strokeVariableId: node.dataset.strokeVariableId }));
    const expectedGlyphs = [
      { id: '267:24', size: 16, sizeVariableId: 'VariableID:39:28', colorVariableId: 'VariableID:39:15', strokeVariableId: 'VariableID:39:41' },
      { id: '267:29', size: 20, sizeVariableId: 'VariableID:39:29', colorVariableId: 'VariableID:39:15', strokeVariableId: 'VariableID:39:41' },
      { id: '267:34', size: 24, sizeVariableId: 'VariableID:39:30', colorVariableId: 'VariableID:39:15', strokeVariableId: 'VariableID:39:41' },
    ];
    const iconEvidence = { contentId: iconContent?.dataset.iconContentId, contentWidth: number(iconContent?.dataset.iconContentWidth), contentHeight: number(iconContent?.dataset.iconContentHeight), layout: iconContent?.dataset.iconLayout, verticalSizing: iconContent?.dataset.iconVerticalSizing, textGlyphCount: number(iconContent?.dataset.textGlyphCount), glyphs };
    record(expectedAssertionIds[13], iconEvidence.contentId === '267:19' && iconEvidence.contentWidth === 1278 && iconEvidence.contentHeight === 134 && iconEvidence.layout === 'HORIZONTAL' && iconEvidence.verticalSizing === 'HUG' && iconEvidence.textGlyphCount === 0 && exact(glyphs, expectedGlyphs), iconEvidence);

    const routeSection = q('#stage2-scope-routes');
    const routes = qa('[data-route-path]').map((node) => ({ path: node.dataset.routePath, protection: node.dataset.routeProtection, scope: node.dataset.v2Scope }));
    const routeProjection = routes.map(({ path, protection }) => ({ path, protection }));
    const routeEvidence = { declared: number(routeSection?.dataset.routeCount), enabled: number(routeSection?.dataset.routeEnabledCount), off: number(routeSection?.dataset.v2ScopeOffCount), routes };
    record(expectedAssertionIds[14], routeEvidence.declared === 29 && routeEvidence.enabled === 0 && routeEvidence.off === 29 && routes.length === 29 && new Set(routes.map((item) => item.path)).size === 29 && routes.every((item) => item.scope === 'off') && exact(routeProjection, expectedRoutes), routeEvidence);

    const protectedGroups = qa('[data-protected-route-kind]').map((node) => ({ kind: node.dataset.protectedRouteKind, routes: node.dataset.protectedRoutes.split(',') }));
    const full = protectedGroups.find((item) => item.kind === 'full')?.routes;
    const mixed = protectedGroups.find((item) => item.kind === 'mixed')?.routes;
    record(expectedAssertionIds[15], exact(full, ['/market', '/chat', '/admin/channels', '/share-receive']) && exact(routes.filter((item) => item.protection === 'full').map((item) => item.path), full), { full });
    record(expectedAssertionIds[16], exact(mixed, ['/', '/admin/messages', '/admin/system']) && exact(routes.filter((item) => item.protection === 'mixed').map((item) => item.path), mixed), { mixed });

    const responsive = q('#stage2-responsive');
    const mobiles = qa('.phone[data-source-width]').map((node) => ({ id: node.dataset.responsiveNodeId, width: number(node.dataset.sourceWidth) }));
    const expectedMobiles = [{ id: '223:7', width: 360 }, { id: '223:31', width: 375 }, { id: '223:55', width: 390 }, { id: '223:80', width: 414 }, { id: '223:104', width: 430 }];
    record(expectedAssertionIds[17], responsive?.dataset.mobileHeight === '620' && exact(responsive?.dataset.mobileWidths.split(',').map(Number), [360, 375, 390, 414, 430]) && exact(mobiles, expectedMobiles), { mobiles, height: responsive?.dataset.mobileHeight });
    const desktop = q('.desktop[data-responsive-node-id]');
    record(expectedAssertionIds[18], responsive?.dataset.desktopWidth === '1440' && responsive?.dataset.desktopHeight === '900' && desktop?.dataset.responsiveNodeId === '224:2', { id: desktop?.dataset.responsiveNodeId, width: responsive?.dataset.desktopWidth, height: responsive?.dataset.desktopHeight });

    const guard = q('#stage2-guard-rollback');
    const qaEvidence = { targets: number(guard?.dataset.touchTargets), failures: number(guard?.dataset.touchTargetFailures), focus: number(guard?.dataset.focusStates), stroke: number(guard?.dataset.focusStroke) };
    record(expectedAssertionIds[19], qaEvidence.targets === 66 && qaEvidence.failures === 0 && qaEvidence.focus === 10 && qaEvidence.stroke === 3, qaEvidence);
    record(expectedAssertionIds[20], number(guard?.dataset.overflowCount) === 0 && number(guard?.dataset.clippingCount) === 0, { overflow: number(guard?.dataset.overflowCount), clipping: number(guard?.dataset.clippingCount) });

    const boundaryEvidence = { productActivation: number(guard?.dataset.productActivationCount), declaredProtectedInteriors: number(guard?.dataset.protectedInteriorCount), renderedProtectedInteriors: qa('[data-protected-interior]').length, role: body.dataset.evidenceRole, canonical: body.dataset.canonical, runtimeBehaviorProven: body.dataset.runtimeBehaviorProven, protectedGitDiffProven: body.dataset.protectedGitDiffProven, sitesProven: body.dataset.sitesProven };
    record(expectedAssertionIds[21], boundaryEvidence.productActivation === 0 && boundaryEvidence.declaredProtectedInteriors === 0 && boundaryEvidence.renderedProtectedInteriors === 0 && boundaryEvidence.role === 'secondary-derivative' && boundaryEvidence.canonical === 'false' && boundaryEvidence.runtimeBehaviorProven === 'false' && boundaryEvidence.protectedGitDiffProven === 'false' && boundaryEvidence.sitesProven === 'false', boundaryEvidence);
    const residueEvidence = { reviewer: number(guard?.dataset.reviewerResidueCount), failedDraft: number(guard?.dataset.failedDraftCount), placeholder: number(guard?.dataset.placeholderCount) };
    record(expectedAssertionIds[22], residueEvidence.reviewer === 0 && residueEvidence.failedDraft === 0 && residueEvidence.placeholder === 0, residueEvidence);

    const sourceImages = qa('[data-source-export]').map((node) => {
      const image = q('img', node);
      return { file: node.dataset.sourceExport, complete: image?.complete, naturalWidth: image?.naturalWidth, naturalHeight: image?.naturalHeight };
    });
    const expectedImages = directExports.map((item) => ({ file: item.path, complete: true, naturalWidth: item.width, naturalHeight: item.height }));
    const exportEvidence = { declaredCount: number(q('[data-source-export-count]')?.dataset.sourceExportCount), aggregate: body.dataset.directExportAggregateSha256, images: sourceImages, canonicalInputsValid };
    record(expectedAssertionIds[23], exportEvidence.declaredCount === 8 && exportEvidence.aggregate === aggregateSha256 && exact(sourceImages, expectedImages) && canonicalInputsValid, exportEvidence);

    const optIn = routeSection?.dataset.optInSelectors.split('|');
    record(expectedAssertionIds[24], exact(optIn, ['[data-ui-system=v2]', '[data-ui-system=v2-portal]']) && qa('[data-ui-system="v2"], [data-ui-system="v2-portal"]').length === 0, { optIn, activatedNodes: qa('[data-ui-system="v2"], [data-ui-system="v2-portal"]').length });

    if (!exact(assertions.map((item) => item.id), expectedAssertionIds)) throw new Error('Audit emitted assertions out of contract order');
    return {
      assertions,
      passed: assertions.filter((item) => item.passed).length,
      failed: assertions.filter((item) => !item.passed).length,
      metrics: { root: rootEvidence, foundations: { collectionCounts, variables: number(foundations?.dataset.variableCount), sourceSets }, components: componentEvidence, catalog: catalogEvidence, bottomNavigation: bottomNavEvidence, iconScale: iconEvidence, routes: routeEvidence, protectedGroups, responsive: { mobiles, desktop: { id: desktop?.dataset.responsiveNodeId, width: number(responsive?.dataset.desktopWidth), height: number(responsive?.dataset.desktopHeight) } }, qa: qaEvidence, boundary: boundaryEvidence, directExports: exportEvidence, executableGuard: codeBindingEvidence },
    };
  }, { expectedAssertionIds: EXPECTED_ASSERTION_IDS, expectedRoutes: EXPECTED_ROUTES, directExports: DIRECT_EXPORTS, freeze: FIGMA_FREEZE, directAuditSha256: DIRECT_AUDIT_SHA256, aggregateSha256: DIRECT_EXPORT_AGGREGATE_SHA256, canonicalTreeSha256: CANONICAL_TREE_SHA256, canonicalInputsValid: context.canonicalInputsValid, codeContractSources: CODE_CONTRACT_SOURCES, codeAggregateSha256: CODE_CONTRACT_AGGREGATE_SHA256, guardVerification: EXECUTABLE_GUARD_VERIFICATION });
}

function createRunId() {
  return `stage2-${new Date().toISOString().replace(/[-:.]/g, '')}-${crypto.randomBytes(4).toString('hex')}`;
}

function atomicPromote(stagingDir, runId) {
  validateEvidenceDirectory(stagingDir);
  const backupDir = path.join(ASSETS_DIR, `.local-evidence-backup-${runId}`);
  assert(!fs.existsSync(backupDir), `Backup already exists: ${backupDir}`);
  let movedCurrent = false;
  try {
    if (fs.existsSync(PUBLISHED_DIR)) {
      validateEvidenceDirectory(PUBLISHED_DIR);
      fs.renameSync(PUBLISHED_DIR, backupDir);
      movedCurrent = true;
    }
    fs.renameSync(stagingDir, PUBLISHED_DIR);
    validateEvidenceDirectory(PUBLISHED_DIR);
    if (movedCurrent) removeDirectory(backupDir);
    assert(residueDirectories().length === 0, `Publication residue found: ${residueDirectories().join(', ')}`);
  } catch (error) {
    if (fs.existsSync(PUBLISHED_DIR)) removeDirectory(PUBLISHED_DIR);
    if (movedCurrent && fs.existsSync(backupDir)) {
      fs.renameSync(backupDir, PUBLISHED_DIR);
      validateEvidenceDirectory(PUBLISHED_DIR);
    }
    throw new Error(`Atomic promotion failed and rolled back: ${error.message}`);
  }
}

async function main() {
  const startedAt = new Date().toISOString();
  const runId = createRunId();
  const stagingDir = path.join(ASSETS_DIR, `.local-evidence-staging-${runId}`);

  // Contractual order: interrupted publication recovery precedes dependency resolution.
  const recovery = recoverEvidenceBeforeDependencyResolution();
  const playwright = resolvePlaywright();
  const fontBundle = resolveFonts();
  const canonicalInputs = validateCanonicalInputs();

  fs.mkdirSync(stagingDir, { recursive: false });
  let browser;
  try {
    const consoleErrors = [];
    const pageErrors = [];
    const failedRequests = [];
    const blockedNetworkRequests = [];
    browser = await playwright.module.chromium.launch({ headless: true });
    const browserVersion = browser.version();
    const context = await browser.newContext({ viewport: { width: 1600, height: 1200 }, deviceScaleFactor: 1, locale: 'fa-IR', colorScheme: 'light', reducedMotion: 'reduce' });
    const page = await context.newPage();
    page.on('console', (message) => { if (message.type() === 'error') consoleErrors.push(message.text()); });
    page.on('pageerror', (error) => pageErrors.push(error.message));
    page.on('requestfailed', (request) => failedRequests.push({ url: request.url(), error: request.failure()?.errorText || 'unknown' }));
    await page.route(/^https?:\/\//, async (route) => { blockedNetworkRequests.push(route.request().url()); await route.abort('blockedbyclient'); });

    await page.goto(pathToFileURL(HTML_PATH).href, { waitUntil: 'load' });
    const fontCss = fontBundle.fonts.map((font) => `@font-face{font-family:'Vazirmatn';font-style:normal;font-weight:${font.weight};font-display:block;src:url(data:font/woff2;base64,${font.data}) format('woff2');}`).join('\n');
    await page.addStyleTag({ content: fontCss });
    await page.evaluate(async () => {
      await Promise.all([400, 500, 600, 700].map((weight) => document.fonts.load(`${weight} 16px Vazirmatn`, 'آزمون')));
      await document.fonts.ready;
      if (![...document.images].every((image) => image.complete && image.naturalWidth > 0)) throw new Error('One or more direct source images failed to load');
    });
    const fontChecks = await page.evaluate(() => [400, 500, 600, 700].map((weight) => ({ weight, loaded: document.fonts.check(`${weight} 16px Vazirmatn`, 'آزمون') })));
    assert(fontChecks.every((item) => item.loaded), `Font load failure: ${JSON.stringify(fontChecks)}`);

    const auditContext = { canonicalInputsValid: true };
    const preAudit = await auditPage(page, auditContext);
    assert(preAudit.passed === 25 && preAudit.failed === 0, `Pre-capture assertion failure: ${JSON.stringify(preAudit.assertions.filter((item) => !item.passed), null, 2)}`);
    const preDom = await canonicalDomSnapshot(page);
    const preDomSha256 = sha256Buffer(Buffer.from(preDom));
    const preAuditSha256 = hashValue(preAudit);

    const captures = [];
    for (const spec of CAPTURE_SPECS) {
      const locator = page.locator(spec.selector);
      assert(await locator.count() === 1, `Capture selector must resolve once: ${spec.selector}`);
      const box = await locator.evaluate((element) => {
        const rect = element.getBoundingClientRect();
        return { x: rect.left + window.scrollX, y: rect.top + window.scrollY, width: rect.width, height: rect.height, documentWidth: document.documentElement.scrollWidth, documentHeight: document.documentElement.scrollHeight };
      });
      assert(box.width > 0 && box.height > 0 && box.x >= 0 && box.y >= 0 && box.x + box.width <= box.documentWidth + 0.01 && box.y + box.height <= box.documentHeight + 0.01, `Invalid capture geometry: ${spec.file} ${JSON.stringify(box)}`);
      const captureWidth = Math.ceil(box.width);
      const captureHeight = Math.ceil(box.height);
      const outputPath = path.join(stagingDir, spec.file);
      const originalStyle = await locator.getAttribute('style');
      try {
        await page.setViewportSize({ width: captureWidth, height: captureHeight });
        await locator.evaluate((element, geometry) => {
          element.style.setProperty('position', 'fixed', 'important');
          element.style.setProperty('inset', '0 auto auto 0', 'important');
          element.style.setProperty('margin', '0', 'important');
          element.style.setProperty('transform', 'none', 'important');
          element.style.setProperty('width', `${geometry.width}px`, 'important');
          element.style.setProperty('height', `${geometry.height}px`, 'important');
          element.style.setProperty('z-index', '2147483647', 'important');
        }, { width: captureWidth, height: captureHeight });
        await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
        const fixedBox = await locator.boundingBox();
        assert(fixedBox && fixedBox.x === 0 && fixedBox.y === 0 && Math.round(fixedBox.width) === captureWidth && Math.round(fixedBox.height) === captureHeight, `Temporary capture geometry mismatch: ${spec.file} ${JSON.stringify(fixedBox)}`);
        await page.screenshot({ path: outputPath, animations: 'disabled', caret: 'hide', clip: { x: 0, y: 0, width: captureWidth, height: captureHeight } });
      } finally {
        await locator.evaluate((element, style) => { if (style === null) element.removeAttribute('style'); else element.setAttribute('style', style); }, originalStyle);
        await page.setViewportSize({ width: 1600, height: 1200 });
      }
      const dimensions = pngDimensions(outputPath);
      const visualStats = pngVisualStats(outputPath);
      assert(dimensions.width === captureWidth && dimensions.height === captureHeight, `Captured PNG dimensions mismatch: ${spec.file}`);
      assert(visualStats.uniqueColorCount >= 8 && visualStats.changedPixelCount >= 100, `Captured PNG blank or degenerate: ${spec.file}`);
      captures.push({ file: spec.file, selector: spec.selector, width: dimensions.width, height: dimensions.height, bytes: fs.statSync(outputPath).size, sha256: sha256File(outputPath), visualStats });
    }

    const postAudit = await auditPage(page, auditContext);
    const postDom = await canonicalDomSnapshot(page);
    const postDomSha256 = sha256Buffer(Buffer.from(postDom));
    const postAuditSha256 = hashValue(postAudit);
    assert(postAudit.passed === 25 && postAudit.failed === 0, `Post-capture assertion failure: ${JSON.stringify(postAudit.assertions.filter((item) => !item.passed), null, 2)}`);
    assert(preAuditSha256 === postAuditSha256 && stableJson(preAudit) === stableJson(postAudit), 'Pre/post audit changed during capture');
    assert(preDomSha256 === postDomSha256 && preDom === postDom, 'Canonical DOM changed during capture');
    assert(consoleErrors.length === 0, `Browser console errors: ${consoleErrors.join(' | ')}`);
    assert(pageErrors.length === 0, `Browser page errors: ${pageErrors.join(' | ')}`);
    assert(failedRequests.length === 0, `Browser failed requests: ${JSON.stringify(failedRequests)}`);
    assert(blockedNetworkRequests.length === 0, `Evidence attempted network access: ${blockedNetworkRequests.join(', ')}`);

    const completedAt = new Date().toISOString();
    const metrics = {
      schemaVersion: 1,
      stage: '2',
      status: 'passed',
      runId,
      startedAt,
      completedAt,
      canonicalFigma: { ...FIGMA_FREEZE, canonicalTree: { bytes: 612614, sha256: CANONICAL_TREE_SHA256, prePostEqual: true }, inventoryDigest: { bytes: 78068, sha256: '9c9fa984188bee28217a1052f42186b2815ec3a9d91c431c8d0649c8e637c829' } },
      directFigma: {
        status: 'passed',
        provenance: 'hash-bound post-freeze direct audit and natural-size exports; not inferred by Chromium',
        auditJson: { path: canonicalInputs.directAudit.path, bytes: canonicalInputs.directAudit.bytes, sha256: canonicalInputs.directAudit.sha256, schema: canonicalInputs.directAudit.parsed.schema, assertions: canonicalInputs.directAudit.parsed.figmaAssertionStatus },
        exports: canonicalInputs.directExports,
        exportAggregateSha256: canonicalInputs.directExportAggregateSha256,
      },
      executableGuard: {
        status: 'passed',
        sources: canonicalInputs.executableGuard.sources,
        sourceAggregateSha256: canonicalInputs.executableGuard.sourceAggregateSha256,
        verification: canonicalInputs.executableGuard.verification,
      },
      localEvidence: { role: FIGMA_FREEZE.localEvidenceRole, canonical: false, runtimeBehaviorProven: false, protectedGitDiffProven: false, sitesProven: false, sourceHtml: canonicalInputs.html },
      claimBoundary: { figmaCanonical: true, localDerivativeCanonical: false, runtimeBehaviorProven: false, protectedGitDiffProven: false, sitesProven: false, externalTechnicalGatesRequired: true },
      recoveryBeforeDependencyResolution: recovery,
      dependencies: {
        node: process.version,
        platform: `${process.platform}-${process.arch}`,
        playwright: playwright.resolvedFrom,
        browser: { engine: 'chromium', version: browserVersion, baseViewport: { width: 1600, height: 1200 }, deviceScaleFactor: 1 },
        fonts: { family: 'Vazirmatn', root: fontBundle.root, checks: fontChecks, files: fontBundle.fonts.map(({ file, weight, bytes, sha256 }) => ({ file, weight, bytes, sha256 })) },
      },
      integrity: { preDomSha256, postDomSha256, domEqual: true, preAuditSha256, postAuditSha256, auditEqual: true, postCaptureRemeasurement: true, consoleErrors, pageErrors, failedRequests, blockedNetworkRequests },
      assertions: postAudit.assertions,
      assertionSummary: { total: 25, passed: postAudit.passed, failed: postAudit.failed, exactOrder: true },
      measurements: postAudit.metrics,
      captures,
      outputSet: { policy: 'exact', pngCount: 6, metricsCount: 1, files: EXACT_OUTPUT_FILES },
      publication: { strategy: 'atomic-directory-rename', partialPromotionAllowed: false, validationBeforePromotion: true, validationAfterPromotion: true, residueAllowed: false },
    };

    fs.writeFileSync(path.join(stagingDir, METRICS_FILE), `${JSON.stringify(metrics, null, 2)}\n`, { encoding: 'utf8', flag: 'wx' });
    validateEvidenceDirectory(stagingDir);
    await context.close();
    await browser.close();
    browser = null;

    atomicPromote(stagingDir, runId);
    const published = validateEvidenceDirectory(PUBLISHED_DIR);
    const residues = residueDirectories();
    assert(residues.length === 0, `Residue after publication: ${residues.join(', ')}`);
    const report = {
      status: published.status,
      runId: published.runId,
      startedAt: published.startedAt,
      completedAt: published.completedAt,
      frozenAt: published.canonicalFigma.frozenAt,
      assertions: `${published.assertionSummary.passed}/${published.assertionSummary.total}`,
      domSha256: published.integrity.postDomSha256,
      auditSha256: published.integrity.postAuditSha256,
      sourceHtml: published.localEvidence.sourceHtml,
      directAuditSha256: published.directFigma.auditJson.sha256,
      directExportAggregateSha256: published.directFigma.exportAggregateSha256,
      codeContractAggregateSha256: published.executableGuard.sourceAggregateSha256,
      outputDirectory: path.relative(REPO_ROOT, PUBLISHED_DIR),
      residueDirectories: residues,
      files: published.captures.map(({ file, width, height, bytes, sha256 }) => ({ file, width, height, bytes, sha256 })).concat({ file: METRICS_FILE, bytes: fs.statSync(path.join(PUBLISHED_DIR, METRICS_FILE)).size, sha256: sha256File(path.join(PUBLISHED_DIR, METRICS_FILE)) }),
    };
    process.stdout.write(`${JSON.stringify(report, null, 2)}\n`);
  } catch (error) {
    if (browser) await browser.close().catch(() => {});
    if (fs.existsSync(stagingDir)) removeDirectory(stagingDir);
    throw error;
  }
}

main().catch((error) => {
  process.stderr.write(`Stage 2 evidence capture failed: ${error.stack || error.message}\n`);
  process.exitCode = 1;
});
