import { ref } from 'vue'
import { defineStore } from 'pinia'
import type { ChatSelectionPurpose } from '../../types/chat'

export const useChatUiStore = defineStore('chatUi', () => {
  const isSearchActive = ref(false)
  const searchQuery = ref('')
  const selectedMessageIds = ref<number[]>([])
  const selectionPurpose = ref<ChatSelectionPurpose>('default')
  const lightboxMessageId = ref<number | null>(null)
  const contextMenuMessageId = ref<number | null>(null)

  function setSearch(active: boolean, query = searchQuery.value) {
    isSearchActive.value = active
    searchQuery.value = query
  }

  function setSelection(messageIds: number[], purpose: ChatSelectionPurpose = 'default') {
    selectedMessageIds.value = [...messageIds]
    selectionPurpose.value = purpose
  }

  function clearRoomOverlays() {
    lightboxMessageId.value = null
    contextMenuMessageId.value = null
  }

  return {
    isSearchActive,
    searchQuery,
    selectedMessageIds,
    selectionPurpose,
    lightboxMessageId,
    contextMenuMessageId,
    setSearch,
    setSelection,
    clearRoomOverlays,
  }
})

