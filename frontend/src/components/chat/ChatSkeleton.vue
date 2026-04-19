<script setup lang="ts">
/**
 * ChatSkeleton.vue
 * Telegram-style shimmer skeleton for chat message loading state.
 * Alternates between sent and received message layouts.
 */
defineProps<{
  count?: number
}>()
</script>

<template>
  <div class="chat-skeleton">
    <div v-for="i in (count || 8)" :key="i" class="skel-msg" :class="i % 3 === 0 ? 'skel-sent' : 'skel-received'">
      <div class="skel-bubble shimmer" :style="{ width: (35 + Math.random() * 35) + '%' }">
        <div class="skel-text shimmer-inner" :style="{ width: '90%' }"></div>
        <div v-if="i % 4 !== 0" class="skel-text shimmer-inner short" :style="{ width: (40 + Math.random() * 30) + '%' }"></div>
        <div class="skel-meta shimmer-inner"></div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.chat-skeleton {
  padding: 12px 16px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.skel-msg {
  display: flex;
}

.skel-sent {
  justify-content: flex-end;
}

.skel-received {
  justify-content: flex-start;
}

.skel-bubble {
  padding: 10px 12px;
  border-radius: 16px;
  min-height: 40px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.skel-sent .skel-bubble {
  background: #e3f0fd;
  border-bottom-right-radius: 4px;
}

.skel-received .skel-bubble {
  background: #f0f0f0;
  border-bottom-left-radius: 4px;
}

.skel-text {
  height: 10px;
  border-radius: 4px;
}

.skel-text.short {
  height: 10px;
}

.skel-meta {
  width: 40px;
  height: 8px;
  border-radius: 4px;
  align-self: flex-end;
}

.skel-sent .shimmer-inner {
  background: linear-gradient(90deg, #cce4f7 25%, #dceefb 50%, #cce4f7 75%);
  background-size: 200% 100%;
  animation: shimmer 1.5s ease-in-out infinite;
}

.skel-received .shimmer-inner {
  background: linear-gradient(90deg, #e0e0e0 25%, #eeeeee 50%, #e0e0e0 75%);
  background-size: 200% 100%;
  animation: shimmer 1.5s ease-in-out infinite;
}

.shimmer {
  background-size: 200% 100%;
  animation: shimmer 1.5s ease-in-out infinite;
}

@keyframes shimmer {
  0% { background-position: 200% 0; }
  100% { background-position: -200% 0; }
}
</style>
