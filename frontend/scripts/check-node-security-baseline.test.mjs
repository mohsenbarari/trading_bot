import { describe, expect, it } from 'vitest'
import { isSupportedSecureNode } from './check-node-security-baseline.mjs'

describe('Node security baseline', () => {
  it.each(['20.19.5', '20.99.0', '22.23.3', '22.22.0', '23.10.0', '24.22.0', '25.9.0', '26.12.0', '26.11.99', '27.0.0', '28.0.0', '24.22.1-rc.1', '', 'not-a-version'])('rejects %s', (version) => {
    expect(isSupportedSecureNode(version)).toBe(false)
  })

  it.each(['22.23.4', '22.24.0', '24.22.1', '24.23.0', '26.12.1', '26.13.0'])('accepts %s', (version) => {
    expect(isSupportedSecureNode(version)).toBe(true)
  })
})
