/* eslint-disable */
// Service Worker fragment imported into the generated workbox SW.
// Handles POST requests to /share-receive coming from the OS share sheet
// (Web Share Target API). Stashes the FormData in IndexedDB under a unique
// key, then redirects to /share-receive?share_key=KEY so the SPA can pick
// it up and let the user choose target conversations.

(() => {
  const SHARE_DB_NAME = 'trading-bot-share-target'
  const SHARE_DB_VERSION = 1
  const SHARE_STORE = 'pending'

  function openShareDB() {
    return new Promise((resolve, reject) => {
      const req = indexedDB.open(SHARE_DB_NAME, SHARE_DB_VERSION)
      req.onupgradeneeded = () => {
        const db = req.result
        if (!db.objectStoreNames.contains(SHARE_STORE)) {
          db.createObjectStore(SHARE_STORE, { keyPath: 'key' })
        }
      }
      req.onsuccess = () => resolve(req.result)
      req.onerror = () => reject(req.error)
    })
  }

  async function putShareEntry(entry) {
    const db = await openShareDB()
    return new Promise((resolve, reject) => {
      const tx = db.transaction(SHARE_STORE, 'readwrite')
      tx.objectStore(SHARE_STORE).put(entry)
      tx.oncomplete = () => { db.close(); resolve(true) }
      tx.onerror = () => { db.close(); reject(tx.error) }
    })
  }

  function generateKey() {
    if (self.crypto && typeof self.crypto.randomUUID === 'function') {
      return 'st-' + self.crypto.randomUUID()
    }
    return 'st-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 10)
  }

  self.addEventListener('fetch', (event) => {
    const req = event.request
    if (req.method !== 'POST') return
    let url
    try { url = new URL(req.url) } catch { return }
    if (url.pathname !== '/share-receive') return

    event.respondWith((async () => {
      try {
        const formData = await req.formData()
        const title = (formData.get('title') || '').toString()
        const text = (formData.get('text') || '').toString()
        const link = (formData.get('url') || '').toString()
        const fileEntries = formData.getAll('files')
        const files = []
        for (const fe of fileEntries) {
          if (fe && typeof fe === 'object' && 'name' in fe && 'type' in fe && 'arrayBuffer' in fe) {
            // Store as Blob (structured-clone safe in IDB).
            try {
              const blob = fe.slice(0, fe.size, fe.type || 'application/octet-stream')
              files.push({
                name: fe.name || 'shared',
                type: fe.type || 'application/octet-stream',
                size: fe.size || blob.size,
                blob,
              })
            } catch (err) {
              // fall through
            }
          }
        }

        const key = generateKey()
        await putShareEntry({
          key,
          createdAt: Date.now(),
          title,
          text,
          url: link,
          files,
        })

        const redirectUrl = '/share-receive?share_key=' + encodeURIComponent(key)
        return Response.redirect(redirectUrl, 303)
      } catch (err) {
        // Fall back: redirect to the SPA without a key so it can show an error UI.
        return Response.redirect('/share-receive?share_error=1', 303)
      }
    })())
  })
})()
