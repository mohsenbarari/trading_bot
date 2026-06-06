import { processImageInWorker } from './imagePreprocessClient'

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

function isSupportedImageFile(file: File) {
  if (file.type.startsWith('image/')) return true
  return /\.(jpe?g|png|gif|webp|heic|heif)$/i.test(file.name)
}

function shouldKeepOriginalAvatarFile(file: File) {
  const normalizedType = file.type.toLowerCase()
  return normalizedType === 'image/gif' || /\.gif$/i.test(file.name)
}

function getAvatarJpegFileName(file: File) {
  const baseName = file.name.replace(/\.[^.]+$/, '').trim() || 'avatar'
  return `${baseName}.jpg`
}

function canvasToJpegBlob(canvas: HTMLCanvasElement, quality: number) {
  return new Promise<Blob>((resolve, reject) => {
    canvas.toBlob((blob) => {
      if (!blob) {
        reject(new Error('Avatar canvas export failed'))
        return
      }
      resolve(blob)
    }, 'image/jpeg', quality)
  })
}

async function transcodeAvatarWithBitmap(file: File) {
  if (typeof createImageBitmap !== 'function' || typeof document === 'undefined') {
    throw new Error('Avatar bitmap preprocessing is unavailable')
  }

  let bitmap: ImageBitmap
  try {
    bitmap = await createImageBitmap(file, { imageOrientation: 'from-image' })
  } catch {
    bitmap = await createImageBitmap(file)
  }

  try {
    const maxSize = 1024
    const ratio = Math.min(1, maxSize / Math.max(bitmap.width, bitmap.height))
    const width = Math.max(1, Math.round(bitmap.width * ratio))
    const height = Math.max(1, Math.round(bitmap.height * ratio))
    const canvas = document.createElement('canvas')
    canvas.width = width
    canvas.height = height
    const context = canvas.getContext('2d')
    if (!context) {
      throw new Error('Avatar canvas context is unavailable')
    }
    context.drawImage(bitmap, 0, 0, width, height)
    return await canvasToJpegBlob(canvas, 0.88)
  } finally {
    bitmap.close()
  }
}

async function transcodeAvatarWithImageElement(file: File) {
  if (typeof document === 'undefined' || typeof URL === 'undefined') {
    throw new Error('Avatar image preprocessing is unavailable')
  }

  const objectUrl = URL.createObjectURL(file)
  try {
    const image = await new Promise<HTMLImageElement>((resolve, reject) => {
      const element = new Image()
      element.onload = () => resolve(element)
      element.onerror = () => reject(new Error('Avatar image decode failed'))
      element.src = objectUrl
    })
    const maxSize = 1024
    const ratio = Math.min(1, maxSize / Math.max(image.naturalWidth, image.naturalHeight))
    const width = Math.max(1, Math.round(image.naturalWidth * ratio))
    const height = Math.max(1, Math.round(image.naturalHeight * ratio))
    const canvas = document.createElement('canvas')
    canvas.width = width
    canvas.height = height
    const context = canvas.getContext('2d')
    if (!context) {
      throw new Error('Avatar canvas context is unavailable')
    }
    context.drawImage(image, 0, 0, width, height)
    return await canvasToJpegBlob(canvas, 0.88)
  } finally {
    URL.revokeObjectURL(objectUrl)
  }
}

async function prepareAvatarUploadFile(file: File) {
  if (shouldKeepOriginalAvatarFile(file)) {
    return file
  }

  try {
    const processed = await processImageInWorker(file)
    return new File([processed.blob], getAvatarJpegFileName(file), {
      type: 'image/jpeg',
      lastModified: file.lastModified,
    })
  } catch {
    try {
      const blob = await transcodeAvatarWithBitmap(file)
      return new File([blob], getAvatarJpegFileName(file), {
        type: 'image/jpeg',
        lastModified: file.lastModified,
      })
    } catch {
      try {
        const blob = await transcodeAvatarWithImageElement(file)
        return new File([blob], getAvatarJpegFileName(file), {
          type: 'image/jpeg',
          lastModified: file.lastModified,
        })
      } catch {
        return file
      }
    }
  }
}

export async function uploadAvatarImage(file: File, apiBaseUrl = ''): Promise<UploadedChatFile> {
  if (!isSupportedImageFile(file)) {
    throw new Error('فقط فایل تصویری برای آواتار قابل استفاده است.')
  }

  const token = getStoredAuthToken()
  if (!token) {
    throw new Error('نشست شما منقضی شده است. دوباره وارد شوید.')
  }

  const uploadFile = await prepareAvatarUploadFile(file)
  const formData = new FormData()
  formData.append('file', uploadFile)

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
