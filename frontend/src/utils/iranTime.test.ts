import { describe, expect, it } from 'vitest'
import { formatIranDate, formatIranTime } from './iranTime'

describe('iranTime utilities', () => {
  it('formats ISO timestamps without timezone as UTC, then displays them in Iran time', () => {
    const time = formatIranTime(
      '2026-01-01T00:00:00',
      { hour: '2-digit', minute: '2-digit', hourCycle: 'h23' },
      'en-GB',
    )

    expect(time).toBe('03:30')
  })

  it('uses Iran calendar day for date labels regardless of viewer timezone', () => {
    const date = formatIranDate(
      '2026-01-01T21:00:00Z',
      { year: 'numeric', month: '2-digit', day: '2-digit' },
      'en-CA',
    )

    expect(date).toBe('2026-01-02')
  })
})
