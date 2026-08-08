import { computed, ref, shallowRef, type ShallowRef } from 'vue'
import { AppHttpError } from '../utils/httpErrorPolicy'

export type AsyncResourceStatus =
  | 'idle'
  | 'loading'
  | 'ready'
  | 'empty'
  | 'error'
  | 'offline'
  | 'stale'
  | 'reconnecting'

export type AsyncResourceLoadReason = 'initial' | 'refresh' | 'retry' | 'reconnect'

export interface AsyncResourceLoadContext {
  signal: AbortSignal
  requestId: number
  reason: AsyncResourceLoadReason
}

export type AsyncResourceLoader<T> = (context: AsyncResourceLoadContext) => Promise<T>

export interface AsyncResourceOptions<T> {
  initialData?: T
  isEmpty?: (value: T) => boolean
  isOfflineError?: (error: unknown) => boolean
}

function defaultIsEmpty(value: unknown) {
  return value === null
    || value === undefined
    || (Array.isArray(value) && value.length === 0)
}

export function isAsyncResourceOfflineError(error: unknown) {
  if (typeof navigator !== 'undefined' && navigator.onLine === false) return true
  return error instanceof AppHttpError && error.errorCode === 'NETWORK_ERROR'
}

function isAbortError(error: unknown) {
  return error instanceof DOMException
    ? error.name === 'AbortError'
    : error instanceof Error && error.name === 'AbortError'
}

export function useAsyncResource<T>(
  loader: AsyncResourceLoader<T>,
  options: AsyncResourceOptions<T> = {},
) {
  const hasInitialData = Object.prototype.hasOwnProperty.call(options, 'initialData')
  const data = shallowRef<T | null>(hasInitialData ? options.initialData ?? null : null) as ShallowRef<T | null>
  const error = shallowRef<unknown | null>(null)
  const hasResolvedData = ref(hasInitialData)
  const lastSucceededAt = ref<number | null>(null)
  const pending = ref(false)
  const isEmpty = options.isEmpty || defaultIsEmpty
  const isOfflineError = options.isOfflineError || isAsyncResourceOfflineError

  const initialStatus: AsyncResourceStatus = hasInitialData
    ? (isEmpty(options.initialData as T) ? 'empty' : 'ready')
    : 'idle'
  const status = ref<AsyncResourceStatus>(initialStatus)

  let requestSequence = 0
  let activeController: AbortController | null = null
  let settledStatus: AsyncResourceStatus = initialStatus

  const hasData = computed(() => hasResolvedData.value && !isEmpty(data.value as T))
  const isBusy = computed(() => pending.value)

  async function execute(reason: AsyncResourceLoadReason = 'initial'): Promise<T | null> {
    const requestId = ++requestSequence
    activeController?.abort(new DOMException('Superseded by a newer resource request', 'AbortError'))

    const controller = new AbortController()
    activeController = controller
    pending.value = true
    error.value = null

    if (reason === 'reconnect') {
      status.value = 'reconnecting'
    } else if (hasResolvedData.value) {
      status.value = 'stale'
    } else {
      status.value = 'loading'
    }

    try {
      const value = await loader({ signal: controller.signal, requestId, reason })
      if (requestId !== requestSequence) return null

      data.value = value
      hasResolvedData.value = true
      lastSucceededAt.value = Date.now()
      status.value = isEmpty(value) ? 'empty' : 'ready'
      settledStatus = status.value
      return value
    } catch (caught) {
      if (requestId !== requestSequence) return null
      if (controller.signal.aborted && isAbortError(caught)) return null

      error.value = caught
      status.value = isOfflineError(caught)
        ? 'offline'
        : hasResolvedData.value
          ? 'stale'
          : 'error'
      settledStatus = status.value
      return null
    } finally {
      if (requestId === requestSequence) {
        pending.value = false
        activeController = null
      }
    }
  }

  function refresh() {
    return execute('refresh')
  }

  function retry() {
    return execute(status.value === 'offline' ? 'reconnect' : 'retry')
  }

  function reconnect() {
    return execute('reconnect')
  }

  function markStale() {
    if (!hasResolvedData.value || pending.value) return
    status.value = 'stale'
    settledStatus = 'stale'
  }

  function cancel() {
    if (!activeController) return
    requestSequence += 1
    activeController.abort(new DOMException('Resource request cancelled', 'AbortError'))
    activeController = null
    pending.value = false
    status.value = settledStatus
  }

  return {
    data,
    error,
    status,
    hasData,
    hasResolvedData: computed(() => hasResolvedData.value),
    lastSucceededAt: computed(() => lastSucceededAt.value),
    isBusy,
    execute,
    refresh,
    retry,
    reconnect,
    markStale,
    cancel,
  }
}
