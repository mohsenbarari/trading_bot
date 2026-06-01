const UPLOAD_RESUME_HINT_KEY = 'chat_upload_background_pending'
const DOCUMENT_DOWNLOAD_RESUME_HINT_KEY = 'chat_document_download_background_pending'

function readHint(key: string): boolean {
  if (typeof window === 'undefined') return false
  try {
    return window.localStorage.getItem(key) === '1'
  } catch {
    return false
  }
}

function writeHint(key: string, hasPending: boolean): void {
  if (typeof window === 'undefined') return
  try {
    if (hasPending) {
      window.localStorage.setItem(key, '1')
    } else {
      window.localStorage.removeItem(key)
    }
  } catch {
    /* ignore storage failures */
  }
}

export function hasPendingUploadResumeHint(): boolean {
  return readHint(UPLOAD_RESUME_HINT_KEY)
}

export function setUploadResumeHint(hasPending: boolean): void {
  writeHint(UPLOAD_RESUME_HINT_KEY, hasPending)
}

export function hasPendingDocumentDownloadResumeHint(): boolean {
  return readHint(DOCUMENT_DOWNLOAD_RESUME_HINT_KEY)
}

export function setDocumentDownloadResumeHint(hasPending: boolean): void {
  writeHint(DOCUMENT_DOWNLOAD_RESUME_HINT_KEY, hasPending)
}