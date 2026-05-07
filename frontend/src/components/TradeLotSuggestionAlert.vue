<script setup lang="ts">
import { Loader2, X } from 'lucide-vue-next'

const props = defineProps<{
  show: boolean
  title: string
  message: string
  availableLots: number[]
  busy?: boolean
  busyAmount?: number | null
}>()

defineEmits<{
  (e: 'close'): void
  (e: 'select-lot', amount: number): void
}>()
</script>

<template>
  <transition name="trade-suggestion-fade">
    <div v-if="props.show" class="trade-suggestion-overlay" @click.self="$emit('close')">
      <div class="trade-suggestion-card" role="alertdialog" aria-modal="true" :aria-label="props.title">
        <div class="trade-suggestion-header">
          <h3>{{ props.title }}</h3>
          <button type="button" class="trade-suggestion-close" @click="$emit('close')" aria-label="بستن">
            <X :size="18" />
          </button>
        </div>

        <p class="trade-suggestion-message">{{ props.message }}</p>

        <div v-if="props.availableLots.length > 0" class="trade-suggestion-actions">
          <button
            v-for="amount in props.availableLots"
            :key="amount"
            type="button"
            class="trade-suggestion-lot-btn"
            :disabled="props.busy"
            @click="$emit('select-lot', amount)"
          >
            <Loader2 v-if="props.busy && props.busyAmount === amount" class="animate-spin" :size="14" />
            <span>{{ amount.toLocaleString() }} عدد</span>
          </button>
        </div>

        <button type="button" class="trade-suggestion-dismiss" @click="$emit('close')">بعداً</button>
      </div>
    </div>
  </transition>
</template>

<style scoped>
.trade-suggestion-overlay {
  position: fixed;
  inset: 0;
  z-index: 120;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 1.25rem;
  background: rgba(15, 23, 42, 0.42);
  backdrop-filter: blur(10px);
}

.trade-suggestion-card {
  width: min(100%, 29rem);
  border-radius: 1.5rem;
  border: 1px solid rgba(251, 191, 36, 0.28);
  background:
    radial-gradient(circle at top right, rgba(251, 191, 36, 0.18), transparent 38%),
    linear-gradient(180deg, #fffdf7 0%, #ffffff 100%);
  box-shadow: 0 24px 60px rgba(15, 23, 42, 0.18);
  padding: 1.2rem;
}

.trade-suggestion-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 0.75rem;
  margin-bottom: 0.85rem;
}

.trade-suggestion-header h3 {
  margin: 0;
  font-size: 1.02rem;
  font-weight: 800;
  color: #92400e;
}

.trade-suggestion-close {
  border: none;
  background: rgba(255, 255, 255, 0.85);
  color: #78716c;
  width: 2rem;
  height: 2rem;
  border-radius: 999px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
}

.trade-suggestion-message {
  margin: 0;
  font-size: 0.95rem;
  line-height: 1.9;
  color: #334155;
  white-space: pre-line;
}

.trade-suggestion-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 0.7rem;
  margin-top: 1.1rem;
}

.trade-suggestion-lot-btn {
  border: none;
  border-radius: 999px;
  padding: 0.8rem 1.1rem;
  background: linear-gradient(135deg, #f59e0b 0%, #ea580c 100%);
  color: white;
  font-weight: 800;
  font-size: 0.9rem;
  display: inline-flex;
  align-items: center;
  gap: 0.45rem;
  box-shadow: 0 10px 24px rgba(234, 88, 12, 0.22);
}

.trade-suggestion-lot-btn:disabled {
  opacity: 0.75;
}

.trade-suggestion-dismiss {
  margin-top: 1rem;
  width: 100%;
  border: 1px solid rgba(148, 163, 184, 0.35);
  border-radius: 1rem;
  background: rgba(255, 255, 255, 0.92);
  color: #475569;
  font-weight: 700;
  padding: 0.78rem 1rem;
}

.trade-suggestion-fade-enter-active,
.trade-suggestion-fade-leave-active {
  transition: opacity 0.18s ease;
}

.trade-suggestion-fade-enter-from,
.trade-suggestion-fade-leave-to {
  opacity: 0;
}
</style>