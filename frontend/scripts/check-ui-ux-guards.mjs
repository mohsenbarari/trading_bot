#!/usr/bin/env node
import fs from 'node:fs'
import path from 'node:path'
import process from 'node:process'
import { fileURLToPath } from 'node:url'

const scriptDir = path.dirname(fileURLToPath(import.meta.url))
const frontendRoot = path.resolve(scriptDir, '..')
const srcRoot = path.join(frontendRoot, 'src')
const tokenSource = path.join(srcRoot, 'assets', 'main.css')

const sourceExtensions = new Set(['.css', '.ts', '.vue'])
const skippedPathFragments = [
  'src/components/chat/',
  'src/components/messenger-v2/',
  'src/services/chat/',
  'src/stores/chat/',
  'src/views/MessengerView.vue',
]
const skippedFileSuffixes = ['.test.ts', '.spec.ts', '.d.ts']

const tradeColorFiles = [
  'src/views/DashboardView.vue',
  'src/views/MarketView.vue',
  'src/components/OffersList.vue',
]

const tradeColorPatterns = [
  /#15803d\b/gi,
  /#16a34a\b/gi,
  /#22c55e\b/gi,
  /#065f46\b/gi,
  /#047857\b/gi,
  /#b91c1c\b/gi,
  /#dc2626\b/gi,
  /#ef4444\b/gi,
  /#991b1b\b/gi,
  /\b(?:text|bg|border|from|via|to)-(?:green|emerald|red|rose)-[0-9]{2,3}\b/g,
]

const modalOverlayAllowlist = new Set([
  'src/components/TradingView.vue',
  'src/components/UserProfile.vue',
  'src/components/PublicProfile.vue',
])

function toRepoPath(filePath) {
  return path.relative(frontendRoot, filePath).split(path.sep).join('/')
}

function shouldSkipSourceFile(filePath) {
  const repoPath = toRepoPath(filePath)
  return skippedPathFragments.some(fragment => repoPath.startsWith(fragment))
    || skippedFileSuffixes.some(suffix => repoPath.endsWith(suffix))
}

function* walkFiles(dir) {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    if (entry.name === 'node_modules' || entry.name === 'dist' || entry.name === 'coverage') continue
    const fullPath = path.join(dir, entry.name)
    if (entry.isDirectory()) {
      yield* walkFiles(fullPath)
      continue
    }
    if (sourceExtensions.has(path.extname(entry.name)) && !shouldSkipSourceFile(fullPath)) {
      yield fullPath
    }
  }
}

function lineForIndex(source, index) {
  return source.slice(0, index).split('\n').length
}

function read(filePath) {
  return fs.readFileSync(filePath, 'utf8')
}

function collectDefinedTokens() {
  const css = read(tokenSource)
  const defined = new Set()
  const definitionPattern = /(--ds-[a-z0-9-]+)\s*:/g
  for (const match of css.matchAll(definitionPattern)) {
    defined.add(match[1])
  }
  return defined
}

function checkUndefinedTokens() {
  const defined = collectDefinedTokens()
  const missingByToken = new Map()
  const usagePattern = /var\(\s*(--ds-[a-z0-9-]+)/g

  for (const filePath of walkFiles(srcRoot)) {
    const source = read(filePath)
    const repoPath = toRepoPath(filePath)
    for (const match of source.matchAll(usagePattern)) {
      const token = match[1]
      if (defined.has(token)) continue
      const locations = missingByToken.get(token) ?? []
      locations.push(`${repoPath}:${lineForIndex(source, match.index ?? 0)}`)
      missingByToken.set(token, locations)
    }
  }

  return [...missingByToken.entries()]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([token, locations]) => ({
      token,
      locations: [...new Set(locations)].sort(),
    }))
}

function checkHardcodedTradeColors() {
  const findings = []

  for (const repoPath of tradeColorFiles) {
    const filePath = path.join(frontendRoot, repoPath)
    if (!fs.existsSync(filePath)) continue
    const source = read(filePath)
    for (const pattern of tradeColorPatterns) {
      pattern.lastIndex = 0
      for (const match of source.matchAll(pattern)) {
        findings.push({
          value: match[0],
          location: `${repoPath}:${lineForIndex(source, match.index ?? 0)}`,
        })
      }
    }
  }

  return findings.sort((a, b) => a.location.localeCompare(b.location) || a.value.localeCompare(b.value))
}

function checkModalOverlays() {
  const findings = []
  const overlayPattern = /\b[a-z-]*modal-overlay\b/g

  for (const filePath of walkFiles(srcRoot)) {
    if (path.extname(filePath) !== '.vue') continue
    const repoPath = toRepoPath(filePath)
    const source = read(filePath)
    const matches = [...source.matchAll(overlayPattern)]
    if (!matches.length || modalOverlayAllowlist.has(repoPath)) continue
    for (const match of matches) {
      findings.push({
        value: match[0],
        location: `${repoPath}:${lineForIndex(source, match.index ?? 0)}`,
      })
    }
  }

  return findings.sort((a, b) => a.location.localeCompare(b.location) || a.value.localeCompare(b.value))
}

function printTokenFindings(findings) {
  if (!findings.length) {
    console.log('PASS undefined design-token guard')
    return
  }

  console.error(`FAIL undefined design-token guard: ${findings.length} missing tokens`)
  for (const finding of findings) {
    console.error(`- ${finding.token}`)
    for (const location of finding.locations.slice(0, 8)) {
      console.error(`  ${location}`)
    }
    if (finding.locations.length > 8) {
      console.error(`  ... ${finding.locations.length - 8} more`)
    }
  }
}

function printFlatFindings(title, findings) {
  if (!findings.length) {
    console.log(`PASS ${title}`)
    return
  }

  console.error(`FAIL ${title}: ${findings.length} findings`)
  for (const finding of findings.slice(0, 80)) {
    console.error(`- ${finding.location} ${finding.value}`)
  }
  if (findings.length > 80) {
    console.error(`... ${findings.length - 80} more`)
  }
}

const undefinedTokens = checkUndefinedTokens()
const hardcodedTradeColors = checkHardcodedTradeColors()
const modalOverlayFindings = checkModalOverlays()

printTokenFindings(undefinedTokens)
printFlatFindings('hardcoded trade-side color guard', hardcodedTradeColors)
printFlatFindings('new bespoke modal-overlay guard', modalOverlayFindings)

const hasFailures = undefinedTokens.length > 0
  || hardcodedTradeColors.length > 0
  || modalOverlayFindings.length > 0

if (hasFailures) {
  process.exitCode = 1
}
