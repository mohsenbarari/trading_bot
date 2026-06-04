<script setup lang="ts">
import { computed, defineAsyncComponent } from 'vue'
import { vAutoAnimate } from '@formkit/auto-animate/vue'
import MessengerLoadingScreen from '../MessengerLoadingScreen.vue'
import ChatEmptyState from '../ChatEmptyState.vue'
import ChatMessageItem from '../ChatMessageItem.vue'
import ChatInputBar from '../ChatInputBar.vue'
import ChatContextMenu from '../ChatContextMenu.vue'
import MessageRenderBoundary from '../messages/MessageRenderBoundary.vue'

const ChatVirtualTimeline = defineAsyncComponent(() => import('../ChatVirtualTimeline.vue'))
const ChatSearchGlobalList = defineAsyncComponent(() => import('../ChatSearchGlobalList.vue'))
const ChatSearchBottomBar = defineAsyncComponent(() => import('../ChatSearchBottomBar.vue'))
const AttachmentMenu = defineAsyncComponent(() => import('../AttachmentMenu.vue'))
const ChatForwardModal = defineAsyncComponent(() => import('../ChatForwardModal.vue'))
const ChatLightbox = defineAsyncComponent(() => import('../ChatLightbox.vue'))
const ChatLocationModal = defineAsyncComponent(() => import('../ChatLocationModal.vue'))

const props = defineProps<{
  state: any
  handlers: Record<string, any>
}>()

const useVirtualTimeline = computed(() => {
  return import.meta.env.VITE_MESSENGER_VIRTUAL_TIMELINE === 'true'
    && props.state?.selectedRoomKind === 'direct'
    && props.state?.timelineRenderBudget?.virtualizationCandidate === true
})
</script>

<template>
  <ChatEmptyState v-if="!state.selectedUserId" />

  <ChatSearchGlobalList
    v-if="state.isSearchActive && state.selectedUserId && state.showInChatSearchList"
    :searchResults="state.searchResults"
    :searchQuery="state.searchQuery"
    :conversations="state.sortedConversations"
    :currentUserId="state.currentUserId"
    @select-result="handlers.handleSearchResultClick"
  />

  <div v-else class="chat-content">
    <div v-if="state.isLoadingMessages" class="loading-state">
      <div v-if="state.prefersReducedMotion" class="compact-chat-loading" role="status">
        <span class="loading-spinner compact-spinner"></span>
        <span>در حال باز کردن گفتگو</span>
      </div>
      <MessengerLoadingScreen
        v-else
        mode="chat"
        title="در حال باز کردن گفتگو"
        subtitle="آخرین پیام‌ها با یک بارگذاری سبک و سریع آماده می‌شوند."
      />
    </div>

    <div
      v-else
      :class="['messages-container', { 'has-pinned-message': !!state.pinnedMessage }]"
      :ref="handlers.setMessagesContainer"
      @scroll.passive="handlers.handleMessagesScroll"
    >
      <div v-if="state.isLoadingOlderMessages" class="history-loading-indicator">
        <span class="history-loading-dot"></span>
        <span>در حال بارگذاری پیام‌های قبلی...</span>
      </div>

      <div v-if="state.messagePanelError" class="chat-panel-error" role="status">
        <div>
          <strong>دریافت گفتگو انجام نشد</strong>
          <p>{{ state.messagePanelError }}</p>
        </div>
        <button @click="handlers.retryLoadMessages">تلاش مجدد</button>
      </div>

      <div v-else-if="state.messages.length === 0" class="empty-state">
        <span>💬</span>
        <p>شروع گفتگو...</p>
      </div>

      <ChatVirtualTimeline
        v-if="useVirtualTimeline"
        :ref="handlers.setVirtualTimelineRef"
        :groups="state.groupedMessages"
        :state="state"
        :handlers="handlers"
      />

      <div
        v-else
        v-for="group in state.groupedMessages"
        :key="group.label"
        class="message-group"
        v-auto-animate
        v-memo="[group, state.searchQuery, state.isSelectionMode, state.activeAlbumSelectionId, state.selectionMemoKey]"
      >
        <div class="date-separator sticky-date">
          <span @click="handlers.scrollToTimelineGroup(group)">{{ group.label }}</span>
        </div>

        <template v-for="item in group.items" :key="item.id">
          <MessageRenderBoundary
            :messageId="item.id"
            :renderKey="`${state.searchQuery}:${state.isSelectionMode}:${state.selectionMemoKey}`"
          >
            <ChatMessageItem
              v-memo="[item, state.searchQuery, state.isSelectionMode, handlers.isAlbumInDownloadSelection(item), state.selectionMemoKey]"
              :msg="handlers.getTimelineItemMessage(item)"
              :isAlbum="handlers.isAlbumTimelineItem(item)"
              :albumItems="handlers.getTimelineItemAlbumItems(item)"
              :isAlbumDownloadMode="handlers.isAlbumInDownloadSelection(item)"
              :selectedAlbumDownloadMessageIds="state.selectedMessages"
              :currentUserId="state.currentUserId"
              :selectedUserName="state.selectedUserName"
              :selectedMessages="state.selectedMessages"
              :imageCache="state.imageCache"
              :isSelectionMode="state.isSelectionMode"
              :searchQuery="state.searchQuery"
              :isManagementMessage="state.isSelectedManagementRoom"
              @swipe-reply="handlers.handleReply"
              @select="handlers.handleGroupedItemSelection(item)"
              @click-message="handlers.handleMessageClick"
              @context-menu="handlers.showContextMenu"
              @scroll-to="handlers.scrollToMessage"
              @media-click="handlers.handleMediaInteraction"
              @location-click="handlers.handleLocationClick"
              @download="handlers.downloadMedia"
              @cancel-send="handlers.handleCancelSend"
              @cancel-download="handlers.handleCancelDownload"
              @reply-album-item="handlers.handleAlbumReplyItem"
              @forward-album-item="handlers.handleAlbumForwardItem"
              @delete-album-item="handlers.handleAlbumDeleteItem"
              @toggle-album-download-item="handlers.handleAlbumDownloadItemToggle"
              @toggle-reaction="handlers.handleMessageReactionToggle"
              @recovery-action="handlers.handleRecoveryAction"
              @open-public-profile="handlers.openPublicProfile"
              :on-load="() => handlers.hydrateRenderedMedia(item)"
            />
          </MessageRenderBoundary>
        </template>
      </div>

      <button
        v-if="state.showScrollButton"
        class="scroll-bottom-btn"
        :class="{ 'has-mention': state.unreadMentionMessages.length > 0 }"
        @click="handlers.handleScrollButtonClick"
      >
        <span v-if="state.unreadNewMessagesCount > 0" class="scroll-badge">{{ state.unreadNewMessagesCount }}</span>
        <span v-if="state.unreadMentionMessages.length > 0" class="scroll-mention-badge">@</span>
        <svg viewBox="0 0 24 24" fill="currentColor" width="24" height="24">
          <path d="M7.41 8.59L12 13.17l4.59-4.58L18 10l-6 6-6-6 1.41-1.41z"/>
        </svg>
      </button>
    </div>
  </div>

  <ChatSearchBottomBar
    v-if="state.selectedUserId && state.isSearchActive && state.searchResults.length > 0"
    :currentSearchIndex="state.currentSearchIndex"
    :totalResults="state.searchResults.length"
    :showInChatSearchList="state.showInChatSearchList"
    @next="handlers.nextSearchResult"
    @prev="handlers.prevSearchResult"
    @toggle-list="handlers.handleToggleInChatList"
  />

  <div v-else-if="state.selectedUserId && state.isAlbumDownloadSelectionMode" class="album-download-selection-bar">
    <button class="selection-action-btn" @click="handlers.clearSelection">
      <svg viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <line x1="18" y1="6" x2="6" y2="18"></line>
        <line x1="6" y1="6" x2="18" y2="18"></line>
      </svg>
      <span>انصراف</span>
    </button>
    <div class="album-download-selection-summary">
      {{ state.selectedMessages.length }} مدیا برای دانلود انتخاب شده
    </div>
    <button class="selection-action-btn primary" @click="handlers.handleDownloadSelectedAlbumMessages">
      <svg viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path>
        <polyline points="7 10 12 15 17 10"></polyline>
        <line x1="12" y1="15" x2="12" y2="3"></line>
      </svg>
      <span>دانلود</span>
    </button>
  </div>

  <div v-else-if="state.selectedUserId && state.isAlbumForwardSelectionMode" class="album-download-selection-bar">
    <button class="selection-action-btn" @click="handlers.clearSelection">
      <svg viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <line x1="18" y1="6" x2="6" y2="18"></line>
        <line x1="6" y1="6" x2="18" y2="18"></line>
      </svg>
      <span>انصراف</span>
    </button>
    <div class="album-download-selection-summary">
      {{ state.selectedMessages.length }} مدیا برای هدایت انتخاب شده
    </div>
    <button class="selection-action-btn primary" :disabled="state.selectedMessages.length === 0" @click="handlers.handleForwardSelectedAlbumMessages">
      <svg viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <polyline points="15 14 20 9 15 4"></polyline>
        <path d="M4 20v-7a4 4 0 0 1 4-4h12"></path>
      </svg>
      <span>هدایت</span>
    </button>
  </div>

  <div v-else-if="state.selectedUserId && state.isAlbumShareSelectionMode" class="album-download-selection-bar">
    <button class="selection-action-btn" @click="handlers.clearSelection">
      <svg viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <line x1="18" y1="6" x2="6" y2="18"></line>
        <line x1="6" y1="6" x2="18" y2="18"></line>
      </svg>
      <span>انصراف</span>
    </button>
    <div class="album-download-selection-summary">
      {{ state.selectedMessages.length }} مدیا برای اشتراک‌گذاری انتخاب شده
    </div>
    <button class="selection-action-btn primary" :disabled="state.selectedMessages.length === 0" @click="handlers.handleShareSelectedAlbumMessages">
      <svg viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <circle cx="18" cy="5" r="3"></circle>
        <circle cx="6" cy="12" r="3"></circle>
        <circle cx="18" cy="19" r="3"></circle>
        <line x1="8.59" y1="13.51" x2="15.42" y2="17.49"></line>
        <line x1="15.41" y1="6.51" x2="8.59" y2="10.49"></line>
      </svg>
      <span>اشتراک‌گذاری</span>
    </button>
  </div>

  <ChatInputBar
    v-else-if="state.selectedUserId"
    :ref="handlers.setChatInputBarRef"
    :modelValue="state.messageInput"
    :stickerPickerOpen="state.showStickerPicker"
    :editingMessage="state.editingMessage"
    :isSelectionMode="state.isSelectionMode"
    :replyingToMessage="state.replyingToMessage"
    :selectedUserName="state.selectedUserName"
    :currentUserId="state.currentUserId"
    :selectedMessagesCount="state.selectedMessages.length"
    :canDeleteSelected="state.canDeleteSelected"
    :canCopySelected="state.canCopySelected"
    :isSending="state.isSending"
    :isDeleted="state.isSelectedUserDeleted"
    :isReadOnly="state.isSelectedRoomReadOnly"
    :readOnlyBannerText="state.readOnlyBannerText"
    :disableRichComposer="state.isSelectedRoomReadOnly"
    :allowVoiceRecording="state.selectedRoomKind === 'direct'"
    :selectedMessages="state.selectedMessages"
    :isUploading="state.isUploading"
    @update:modelValue="handlers.updateMessageInput"
    @update:stickerPickerOpen="handlers.updateStickerPickerOpen"
    @cancel-edit="handlers.cancelEdit"
    @cancel-reply="handlers.cancelReply"
    @delete-selected="handlers.handleDeleteSelected"
    @reply-selected="handlers.handleReplySelected"
    @copy-selected="handlers.handleCopySelected"
    @forward-selected="handlers.openForwardModal"
    @toggle-attachment="handlers.handleToggleAttachment"
    @send-text="handlers.handleSendText"
    @send-voice="handlers.handleSendVoice"
    @typing="handlers.handleTypingForCurrentRoom"
  />

  <AttachmentMenu
    v-if="state.showAttachmentMenu || state.keepInactiveMessengerSurfacesMounted"
    :modelValue="state.showAttachmentMenu"
    :allowLocation="!state.isSelectedRoomReadOnly"
    @update:modelValue="handlers.updateAttachmentMenu"
    @select-media="handlers.handleAttachmentMediaSelection"
    @select-file="handlers.handleAttachmentFileSelection"
    @select-location="handlers.handleSendLocation"
  />

  <ChatForwardModal
    v-if="state.showForwardModal || state.keepInactiveMessengerSurfacesMounted"
    :showForwardModal="state.showForwardModal"
    :sortedConversations="state.sortedConversations"
    :includeChannels="true"
    :includeGroups="true"
    @close="handlers.closeForwardModal"
    @forward-to="handlers.forwardSelectedMessages"
  />

  <ChatContextMenu
    :menuState="state.contextMenu"
    :isAlbumSelection="state.contextMenu.messageIds.length > 1"
    :currentUserId="state.currentUserId"
    :canEdit="state.canEdit"
    :canDelete="state.canDelete"
    :canPin="state.canPinContextMessage"
    :isPinnedMessage="state.isContextMessagePinned"
    :availableReactions="state.availableMessageReactions"
    @react="handlers.handleContextMenuReaction"
    @reply="handlers.handleReplyMessage"
    @forward="handlers.handleForwardMessage"
    @copy="handlers.handleCopyMessage"
    @edit="handlers.handleEditMessage"
    @delete="handlers.handleDeleteMessage"
    @pin-message="handlers.handlePinMessage"
    @close="handlers.closeContextMenu"
    @save-media="handlers.handleSaveMedia"
    @save-album="handlers.handleSaveAlbum"
    @share="handlers.handleShareMessage"
    @share-album="handlers.handleShareAlbum"
  />

  <ChatLightbox
    v-if="state.lightboxMedia || state.keepInactiveMessengerSurfacesMounted"
    :lightboxMedia="state.lightboxMedia"
    :currentUserId="state.currentUserId"
    @close="handlers.closeLightbox"
    @navigate="handlers.handleLightboxNavigate"
    @reply="handlers.handleLightboxReply"
    @forward="handlers.handleLightboxForward"
    @share="handlers.handleLightboxShare"
    @delete="handlers.handleLightboxDelete"
  />

  <ChatLocationModal
    v-if="state.selectedLocation || state.keepInactiveMessengerSurfacesMounted"
    :location="state.selectedLocation"
    @close="handlers.closeLocationModal"
  />
</template>

<style scoped>
.chat-content {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  min-height: 0;
  position: relative;
}

.messages-container {
  flex: 1;
  overflow-y: auto;
  overflow-anchor: none;
  padding: 70px 16px 20px 16px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.messages-container.has-pinned-message {
  padding-top: 126px;
}

.history-loading-indicator {
  align-self: center;
  display: inline-flex;
  align-items: center;
  gap: 10px;
  padding: 8px 14px;
  margin-bottom: 6px;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.88);
  color: #5f6d79;
  font-size: 12px;
  box-shadow: 0 8px 18px rgba(26, 41, 53, 0.08);
  position: sticky;
  top: 8px;
  z-index: 8;
}

.history-loading-dot {
  width: 10px;
  height: 10px;
  border-radius: 50%;
  background: linear-gradient(135deg, #f59e0b, #3390ec);
  animation: history-loading-pulse calc(var(--messenger-motion-standard, 180ms) * 7) ease-in-out infinite;
}

@keyframes history-loading-pulse {
  0%,
  100% {
    transform: scale(0.8);
    opacity: 0.7;
  }
  50% {
    transform: scale(1);
    opacity: 1;
  }
}

.message-group {
  display: flex;
  flex-direction: column;
  width: 100%;
  gap: 6px;
}

.date-separator {
  display: flex;
  justify-content: center;
  margin: 16px 0;
  z-index: 5;
}

.sticky-date {
  position: sticky;
  top: 10px;
}

.date-separator span {
  background-color: rgba(0, 0, 0, 0.15);
  color: #fff;
  padding: 4px 12px;
  border-radius: 12px;
  font-size: 12px;
  text-shadow: 0 1px 2px rgba(0,0,0,0.1);
  backdrop-filter: blur(4px);
  cursor: pointer;
  user-select: none;
}

@media (prefers-color-scheme: light) {
  .date-separator span {
    background-color: rgba(0, 0, 0, 0.2);
    color: #fff;
    text-shadow: none;
    font-weight: 500;
    border: none;
  }
}

.loading-state {
  flex: 1;
  padding-top: 60px;
  width: 100%;
  display: flex;
  flex-direction: column;
  justify-content: center;
  align-items: center;
}

.compact-chat-loading {
  display: inline-flex;
  align-items: center;
  gap: 10px;
  padding: 12px 16px;
  color: var(--messenger-text-secondary);
  font-size: 0.92rem;
}

.compact-spinner {
  width: 18px;
  height: 18px;
}

.chat-panel-error {
  margin: 18px auto;
  width: min(92%, 420px);
  padding: 14px 16px;
  border: 1px solid rgba(220, 38, 38, 0.18);
  border-radius: 14px;
  background: rgba(255, 247, 237, 0.96);
  color: #7f1d1d;
  box-shadow: 0 10px 30px rgba(15, 23, 42, 0.08);
  display: flex;
  gap: 12px;
  align-items: center;
  justify-content: space-between;
}

.chat-panel-error strong {
  display: block;
  font-size: 14px;
  margin-bottom: 4px;
}

.chat-panel-error p {
  margin: 0;
  font-size: 13px;
  line-height: 1.6;
}

.chat-panel-error button {
  flex: 0 0 auto;
  border: none;
  border-radius: 10px;
  background: #b91c1c;
  color: #fff;
  padding: 8px 12px;
  font-size: 13px;
  cursor: pointer;
}

.scroll-bottom-btn {
  position: absolute;
  bottom: 80px;
  right: 20px;
  width: 40px;
  height: 40px;
  border-radius: 50%;
  background: #fff;
  border: none;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  color: #8e8e93;
  z-index: 999;
  transition: transform var(--messenger-motion-standard, 180ms), box-shadow var(--messenger-motion-standard, 180ms), background var(--messenger-motion-standard, 180ms);
  box-shadow: 0 2px 8px rgba(0,0,0,0.15);
}

.scroll-bottom-btn:hover {
  transform: translateY(-2px);
  box-shadow: 0 4px 12px rgba(0,0,0,0.2);
}

.scroll-badge {
  position: absolute;
  top: -5px;
  left: -5px;
  background: #ff3b30;
  color: white;
  border-radius: 10px;
  padding: 2px 6px;
  font-size: 11px;
  font-weight: bold;
  min-width: 18px;
  text-align: center;
  box-shadow: 0 2px 4px rgba(0,0,0,0.2);
}

.scroll-mention-badge {
  position: absolute;
  top: -5px;
  right: -5px;
  background: #7c3aed;
  color: white;
  border-radius: 50%;
  width: 18px;
  height: 18px;
  font-size: 11px;
  font-weight: bold;
  display: flex;
  align-items: center;
  justify-content: center;
  box-shadow: 0 2px 4px rgba(0,0,0,0.2);
  animation: pulse-mention calc(var(--messenger-motion-standard, 180ms) * 11) infinite;
}

.scroll-bottom-btn.has-mention {
  border: 1.5px solid #7c3aed;
  color: #7c3aed;
}

@keyframes pulse-mention {
  0% { transform: scale(1); }
  50% { transform: scale(1.05); }
  100% { transform: scale(1); }
}

.selection-action-btn {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  background: none;
  border: none;
  color: #8e8e93;
  font-size: 11px;
  font-weight: 500;
  gap: 4px;
  padding: 6px 16px;
  border-radius: 8px;
  cursor: pointer;
  transition: opacity var(--messenger-motion-standard, 180ms), background var(--messenger-motion-standard, 180ms);
}

.selection-action-btn:hover {
  background: rgba(0,0,0,0.05);
  color: #000;
}

.selection-action-btn svg {
  margin-bottom: 2px;
}

.selection-action-btn.primary {
  color: #3390ec;
}

.selection-action-btn.primary:hover {
  background: rgba(51, 144, 236, 0.1);
  color: #1d6fc2;
}

.album-download-selection-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  width: 100%;
  padding: 10px 14px;
  background: rgba(255, 255, 255, 0.98);
  border-top: 1px solid rgba(0, 0, 0, 0.06);
  min-height: 60px;
}

.album-download-selection-summary {
  flex: 1;
  text-align: center;
  font-size: 13px;
  font-weight: 600;
  color: #374151;
}
</style>
