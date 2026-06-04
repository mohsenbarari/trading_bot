<script setup lang="ts">
import { defineAsyncComponent } from 'vue'
import ChatConversationList from '../ChatConversationList.vue'

const ChatSearchGlobalList = defineAsyncComponent(() => import('../ChatSearchGlobalList.vue'))

defineProps<{
  isSearchActive: boolean
  selectedUserId: number | null
  searchResults: any[]
  searchQuery: string
  conversations: any[]
  currentUserId: number
  typingUsers: Record<number, boolean>
  activityTextByConversation: Record<number, string>
  apiBaseUrl: string
  canStartNewConversation: boolean
}>()

defineEmits<{
  (event: 'select-result', payload: any): void
  (event: 'select-conversation', conversation: any): void
  (event: 'conversation-action', payload: any): void
  (event: 'new-conversation'): void
}>()
</script>

<template>
  <ChatSearchGlobalList
    v-if="isSearchActive && !selectedUserId"
    :searchResults="searchResults"
    :searchQuery="searchQuery"
    :conversations="conversations"
    :currentUserId="currentUserId"
    @select-result="$emit('select-result', $event)"
  />

  <ChatConversationList
    v-else-if="!selectedUserId && !isSearchActive"
    :conversations="conversations"
    :selectedUserId="selectedUserId"
    :typingUsers="typingUsers"
    :activityTextByConversation="activityTextByConversation"
    :apiBaseUrl="apiBaseUrl"
    :canStartNewConversation="canStartNewConversation"
    @select-conversation="$emit('select-conversation', $event)"
    @conversation-action="$emit('conversation-action', $event)"
    @new-conversation="$emit('new-conversation')"
  />
</template>
