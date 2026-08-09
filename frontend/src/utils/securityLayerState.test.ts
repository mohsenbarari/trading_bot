import { describe, expect, it } from 'vitest'
import {
  isSecurityLayerActive,
  resetSecurityLayerStateForTests,
  setSecurityLayerActive,
} from './securityLayerState'

describe('securityLayerState', () => {
  it('tracks multiple blocking security layers without releasing another owner', () => {
    resetSecurityLayerStateForTests()
    setSecurityLayerActive('session-approval', true)
    setSecurityLayerActive('security-gate', true)
    expect(isSecurityLayerActive.value).toBe(true)

    setSecurityLayerActive('session-approval', false)
    expect(isSecurityLayerActive.value).toBe(true)

    setSecurityLayerActive('security-gate', false)
    expect(isSecurityLayerActive.value).toBe(false)
  })
})
