/* eslint-disable */
// Service Worker fragment imported into the generated workbox SW.
// Provides a narrow Chromium-oriented best-effort background executor for
// single direct/group resumable uploads. If the worker is terminated or the
// platform declines to keep it alive, the page-owned IndexedDB queue still
// resumes on the next foreground wake.

(() => {
  const DB_NAME = 'chat_upload_queue'
  const DB_VERSION = 1
  const STORE_NAME = 'pending'
  const DEFAULT_RESUMABLE_CHUNK_SIZE_BYTES = 512 * 1024
  const ACTIVE_TASKS = new Map()
  const READY_LIKE_STATUSES = new Set(['ready', 'committed'])
  const TERMINAL_STATUSES = new Set(['failed', 'cancelled', 'expired'])

  function openDB() {
    return new Promise((resolve, reject) => {
      const req = indexedDB.open(DB_NAME, DB_VERSION)
      req.onupgradeneeded = () => {
        const db = req.result
        if (!db.objectStoreNames.contains(STORE_NAME)) {
          db.createObjectStore(STORE_NAME, { keyPath: 'id' })
        }
      }
      req.onsuccess = () => resolve(req.result)
      req.onerror = () => reject(req.error)
    })
  }

  function dataUrlToBlob(dataUrl, fallbackType) {
    const parts = String(dataUrl || '').split(',', 2)
    const header = parts[0] || ''
    const base64 = parts[1] || ''
    const mimeMatch = /^data:([^;]+);base64$/i.exec(header)
    const mimeType = (mimeMatch && mimeMatch[1]) || fallbackType || 'application/octet-stream'
    const binary = atob(base64)
    const bytes = new Uint8Array(binary.length)
    for (let index = 0; index < binary.length; index += 1) {
      bytes[index] = binary.charCodeAt(index)
    }
    return new Blob([bytes], { type: mimeType })
  }

  function restorePersistedFile(record) {
    if (record.file instanceof Blob) {
      return record.file
    }
    if (record.fileDataUrl) {
      return dataUrlToBlob(record.fileDataUrl, record.mimeType)
    }
    if (record.fileBytes) {
      return new Blob([record.fileBytes], { type: record.mimeType || 'application/octet-stream' })
    }
    return null
  }

  async function getUploadRecord(id) {
    const db = await openDB()
    return await new Promise((resolve) => {
      const tx = db.transaction(STORE_NAME, 'readonly')
      const req = tx.objectStore(STORE_NAME).get(id)
      req.onsuccess = () => {
        const record = req.result
        if (!record) {
          db.close()
          resolve(null)
          return
        }
        const restoredFile = restorePersistedFile(record)
        if (!restoredFile) {
          db.close()
          resolve(null)
          return
        }
        resolve({ ...record, file: restoredFile })
        db.close()
      }
      req.onerror = () => {
        db.close()
        resolve(null)
      }
    })
  }

  async function putUploadRecord(upload) {
    const db = await openDB()
    await new Promise((resolve) => {
      const tx = db.transaction(STORE_NAME, 'readwrite')
      tx.objectStore(STORE_NAME).put(upload)
      tx.oncomplete = () => resolve()
      tx.onerror = () => resolve()
      tx.onabort = () => resolve()
    })
    db.close()
  }

  async function deleteUploadRecord(id) {
    const db = await openDB()
    await new Promise((resolve) => {
      const tx = db.transaction(STORE_NAME, 'readwrite')
      tx.objectStore(STORE_NAME).delete(id)
      tx.oncomplete = () => resolve()
      tx.onerror = () => resolve()
      tx.onabort = () => resolve()
    })
    db.close()
  }

  function isSingleSessionBackedUpload(upload) {
    return Boolean(upload) && (upload.roomKind === 'direct' || upload.roomKind === 'group') && !upload.albumId
  }

  function resolveUploadTargetId(upload) {
    if (upload.roomKind === 'direct') {
      return upload.userId
    }
    return Math.abs(Number(upload.userId))
  }

  function buildSingleUploadBatchIdempotencyKey(upload) {
    return [
      'single',
      upload.roomKind,
      String(upload.senderId),
      String(resolveUploadTargetId(upload)),
      String(upload.id),
      upload.msgType,
    ].join(':').slice(0, 128)
  }

  function getUploadPreviewMetadata(upload) {
    const payload = {}
    if (upload.thumbnail && upload.msgType !== 'document') payload.thumbnail = upload.thumbnail
    if (Number(upload.width) > 0 && upload.msgType !== 'document') payload.width = upload.width
    if (Number(upload.height) > 0 && upload.msgType !== 'document') payload.height = upload.height
    if (typeof upload.durationMs === 'number' && upload.durationMs >= 0 && upload.msgType !== 'document') {
      payload.duration_ms = upload.durationMs
    }
    if ((upload.msgType === 'image' || upload.msgType === 'video') && upload.caption) {
      payload.caption = upload.caption
    }
    return payload
  }

  function applyPreviewMetadataToUpload(upload, previewMetadata) {
    if (!previewMetadata || typeof previewMetadata !== 'object') return
    if (typeof previewMetadata.thumbnail === 'string' && previewMetadata.thumbnail.trim()) {
      upload.serverThumbnail = previewMetadata.thumbnail
      if (!upload.thumbnail) upload.thumbnail = previewMetadata.thumbnail
    }
    const width = Number(previewMetadata.width)
    const height = Number(previewMetadata.height)
    if (Number.isFinite(width) && width > 0 && Number.isFinite(height) && height > 0) {
      upload.width = width
      upload.height = height
    }
    const durationMs = Number(previewMetadata.duration_ms ?? previewMetadata.durationMs)
    if (Number.isFinite(durationMs) && durationMs >= 0) {
      upload.durationMs = durationMs
    }
  }

  function getUploadResumeProgress(upload) {
    const totalBytes = upload.file.size || upload.totalBytes || 0
    const nextOffset = Math.max(0, Math.min(upload.nextOffset || upload.uploadedBytes || 0, totalBytes))
    if (totalBytes <= 0) return 0
    return Math.max(0, Math.min(100, Math.round((nextOffset / totalBytes) * 100)))
  }

  function clearResumableUploadState(upload) {
    delete upload.batchId
    delete upload.sessionId
    delete upload.resumeToken
    delete upload.sessionExpiresAt
    delete upload.fileId
    delete upload.nextOffset
  }

  async function uploadApiFetchJson(path, init, runtime) {
    const response = await fetch(`${runtime.apiBaseUrl}/api${path}`, {
      ...init,
      headers: {
        ...(runtime.authToken ? { Authorization: `Bearer ${runtime.authToken}` } : {}),
        ...(init && init.headers ? init.headers : {}),
      },
    })

    if (!response.ok) {
      let detail = ''
      try {
        const parsed = await response.json()
        if (parsed && typeof parsed.detail === 'string') {
          detail = parsed.detail
        }
      } catch {
        // ignore
      }
      const error = new Error(detail || `Upload API error (${response.status})`)
      error.status = response.status
      throw error
    }

    if (response.status === 204) return null
    return await response.json()
  }

  function isTransientUploadError(error) {
    const msg = error && error.message ? String(error.message) : String(error || '')
    if (!msg) return false
    return /network error|failed to fetch|networkerror|load failed|connection was lost/i.test(msg) || /\b(502|503|504|520|521|522|524|408)\b/.test(msg)
  }

  function broadcast(message) {
    self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then((clients) => {
      clients.forEach((client) => client.postMessage(message))
    }).catch(() => {})
  }

  async function syncResumableUploadState(upload, runtime) {
    if (!upload.sessionId) return null
    try {
      const state = await uploadApiFetchJson(`/chat/upload-sessions/${upload.sessionId}`, { method: 'GET' }, runtime)
      if (!state) return null
      upload.nextOffset = Math.max(0, Number(state.next_offset || 0))
      upload.uploadedBytes = Math.max(0, Math.min(Number(state.received_bytes || 0), upload.file.size))
      upload.totalBytes = Number(state.total_bytes || upload.file.size || 0)
      upload.progress = getUploadResumeProgress(upload)
      upload.sessionExpiresAt = state.expires_at || upload.sessionExpiresAt
      if (state.final_chat_file_id) upload.fileId = state.final_chat_file_id
      applyPreviewMetadataToUpload(upload, state.preview_metadata)
      if (TERMINAL_STATUSES.has(String(state.status || ''))) {
        clearResumableUploadState(upload)
        await putUploadRecord(upload)
        return null
      }
      return state
    } catch (error) {
      if (error && error.status === 404) {
        clearResumableUploadState(upload)
        await putUploadRecord(upload)
        return null
      }
      throw error
    }
  }

  async function ensureResumableUploadBatch(upload, runtime) {
    if (upload.batchId) return
    const payload = await uploadApiFetchJson('/chat/upload-batches', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        room_kind: upload.roomKind,
        target_id: resolveUploadTargetId(upload),
        message_kind: 'single',
        expected_items: 1,
        caption_policy: 'none',
        idempotency_key: buildSingleUploadBatchIdempotencyKey(upload),
      }),
    }, runtime)
    upload.batchId = payload.batch_id
    await putUploadRecord(upload)
  }

  async function ensureResumableUploadSession(upload, runtime) {
    const syncedState = await syncResumableUploadState(upload, runtime)
    if (syncedState) return syncedState

    await ensureResumableUploadBatch(upload, runtime)
    const payload = await uploadApiFetchJson('/chat/upload-sessions', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        batch_id: upload.batchId,
        room_kind: upload.roomKind,
        target_id: resolveUploadTargetId(upload),
        media_type: upload.msgType,
        file_name: upload.fileName,
        mime_type: upload.mimeType || 'application/octet-stream',
        total_bytes: upload.file.size,
        chunk_size: DEFAULT_RESUMABLE_CHUNK_SIZE_BYTES,
        preview_metadata: getUploadPreviewMetadata(upload),
      }),
    }, runtime)
    upload.sessionId = payload.session_id
    upload.resumeToken = payload.resume_token
    upload.nextOffset = Math.max(0, Number(payload.next_offset || 0))
    upload.sessionExpiresAt = payload.expires_at
    upload.totalBytes = upload.file.size
    upload.uploadedBytes = Math.max(0, Math.min(upload.nextOffset, upload.file.size))
    upload.progress = getUploadResumeProgress(upload)
    await putUploadRecord(upload)
    return null
  }

  async function appendChunk(upload, runtime, offset, isLastChunk, task) {
    const formData = new FormData()
    formData.append('resume_token', upload.resumeToken)
    formData.append('offset', String(offset))
    formData.append('is_last_chunk', String(isLastChunk))
    formData.append('chunk', upload.file.slice(offset, Math.min(offset + DEFAULT_RESUMABLE_CHUNK_SIZE_BYTES, upload.file.size)), upload.fileName)

    const controller = new AbortController()
    task.controller = controller
    try {
      return await uploadApiFetchJson(`/chat/upload-sessions/${upload.sessionId}/chunk`, {
        method: 'PATCH',
        body: formData,
        signal: controller.signal,
      }, runtime)
    } finally {
      if (task.controller === controller) {
        task.controller = null
      }
    }
  }

  async function finalizeSession(upload, runtime, task) {
    const controller = new AbortController()
    task.controller = controller
    try {
      return await uploadApiFetchJson(`/chat/upload-sessions/${upload.sessionId}/finalize`, {
        method: 'POST',
        signal: controller.signal,
      }, runtime)
    } finally {
      if (task.controller === controller) {
        task.controller = null
      }
    }
  }

  async function commitSingleUpload(upload, runtime, task) {
    const controller = new AbortController()
    task.controller = controller
    try {
      const payload = await uploadApiFetchJson(`/chat/upload-batches/${upload.batchId}/commit`, {
        method: 'POST',
        signal: controller.signal,
      }, runtime)
      const serverMessage = Array.isArray(payload && payload.messages) ? payload.messages[0] : null
      if (!serverMessage) {
        throw new Error('Final server message missing')
      }
      await deleteUploadRecord(upload.id)
      broadcast({
        type: 'chat-upload:sent',
        uploadId: upload.id,
        serverMessage,
      })
    } finally {
      if (task.controller === controller) {
        task.controller = null
      }
    }
  }

  async function runSingleUpload(uploadId, task) {
    let upload = await getUploadRecord(uploadId)
    if (!upload || !isSingleSessionBackedUpload(upload)) return

    if (upload.phase === 'sent' || upload.phase === 'cancelled') {
      await deleteUploadRecord(uploadId)
      return
    }

    try {
      if (upload.phase === 'sending') {
        upload.phase = 'uploaded'
      }

      if (upload.phase === 'queued' || upload.phase === 'uploading') {
        upload.phase = 'uploading'
        upload.totalBytes = upload.file.size
        upload.uploadedBytes = Math.max(0, Math.min(upload.nextOffset || upload.uploadedBytes || 0, upload.file.size))
        upload.progress = getUploadResumeProgress(upload)
        await putUploadRecord(upload)

        const syncedState = await ensureResumableUploadSession(upload, task.runtime)
        if (task.reclaimed) return

        if (syncedState && READY_LIKE_STATUSES.has(String(syncedState.status || ''))) {
          upload.phase = 'uploaded'
          upload.progress = 100
          upload.uploadedBytes = upload.totalBytes
          if (syncedState.final_chat_file_id) {
            upload.fileId = syncedState.final_chat_file_id
          }
          await putUploadRecord(upload)
        } else {
          let offset = Math.max(0, Math.min(upload.nextOffset || 0, upload.file.size))
          while (offset < upload.file.size) {
            if (task.reclaimed) return
            const chunkPayload = await appendChunk(upload, task.runtime, offset, offset + DEFAULT_RESUMABLE_CHUNK_SIZE_BYTES >= upload.file.size, task)
            if (task.reclaimed) return
            offset = Math.max(0, Math.min(Number(chunkPayload.next_offset || (offset + DEFAULT_RESUMABLE_CHUNK_SIZE_BYTES)), upload.file.size))
            upload.nextOffset = offset
            upload.uploadedBytes = Math.max(0, Math.min(Number(chunkPayload.received_bytes || offset), upload.file.size))
            upload.totalBytes = upload.file.size
            upload.progress = getUploadResumeProgress(upload)
            await putUploadRecord(upload)
          }

          const finalizePayload = await finalizeSession(upload, task.runtime, task)
          if (task.reclaimed) return
          upload.fileId = finalizePayload && finalizePayload.final_chat_file_id ? finalizePayload.final_chat_file_id : upload.fileId
          upload.phase = 'uploaded'
          upload.progress = 100
          upload.uploadedBytes = upload.totalBytes
          await putUploadRecord(upload)
        }
      }

      upload = await getUploadRecord(uploadId)
      if (!upload || task.reclaimed) return
      if (upload.phase === 'uploaded') {
        await commitSingleUpload(upload, task.runtime, task)
      }
    } catch (error) {
      if (task.reclaimed || (error && error.name === 'AbortError')) {
        upload = await getUploadRecord(uploadId)
        if (upload) {
          upload.phase = upload.fileId ? 'uploaded' : 'queued'
          upload.progress = upload.fileId ? 100 : getUploadResumeProgress(upload)
          await putUploadRecord(upload)
        }
        return
      }

      upload = await getUploadRecord(uploadId)
      if (!upload) return
      upload.phase = upload.fileId ? 'uploaded' : 'queued'
      upload.errorMessage = error && error.message ? error.message : String(error)
      await putUploadRecord(upload)

      if (!isTransientUploadError(error)) {
        broadcast({
          type: 'chat-upload:error',
          uploadId: upload.id,
          errorMessage: upload.errorMessage,
        })
      }
    }
  }

  async function startUploadTask(uploadId, runtime) {
    if (ACTIVE_TASKS.has(uploadId)) return
    const task = { runtime, controller: null, reclaimed: false }
    ACTIVE_TASKS.set(uploadId, task)
    try {
      await runSingleUpload(uploadId, task)
    } finally {
      const current = ACTIVE_TASKS.get(uploadId)
      if (current === task) {
        ACTIVE_TASKS.delete(uploadId)
      }
    }
  }

  self.addEventListener('message', (event) => {
    const data = event.data || {}
    if (data.type === 'chat-upload:handoff') {
      const runtime = {
        apiBaseUrl: String(data.apiBaseUrl || ''),
        authToken: String(data.authToken || ''),
      }
      const uploadIds = Array.isArray(data.uploadIds) ? data.uploadIds.filter((id) => typeof id === 'number') : []
      const work = Promise.all(uploadIds.map((uploadId) => startUploadTask(uploadId, runtime)))
      if (event.waitUntil) {
        event.waitUntil(work)
      }
      return
    }

    if (data.type === 'chat-upload:reclaim') {
      const uploadIds = Array.isArray(data.uploadIds) ? data.uploadIds.filter((id) => typeof id === 'number') : []
      uploadIds.forEach((uploadId) => {
        const task = ACTIVE_TASKS.get(uploadId)
        if (!task) return
        task.reclaimed = true
        try {
          task.controller && task.controller.abort()
        } catch {
          // ignore
        }
      })
    }
  })
})()