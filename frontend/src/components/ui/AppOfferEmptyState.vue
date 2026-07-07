<script setup lang="ts">
import { Search } from 'lucide-vue-next'
</script>

<template>
  <div class="empty-state" data-test="offers-empty-state">
    <div class="empty-icon">
      <Search :size="28" />
    </div>
    <p>هیچ لفظ فعالی یافت نشد.</p>
  </div>
</template>

<style scoped>
.empty-state {
  text-align: center;
  padding: 24px 16px;
  color: var(--ds-text-placeholder);
  font-size: 0.76rem;
}

.empty-icon {
  width: 42px;
  height: 42px;
  background: var(--ds-primary-50);
  border-radius: 12px;
  display: flex;
  align-items: center;
  justify-content: center;
  margin: 0 auto 8px;
  color: var(--ds-primary-500);
}
</style>
