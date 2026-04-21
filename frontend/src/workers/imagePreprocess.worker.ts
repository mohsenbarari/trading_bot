/// <reference lib="webworker" />

type ImagePreprocessRequest = {
  id: string
  file: Blob
  maxWidthOrHeight: number
  quality: number
  thumbnailMaxWidthOrHeight: number
  thumbnailQuality: number
}

type ImagePreprocessSuccessResponse = {
  id: string
  ok: true
  blob: Blob
  width: number
  height: number
  thumbnailDataUrl: string
}

type ImagePreprocessErrorResponse = {
  id: string
  ok: false
  error: string
}

type ImagePreprocessResponse = ImagePreprocessSuccessResponse | ImagePreprocessErrorResponse

const workerScope = self as unknown as DedicatedWorkerGlobalScope

async function createBitmapFromBlob(blob: Blob) {
  if (typeof createImageBitmap !== 'function') {
    throw new Error('createImageBitmap is unavailable in worker')
  }

  try {
    return await createImageBitmap(blob, { imageOrientation: 'from-image' })
  } catch {
    return await createImageBitmap(blob)
  }
}

async function compressImage(blob: Blob, maxWidthOrHeight: number, quality: number) {
  if (typeof OffscreenCanvas === 'undefined') {
    throw new Error('OffscreenCanvas is unavailable in worker')
  }

  const bitmap = await createBitmapFromBlob(blob)

  try {
    let width = bitmap.width
    let height = bitmap.height

    if (width > maxWidthOrHeight || height > maxWidthOrHeight) {
      const ratio = Math.min(maxWidthOrHeight / width, maxWidthOrHeight / height)
      width = Math.max(1, Math.round(width * ratio))
      height = Math.max(1, Math.round(height * ratio))
    }

    const canvas = new OffscreenCanvas(width, height)
    const context = canvas.getContext('2d')
    if (!context) {
      throw new Error('No OffscreenCanvas 2D context')
    }

    context.drawImage(bitmap, 0, 0, width, height)
    const output = await canvas.convertToBlob({ type: 'image/jpeg', quality })

    return { blob: output, width, height }
  } finally {
    bitmap.close()
  }
}

async function blobToDataUrl(blob: Blob) {
  const buffer = await blob.arrayBuffer()
  const bytes = new Uint8Array(buffer)
  let binary = ''
  const chunkSize = 0x8000

  for (let index = 0; index < bytes.length; index += chunkSize) {
    const chunk = bytes.subarray(index, index + chunkSize)
    binary += String.fromCharCode(...chunk)
  }

  return `data:${blob.type || 'application/octet-stream'};base64,${workerScope.btoa(binary)}`
}

workerScope.onmessage = async (event: MessageEvent<ImagePreprocessRequest>) => {
  const {
    id,
    file,
    maxWidthOrHeight,
    quality,
    thumbnailMaxWidthOrHeight,
    thumbnailQuality,
  } = event.data

  try {
    const compressed = await compressImage(file, maxWidthOrHeight, quality)
    const thumbnail = await compressImage(compressed.blob, thumbnailMaxWidthOrHeight, thumbnailQuality)
    const response: ImagePreprocessResponse = {
      id,
      ok: true,
      blob: compressed.blob,
      width: compressed.width,
      height: compressed.height,
      thumbnailDataUrl: await blobToDataUrl(thumbnail.blob),
    }

    workerScope.postMessage(response)
  } catch (error) {
    const response: ImagePreprocessResponse = {
      id,
      ok: false,
      error: error instanceof Error ? error.message : 'Unknown worker preprocessing error',
    }

    workerScope.postMessage(response)
  }
}

export {}