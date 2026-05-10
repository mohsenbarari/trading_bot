export type UploadedChatFile = {
  file_id: string
  file_name: string
  mime_type: string
  size: number
  thumbnail?: string | null
}

function getStoredAuthToken() {
  if (typeof window === 'undefined') return null
  return localStorage.getItem('auth_token')
}

export function buildChatFileUrl(fileId: string | null | undefined, apiBaseUrl = '') {
  const token = getStoredAuthToken()
  if (!fileId || !token) return ''
  return `${apiBaseUrl}/api/chat/files/${encodeURIComponent(fileId)}?token=${encodeURIComponent(token)}`
}

export function getAvatarInitial(name: string | null | undefined) {
  const normalized = (name || '').trim()
  return normalized ? normalized.charAt(0).toUpperCase() : '?'
}

export async function uploadAvatarImage(file: File, apiBaseUrl = ''): Promise<UploadedChatFile> {
  if (!file.type.startsWith('image/')) {
    throw new Error('فقط فایل تصویری برای آواتار قابل استفاده است.')
  }

  const token = getStoredAuthToken()
  if (!token) {
    throw new Error('نشست شما منقضی شده است. دوباره وارد شوید.')
  }

  const formData = new FormData()
  formData.append('file', file)

  const response = await fetch(`${apiBaseUrl}/api/chat/upload-media`, {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${token}`,
    },
    body: formData,
  })

  const data = await response.json().catch(() => ({})) as Partial<UploadedChatFile> & { detail?: string }
  if (!response.ok || typeof data.file_id !== 'string') {
    throw new Error(data.detail || 'آپلود آواتار ناموفق بود.')
  }

  return data as UploadedChatFile
}