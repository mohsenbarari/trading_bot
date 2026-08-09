/**
 * Vue Router resolves aborted, cancelled, and duplicated navigations with a
 * NavigationFailure object. Cleanup that depends on a completed transition
 * must therefore reject every non-null resolved result, not only exceptions.
 */
export function assertSuccessfulNavigation(result: unknown): asserts result is null | undefined {
  if (result !== null && result !== undefined) {
    throw new Error('Navigation did not complete.')
  }
}
