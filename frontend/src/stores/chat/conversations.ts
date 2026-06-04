import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import type { Conversation } from '../../types/chat'
import {
  CHAT_CACHE_SCHEMA_VERSION,
  readCachedConversations,
  writeCachedConversations,
  type ChatPendingLocalMutation,
} from '../../services/chat/chatCacheRepository'

type HydrationStatus = 'idle' | 'hydrating' | 'cached' | 'fresh' | 'error'

export const useConversationsStore = defineStore('chatConversations', () => {
  const conversations = ref<Conversation[]>([])
  const hydrationStatus = ref<HydrationStatus>('idle')
  const cacheUserId = ref<number | null>(null)
  const lastCacheAt = ref<number | null>(null)
  const pendingMutations = ref<ChatPendingLocalMutation[]>([])
  const error = ref('')

  const hasCachedData = computed(() => hydrationStatus.value === 'cached' && conversations.value.length > 0)

  function replaceLocalState(
    nextConversations: Conversation[],
    nextPendingMutations: ChatPendingLocalMutation[] = pendingMutations.value,
  ) {
    conversations.value = Array.isArray(nextConversations) ? [...nextConversations] : []
    pendingMutations.value = [...nextPendingMutations]
  }

  async function hydrateFromCache(userId: number) {
    cacheUserId.value = userId
    hydrationStatus.value = 'hydrating'
    const cached = await readCachedConversations({
      userId,
      schemaVersion: CHAT_CACHE_SCHEMA_VERSION,
    })

    if (!cached) {
      hydrationStatus.value = 'idle'
      return null
    }

    replaceLocalState(cached.conversations, cached.pendingMutations)
    lastCacheAt.value = cached.cachedAt
    hydrationStatus.value = 'cached'
    return cached
  }

  function mergePendingConversationState(serverConversations: Conversation[]) {
    const merged = serverConversations.map((conversation) => ({ ...conversation }))

    for (const mutation of pendingMutations.value) {
      if (mutation.kind === 'mute' || mutation.kind === 'pin') {
        const roomKey = Number(mutation.roomKey)
        const target = merged.find((conversation) => conversation.other_user_id === roomKey)
        if (!target) continue
        if (mutation.kind === 'mute' && typeof mutation.payload.muted === 'boolean') {
          target.is_muted = mutation.payload.muted
        }
        if (mutation.kind === 'pin' && typeof mutation.payload.pinned === 'boolean') {
          target.is_pinned = mutation.payload.pinned
        }
      }
    }

    return merged
  }

  async function replaceFromServer(userId: number, serverConversations: Conversation[]) {
    cacheUserId.value = userId
    const merged = mergePendingConversationState(Array.isArray(serverConversations) ? serverConversations : [])
    replaceLocalState(merged)
    hydrationStatus.value = 'fresh'
    error.value = ''
    await writeCachedConversations({
      userId,
      schemaVersion: CHAT_CACHE_SCHEMA_VERSION,
    }, merged, pendingMutations.value)
    return merged
  }

  function patchConversation(conversationKey: number, patch: Partial<Conversation>) {
    conversations.value = conversations.value.map((conversation) => (
      conversation.other_user_id === conversationKey
        ? { ...conversation, ...patch }
        : conversation
    ))
  }

  function addPendingMutation(mutation: ChatPendingLocalMutation) {
    pendingMutations.value = [
      ...pendingMutations.value.filter((item) => item.id !== mutation.id),
      mutation,
    ]
  }

  function removePendingMutation(mutationId: string) {
    pendingMutations.value = pendingMutations.value.filter((mutation) => mutation.id !== mutationId)
  }

  function setError(message: string) {
    error.value = message
    hydrationStatus.value = 'error'
  }

  return {
    conversations,
    hydrationStatus,
    cacheUserId,
    lastCacheAt,
    pendingMutations,
    error,
    hasCachedData,
    hydrateFromCache,
    replaceFromServer,
    replaceLocalState,
    patchConversation,
    addPendingMutation,
    removePendingMutation,
    setError,
  }
})

