#!/usr/bin/env node
import fs from 'node:fs'
import path from 'node:path'
import process from 'node:process'
import { fileURLToPath } from 'node:url'
import {
  STAGE8B_TYPOGRAPHY_PATHS,
  assertStage8bTypographyContract,
} from './lib/stage8b-typography-guard.mjs'

const scriptDir = path.dirname(fileURLToPath(import.meta.url))
const repoRoot = path.resolve(scriptDir, '..', '..')

function sourceEntries() {
  return new Map(
    Object.values(STAGE8B_TYPOGRAPHY_PATHS).map((repoPath) => [
      repoPath,
      fs.readFileSync(path.join(repoRoot, repoPath), 'utf8'),
    ]),
  )
}

function walkProductSources(directory) {
  const entries = []
  for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
    if (entry.name === 'node_modules' || entry.name === 'dist') continue
    const filePath = path.join(directory, entry.name)
    if (entry.isDirectory()) {
      entries.push(...walkProductSources(filePath))
      continue
    }
    if (!/\.(?:css|[cm]?[jt]sx?|vue)$/.test(entry.name)) continue
    if (/\.(?:test|spec)\.[^/]+$/.test(entry.name) || entry.name.endsWith('.d.ts')) continue
    entries.push(filePath)
  }
  return entries
}

function productSourceEntries() {
  const sourceRoot = path.join(repoRoot, 'frontend', 'src')
  const entries = walkProductSources(sourceRoot).map((filePath) => [
    path.relative(repoRoot, filePath).split(path.sep).join('/'),
    fs.readFileSync(filePath, 'utf8'),
  ])
  entries.push([
    'frontend/index.html',
    fs.readFileSync(path.join(repoRoot, 'frontend', 'index.html'), 'utf8'),
  ])
  return new Map(entries)
}

try {
  const contract = assertStage8bTypographyContract(sourceEntries(), productSourceEntries())
  console.log(
    `PASS Stage 8B bounded typography (${contract.eligibility}; ${contract.legacyBaseClass} retained for FULL/MIXED; ${contract.scopedClass})`,
  )
} catch (error) {
  console.error(
    `FAIL Stage 8B bounded typography: ${error instanceof Error ? error.message : String(error)}`,
  )
  process.exitCode = 1
}
