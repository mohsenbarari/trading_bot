export type LocalLogoutOutcome = 'server-confirmed' | 'local-only'

const LOCAL_LOGOUT_RECEIPT_KEY = 'stage4_local_logout_result_v1'

export function storeLocalLogoutReceipt(outcome: LocalLogoutOutcome): void {
  try {
    sessionStorage.setItem(LOCAL_LOGOUT_RECEIPT_KEY, outcome)
  } catch {
    // The logout itself must continue when session storage is unavailable.
  }
}

export function consumeLocalLogoutReceipt(): LocalLogoutOutcome | null {
  try {
    const value = sessionStorage.getItem(LOCAL_LOGOUT_RECEIPT_KEY)
    sessionStorage.removeItem(LOCAL_LOGOUT_RECEIPT_KEY)
    return value === 'server-confirmed' || value === 'local-only' ? value : null
  } catch {
    return null
  }
}
