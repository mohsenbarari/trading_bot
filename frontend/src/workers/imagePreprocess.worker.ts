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

function getScaledDimensions(width: number, height: number, maxWidthOrHeight: number) {
  let nextWidth = width
  let nextHeight = height

  if (nextWidth > maxWidthOrHeight || nextHeight > maxWidthOrHeight) {
    const ratio = Math.min(maxWidthOrHeight / nextWidth, maxWidthOrHeight / nextHeight)
    nextWidth = Math.max(1, Math.round(nextWidth * ratio))
    nextHeight = Math.max(1, Math.round(nextHeight * ratio))
  }

  return { width: nextWidth, height: nextHeight }
}

async function preprocessImage(
  blob: Blob,
  maxWidthOrHeight: number,
  quality: number,
  thumbnailMaxWidthOrHeight: number,
  thumbnailQuality: number
) {
  if (typeof OffscreenCanvas === 'undefined') {
    throw new Error('OffscreenCanvas is unavailable in worker')
  }

  const bitmap = await createBitmapFromBlob(blob)

  try {
    const scaled = getScaledDimensions(bitmap.width, bitmap.height, maxWidthOrHeight)
    const width = scaled.width
    const height = scaled.height

    const canvas = new OffscreenCanvas(width, height)
    const context = canvas.getContext('2d')
    if (!context) {
      throw new Error('No OffscreenCanvas 2D context')
    }

    context.drawImage(bitmap, 0, 0, width, height)
    const output = await canvas.convertToBlob({ type: 'image/jpeg', quality })

    const thumbnailScaled = getScaledDimensions(width, height, thumbnailMaxWidthOrHeight)
    const thumbnailCanvas = new OffscreenCanvas(thumbnailScaled.width, thumbnailScaled.height)
    const thumbnailContext = thumbnailCanvas.getContext('2d')
    if (!thumbnailContext) {
      throw new Error('No OffscreenCanvas thumbnail context')
    }

    thumbnailContext.drawImage(canvas, 0, 0, thumbnailScaled.width, thumbnailScaled.height)
    const thumbnailBlob = await thumbnailCanvas.convertToBlob({
      type: 'image/jpeg',
      quality: thumbnailQuality,
    })

    return {
      blob: output,
      width,
      height,
      thumbnailDataUrl: await blobToDataUrl(thumbnailBlob),
    }
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
    const processed = await preprocessImage(
      file,
      maxWidthOrHeight,
      quality,
      thumbnailMaxWidthOrHeight,
      thumbnailQuality
    )
    const response: ImagePreprocessResponse = {
      id,
      ok: true,
      blob: processed.blob,
      width: processed.width,
      height: processed.height,
      thumbnailDataUrl: processed.thumbnailDataUrl,
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