import { describe, expect, it } from 'vitest'
import {
  isActiveLifecycleVisible,
  isFinalTailPhase,
  isOvertimeMarkerAnimated,
  isOvertimePhase,
  showOvertimeMarker,
  timerDeadlineTs,
} from './offerLifecycle'

describe('offerLifecycle helpers', () => {
  it('keeps final-tail active offers visible after expires_at_ts', () => {
    const nowSec = 1_000_000
    expect(
      isActiveLifecycleVisible(
        {
          status: 'active',
          lifecycle_phase: 'final_tail',
          expires_at_ts: nowSec - 10,
          accepts_new_public_interaction: false,
        },
        nowSec,
      ),
    ).toBe(true)
    expect(
      isActiveLifecycleVisible(
        {
          status: 'active',
          lifecycle_phase: 'normal',
          expires_at_ts: nowSec - 10,
        },
        nowSec,
      ),
    ).toBe(false)
  })

  it('shows animated marker only in overtime; static in final-tail and committed history', () => {
    expect(showOvertimeMarker({ lifecycle_phase: 'overtime' })).toBe(true)
    expect(isOvertimeMarkerAnimated({ lifecycle_phase: 'overtime' })).toBe(true)
    expect(isOvertimePhase({ lifecycle_phase: 'overtime' })).toBe(true)

    expect(showOvertimeMarker({ lifecycle_phase: 'final_tail' })).toBe(true)
    expect(isOvertimeMarkerAnimated({ lifecycle_phase: 'final_tail' })).toBe(false)
    expect(isFinalTailPhase({ lifecycle_phase: 'final_tail' })).toBe(true)

    expect(
      showOvertimeMarker({
        status: 'completed',
        history_state: 'traded',
        overtime_trade_committed: true,
      }),
    ).toBe(true)
    expect(
      showOvertimeMarker({
        status: 'expired',
        history_state: 'expired',
        overtime_trade_committed: false,
      }),
    ).toBe(false)
  })

  it('picks normal vs final deadline for the timer ring', () => {
    expect(
      timerDeadlineTs({
        lifecycle_phase: 'normal',
        normal_deadline_ts: 100,
        final_deadline_ts: 200,
        expires_at_ts: 200,
      }),
    ).toBe(100)
    expect(
      timerDeadlineTs({
        lifecycle_phase: 'overtime',
        normal_deadline_ts: 100,
        final_deadline_ts: 200,
        expires_at_ts: 200,
      }),
    ).toBe(200)
    expect(timerDeadlineTs({ lifecycle_phase: 'final_tail', expires_at_ts: 200 })).toBeNull()
  })
})
