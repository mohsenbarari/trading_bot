export type ChatMessageTransferKind =
  | 'idle'
  | 'uploading'
  | 'processing'
  | 'downloading'
  | 'cached-download'

export type ChatMessageTransferInput = {
  isSending?: boolean | null
  isDownloading?: boolean | null
  uploadProgress?: number | null
  downloadProgress?: number | null
  cachedDownloadBusy?: boolean | null
  cachedDownloadProgress?: number | null
}

export type ChatMessageTransferState = {
  kind: ChatMessageTransferKind
  progress: number
  isBusy: boolean
  isSendingBusy: boolean
  isDownloadingBusy: boolean
  isCachedDownloadBusy: boolean
  isDownloadBusy: boolean
  isProcessing: boolean
  cancelAction: 'send' | 'download' | null
}

function clampTransferProgress(value: number | null | undefined): number {
  if (!Number.isFinite(value)) return 0
  return Math.max(0, Math.min(100, Math.round(value ?? 0)))
}

export function getChatMessageTransferState(
  input: ChatMessageTransferInput,
): ChatMessageTransferState {
  if (input.isSending) {
    const progress = clampTransferProgress(input.uploadProgress)
    const isProcessing = progress >= 100
    return {
      kind: isProcessing ? 'processing' : 'uploading',
      progress,
      isBusy: true,
      isSendingBusy: true,
      isDownloadingBusy: false,
      isCachedDownloadBusy: false,
      isDownloadBusy: false,
      isProcessing,
      cancelAction: 'send',
    }
  }

  if (input.isDownloading) {
    return {
      kind: 'downloading',
      progress: clampTransferProgress(input.downloadProgress),
      isBusy: true,
      isSendingBusy: false,
      isDownloadingBusy: true,
      isCachedDownloadBusy: false,
      isDownloadBusy: true,
      isProcessing: false,
      cancelAction: 'download',
    }
  }

  if (input.cachedDownloadBusy) {
    return {
      kind: 'cached-download',
      progress: clampTransferProgress(input.cachedDownloadProgress ?? 60),
      isBusy: true,
      isSendingBusy: false,
      isDownloadingBusy: false,
      isCachedDownloadBusy: true,
      isDownloadBusy: true,
      isProcessing: false,
      cancelAction: null,
    }
  }

  return {
    kind: 'idle',
    progress: 0,
    isBusy: false,
    isSendingBusy: false,
    isDownloadingBusy: false,
    isCachedDownloadBusy: false,
    isDownloadBusy: false,
    isProcessing: false,
    cancelAction: null,
  }
}