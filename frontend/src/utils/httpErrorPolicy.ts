export type ErrorSurface = 'app' | 'auth' | 'market' | 'messenger' | 'public-profile' | 'admin' | 'settings' | 'generic'
export type ErrorScope = 'app' | 'page' | 'panel' | 'list' | 'action' | 'form' | 'field' | 'item'
export type ErrorOperation =
    | 'initial-load'
    | 'background-refresh'
    | 'load-list'
    | 'load-detail'
    | 'submit'
    | 'send-message'
    | 'download'
    | 'upload'
    | 'delete'
    | 'update'
    | 'realtime-sync'
    | 'unknown'

export type ErrorPresentationKind =
    | 'redirect-login'
    | 'page-error'
    | 'panel-error'
    | 'inline-error'
    | 'toast-error'
    | 'item-error'
    | 'silent-retry'

export interface ErrorPolicyContext {
    surface?: ErrorSurface
    scope?: ErrorScope
    operation?: ErrorOperation
    preserveExistingData?: boolean
    userInitiated?: boolean
    expected404?: boolean
    resourceLabel?: string
    fallbackMessage?: string
}

export interface ErrorPresentation {
    kind: ErrorPresentationKind
    title: string
    message: string
    retry: boolean
    preserveData: boolean
}

type ErrorPayload = Record<string, unknown>

export class AppHttpError extends Error {
    status: number | null
    detail: string
    errorCode: string | null
    payload: ErrorPayload | null
    context: ErrorPolicyContext
    presentation: ErrorPresentation

    constructor(options: {
        status?: number | null
        detail?: string
        errorCode?: string | null
        payload?: ErrorPayload | null
        context?: ErrorPolicyContext
        presentation?: ErrorPresentation
    }) {
        const context = options.context || {}
        const detail = options.detail || context.fallbackMessage || 'خطایی رخ داد.'
        const presentation = options.presentation || buildErrorPresentation({
            status: options.status ?? null,
            detail,
            errorCode: options.errorCode || null,
            context,
        })

        super(presentation.message)
        this.name = 'AppHttpError'
        this.status = options.status ?? null
        this.detail = detail
        this.errorCode = options.errorCode || null
        this.payload = options.payload || null
        this.context = context
        this.presentation = presentation
    }
}

function asString(value: unknown): string | null {
    return typeof value === 'string' && value.trim() ? value.trim() : null
}

function readPayloadDetail(payload: ErrorPayload | null): string | null {
    if (!payload) return null
    const detail = payload.detail
    if (typeof detail === 'string') return detail
    if (Array.isArray(detail) && detail.length > 0) return 'اطلاعات واردشده معتبر نیست.'
    return asString(payload.message) || asString(payload.error)
}

function readPayloadErrorCode(payload: ErrorPayload | null): string | null {
    if (!payload) return null
    return asString(payload.error_code) || asString(payload.code) || null
}

async function parseErrorPayload(response: Response): Promise<{ payload: ErrorPayload | null; detail: string | null }> {
    const contentType = response.headers?.get?.('content-type') || ''
    if (!contentType || contentType.includes('application/json')) {
        const jsonClone = typeof response.clone === 'function' ? response.clone() : response
        const payload = typeof jsonClone.json === 'function' ? await jsonClone.json().catch(() => null) : null
        if (payload && typeof payload === 'object') {
            return { payload: payload as ErrorPayload, detail: readPayloadDetail(payload as ErrorPayload) }
        }
    }

    const textClone = typeof response.clone === 'function' ? response.clone() : response
    const text = typeof textClone.text === 'function' ? await textClone.text().catch(() => '') : ''
    return { payload: null, detail: text.trim() || null }
}

function isAccountLevelForbidden(detail: string, errorCode: string | null) {
    return errorCode === 'ACCOUNT_INACTIVE'
        || errorCode === 'INACTIVE_ACCOUNT_BLOCKED'
        || detail === 'حساب کاربری غیرفعال شده است'
        || detail === 'User is blocked'
}

function titleForKind(kind: ErrorPresentationKind) {
    if (kind === 'redirect-login') return 'نیاز به ورود مجدد'
    if (kind === 'page-error') return 'دسترسی به این صفحه ممکن نیست'
    if (kind === 'panel-error') return 'دریافت اطلاعات ممکن نشد'
    if (kind === 'item-error') return 'این مورد در دسترس نیست'
    if (kind === 'silent-retry') return 'ارتباط ناپایدار است'
    return 'عملیات انجام نشد'
}

function resolve404Message(context: ErrorPolicyContext, detail: string) {
    if (context.fallbackMessage) return context.fallbackMessage
    if (context.scope === 'page') return context.resourceLabel ? `${context.resourceLabel} پیدا نشد.` : 'صفحه یا داده موردنظر پیدا نشد.'
    if (context.scope === 'item') return context.resourceLabel ? `${context.resourceLabel} در دسترس نیست.` : 'این مورد دیگر در دسترس نیست.'
    if (context.scope === 'list' || context.scope === 'panel') return 'دریافت اطلاعات این بخش ممکن نشد.'
    return detail || 'مورد موردنظر پیدا نشد.'
}

export function buildErrorPresentation(input: {
    status: number | null
    detail: string
    errorCode?: string | null
    context?: ErrorPolicyContext
}): ErrorPresentation {
    const context = input.context || {}
    const status = input.status
    const detail = input.detail
    const errorCode = input.errorCode || null
    const preserveData = Boolean(context.preserveExistingData || context.operation === 'background-refresh')
    let kind: ErrorPresentationKind = 'toast-error'
    let message = detail || context.fallbackMessage || 'خطایی رخ داد.'
    let retry = false

    if (status === 401) {
        kind = 'redirect-login'
        message = 'نشست شما منقضی شده است. لطفا مجددا وارد شوید.'
    } else if (status === 403 && isAccountLevelForbidden(detail, errorCode)) {
        kind = 'redirect-login'
        message = 'حساب کاربری شما غیرفعال شده است.'
    } else if (status === 403) {
        kind = context.scope === 'page' ? 'page-error' : 'inline-error'
        message = context.fallbackMessage || detail || 'شما به این بخش دسترسی ندارید.'
    } else if (status === 404) {
        if (context.scope === 'page') kind = 'page-error'
        else if (context.scope === 'item') kind = 'item-error'
        else if (context.scope === 'panel' || context.scope === 'list') kind = 'panel-error'
        else kind = 'inline-error'
        message = resolve404Message(context, detail)
        retry = context.scope === 'panel' || context.scope === 'list'
    } else if (status === 409 || status === 400 || status === 422) {
        kind = context.scope === 'field' || context.scope === 'form' || context.scope === 'action' ? 'inline-error' : 'toast-error'
        message = detail || context.fallbackMessage || 'درخواست معتبر نیست.'
    } else if (status === 429) {
        kind = 'inline-error'
        message = context.fallbackMessage || detail || 'تعداد درخواست‌ها زیاد است. کمی بعد دوباره تلاش کنید.'
        retry = true
    } else if (status !== null && status >= 500) {
        if (context.operation === 'background-refresh') kind = 'silent-retry'
        else if (context.scope === 'page') kind = 'page-error'
        else if (context.scope === 'panel' || context.scope === 'list') kind = 'panel-error'
        else kind = 'toast-error'
        message = context.fallbackMessage || 'اختلال موقت در سرور. لطفا دوباره تلاش کنید.'
        retry = true
    } else if (context.operation === 'background-refresh') {
        kind = 'silent-retry'
        message = context.fallbackMessage || 'ارتباط ناپایدار است. در حال تلاش مجدد...'
        retry = true
    } else if (context.scope === 'page') {
        kind = 'page-error'
        retry = true
    } else if (context.scope === 'panel' || context.scope === 'list') {
        kind = 'panel-error'
        message = context.fallbackMessage || 'دریافت اطلاعات این بخش ممکن نشد.'
        retry = true
    } else if (context.scope === 'item') {
        kind = 'item-error'
    } else {
        kind = 'toast-error'
    }

    return {
        kind,
        title: titleForKind(kind),
        message,
        retry,
        preserveData,
    }
}

export async function createHttpErrorFromResponse(
    response: Response,
    context: ErrorPolicyContext = {},
    knownPayload?: ErrorPayload | null,
): Promise<AppHttpError> {
    const parsed = knownPayload === undefined
        ? await parseErrorPayload(response)
        : { payload: knownPayload, detail: readPayloadDetail(knownPayload) }

    const payload = parsed.payload
    const detail = parsed.detail || context.fallbackMessage || response.statusText || `خطا: ${response.status}`
    const errorCode = readPayloadErrorCode(payload)
    const presentation = buildErrorPresentation({ status: response.status, detail, errorCode, context })

    return new AppHttpError({
        status: response.status,
        detail,
        errorCode,
        payload,
        context,
        presentation,
    })
}

export function isAppHttpError(error: unknown): error is AppHttpError {
    return error instanceof AppHttpError
}

export function normalizeErrorPresentation(error: unknown, context: ErrorPolicyContext = {}): ErrorPresentation {
    if (isAppHttpError(error)) {
        if (Object.keys(context).length === 0) return error.presentation
        return buildErrorPresentation({
            status: error.status,
            detail: error.detail,
            errorCode: error.errorCode,
            context: { ...error.context, ...context },
        })
    }

    const detail = error instanceof Error ? error.message : String(error || '')
    return buildErrorPresentation({ status: null, detail: detail || 'خطایی رخ داد.', context })
}

export function getUserFacingErrorMessage(error: unknown, context: ErrorPolicyContext = {}) {
    return normalizeErrorPresentation(error, context).message
}
