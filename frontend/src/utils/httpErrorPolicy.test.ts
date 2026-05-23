import { describe, expect, it } from 'vitest'
import { AppHttpError, buildErrorPresentation, createHttpErrorFromResponse, getUserFacingErrorMessage } from './httpErrorPolicy'

function jsonResponse(payload: unknown, status: number) {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { 'Content-Type': 'application/json' },
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
})
