#!/usr/bin/env node
import fs from 'node:fs'
import path from 'node:path'
import process from 'node:process'
import { fileURLToPath } from 'node:url'
import {
  checkCatalogBoundary,
  checkRoutePolicy,
  checkV2Styles,
  hasV2CssMarker,
  isProductActivationSourcePath,
} from './lib/design-system-v2-guard.mjs'

const scriptDir = path.dirname(fileURLToPath(import.meta.url))
const frontendRoot = path.resolve(scriptDir, '..')
const srcRoot = path.join(frontendRoot, 'src')

function toFrontendPath(filePath) {
  return path.relative(frontendRoot, filePath).split(path.sep).join('/')
}

function walkFiles(directory) {
  if (!fs.existsSync(directory)) return []
  const files = []
  for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
    const filePath = path.join(directory, entry.name)
    if (entry.isDirectory()) {
      files.push(...walkFiles(filePath))
    } else {
      files.push(filePath)
    }
  }
  return files
}

function isV2Style(filePath) {
  const repoPath = toFrontendPath(filePath)
  return (
    repoPath.endsWith('.css') &&
    (/^src\/styles\/design-system-v2(?:[.-].*)?\.css$/.test(repoPath) ||
      repoPath.startsWith('src/design-system-v2/'))
  )
}

function readV2StyleSources() {
  const styleSources = []
  for (const filePath of walkFiles(srcRoot)) {
    if (filePath.endsWith('.css')) {
      const styleSource = readSource(filePath)
      if (isV2Style(filePath) || hasV2CssMarker(styleSource.source)) styleSources.push(styleSource)
      continue
    }
    if (!filePath.endsWith('.vue')) continue

    const source = fs.readFileSync(filePath, 'utf8')
    let styleIndex = 0
    for (const match of source.matchAll(/<style\b[^>]*>([\s\S]*?)<\/style>/gi)) {
      styleIndex += 1
      if (!hasV2CssMarker(match[1])) continue
      styleSources.push({
        path: `${toFrontendPath(filePath)}#style-${styleIndex}`,
        source: match[1],
      })
    }
  }
  return styleSources
}

function readSource(filePath) {
  return { path: toFrontendPath(filePath), source: fs.readFileSync(filePath, 'utf8') }
}

function readProductActivationSources() {
  const candidates = [...walkFiles(srcRoot), path.join(frontendRoot, 'index.html')].filter(
    (filePath) => isProductActivationSourcePath(toFrontendPath(filePath)),
  )
  return candidates.filter((filePath) => fs.existsSync(filePath)).map(readSource)
}

const styleFiles = readV2StyleSources()
const manifestPath = path.join(srcRoot, 'design-system-v2', 'scope-manifest.json')
const routerPath = path.join(srcRoot, 'router', 'index.ts')
const catalogPath = path.join(srcRoot, 'components', 'ui', 'AppDesignSystemCatalog.vue')

let findings = []
if (!fs.existsSync(manifestPath)) {
  findings.push({
    code: 'missing-scope-manifest',
    file: 'src/design-system-v2/scope-manifest.json',
    detail: 'Stage 3 requires a machine-readable scope manifest',
  })
} else {
  try {
    const manifest = JSON.parse(fs.readFileSync(manifestPath, 'utf8'))
    findings.push(
      ...checkRoutePolicy({
        manifest,
        routerSource: fs.readFileSync(routerPath, 'utf8'),
        activationSources: readProductActivationSources(),
      }),
    )
  } catch (error) {
    findings.push({
      code: 'invalid-scope-manifest-json',
      file: 'src/design-system-v2/scope-manifest.json',
      detail: error instanceof Error ? error.message : String(error),
    })
  }
}

findings.push(...checkV2Styles(styleFiles))
if (!fs.existsSync(catalogPath)) {
  findings.push({
    code: 'missing-catalog-source',
    file: 'src/components/ui/AppDesignSystemCatalog.vue',
    detail: 'Stage 2 requires the private executable catalog',
  })
} else {
  findings.push(...checkCatalogBoundary(readSource(catalogPath)))
}
findings.sort(
  (left, right) =>
    left.file.localeCompare(right.file) ||
    left.code.localeCompare(right.code) ||
    (left.location ?? '').localeCompare(right.location ?? ''),
)

if (!findings.length) {
  console.log(`PASS UIUX v2 scope guard (${styleFiles.length} V2 CSS files, 30 product routes)`)
} else {
  console.error(`FAIL UIUX v2 scope guard: ${findings.length} findings`)
  for (const finding of findings) {
    const location = finding.location ? `:${finding.location}` : ''
    console.error(`- [${finding.code}] ${finding.file}${location} ${finding.detail}`)
  }
  process.exitCode = 1
}
