import { afterEach, describe, expect, it, vi } from 'vitest'
import { deleteSharedPayload, readSharedPayload } from './shareTargetStore'

type IndexedDbRecord = Record<string, any>

function installIndexedDb(recordsInput: IndexedDbRecord[], options?: {
  hasStore?: boolean
  triggerUpgrade?: boolean
  failOpen?: boolean
  failGet?: boolean
  failDelete?: boolean
}) {
  const records = new Map(recordsInput.map((record) => [record.key, { ...record }]))
  const createdStores: string[] = []

  const buildReadRequest = <T,>(result: T, shouldError = false) => {
    const request: {
      result: T
      error: Error | null
      onsuccess: null | (() => void)
      onerror: null | (() => void)
    } = {
      result,
      error: null,
      onsuccess: null,
      onerror: null,
    }

    Promise.resolve().then(() => {
      if (shouldError) {
        request.error = new Error('request failed')
        request.onerror?.()
        return
      }
      request.onsuccess?.()
    })

    return request
  }

  const createTransaction = () => {
    const tx: {
      error: Error | null
      objectStore: (_store: string) => {
        get: (key: string) => ReturnType<typeof buildReadRequest>
        delete: (key: string) => void
      }
      oncomplete: null | (() => void)
      onerror: null | (() => void)
    } = {
      error: null,
      objectStore: () => ({
        get(key: string) {
          return buildReadRequest(records.get(key) ?? null, options?.failGet === true)
        },
        delete(key: string) {
          Promise.resolve().then(() => {
            if (options?.failDelete) {
              tx.error = new Error('delete failed')
              tx.onerror?.()
              return
            }

            records.delete(key)
            tx.oncomplete?.()
          })
        },
      }),
      oncomplete: null,
      onerror: null,
    }

    return tx
  }

  const db = {
    objectStoreNames: {
      contains: () => options?.hasStore ?? true,
    },
    createObjectStore: (name: string) => {
      createdStores.push(name)
    },
    transaction: () => createTransaction(),
    close: vi.fn(),
  }

  vi.stubGlobal('indexedDB', {
    open: () => {
      const request: {
        result: typeof db
        error: Error | null
        onupgradeneeded: null | (() => void)
        onsuccess: null | (() => void)
        onerror: null | (() => void)
      } = {
        result: db,
        error: null,
        onupgradeneeded: null,
        onsuccess: null,
        onerror: null,
      }

      Promise.resolve().then(() => {
        if (options?.failOpen) {
          request.error = new Error('open failed')
          request.onerror?.()
          return
        }

        if (options?.triggerUpgrade) {
          request.onupgradeneeded?.()
        }

        request.onsuccess?.()
      })

      return request
    },
  })

  return { records, createdStores, closeMock: db.close }
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('shareTargetStore', () => {
  it('normalizes blob, base64, array, buffer, and typed-view files while creating the store on upgrade', async () => {
    const blobFile = new Blob(['blob-data'], { type: 'text/plain' })
    const buffer = Uint8Array.from([99, 100]).buffer
    const viewBuffer = Uint8Array.from([101, 102]).buffer
    const { createdStores, closeMock } = installIndexedDb([
      {
        key: 'payload-1',
        createdAt: 123,
        title: 'عنوان',
        text: 'متن',
        url: 'https://example.test',
        files: [
          {
            name: 'blob.txt',
            type: 'text/plain',
            size: blobFile.size,
            blob: blobFile,
          },
          {
            name: 'base64.bin',
            type: 'application/octet-stream',
            bodyBase64: btoa('abc'),
          },
          {
            name: 'array.bin',
            type: 'application/octet-stream',
            bytes: [97, 98],
          },
          {
            name: 'buffer.bin',
            type: 'application/octet-stream',
            bytes: buffer,
          },
          {
            name: 'view.bin',
            type: 'application/octet-stream',
            bytes: new DataView(viewBuffer),
          },
          {
            name: 'invalid.bin',
            type: 'application/octet-stream',
          },
        ],
      },
    ], {
      hasStore: false,
      triggerUpgrade: true,
    })

    const payload = await readSharedPayload('payload-1')

    expect(createdStores).toEqual(['pending'])
    expect(closeMock).toHaveBeenCalledTimes(1)
    expect(payload).not.toBeNull()
    expect(payload?.files).toHaveLength(5)
    expect(payload?.files.map((file) => ({ name: file.name, type: file.type, size: file.size }))).toEqual([
      { name: 'blob.txt', type: 'text/plain', size: blobFile.size },
      { name: 'base64.bin', type: 'application/octet-stream', size: 3 },
      { name: 'array.bin', type: 'application/octet-stream', size: 2 },
      { name: 'buffer.bin', type: 'application/octet-stream', size: 2 },
      { name: 'view.bin', type: 'application/octet-stream', size: 2 },
    ])
    expect(payload?.files[1]?.blob).toBeDefined()
    expect(payload?.files[2]?.blob).toBeDefined()
    expect(payload?.files[3]?.blob).toBeDefined()
    expect(payload?.files[4]?.blob).toBeDefined()
  })

  it('returns null when the payload key is missing', async () => {
    installIndexedDb([], { hasStore: true })

    await expect(readSharedPayload('missing-key')).resolves.toBeNull()
  })

  it('returns null when the database cannot open or the read request fails', async () => {
    installIndexedDb([], { failOpen: true })
    await expect(readSharedPayload('broken-open')).resolves.toBeNull()

    installIndexedDb([
      {
        key: 'payload-2',
        createdAt: 1,
        title: '',
        text: '',
        url: '',
        files: [],
      },
    ], { failGet: true })
    await expect(readSharedPayload('payload-2')).resolves.toBeNull()
  })

  it('deletes a stored payload and closes the database on success', async () => {
    const { records, closeMock } = installIndexedDb([
      {
        key: 'payload-3',
        createdAt: 1,
        title: 'x',
        text: 'y',
        url: '',
        files: [],
      },
    ])

    await deleteSharedPayload('payload-3')

    expect(records.has('payload-3')).toBe(false)
    expect(closeMock).toHaveBeenCalledTimes(1)
  })

  it('swallows delete failures and open failures without throwing', async () => {
    installIndexedDb([
      {
        key: 'payload-4',
        createdAt: 1,
        title: '',
        text: '',
        url: '',
        files: [],
      },
    ], { failDelete: true })
    await expect(deleteSharedPayload('payload-4')).resolves.toBeUndefined()

    installIndexedDb([], { failOpen: true })
    await expect(deleteSharedPayload('payload-5')).resolves.toBeUndefined()
  })
})