import { computed, shallowRef } from 'vue'

export type ActionStatus = 'idle' | 'busy' | 'success' | 'error'
export type ActionContractErrorCode = 'NON_SUCCESS_RESPONSE' | 'UNEXPECTED_RECEIPT'

export class ActionContractError extends Error {
  code: ActionContractErrorCode
  response: Response

  constructor(code: ActionContractErrorCode, response: Response) {
    super(code === 'NON_SUCCESS_RESPONSE'
      ? 'The action response was not successful.'
      : 'The action response did not contain the expected receipt.')
    this.name = 'ActionContractError'
    this.code = code
    this.response = response
  }
}

export interface ActionReceiptEnvelope<Receipt> {
  response: Response
  receipt: Receipt
}

export interface ActionState<Context, Receipt> {
  status: ActionStatus
  context: Context | null
  receipt: Receipt | null
  error: unknown | null
}

export interface ActionRunInput<Context, Receipt> {
  key: string
  context: Context
  action: (context: Context) => Promise<ActionReceiptEnvelope<Receipt>>
  validateReceipt: (receipt: Receipt, context: Context, response: Response) => boolean
}

export type ActionRunResult<Context, Receipt> =
  | {
      outcome: 'success'
      key: string
      context: Context
      receipt: Receipt
      response: Response
    }
  | {
      outcome: 'error'
      key: string
      context: Context
      error: unknown
      response: Response | null
    }
  | {
      outcome: 'duplicate'
      key: string
      context: Context
    }

function idleActionState<Context, Receipt>(): ActionState<Context, Receipt> {
  return {
    status: 'idle',
    context: null,
    receipt: null,
    error: null,
  }
}

export function useActionState<Context, Receipt>() {
  const states = shallowRef<Record<string, ActionState<Context, Receipt>>>({})
  const inFlightKeys = new Set<string>()

  function writeState(key: string, state: ActionState<Context, Receipt>) {
    states.value = { ...states.value, [key]: state }
  }

  function getState(key: string): ActionState<Context, Receipt> {
    return states.value[key] || idleActionState<Context, Receipt>()
  }

  function stateFor(key: string) {
    return computed(() => getState(key))
  }

  function isBusy(key: string) {
    return inFlightKeys.has(key)
  }

  async function run(
    input: ActionRunInput<Context, Receipt>,
  ): Promise<ActionRunResult<Context, Receipt>> {
    const { key, context, action, validateReceipt } = input

    if (inFlightKeys.has(key)) {
      return { outcome: 'duplicate', key, context }
    }

    inFlightKeys.add(key)
    writeState(key, {
      status: 'busy',
      context,
      receipt: null,
      error: null,
    })

    let response: Response | null = null
    try {
      const result = await action(context)
      response = result.response

      if (!response.ok || response.status < 200 || response.status >= 300) {
        throw new ActionContractError('NON_SUCCESS_RESPONSE', response)
      }
      if (!validateReceipt(result.receipt, context, response)) {
        throw new ActionContractError('UNEXPECTED_RECEIPT', response)
      }

      writeState(key, {
        status: 'success',
        context,
        receipt: result.receipt,
        error: null,
      })
      return {
        outcome: 'success',
        key,
        context,
        receipt: result.receipt,
        response,
      }
    } catch (error) {
      writeState(key, {
        status: 'error',
        context,
        receipt: null,
        error,
      })
      return {
        outcome: 'error',
        key,
        context,
        error,
        response,
      }
    } finally {
      inFlightKeys.delete(key)
    }
  }

  function reset(key: string) {
    if (inFlightKeys.has(key)) return false
    const next = { ...states.value }
    delete next[key]
    states.value = next
    return true
  }

  return {
    states: computed(() => states.value),
    getState,
    stateFor,
    isBusy,
    run,
    reset,
  }
}
