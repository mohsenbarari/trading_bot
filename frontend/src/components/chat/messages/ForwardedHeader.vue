<script setup lang="ts">
defineProps<{
  name: string
  canOpenProfile: boolean
  isSent: boolean
}>()

defineEmits<{
  (event: 'open-profile'): void
}>()
</script>

<template>
  <div class="forwarded-banner" :class="{ 'is-sent': isSent, 'is-received': !isSent }">
    <span class="forward-icon">
      <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <polyline points="15 14 20 9 15 4"></polyline>
        <path d="M4 20v-7a4 4 0 0 1 4-4h12"></path>
      </svg>
    </span>
    <div class="forward-content">
      <span class="forward-title">پیام هدایت شده</span>
      <button
        v-if="canOpenProfile"
        type="button"
        class="forward-text forward-link"
        data-context-ignore
        data-swipe-ignore
        @click.stop="$emit('open-profile')"
      >
        از {{ name }}
      </button>
      <span v-else class="forward-text">از {{ name }}</span>
    </div>
  </div>
</template>

<style scoped>
.forwarded-banner {
  font-size: 13px;
  color: var(--messenger-text-muted, #64748b);
  margin-bottom: 2px;
  display: flex;
  align-items: center;
  gap: 4px;
}

.forwarded-banner.is-sent {
  color: var(--messenger-chat-success, #43a047);
}

.forward-icon {
  color: var(--messenger-chat-link, #3390ec);
}

.forwarded-banner.is-sent .forward-icon {
  color: var(--messenger-chat-success, #43a047);
}

.forward-content {
  display: flex;
  flex-direction: column;
}

.forward-title {
  font-size: 13px;
  font-weight: 500;
  color: var(--messenger-chat-link, #3390ec);
  line-height: 1.2;
}

.forwarded-banner.is-sent .forward-title {
  color: var(--messenger-chat-success, #43a047);
}

.forward-text {
  font-size: 13px;
  color: inherit;
  opacity: 0.8;
  line-height: 1.2;
}

.forward-link {
  appearance: none;
  background: none;
  border: none;
  padding: 0;
  margin: 0;
  text-align: right;
  font: inherit;
  cursor: pointer;
}

.forward-link:hover {
  opacity: 1;
}
</style>
