import { describe, expect, it } from 'vitest'

import { getChatMessageTransferState } from './chatMessageTransferState'

describe('chatMessageTransferState', () => {
  it('normalizes uploading messages into uploading and processing states', () => {
    expect(getChatMessageTransferState({
      isSending: true,
      uploadProgress: 44.2,
    })).toMatchObject({
      kind: 'uploading',
      progress: 44,
      isBusy: true,
      isSendingBusy: true,
      cancelAction: 'send',
    })

    expect(getChatMessageTransferState({
      isSending: true,
      uploadProgress: 140,
    })).toMatchObject({
      kind: 'processing',
      progress: 100,
      isProcessing: true,
      cancelAction: 'send',
    })
  })

  it('normalizes download progress and cached download busy states', () => {
    expect(getChatMessageTransferState({
      isDownloading: true,
      downloadProgress: 145,
    })).toMatchObject({
      kind: 'downloading',
      progress: 100,
      isDownloadBusy: true,
      cancelAction: 'download',
    })

    expect(getChatMessageTransferState({
      cachedDownloadBusy: true,
    })).toMatchObject({
      kind: 'cached-download',
      progress: 60,
      isBusy: true,
      isCachedDownloadBusy: true,
      isDownloadBusy: true,
      cancelAction: null,
    })
  })

  it('returns an idle state when no transfer is active', () => {
    expect(getChatMessageTransferState({})).toEqual({
      kind: 'idle',
      progress: 0,
      isBusy: false,
      isSendingBusy: false,
      isDownloadingBusy: false,
      isCachedDownloadBusy: false,
      isDownloadBusy: false,
      isProcessing: false,
      cancelAction: null,
    })
  })
})