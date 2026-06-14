<script setup lang="ts">
import { Info } from 'lucide-vue-next'
import { onUnmounted, ref } from 'vue'

withDefaults(defineProps<{
  text: string
  label?: string
  buttonTest?: string
  noteTest?: string
  floating?: boolean
}>(), {
  label: 'توضیحات',
  buttonTest: undefined,
  noteTest: undefined,
  floating: false,
})

const isOpen = ref(false)
const timerId = ref<number | null>(null)

function clearTimer() {
  if (timerId.value !== null) {
    window.clearTimeout(timerId.value)
    timerId.value = null
  }
}

function showHelp() {
  isOpen.value = true
  clearTimer()
  timerId.value = window.setTimeout(() => {
    isOpen.value = false
    timerId.value = null
  }, 6000)
}

onUnmounted(clearTimer)
</script>

<template>
  <span class="help-popover" :class="{ 'help-popover--floating': floating }">
    <button
      type="button"
      class="help-popover-trigger"
      :data-test="buttonTest"
      :aria-label="label"
      @click.stop="showHelp"
    >
      <Info :size="18" />
    </button>
    <span v-if="isOpen" class="help-popover-note" :data-test="noteTest">
      {{ text }}
    </span>
  </span>
</template>

<style scoped>
.help-popover {
  position: relative;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex: 0 0 auto;
}

.help-popover--floating {
  position: absolute;
  top: 1rem;
  left: 1rem;
  z-index: 3;
}

.help-popover-trigger {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 2rem;
  height: 2rem;
  border: 1px solid rgba(15, 23, 42, 0.08);
  border-radius: 999px;
  background: rgba(248, 250, 252, 0.94);
  color: #475569;
  cursor: pointer;
  transition: border-color 0.2s ease, color 0.2s ease, background 0.2s ease, box-shadow 0.2s ease;
}

.help-popover-trigger:hover,
.help-popover-trigger:focus-visible {
  color: #0f766e;
  border-color: rgba(15, 118, 110, 0.24);
  background: rgba(240, 253, 250, 0.95);
  box-shadow: 0 8px 18px rgba(15, 118, 110, 0.1);
  outline: none;
}

.help-popover-note {
  position: absolute;
  top: calc(100% + 0.45rem);
  left: 0;
  width: min(17rem, calc(100vw - 2rem));
  padding: 0.72rem 0.85rem;
  border-radius: 14px;
  border: 1px solid rgba(15, 118, 110, 0.12);
  background: rgba(240, 253, 250, 0.98);
  color: #0f4c48;
  font-size: 0.78rem;
  font-weight: 700;
  line-height: 1.8;
  text-align: right;
  box-shadow: 0 14px 30px rgba(15, 118, 110, 0.14);
  white-space: normal;
  z-index: 20;
}

.help-popover-note::before {
  content: '';
  position: absolute;
  top: -6px;
  left: 12px;
  width: 12px;
  height: 12px;
  background: inherit;
  border-left: 1px solid rgba(15, 118, 110, 0.12);
  border-top: 1px solid rgba(15, 118, 110, 0.12);
  transform: rotate(45deg);
}
</style>
