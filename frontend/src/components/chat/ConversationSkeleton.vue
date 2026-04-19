<script setup lang="ts">
/**
 * ConversationSkeleton.vue
 * Telegram-style shimmer skeleton for conversation list loading state.
 */
defineProps<{
  count?: number
}>()
</script>

<template>
  <div class="skeleton-list">
    <div v-for="i in (count || 6)" :key="i" class="skeleton-item">
      <div class="skeleton-avatar shimmer"></div>
      <div class="skeleton-content">
        <div class="skeleton-row">
          <div class="skeleton-name shimmer" :style="{ width: (50 + Math.random() * 30) + '%' }"></div>
          <div class="skeleton-time shimmer"></div>
        </div>
        <div class="skeleton-preview shimmer" :style="{ width: (60 + Math.random() * 30) + '%' }"></div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.skeleton-list {
  padding: 0;
}

.skeleton-item {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px 16px;
}

.skeleton-avatar {
  width: 50px;
  height: 50px;
  min-width: 50px;
  border-radius: 50%;
  background: #e8e8e8;
}

.skeleton-content {
  flex: 1;
  min-width: 0;
}

.skeleton-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 8px;
}

.skeleton-name {
  height: 14px;
  border-radius: 4px;
  background: #e8e8e8;
}

.skeleton-time {
  width: 32px;
  height: 10px;
  border-radius: 4px;
  background: #e8e8e8;
}

.skeleton-preview {
  height: 12px;
  border-radius: 4px;
  background: #e8e8e8;
}

/* Shimmer animation */
.shimmer {
  background: linear-gradient(90deg, #e8e8e8 25%, #f5f5f5 50%, #e8e8e8 75%);
  background-size: 200% 100%;
  animation: shimmer 1.5s ease-in-out infinite;
}

@keyframes shimmer {
  0% { background-position: 200% 0; }
  100% { background-position: -200% 0; }
}
</style>
