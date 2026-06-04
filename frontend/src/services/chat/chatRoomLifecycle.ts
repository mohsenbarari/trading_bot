export type ChatRoomCleanup = () => void

export interface ChatRoomLifecycleRuntime {
  enterRoom: (roomKey: number | null) => void
  leaveRoom: (roomKey: number | null) => void
  registerRoomCleanup: (roomKey: number, cleanup: ChatRoomCleanup) => ChatRoomCleanup
  trackRoomObjectUrl: (roomKey: number, objectUrl: string) => void
  trackRoomTimer: (roomKey: number, timerId: number) => void
  getActiveRoomKey: () => number | null
}

export function createChatRoomLifecycleRuntime(): ChatRoomLifecycleRuntime {
  let activeRoomKey: number | null = null
  const cleanupsByRoom = new Map<number, Set<ChatRoomCleanup>>()
  const objectUrlsByRoom = new Map<number, Set<string>>()
  const timersByRoom = new Map<number, Set<number>>()

  function getCleanupSet(roomKey: number) {
    const existing = cleanupsByRoom.get(roomKey)
    if (existing) return existing
    const next = new Set<ChatRoomCleanup>()
    cleanupsByRoom.set(roomKey, next)
    return next
  }

  function leaveRoom(roomKey: number | null) {
    if (roomKey === null) return

    objectUrlsByRoom.get(roomKey)?.forEach((objectUrl) => {
      try {
        URL.revokeObjectURL(objectUrl)
      } catch {
        // Object URL cleanup is best-effort.
      }
    })
    objectUrlsByRoom.delete(roomKey)

    timersByRoom.get(roomKey)?.forEach((timerId) => {
      window.clearTimeout(timerId)
      window.clearInterval(timerId)
    })
    timersByRoom.delete(roomKey)

    cleanupsByRoom.get(roomKey)?.forEach((cleanup) => {
      try {
        cleanup()
      } catch {
        // Room teardown must not break navigation.
      }
    })
    cleanupsByRoom.delete(roomKey)
  }

  function enterRoom(roomKey: number | null) {
    if (activeRoomKey === roomKey) return
    leaveRoom(activeRoomKey)
    activeRoomKey = roomKey
  }

  function registerRoomCleanup(roomKey: number, cleanup: ChatRoomCleanup) {
    getCleanupSet(roomKey).add(cleanup)
    return () => {
      cleanupsByRoom.get(roomKey)?.delete(cleanup)
    }
  }

  function trackRoomObjectUrl(roomKey: number, objectUrl: string) {
    const urls = objectUrlsByRoom.get(roomKey) ?? new Set<string>()
    urls.add(objectUrl)
    objectUrlsByRoom.set(roomKey, urls)
  }

  function trackRoomTimer(roomKey: number, timerId: number) {
    const timers = timersByRoom.get(roomKey) ?? new Set<number>()
    timers.add(timerId)
    timersByRoom.set(roomKey, timers)
  }

  return {
    enterRoom,
    leaveRoom,
    registerRoomCleanup,
    trackRoomObjectUrl,
    trackRoomTimer,
    getActiveRoomKey: () => activeRoomKey,
  }
}

