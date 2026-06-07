/**
 * useBackButton — مدیریت دکمه بازگشت مرورگر / تلگرام
 *
 * هر view که navigation داخلی دارد (مودال، ویزارد، ساب‌پیج)
 * می‌تواند state خود را push کند. وقتی کاربر دکمه back می‌زند
 * آخرین state عقب رفته و callback اجرا می‌شود.
 *
 * اگر هیچ state داخلی‌ای نمانده، back پیش‌فرض مرورگر اجرا می‌شود.
 */

import { onMounted, onUnmounted } from 'vue'

type BackHandler = () => void

// پشته سراسری — هر ورودی یک callback بازگشت است
const backStack: BackHandler[] = []

let isListening = false
let isTelegramListening = false
let ignoreNextPopState = false
let ignoredPopStateCallback: BackHandler | null = null
let ignoredPopStateFallbackTimer: ReturnType<typeof window.setTimeout> | null = null
let telegramInitInterval: any = null

function handlePopState() {
  if (ignoreNextPopState) {
    ignoreNextPopState = false
    if (ignoredPopStateFallbackTimer !== null) {
      window.clearTimeout(ignoredPopStateFallbackTimer)
      ignoredPopStateFallbackTimer = null
    }
    const callback = ignoredPopStateCallback
    ignoredPopStateCallback = null
    callback?.()
    return
  }
  if (backStack.length > 0) {
    const handler = backStack.pop()!
    handler()

    // Telegram BackButton — اگر پشته خالی شد مخفی کن
    updateTelegramBackButton()
  }
  // اگر پشته خالی بود مرورگر خودش navigate back می‌کند
}

function updateTelegramBackButton() {
  const tg = (window as any).Telegram?.WebApp
  if (!tg?.BackButton) return
  if (backStack.length > 0) {
    tg.BackButton.show()
  } else {
    tg.BackButton.hide()
  }
}

function handleTelegramBackClick() {
  window.history.back()
}

function initTelegramBackButton() {
  const tg = (window as any).Telegram?.WebApp
  if (tg?.BackButton) {
    if (!isTelegramListening) {
      isTelegramListening = true
      tg.BackButton.onClick(handleTelegramBackClick)
      if (telegramInitInterval) {
        clearInterval(telegramInitInterval)
        telegramInitInterval = null
      }
    }
    updateTelegramBackButton()
  }
}

function startListening() {
  if (!isListening) {
    isListening = true
    window.addEventListener('popstate', handlePopState)
  }

  initTelegramBackButton()

  if (!isTelegramListening && !telegramInitInterval) {
    telegramInitInterval = setInterval(initTelegramBackButton, 100)
  }
}

/**
 * یک state داخلی push کن (مثلاً باز شدن مودال یا ورود به ساب‌پیج)
 * @param onBack — وقتی back زده شد چه کاری انجام شود
 */
export function pushBackState(onBack: BackHandler) {
  startListening()
  backStack.push(onBack)

  // یک state خالی به history اضافه کن تا popstate کار کند اما state مربوط به روتر Vue پاک نشود
  const currentState = history.state || {}
  history.pushState(Object.assign({}, currentState, { backStack: backStack.length }), '')

  updateTelegramBackButton()
}

/**
 * حذف آخرین state بدون trigger کردن callback
 * (مثلاً وقتی کاربر خودش دکمه UI رو زد و مودال بسته شد)
 */
export function popBackState() {
  if (backStack.length > 0) {
    backStack.pop()
    ignoreNextPopState = true
    // history entry اضافی را بردار
    history.back()
    updateTelegramBackButton()
    return true
  }
  return false
}

export function popBackStateAfterHistory(onAfterHistoryBack: BackHandler) {
  const didPop = popBackState()
  if (!didPop) {
    onAfterHistoryBack()
    return false
  }

  ignoredPopStateCallback = onAfterHistoryBack
  if (ignoredPopStateFallbackTimer !== null) {
    window.clearTimeout(ignoredPopStateFallbackTimer)
  }
  ignoredPopStateFallbackTimer = window.setTimeout(() => {
    if (!ignoreNextPopState || ignoredPopStateCallback !== onAfterHistoryBack) {
      return
    }
    ignoreNextPopState = false
    ignoredPopStateCallback = null
    ignoredPopStateFallbackTimer = null
    onAfterHistoryBack()
  }, 120)
  return true
}

/**
 * حذف آخرین state بدون دست زدن به history
 * برای actionهایی که خودشان بلافاصله route/ui دیگری را باز می‌کنند
 */
export function discardBackState() {
  if (backStack.length > 0) {
    backStack.pop()
    updateTelegramBackButton()
  }
}

/**
 * پاک کردن همه stateهای مربوط به view فعلی
 * (مثلاً وقتی از یک route خارج می‌شویم)
 * history entries اضافی را نمی‌بردارد چون Vue Router خودش history را مدیریت می‌کند
 * و history.go() باعث بهم ریختن navigation می‌شود
 */
export function clearBackStack() {
  backStack.length = 0
  ignoreNextPopState = false
  ignoredPopStateCallback = null
  if (ignoredPopStateFallbackTimer !== null) {
    window.clearTimeout(ignoredPopStateFallbackTimer)
    ignoredPopStateFallbackTimer = null
  }
  if (telegramInitInterval) {
    clearInterval(telegramInitInterval)
    telegramInitInterval = null
  }
  const tg = (window as any).Telegram?.WebApp
  if (tg?.BackButton && isTelegramListening) {
    tg.BackButton.offClick(handleTelegramBackClick)
  }
  isTelegramListening = false
  updateTelegramBackButton()
}

/**
 * Composable برای cleanup خودکار وقتی کامپوننت unmount می‌شود
 */
export function useBackButton() {
  onMounted(() => {
    startListening()
  })

  onUnmounted(() => {
    // فقط stateهای این کامپوننت را پاک کن — نمی‌خواهیم listener سراسری را حذف کنیم
    // چون ممکن است view دیگری هم ازش استفاده کند
  })

  return { pushBackState, popBackState, clearBackStack }
}
