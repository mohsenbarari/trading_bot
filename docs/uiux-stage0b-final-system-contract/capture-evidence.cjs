#!/usr/bin/env node
'use strict';

/**
 * Stage 0B-6 local derivative evidence harness.
 *
 * Figma is canonical. This harness validates and captures a secondary local
 * rendering only. It neither edits runtime source nor claims to prove runtime
 * behavior. Publication is atomic and fail-closed.
 */

const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const zlib = require('node:zlib');
const { pathToFileURL } = require('node:url');

const CONTRACT_DIR = __dirname;
const REPO_ROOT = path.resolve(CONTRACT_DIR, '..', '..');
const HTML_PATH = path.join(CONTRACT_DIR, 'final-system-contract-evidence.html');
const ASSETS_DIR = path.join(CONTRACT_DIR, 'assets');
const PUBLISHED_DIR = path.join(ASSETS_DIR, 'local-evidence');
const METRICS_FILE = 'local-stage0b6-final-system-contract-validation-metrics.json';
const DIRECT_AUDIT_FILE = 'figma-stage0b6-audit-metrics.json';
const DIRECT_AUDIT_SHA256 = '7eaa85d626366ea623714fa4d22cc521bf4455434c05e72db1a4b38a9659e2ff';

const FIGMA_FREEZE = Object.freeze({
  fileKey: 'z8jgJxST4O2APzWnlyP9gv',
  pageId: '168:1974',
  boardId: '168:1975',
  frozenAt: '2026-08-08T19:54:11.151Z',
  auditedAt: '2026-08-08T20:06:32.118Z',
  provenanceRereadAt: '2026-08-08T20:15:41.663Z',
  canonicalRole: 'primary',
  localEvidenceRole: 'secondary-derivative',
});

const EXPECTED_ASSERTION_IDS = Object.freeze([
  'owner-approval-0b1-through-0b5-recorded',
  'canonical-source-registry-complete',
  'canonical-source-references-resolve',
  'approved-source-fact-parity',
  'stage0b6-contract-only-no-new-feature-facts',
  'runtime-diff-empty',
  'modern-finance-direction-locked',
  'font-vazirmatn-only',
  'foundation-inventory-65-9-2',
  'broken-variable-aliases-zero',
  'component-inventory-12-sets-56-variants-with-delta',
  'product-proof-detached-instances-zero',
  'known-figma-debt-disposition-complete',
  'five-mobile-family-references-complete',
  'mobile-reference-roots-390x844',
  'responsive-widths-360-375-390-414-430',
  'desktop-layout-archetypes-complete',
  'desktop-fact-parity',
  'no-product-overflow-or-clipping',
  'touch-targets-44',
  'primary-cta-height-48',
  'navigation-label-11',
  'text-contrast-45',
  'focus-contrast-3-stroke-3',
  'shell-route-layer-contract-complete',
  'common-state-feedback-contract-complete',
  'motion-reduced-motion-contract-complete',
  'content-necessity-inventory-complete',
  'reviewer-metadata-absent-from-product-roots',
  'synthetic-identities-and-forbidden-copy-clean',
  'protected-interiors-absent',
  'implementation-gate-and-static-limits-explicit',
]);

const FIGMA_DIRECT_AUDIT = Object.freeze({
  assertionCount: 32,
  passed: 32,
  failed: 0,
  status: 'passed',
  variables: 65,
  textStyles: 9,
  effects: 2,
  componentSets: 12,
  componentVariants: 56,
  brokenAliases: 0,
  productInstances: 30,
  detachedProductInstances: 0,
  operationsConsumers: 31,
  factPairsPassed: 11,
  factPairsTotal: 11,
  productOverflowFindings: 0,
  textClippingFindings: 0,
  minimumTextContrast: 4.55,
  touchTargetsMeasured: 41,
  minimumTouchTarget: 44,
  primaryCta: { width: 358, height: 48 },
  minimumNavigationLabelPx: 11,
  focusContrast: 4.35,
  focusStrokePx: 3,
  reviewerMetadataFindings: 0,
  piiFindings: 0,
  forbiddenCopyFindings: 0,
  protectedInteriorFindings: 0,
  provenance: 'post-freeze-direct-figma-audit',
  localHarnessClaim: false,
});

const DIRECT_EXPORTS = Object.freeze([
  { file: 'figma-approved-family-reference-strip.png', width: 1720, height: 1992, bytes: 152409, sha256: 'a9ecd7aa52688d1d1ef217894d8e611ea9246ad93e1e060a0c479a32c7bc3108' },
  { file: 'figma-content-privacy-protected-surfaces-contract.png', width: 1720, height: 542, bytes: 63242, sha256: 'd6b621c463f6eebfc798794d3a83ea0a36e8ea2735a7a710d77a641e3a417bd0' },
  { file: 'figma-desktop-operational-master-detail-1440x900.png', width: 1440, height: 900, bytes: 63343, sha256: 'dca4b855d7d04c7b9664978b6e49a67eb2d629b37ae8b0c8c3d4b3e99a326361' },
  { file: 'figma-foundations-components-contract.png', width: 1720, height: 426, bytes: 46337, sha256: 'ecdff065886a2da92878515702f7afbc35e7c1f373aa793c25d9f164652f1e29' },
  { file: 'figma-implementation-stage-map.png', width: 1720, height: 566, bytes: 68419, sha256: 'c397b399c1f787024a512168661c6bb367f07c0ec4bb98632d185fb9f8e35705' },
  { file: 'figma-responsive-desktop-acceptance-proofs.png', width: 1720, height: 3206, bytes: 166773, sha256: 'ee54bfbf6b674c4621dee5de4331642d461f27726413f486b07c46a024e880fe' },
  { file: 'figma-shell-route-layout-contract.png', width: 1720, height: 706, bytes: 106651, sha256: '4cb6b124aac4d7ffbda39325e34dcd9c75fbdf73d0289bde9fc25d5034e7cfa5' },
  { file: 'figma-state-feedback-motion-accessibility-contract.png', width: 1720, height: 448, bytes: 55788, sha256: '1d3d9ab1b009b1024df286d60a5e5aab36bfd930e908a8d8c7546d82ecbcf91a' },
  { file: 'figma-system-scope-provenance-gate.png', width: 1720, height: 444, bytes: 44974, sha256: '7a68aa07a73166ccbedcdade3ee6c6af3a5ea44f1de2656c81461f6ae8f6c5f2' },
]);

const CAPTURE_SPECS = Object.freeze([
  { selector: '#system-contract-overview', file: 'local-system-scope-provenance-gate.png' },
  { selector: '#family-references', file: 'local-approved-family-reference-strip.png' },
  { selector: '#foundations-components', file: 'local-foundations-components-contract.png' },
  { selector: '#shell-state-contract', file: 'local-shell-state-feedback-contract.png' },
  { selector: '#content-protected-contract', file: 'local-content-privacy-protected-contract.png' },
  { selector: '#responsive-proofs', file: 'local-responsive-acceptance-proofs.png' },
  { selector: '[data-desktop-proof]', file: 'local-desktop-operational-master-detail-1440x900.png', width: 1440, height: 900 },
]);

const EXACT_OUTPUT_FILES = Object.freeze([...CAPTURE_SPECS.map((item) => item.file), METRICS_FILE].sort());

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

function pngDimensions(filePath) {
  const buffer = fs.readFileSync(filePath);
  const signature = '89504e470d0a1a0a';
  if (buffer.length < 24 || buffer.subarray(0, 8).toString('hex') !== signature || buffer.subarray(12, 16).toString('ascii') !== 'IHDR') {
    throw new Error(`Not a valid PNG with IHDR: ${filePath}`);
  }
  return { width: buffer.readUInt32BE(16), height: buffer.readUInt32BE(20) };
}

function pngVisualStats(filePath) {
  const buffer = fs.readFileSync(filePath);
  const width = buffer.readUInt32BE(16);
  const height = buffer.readUInt32BE(20);
  const bitDepth = buffer[24];
  const colorType = buffer[25];
  assert(bitDepth === 8 && (colorType === 2 || colorType === 6), `Unsupported PNG format for visual audit: ${filePath} depth=${bitDepth} type=${colorType}`);
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
    const filter = raw[cursor];
    cursor += 1;
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

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

function sortedDirectoryEntries(directory) {
  return fs.readdirSync(directory, { withFileTypes: true })
    .map((entry) => entry.name)
    .sort();
}

function validateEvidenceDirectory(directory) {
  assert(fs.existsSync(directory) && fs.statSync(directory).isDirectory(), `Evidence directory missing: ${directory}`);
  const actual = sortedDirectoryEntries(directory);
  assert(JSON.stringify(actual) === JSON.stringify(EXACT_OUTPUT_FILES), `Evidence artifact set mismatch in ${directory}: ${actual.join(', ')}`);

  const metricsPath = path.join(directory, METRICS_FILE);
  const metrics = JSON.parse(fs.readFileSync(metricsPath, 'utf8'));
  assert(metrics.schemaVersion === 1 && metrics.stage === '0B-6' && metrics.status === 'passed', `Evidence metrics are not a passed Stage 0B-6 schema: ${directory}`);
  assert(metrics.canonicalFigma?.frozenAt === FIGMA_FREEZE.frozenAt, `Evidence freeze mismatch: ${directory}`);
  assert(metrics.canonicalFigma?.fileKey === FIGMA_FREEZE.fileKey, `Evidence Figma key mismatch: ${directory}`);
  assert(metrics.localEvidence?.role === FIGMA_FREEZE.localEvidenceRole, `Evidence role mismatch: ${directory}`);
  assert(metrics.runtimeDiffProof?.kind === 'external-read-only' && metrics.runtimeDiffProof?.localHarnessClaim === false, `Runtime proof boundary mismatch: ${directory}`);
  assert(Array.isArray(metrics.assertions) && metrics.assertions.length === EXPECTED_ASSERTION_IDS.length, `Assertion count mismatch: ${directory}`);
  assert(JSON.stringify(metrics.assertions.map((item) => item.id)) === JSON.stringify(EXPECTED_ASSERTION_IDS), `Assertion order mismatch: ${directory}`);
  assert(metrics.assertions.every((item) => item.passed === true), `Failed assertion found: ${directory}`);
  assert(Array.isArray(metrics.captures) && metrics.captures.length === CAPTURE_SPECS.length, `Capture count mismatch: ${directory}`);
  assert(JSON.stringify(metrics.outputSet?.files?.slice().sort()) === JSON.stringify(EXACT_OUTPUT_FILES), `Declared output set mismatch: ${directory}`);

  for (const capture of metrics.captures) {
    const filePath = path.join(directory, capture.file);
    assert(EXACT_OUTPUT_FILES.includes(capture.file), `Unexpected capture declared: ${capture.file}`);
    const stat = fs.statSync(filePath);
    const dimensions = pngDimensions(filePath);
    const visualStats = pngVisualStats(filePath);
    assert(stat.size === capture.bytes, `Capture byte count mismatch: ${capture.file}`);
    assert(sha256File(filePath) === capture.sha256, `Capture hash mismatch: ${capture.file}`);
    assert(dimensions.width === capture.width && dimensions.height === capture.height, `Capture dimension mismatch: ${capture.file}`);
    assert(visualStats.uniqueColorCount >= 8 && visualStats.changedPixelCount >= 100, `Capture is visually blank or degenerate: ${capture.file} ${JSON.stringify(visualStats)}`);
    assert(JSON.stringify(visualStats) === JSON.stringify(capture.visualStats), `Capture visual stats mismatch: ${capture.file}`);
  }
  return metrics;
}

function removeDirectory(directory) {
  fs.rmSync(directory, { recursive: true, force: true });
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
    try {
      validateEvidenceDirectory(PUBLISHED_DIR);
      currentValid = true;
    } catch (error) {
      currentError = error;
    }
  }

  const validBackups = [];
  const invalidBackups = [];
  for (const backup of backupDirs) {
    try {
      validateEvidenceDirectory(backup);
      validBackups.push(backup);
    } catch (error) {
      invalidBackups.push({ backup, error });
    }
  }

  if (currentValid) {
    for (const backup of backupDirs) removeDirectory(backup);
    return { action: stagingDirs.length || backupDirs.length ? 'cleaned-with-valid-current' : 'clean', recovered: false };
  }

  if (validBackups.length > 1) {
    throw new Error(`Fail-closed recovery: multiple valid backups exist (${validBackups.join(', ')})`);
  }

  if (validBackups.length === 1) {
    const backup = validBackups[0];
    const invalidCurrent = fs.existsSync(PUBLISHED_DIR) ? `${PUBLISHED_DIR}.invalid-recovery-${process.pid}` : null;
    if (invalidCurrent) fs.renameSync(PUBLISHED_DIR, invalidCurrent);
    fs.renameSync(backup, PUBLISHED_DIR);
    try {
      validateEvidenceDirectory(PUBLISHED_DIR);
    } catch (error) {
      if (fs.existsSync(PUBLISHED_DIR)) fs.renameSync(PUBLISHED_DIR, backup);
      if (invalidCurrent && fs.existsSync(invalidCurrent)) fs.renameSync(invalidCurrent, PUBLISHED_DIR);
      throw new Error(`Fail-closed recovery validation failed: ${error.message}`);
    }
    if (invalidCurrent) removeDirectory(invalidCurrent);
    for (const item of invalidBackups) removeDirectory(item.backup);
    return { action: 'restored-valid-backup', recovered: true };
  }

  if (fs.existsSync(PUBLISHED_DIR)) {
    throw new Error(`Fail-closed recovery: published evidence is invalid and no valid backup exists: ${currentError?.message || 'unknown validation error'}`);
  }
  if (invalidBackups.length) {
    throw new Error(`Fail-closed recovery: only invalid backups exist: ${invalidBackups.map((item) => `${item.backup}: ${item.error.message}`).join('; ')}`);
  }

  return { action: stagingDirs.length ? 'removed-stale-staging' : 'nothing-to-recover', recovered: false };
}

function resolvePlaywright() {
  const candidates = [
    process.env.UIUX_PLAYWRIGHT_MODULE,
    'playwright',
    path.join(REPO_ROOT, 'frontend', 'node_modules', 'playwright'),
    '/root/trading-bot/trading_bot/frontend/node_modules/playwright',
  ].filter(Boolean);
  const errors = [];
  for (const candidate of candidates) {
    try {
      return { module: require(candidate), resolvedFrom: require.resolve(candidate) };
    } catch (error) {
      errors.push(`${candidate}: ${error.code || error.message}`);
    }
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
    const resolved = weights.map((item) => ({ ...item, path: path.join(root, item.file) }));
    if (resolved.every((item) => fs.existsSync(item.path))) {
      return {
        root,
        fonts: resolved.map((item) => {
          const buffer = fs.readFileSync(item.path);
          return { ...item, bytes: buffer.length, sha256: sha256Buffer(buffer), data: buffer.toString('base64') };
        }),
      };
    }
  }
  throw new Error(`Required Vazirmatn 400/500/600/700 webfonts unavailable after recovery; checked ${roots.join(', ')}`);
}

function validateCanonicalInputs() {
  assert(fs.existsSync(HTML_PATH), `Evidence HTML missing: ${HTML_PATH}`);
  const actualPngFiles = fs.readdirSync(ASSETS_DIR, { withFileTypes: true })
    .filter((entry) => entry.isFile() && entry.name.endsWith('.png'))
    .map((entry) => entry.name)
    .sort();
  const expectedPngFiles = DIRECT_EXPORTS.map((item) => item.file).sort();
  assert(JSON.stringify(actualPngFiles) === JSON.stringify(expectedPngFiles), `Direct Figma export set mismatch: ${actualPngFiles.join(', ')}`);

  const exports = DIRECT_EXPORTS.map((expected) => {
    const filePath = path.join(ASSETS_DIR, expected.file);
    const stat = fs.statSync(filePath);
    const dimensions = pngDimensions(filePath);
    const sha256 = sha256File(filePath);
    assert(stat.size === expected.bytes, `Direct export byte mismatch: ${expected.file}`);
    assert(dimensions.width === expected.width && dimensions.height === expected.height, `Direct export dimension mismatch: ${expected.file}`);
    assert(sha256 === expected.sha256, `Direct export hash mismatch: ${expected.file}`);
    return { ...expected, validated: true };
  });

  const directAuditPath = path.join(ASSETS_DIR, DIRECT_AUDIT_FILE);
  assert(fs.existsSync(directAuditPath) && fs.statSync(directAuditPath).isFile(), `Direct Figma audit JSON missing: ${DIRECT_AUDIT_FILE}`);
  const directAuditSha256 = sha256File(directAuditPath);
  assert(directAuditSha256 === DIRECT_AUDIT_SHA256, `Direct Figma audit JSON hash mismatch: expected ${DIRECT_AUDIT_SHA256}, received ${directAuditSha256}`);
  const directAudit = JSON.parse(fs.readFileSync(directAuditPath, 'utf8'));
  assert(directAudit.schema === 3 && directAudit.stage === '0B-6', 'Direct Figma audit schema/stage mismatch');
  assert(directAudit.fileKey === FIGMA_FREEZE.fileKey && directAudit.pageId === FIGMA_FREEZE.pageId && directAudit.boardId === FIGMA_FREEZE.boardId, 'Direct Figma audit canonical node identity mismatch');
  assert(directAudit.frozenAt === FIGMA_FREEZE.frozenAt && directAudit.auditedAt === FIGMA_FREEZE.auditedAt, 'Direct Figma audit freeze/audit timestamp mismatch');
  assert(directAudit.passedCount === 32 && directAudit.failedCount === 0 && directAudit.figmaAssertionStatus === '32/32 passed', 'Direct Figma audit result is not exact 32/32');
  assert(Array.isArray(directAudit.assertions) && JSON.stringify(directAudit.assertions.map((item) => item.id)) === JSON.stringify(EXPECTED_ASSERTION_IDS), `Direct Figma audit assertion IDs/order mismatch: ${JSON.stringify(directAudit.assertions?.map((item) => item.id))}`);
  assert(directAudit.assertions.every((item, index) => item.order === index + 1 && item.status === 'passed'), 'Direct Figma audit assertion order/status mismatch');
  assert(directAudit.metrics?.foundations?.variables === FIGMA_DIRECT_AUDIT.variables && directAudit.metrics?.foundations?.textStyles === FIGMA_DIRECT_AUDIT.textStyles && directAudit.metrics?.foundations?.effects === FIGMA_DIRECT_AUDIT.effects, 'Direct Figma foundation inventory mismatch');
  assert(directAudit.metrics?.components?.sets === FIGMA_DIRECT_AUDIT.componentSets && directAudit.metrics?.components?.variants === FIGMA_DIRECT_AUDIT.componentVariants, 'Direct Figma component inventory mismatch');
  assert(directAudit.metrics?.foundations?.brokenAliases === 0 && directAudit.metrics?.instances?.detached === 0, 'Direct Figma broken alias/detached instance finding');
  assert(directAudit.metrics?.scans?.protectedInteriors === 0 && directAudit.metrics?.scans?.reviewerMetadata === 0 && directAudit.metrics?.scans?.identityPii === 0 && directAudit.metrics?.scans?.forbiddenCopy === 0, 'Direct Figma protected/reviewer/PII/copy scan finding');
  assert(Array.isArray(directAudit.directEvidence) && directAudit.directEvidence.length === DIRECT_EXPORTS.length, 'Direct Figma audit export record count mismatch');
  for (const expected of DIRECT_EXPORTS) {
    const record = directAudit.directEvidence.find((item) => item.path === expected.file);
    assert(record && record.width === expected.width && record.height === expected.height && record.bytes === expected.bytes && record.sha256 === expected.sha256, `Direct audit record mismatch: ${expected.file}`);
  }

  const html = fs.readFileSync(HTML_PATH, 'utf8');
  const sourceMatches = [...html.matchAll(/data-local-path="([^"]+)"/g)].map((match) => match[1]);
  assert(sourceMatches.length === 5 && new Set(sourceMatches).size === 5, `Expected five unique canonical source references; found ${sourceMatches.length}`);
  const sourceReferences = sourceMatches.map((relativePath) => {
    const absolutePath = path.resolve(REPO_ROOT, relativePath);
    assert(absolutePath.startsWith(`${REPO_ROOT}${path.sep}`), `Source reference escapes repository root: ${relativePath}`);
    assert(fs.existsSync(absolutePath) && fs.statSync(absolutePath).isFile(), `Canonical source reference does not resolve: ${relativePath}`);
    return { path: relativePath, bytes: fs.statSync(absolutePath).size, sha256: sha256File(absolutePath), resolved: true };
  });

  return {
    html: { path: path.relative(REPO_ROOT, HTML_PATH), bytes: fs.statSync(HTML_PATH).size, sha256: sha256File(HTML_PATH) },
    directExports: exports,
    directExportAggregateSha256: hashValue(exports.map(({ file, width, height, bytes, sha256 }) => ({ file, width, height, bytes, sha256 }))),
    directAuditJson: { path: path.relative(REPO_ROOT, directAuditPath), bytes: fs.statSync(directAuditPath).size, sha256: directAuditSha256, parsed: directAudit },
    sourceReferences,
  };
}

async function canonicalDomSnapshot(page) {
  return page.evaluate(() => {
    const clone = document.documentElement.cloneNode(true);
    for (const node of clone.querySelectorAll('[style=""]')) node.removeAttribute('style');
    return `<!doctype html>\n${clone.outerHTML}`;
  });
}

async function auditPage(page, context) {
  return page.evaluate(({ expectedAssertionIds, sourceReferencesResolved, fontsLoaded }) => {
    const q = (selector, root = document) => root.querySelector(selector);
    const qa = (selector, root = document) => [...root.querySelectorAll(selector)];
    const round = (value, places = 2) => Number(value.toFixed(places));
    const exact = (actual, expected) => JSON.stringify(actual) === JSON.stringify(expected);
    const sortedUnique = (values) => [...new Set(values)].sort();
    const dims = (node) => {
      const rect = node.getBoundingClientRect();
      return { width: round(rect.width), height: round(rect.height) };
    };
    const assertionResults = [];
    const record = (id, passed, evidence) => assertionResults.push({ id, passed: Boolean(passed), evidence });
    const parseHex = (value) => {
      const hex = value.trim().replace('#', '');
      if (!/^[0-9a-f]{6}$/i.test(hex)) throw new Error(`Unsupported contrast color: ${value}`);
      return [0, 2, 4].map((index) => parseInt(hex.slice(index, index + 2), 16));
    };
    const luminance = (hex) => {
      const channels = parseHex(hex).map((channel) => {
        const normalized = channel / 255;
        return normalized <= 0.04045 ? normalized / 12.92 : ((normalized + 0.055) / 1.055) ** 2.4;
      });
      return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2];
    };
    const contrast = (foreground, background) => {
      const first = luminance(foreground);
      const second = luminance(background);
      return round((Math.max(first, second) + 0.05) / (Math.min(first, second) + 0.05));
    };
    const isVisible = (node) => {
      const style = getComputedStyle(node);
      const rect = node.getBoundingClientRect();
      return style.display !== 'none' && style.visibility !== 'hidden' && Number(style.opacity) !== 0 && rect.width > 0 && rect.height > 0;
    };
    const normalizeToken = (value) => String(value || '').replace(/\s+/g, ' ').trim();
    const visibleTokens = (root) => {
      const tokens = [];
      const visit = (node) => {
        if (node.nodeType === Node.TEXT_NODE) {
          const value = normalizeToken(node.nodeValue);
          if (value && node.parentElement && isVisible(node.parentElement)) tokens.push(value);
          return;
        }
        if (node.nodeType !== Node.ELEMENT_NODE) return;
        const element = node;
        if (element.getAttribute('aria-hidden') === 'true' || !isVisible(element)) return;
        if (element instanceof HTMLInputElement || element instanceof HTMLTextAreaElement || element instanceof HTMLSelectElement) {
          const value = normalizeToken(element.value);
          if (value) tokens.push(value);
          return;
        }
        for (const child of element.childNodes) visit(child);
      };
      visit(root);
      return tokens;
    };
    const multiset = (values) => [...values].sort();
    const exactMultiset = (actual, expected) => exact(multiset(actual), multiset(expected));
    const interactiveLabel = (node) => {
      if (node.matches('.nav-item')) return normalizeToken(node.querySelector('[data-nav-label]')?.textContent);
      if (node instanceof HTMLInputElement || node instanceof HTMLTextAreaElement || node instanceof HTMLSelectElement) return normalizeToken(node.value);
      const aria = normalizeToken(node.getAttribute('aria-label'));
      if (aria) return aria;
      return visibleTokens(node).join(' ');
    };
    const interactiveLabels = (root) => qa('button, input, select, textarea, a[href], [role="button"], [tabindex]:not([tabindex="-1"])', root).filter(isVisible).map(interactiveLabel);

    const registryIds = qa('[data-assertion-id]').map((node) => node.dataset.assertionId);
    if (!exact(registryIds, expectedAssertionIds) || new Set(registryIds).size !== expectedAssertionIds.length) {
      throw new Error(`Exact assertion registry mismatch: ${JSON.stringify(registryIds)}`);
    }

    const approvals = qa('[data-owner-approval-stage]').map((node) => ({ stage: node.dataset.ownerApprovalStage, status: node.dataset.status }));
    record(expectedAssertionIds[0], exact(approvals, ['0B-1', '0B-2', '0B-3', '0B-4', '0B-5'].map((stage) => ({ stage, status: 'approved' }))), approvals);

    const sources = qa('[data-canonical-source-registry] [data-source-stage]').map((node) => ({ stage: node.dataset.sourceStage, family: node.dataset.family, status: node.dataset.status, path: node.dataset.localPath }));
    const expectedSources = [
      ['0B-1', 'auth'], ['0B-2', 'home-shell'], ['0B-3', 'operations-workspaces'], ['0B-4', 'admin-invitations'], ['0B-5', 'account-profile'],
    ];
    record(expectedAssertionIds[1], sources.length === 5 && sources.every((source, index) => source.stage === expectedSources[index][0] && source.family === expectedSources[index][1] && source.status === 'resolved'), sources);
    record(expectedAssertionIds[2], sourceReferencesResolved === true && sources.every((source) => source.path), { sourceReferencesResolved, paths: sources.map((source) => source.path) });

    const familyRoots = qa('[data-mobile-family-reference]');
    const expectedFamilies = ['auth', 'home-shell', 'operations-workspaces', 'admin-invitations', 'account-profile'];
    const familySourceMap = { auth: '0B-1', 'home-shell': '0B-2', 'operations-workspaces': '0B-3', 'admin-invitations': '0B-4', 'account-profile': '0B-5' };
    const expectedFamilyTokens = {
      auth: ['سامانه معاملات', 'مرحله ۱ از ۲', 'ورود به سامانه', 'شماره موبایل ثبت‌شده را وارد کنید تا کد تأیید برای شما ارسال شود.', 'شماره موبایل', '0912 345 6789', 'دریافت کد تأیید', 'دریافت کد', 'کد ابتدا در تلگرام و در صورت نیاز به‌صورت خودکار با پیامک ارسال می‌شود.'],
      'home-shell': ['ن', 'نگار پارسا', 'خانه', 'خانه', 'بازار', '۲', 'پیام‌رسان', 'عملیات', 'حساب'],
      'operations-workspaces': ['ن', 'نگار پارسا', 'مشتریان', 'جستجوی مشتری', '+', '۲ دعوت در انتظار', 'دعوت', 'دعوت نمونه', '0912 *** 0003 · تا ۲۲ مرداد', 'فعال', 'مشتری نمونه یک', '0912 *** 0004 · سطح ۲', 'فعال', 'مشتری نمونه دو', '0912 *** 0005 · سطح ۱', 'غیرفعال', 'مشتری نمونه سه', '0912 *** 0006', 'خانه', 'بازار', 'پیام‌رسان', 'عملیات', 'حساب'],
      'admin-invitations': ['م', 'مدیر نمونه', 'کاربران', 'جست‌وجو', 'نام، شماره یا حساب', 'فعال', 'کاربر نمونه', '0912 *** 0000 · کاربر عادی', 'فعال', 'کاربر نمونه با نام طولانی', '0912 *** 0001 · تماشا', 'نیازمند اقدام', 'کاربر آزمایشی', '0912 *** 0002 · غیرفعال', 'خانه', 'بازار', 'پیام‌رسان', 'عملیات', 'حساب'],
      'account-profile': ['ن', 'نگار پارسا', 'حساب', 'پروفایل', 'آواتار و آدرس حساب', 'امنیت و نشست‌ها', 'دستگاه‌های فعال این حساب', 'حافظه و داده‌ها', 'فایل‌های ذخیره‌شده همین دستگاه', 'اعلان‌ها', 'معاملات و سایر پیام‌ها', 'اتصال تلگرام', 'اختیاری؛ دسترسی وب محدود نمی‌شود', 'خانه', 'بازار', 'پیام‌رسان', 'عملیات', 'حساب'],
    };
    const expectedFamilyActions = {
      auth: ['0912 345 6789', 'دریافت کد تأیید'],
      'home-shell': ['اعلان‌ها', 'خانه', 'بازار', 'پیام‌رسان', 'عملیات', 'حساب'],
      'operations-workspaces': ['اعلان‌ها', 'جستجوی مشتری', 'افزودن مشتری', '۲ دعوت در انتظار', 'دعوت دعوت نمونه 0912 *** 0003 · تا ۲۲ مرداد', 'فعال مشتری نمونه یک 0912 *** 0004 · سطح ۲', 'فعال مشتری نمونه دو 0912 *** 0005 · سطح ۱', 'غیرفعال مشتری نمونه سه 0912 *** 0006', 'خانه', 'بازار', 'پیام‌رسان', 'عملیات', 'حساب'],
      'admin-invitations': ['اعلان‌ها', 'نام، شماره یا حساب', 'فعال کاربر نمونه 0912 *** 0000 · کاربر عادی', 'فعال کاربر نمونه با نام طولانی 0912 *** 0001 · تماشا', 'نیازمند اقدام کاربر آزمایشی 0912 *** 0002 · غیرفعال', 'خانه', 'بازار', 'پیام‌رسان', 'عملیات', 'حساب'],
      'account-profile': ['اعلان‌ها', 'پروفایل آواتار و آدرس حساب', 'امنیت و نشست‌ها دستگاه‌های فعال این حساب', 'حافظه و داده‌ها فایل‌های ذخیره‌شده همین دستگاه', 'اعلان‌ها معاملات و سایر پیام‌ها', 'اتصال تلگرام اختیاری؛ دسترسی وب محدود نمی‌شود', 'خانه', 'بازار', 'پیام‌رسان', 'عملیات', 'حساب'],
    };
    const familyParity = familyRoots.map((root) => ({
      family: root.dataset.family,
      source: root.dataset.approvedSource,
      tokens: visibleTokens(root),
      expectedTokens: expectedFamilyTokens[root.dataset.family],
      actions: interactiveLabels(root),
      expectedActions: expectedFamilyActions[root.dataset.family],
    }));
    const authInput = q('[data-family="auth"] input.canonical-focus-input');
    const authInputStyle = authInput ? getComputedStyle(authInput) : null;
    const authVisualState = authInput ? { value: authInput.value, direction: authInputStyle.direction, textAlign: authInputStyle.textAlign, borderColor: authInputStyle.borderTopColor, borderWidth: round(parseFloat(authInputStyle.borderTopWidth)), readOnly: authInput.readOnly } : null;
    const factNodes = qa('[data-product-root] [data-fact-origin]');
    record(expectedAssertionIds[3], familyParity.length === 5 && familyParity.every((item) => item.source === familySourceMap[item.family] && exactMultiset(item.tokens, item.expectedTokens) && exact(item.actions, item.expectedActions)) && authVisualState?.value === '0912 345 6789' && authVisualState?.direction === 'ltr' && authVisualState?.borderColor === 'rgb(31, 94, 216)' && authVisualState?.borderWidth === 2 && authVisualState?.readOnly === false && factNodes.length > 0 && factNodes.every((node) => node.dataset.factOrigin === 'approved'), { familyBindings: familyParity.map((item) => [item.family, item.source]), exactVisibleFactParity: familyParity.map((item) => ({ family: item.family, passed: exactMultiset(item.tokens, item.expectedTokens), actual: multiset(item.tokens), expected: multiset(item.expectedTokens) })), exactActionParity: familyParity.map((item) => ({ family: item.family, passed: exact(item.actions, item.expectedActions), actual: item.actions, expected: item.expectedActions })), authVisualState, approvedFacts: factNodes.length });
    const newFeatureFacts = qa('[data-new-feature]');
    const nonApprovedFacts = factNodes.filter((node) => node.dataset.factOrigin !== 'approved');
    const canonicalDriftPhrases = ['امروز چه کاری دارید؟', 'اطلاعات همین حالا به‌روز است.', 'اقدام‌های حساس مستقل‌اند', 'شماره دیگران ماسک و نشانی پنهان می‌ماند.', 'راهنمای بازیابی ورود', 'مرور و ثبت تغییر'];
    const canonicalDriftFindings = familyRoots.flatMap((root) => canonicalDriftPhrases.filter((phrase) => root.innerText.includes(phrase)).map((phrase) => ({ family: root.dataset.family, phrase })));
    record(expectedAssertionIds[4], newFeatureFacts.length === 0 && nonApprovedFacts.length === 0 && canonicalDriftFindings.length === 0 && familyParity.every((item) => exactMultiset(item.tokens, item.expectedTokens) && exact(item.actions, item.expectedActions)), { newFeatureFacts: newFeatureFacts.length, nonApprovedFacts: nonApprovedFacts.length, canonicalDriftFindings });

    const runtimeProof = q('[data-runtime-external-proof]');
    const runtimeBoundary = runtimeProof ? { kind: runtimeProof.dataset.kind, status: runtimeProof.dataset.status, localHarnessClaim: runtimeProof.dataset.localHarnessClaim } : null;
    record(expectedAssertionIds[5], runtimeBoundary?.kind === 'external-read-only' && runtimeBoundary?.status === 'passed' && runtimeBoundary?.localHarnessClaim === 'false', runtimeBoundary);
    record(expectedAssertionIds[6], document.body.dataset.designDirection === 'modern-finance' && document.body.dataset.canonicalKind === 'figma' && document.body.dataset.localEvidenceRole === 'secondary-derivative' && document.body.dataset.mobileFirstShare === '95', { designDirection: document.body.dataset.designDirection, canonicalKind: document.body.dataset.canonicalKind, localEvidenceRole: document.body.dataset.localEvidenceRole, mobileFirstShare: document.body.dataset.mobileFirstShare });

    const rootFonts = qa('[data-product-root]').map((root) => getComputedStyle(root).fontFamily);
    record(expectedAssertionIds[7], fontsLoaded === true && rootFonts.length === 11 && rootFonts.every((font) => /Vazirmatn/i.test(font)), { fontsLoaded, rootCount: rootFonts.length, families: sortedUnique(rootFonts) });

    const foundation = q('[data-variable-count]');
    const foundationCounts = foundation ? { variables: Number(foundation.dataset.variableCount), textStyles: Number(foundation.dataset.textStyleCount), effects: Number(foundation.dataset.effectCount) } : null;
    record(expectedAssertionIds[8], foundationCounts?.variables === 65 && foundationCounts?.textStyles === 9 && foundationCounts?.effects === 2, foundationCounts);
    record(expectedAssertionIds[9], Number(foundation?.dataset.brokenAliasCount) === 0, { brokenAliases: Number(foundation?.dataset.brokenAliasCount) });
    const components = q('[data-component-set-count]');
    const componentCounts = components ? { sets: Number(components.dataset.componentSetCount), variants: Number(components.dataset.componentVariantCount), HomeDelta: Number(components.dataset.componentVariantDelta) } : null;
    record(expectedAssertionIds[10], componentCounts?.sets === 12 && componentCounts?.variants === 56 && componentCounts?.HomeDelta === 2, componentCounts);
    record(expectedAssertionIds[11], Number(q('[data-detached-product-instance-count]')?.dataset.detachedProductInstanceCount) === 0, { detachedProductInstances: Number(q('[data-detached-product-instance-count]')?.dataset.detachedProductInstanceCount) });

    const debtGates = qa('[data-debt-gate]').map((node) => ({ gate: node.dataset.debtGate, status: node.dataset.status }));
    const carryForward = q('[data-debt-carry-forward]');
    record(expectedAssertionIds[12], exact(debtGates, ['auth-canonicalization', 'home-binding', 'operations-navigation'].map((gate) => ({ gate, status: 'resolved' }))) && carryForward?.dataset.debtCarryForward === 'avatar-initials-text-style' && carryForward?.dataset.status === 'documented', { debtGates, carryForward: carryForward ? { debt: carryForward.dataset.debtCarryForward, status: carryForward.dataset.status } : null });

    const familyNames = familyRoots.map((root) => root.dataset.family);
    record(expectedAssertionIds[13], exact(familyNames, expectedFamilies), { families: familyNames });
    const mobileDimensions = familyRoots.map((root) => ({ family: root.dataset.family, ...dims(root) }));
    record(expectedAssertionIds[14], mobileDimensions.length === 5 && mobileDimensions.every((item) => item.width === 390 && item.height === 844), mobileDimensions);

    const responsiveRoots = qa('[data-responsive-proof]');
    const responsiveDimensions = responsiveRoots.map((root) => ({ declaredWidth: Number(root.dataset.width), ...dims(root) }));
    const responsiveParity = responsiveRoots.map((root) => ({ width: Number(root.dataset.width), source: root.dataset.approvedSource, tokens: visibleTokens(root), actions: interactiveLabels(root), bodyChildren: q('.screen-content', root)?.children.length ?? -1 }));
    record(expectedAssertionIds[15], exact(responsiveDimensions.map((item) => item.declaredWidth), [360, 375, 390, 414, 430]) && responsiveDimensions.every((item) => item.width === item.declaredWidth && item.height === 844) && responsiveParity.every((item) => item.source === '0B-2' && item.bodyChildren === 0 && exactMultiset(item.tokens, expectedFamilyTokens['home-shell']) && exact(item.actions, expectedFamilyActions['home-shell'])), { dimensions: responsiveDimensions, exactQuietHomeParity: responsiveParity.map((item) => ({ width: item.width, source: item.source, bodyChildren: item.bodyChildren, tokenParity: exactMultiset(item.tokens, expectedFamilyTokens['home-shell']), actionParity: exact(item.actions, expectedFamilyActions['home-shell']) })) });

    const archetypes = qa('[data-desktop-archetype]').map((node) => ({ name: node.dataset.desktopArchetype, status: node.dataset.status }));
    const expectedArchetypes = ['public-centered-auth', 'home-contained-shell', 'operations-workspace-list-detail', 'admin-list-detail', 'account-security-contained-detail'];
    const desktopProof = q('[data-desktop-proof]');
    const desktopDimensions = desktopProof ? dims(desktopProof) : null;
    record(expectedAssertionIds[16], exact(archetypes, expectedArchetypes.map((name) => ({ name, status: 'complete' }))) && desktopDimensions?.width === 1440 && desktopDimensions?.height === 900, { archetypes, desktopDimensions });

    const expectedDesktopTokens = ['♢', 'حساب', 'عملیات', 'پیام‌رسان', 'بازار', 'خانه', 'نگار پارسا', 'ن', 'افزودن مشتری', 'مشتریان', 'فعال', 'مشتری نمونه یک', '0912 *** 0004', 'تنظیمات مالی', 'سطح حساب', 'سطح ۲', 'کارمزد', '۰٫۵٪', 'سقف خرید', 'بدون محدودیت', 'سقف فروش', '۵۰ گرم', 'اثر تغییر', 'کارمزد و سقف‌های جدید فقط روی معاملات آینده اثر دارند؛ تاریخچه معاملات تغییر نمی‌کند.', 'مرور تغییرات', 'جست‌وجوی مشتری', '۲ دعوت در انتظار', 'فعال', 'مشتری نمونه یک', '0912 *** 0004', 'دعوت', 'مشتری فروشگاه', 'تا ۲۲ مرداد', 'فعال', 'فروشگاه مرکزی', '0912 *** 0005', 'غیرفعال', 'مشتری قدیمی', '0912 *** 0006'];
    const expectedDesktopActions = ['اعلان‌ها', 'خانه', 'بازار', 'پیام‌رسان', 'عملیات', 'حساب', 'افزودن مشتری', 'جست‌وجوی مشتری', '۲ دعوت در انتظار', 'فعال مشتری نمونه یک 0912 *** 0004', 'دعوت مشتری فروشگاه تا ۲۲ مرداد', 'فعال فروشگاه مرکزی 0912 *** 0005', 'غیرفعال مشتری قدیمی 0912 *** 0006', 'مرور تغییرات'];
    const desktopTokens = desktopProof ? visibleTokens(desktopProof) : [];
    const desktopActions = desktopProof ? interactiveLabels(desktopProof) : [];
    const desktopPrimaryCtaDimensions = desktopProof ? qa('.primary-cta', desktopProof).filter(isVisible).map(dims) : [];
    const selectedDesktopRow = desktopProof ? q('.list-row.selected[aria-current="true"]', desktopProof) : null;
    const unselectedDesktopRow = desktopProof ? q('.list-row:not(.selected)', desktopProof) : null;
    const selectedDesktopStyle = selectedDesktopRow ? getComputedStyle(selectedDesktopRow) : null;
    const unselectedDesktopStyle = unselectedDesktopRow ? getComputedStyle(unselectedDesktopRow) : null;
    const desktopSelectionState = selectedDesktopRow && unselectedDesktopRow ? { selectedBorderColor: selectedDesktopStyle.borderTopColor, unselectedBorderColor: unselectedDesktopStyle.borderTopColor, selectedBackground: selectedDesktopStyle.backgroundColor, unselectedBackground: unselectedDesktopStyle.backgroundColor, distinct: selectedDesktopStyle.borderTopColor !== unselectedDesktopStyle.borderTopColor && selectedDesktopStyle.backgroundColor !== unselectedDesktopStyle.backgroundColor } : null;
    const desktopFacts = desktopProof ? Object.fromEntries(qa('[data-fact]', desktopProof).map((node) => [node.dataset.fact, node.dataset.value])) : {};
    const expectedDesktopFacts = { task: 'customer-management', selected: 'customer-one', status: 'active', level: '2', commission: '0.5-percent', 'buy-limit': 'unlimited', 'sell-limit': '50-grams', scope: 'future-only' };
    record(expectedAssertionIds[17], exactMultiset(desktopTokens, expectedDesktopTokens) && exact(desktopActions, expectedDesktopActions) && exact(desktopFacts, expectedDesktopFacts) && desktopPrimaryCtaDimensions.length === 2 && desktopPrimaryCtaDimensions.every((item) => item.height === 48) && desktopSelectionState?.distinct === true, { exactVisibleFactParity: exactMultiset(desktopTokens, expectedDesktopTokens), actualTokens: multiset(desktopTokens), expectedTokens: multiset(expectedDesktopTokens), exactActionParity: exact(desktopActions, expectedDesktopActions), actualActions: desktopActions, expectedActions: expectedDesktopActions, desktopFacts, expectedDesktopFacts, desktopPrimaryCtaDimensions, desktopSelectionState });

    const productRoots = qa('[data-product-root]');
    const rootOverflow = productRoots.map((root) => ({
      kind: root.dataset.family || root.dataset.width || (root.hasAttribute('data-desktop-proof') ? 'desktop' : 'unknown'),
      horizontal: round(root.scrollWidth - root.clientWidth),
      vertical: round(root.scrollHeight - root.clientHeight),
    }));
    const fitClipping = qa('[data-product-root] [data-fit]').filter((node) => {
      const style = getComputedStyle(node);
      if (!isVisible(node) || style.display === 'inline' || node.clientWidth === 0 || node.clientHeight === 0) return false;
      const clipsX = style.overflowX === 'hidden' || style.overflowX === 'clip';
      const clipsY = style.overflowY === 'hidden' || style.overflowY === 'clip';
      return (clipsX && node.scrollWidth - node.clientWidth > 1) || (clipsY && node.scrollHeight - node.clientHeight > 1);
    }).map((node) => ({ text: node.textContent.trim().slice(0, 80), overflowX: round(node.scrollWidth - node.clientWidth), overflowY: round(node.scrollHeight - node.clientHeight) }));
    record(expectedAssertionIds[18], rootOverflow.every((item) => item.horizontal <= 1 && item.vertical <= 1) && fitClipping.length === 0, { rootOverflow, fitClipping });

    const interactive = productRoots.flatMap((root) => qa('button, input, select, textarea, a[href], [role="button"], [tabindex]:not([tabindex="-1"])', root)).filter(isVisible);
    const touchDimensions = interactive.map((node) => ({ tag: node.tagName.toLowerCase(), label: (node.getAttribute('aria-label') || node.textContent || node.value || '').trim().slice(0, 50), ...dims(node) }));
    const minimumTouchWidth = Math.min(...touchDimensions.map((item) => item.width));
    const minimumTouchHeight = Math.min(...touchDimensions.map((item) => item.height));
    record(expectedAssertionIds[19], touchDimensions.length > 0 && touchDimensions.every((item) => item.width >= 44 && item.height >= 44), { count: touchDimensions.length, minimumWidth: minimumTouchWidth, minimumHeight: minimumTouchHeight, failures: touchDimensions.filter((item) => item.width < 44 || item.height < 44) });

    const ctas = qa('[data-product-root] .primary-cta').filter(isVisible).map((node) => ({ label: node.textContent.trim(), ...dims(node) }));
    const minimumCtaHeight = Math.min(...ctas.map((item) => item.height));
    record(expectedAssertionIds[20], ctas.length > 0 && ctas.every((item) => item.height >= 48), { count: ctas.length, minimumHeight: minimumCtaHeight, failures: ctas.filter((item) => item.height < 48) });

    const navigationLabels = qa('[data-product-root] [data-nav-label]').filter(isVisible).map((node) => ({ label: node.textContent.trim(), fontSize: round(parseFloat(getComputedStyle(node).fontSize)) }));
    const minimumNavigationLabel = Math.min(...navigationLabels.map((item) => item.fontSize));
    record(expectedAssertionIds[21], navigationLabels.length > 0 && navigationLabels.every((item) => item.fontSize >= 11), { count: navigationLabels.length, minimumPx: minimumNavigationLabel });

    const contrastPairs = qa('[data-contrast-pair]').map((node) => ({ foreground: node.dataset.foreground, background: node.dataset.background, ratio: contrast(node.dataset.foreground, node.dataset.background) }));
    const minimumTextContrast = Math.min(...contrastPairs.map((item) => item.ratio));
    record(expectedAssertionIds[22], contrastPairs.length === 5 && contrastPairs.every((item) => item.ratio >= 4.5), { pairs: contrastPairs, minimum: minimumTextContrast });

    const focus = q('[data-focus-contract]');
    const focusEvidence = focus ? { color: focus.dataset.color, page: focus.dataset.page, surface: focus.dataset.surface, stroke: Number(focus.dataset.stroke), pageContrast: contrast(focus.dataset.color, focus.dataset.page), surfaceContrast: contrast(focus.dataset.color, focus.dataset.surface) } : null;
    record(expectedAssertionIds[23], focusEvidence?.stroke === 3 && focusEvidence?.pageContrast >= 3 && focusEvidence?.surfaceContrast >= 3, focusEvidence);

    const shells = qa('[data-shell-kind]').map((node) => node.dataset.shellKind);
    const layers = qa('[data-layer-priority]').map((node) => Number(node.dataset.layerPriority));
    const routes = qa('[data-route]').map((node) => node.dataset.route);
    const routeContract = q('[data-route-contract]');
    record(expectedAssertionIds[24], exact(sortedUnique(shells), sortedUnique(['public', 'standard-authenticated', 'focused-authenticated', 'invalid-forbidden-recovery', 'protected-legacy'])) && shells.length === 5 && exact(layers, [1, 2, 3, 4, 5]) && routes.length === 29 && new Set(routes).size === 29 && Number(routeContract?.dataset.routeCount) === 29 && routeContract?.dataset.catchAll === 'planned', { shells, layers, routeCount: routes.length, uniqueRouteCount: new Set(routes).size, catchAll: routeContract?.dataset.catchAll });

    const commonStates = qa('[data-common-state]').map((node) => node.dataset.commonState);
    const actionStates = qa('[data-action-state]').map((node) => node.dataset.actionState);
    record(expectedAssertionIds[25], exact(commonStates, ['loading', 'empty', 'normal-dense', 'error-retry', 'offline', 'stale-reconnecting']) && exact(actionStates, ['confirm', 'busy', 'success', 'failure-preserved']), { commonStates, actionStates });

    const motion = q('[data-motion-contract]');
    const motionEvidence = motion ? { microMs: Number(motion.dataset.microMs), stateMs: Number(motion.dataset.stateMs), reducedMotion: motion.dataset.reducedMotion } : null;
    record(expectedAssertionIds[26], motionEvidence?.microMs === 140 && motionEvidence?.stateMs === 180 && motionEvidence?.reducedMotion === 'true', motionEvidence);

    const contentFamilies = qa('[data-content-family]').map((node) => ({ family: node.dataset.contentFamily, status: node.dataset.status }));
    const defaultUnits = qa('[data-product-root] [data-default-unit]');
    const unclassifiedUnits = defaultUnits.filter((node) => node.dataset.necessity !== 'keep');
    const exactFamilyContent = familyParity.every((item) => exactMultiset(item.tokens, item.expectedTokens) && exact(item.actions, item.expectedActions));
    const exactResponsiveContent = responsiveParity.every((item) => item.bodyChildren === 0 && exactMultiset(item.tokens, expectedFamilyTokens['home-shell']) && exact(item.actions, expectedFamilyActions['home-shell']));
    const exactDesktopContent = exactMultiset(desktopTokens, expectedDesktopTokens) && exact(desktopActions, expectedDesktopActions);
    record(expectedAssertionIds[27], exact(contentFamilies, expectedFamilies.map((family) => ({ family, status: 'complete' }))) && defaultUnits.length > 0 && unclassifiedUnits.length === 0 && exactFamilyContent && exactResponsiveContent && exactDesktopContent, { contentFamilies, defaultUnitCount: defaultUnits.length, unclassified: unclassifiedUnits.length, exactFamilyContent, exactResponsiveContent, exactDesktopContent, policy: 'Every always-visible unit is bounded by exact canonical text/action signatures; whitespace is valid and Home bodies are empty.' });

    const metadataPatterns = [
      /(?:node\s*id|file\s*key|run\s*id|backend|api\b|sha(?:256)?\b|commit\b|figma\b)/i,
      /(?:^|\s)\/(?:admin|account|operations|market|chat|profile|settings|notifications|share-receive)(?:\/|\s|$)/i,
      /(?:اقدام‌های حساس مستقل‌اند|با هم ادغام نمی‌شوند|هیچ واقعیت تازه‌ای نسبت به مرجع|نسخه تطبیقی همان تجربه|فضای کاری یکپارچه)/,
    ];
    const metadataFindings = [];
    for (const root of productRoots) {
      const samples = [root.innerText, ...qa('*', root).flatMap((node) => ['title', 'aria-description'].map((attribute) => node.getAttribute(attribute)).filter(Boolean))];
      for (const sample of samples) for (const pattern of metadataPatterns) if (pattern.test(sample)) metadataFindings.push({ sample: sample.slice(0, 120), pattern: pattern.source });
    }
    record(expectedAssertionIds[28], metadataFindings.length === 0, { findings: metadataFindings });

    const forbiddenCopyPatterns = [/تعداد روابط/, /تعداد ابزار/, /مسیر فعال/, /حساب فعال/, /\b(?:09|\+98)\d{9,10}\b/];
    const identityEvidence = productRoots.map((root) => ({ synthetic: root.dataset.synthetic, text: root.innerText }));
    const forbiddenCopyFindings = [];
    for (const item of identityEvidence) for (const pattern of forbiddenCopyPatterns) if (pattern.test(item.text)) forbiddenCopyFindings.push(pattern.source);
    record(expectedAssertionIds[29], identityEvidence.length === 11 && identityEvidence.every((item) => item.synthetic === 'true') && forbiddenCopyFindings.length === 0, { productRootCount: identityEvidence.length, allSynthetic: identityEvidence.every((item) => item.synthetic === 'true'), forbiddenCopyFindings });

    const protectedInteriors = qa('[data-protected-interior]');
    const protectedReferences = qa('[data-protected-reference]').map((node) => ({ name: node.dataset.protectedReference, status: node.dataset.status }));
    record(expectedAssertionIds[30], protectedInteriors.length === 0 && exact(protectedReferences, ['market', 'messenger', 'share-receive', 'admin-channels'].map((name) => ({ name, status: 'omitted' }))), { protectedInteriorCount: protectedInteriors.length, protectedReferences });

    const gate = q('[data-implementation-gate]');
    const staticLimits = qa('[data-static-limit]').map((node) => node.dataset.staticLimit);
    const gateEvidence = gate ? { ownerApproval: gate.dataset.ownerApproval, runtimeAuthorized: gate.dataset.runtimeAuthorized, nextStage: gate.dataset.nextStage, staticLimits } : null;
    record(expectedAssertionIds[31], gateEvidence?.ownerApproval === 'pending' && gateEvidence?.runtimeAuthorized === 'false' && gateEvidence?.nextStage === 'none' && exact(staticLimits, ['authorization', 'mutation', 'delivery', 'realtime', 'keyboard', 'screen-reader']), gateEvidence);

    if (!exact(assertionResults.map((item) => item.id), expectedAssertionIds)) throw new Error('Audit implementation emitted assertions out of contract order');

    return {
      assertions: assertionResults,
      passed: assertionResults.filter((item) => item.passed).length,
      failed: assertionResults.filter((item) => !item.passed).length,
      metrics: {
        productRootCount: productRoots.length,
        familyReferences: mobileDimensions,
        responsiveProofs: responsiveDimensions,
        desktopProof: desktopDimensions,
        productOverflow: rootOverflow,
        fitClippingFindings: fitClipping.length,
        touchTargets: { count: touchDimensions.length, minimumWidth: minimumTouchWidth, minimumHeight: minimumTouchHeight },
        primaryCtas: { count: ctas.length, minimumHeight: minimumCtaHeight },
        navigationLabels: { count: navigationLabels.length, minimumPx: minimumNavigationLabel },
        textContrast: { pairs: contrastPairs, minimum: minimumTextContrast },
        focus: focusEvidence,
        foundations: foundationCounts,
        components: componentCounts,
        routes: { count: routes.length, unique: new Set(routes).size },
        canonicalContentParity: {
          familyRoots: familyParity.map((item) => ({ family: item.family, visibleFactsExact: exactMultiset(item.tokens, item.expectedTokens), actionsExact: exact(item.actions, item.expectedActions) })),
          responsiveQuietHomeExact: exactResponsiveContent,
          desktopVisibleFactsExact: exactMultiset(desktopTokens, expectedDesktopTokens),
          desktopActionsExact: exact(desktopActions, expectedDesktopActions),
          driftFindings: canonicalDriftFindings,
        },
        contentDefaultUnits: defaultUnits.length,
        protectedInteriorFindings: protectedInteriors.length,
        reviewerMetadataFindings: metadataFindings.length,
        forbiddenCopyFindings: forbiddenCopyFindings.length,
      },
    };
  }, { expectedAssertionIds: EXPECTED_ASSERTION_IDS, sourceReferencesResolved: context.sourceReferencesResolved, fontsLoaded: context.fontsLoaded });
}

function createRunId() {
  const time = new Date().toISOString().replace(/[-:.]/g, '').replace('Z', 'Z');
  return `stage0b6-${time}-${crypto.randomBytes(4).toString('hex')}`;
}

function atomicPromote(stagingDir, runId) {
  validateEvidenceDirectory(stagingDir);
  const backupDir = path.join(ASSETS_DIR, `.local-evidence-backup-${runId}`);
  assert(!fs.existsSync(backupDir), `Promotion backup already exists: ${backupDir}`);
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
  } catch (error) {
    if (fs.existsSync(PUBLISHED_DIR)) removeDirectory(PUBLISHED_DIR);
    if (movedCurrent && fs.existsSync(backupDir)) {
      fs.renameSync(backupDir, PUBLISHED_DIR);
      validateEvidenceDirectory(PUBLISHED_DIR);
    }
    throw new Error(`Atomic promotion failed and was rolled back: ${error.message}`);
  }
}

async function main() {
  const startedAt = new Date().toISOString();
  const runId = createRunId();
  const stagingDir = path.join(ASSETS_DIR, `.local-evidence-staging-${runId}`);

  // This ordering is contractual: recover interrupted publication before any
  // browser or font dependency is resolved.
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
    const context = await browser.newContext({
      viewport: { width: 2300, height: 1200 },
      deviceScaleFactor: 1,
      locale: 'fa-IR',
      colorScheme: 'light',
      reducedMotion: 'reduce',
    });
    const page = await context.newPage();
    page.on('console', (message) => { if (message.type() === 'error') consoleErrors.push(message.text()); });
    page.on('pageerror', (error) => pageErrors.push(error.message));
    page.on('requestfailed', (request) => failedRequests.push({ url: request.url(), failure: request.failure()?.errorText || 'unknown' }));
    await page.route(/^https?:\/\//, async (route) => {
      blockedNetworkRequests.push(route.request().url());
      await route.abort('blockedbyclient');
    });

    await page.goto(pathToFileURL(HTML_PATH).href, { waitUntil: 'load' });
    const fontCss = fontBundle.fonts.map((font) => `@font-face{font-family:'Vazirmatn';font-style:normal;font-weight:${font.weight};font-display:block;src:url(data:font/woff2;base64,${font.data}) format('woff2');}`).join('\n');
    await page.addStyleTag({ content: fontCss });
    await page.evaluate(async () => {
      await Promise.all([400, 500, 600, 700].map((weight) => document.fonts.load(`${weight} 16px Vazirmatn`, 'آزمون')));
      await document.fonts.ready;
    });
    const fontChecks = await page.evaluate(() => [400, 500, 600, 700].map((weight) => ({ weight, loaded: document.fonts.check(`${weight} 16px Vazirmatn`, 'آزمون') })));
    const fontsLoaded = fontChecks.every((item) => item.loaded);
    assert(fontsLoaded, `Vazirmatn font load failed: ${JSON.stringify(fontChecks)}`);

    const preAudit = await auditPage(page, { sourceReferencesResolved: canonicalInputs.sourceReferences.every((item) => item.resolved), fontsLoaded });
    assert(preAudit.failed === 0 && preAudit.passed === 32, `Pre-capture assertion failure: ${JSON.stringify(preAudit.assertions.filter((item) => !item.passed), null, 2)}`);
    const preDom = await canonicalDomSnapshot(page);
    const preDomSha256 = sha256Buffer(Buffer.from(preDom));
    const preAuditSha256 = hashValue(preAudit);

    const captures = [];
    for (const spec of CAPTURE_SPECS) {
      const locator = page.locator(spec.selector);
      assert(await locator.count() === 1, `Capture selector must resolve exactly once: ${spec.selector}`);
      const box = await locator.evaluate((element) => {
        const rect = element.getBoundingClientRect();
        return {
          x: rect.left + window.scrollX,
          y: rect.top + window.scrollY,
          width: rect.width,
          height: rect.height,
          documentWidth: document.documentElement.scrollWidth,
          documentHeight: document.documentElement.scrollHeight,
        };
      });
      assert(box.width > 0 && box.height > 0, `Capture target has no geometry: ${spec.selector}`);
      assert(box.x >= 0 && box.y >= 0 && box.x + box.width <= box.documentWidth + 0.01 && box.y + box.height <= box.documentHeight + 0.01, `Capture target lies outside the document: ${spec.file} ${JSON.stringify(box)}`);
      if (spec.width) assert(Math.round(box.width) === spec.width && Math.round(box.height) === spec.height, `Exact capture geometry mismatch for ${spec.file}: ${box.width}x${box.height}`);
      const outputPath = path.join(stagingDir, spec.file);
      const captureWidth = spec.width || Math.round(box.width);
      const captureHeight = spec.height || Math.round(box.height);
      const originalStyle = await locator.getAttribute('style');
      try {
        await page.setViewportSize({ width: Math.max(2300, captureWidth), height: captureHeight });
        await locator.evaluate((element) => {
          element.style.setProperty('position', 'fixed', 'important');
          element.style.setProperty('inset', '0 auto auto 0', 'important');
          element.style.setProperty('margin', '0', 'important');
          element.style.setProperty('transform', 'none', 'important');
          element.style.setProperty('z-index', '2147483647', 'important');
        });
        await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
        const fixedBox = await locator.boundingBox();
        assert(fixedBox && fixedBox.x === 0 && fixedBox.y === 0 && Math.round(fixedBox.width) === captureWidth && Math.round(fixedBox.height) === captureHeight, `Temporary capture geometry mismatch for ${spec.file}: ${JSON.stringify(fixedBox)}`);
        await page.screenshot({
          path: outputPath,
          animations: 'disabled',
          caret: 'hide',
          clip: { x: 0, y: 0, width: captureWidth, height: captureHeight },
        });
      } finally {
        await locator.evaluate((element, style) => {
          if (style === null) element.removeAttribute('style');
          else element.setAttribute('style', style);
        }, originalStyle);
        await page.setViewportSize({ width: 2300, height: 1200 });
      }
      const dimensions = pngDimensions(outputPath);
      const visualStats = pngVisualStats(outputPath);
      if (spec.width) assert(dimensions.width === spec.width && dimensions.height === spec.height, `Exact PNG geometry mismatch for ${spec.file}: ${dimensions.width}x${dimensions.height}`);
      assert(visualStats.uniqueColorCount >= 8 && visualStats.changedPixelCount >= 100, `Capture is visually blank or degenerate: ${spec.file} ${JSON.stringify(visualStats)}`);
      captures.push({
        file: spec.file,
        selector: spec.selector,
        width: dimensions.width,
        height: dimensions.height,
        bytes: fs.statSync(outputPath).size,
        sha256: sha256File(outputPath),
        visualStats,
      });
    }

    const postAudit = await auditPage(page, { sourceReferencesResolved: canonicalInputs.sourceReferences.every((item) => item.resolved), fontsLoaded });
    const postDom = await canonicalDomSnapshot(page);
    const postDomSha256 = sha256Buffer(Buffer.from(postDom));
    const postAuditSha256 = hashValue(postAudit);
    assert(postAudit.failed === 0 && postAudit.passed === 32, `Post-capture assertion failure: ${JSON.stringify(postAudit.assertions.filter((item) => !item.passed), null, 2)}`);
    assert(preAuditSha256 === postAuditSha256 && stableJson(preAudit) === stableJson(postAudit), 'Pre/post audit measurements changed during capture');
    assert(preDomSha256 === postDomSha256 && preDom === postDom, 'Canonical DOM changed during capture');
    assert(consoleErrors.length === 0, `Browser console errors: ${consoleErrors.join(' | ')}`);
    assert(pageErrors.length === 0, `Browser page errors: ${pageErrors.join(' | ')}`);
    assert(failedRequests.length === 0, `Browser request failures: ${JSON.stringify(failedRequests)}`);
    assert(blockedNetworkRequests.length === 0, `Evidence attempted network access: ${blockedNetworkRequests.join(', ')}`);

    const completedAt = new Date().toISOString();
    const metrics = {
      schemaVersion: 1,
      stage: '0B-6',
      status: 'passed',
      runId,
      startedAt,
      completedAt,
      canonicalFigma: { ...FIGMA_FREEZE, directAudit: FIGMA_DIRECT_AUDIT, exactAssertionIds: EXPECTED_ASSERTION_IDS },
      directFigmaReconciliation: {
        status: 'passed',
        provenance: 'hash-bound post-freeze direct audit JSON; not inferred by local browser',
        auditedAt: FIGMA_FREEZE.auditedAt,
        assertionCount: FIGMA_DIRECT_AUDIT.assertionCount,
        passed: FIGMA_DIRECT_AUDIT.passed,
        failed: FIGMA_DIRECT_AUDIT.failed,
        exactAssertionIds: EXPECTED_ASSERTION_IDS,
        localHarnessClaim: false,
        auditJson: { path: canonicalInputs.directAuditJson.path, bytes: canonicalInputs.directAuditJson.bytes, sha256: canonicalInputs.directAuditJson.sha256, schema: canonicalInputs.directAuditJson.parsed.schema },
        exports: canonicalInputs.directExports,
        exportAggregateSha256: canonicalInputs.directExportAggregateSha256,
      },
      localEvidence: {
        role: FIGMA_FREEZE.localEvidenceRole,
        canonical: false,
        productBehaviorProof: false,
        sourceHtml: canonicalInputs.html,
        mobileFirstShare: 95,
      },
      runtimeDiffProof: {
        kind: 'external-read-only',
        status: 'passed',
        runtimeDiff: 'empty',
        localHarnessClaim: false,
        behaviorProven: false,
      },
      protectedScope: {
        omitted: ['market', 'messenger', 'share-receive', 'admin-channels'],
        localInteriorCaptures: 0,
      },
      sourceReferences: canonicalInputs.sourceReferences,
      recoveryBeforeDependencyResolution: recovery,
      dependencies: {
        node: process.version,
        platform: `${process.platform}-${process.arch}`,
        playwright: playwright.resolvedFrom,
        browser: { engine: 'chromium', version: browserVersion, viewport: { width: 2300, height: 1200 }, deviceScaleFactor: 1 },
        fonts: { family: 'Vazirmatn', root: fontBundle.root, checks: fontChecks, files: fontBundle.fonts.map(({ file, weight, bytes, sha256 }) => ({ file, weight, bytes, sha256 })) },
      },
      integrity: {
        preDomSha256,
        postDomSha256,
        domEqual: true,
        preAuditSha256,
        postAuditSha256,
        auditEqual: true,
        postCaptureRemeasurement: true,
        consoleErrors,
        pageErrors,
        failedRequests,
        blockedNetworkRequests,
      },
      assertions: postAudit.assertions,
      assertionSummary: { total: postAudit.assertions.length, passed: postAudit.passed, failed: postAudit.failed, exactOrder: true },
      measurements: postAudit.metrics,
      captures,
      outputSet: { policy: 'exact', pngCount: 7, metricsCount: 1, files: EXACT_OUTPUT_FILES },
      publication: { strategy: 'atomic-directory-rename', partialPromotionAllowed: false, validationBeforePromotion: true, validationAfterPromotion: true },
    };

    fs.writeFileSync(path.join(stagingDir, METRICS_FILE), `${JSON.stringify(metrics, null, 2)}\n`, { encoding: 'utf8', flag: 'wx' });
    validateEvidenceDirectory(stagingDir);
    await context.close();
    await browser.close();
    browser = null;

    atomicPromote(stagingDir, runId);
    const published = validateEvidenceDirectory(PUBLISHED_DIR);
    const report = {
      status: published.status,
      runId: published.runId,
      canonicalFreeze: published.canonicalFigma.frozenAt,
      assertions: `${published.assertionSummary.passed}/${published.assertionSummary.total}`,
      domSha256: published.integrity.postDomSha256,
      auditSha256: published.integrity.postAuditSha256,
      outputDirectory: path.relative(REPO_ROOT, PUBLISHED_DIR),
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
  process.stderr.write(`Stage 0B-6 evidence capture failed: ${error.stack || error.message}\n`);
  process.exitCode = 1;
});
