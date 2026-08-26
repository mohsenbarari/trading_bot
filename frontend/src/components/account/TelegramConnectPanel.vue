<script setup lang="ts">
import { Check, Send } from 'lucide-vue-next'

withDefaults(defineProps<{
  connected?: boolean
  loading?: boolean
  error?: string | null
}>(), {
  connected: false,
  loading: false,
  error: null,
})

const emit = defineEmits<{
  connect: []
}>()
</script>

<template>
  <button
    type="button"
    class="telegram-connect-panel"
    :class="{ 'is-connected': connected, 'is-loading': loading }"
    :disabled="connected || loading"
    @click="emit('connect')"
  >
    <span class="telegram-connect-panel__mark" aria-hidden="true">
      <Check v-if="connected" :size="22" />
      <Send v-else :size="22" />
    </span>

    <span class="telegram-connect-panel__copy">
      <strong>{{ connected ? 'تلگرام متصل است' : 'اتصال تلگرام' }}</strong>
      <span v-if="error" class="telegram-connect-panel__error">{{ error }}</span>
    </span>

    <span
      v-if="loading || connected"
      class="telegram-connect-panel__state"
      aria-hidden="true"
    >
      <span v-if="loading" class="telegram-connect-panel__spinner"></span>
      <span v-else>متصل</span>
    </span>
  </button>
</template>

<style scoped>
.telegram-connect-panel {
  --telegram-blue: var(--ds-telegram-500);

  width: 100%;
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) auto;
  align-items: center;
  gap: 0.9rem;
  min-height: var(--ds-native-row-min-height, 48px);
  padding: 0.75rem 1rem;
  border: 0;
  border-radius: 0;
  color: var(--ds-text-primary);
  font: inherit;
  text-align: right;
  cursor: pointer;
  background: transparent;
  box-shadow: none;
  transition: background 0.18s ease;
  -webkit-tap-highlight-color: transparent;
}

.telegram-connect-panel:hover:not(:disabled) {
  background: var(--ds-bg-inset);
}

.telegram-connect-panel:active:not(:disabled) {
  background: var(--ds-bg-hover);
}

.telegram-connect-panel:focus-visible {
  outline: 3px solid var(--ds-telegram-focus);
  outline-offset: 3px;
}

.telegram-connect-panel:disabled {
  cursor: default;
}

.telegram-connect-panel.is-connected {
  --telegram-blue: var(--ds-success-600);
}

.telegram-connect-panel.is-loading {
  opacity: 0.82;
}

.telegram-connect-panel__mark {
  width: 32px;
  height: 32px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: 8px;
  color: var(--ds-bg-card);
  background: var(--telegram-blue);
  border: 0;
  box-shadow: none;
  flex-shrink: 0;
}

.telegram-connect-panel__copy {
  min-width: 0;
  display: grid;
  gap: 0.22rem;
}

.telegram-connect-panel__copy strong {
  color: var(--ds-text-primary);
  font-size: var(--ds-font-md);
  font-weight: 700;
  line-height: 1.55;
}

.telegram-connect-panel__copy span,
.telegram-connect-panel__error {
  margin: 0;
  color: var(--ds-text-muted);
  font-size: var(--ds-font-sm);
  line-height: 1.8;
}

.telegram-connect-panel__error {
  color: var(--ds-danger-700);
  font-weight: 700;
}

.telegram-connect-panel__state {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-height: 1.25rem;
  padding: 0;
  border: 0;
  background: transparent;
  color: var(--ds-text-muted);
  font-size: var(--ds-font-sm);
  font-weight: 650;
  line-height: 1.7;
}

.telegram-connect-panel__spinner {
  width: 1rem;
  height: 1rem;
  border-radius: 999px;
  border: 2px solid var(--ds-telegram-border);
  border-top-color: var(--telegram-blue);
  animation: telegram-connect-spin 0.8s linear infinite;
}

@keyframes telegram-connect-spin {
  to {
    transform: rotate(360deg);
  }
}
</style>
