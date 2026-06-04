<script setup lang="ts">
defineProps<{
  reply: {
    id: number
    sender_id: number
    content: string
    message_type: string
  }
  currentUserId: number | null
  selectedUserName: string
  isSent: boolean
}>()

defineEmits<{
  (event: 'scroll-to', messageId: number): void
}>()

function getReplyPreviewText(messageType: string, content: string) {
  if (messageType === 'image') return '🖼️ تصویر'
  if (messageType === 'video') return '📹 ویدیو'
  if (messageType === 'voice') return '🎤 پیام صوتی'
  if (messageType === 'sticker') return '😊 استیکر'
  if (messageType === 'location') return '📍 موقعیت'
  return content
}
</script>

<template>
  <div
    class="reply-context"
    :class="{ 'is-sent': isSent }"
    @click.stop="$emit('scroll-to', reply.id)"
  >
    <div class="reply-content">
      <span class="reply-author">
        {{ reply.sender_id === currentUserId ? 'شما' : selectedUserName }}
      </span>
      <span class="reply-text">
        {{ getReplyPreviewText(reply.message_type, reply.content) }}
      </span>
    </div>
  </div>
</template>

<style scoped>
.reply-context {
  border-right: 2px solid var(--messenger-chat-link, #3390ec);
  background: rgba(51, 144, 236, 0.08);
  border-radius: 4px;
  padding: 4px 8px;
  margin-bottom: 6px;
  cursor: pointer;
  display: flex;
  flex-direction: column;
  max-width: 100%;
  overflow: hidden;
}

.reply-context.is-sent {
  border-right: 2px solid var(--messenger-chat-success, #43a047);
  background: rgba(67, 160, 71, 0.1);
}

.reply-content {
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.reply-author {
  font-size: 13px;
  font-weight: 500;
  color: var(--messenger-chat-link, #3390ec);
}

.reply-context.is-sent .reply-author {
  color: #2ea043;
}

.reply-text {
  font-size: 13px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  opacity: 0.8;
  display: block;
  max-width: 100%;
}
</style>

