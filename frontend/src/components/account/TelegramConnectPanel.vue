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
      <strong>{{ connected ? 'تلگرام متصل است' : 'اتصال به بات تلگرام' }}</strong>
      <span>برای استفاده از امکانات اپ در بستر تلگرام ضربه بزنید!</span>
      <span v-if="error" class="telegram-connect-panel__error">{{ error }}</span>
    </span>

    <span class="telegram-connect-panel__state" aria-hidden="true">
      <span v-if="loading" class="telegram-connect-panel__spinner"></span>
      <span v-else>{{ connected ? 'متصل' : 'ضربه بزنید' }}</span>
    </span>
  </button>
</template>

<style scoped>
.telegram-connect-panel {
  --telegram-blue: var(--ds-telegram-500);
  --telegram-blue-dark: var(--ds-telegram-600);
  --telegram-blue-light: var(--ds-telegram-400);
  --telegram-blue-border: var(--ds-telegram-border);

  width: 100%;
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) auto;
  align-items: center;
  gap: 0.9rem;
  padding: 0.95rem;
  border: 1px solid var(--telegram-blue-border);
  border-radius: var(--ds-radius-lg);
  color: #ffffff;
  font: inherit;
  text-align: right;
  cursor: pointer;
  background:
    radial-gradient(circle at 12% 18%, rgba(255, 255, 255, 0.26), transparent 29%),
    linear-gradient(135deg, var(--telegram-blue-light), var(--telegram-blue) 48%, var(--telegram-blue-dark));
  box-shadow: 0 14px 32px var(--ds-telegram-shadow);
  transition: transform 0.18s ease, box-shadow 0.18s ease, filter 0.18s ease;
  -webkit-tap-highlight-color: transparent;
}

.telegram-connect-panel:hover:not(:disabled) {
  box-shadow: 0 18px 40px var(--ds-telegram-shadow-strong);
  filter: saturate(1.08);
}

.telegram-connect-panel:active:not(:disabled) {
  transform: scale(0.985);
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
  --telegram-blue-dark: var(--ds-trade-buy-text);
  --telegram-blue-light: #34d399;
  --telegram-blue-border: rgba(22, 101, 52, 0.22);
  box-shadow: 0 12px 28px rgba(22, 163, 74, 0.2);
}

.telegram-connect-panel.is-loading {
  opacity: 0.82;
}

.telegram-connect-panel__mark {
  width: 46px;
  height: 46px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: 50%;
  color: #ffffff;
  background: rgba(255, 255, 255, 0.18);
  border: 1px solid rgba(255, 255, 255, 0.32);
  box-shadow: 0 10px 22px rgba(8, 126, 184, 0.24);
  flex-shrink: 0;
}

.telegram-connect-panel__copy {
  min-width: 0;
  display: grid;
  gap: 0.22rem;
}

.telegram-connect-panel__copy strong {
  color: #ffffff;
  font-size: var(--ds-font-sm);
  font-weight: 900;
  line-height: 1.55;
}

.telegram-connect-panel__copy span,
.telegram-connect-panel__error {
  margin: 0;
  color: rgba(255, 255, 255, 0.88);
  font-size: var(--ds-font-xs);
  line-height: 1.8;
}

.telegram-connect-panel__error {
  color: #fee2e2;
  font-weight: 700;
}

.telegram-connect-panel__state {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 0.35rem;
  min-height: 2rem;
  padding: 0.24rem 0.72rem;
  border-radius: var(--ds-radius-full);
  border: 1px solid rgba(255, 255, 255, 0.34);
  background: rgba(255, 255, 255, 0.18);
  color: #ffffff;
  font-size: var(--ds-font-xs);
  font-weight: 900;
  white-space: nowrap;
}

.telegram-connect-panel__spinner {
  width: 1rem;
  height: 1rem;
  border-radius: 999px;
  border: 2px solid rgba(255, 255, 255, 0.45);
  border-top-color: #ffffff;
  animation: telegram-connect-spin 0.8s linear infinite;
}

@keyframes telegram-connect-spin {
  to {
    transform: rotate(360deg);
  }
}

@media (max-width: 640px) {
  .telegram-connect-panel {
    grid-template-columns: auto minmax(0, 1fr);
  }

  .telegram-connect-panel__state {
    grid-column: 1 / -1;
    justify-self: stretch;
  }
}
</style>
