<script setup lang="ts">
import { onErrorCaptured, ref, watch } from 'vue'

const props = defineProps<{
  messageId: number | string
  renderKey?: string | number | null
}>()

const hasError = ref(false)
const reportedErrorKey = ref<string | null>(null)

const errorKey = () => `${props.messageId}:${props.renderKey ?? ''}`

watch(() => [props.messageId, props.renderKey], () => {
  hasError.value = false
  reportedErrorKey.value = null
})

onErrorCaptured((error) => {
  hasError.value = true
  const nextErrorKey = errorKey()
  if (reportedErrorKey.value !== nextErrorKey) {
    reportedErrorKey.value = nextErrorKey
    console.warn('[messenger] message render failed', {
      messageId: props.messageId,
      error,
    })
  }
  return false
})
</script>

<template>
  <div v-if="hasError" class="message-render-fallback" role="status">
    این پیام قابل نمایش نیست
  </div>
  <slot v-else />
</template>

<style scoped>
.message-render-fallback {
  align-self: center;
  max-width: min(78%, 360px);
  margin: 6px 12px;
  padding: 10px 12px;
  border-radius: 12px;
  background: rgba(255, 255, 255, 0.92);
  color: #6b7280;
  font-size: 13px;
  line-height: 1.6;
  box-shadow: 0 4px 16px rgba(15, 23, 42, 0.08);
}
</style>

