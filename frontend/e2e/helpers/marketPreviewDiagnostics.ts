import type { Page, Request, Response } from '@playwright/test'

const SCHEMA = 'market_preview_network_observation/1' as const
const MAX_BUCKETS = 16
const MAX_COUNT = 4096
const selectedTests = new Map([
  ['market page stays text-only and persists a new buy offer from text input', 'buy_text'],
  ['text offer submission parses and persists a new sell offer', 'sell_text'],
  ['tier1 customer can publish a sell offer while owner and tier2 viewers see raw versus projected prices', 'tier1_sell'],
  ['sell outlier warning requires a second confirmation and persists exclusion flags', 'sell_warning'],
  ['buy outlier warning requires a second confirmation and persists exclusion flags', 'buy_warning'],
])

type Endpoint = 'offer_parse' | 'offer_publish'
type Row = {
  endpoint: Endpoint
  method: 'POST'
  event: 'response' | 'requestfailed'
  status: number | null
  code: 'REQUEST_FAILED' | null
  count: number
}
type Report = {
  schema: typeof SCHEMA
  scope: string
  endpoint_scope: 'FIXED_PATH_POST_ONLY'
  rows: Row[]
  matching_events_seen: number
  seen_count_capped: boolean
  dropped_count_capped: boolean
  overflow: boolean
  dropped_matching_events: number
  read_failed: boolean
  setup_failed: boolean
  detach_failed: boolean
  observation_incomplete: boolean
  diagnostic_only: true
  security_accepted: false
}

export function observeMarketPreviewRequests(page: Pick<Page, 'on' | 'off'>, testTitle: string) {
  const scope = selectedTests.get(testTitle)
  if (!scope) return null
  const buckets = new Map<string, Row>()
  let seen = 0, dropped = 0
  let seenCapped = false, droppedCapped = false, overflow = false, readFailed = false, setupFailed = false, detachFailed = false
  let finished = false
  let finalReport: Report | null = null
  function endpoint(request: Request): Endpoint | null {
    if (request.method() !== 'POST') return null
    const value = request.url()
    if (typeof value !== 'string' || value.length > 8192) throw new Error('DIAGNOSTIC_URL_SHAPE')
    const path = new URL(value).pathname
    return path === '/api/offers/parse' ? 'offer_parse' : path === '/api/offers/' ? 'offer_publish' : null
  }
  function drop() {
    overflow = true
    if (dropped < MAX_COUNT) dropped += 1
    else droppedCapped = true
  }
  function record(row: Omit<Row, 'count'>) {
    if (seen < MAX_COUNT) seen += 1
    else { seenCapped = true; overflow = true }
    const key = `${row.endpoint}:${row.event}:${row.status}`
    const previous = buckets.get(key)
    if (previous) {
      if (previous.count < MAX_COUNT) previous.count += 1
      else drop()
    } else if (buckets.size < MAX_BUCKETS) buckets.set(key, { ...row, count: 1 })
    else drop()
  }
  function onResponse(response: Response) {
    if (finished) return
    try {
      const name = endpoint(response.request())
      if (!name) return
      const status = response.status()
      if (!Number.isInteger(status) || status < 100 || status > 599) throw new Error('DIAGNOSTIC_STATUS_SHAPE')
      record({ endpoint: name, method: 'POST', event: 'response', status, code: null })
    } catch { readFailed = true }
  }
  function onRequestFailed(request: Request) {
    if (finished) return
    try {
      const name = endpoint(request)
      if (name) record({ endpoint: name, method: 'POST', event: 'requestfailed', status: null, code: 'REQUEST_FAILED' })
    } catch { readFailed = true }
  }
  try {
    page.on('response', onResponse)
    page.on('requestfailed', onRequestFailed)
  } catch { setupFailed = true }
  return {
    finish(): Report {
      if (finalReport) return finalReport
      finished = true
      try { page.off('response', onResponse) } catch { detachFailed = true }
      try { page.off('requestfailed', onRequestFailed) } catch { detachFailed = true }
      finalReport = {
        schema: SCHEMA, scope, endpoint_scope: 'FIXED_PATH_POST_ONLY',
        rows: [...buckets.values()].map((row) => ({ ...row })), matching_events_seen: seen,
        seen_count_capped: seenCapped, dropped_count_capped: droppedCapped, overflow, dropped_matching_events: dropped,
        read_failed: readFailed, setup_failed: setupFailed, detach_failed: detachFailed,
        observation_incomplete: readFailed || setupFailed || detachFailed || overflow,
        diagnostic_only: true, security_accepted: false,
      }
      return finalReport
    },
  }
}
