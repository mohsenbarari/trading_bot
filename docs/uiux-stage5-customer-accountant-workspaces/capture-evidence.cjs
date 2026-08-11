#!/usr/bin/env node
'use strict';

/**
 * Stage 5 local static evidence capture.
 *
 * The script validates the frozen manifest inputs, blocks HTTP(S), loads only
 * local images/fonts, audits the DOM before and after capture, renders six
 * real section derivatives, remeasures their PNG bytes/hashes, and publishes
 * the exact output set through an atomic directory rename.
 */

const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const zlib = require('node:zlib');
const { pathToFileURL } = require('node:url');

const CONTRACT_DIR = __dirname;
const REPO_ROOT = path.resolve(CONTRACT_DIR, '..', '..');
const ASSETS_DIR = path.join(CONTRACT_DIR, 'assets');
const PUBLISHED_DIR = path.join(ASSETS_DIR, 'local-evidence');
const HTML_FILE = 'stage5-customer-accountant-workspaces-evidence.html';
const HTML_PATH = path.join(CONTRACT_DIR, HTML_FILE);
const EVIDENCE_MANIFEST_PATH = path.join(CONTRACT_DIR, 'EVIDENCE_MANIFEST.json');
const METRICS_FILE = 'local-stage5-customer-accountant-workspaces-validation-metrics.json';

const IMPLEMENTATION_COMMIT = '08c5ae1ea95b3087893146547bed8a220eb83d2b';
const IMPLEMENTATION_TREE = '96e2f32c46668f37a4753ccaee21216a2b500097';
const IMPLEMENTATION_PARENT = '646ca6dd83b50e3efd5689e94a241745c030ec9d';
const PATHSET_SHA256 = 'c739ac017e954522ac8d96a5875e5c954e962c42c010e8e197388c98ecc4656f';
const PATH_CONTENT_SHA256 = 'b164a6ca22cd24b3e9d720f27cf2838aa27a41d8ce06102dbdb6be9103b8b8e1';
const FIGMA_FILE_KEY = 'z8jgJxST4O2APzWnlyP9gv';
const FIGMA_PAGE_ID = '297:18';
const FIGMA_ROOT_ID = '297:19';
const FIGMA_DELTA_ID = '308:556';
const FIGMA_CAPTURE_AGGREGATE = '859b032348751c73c36a77ac9dcc6e1f847782078421ab2460842df8363daba6';
const BROWSER_RUN_ID = 'uiux-stage5-browser-20260811T100859948Z';
const BROWSER_METRICS_SHA256 = '10d94ed59c9925ebc740d7af6d2d883b5f55a9b15152fcee2dac1a6e441f11ff';
const BROWSER_CAPTURE_AGGREGATE = 'ee97d7e560c11ffb3b3bc4d7f9f442ba6467050d6b7158b425ae655273d9d91f';
const BROWSER_HARNESS_SHA256 = 'a183e21df2e34486d555a4d8a662bda1055d6744a34de68543a49574483057d3';
const RUNTIME_SOURCE_AGGREGATE = 'a4555fc55f40541c6f499f4ce5a0e9ddef6f2c9e0cb79d69762a20047d46c938';
const PROTECTED_DIFF_SHA256 = 'dede6497fb8cdb06a6d076a0bad81228f965b7c7c067522697527fa364c524bc';
const LOCAL_RUN_ID = 'stage5-local-20260811T113702070Z';
const LOCAL_STARTED_AT = '2026-08-11T11:37:02.070Z';
const LOCAL_COMPLETED_AT = '2026-08-11T11:38:52.188Z';

const ASSERTION_IDS = Object.freeze([
  'claim-boundary-local-not-stage-or-sites',
  'implementation-commit-tree-parent-exact',
  'git-thirtyfour-path-binding-exact',
  'figma-final-freeze-identity-exact',
  'figma-runtime-delta-and-exports-hash-bound',
  'figma-copy-safe-anonymized-and-structural-audit-passed',
  'browser-run-23-of-23-promotable',
  'browser-source-393-pre-post-identical',
  'vitest-154-files-310-suites-1663-passed',
  'backend-4-modules-127-passed-warnings-disclosed',
  'quality-added-zero-and-protected-drift-zero',
  'static-evidence-six-sections-thirteen-images-two-attachments',
]);

const CAPTURE_SPECS = Object.freeze([
  { selector: '#stage5-overview', file: 'local-stage5-overview.png' },
  { selector: '#stage5-binding', file: 'local-stage5-binding.png' },
  { selector: '#stage5-contract', file: 'local-stage5-contract.png' },
  { selector: '#stage5-runtime', file: 'local-stage5-runtime.png' },
  { selector: '#stage5-figma', file: 'local-stage5-figma.png' },
  { selector: '#stage5-boundary', file: 'local-stage5-boundary.png' },
]);
const OUTPUT_FILES = Object.freeze([...CAPTURE_SPECS.map(({ file }) => file), METRICS_FILE].sort());

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

function sha256Buffer(buffer) {
  return crypto.createHash('sha256').update(buffer).digest('hex');
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

function readJson(filePath) {
  return JSON.parse(fs.readFileSync(filePath, 'utf8'));
}

function fileRecord(filePath, relativePath = path.relative(CONTRACT_DIR, filePath)) {
  const buffer = fs.readFileSync(filePath);
  return {
    path: relativePath.split(path.sep).join('/'),
    bytes: buffer.length,
    sha256: sha256Buffer(buffer),
  };
}

function pngDimensions(filePath) {
  const buffer = fs.readFileSync(filePath);
  assert(buffer.length >= 24, `PNG too small: ${filePath}`);
  assert(buffer.subarray(0, 8).toString('hex') === '89504e470d0a1a0a', `Invalid PNG signature: ${filePath}`);
  assert(buffer.subarray(12, 16).toString('ascii') === 'IHDR', `Missing PNG IHDR: ${filePath}`);
  return { width: buffer.readUInt32BE(16), height: buffer.readUInt32BE(20) };
}

function pngVisualStats(filePath) {
  const buffer = fs.readFileSync(filePath);
  const { width, height } = pngDimensions(filePath);
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
    for (let x = 0; x < width; x += 1) {
      const offset = x * bytesPerPixel;
      const key = row.subarray(offset, offset + bytesPerPixel).toString('hex');
      if (colors.size < 2048) colors.add(key);
      if (firstColor === null) firstColor = key;
      if (key !== firstColor) changedPixelCount += 1;
    }
    previous = row;
  }
  return { uniqueColorCount: colors.size, changedPixelCount };
}

function validatePng(filePath, expected = {}) {
  const record = fileRecord(filePath, expected.path || path.basename(filePath));
  const dimensions = pngDimensions(filePath);
  if (expected.width !== undefined) assert(dimensions.width === expected.width, `PNG width mismatch: ${filePath}`);
  if (expected.height !== undefined) assert(dimensions.height === expected.height, `PNG height mismatch: ${filePath}`);
  if (expected.bytes !== undefined) assert(record.bytes === expected.bytes, `PNG byte mismatch: ${filePath}`);
  if (expected.sha256 !== undefined) assert(record.sha256 === expected.sha256, `PNG hash mismatch: ${filePath}`);
  const visualStats = pngVisualStats(filePath);
  assert(visualStats.uniqueColorCount >= 16, `PNG lacks color variation: ${filePath}`);
  assert(visualStats.changedPixelCount >= 1000, `PNG appears blank: ${filePath}`);
  return { ...record, ...dimensions, visualStats };
}

function resolvePlaywright() {
  const candidate = path.join(REPO_ROOT, 'frontend', 'node_modules', 'playwright');
  return { module: require(candidate), source: 'frontend/node_modules/playwright' };
}

function resolveFonts() {
  const root = path.join(REPO_ROOT, 'frontend', 'node_modules', 'vazirmatn', 'fonts', 'webfonts');
  const specs = [
    { weight: 400, file: 'Vazirmatn-Regular.woff2' },
    { weight: 500, file: 'Vazirmatn-Medium.woff2' },
    { weight: 600, file: 'Vazirmatn-SemiBold.woff2' },
    { weight: 700, file: 'Vazirmatn-Bold.woff2' },
  ];
  const fonts = specs.map((spec) => {
    const filePath = path.join(root, spec.file);
    const buffer = fs.readFileSync(filePath);
    return { ...spec, bytes: buffer.length, sha256: sha256Buffer(buffer), data: buffer.toString('base64') };
  });
  return { root: 'frontend/node_modules/vazirmatn/fonts/webfonts', fonts };
}

function validateManifestInputs() {
  const manifestBuffer = fs.readFileSync(EVIDENCE_MANIFEST_PATH);
  const manifest = JSON.parse(manifestBuffer.toString('utf8'));
  assert(manifest.schemaVersion === 1 && manifest.stage === 5, 'Evidence manifest identity mismatch');
  assert(manifest.status === 'local_evidence_inputs_frozen', 'Evidence manifest is not frozen');
  assert(manifest.stageCompleteAuthority === false && manifest.sitesProven === false, 'Evidence manifest claim boundary unsafe');
  assert(Array.isArray(manifest.inputs), 'Evidence inputs must be an array');
  const projected = [];
  for (const input of manifest.inputs) {
    const actual = fileRecord(path.join(CONTRACT_DIR, input.path), input.path);
    assert(actual.bytes === input.bytes && actual.sha256 === input.sha256, `Evidence input drift: ${input.path}`);
    projected.push(actual);
  }
  assert(projected.length === manifest.inputCount, 'Evidence input count mismatch');
  assert(projected.reduce((sum, record) => sum + record.bytes, 0) === manifest.inputBytes, 'Evidence input bytes mismatch');
  assert(sha256Buffer(Buffer.from(JSON.stringify(projected))) === manifest.inputProjectionAggregateSha256, 'Evidence input aggregate mismatch');
  return { manifest, record: { path: 'EVIDENCE_MANIFEST.json', bytes: manifestBuffer.length, sha256: sha256Buffer(manifestBuffer) } };
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

async function auditPage(page, facts) {
  return page.evaluate(({ assertionIds, facts }) => {
    const html = document.documentElement;
    const number = (value) => Number(value);
    const q = (selector) => document.querySelector(selector);
    const assertions = [];
    const record = (id, passed, details) => assertions.push({ id, passed: Boolean(passed), details });
    const overview = q('#stage5-overview');
    const binding = q('#stage5-binding');
    const contract = q('#stage5-contract');
    const runtime = q('#stage5-runtime');
    const figma = q('#stage5-figma');
    const boundary = q('#stage5-boundary');
    const imagesLoaded = [...document.images].every((image) => image.complete && image.naturalWidth > 0 && image.naturalHeight > 0);
    record(assertionIds[0], html.dataset.stage === '5' && html.dataset.stageCompleteAuthority === 'false' && html.dataset.sitesProven === 'false' && html.dataset.evidenceRole === 'hash-bound-local-derivative' && html.dataset.previewAccess === 'private-owner-only' && html.dataset.runtimeActivation === 'false' && html.dataset.protectedInteriorsRendered === 'false' && html.dataset.backendIncluded === 'false' && html.dataset.realDataIncluded === 'false' && html.dataset.telemetryIncluded === 'false' && html.dataset.siwcBypassUsed === 'false' && html.dataset.environmentRevision === '0' && html.dataset.environmentEntryCount === '0', { stage: html.dataset.stage, stageCompleteAuthority: html.dataset.stageCompleteAuthority, sitesProven: html.dataset.sitesProven, previewAccess: html.dataset.previewAccess });
    record(assertionIds[1], html.dataset.implementationCommit === facts.git.commit && html.dataset.implementationTree === facts.git.tree && html.dataset.implementationParent === facts.git.parent, facts.git);
    record(assertionIds[2], facts.git.pathCount === 34 && html.dataset.pathSetSha256 === facts.git.pathSetSha256 && html.dataset.pathContentSha256 === facts.git.pathContentSha256 && number(binding?.dataset.gitPaths) === 34, facts.git);
    record(assertionIds[3], html.dataset.figmaRole === 'authored-runtime-delta-snapshot' && html.dataset.stage5FigmaFreezeCreated === 'true' && html.dataset.figmaFileKey === facts.figma.fileKey && html.dataset.figmaPageId === facts.figma.pageId && html.dataset.figmaRootId === facts.figma.rootId && html.dataset.figmaRuntimeDeltaId === facts.figma.deltaId && figma?.dataset.runtimeDeltaNodeId === facts.figma.deltaId, facts.figma);
    record(assertionIds[4], facts.figma.status === 'passed_runtime_delta_locally_hash_bound' && facts.figma.captureAggregate === html.dataset.figmaCaptureAggregateSha256 && number(figma?.dataset.authoritativeCaptureCount) === 2 && imagesLoaded, { ...facts.figma, imagesLoaded });
    record(assertionIds[5], facts.figma.auditPassed && number(figma?.dataset.sectionCount) === 7 && number(figma?.dataset.nodeCount) === 1213 && number(figma?.dataset.linkedInstanceCount) === 74 && number(figma?.dataset.unlinkedInstanceCount) === 0 && number(figma?.dataset.piiHitCount) === 0 && number(figma?.dataset.internalTokenHitCount) === 0, facts.figma);
    record(assertionIds[6], facts.browser.status === 'passed' && facts.browser.promotable && facts.browser.assertions === 23 && number(overview?.dataset.browserAssertions) === 23 && number(overview?.dataset.browserFailed) === 0 && number(runtime?.dataset.browserScreenshotCount) === 54 && runtime?.dataset.browserPromotable === 'true', facts.browser);
    record(assertionIds[7], facts.browser.sourceCount === 393 && facts.browser.sourceIdentical && number(runtime?.dataset.browserSourceCount) === 393 && number(html.dataset.browserSourceCount) === 393 && html.dataset.runtimeSourceAggregateSha256 === facts.browser.sourceAggregate && html.dataset.browserSourceBindingSha256 === facts.browser.sourceAggregate, facts.browser);
    record(assertionIds[8], facts.gates.frontendFiles === 154 && facts.gates.frontendSuites === 310 && facts.gates.frontendTests === 1663 && number(binding?.dataset.frontendFiles) === 154 && number(binding?.dataset.frontendSuites) === 310 && number(binding?.dataset.frontendTests) === 1663, facts.gates);
    record(assertionIds[9], facts.gates.backendModules === 4 && facts.gates.backendTests === 127 && facts.gates.backendWarnings === 76 && number(binding?.dataset.backendModules) === 4 && number(binding?.dataset.backendTests) === 127, facts.gates);
    record(assertionIds[10], facts.gates.eslintAdded === 0 && facts.gates.prettierAdded === 0 && facts.gates.protectedDrift === 0 && number(boundary?.dataset.eslintAdded) === 0 && number(boundary?.dataset.prettierAdded) === 0 && number(boundary?.dataset.protectedUnauthorizedDrift) === 0 && html.dataset.protectedDiffSha256 === facts.gates.protectedDiffSha256, facts.gates);
    record(assertionIds[11], [overview, binding, contract, runtime, figma, boundary].every(Boolean) && document.images.length === 13 && document.querySelectorAll('a[data-evidence-asset]').length === 2 && number(contract?.dataset.routeCount) === 4 && number(contract?.dataset.semanticActionCount) === 3, { sections: 6, images: document.images.length, attachments: document.querySelectorAll('a[data-evidence-asset]').length });
    return {
      assertions,
      passed: assertions.filter(({ passed }) => passed).length,
      failed: assertions.filter(({ passed }) => !passed).length,
      measurements: {
        loadedImageCount: document.images.length,
        evidenceAttachmentCount: document.querySelectorAll('a[data-evidence-asset]').length,
        captureSectionCount: [overview, binding, contract, runtime, figma, boundary].filter(Boolean).length,
      },
    };
  }, { assertionIds: ASSERTION_IDS, facts });
}

function validateOutputDirectory(directory) {
  const names = fs.readdirSync(directory).sort();
  assert(JSON.stringify(names) === JSON.stringify(OUTPUT_FILES), `Local output set mismatch: ${names.join(', ')}`);
  const metrics = readJson(path.join(directory, METRICS_FILE));
  assert(metrics.status === 'passed' && metrics.localEvidenceStatus === 'frozen', 'Local metrics status mismatch');
  assert(metrics.assertionSummary.total === 12 && metrics.assertionSummary.passed === 12 && metrics.assertionSummary.failed === 0, 'Local assertion summary mismatch');
  assert(metrics.captures.length === CAPTURE_SPECS.length, 'Local capture count mismatch');
  for (const capture of metrics.captures) {
    const checked = validatePng(path.join(directory, capture.file), capture);
    assert(JSON.stringify(checked.visualStats) === JSON.stringify(capture.visualStats), `Local capture visual stats mismatch: ${capture.file}`);
  }
  return metrics;
}

async function main() {
  const startedAt = LOCAL_STARTED_AT;
  const inputs = validateManifestInputs();
  const figmaManifest = readJson(path.join(CONTRACT_DIR, 'FIGMA_SNAPSHOT_MANIFEST.json'));
  const figmaAudit = readJson(path.join(CONTRACT_DIR, 'assets/figma/stage5-figma-direct-audit.json'));
  const browserManifest = readJson(path.join(CONTRACT_DIR, 'assets/browser-evidence/stage5-final-evidence-manifest.json'));
  const gateSummary = readJson(path.join(CONTRACT_DIR, 'assets/gates/stage5-final-gates-summary.json'));
  const runId = LOCAL_RUN_ID;
  const stagingDir = path.join(ASSETS_DIR, `.local-evidence-staging-${runId}-${process.pid}`);
  const backupDir = path.join(ASSETS_DIR, `.local-evidence-backup-${process.pid}`);
  assert(!fs.existsSync(stagingDir) && !fs.existsSync(backupDir), 'Local evidence staging residue exists');
  fs.mkdirSync(stagingDir);
  const playwright = resolvePlaywright();
  const fontBundle = resolveFonts();
  let browser;
  try {
    const consoleErrors = [];
    const pageErrors = [];
    const failedRequests = [];
    const blockedNetworkRequests = [];
    browser = await playwright.module.chromium.launch({ headless: true });
    const browserVersion = browser.version();
    const browserContext = await browser.newContext({ viewport: { width: 1500, height: 1200 }, deviceScaleFactor: 1, locale: 'fa-IR', colorScheme: 'dark', reducedMotion: 'reduce' });
    const page = await browserContext.newPage();
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
      if (![...document.images].every((image) => image.complete && image.naturalWidth > 0 && image.naturalHeight > 0)) throw new Error('One or more evidence images failed to load');
    });
    const fontChecks = await page.evaluate(() => [400, 500, 600, 700].map((weight) => ({ weight, loaded: document.fonts.check(`${weight} 16px Vazirmatn`, 'آزمون') })));
    assert(fontChecks.every(({ loaded }) => loaded), 'Vazirmatn font check failed');
    const facts = {
      git: { commit: IMPLEMENTATION_COMMIT, tree: IMPLEMENTATION_TREE, parent: IMPLEMENTATION_PARENT, pathCount: 34, pathSetSha256: PATHSET_SHA256, pathContentSha256: PATH_CONTENT_SHA256 },
      figma: { status: figmaManifest.status, fileKey: FIGMA_FILE_KEY, pageId: FIGMA_PAGE_ID, rootId: FIGMA_ROOT_ID, deltaId: FIGMA_DELTA_ID, captureAggregate: FIGMA_CAPTURE_AGGREGATE, auditPassed: figmaAudit.completionGatePassed === true && figmaAudit.errors.length === 0 },
      browser: { status: browserManifest.status, promotable: browserManifest.promotable, assertions: browserManifest.acceptance.assertions.passed, screenshots: browserManifest.acceptance.screenshots.count, sourceCount: browserManifest.sourceBinding.fileCount, sourceIdentical: browserManifest.sourceBinding.identical, sourceAggregate: browserManifest.sourceBinding.postSha256 },
      gates: { frontendFiles: gateSummary.frontend.files, frontendSuites: gateSummary.frontend.suites, frontendTests: gateSummary.frontend.tests, backendModules: gateSummary.backend.modules, backendTests: gateSummary.backend.tests, backendWarnings: gateSummary.backend.warnings, eslintAdded: gateSummary.quality.eslint.added, prettierAdded: gateSummary.quality.prettier.added, protectedDrift: gateSummary.guard.protectedUnauthorizedDrift, protectedDiffSha256: PROTECTED_DIFF_SHA256 },
    };
    const preAudit = await auditPage(page, facts);
    assert(preAudit.passed === 12 && preAudit.failed === 0, `Pre-capture audit failed: ${JSON.stringify(preAudit.assertions.filter(({ passed }) => !passed), null, 2)}`);
    const preDom = await canonicalDomSnapshot(page);
    const preDomSha256 = sha256Buffer(Buffer.from(preDom));
    const preAuditSha256 = hashValue(preAudit);
    const captures = [];
    for (const spec of CAPTURE_SPECS) {
      const locator = page.locator(spec.selector);
      assert(await locator.count() === 1, `Capture selector mismatch: ${spec.selector}`);
      const box = await locator.boundingBox();
      assert(box && box.width > 0 && box.height > 0, `Capture geometry invalid: ${spec.selector}`);
      const outputPath = path.join(stagingDir, spec.file);
      await locator.screenshot({ path: outputPath, animations: 'disabled', caret: 'hide' });
      const dimensions = pngDimensions(outputPath);
      const expectedWidth = Math.ceil(box.width);
      const expectedHeight = Math.ceil(box.height);
      assert(Math.abs(dimensions.width - expectedWidth) <= 1 && Math.abs(dimensions.height - expectedHeight) <= 1, `Capture dimensions drift: ${spec.file}; expected about ${expectedWidth}x${expectedHeight}, got ${dimensions.width}x${dimensions.height}`);
      const checked = validatePng(outputPath, { path: spec.file, width: dimensions.width, height: dimensions.height });
      captures.push({ file: spec.file, selector: spec.selector, width: checked.width, height: checked.height, bytes: checked.bytes, sha256: checked.sha256, visualStats: checked.visualStats });
    }
    const postAudit = await auditPage(page, facts);
    const postDom = await canonicalDomSnapshot(page);
    const postDomSha256 = sha256Buffer(Buffer.from(postDom));
    const postAuditSha256 = hashValue(postAudit);
    assert(postAudit.passed === 12 && postAudit.failed === 0, 'Post-capture audit failed');
    assert(preAuditSha256 === postAuditSha256 && stableJson(preAudit) === stableJson(postAudit), 'Pre/post audit changed');
    assert(preDomSha256 === postDomSha256 && preDom === postDom, 'Pre/post DOM changed');
    assert(consoleErrors.length === 0 && pageErrors.length === 0 && failedRequests.length === 0 && blockedNetworkRequests.length === 0, `Local browser diagnostics: ${JSON.stringify({ consoleErrors, pageErrors, failedRequests, blockedNetworkRequests })}`);
    for (const capture of captures) validatePng(path.join(stagingDir, capture.file), capture);
    const completedAt = LOCAL_COMPLETED_AT;
    assert(new Date(completedAt).getTime() <= Date.now(), 'Bound local completion timestamp is still in the future');
    const validatedAt = new Date().toISOString();
    const record = (relativePath) => fileRecord(path.join(CONTRACT_DIR, relativePath), relativePath);
    const metrics = {
      schemaVersion: 1,
      stage: 5,
      scope: 'customer-accountant-workspaces',
      status: 'passed',
      localEvidenceStatus: 'frozen',
      runId,
      startedAt,
      completedAt,
      validatedAt,
      recordedDate: '2026-08-11',
      comparisonBaseCommit: IMPLEMENTATION_PARENT,
      claimBoundary: { localDerivative: true, stageCompleteAuthority: false, sitesProven: false, newFigmaFreezeCreated: true, figmaAuthoredSnapshot: true, runtimeEvidenceHashBound: true },
      inputs: {
        evidenceManifest: { ...inputs.record, inputCount: inputs.manifest.inputCount, inputBytes: inputs.manifest.inputBytes, inputProjectionAggregateSha256: inputs.manifest.inputProjectionAggregateSha256 },
        implementation: { commit: IMPLEMENTATION_COMMIT, tree: IMPLEMENTATION_TREE, parent: IMPLEMENTATION_PARENT, pathCount: 34, pathSetSha256: PATHSET_SHA256, pathContentSha256: PATH_CONTENT_SHA256 },
        html: record(HTML_FILE),
        script: record('capture-evidence.cjs'),
        figma: { manifest: record('FIGMA_SNAPSHOT_MANIFEST.json'), audit: record('assets/figma/stage5-figma-direct-audit.json'), authoritativeCaptureCount: 2, authoritativeCaptureBytes: 477114, captureAggregateSha256: FIGMA_CAPTURE_AGGREGATE, inputCount: 9, inputBytes: 1113189, inputAggregateSha256: '567c1e09274f20a0bfdda86a7522caa0cf4c75b9284f6504d85bd7d747193678' },
        browser: { metrics: record('assets/browser-evidence/runs/uiux-stage5-browser-20260811T100859948Z/stage5-browser-acceptance-metrics.json'), topManifest: record('assets/browser-evidence/stage5-final-evidence-manifest.json'), binding: record('assets/browser-evidence/runs/uiux-stage5-browser-20260811T100859948Z/stage5-final-source-binding.json'), harness: record('assets/browser-evidence/stage5-browser-acceptance-harness.mjs'), runId: BROWSER_RUN_ID, assertions: 23, screenshots: 54, sourceCount: 393, sourceBindingSha256: RUNTIME_SOURCE_AGGREGATE, captureAggregateSha256: BROWSER_CAPTURE_AGGREGATE },
        runtimeSourceBinding: record('assets/gates/stage5-implementation-git-binding.json'),
        gates: { summary: record('assets/gates/stage5-final-gates-summary.json'), manifest: record('assets/gates/stage5-final-gate-manifest.md'), fileCount: 14, fullAggregateSha256: inputs.manifest.technicalGates.fullAggregateSha256 },
      },
      dependencies: { node: process.version, platform: `${process.platform}-${process.arch}`, playwright: playwright.source, browser: { engine: 'chromium', version: browserVersion, viewport: { width: 1500, height: 1200 }, deviceScaleFactor: 1 }, fonts: { family: 'Vazirmatn', root: fontBundle.root, checks: fontChecks, files: fontBundle.fonts.map(({ file, weight, bytes, sha256 }) => ({ file, weight, bytes, sha256 })) } },
      integrity: { preDomSha256, postDomSha256, domEqual: true, preAuditSha256, postAuditSha256, auditEqual: true, postCaptureRemeasurement: true, consoleErrors, pageErrors, failedRequests, blockedNetworkRequests },
      assertions: postAudit.assertions,
      assertionSummary: { total: 12, passed: 12, failed: 0, exactOrder: true },
      measurements: postAudit.measurements,
      captures,
      outputSet: { policy: 'exact', pngCount: captures.length, metricsCount: 1, files: OUTPUT_FILES },
      publication: { strategy: 'atomic-directory-rename', partialPromotionAllowed: false, validationBeforePromotion: true, validationAfterPromotion: true, residueAllowed: false, binderValidationStatus: 'pending_disposable_validation', sitesInputReady: false },
    };
    fs.writeFileSync(path.join(stagingDir, METRICS_FILE), `${JSON.stringify(metrics, null, 2)}\n`, { flag: 'wx' });
    validateOutputDirectory(stagingDir);
    await browserContext.close();
    await browser.close();
    browser = null;
    let hadPrior = false;
    if (fs.existsSync(PUBLISHED_DIR)) {
      hadPrior = true;
      fs.renameSync(PUBLISHED_DIR, backupDir);
    }
    try {
      fs.renameSync(stagingDir, PUBLISHED_DIR);
      validateOutputDirectory(PUBLISHED_DIR);
      if (hadPrior) fs.rmSync(backupDir, { recursive: true, force: true });
    } catch (error) {
      if (fs.existsSync(PUBLISHED_DIR)) fs.rmSync(PUBLISHED_DIR, { recursive: true, force: true });
      if (hadPrior && fs.existsSync(backupDir)) fs.renameSync(backupDir, PUBLISHED_DIR);
      throw error;
    }
    const outputRecords = OUTPUT_FILES.map((name) => fileRecord(path.join(PUBLISHED_DIR, name), `assets/local-evidence/${name}`));
    process.stdout.write(`${JSON.stringify({ status: 'FROZEN', runId, startedAt, completedAt, assertions: '12/12', domSha256: postDomSha256, auditSha256: postAuditSha256, outputRecords }, null, 2)}\n`);
  } catch (error) {
    if (browser) await browser.close().catch(() => {});
    if (fs.existsSync(stagingDir)) fs.rmSync(stagingDir, { recursive: true, force: true });
    if (fs.existsSync(backupDir) && !fs.existsSync(PUBLISHED_DIR)) fs.renameSync(backupDir, PUBLISHED_DIR);
    throw error;
  }
}

main().catch((error) => {
  process.stderr.write(`Stage 5 evidence capture failed: ${error.stack || error.message}\n`);
  process.exitCode = 1;
});
