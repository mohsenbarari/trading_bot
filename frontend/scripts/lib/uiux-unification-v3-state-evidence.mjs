export function assertRequestedState(state, spec, selectorCount) {
  const failures = []
  if (!spec?.selector) {
    failures.push(`${state} descriptor has no visible-state selector`)
    return failures
  }
  if (selectorCount < 1) failures.push(`${state} selector was not visible: ${spec.selector}`)
  return failures
}
