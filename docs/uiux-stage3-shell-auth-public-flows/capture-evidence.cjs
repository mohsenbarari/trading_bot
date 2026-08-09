#!/usr/bin/env node
'use strict';

/**
 * Stage 3 shell/auth/public-flow local evidence harness.
 *
 * Adapted from the Stage 2 evidence architecture: immutable inputs are
 * hash-bound before Chromium starts, HTTP(S) is blocked, the DOM and audit are
 * compared before/after capture, and only an exact validated output directory
 * is atomically promoted. `--freeze-runtime-binding` is a one-shot deterministic
 * generator for the complete non-doc Stage 3 worktree source binding.
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
const HTML_FILE = 'stage3-shell-auth-public-flows-evidence.html';
const HTML_PATH = path.join(CONTRACT_DIR, HTML_FILE);
const FIGMA_MANIFEST_PATH = path.join(CONTRACT_DIR, 'FIGMA_SNAPSHOT_MANIFEST.json');
const EVIDENCE_MANIFEST_PATH = path.join(CONTRACT_DIR, 'EVIDENCE_MANIFEST.json');
const TECHNICAL_GATES_PATH = path.join(GATES_DIR, 'stage3-technical-gates.json');
const SOURCE_BINDING_PATH = path.join(GATES_DIR, 'stage3-runtime-source-binding.json');
const METRICS_FILE = 'local-stage3-shell-auth-public-flows-validation-metrics.json';
const COMPARISON_BASE = '3822df67a48e7ee3197bc6d67c79aa7ee84a7905';
const SOURCE_BINDING_AGGREGATE_SHA256 = '6c9a3f0bcd96635a1fb40fda00278401c4f54da87d7535132cd342945cf5f87f';
const FIGMA_AGGREGATE_SHA256 = '98fed43b41661d2f63c605bfa8a68c42fb35ba514633cba8da84ce5ae31ccdc7';
const BROWSER_NINE_FILE_REPORTED_AGGREGATE_SHA256 = '769d588660fead77e1bdb00f0dd9cf871c8ef1a0ea48f7e03e25b4985c8b56c9';
const BROWSER_NINE_FILE_MANIFEST_AGGREGATE_SHA256 = 'e59a649abc54732269e61689c9a1c9777cff2b742a92fc1d910417a2be42ef90';
const BROWSER_METRICS_SHA256 = 'e93d7ffa69d7dbbacbf6749f3a49030da9895b1e987925d96f23083dbaf3f52c';
const BROWSER_HARNESS_SHA256 = '6c47b4f691219847390d8d91c8d018a96e4bb7d882f033e4e86c2f17556749d9';
const RUNTIME_FINAL_SHA256 = '73de6208d8dc9ad8b3c67c3cf81548946898676ff8719b5ffca4faff52fa18b9';
const ESLINT_FINAL_SHA256 = '8bb5b1f13d315d44831675d4edda16f56e3c4ce7244d3b3b528303591f24a7a2';
const ESLINT_DELTA_SHA256 = 'd905b42b822da31f7ee7556ceae7f583de378cbc601175e037afcd8c1e0965c1';
const PRETTIER_DELTA_SHA256 = 'f104fbf9a5a4bb6fe182e4e24c1e4a879161bba4716dda22c9209ed12484a60a';
const EMPTY_SHA256 = 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855';
const PROTECTED_REGION_SHA256 = 'f25c01dac38db208517047ffc0f2458e2c89868e988a6d7f68749221db106860';

const SOURCE_BINDING_SPEC = Object.freeze([
  ['modified', 'api/routers/accountants.py'],
  ['modified', 'api/routers/auth.py'],
  ['modified', 'api/routers/customers.py'],
  ['modified', 'api/routers/invitations.py'],
  ['modified', 'bot/handlers/panel.py'],
  ['modified', 'bot/handlers/start.py'],
  ['modified', 'core/db.py'],
  ['modified', 'core/invitation_contract_service.py'],
  ['modified', 'core/log_redaction.py'],
  ['modified', 'core/logging_config.py'],
  ['modified', 'core/metrics.py'],
  ['modified', 'core/registration_contracts.py'],
  ['modified', 'deploy/production/nginx-iran-online-https.conf.template'],
  ['modified', 'deploy/production/nginx-iran-online.conf.template'],
  ['modified', 'deploy/staging/nginx-staging.conf.template'],
  ['modified', 'frontend/e2e/mandatory-channel.spec.ts'],
  ['modified', 'frontend/index.html'],
  ['modified', 'frontend/package.json'],
  ['added', 'frontend/public/uiux-v2-brand-mark.svg'],
  ['modified', 'frontend/scripts/check-design-system-v2-guards.mjs'],
  ['added', 'frontend/scripts/check-stage3-protected-regions.mjs'],
  ['modified', 'frontend/scripts/design-system-v2-guard.test.mjs'],
  ['modified', 'frontend/scripts/lib/design-system-v2-guard.mjs'],
  ['added', 'frontend/scripts/lib/stage3-protected-region-guard.mjs'],
  ['added', 'frontend/scripts/stage3-protected-region-guard.test.mjs'],
  ['modified', 'frontend/src/App.test.ts'],
  ['modified', 'frontend/src/App.vue'],
  ['added', 'frontend/src/bootRecoveryHtml.test.ts'],
  ['modified', 'frontend/src/components/AppAuthenticatedShell.test.ts'],
  ['modified', 'frontend/src/components/AppAuthenticatedShell.vue'],
  ['modified', 'frontend/src/components/AppToasts.test.ts'],
  ['modified', 'frontend/src/components/AppToasts.vue'],
  ['added', 'frontend/src/components/auth/AuthFlowShell.test.ts'],
  ['added', 'frontend/src/components/auth/AuthFlowShell.vue'],
  ['modified', 'frontend/src/components/BottomNav.test.ts'],
  ['modified', 'frontend/src/components/BottomNav.vue'],
  ['modified', 'frontend/src/components/CreateInvitationView.test.ts'],
  ['modified', 'frontend/src/components/OwnerAccountantManagerModal.test.ts'],
  ['modified', 'frontend/src/components/OwnerCustomerManagerModal.test.ts'],
  ['modified', 'frontend/src/components/PWAInstallOverlay.test.ts'],
  ['modified', 'frontend/src/components/PWAInstallOverlay.vue'],
  ['added', 'frontend/src/components/SessionApprovalModal.test.ts'],
  ['modified', 'frontend/src/components/SessionApprovalModal.vue'],
  ['modified', 'frontend/src/components/ui/AppInput.vue'],
  ['modified', 'frontend/src/components/ui/index.ts'],
  ['modified', 'frontend/src/composables/useOwnerAccountants.ts'],
  ['modified', 'frontend/src/composables/useOwnerCustomers.ts'],
  ['modified', 'frontend/src/composables/useOwnerRelations.test.ts'],
  ['modified', 'frontend/src/design-system-v2/scope-manifest.json'],
  ['modified', 'frontend/src/env.d.ts'],
  ['modified', 'frontend/src/main.test.ts'],
  ['modified', 'frontend/src/main.ts'],
  ['added', 'frontend/src/router/chunkRecovery.test.ts'],
  ['added', 'frontend/src/router/chunkRecovery.ts'],
  ['modified', 'frontend/src/router/index.test.ts'],
  ['modified', 'frontend/src/router/index.ts'],
  ['added', 'frontend/src/router/systemRecovery.test.ts'],
  ['added', 'frontend/src/router/systemRecovery.ts'],
  ['modified', 'frontend/src/router/uiRouteContract.test.ts'],
  ['modified', 'frontend/src/router/uiRouteContract.ts'],
  ['modified', 'frontend/src/styles/design-system-v2.components.css'],
  ['modified', 'frontend/src/styles/designSystemV2.test.ts'],
  ['modified', 'frontend/src/utils/auth.test.ts'],
  ['modified', 'frontend/src/utils/auth.ts'],
  ['added', 'frontend/src/utils/authNavigation.test.ts'],
  ['added', 'frontend/src/utils/authNavigation.ts'],
  ['modified', 'frontend/src/utils/invitationContract.test.ts'],
  ['modified', 'frontend/src/utils/invitationContract.ts'],
  ['added', 'frontend/src/utils/navigationResult.test.ts'],
  ['added', 'frontend/src/utils/navigationResult.ts'],
  ['modified', 'frontend/src/utils/pwaInstall.test.ts'],
  ['modified', 'frontend/src/utils/pwaInstall.ts'],
  ['added', 'frontend/src/utils/registrationHandoff.test.ts'],
  ['added', 'frontend/src/utils/registrationHandoff.ts'],
  ['added', 'frontend/src/utils/securityLayerState.test.ts'],
  ['added', 'frontend/src/utils/securityLayerState.ts'],
  ['modified', 'frontend/src/views/AccountantWorkspaceView.test.ts'],
  ['modified', 'frontend/src/views/AccountantWorkspaceView.vue'],
  ['modified', 'frontend/src/views/CustomerWorkspaceView.test.ts'],
  ['modified', 'frontend/src/views/CustomerWorkspaceView.vue'],
  ['modified', 'frontend/src/views/DashboardView.test.ts'],
  ['modified', 'frontend/src/views/DashboardView.vue'],
  ['modified', 'frontend/src/views/InviteLanding.test.ts'],
  ['modified', 'frontend/src/views/InviteLanding.vue'],
  ['modified', 'frontend/src/views/LoginView.test.ts'],
  ['modified', 'frontend/src/views/LoginView.vue'],
  ['modified', 'frontend/src/views/SetupPassword.test.ts'],
  ['modified', 'frontend/src/views/SetupPassword.vue'],
  ['added', 'frontend/src/views/SystemRecoveryView.test.ts'],
  ['added', 'frontend/src/views/SystemRecoveryView.vue'],
  ['modified', 'frontend/src/views/WebRegister.test.ts'],
  ['modified', 'frontend/src/views/WebRegister.vue'],
  ['modified', 'main.py'],
  ['modified', 'nginx.conf'],
  ['modified', 'schemas.py'],
  ['modified', 'scripts/setup_foreign_nginx.sh'],
  ['modified', 'scripts/setup_iran_nginx.sh'],
  ['modified', 'tests/customer_live_auth_smoke.py'],
  ['modified', 'tests/test_accountants_router.py'],
  ['modified', 'tests/test_auth_router_login_otp_flows.py'],
  ['modified', 'tests/test_auth_router_registration_flows.py'],
  ['modified', 'tests/test_authoritative_registration_postgres.py'],
  ['modified', 'tests/test_bot_admin_role.py'],
  ['modified', 'tests/test_bot_panel_standard_actions.py'],
  ['modified', 'tests/test_bot_start_invitation_entry.py'],
  ['modified', 'tests/test_bot_start_profile_token_success.py'],
  ['modified', 'tests/test_bot_start_registration_address.py'],
  ['modified', 'tests/test_bot_start_registration_contact.py'],
  ['modified', 'tests/test_customers_router.py'],
  ['modified', 'tests/test_deploy_surface_smoke.py'],
  ['modified', 'tests/test_error_tracking.py'],
  ['modified', 'tests/test_invitation_public_access.py'],
  ['modified', 'tests/test_invitations_router.py'],
  ['modified', 'tests/test_logging_foundation.py'],
  ['modified', 'tests/test_main_frontend_serving.py'],
  ['modified', 'tests/test_metrics.py'],
  ['modified', 'tests/test_registration_stage1_contracts.py'],
  ['modified', 'tests/test_render_release_artifacts.py'],
  ['modified', 'tests/test_request_logging.py'],
  ['modified', 'tests/test_stage5_direct_telegram_registration.py'],
]);

const FIGMA_RECORDS = Object.freeze([
  { nodeId: '168:2017', path: 'assets/figma/figma-stage3-auth-168-2017.png', width: 390, height: 844, bytes: 20697, sha256: '61c13fdeb43a342e387046d22abdc8102205e2c5ed0d0c05825ee9fffd019bd4' },
  { nodeId: '168:2018', path: 'assets/figma/figma-stage3-home-168-2018.png', width: 390, height: 844, bytes: 12964, sha256: '8ffdf6c6957940e6f5dea8123439f6bf68f2b996e9762d2cbc8d80d8044720ac' },
  { nodeId: '168:1979', path: 'assets/figma/figma-stage3-shell-168-1979.png', width: 1720, height: 706, bytes: 107241, sha256: '4a8ee0bd7387ad3190c6774d006a5ee1c5524d509b8c3fbc2f3de285c0de8eb9' },
  { nodeId: '168:1980', path: 'assets/figma/figma-stage3-state-168-1980.png', width: 1720, height: 448, bytes: 56029, sha256: 'c6577ab7c4af8b79eaabd2be0f960b2ab1c3f864b4704f23b7408fa515728d7f' },
]);

const BROWSER_RECORDS = Object.freeze([
  { path: 'home-pwa-positive-mobile-390.png', width: 390, height: 844, bytes: 101042, sha256: 'e21907ae908dd6e5ac8cc1bcb6fd141325996c378be6438456be30de5c21b315' },
  { path: 'home-standard-desktop-1440.png', width: 1440, height: 900, bytes: 288820, sha256: '13a17448149d425cb31a6d7775d3d0f91d1457129da1b2097b37681dfcf6482b' },
  { path: 'layer-coexistence-session-connection-toast-nav-mobile-390.png', width: 390, height: 844, bytes: 63968, sha256: 'b17786f94187000f5f880478c73f6337cea4a9540dfccbe9b4e296a7faefda59' },
  { path: 'legacy-session-modal-protected-market-mobile-390.png', width: 390, height: 844, bytes: 52372, sha256: '08e9c3b0324af8179e099f97930854dfd118b6043b86a2f9a019d4814bd49f49' },
  { path: 'login-public-mobile-390.png', width: 390, height: 844, bytes: 50548, sha256: '0194ae5b077e3dca06ad8ff0354a2c063454ad8de8410d2fc15c51ac82d78077' },
  { path: 'registration-resumed-step-3-mobile-390.png', width: 390, height: 844, bytes: 47631, sha256: '519f4d728e12bfb1e994e88c4a5ca151c050d48bdb625cd13da1e9e21521e974' },
  { path: 'registration-step-4-retained-marker-mobile-390.png', width: 390, height: 844, bytes: 52531, sha256: '7c17b4e465cc3ccb207ad8dc6c8503ec38d2e5a12ef2aaeb33fd111eaf553dca' },
  { path: 'setup-password-focused-mobile-390.png', width: 390, height: 844, bytes: 55145, sha256: '497d9af385649ccd404f406a80b886b650dd00c712c04688f1a0d8c685283d48' },
  { path: 'stage3-browser-acceptance-metrics.json', bytes: 105542, sha256: BROWSER_METRICS_SHA256 },
]);

const EXPECTED_BROWSER_ASSERTIONS = Object.freeze([
  'responsive-shell-matrix-8-widths',
  'public-focused-system-shell-isolation',
  'home-hidden-pwa-wrapper-no-layout-item',
  'focus-indicator-exact-3px',
  'cta-48-nav-target-44-label-11',
  'mobile-keyboard-resize-proxy',
  'pwa-positive-negative-runtime',
  'layer-computed-z-order',
  'session-modal-focus-trap-escape-restore',
  'session-modal-v2-color-contrast',
  'reduced-motion-runtime-max-1ms',
  'legacy-modal-white-gradient-false-branch',
  'registration-browser-url-fragment-query-scrub',
  'invite-availability-flags-without-link-fields',
  'secure-invitation-registration-refresh-back-forward',
  'registration-secret-browser-leak-scan-zero',
  'registration-step4-refresh-retained-marker-clear-retry',
  'registration-authenticated-context-410-home',
  'registration-no-auth-completion-marker-login-clear',
  'login-registration-required-cookie-direct-resume',
  'request-lifecycle-artifacts-exactly-classified',
  'browser-unexpected-diagnostics-zero',
  'source-hash-mtime-pre-post-identical',
]);

const EXPECTED_LOCAL_ASSERTIONS = Object.freeze([
  'claim-boundary-local-not-stage-or-sites',
  'figma-read-only-reference-no-new-freeze',
  'figma-four-direct-exports-hash-bound',
  'route-registry-thirty-exact',
  'scope-counts-five-twentyone-four',
  'shell-counts-three-one-twentyone-four-one',
  'public-focused-routes-exact',
  'system-recovery-outcomes-exact',
  'canonical-short-link-two-raw-exceptions',
  'opaque-context-ttl-browser-leaks-zero',
  'browser-run-metrics-23-of-23',
  'browser-eight-screenshots-loaded-hash-bound',
  'responsive-eight-widths-source-stable',
  'browser-accessibility-layer-security-zero-unexpected',
  'vitest-58-118-664-snapshots-zero',
  'eslint-delta-zero-inherited-disclosed',
  'prettier-delta-zero-inherited-disclosed',
  'guard-type-build-passed',
  'backend-compose-caveat-exact',
  'protected-empty-diff-region-exact',
  'runtime-source-binding-120-exact',
]);

const CAPTURE_SPECS = Object.freeze([
  { selector: '#stage3-overview', file: 'local-stage3-overview.png' },
  { selector: '#stage3-figma-reference', file: 'local-stage3-figma-reference.png' },
  { selector: '#stage3-shell-matrix', file: 'local-stage3-shell-matrix.png' },
  { selector: '#stage3-secure-registration', file: 'local-stage3-secure-registration.png' },
  { selector: '#stage3-browser-proof', file: 'local-stage3-browser-proof.png' },
  { selector: '#stage3-gates-protected', file: 'local-stage3-gates-protected.png' },
]);

const EXACT_OUTPUT_FILES = Object.freeze([...CAPTURE_SPECS.map((item) => item.file), METRICS_FILE].sort());
const EXACT_FIGMA_FILES = Object.freeze(FIGMA_RECORDS.map((item) => path.basename(item.path)).sort());
const EXACT_BROWSER_FILES = Object.freeze([...BROWSER_RECORDS.map((item) => item.path), 'stage3-browser-acceptance-harness.mjs'].sort());
const EXACT_GATE_FILES = Object.freeze([
  'stage3-eslint-delta.json',
  'stage3-eslint-final.json',
  'stage3-prettier-delta.json',
  'stage3-runtime-final.json',
  'stage3-runtime-source-binding.json',
  'stage3-technical-gates.json',
].sort());

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

function exact(actual, expected) {
  return JSON.stringify(actual) === JSON.stringify(expected);
}

function sha256Buffer(buffer) {
  return crypto.createHash('sha256').update(buffer).digest('hex');
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

function fileRecord(filePath, relativePath = path.relative(CONTRACT_DIR, filePath)) {
  const buffer = fs.readFileSync(filePath);
  return { path: relativePath.split(path.sep).join('/'), bytes: buffer.length, sha256: sha256Buffer(buffer) };
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
      const color = bytesPerPixel === 3 ? `${row[x]},${row[x + 1]},${row[x + 2]}` : `${row[x]},${row[x + 1]},${row[x + 2]},${row[x + 3]}`;
      if (firstColor === null) firstColor = color;
      if (color !== firstColor) changedPixelCount += 1;
      if (colors.size <= 512) colors.add(color);
    }
    previous = row;
  }
  return { uniqueColorCount: colors.size, changedPixelCount };
}

function validatePng(filePath, expected) {
  assert(fs.existsSync(filePath) && fs.statSync(filePath).isFile(), `PNG missing: ${filePath}`);
  const stat = fs.statSync(filePath);
  const dimensions = pngDimensions(filePath);
  const sha256 = sha256File(filePath);
  const visualStats = pngVisualStats(filePath);
  assert(stat.size === expected.bytes && sha256 === expected.sha256, `PNG byte/hash drift: ${filePath}`);
  assert(dimensions.width === expected.width && dimensions.height === expected.height, `PNG dimensions drift: ${filePath}`);
  assert(visualStats.uniqueColorCount >= 8 && visualStats.changedPixelCount >= 100, `PNG blank or degenerate: ${filePath}`);
  return { ...expected, visualStats, validated: true };
}

function buildRuntimeBinding() {
  assert(SOURCE_BINDING_SPEC.length === 120, `Source binding spec count drift: ${SOURCE_BINDING_SPEC.length}`);
  const sources = SOURCE_BINDING_SPEC.map(([status, relativePath]) => {
    assert(status === 'modified' || status === 'added', `Invalid source status: ${status}`);
    assert(!relativePath.startsWith('docs/'), `Docs source cannot enter runtime binding: ${relativePath}`);
    const absolutePath = path.join(REPO_ROOT, relativePath);
    assert(fs.existsSync(absolutePath) && fs.statSync(absolutePath).isFile(), `Runtime source missing: ${relativePath}`);
    const buffer = fs.readFileSync(absolutePath);
    return { status, path: relativePath, bytes: buffer.length, sha256: sha256Buffer(buffer) };
  }).sort((a, b) => a.path.localeCompare(b.path));
  const aggregateSha256 = sha256Buffer(Buffer.from(JSON.stringify(sources)));
  assert(aggregateSha256 === SOURCE_BINDING_AGGREGATE_SHA256, `Runtime source aggregate drift: ${aggregateSha256}`);
  return {
    schemaVersion: 1,
    stage: '3',
    comparisonBaseCommit: COMPARISON_BASE,
    status: 'source-bound',
    scope: 'all non-doc files modified or added relative to comparison base, including untracked non-ignored runtime/test/deploy sources',
    algorithm: 'sha256(JSON.stringify(sources)); sources sorted by path; key order status,path,bytes,sha256',
    sourceCount: sources.length,
    modifiedCount: sources.filter((item) => item.status === 'modified').length,
    addedCount: sources.filter((item) => item.status === 'added').length,
    deletedCount: 0,
    aggregateSha256,
    sources,
  };
}

function freezeRuntimeBinding() {
  assert(!fs.existsSync(SOURCE_BINDING_PATH), `Runtime source binding already exists: ${SOURCE_BINDING_PATH}`);
  const binding = buildRuntimeBinding();
  fs.writeFileSync(SOURCE_BINDING_PATH, `${JSON.stringify(binding, null, 2)}\n`, { encoding: 'utf8', flag: 'wx' });
  const record = fileRecord(SOURCE_BINDING_PATH);
  process.stdout.write(`${JSON.stringify({ status: 'frozen', ...record, sourceCount: binding.sourceCount, aggregateSha256: binding.aggregateSha256 }, null, 2)}\n`);
}

if (process.argv.includes('--freeze-runtime-binding')) {
  freezeRuntimeBinding();
  process.exit(0);
}

function readJson(filePath) {
  return JSON.parse(fs.readFileSync(filePath, 'utf8'));
}

function validateRuntimeBinding() {
  assert(fs.existsSync(SOURCE_BINDING_PATH), `Runtime source binding missing: ${SOURCE_BINDING_PATH}`);
  const frozen = readJson(SOURCE_BINDING_PATH);
  const current = buildRuntimeBinding();
  assert(frozen.schemaVersion === 1 && frozen.stage === '3' && frozen.comparisonBaseCommit === COMPARISON_BASE, 'Runtime source binding header mismatch');
  assert(frozen.sourceCount === 120 && frozen.modifiedCount === 98 && frozen.addedCount === 22 && frozen.deletedCount === 0, 'Runtime source binding cardinality mismatch');
  assert(frozen.aggregateSha256 === SOURCE_BINDING_AGGREGATE_SHA256 && exact(frozen.sources, current.sources), 'Runtime source binding source/aggregate mismatch');
  return { ...fileRecord(SOURCE_BINDING_PATH), sourceCount: frozen.sourceCount, modifiedCount: frozen.modifiedCount, addedCount: frozen.addedCount, aggregateSha256: frozen.aggregateSha256, sources: frozen.sources };
}

function validateFigmaInputs() {
  assert(exact(sortedEntries(FIGMA_DIR), EXACT_FIGMA_FILES), `Figma file set mismatch: ${sortedEntries(FIGMA_DIR).join(', ')}`);
  const manifest = readJson(FIGMA_MANIFEST_PATH);
  assert(manifest.schemaVersion === 1 && manifest.stage === '3' && manifest.status === 'read_only_reference_hash_bound', 'Figma manifest header mismatch');
  assert(manifest.fileKey === 'z8jgJxST4O2APzWnlyP9gv' && manifest.pageId === '168:1974', 'Figma identity mismatch');
  assert(manifest.stage3FreezeCreated === false && manifest.figmaMutationPerformed === false && manifest.directRead?.detachedInstanceCount === 0, 'Figma read-only/no-detached boundary mismatch');
  const projection = manifest.captures.map(({ nodeId, path: filePath, width, height, bytes, sha256 }) => ({ nodeId, path: filePath, width, height, bytes, sha256 }));
  assert(exact(projection, FIGMA_RECORDS), 'Figma manifest capture declaration mismatch');
  assert(sha256Buffer(Buffer.from(JSON.stringify(projection))) === FIGMA_AGGREGATE_SHA256 && manifest.captureAggregate?.sha256 === FIGMA_AGGREGATE_SHA256 && manifest.captureAggregate?.bytes === 196931, 'Figma aggregate mismatch');
  const captures = FIGMA_RECORDS.map((record) => validatePng(path.join(CONTRACT_DIR, record.path), record));
  return { manifest: fileRecord(FIGMA_MANIFEST_PATH), captures, aggregateSha256: FIGMA_AGGREGATE_SHA256, bytes: 196931, role: 'read-only-reference', newFreezeCreated: false };
}

function validateBrowserInputs() {
  assert(exact(sortedEntries(BROWSER_DIR), EXACT_BROWSER_FILES), `Browser evidence file set mismatch: ${sortedEntries(BROWSER_DIR).join(', ')}`);
  const records = BROWSER_RECORDS.map((expected) => {
    const filePath = path.join(BROWSER_DIR, expected.path);
    assert(fs.existsSync(filePath) && fs.statSync(filePath).isFile(), `Browser artifact missing: ${expected.path}`);
    const actual = fileRecord(filePath, expected.path);
    assert(actual.bytes === expected.bytes && actual.sha256 === expected.sha256, `Browser artifact drift: ${expected.path}`);
    if (expected.path.endsWith('.png')) validatePng(filePath, expected);
    return { path: expected.path, bytes: expected.bytes, sha256: expected.sha256 };
  });
  const bytes = records.reduce((sum, item) => sum + item.bytes, 0);
  const manifestAggregate = sha256Buffer(Buffer.from(JSON.stringify(records)));
  assert(bytes === 817599 && manifestAggregate === BROWSER_NINE_FILE_MANIFEST_AGGREGATE_SHA256, 'Browser nine-file bytes/manifest aggregate mismatch');
  const harness = fileRecord(path.join(BROWSER_DIR, 'stage3-browser-acceptance-harness.mjs'), 'stage3-browser-acceptance-harness.mjs');
  assert(harness.bytes === 94749 && harness.sha256 === BROWSER_HARNESS_SHA256, 'Browser harness drift');
  const metrics = readJson(path.join(BROWSER_DIR, 'stage3-browser-acceptance-metrics.json'));
  assert(metrics.schemaVersion === 1 && metrics.stage === '3' && metrics.status === 'passed' && metrics.mode === 'full-source-bound', 'Browser metrics header mismatch');
  assert(metrics.runId === 'uiux-stage3-browser-20260809T115615647Z' && metrics.browser?.name === 'chromium' && metrics.browser?.version === '147.0.7727.15', 'Browser identity mismatch');
  assert(metrics.assertionSummary?.total === 23 && metrics.assertionSummary?.passed === 23 && metrics.assertionSummary?.failed === 0, 'Browser assertion summary mismatch');
  assert(exact(metrics.assertions?.map((item) => item.id), EXPECTED_BROWSER_ASSERTIONS) && metrics.assertions.every((item) => item.passed === true), 'Browser assertion registry mismatch');
  assert(exact(metrics.viewports?.map((item) => item.width), [360, 375, 390, 414, 430, 768, 1024, 1440]), 'Browser viewport matrix mismatch');
  assert(metrics.sourceBinding?.identical === true && metrics.sourceBinding.pre?.length === 67 && exact(metrics.sourceBinding.pre, metrics.sourceBinding.post), 'Browser source pre/post mismatch');
  for (const source of metrics.sourceBinding.pre) {
    const absolutePath = path.join(REPO_ROOT, source.path);
    assert(fs.existsSync(absolutePath) && fs.statSync(absolutePath).isFile(), `Browser-bound source missing: ${source.path}`);
    assert(fs.statSync(absolutePath).size === source.bytes && sha256File(absolutePath) === source.sha256, `Browser-bound source drift: ${source.path}`);
  }
  assert(metrics.metrics?.responsiveRows === 8 && metrics.metrics?.pwaLayers?.motion?.durations?.every((item) => Number.parseFloat(item.transitionDuration) <= 0.001), 'Browser responsive/reduced-motion evidence mismatch');
  assert(metrics.metrics?.browserUrlScrub?.fragments?.every((item) => item.scan?.failures?.length === 0) && metrics.metrics?.browserUrlScrub?.query?.leakScans?.every((item) => item.failures?.length === 0), 'Browser URL scrub/leak scan mismatch');
  assert(metrics.diagnostics?.unexpectedConsoleErrors?.length === 0 && metrics.diagnostics?.pageErrors?.length === 0 && metrics.diagnostics?.unexpectedRequestFailures?.length === 0, 'Browser unexpected diagnostics mismatch');
  const screenshotProjection = metrics.screenshots.map(({ file, bytes: fileBytes, sha256 }) => ({ path: file, bytes: fileBytes, sha256 })).sort((a, b) => a.path.localeCompare(b.path));
  assert(exact(screenshotProjection, records.filter((item) => item.path.endsWith('.png'))), 'Browser screenshot declaration mismatch');
  return { metrics: fileRecord(path.join(BROWSER_DIR, 'stage3-browser-acceptance-metrics.json')), harness, records, bytes, reportedAggregateSha256: BROWSER_NINE_FILE_REPORTED_AGGREGATE_SHA256, manifestAggregateSha256: manifestAggregate, runId: metrics.runId, assertions: 23, screenshots: 8, viewports: 8, sourcePrePostIdentical: true };
}

function validateGateInputs() {
  assert(exact(sortedEntries(GATES_DIR), EXACT_GATE_FILES), `Gate file set mismatch: ${sortedEntries(GATES_DIR).join(', ')}`);
  const expectedFiles = [
    ['stage3-runtime-final.json', 219627, RUNTIME_FINAL_SHA256],
    ['stage3-eslint-final.json', 482285, ESLINT_FINAL_SHA256],
    ['stage3-eslint-delta.json', 15717, ESLINT_DELTA_SHA256],
    ['stage3-prettier-delta.json', 3468, PRETTIER_DELTA_SHA256],
  ];
  const artifacts = expectedFiles.map(([name, bytes, sha256]) => {
    const record = fileRecord(path.join(GATES_DIR, name), `assets/gates/${name}`);
    assert(record.bytes === bytes && record.sha256 === sha256, `Gate artifact drift: ${name}`);
    return record;
  });
  const runtime = readJson(path.join(GATES_DIR, 'stage3-runtime-final.json'));
  assert(runtime.success === true && runtime.testResults?.length === 58 && runtime.numTotalTestSuites === 118 && runtime.numPassedTestSuites === 118 && runtime.numFailedTestSuites === 0, 'Runtime suite/file gate mismatch');
  assert(runtime.numTotalTests === 664 && runtime.numPassedTests === 664 && runtime.numFailedTests === 0 && runtime.numPendingTests === 0 && runtime.numTodoTests === 0, 'Runtime test count mismatch');
  assert(runtime.snapshot?.added === 0 && runtime.snapshot?.filesAdded === 0 && runtime.snapshot?.filesRemoved === 0 && runtime.snapshot?.filesUpdated === 0 && runtime.snapshot?.didUpdate === false, 'Runtime snapshot mutation mismatch');
  const eslint = readJson(path.join(GATES_DIR, 'stage3-eslint-final.json'));
  assert(Array.isArray(eslint) && eslint.length === 66, 'ESLint exact-file report mismatch');
  const eslintCounts = eslint.reduce((sum, item) => ({ errors: sum.errors + item.errorCount, warnings: sum.warnings + item.warningCount }), { errors: 0, warnings: 0 });
  assert(exact(eslintCounts, { errors: 184, warnings: 1 }), `ESLint raw counts mismatch: ${JSON.stringify(eslintCounts)}`);
  const eslintDelta = readJson(path.join(GATES_DIR, 'stage3-eslint-delta.json'));
  assert(eslintDelta.comparison?.stage3New === 0 && eslintDelta.comparison?.baseOnlyRemoved === 83 && eslintDelta.comparison?.exactMatched === 148 && eslintDelta.comparison?.semanticMatched === 37, 'ESLint delta mismatch');
  const prettier = readJson(path.join(GATES_DIR, 'stage3-prettier-delta.json'));
  assert(prettier.scope?.prettierFiles === 77 && prettier.rawCheck?.exitCode === 2 && prettier.rawCheck?.styleDirtyFiles === 14 && prettier.comparison?.stage3New === 0 && prettier.comparison?.baseOnlyRemoved === 4, 'Prettier delta/raw disclosure mismatch');
  const technical = readJson(TECHNICAL_GATES_PATH);
  assert(technical.schemaVersion === 1 && technical.stage === '3' && technical.status === 'settled_with_disclosed_inherited_diagnostics_and_compose_fixture_caveat', 'Technical gate header mismatch');
  assert(technical.frontend?.serialVitest?.tests === 664 && technical.frontend?.guardSelfTests?.tests === 45 && technical.frontend?.guardUi?.routes === 30 && technical.frontend?.vueTsc?.exitCode === 0, 'Technical frontend gate mismatch');
  assert(technical.frontend?.build?.modules === 2160 && technical.frontend?.build?.pwaEntries === 166 && technical.frontend?.eslint?.stage3NewDiagnostics === 0 && technical.frontend?.prettier?.stage3NewHunks === 0, 'Technical build/delta gate mismatch');
  assert(technical.backendAndDeploy?.g1?.passed === 231 && technical.backendAndDeploy?.g1?.skipped === 20 && technical.backendAndDeploy?.g2?.passed === 47, 'Backend G1/G2 mismatch');
  assert(technical.backendAndDeploy?.g3LiteralCleanWorktree?.exitCode === 1 && technical.backendAndDeploy?.g3LiteralCleanWorktree?.failureRecords === 2 && technical.backendAndDeploy?.g3LiteralCleanWorktree?.blanketPassClaimed === false, 'Backend G3 caveat mismatch');
  assert(technical.backendAndDeploy?.isolatedByteIdenticalComposeMirror?.exitCode === 0 && technical.backendAndDeploy?.isolatedByteIdenticalComposeMirror?.bothComposeSubtestsPassed === true, 'Compose mirror gate mismatch');
  assert(technical.protectedBoundary?.protectedDiffSha256 === EMPTY_SHA256 && technical.protectedBoundary?.dashboardMarketRegion?.sha256 === PROTECTED_REGION_SHA256, 'Protected boundary gate mismatch');
  const sourceBinding = validateRuntimeBinding();
  return { artifacts, technical: fileRecord(TECHNICAL_GATES_PATH), sourceBinding, facts: technical };
}

function validateEvidenceManifest() {
  const manifest = readJson(EVIDENCE_MANIFEST_PATH);
  assert(manifest.schemaVersion === 1 && manifest.stage === '3' && manifest.status === 'local_evidence_inputs_frozen', 'Evidence manifest header mismatch');
  assert(manifest.stageCompleteAuthority === false && manifest.sitesProven === false && manifest.comparisonBaseCommit === COMPARISON_BASE, 'Evidence manifest claim boundary mismatch');
  assert(manifest.browserEvidence?.reportedNineFileAggregateSha256 === BROWSER_NINE_FILE_REPORTED_AGGREGATE_SHA256 && manifest.browserEvidence?.nineFileBytes === 817599, 'Evidence manifest browser aggregate mismatch');
  assert(manifest.runtimeSourceBinding?.sourceCount === 120 && manifest.runtimeSourceBinding?.aggregateSha256 === SOURCE_BINDING_AGGREGATE_SHA256, 'Evidence manifest source binding mismatch');
  assert(manifest.localCapture?.outputPolicy === 'exact' && exact(manifest.localCapture?.expectedFiles, EXACT_OUTPUT_FILES), 'Evidence manifest local output contract mismatch');
  assert(Array.isArray(manifest.inputs) && manifest.inputs.length > 0, 'Evidence manifest input set missing');
  for (const expected of manifest.inputs) {
    const absolutePath = path.join(CONTRACT_DIR, expected.path);
    assert(fs.existsSync(absolutePath) && fs.statSync(absolutePath).isFile(), `Manifest input missing: ${expected.path}`);
    const actual = fileRecord(absolutePath, expected.path);
    assert(actual.bytes === expected.bytes && actual.sha256 === expected.sha256, `Manifest input drift: ${expected.path}`);
  }
  const projection = manifest.inputs.map(({ path: filePath, bytes, sha256 }) => ({ path: filePath, bytes, sha256 }));
  assert(sha256Buffer(Buffer.from(JSON.stringify(projection))) === manifest.inputProjectionAggregateSha256, 'Evidence manifest input projection aggregate mismatch');
  return { ...fileRecord(EVIDENCE_MANIFEST_PATH), inputCount: manifest.inputs.length, inputProjectionAggregateSha256: manifest.inputProjectionAggregateSha256 };
}

function validateCanonicalInputs() {
  assert(fs.existsSync(HTML_PATH) && fs.statSync(HTML_PATH).isFile(), `Evidence HTML missing: ${HTML_PATH}`);
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
  return page.evaluate(({ expectedAssertionIds, figmaRecords, browserRecords, sourceAggregate, figmaAggregate, browserMetricsSha, browserAggregate, harnessSha, emptySha, protectedSha, inputsValid, technical }) => {
    const q = (selector, root = document) => root.querySelector(selector);
    const qa = (selector, root = document) => [...root.querySelectorAll(selector)];
    const exact = (actual, expected) => JSON.stringify(actual) === JSON.stringify(expected);
    const number = (value) => Number(value);
    const body = document.body;
    const assertions = [];
    const record = (id, passed, evidence) => assertions.push({ id, passed: Boolean(passed), evidence });
    const registry = qa('[data-assertion-id]').map((node) => node.dataset.assertionId);
    if (!exact(registry, expectedAssertionIds) || new Set(registry).size !== expectedAssertionIds.length) throw new Error(`Assertion registry mismatch: ${JSON.stringify(registry)}`);

    record(expectedAssertionIds[0], body.dataset.evidenceRole === 'hash-bound-local-derivative' && body.dataset.stageCompleteAuthority === 'false' && body.dataset.sitesProven === 'false', { role: body.dataset.evidenceRole, stageCompleteAuthority: body.dataset.stageCompleteAuthority, sitesProven: body.dataset.sitesProven });
    const figma = q('#stage3-figma-reference');
    record(expectedAssertionIds[1], body.dataset.figmaRole === 'read-only-reference' && body.dataset.stage3FigmaFreezeCreated === 'false' && figma?.dataset.newFreeze === 'false' && number(figma?.dataset.detachedCount) === 0, { role: body.dataset.figmaRole, newFreeze: figma?.dataset.newFreeze, detached: number(figma?.dataset.detachedCount) });
    const figmaImages = qa('[data-figma-export]').map((node) => { const image = q('img', node); return { nodeId: node.dataset.nodeId, path: node.dataset.figmaExport, width: image?.naturalWidth, height: image?.naturalHeight, complete: image?.complete }; });
    const expectedFigmaImages = figmaRecords.map((item) => ({ nodeId: item.nodeId, path: item.path, width: item.width, height: item.height, complete: true }));
    record(expectedAssertionIds[2], number(figma?.dataset.figmaCaptureCount) === 4 && body.dataset.figmaCaptureAggregateSha256 === figmaAggregate && exact(figmaImages, expectedFigmaImages) && inputsValid, { images: figmaImages, aggregate: body.dataset.figmaCaptureAggregateSha256 });

    const shell = q('#stage3-shell-matrix');
    const routes = qa('[data-route]').map((node) => ({ path: node.dataset.route, scope: node.dataset.scope, shell: node.dataset.shell }));
    record(expectedAssertionIds[3], routes.length === 30 && new Set(routes.map((item) => item.path)).size === 30 && number(shell?.dataset.routeCount) === 30, { routeCount: routes.length });
    const scopeCounts = Object.fromEntries(['route', 'section', 'off'].map((scope) => [scope, routes.filter((item) => item.scope === scope).length]));
    record(expectedAssertionIds[4], exact(scopeCounts, { route: 5, section: 21, off: 4 }) && number(shell?.dataset.scopeRoute) === 5 && number(shell?.dataset.scopeSection) === 21 && number(shell?.dataset.scopeOff) === 4, scopeCounts);
    const shellCounts = Object.fromEntries(['public', 'focused-authenticated', 'standard-authenticated', 'protected-legacy', 'system-recovery'].map((kind) => [kind, routes.filter((item) => item.shell === kind).length]));
    record(expectedAssertionIds[5], exact(shellCounts, { public: 3, 'focused-authenticated': 1, 'standard-authenticated': 21, 'protected-legacy': 4, 'system-recovery': 1 }), shellCounts);
    record(expectedAssertionIds[6], exact(routes.filter((item) => item.shell === 'public').map((item) => item.path), ['/login', '/i/:code', '/register']) && exact(routes.filter((item) => item.shell === 'focused-authenticated').map((item) => item.path), ['/setup-password']), { public: routes.filter((item) => item.shell === 'public').map((item) => item.path), focused: routes.filter((item) => item.shell === 'focused-authenticated').map((item) => item.path) });

    const security = q('#stage3-secure-registration');
    record(expectedAssertionIds[7], exact(security?.dataset.recoveryOutcomes.split(','), ['not-found', 'forbidden', 'deep-link-failure']), { outcomes: security?.dataset.recoveryOutcomes });
    record(expectedAssertionIds[8], security?.dataset.webLinkPattern === '^/i/[A-Za-z0-9]{8}$' && number(security?.dataset.rawResponseExceptions) === 1 && number(security?.dataset.rawUrlExceptions) === 1, { pattern: security?.dataset.webLinkPattern, rawResponseExceptions: number(security?.dataset.rawResponseExceptions), rawUrlExceptions: number(security?.dataset.rawUrlExceptions) });
    record(expectedAssertionIds[9], number(security?.dataset.contextTtlMax) === 600 && number(security?.dataset.secretBrowserLeaks) === 0, { ttlMax: number(security?.dataset.contextTtlMax), leaks: number(security?.dataset.secretBrowserLeaks) });

    const browser = q('#stage3-browser-proof');
    record(expectedAssertionIds[10], browser?.dataset.browserStatus === 'passed' && number(browser?.dataset.browserAssertionCount) === 23 && number(browser?.dataset.browserFailedCount) === 0 && body.dataset.browserMetricsSha256 === browserMetricsSha && body.dataset.browserNineFileAggregateSha256 === browserAggregate && body.dataset.browserHarnessSha256 === harnessSha, { runId: body.dataset.browserRunId, metricsSha256: body.dataset.browserMetricsSha256, aggregateSha256: body.dataset.browserNineFileAggregateSha256 });
    const browserImages = qa('[data-browser-screenshot]').map((node) => { const image = q('img', node); return { path: node.dataset.browserScreenshot, width: image?.naturalWidth, height: image?.naturalHeight, complete: image?.complete }; }).sort((a, b) => a.path.localeCompare(b.path));
    const expectedBrowserImages = browserRecords.filter((item) => item.path.endsWith('.png')).map((item) => ({ path: item.path, width: item.width, height: item.height, complete: true })).sort((a, b) => a.path.localeCompare(b.path));
    record(expectedAssertionIds[11], browserImages.length === 8 && number(browser?.dataset.browserScreenshotCount) === 8 && exact(browserImages, expectedBrowserImages) && inputsValid, { images: browserImages });
    record(expectedAssertionIds[12], number(browser?.dataset.responsiveRowCount) === 8 && browser?.dataset.sourcePrePostIdentical === 'true', { responsiveRows: number(browser?.dataset.responsiveRowCount), sourcePrePostIdentical: browser?.dataset.sourcePrePostIdentical });
    record(expectedAssertionIds[13], number(browser?.dataset.unexpectedDiagnostics) === 0 && number(security?.dataset.secretBrowserLeaks) === 0, { unexpectedDiagnostics: number(browser?.dataset.unexpectedDiagnostics), secretLeaks: number(security?.dataset.secretBrowserLeaks) });

    const gates = q('#stage3-gates-protected');
    record(expectedAssertionIds[14], number(gates?.dataset.vitestFiles) === 58 && number(gates?.dataset.vitestSuites) === 118 && number(gates?.dataset.vitestTests) === 664, { files: number(gates?.dataset.vitestFiles), suites: number(gates?.dataset.vitestSuites), tests: number(gates?.dataset.vitestTests) });
    record(expectedAssertionIds[15], number(gates?.dataset.eslintStage3New) === 0, { stage3New: number(gates?.dataset.eslintStage3New), inheritedDisclosed: true });
    record(expectedAssertionIds[16], number(gates?.dataset.prettierStage3New) === 0, { stage3New: number(gates?.dataset.prettierStage3New), inheritedDisclosed: true });
    record(expectedAssertionIds[17], number(gates?.dataset.guardTests) === 45 && number(gates?.dataset.guardRoutes) === 30 && number(gates?.dataset.v2CssFiles) === 3 && number(gates?.dataset.buildModules) === 2160 && number(gates?.dataset.pwaEntries) === 166, { guardTests: number(gates?.dataset.guardTests), routes: number(gates?.dataset.guardRoutes), css: number(gates?.dataset.v2CssFiles), modules: number(gates?.dataset.buildModules), pwaEntries: number(gates?.dataset.pwaEntries) });
    record(expectedAssertionIds[18], technical.backendAndDeploy.g3LiteralCleanWorktree.exitCode === 1 && technical.backendAndDeploy.g3LiteralCleanWorktree.blanketPassClaimed === false && technical.backendAndDeploy.isolatedByteIdenticalComposeMirror.exitCode === 0 && technical.backendAndDeploy.isolatedByteIdenticalComposeMirror.bothComposeSubtestsPassed === true, { literal: technical.backendAndDeploy.g3LiteralCleanWorktree, mirror: technical.backendAndDeploy.isolatedByteIdenticalComposeMirror });
    record(expectedAssertionIds[19], number(gates?.dataset.protectedDiffBytes) === 0 && gates?.dataset.protectedDiffSha256 === emptySha && number(gates?.dataset.protectedRegionSections) === 6 && number(gates?.dataset.protectedRegionBytes) === 4553 && gates?.dataset.protectedRegionSha256 === protectedSha, { diffBytes: number(gates?.dataset.protectedDiffBytes), diffSha256: gates?.dataset.protectedDiffSha256, region: { sections: number(gates?.dataset.protectedRegionSections), bytes: number(gates?.dataset.protectedRegionBytes), sha256: gates?.dataset.protectedRegionSha256 } });
    record(expectedAssertionIds[20], number(gates?.dataset.runtimeSourceCount) === 120 && gates?.dataset.runtimeSourceAggregate === sourceAggregate && number(body.dataset.runtimeSourceCount) === 120 && body.dataset.runtimeSourceAggregateSha256 === sourceAggregate, { count: number(gates?.dataset.runtimeSourceCount), aggregate: gates?.dataset.runtimeSourceAggregate });
    if (!exact(assertions.map((item) => item.id), expectedAssertionIds)) throw new Error('Audit assertion order mismatch');
    return { assertions, passed: assertions.filter((item) => item.passed).length, failed: assertions.filter((item) => !item.passed).length, measurements: { routes, scopeCounts, shellCounts, figmaImages, browserImages } };
  }, { expectedAssertionIds: EXPECTED_LOCAL_ASSERTIONS, figmaRecords: FIGMA_RECORDS, browserRecords: BROWSER_RECORDS, sourceAggregate: SOURCE_BINDING_AGGREGATE_SHA256, figmaAggregate: FIGMA_AGGREGATE_SHA256, browserMetricsSha: BROWSER_METRICS_SHA256, browserAggregate: BROWSER_NINE_FILE_REPORTED_AGGREGATE_SHA256, harnessSha: BROWSER_HARNESS_SHA256, emptySha: EMPTY_SHA256, protectedSha: PROTECTED_REGION_SHA256, inputsValid: context.inputsValid, technical: context.technical });
}

function residueDirectories() {
  if (!fs.existsSync(ASSETS_DIR)) return [];
  return fs.readdirSync(ASSETS_DIR, { withFileTypes: true }).filter((entry) => entry.isDirectory() && (entry.name.startsWith('.local-evidence-staging-') || entry.name.startsWith('.local-evidence-backup-'))).map((entry) => path.join(ASSETS_DIR, entry.name)).sort();
}

function removeDirectory(directory) {
  fs.rmSync(directory, { recursive: true, force: true });
}

function validateEvidenceDirectory(directory) {
  assert(fs.existsSync(directory) && fs.statSync(directory).isDirectory(), `Local evidence directory missing: ${directory}`);
  const actual = sortedEntries(directory);
  assert(exact(actual, EXACT_OUTPUT_FILES), `Local evidence exact output mismatch: ${actual.join(', ')}`);
  const metrics = readJson(path.join(directory, METRICS_FILE));
  assert(metrics.schemaVersion === 1 && metrics.stage === '3' && metrics.status === 'passed' && metrics.localEvidenceStatus === 'frozen', 'Local metrics header mismatch');
  assert(metrics.claimBoundary?.stageCompleteAuthority === false && metrics.claimBoundary?.sitesProven === false && metrics.claimBoundary?.newFigmaFreezeCreated === false, 'Local metrics claim boundary mismatch');
  assert(metrics.inputs?.runtimeSourceBinding?.sourceCount === 120 && metrics.inputs?.runtimeSourceBinding?.aggregateSha256 === SOURCE_BINDING_AGGREGATE_SHA256, 'Local metrics source binding mismatch');
  assert(metrics.inputs?.browser?.reportedAggregateSha256 === BROWSER_NINE_FILE_REPORTED_AGGREGATE_SHA256 && metrics.inputs?.browser?.metrics?.sha256 === BROWSER_METRICS_SHA256, 'Local metrics browser binding mismatch');
  assert(metrics.inputs?.figma?.aggregateSha256 === FIGMA_AGGREGATE_SHA256 && metrics.inputs?.figma?.newFreezeCreated === false, 'Local metrics Figma binding mismatch');
  assert(metrics.integrity?.domEqual === true && metrics.integrity?.auditEqual === true && metrics.integrity?.postCaptureRemeasurement === true, 'Local pre/post integrity mismatch');
  assert(exact(metrics.integrity?.consoleErrors, []) && exact(metrics.integrity?.pageErrors, []) && exact(metrics.integrity?.failedRequests, []) && exact(metrics.integrity?.blockedNetworkRequests, []), 'Local browser diagnostics mismatch');
  assert(exact(metrics.assertions?.map((item) => item.id), EXPECTED_LOCAL_ASSERTIONS) && metrics.assertions.every((item) => item.passed === true), 'Local assertion result mismatch');
  assert(metrics.assertionSummary?.total === 21 && metrics.assertionSummary?.passed === 21 && metrics.assertionSummary?.failed === 0, 'Local assertion summary mismatch');
  assert(exact(metrics.outputSet?.files, EXACT_OUTPUT_FILES) && metrics.outputSet?.pngCount === 6 && metrics.outputSet?.metricsCount === 1, 'Local declared output mismatch');
  assert(Array.isArray(metrics.captures) && metrics.captures.length === 6, 'Local capture count mismatch');
  for (const capture of metrics.captures) {
    const filePath = path.join(directory, capture.file);
    assert(CAPTURE_SPECS.some((spec) => spec.file === capture.file && spec.selector === capture.selector), `Unexpected local capture: ${capture.file}`);
    const stat = fs.statSync(filePath);
    const dimensions = pngDimensions(filePath);
    const visualStats = pngVisualStats(filePath);
    assert(stat.size === capture.bytes && sha256File(filePath) === capture.sha256, `Local capture byte/hash mismatch: ${capture.file}`);
    assert(dimensions.width === capture.width && dimensions.height === capture.height, `Local capture dimensions mismatch: ${capture.file}`);
    assert(visualStats.uniqueColorCount >= 8 && visualStats.changedPixelCount >= 100 && exact(visualStats, capture.visualStats), `Local capture visual mismatch: ${capture.file}`);
  }
  return metrics;
}

function recoverBeforeDependencies() {
  fs.mkdirSync(ASSETS_DIR, { recursive: true });
  for (const residue of residueDirectories()) removeDirectory(residue);
  if (fs.existsSync(PUBLISHED_DIR) && sortedEntries(PUBLISHED_DIR).length === 0) removeDirectory(PUBLISHED_DIR);
  if (fs.existsSync(PUBLISHED_DIR)) validateEvidenceDirectory(PUBLISHED_DIR);
  return { residueRemoved: true, priorPublishedValidated: fs.existsSync(PUBLISHED_DIR) };
}

function atomicPromote(stagingDir, runId) {
  validateEvidenceDirectory(stagingDir);
  const backupDir = path.join(ASSETS_DIR, `.local-evidence-backup-${runId}`);
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
    assert(residueDirectories().length === 0, 'Local evidence publication residue found');
  } catch (error) {
    if (fs.existsSync(PUBLISHED_DIR)) removeDirectory(PUBLISHED_DIR);
    if (movedCurrent && fs.existsSync(backupDir)) fs.renameSync(backupDir, PUBLISHED_DIR);
    throw new Error(`Atomic promotion failed: ${error.message}`);
  }
}

function createRunId() {
  return `stage3-local-${new Date().toISOString().replace(/[-:.]/g, '')}-${crypto.randomBytes(4).toString('hex')}`;
}

async function main() {
  const startedAt = new Date().toISOString();
  const runId = createRunId();
  const stagingDir = path.join(ASSETS_DIR, `.local-evidence-staging-${runId}`);
  const recovery = recoverBeforeDependencies();
  const playwright = resolvePlaywright();
  const fonts = resolveFonts();
  const inputs = validateCanonicalInputs();
  fs.mkdirSync(stagingDir, { recursive: false });
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
    assert(fontChecks.every((item) => item.loaded), `Font checks failed: ${JSON.stringify(fontChecks)}`);

    const auditContext = { inputsValid: true, technical: inputs.gates.facts };
    const preAudit = await auditPage(page, auditContext);
    assert(preAudit.passed === 21 && preAudit.failed === 0, `Pre-capture assertion failures: ${JSON.stringify(preAudit.assertions.filter((item) => !item.passed), null, 2)}`);
    const preDom = await canonicalDomSnapshot(page);
    const preDomSha256 = sha256Buffer(Buffer.from(preDom));
    const preAuditSha256 = hashValue(preAudit);
    const captures = [];
    for (const spec of CAPTURE_SPECS) {
      const locator = page.locator(spec.selector);
      assert(await locator.count() === 1, `Capture selector count mismatch: ${spec.selector}`);
      const box = await locator.evaluate((element) => { const rect = element.getBoundingClientRect(); return { x: rect.left + window.scrollX, y: rect.top + window.scrollY, width: rect.width, height: rect.height, documentWidth: document.documentElement.scrollWidth, documentHeight: document.documentElement.scrollHeight }; });
      assert(box.width > 0 && box.height > 0 && box.x >= 0 && box.y >= 0 && box.x + box.width <= box.documentWidth + 0.01 && box.y + box.height <= box.documentHeight + 0.01, `Capture geometry invalid: ${spec.file} ${JSON.stringify(box)}`);
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
      const dimensions = pngDimensions(outputPath);
      const visualStats = pngVisualStats(outputPath);
      assert(dimensions.width === width && dimensions.height === height && visualStats.uniqueColorCount >= 8 && visualStats.changedPixelCount >= 100, `Local capture invalid: ${spec.file}`);
      captures.push({ file: spec.file, selector: spec.selector, width, height, bytes: fs.statSync(outputPath).size, sha256: sha256File(outputPath), visualStats });
    }

    const postAudit = await auditPage(page, auditContext);
    const postDom = await canonicalDomSnapshot(page);
    const postDomSha256 = sha256Buffer(Buffer.from(postDom));
    const postAuditSha256 = hashValue(postAudit);
    assert(postAudit.passed === 21 && postAudit.failed === 0, `Post-capture assertion failures: ${JSON.stringify(postAudit.assertions.filter((item) => !item.passed), null, 2)}`);
    assert(preAuditSha256 === postAuditSha256 && stableJson(preAudit) === stableJson(postAudit), 'Pre/post audit changed');
    assert(preDomSha256 === postDomSha256 && preDom === postDom, 'Pre/post canonical DOM changed');
    assert(consoleErrors.length === 0 && pageErrors.length === 0 && failedRequests.length === 0 && blockedNetworkRequests.length === 0, `Local Chromium diagnostics: ${JSON.stringify({ consoleErrors, pageErrors, failedRequests, blockedNetworkRequests })}`);

    const metrics = {
      schemaVersion: 1,
      stage: '3',
      status: 'passed',
      localEvidenceStatus: 'frozen',
      runId,
      startedAt,
      completedAt: new Date().toISOString(),
      comparisonBaseCommit: COMPARISON_BASE,
      claimBoundary: { localDerivative: true, stageCompleteAuthority: false, sitesProven: false, newFigmaFreezeCreated: false, runtimeEvidenceHashBound: true },
      inputs: {
        evidenceManifest: inputs.evidenceManifest,
        html: inputs.html,
        script: inputs.script,
        figma: { manifest: inputs.figma.manifest, captures: inputs.figma.captures, bytes: inputs.figma.bytes, aggregateSha256: inputs.figma.aggregateSha256, role: inputs.figma.role, newFreezeCreated: false },
        browser: { metrics: inputs.browser.metrics, harness: inputs.browser.harness, runId: inputs.browser.runId, records: inputs.browser.records, bytes: inputs.browser.bytes, reportedAggregateSha256: inputs.browser.reportedAggregateSha256, manifestAggregateSha256: inputs.browser.manifestAggregateSha256, assertions: 23, screenshots: 8, viewports: 8, sourcePrePostIdentical: true },
        gates: { artifacts: inputs.gates.artifacts, technical: inputs.gates.technical },
        runtimeSourceBinding: { path: inputs.gates.sourceBinding.path, bytes: inputs.gates.sourceBinding.bytes, sha256: inputs.gates.sourceBinding.sha256, sourceCount: inputs.gates.sourceBinding.sourceCount, modifiedCount: inputs.gates.sourceBinding.modifiedCount, addedCount: inputs.gates.sourceBinding.addedCount, aggregateSha256: inputs.gates.sourceBinding.aggregateSha256 },
      },
      recoveryBeforeDependencyResolution: recovery,
      dependencies: { node: process.version, platform: `${process.platform}-${process.arch}`, playwright: playwright.resolvedFrom, browser: { engine: 'chromium', version: browserVersion, viewport: { width: 1500, height: 1200 }, deviceScaleFactor: 1 }, fonts: { family: 'Vazirmatn', root: fonts.root, checks: fontChecks, files: fonts.fonts.map(({ file, weight, bytes, sha256 }) => ({ file, weight, bytes, sha256 })) } },
      integrity: { preDomSha256, postDomSha256, domEqual: true, preAuditSha256, postAuditSha256, auditEqual: true, postCaptureRemeasurement: true, consoleErrors, pageErrors, failedRequests, blockedNetworkRequests },
      assertions: postAudit.assertions,
      assertionSummary: { total: 21, passed: 21, failed: 0, exactOrder: true },
      measurements: postAudit.measurements,
      captures,
      outputSet: { policy: 'exact', pngCount: 6, metricsCount: 1, files: EXACT_OUTPUT_FILES },
      publication: { strategy: 'atomic-directory-rename', partialPromotionAllowed: false, validationBeforePromotion: true, validationAfterPromotion: true, residueAllowed: false },
    };
    fs.writeFileSync(path.join(stagingDir, METRICS_FILE), `${JSON.stringify(metrics, null, 2)}\n`, { encoding: 'utf8', flag: 'wx' });
    validateEvidenceDirectory(stagingDir);
    await browserContext.close();
    await browser.close();
    browser = null;
    atomicPromote(stagingDir, runId);
    const published = validateEvidenceDirectory(PUBLISHED_DIR);
    const residues = residueDirectories();
    assert(residues.length === 0, `Publication residue: ${residues.join(', ')}`);
    const report = {
      status: 'FROZEN',
      runId: published.runId,
      startedAt: published.startedAt,
      completedAt: published.completedAt,
      assertions: `${published.assertionSummary.passed}/${published.assertionSummary.total}`,
      domSha256: published.integrity.postDomSha256,
      auditSha256: published.integrity.postAuditSha256,
      evidenceManifestSha256: published.inputs.evidenceManifest.sha256,
      runtimeSourceAggregateSha256: published.inputs.runtimeSourceBinding.aggregateSha256,
      browserReportedAggregateSha256: published.inputs.browser.reportedAggregateSha256,
      figmaAggregateSha256: published.inputs.figma.aggregateSha256,
      outputDirectory: path.relative(REPO_ROOT, PUBLISHED_DIR),
      residueDirectories: residues,
      files: published.captures.map(({ file, width, height, bytes, sha256 }) => ({ file, width, height, bytes, sha256 })).concat(fileRecord(path.join(PUBLISHED_DIR, METRICS_FILE), METRICS_FILE)),
    };
    process.stdout.write(`${JSON.stringify(report, null, 2)}\n`);
  } catch (error) {
    if (browser) await browser.close().catch(() => {});
    if (fs.existsSync(stagingDir)) removeDirectory(stagingDir);
    throw error;
  }
}

main().catch((error) => {
  process.stderr.write(`Stage 3 evidence capture failed: ${error.stack || error.message}\n`);
  process.exitCode = 1;
});
