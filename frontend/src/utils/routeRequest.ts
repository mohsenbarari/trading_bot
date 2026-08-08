import { apiFetch } from './auth'
import {
  AppHttpError,
  createHttpErrorFromResponse,
  type ErrorPolicyContext,
} from './httpErrorPolicy'

export const DEFAULT_ROUTE_REQUEST_TIMEOUT_MS = 15_000

export type RouteRequestMode = 'authenticated' | 'public'

export interface RouteRequestOptions extends Omit<RequestInit, 'mode' | 'signal'> {
  mode?: RouteRequestMode
  requestMode?: RequestMode
  signal?: AbortSignal
  timeoutMs?: number | null
  errorContext?: ErrorPolicyContext
}

function resolvePublicUrl(url: string) {
  const baseUrl = import.meta.env.VITE_API_BASE_URL || ''
  return url.startsWith('http') ? url : `${baseUrl}${url}`
}

function isNetworkFailure(error: unknown) {
  if (error instanceof TypeError) return true
  if (!(error instanceof Error)) return false

  const message = error.message.toLowerCase()
  return message.includes('failed to fetch')
    || message.includes('network')
    || message.includes('load failed')
    || message.includes('خطا در ارتباط با سرور')
}

function transportError(
  errorCode: 'NETWORK_ERROR' | 'REQUEST_TIMEOUT',
  context: ErrorPolicyContext,
) {
  const detail = errorCode === 'REQUEST_TIMEOUT'
    ? 'زمان انتظار برای دریافت پاسخ به پایان رسید.'
    : 'ارتباط با سرور برقرار نشد.'

  return new AppHttpError({
    status: null,
    errorCode,
    detail,
    context: {
      ...context,
      fallbackMessage: context.fallbackMessage || detail,
    },
  })
}

/**
 * Runs one bounded route-level request.
 *
 * Authenticated calls deliberately opt out of apiFetch's unbounded network
 * retry loop. A UI retry therefore always starts a fresh call controlled by
 * the caller instead of mutating the app-wide connection indicator.
 */
export async function routeRequest(
  url: string,
  options: RouteRequestOptions = {},
): Promise<Response> {
  const {
    mode = 'authenticated',
    requestMode,
    signal: externalSignal,
    timeoutMs = DEFAULT_ROUTE_REQUEST_TIMEOUT_MS,
    errorContext = {},
    ...requestInit
  } = options

  const controller = new AbortController()
  let timedOut = false
  let timeoutId: ReturnType<typeof setTimeout> | null = null

  const forwardAbort = () => controller.abort(externalSignal?.reason)
  if (externalSignal?.aborted) {
    forwardAbort()
  } else {
    externalSignal?.addEventListener('abort', forwardAbort, { once: true })
  }

  if (typeof timeoutMs === 'number' && Number.isFinite(timeoutMs) && timeoutMs > 0) {
    timeoutId = setTimeout(() => {
      timedOut = true
      controller.abort(new DOMException('Route request timed out', 'TimeoutError'))
    }, timeoutMs)
  }

  const fetchOptions: RequestInit = {
    ...requestInit,
    ...(requestMode ? { mode: requestMode } : {}),
    signal: controller.signal,
  }

  try {
    const response = mode === 'public'
      ? await fetch(resolvePublicUrl(url), fetchOptions)
      : await apiFetch(url, {
          ...fetchOptions,
          retryNetwork: false,
          trackConnectionState: false,
        })

    if (!response.ok) {
      throw await createHttpErrorFromResponse(response, errorContext)
    }

    return response
  } catch (error) {
    if (error instanceof AppHttpError) throw error
    if (timedOut) throw transportError('REQUEST_TIMEOUT', errorContext)
    if (externalSignal?.aborted) throw error
    if (isNetworkFailure(error)) throw transportError('NETWORK_ERROR', errorContext)
    throw error
  } finally {
    if (timeoutId !== null) clearTimeout(timeoutId)
    externalSignal?.removeEventListener('abort', forwardAbort)
  }
}

export async function routeRequestJson<T>(
  url: string,
  options: RouteRequestOptions = {},
): Promise<T> {
  const response = await routeRequest(url, options)
  if (response.status === 204) return null as T
  return response.json() as Promise<T>
}
