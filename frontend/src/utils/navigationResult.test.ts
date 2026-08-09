import { describe, expect, it } from 'vitest'
import { assertSuccessfulNavigation } from './navigationResult'

describe('assertSuccessfulNavigation', () => {
  it('accepts the successful Vue Router result', () => {
    expect(() => assertSuccessfulNavigation(undefined)).not.toThrow()
    expect(() => assertSuccessfulNavigation(null)).not.toThrow()
  })

  it('rejects a resolved NavigationFailure-like result', () => {
    expect(() => assertSuccessfulNavigation({ type: 4, to: '/blocked' })).toThrow(
      'Navigation did not complete.',
    )
  })
})
