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

function handlePopState() {
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

function startListening() {
  if (isListening) return
  isListening = true
  window.addEventListener('popstate', handlePopState)

  // Telegram BackButton
  const tg = (window as any).Telegram?.WebApp
  if (tg?.BackButton) {
    tg.BackButton.onClick(handlePopState)
  }
}

/**
 * یک state داخلی push کن (مثلاً باز شدن مودال یا ورود به ساب‌پیج)
 * @param onBack — وقتی back زده شد چه کاری انجام شود
 */
export function pushBackState(onBack: BackHandler) {
  startListening()
  backStack.push(onBack)

  // یک state خالی به history اضافه کن تا popstate کار کند
  history.pushState({ backStack: backStack.length }, '')

  updateTelegramBackButton()
}

/**
 * حذف آخرین state بدون trigger کردن callback
 * (مثلاً وقتی کاربر خودش دکمه UI رو زد و مودال بسته شد)
 */
export function popBackState() {
  if (backStack.length > 0) {
    backStack.pop()
    // history entry اضافی را بردار
    history.back()
    updateTelegramBackButton()
  }
}

/**
 * پاک کردن همه stateهای مربوط به view فعلی
 * (مثلاً وقتی از یک route خارج می‌شویم)
 */
export function clearBackStack() {
  const count = backStack.length
  backStack.length = 0
  // history entries اضافی را بردار
  if (count > 0) {
    history.go(-count)
  }
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
