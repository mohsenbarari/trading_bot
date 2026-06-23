<script setup lang="ts">
import { Check, Send } from 'lucide-vue-next'
import { AppButton } from '../ui'

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
  <section class="telegram-connect-panel" :class="{ 'is-connected': connected }">
    <div class="telegram-connect-panel__mark" aria-hidden="true">
      <Check v-if="connected" :size="22" />
      <Send v-else :size="22" />
    </div>

    <div class="telegram-connect-panel__copy">
      <strong>{{ connected ? 'تلگرام متصل است' : 'اتصال به بات تلگرام' }}</strong>
      <span>برای استفاده از امکانات اپ در بستر تلگرام ضربه بزنید!</span>
      <p v-if="error" class="telegram-connect-panel__error">{{ error }}</p>
    </div>

    <AppButton
      type="button"
      size="sm"
      class="telegram-connect-panel__button"
      :loading="loading"
      :disabled="connected"
      @click="!connected && emit('connect')"
    >
      <template #icon>
        <Check v-if="connected" :size="16" />
        <Send v-else :size="16" />
      </template>
      {{ connected ? 'متصل' : 'اتصال' }}
    </AppButton>
  </section>
</template>

<style scoped>
.telegram-connect-panel {
  --telegram-blue: #229ed9;
  --telegram-blue-dark: #168acd;
  --telegram-blue-soft: rgba(34, 158, 217, 0.11);
  --telegram-blue-border: rgba(34, 158, 217, 0.22);

  display: grid;
  grid-template-columns: auto minmax(0, 1fr) auto;
  align-items: center;
  gap: 0.9rem;
  padding: 0.85rem;
  border: 1px solid var(--telegram-blue-border);
  border-radius: var(--ds-radius-lg);
  background:
    linear-gradient(135deg, rgba(34, 158, 217, 0.12), rgba(255, 255, 255, 0.98) 58%),
    #ffffff;
  box-shadow: 0 8px 22px rgba(34, 158, 217, 0.1);
}

.telegram-connect-panel.is-connected {
  --telegram-blue: #16a34a;
  --telegram-blue-dark: #15803d;
  --telegram-blue-soft: rgba(22, 163, 74, 0.1);
  --telegram-blue-border: rgba(22, 163, 74, 0.2);
}

.telegram-connect-panel__mark {
  width: 46px;
  height: 46px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: 50%;
  color: #ffffff;
  background: linear-gradient(135deg, var(--telegram-blue), var(--telegram-blue-dark));
  box-shadow: 0 7px 18px rgba(34, 158, 217, 0.26);
  flex-shrink: 0;
}

.telegram-connect-panel__copy {
  min-width: 0;
  display: grid;
  gap: 0.22rem;
}

.telegram-connect-panel__copy strong {
  color: var(--ds-text-primary);
  font-size: var(--ds-font-sm);
  font-weight: 900;
  line-height: 1.55;
}

.telegram-connect-panel__copy span,
.telegram-connect-panel__error {
  margin: 0;
  color: var(--ds-text-secondary);
  font-size: var(--ds-font-xs);
  line-height: 1.8;
}

.telegram-connect-panel__error {
  color: var(--ds-danger-600);
}

.telegram-connect-panel__button {
  --ds-primary-500: var(--telegram-blue);
  --ds-primary-600: var(--telegram-blue-dark);
  white-space: nowrap;
}

.telegram-connect-panel__button:disabled {
  opacity: 0.82;
}

@media (max-width: 640px) {
  .telegram-connect-panel {
    grid-template-columns: auto minmax(0, 1fr);
  }

  .telegram-connect-panel__button {
    grid-column: 1 / -1;
    justify-self: stretch;
  }
}
</style>
