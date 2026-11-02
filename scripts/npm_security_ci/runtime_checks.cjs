'use strict';
// Bounded regression checks against the actual original and rebuilt fork dist.
// Does not fetch packages, change the project, or accept a security release.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {pathToFileURL} = require('node:url');
const [root, kind] = process.argv.slice(2);
assert.equal(process.version, 'v24.22.1');
assert.ok(path.isAbsolute(root));
assert.ok(['glob', 'minimatch', 'resolve'].includes(kind));
const entries = kind === 'glob' ? ['commonjs/walker.js', 'esm/walker.js'] :
 kind === 'minimatch' ? ['commonjs/index.js', 'esm/index.js'] :
 ['cjs/index.js', 'es/index.js'];
let matches = 0, aborts = 0, guards = 0, parents = 0;
(async () => {
 for (const [i, entry] of entries.entries()) {
  const oldPath = path.join(root, '.security-original-package', 'dist', entry);
  const newPath = path.join(root, 'dist', entry);
  assert.ok(fs.statSync(oldPath).isFile() && fs.statSync(newPath).isFile());
  const original = i === 0 ? require(oldPath) : await import(pathToFileURL(oldPath).href);
  const patched = i === 0 ? require(newPath) : await import(pathToFileURL(newPath).href);
  assert.deepEqual(Object.keys(patched).sort(), Object.keys(original).sort());
  if (kind === 'glob') {
   for (const method of ['walkCB', 'walkCB2', 'walkCBSync', 'walkCB2Sync']) {
    const controller = new AbortController(); controller.abort();
    const before = new original.GlobUtil([], {}, {signal: controller.signal, posix: true});
    const after = new patched.GlobUtil([], {}, {signal: controller.signal, posix: true});
    let continued = 0, callback = 0;
    const stop = new Error('bounded ordinary cancellation continuation boundary');
    if (method === 'walkCB' || method === 'walkCBSync') {
     const next = method === 'walkCB' ? 'walkCB2' : 'walkCB2Sync';
     before[next] = () => {throw stop;};
     after[next] = () => {continued++; throw stop;};
     assert.throws(() => before[method]({}, [], () => {}), error => error === stop);
     after[method]({}, [], () => callback++);
    } else {
     assert.throws(() => before[method]({}, [], {processPatterns() {throw stop;}}, () => {}), error => error === stop);
     after[method]({}, [], {processPatterns() {continued++; throw stop;}}, () => callback++);
    }
    assert.equal(continued, 0); assert.equal(callback, 1); aborts++;
   }
  } else if (kind === 'minimatch') {
   for (const optimizationLevel of [0, 1, 2]) for (const dot of [false, true]) for (const nocase of [false, true]) {
    const options = {optimizationLevel, dot, nocase};
    for (const pattern of ['**/*.js', 'src/{api,ui}/*.vue', 'a/?/b', 'a/**/b', '{x,*}/{0..9}', '[!a]*.ts', '*.json', 'a/**/../b/c']) {
     for (const name of ['a.js', 'src/api/Page.vue', 'src/ui/App.vue', 'a/x/b', 'a/b', 'a/x/y/b', 'x/5', 'y/9', 'beta.ts', 'alpha.ts', 'a.json', '.hidden.js', '../b/c', 'a/x/../b/c']) {
      assert.equal(patched.minimatch(name, pattern, options), original.minimatch(name, pattern, options)); matches++;
     }
    }
   }
   const before = new original.Minimatch('a', {optimizationLevel: 0, braceExpandMax: 1});
   const after = new patched.Minimatch('a', {optimizationLevel: 0, braceExpandMax: 1});
   const ordinary = [['**', '..', 'a', 'b']];
   assert.equal(before.firstPhasePreProcess(structuredClone(ordinary)).length, 2);
   assert.equal(after.firstPhasePreProcess(structuredClone(ordinary)).length, 1); guards++;
   const list = Array.from({length: 1001}, (_, n) => ['file' + n]);
   assert.equal(patched.Minimatch.prototype.secondPhasePreProcess.call({partsMatch() {throw new Error('dedup must be skipped');}}, list), list); guards++;
  } else {
   assert.deepEqual(patched.DEFAULTS, original.DEFAULTS);
   for (const [key, value] of Object.entries(original.DEFAULTS)) if (Array.isArray(value)) {
    assert.notEqual(value, patched.DEFAULTS[key]); assert.equal(Object.isFrozen(patched.DEFAULTS[key]), true);
   }
   assert.equal(Object.isFrozen(patched.DEFAULTS), true);
   for (const options of [{}, {browser: true}, {dedupe: ['vue']}, {resolveOnly: ['vue']}, {exportConditions: ['production']}]) {
    const a = original.nodeResolve(options), b = patched.nodeResolve(options);
    assert.equal(a.name, b.name); assert.deepEqual(Object.keys(a).sort(), Object.keys(b).sort()); parents++;
   }
  }
 }
 console.log(JSON.stringify({status: 'ACTUAL_CJS_ESM_FORK_REGRESSION_PASS', kind, formats: 2, matches, aborts, guards, parents, security_accepted: false}));
})().catch(error => {console.error(error); process.exitCode = 1;});
