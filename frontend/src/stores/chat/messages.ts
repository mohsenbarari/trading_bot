import { ref } from 'vue'
import { defineStore } from 'pinia'
import type { Message, MessageReaction } from '../../types/chat'

export const useMessagesStore = defineStore('chatMessages', () => {
  const messagesByRoom = ref<Record<number, Message[]>>({})
  const cursorsByRoom = ref<Record<number, { hasOlder: boolean; oldestId?: number | null }>>({})

  function setMessages(roomKey: number, messages: Message[]) {
    messagesByRoom.value = {
      ...messagesByRoom.value,
      [roomKey]: [...messages],
    }
  }

  function appendOrReplaceMessage(roomKey: number, message: Message) {
    const current = messagesByRoom.value[roomKey] || []
    const index = current.findIndex((candidate) => candidate.id === message.id)
    const next = [...current]
    if (index === -1) {
      next.push(message)
    } else {
      next[index] = { ...next[index], ...message }
    }
    setMessages(roomKey, next)
  }

  function patchReadState(roomKey: number, readerId?: number | null) {
    const current = messagesByRoom.value[roomKey] || []
    setMessages(roomKey, current.map((message) => (
      readerId == null || message.receiver_id === readerId
        ? { ...message, is_read: true }
        : message
    )))
  }

  function patchReaction(messageId: number, reactions: MessageReaction[]) {
    const nextByRoom: Record<number, Message[]> = {}
    for (const [roomKey, roomMessages] of Object.entries(messagesByRoom.value)) {
      nextByRoom[Number(roomKey)] = roomMessages.map((message) => (
        message.id === messageId
          ? { ...message, reactions }
          : message
      ))
    }
    messagesByRoom.value = nextByRoom
  }

  function setCursor(roomKey: number, cursor: { hasOlder: boolean; oldestId?: number | null }) {
    cursorsByRoom.value = {
      ...cursorsByRoom.value,
      [roomKey]: cursor,
    }
  }

  function clearRoom(roomKey: number) {
    const nextMessages = { ...messagesByRoom.value }
    delete nextMessages[roomKey]
    messagesByRoom.value = nextMessages

    const nextCursors = { ...cursorsByRoom.value }
    delete nextCursors[roomKey]
    cursorsByRoom.value = nextCursors
  }

  return {
    messagesByRoom,
    cursorsByRoom,
    setMessages,
    appendOrReplaceMessage,
    patchReadState,
    patchReaction,
    setCursor,
    clearRoom,
  }
})

