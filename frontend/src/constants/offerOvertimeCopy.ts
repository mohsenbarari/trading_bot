/**
 * Approved inventory strings for offer overtime (وقت اضافه).
 * Exact wording from the planning inventory — do not paraphrase.
 */

export const OVERTIME_MIN_MINUTES = 0
export const OVERTIME_MAX_MINUTES = 10

/** M2 current-value line (shared inventory wording) */
export const M2_CURRENT_TEMPLATE = 'وقت اضافه لفظ‌های جدید شما: {current_value}'

/** M3 */
export const M3_ZERO_DISPLAY = 'غیرفعال'

/** M4 — `{minutes}` is the saved whole number */
export const M4_SAVE_SUCCESS_NONZERO = '✅ وقت اضافه لفظ‌های جدید شما روی {minutes} دقیقه تنظیم شد.'

/** M5 */
export const M5_SAVE_SUCCESS_ZERO = '✅ وقت اضافه برای لفظ‌های جدید شما غیرفعال شد.'

/** M6 */
export const M6_REACHABILITY_WARNING =
  'تأیید هر لفظ فقط در همان محل ثبت لفظ نمایش داده می‌شود: لفظ وب در وب‌اپ و لفظ بات در بات.'

/** M8 */
export const M8_INVALID_VALUE = 'لطفاً فقط یک عدد بین ۰ تا ۱۰ بفرستید.'

/** M9 */
export const M9_LABEL = 'وقت اضافه'
export const M9_HELPER =
  'پس از پایان زمان لفظ، تا این مدت درخواست معامله با تأیید شما پذیرفته می‌شود.'

/** M12 */
export const M12_CANCEL_BUTTON = 'لغو درخواست'

/** M15 */
export const M15_CANCELLED = 'درخواست لغو شد.'

/** M21 */
export const M21_REQUESTER_QUEUED = 'در حال ارسال درخواست...'

/** M22 display helper — pad to mm:ss Persian digits */
export const M22_COUNTDOWN_START = '۰۰:۳۰'

/** M27 — lot-based quantity line */
export const M27_QUANTITY_TEMPLATE = '📦 مقدار درخواستی: {count} عدد'

/** M35 */
export const M35_OWNER_TITLE = 'درخواست معامله در وقت اضافه'

/** M36 */
export const M36_OWNER_APPROVE = 'تأیید معامله'
export const M36_OWNER_REJECT = 'رد درخواست'

/** M37 */
export const M37_REVALIDATION_FAILED = 'شرایط این لفظ تغییر کرده و معامله انجام نشد.'

const PERSIAN_DIGITS = ['۰', '۱', '۲', '۳', '۴', '۵', '۶', '۷', '۸', '۹']

export function toPersianDigits(value: string | number): string {
  return String(value).replace(/\d/g, (digit) => PERSIAN_DIGITS[Number(digit)] ?? digit)
}

/** Format remaining seconds as M22-style `۰۰:۳۰`. */
export function formatOvertimeCountdown(totalSeconds: number): string {
  const safe = Math.max(0, Math.floor(totalSeconds))
  const minutes = Math.floor(safe / 60)
  const seconds = safe % 60
  const raw = `${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`
  return toPersianDigits(raw)
}

export function formatSaveSuccessDetail(minutes: number): string {
  if (minutes <= 0) return M5_SAVE_SUCCESS_ZERO
  return M4_SAVE_SUCCESS_NONZERO.replace('{minutes}', String(minutes))
}

export function formatCurrentPreferenceLine(minutes: number): string {
  const currentValue = minutes <= 0 ? M3_ZERO_DISPLAY : String(minutes)
  return M2_CURRENT_TEMPLATE.replace('{current_value}', currentValue)
}

export function formatQuantityLine(count: number): string {
  return M27_QUANTITY_TEMPLATE.replace('{count}', toPersianDigits(count))
}
