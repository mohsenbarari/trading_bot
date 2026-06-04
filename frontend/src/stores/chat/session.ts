import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import type { ChatKind } from '../../types/chat'

interface ParticipantActivity {
  name?: string | null
  activity: string
  updatedAt: number
}

export const useChatSessionStore = defineStore('chatSession', () => {
  const activeRoomKey = ref<number | null>(null)
  const activeRoomKind = ref<ChatKind>('direct')
  const activeRoomTitle = ref('')
  const activeRoomAvatarFileId = ref<string | null>(null)
  const activeRoomStatusText = ref('')
  const routeRestoreRoomKey = ref<number | null>(null)
  const typingByRoom = ref<Record<number, Record<number, string | null>>>({})
  const activityByRoom = ref<Record<number, Record<number, ParticipantActivity>>>({})

  const isRoomActive = computed(() => (roomKey: number | null) => (
    roomKey !== null && activeRoomKey.value === roomKey
  ))

  function setActiveRoom(
    roomKey: number | null,
    options: {
      kind?: ChatKind
      title?: string
      avatarFileId?: string | null
      statusText?: string
    } = {},
  ) {
    activeRoomKey.value = roomKey
    activeRoomKind.value = options.kind ?? 'direct'
    activeRoomTitle.value = options.title ?? ''
    activeRoomAvatarFileId.value = options.avatarFileId ?? null
    activeRoomStatusText.value = options.statusText ?? ''
  }

  function setRouteRestoreRoom(roomKey: number | null) {
    routeRestoreRoomKey.value = roomKey
  }

  function setUserTyping(roomKey: number, userId: number, active: boolean, name: string | null = null) {
    const currentRoom = { ...(typingByRoom.value[roomKey] || {}) }
    if (active) {
      currentRoom[userId] = name
    } else {
      delete currentRoom[userId]
    }

    typingByRoom.value = {
      ...typingByRoom.value,
      [roomKey]: currentRoom,
    }
  }

  function setUserActivity(
    roomKey: number,
    userId: number,
    activity: string,
    active: boolean,
    name: string | null = null,
  ) {
    const currentRoom = { ...(activityByRoom.value[roomKey] || {}) }
    if (active) {
      currentRoom[userId] = { activity, name, updatedAt: Date.now() }
    } else {
      delete currentRoom[userId]
    }

    activityByRoom.value = {
      ...activityByRoom.value,
      [roomKey]: currentRoom,
    }
  }

  function clearRoomRuntime(roomKey: number) {
    const nextTyping = { ...typingByRoom.value }
    delete nextTyping[roomKey]
    typingByRoom.value = nextTyping

    const nextActivity = { ...activityByRoom.value }
    delete nextActivity[roomKey]
    activityByRoom.value = nextActivity
  }

  return {
    activeRoomKey,
    activeRoomKind,
    activeRoomTitle,
    activeRoomAvatarFileId,
    activeRoomStatusText,
    routeRestoreRoomKey,
    typingByRoom,
    activityByRoom,
    isRoomActive,
    setActiveRoom,
    setRouteRestoreRoom,
    setUserTyping,
    setUserActivity,
    clearRoomRuntime,
  }
})

