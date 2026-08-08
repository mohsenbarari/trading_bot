import { describe, expect, it, vi } from 'vitest'
import { ActionContractError, useActionState } from './useActionState'

interface Context {
  rowId: number
  draft: string
}

interface Receipt {
  commandId: string
}

function response(status = 200) {
  return new Response(null, { status })
}

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((promiseResolve) => {
    resolve = promiseResolve
  })
  return { promise, resolve }
}

describe('useActionState', () => {
  it('accepts success only when both the HTTP response and expected receipt match', async () => {
    const actions = useActionState<Context, Receipt>()
    const context = { rowId: 4, draft: 'هشدار' }

    const result = await actions.run({
      key: 'warn:4',
      context,
      action: async () => ({ response: response(202), receipt: { commandId: 'cmd-4' } }),
      validateReceipt: (receipt) => receipt.commandId === 'cmd-4',
    })

    expect(result).toMatchObject({ outcome: 'success', key: 'warn:4', context })
    expect(actions.getState('warn:4')).toMatchObject({
      status: 'success',
      context,
      receipt: { commandId: 'cmd-4' },
      error: null,
    })
    expect(actions.getState('warn:4').context).toBe(context)
  })

  it('does not report a non-2xx response as success even with a matching receipt', async () => {
    const actions = useActionState<Context, Receipt>()
    const result = await actions.run({
      key: 'remove:8',
      context: { rowId: 8, draft: 'remove' },
      action: async () => ({ response: response(409), receipt: { commandId: 'cmd-8' } }),
      validateReceipt: () => true,
    })

    expect(result.outcome).toBe('error')
    expect(actions.getState('remove:8').status).toBe('error')
    expect(actions.getState('remove:8').error).toMatchObject({ code: 'NON_SUCCESS_RESPONSE' })
    expect(actions.getState('remove:8').error).toBeInstanceOf(ActionContractError)
  })

  it('does not report an unexpected or missing receipt as success', async () => {
    const actions = useActionState<Context, Receipt>()
    const result = await actions.run({
      key: 'limit:2',
      context: { rowId: 2, draft: 'limit' },
      action: async () => ({ response: response(200), receipt: { commandId: 'other' } }),
      validateReceipt: (receipt) => receipt.commandId === 'expected',
    })

    expect(result.outcome).toBe('error')
    expect(actions.getState('limit:2')).toMatchObject({
      status: 'error',
      error: { code: 'UNEXPECTED_RECEIPT' },
    })
  })

  it('guards duplicate mutations by key without replacing the original caller context', async () => {
    const pending = deferred<{ response: Response; receipt: Receipt }>()
    const action = vi.fn(() => pending.promise)
    const actions = useActionState<Context, Receipt>()
    const originalContext = { rowId: 10, draft: 'first' }
    const duplicateContext = { rowId: 10, draft: 'second' }

    const first = actions.run({
      key: 'save:10',
      context: originalContext,
      action,
      validateReceipt: () => true,
    })
    const duplicate = await actions.run({
      key: 'save:10',
      context: duplicateContext,
      action,
      validateReceipt: () => true,
    })

    expect(duplicate).toEqual({ outcome: 'duplicate', key: 'save:10', context: duplicateContext })
    expect(action).toHaveBeenCalledTimes(1)
    expect(actions.isBusy('save:10')).toBe(true)
    expect(actions.getState('save:10').context).toBe(originalContext)

    pending.resolve({ response: response(200), receipt: { commandId: 'saved' } })
    await expect(first).resolves.toMatchObject({ outcome: 'success' })
    expect(actions.isBusy('save:10')).toBe(false)
  })

  it('keeps the caller context on thrown failure and permits a fresh manual retry', async () => {
    const action = vi.fn()
      .mockRejectedValueOnce(new Error('offline'))
      .mockResolvedValueOnce({ response: response(200), receipt: { commandId: 'retry-ok' } })
    const actions = useActionState<Context, Receipt>()
    const context = { rowId: 3, draft: 'unchanged form value' }
    const input = {
      key: 'save:3',
      context,
      action,
      validateReceipt: (receipt: Receipt) => receipt.commandId === 'retry-ok',
    }

    await expect(actions.run(input)).resolves.toMatchObject({ outcome: 'error', context })
    expect(actions.getState('save:3').context).toBe(context)
    await expect(actions.run(input)).resolves.toMatchObject({ outcome: 'success', context })
    expect(action).toHaveBeenCalledTimes(2)
  })
})
