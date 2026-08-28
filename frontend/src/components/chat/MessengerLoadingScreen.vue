<script setup lang="ts">
const props = withDefaults(defineProps<{
  mode?: 'list' | 'chat'
  title?: string
  subtitle?: string
}>(), {
  mode: 'list',
  title: undefined,
  subtitle: undefined,
})

const listRows = ['78%', '56%', '70%', '48%', '64%', '52%']
const chatRows = [
  { side: 'received', width: '68%' },
  { side: 'sent', width: '48%' },
  { side: 'received', width: '60%' },
  { side: 'sent', width: '40%' },
  { side: 'received', width: '54%' },
]
</script>

<template>
  <div class="messenger-loader" :class="`mode-${props.mode}`" role="status" :aria-label="props.title || 'در حال بارگذاری…'">
    <p class="loader-title">{{ props.title || 'در حال بارگذاری…' }}</p>

    <div v-if="props.mode === 'list'" class="preview-list">
      <div v-for="(row, index) in listRows" :key="row" class="list-item-ghost">
        <div class="avatar-ghost skeleton-wave"></div>
        <div class="list-copy-ghost">
          <div class="line-ghost skeleton-wave" :style="{ width: row }"></div>
          <div class="line-ghost small skeleton-wave" :style="{ width: listRows[(index + 1) % listRows.length] }"></div>
        </div>
      </div>
    </div>

    <div v-else class="preview-chat">
      <div
        v-for="bubble in chatRows"
        :key="`${bubble.side}-${bubble.width}`"
        class="bubble-row-ghost"
        :class="bubble.side"
      >
        <div class="bubble-ghost skeleton-wave" :style="{ width: bubble.width }"></div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.messenger-loader {
  width: 100%;
  min-height: 100%;
  display: flex;
  flex-direction: column;
  background: var(--messenger-surface-page, #f2f2f7);
}

.loader-title {
  margin: 0;
  padding: 12px 16px 8px;
  color: var(--messenger-text-muted, #8e8e93);
  font-size: 0.82rem;
  font-weight: 650;
}

.preview-list,
.preview-chat {
  width: 100%;
  display: flex;
  flex-direction: column;
}

.list-item-ghost {
  display: flex;
  align-items: center;
  gap: 12px;
  min-height: 64px;
  padding: 10px 16px;
  border-bottom: 1px solid var(--messenger-border-subtle, rgba(60, 60, 67, 0.12));
}

.avatar-ghost {
  width: 44px;
  height: 44px;
  min-width: 44px;
  border-radius: 50%;
}

.list-copy-ghost {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.line-ghost {
  height: 10px;
  border-radius: 4px;
}

.line-ghost.small {
  height: 8px;
  opacity: 0.78;
}

.preview-chat {
  gap: 10px;
  padding: 16px;
}

.bubble-row-ghost {
  display: flex;
}

.bubble-row-ghost.sent {
  justify-content: flex-end;
}

.bubble-row-ghost.received {
  justify-content: flex-start;
}

.bubble-ghost {
  height: 36px;
  border-radius: 18px;
}

.bubble-row-ghost.sent .bubble-ghost {
  border-bottom-right-radius: 6px;
}

.bubble-row-ghost.received .bubble-ghost {
  border-bottom-left-radius: 6px;
}

.skeleton-wave {
  background-color: rgba(60, 60, 67, 0.1);
  background-image: linear-gradient(110deg, rgba(60, 60, 67, 0.08) 8%, rgba(255, 255, 255, 0.72) 18%, rgba(60, 60, 67, 0.08) 33%);
  background-size: 200% 100%;
  animation: shimmer 1.4s linear infinite;
}

@keyframes shimmer {
  to {
    background-position-x: -200%;
  }
}

@media (prefers-reduced-motion: reduce) {
  .skeleton-wave {
    animation: none;
  }
}
</style>
