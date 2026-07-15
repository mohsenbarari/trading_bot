import { describe, expect, it } from 'vitest'
import {
  AppHttpError,
  buildErrorPresentation,
  createHttpErrorFromResponse,
  getUserFacingErrorMessage,
  normalizeErrorPresentation,
} from './httpErrorPolicy'

function jsonResponse(payload: unknown, status: number) {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

function textResponse(text: string, status: number, contentType = 'text/plain') {
  return new Response(text, {
    status,
    headers: { 'Content-Type': contentType },
  })
}

describe('httpErrorPolicy', () => {
  it('keeps validation details visible for field/form errors', async () => {
    const error = await createHttpErrorFromResponse(jsonResponse({ detail: 'متن نامعتبر است' }, 422), {
      surface: 'market',
      scope: 'field',
      operation: 'submit',
      fallbackMessage: 'خطا در پردازش متن',
    })

    expect(error).toBeInstanceOf(AppHttpError)
    expect(error.presentation.kind).toBe('inline-error')
    expect(error.message).toBe('متن نامعتبر است')
  })

  it('reads machine codes and messages from structured FastAPI details', async () => {
    const error = await createHttpErrorFromResponse(jsonResponse({
      detail: {
        error_code: 'TRADE_CONTENTION_BUSY',
        message: 'این لفظ در حال معامله است.',
      },
    }, 409), {
      surface: 'market',
      scope: 'action',
      operation: 'submit',
    })

    expect(error.errorCode).toBe('TRADE_CONTENTION_BUSY')
    expect(error.detail).toBe('این لفظ در حال معامله است.')
  })

  it('hides unknown technical errors on list and panel surfaces', () => {
    expect(getUserFacingErrorMessage(new Error('socket hang up'), {
      surface: 'messenger',
      scope: 'list',
      operation: 'load-list',
      fallbackMessage: 'دریافت گفتگوها ممکن نشد.',
    })).toBe('دریافت گفتگوها ممکن نشد.')
  })

  it('scopes identical 404 responses differently by UI context', () => {
    const page404 = buildErrorPresentation({
      status: 404,
      detail: 'not found',
      context: { surface: 'public-profile', scope: 'page', resourceLabel: 'پروفایل کاربر' },
    })
    const item404 = buildErrorPresentation({
      status: 404,
      detail: 'not found',
      context: { surface: 'messenger', scope: 'item', resourceLabel: 'فایل' },
    })

    expect(page404.kind).toBe('page-error')
    expect(page404.message).toBe('پروفایل کاربر پیدا نشد.')
    expect(item404.kind).toBe('item-error')
    expect(item404.message).toBe('فایل در دسترس نیست.')
  })

  it('maps auth and account-level forbidden errors to re-login messaging', () => {
    const expired = buildErrorPresentation({
      status: 401,
      detail: 'expired',
      context: { surface: 'auth', scope: 'page', operation: 'initial-load' },
    })
    const inactive = buildErrorPresentation({
      status: 403,
      detail: 'User is blocked',
      errorCode: 'INACTIVE_ACCOUNT_BLOCKED',
      context: { surface: 'market', scope: 'action', operation: 'submit' },
    })

    expect(expired.kind).toBe('redirect-login')
    expect(expired.title).toBe('نیاز به ورود مجدد')
    expect(expired.message).toContain('نشست شما منقضی شده است')
    expect(inactive.kind).toBe('redirect-login')
    expect(inactive.message).toBe('حساب کاربری شما غیرفعال شده است.')
  })

  it('uses scope-aware fallback messages for 403, 404, 429, 500, and background refresh flows', () => {
    const forbiddenPage = buildErrorPresentation({
      status: 403,
      detail: 'denied',
      context: { scope: 'page', fallbackMessage: 'ورود به این صفحه مجاز نیست.' },
    })
    const missingList = buildErrorPresentation({
      status: 404,
      detail: 'missing',
      context: { scope: 'list', preserveExistingData: true },
    })
    const throttled = buildErrorPresentation({
      status: 429,
      detail: '',
      context: { scope: 'action', fallbackMessage: 'بعدا دوباره تلاش کنید.' },
    })
    const panel500 = buildErrorPresentation({
      status: 503,
      detail: 'server error',
      context: { scope: 'panel', operation: 'load-list' },
    })
    const refreshFailure = buildErrorPresentation({
      status: null,
      detail: '',
      context: { operation: 'background-refresh', fallbackMessage: 'در حال تلاش مجدد...' },
    })

    expect(forbiddenPage.kind).toBe('page-error')
    expect(forbiddenPage.message).toBe('ورود به این صفحه مجاز نیست.')
    expect(missingList.kind).toBe('panel-error')
    expect(missingList.retry).toBe(true)
    expect(missingList.preserveData).toBe(true)
    expect(throttled.kind).toBe('inline-error')
    expect(throttled.retry).toBe(true)
    expect(throttled.message).toBe('بعدا دوباره تلاش کنید.')
    expect(panel500.kind).toBe('panel-error')
    expect(panel500.retry).toBe(true)
    expect(panel500.message).toBe('اختلال موقت در سرور. لطفا دوباره تلاش کنید.')
    expect(refreshFailure.kind).toBe('silent-retry')
    expect(refreshFailure.retry).toBe(true)
    expect(refreshFailure.preserveData).toBe(true)
  })

  it('parses array validation details, plain text bodies, and known payload shortcuts from responses', async () => {
    const arrayDetail = await createHttpErrorFromResponse(jsonResponse({ detail: [{ field: 'text' }] }, 422), {
      surface: 'market',
      scope: 'form',
      operation: 'submit',
    })
    const plainText = await createHttpErrorFromResponse(textResponse('درگاه در دسترس نیست', 503), {
      surface: 'messenger',
      scope: 'panel',
      operation: 'load-list',
    })
    const shortcut = await createHttpErrorFromResponse(new Response(null, { status: 404, statusText: 'Not Found' }), {
      scope: 'item',
      resourceLabel: 'فایل',
    }, {
      message: 'فایل حذف شده است',
      code: 'FILE_GONE',
    })

    expect(arrayDetail.detail).toBe('اطلاعات واردشده معتبر نیست.')
    expect(arrayDetail.presentation.kind).toBe('inline-error')
    expect(plainText.detail).toBe('درگاه در دسترس نیست')
    expect(plainText.presentation.kind).toBe('panel-error')
    expect(shortcut.errorCode).toBe('FILE_GONE')
    expect(shortcut.presentation.message).toBe('فایل در دسترس نیست.')
  })

  it('normalizes AppHttpError and generic errors into user-facing presentations', async () => {
    const original = await createHttpErrorFromResponse(jsonResponse({ detail: 'مجاز نیست' }, 403), {
      surface: 'admin',
      scope: 'action',
      operation: 'delete',
    })

    const overridden = normalizeErrorPresentation(original, {
      scope: 'page',
      fallbackMessage: 'دسترسی به این صفحه ممکن نیست.',
    })
    const generic = normalizeErrorPresentation(new Error('socket hang up'), {
      scope: 'panel',
      fallbackMessage: 'بارگذاری تنظیمات ممکن نشد.',
    })

    expect(overridden.kind).toBe('page-error')
    expect(overridden.message).toBe('دسترسی به این صفحه ممکن نیست.')
    expect(getUserFacingErrorMessage(original)).toBe('مجاز نیست')
    expect(generic.kind).toBe('panel-error')
    expect(generic.message).toBe('بارگذاری تنظیمات ممکن نشد.')
  })

  it('covers 404 generic-detail fallback, 500 toast fallback, and item-scope generic fallback', () => {
    const generic404 = buildErrorPresentation({
      status: 404,
      detail: 'raw-404-detail',
      context: { scope: 'action' },
    })
    const serverToast = buildErrorPresentation({
      status: 500,
      detail: 'ignored',
      context: { scope: 'action' },
    })
    const itemFallback = buildErrorPresentation({
      status: null,
      detail: '',
      context: { scope: 'item' },
    })

    expect(generic404.kind).toBe('inline-error')
    expect(generic404.message).toBe('raw-404-detail')
    expect(serverToast.kind).toBe('toast-error')
    expect(serverToast.retry).toBe(true)
    expect(itemFallback.kind).toBe('item-error')
  })

  it('uses custom AppHttpError presentation and payload message/code shortcuts', async () => {
    const customPresentation = {
      kind: 'toast-error' as const,
      title: 'عنوان سفارشی',
      message: 'پیام سفارشی',
      retry: false,
      preserveData: false,
    }
    const custom = new AppHttpError({
      status: 418,
      detail: 'ignored',
      presentation: customPresentation,
    })
    const fromKnownPayload = await createHttpErrorFromResponse(
      new Response(null, { status: 400, statusText: 'Bad Request' }),
      { scope: 'action' },
      { message: 'payload message', code: 'PAYLOAD_CODE' },
    )

    expect(custom.presentation).toEqual(customPresentation)
    expect(custom.message).toBe('پیام سفارشی')
    expect(fromKnownPayload.detail).toBe('payload message')
    expect(fromKnownPayload.errorCode).toBe('PAYLOAD_CODE')
  })
})
