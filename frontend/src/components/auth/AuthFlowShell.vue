<script setup lang="ts">
import { computed } from 'vue'

const props = withDefaults(
  defineProps<{
    title: string
    description?: string
    currentStep?: number
    totalSteps?: number
    focused?: boolean
  }>(),
  {
    description: '',
    currentStep: undefined,
    totalSteps: undefined,
    focused: false,
  },
)

const hasProgress = computed(
  () =>
    Number.isInteger(props.currentStep) &&
    Number.isInteger(props.totalSteps) &&
    Number(props.currentStep) > 0 &&
    Number(props.totalSteps) > 0 &&
    Number(props.currentStep) <= Number(props.totalSteps),
)

const progressSegments = computed(() =>
  hasProgress.value
    ? Array.from({ length: Number(props.totalSteps) }, (_, index) => index + 1)
    : [],
)

const persianNumber = new Intl.NumberFormat('fa-IR', { useGrouping: false })
const progressLabel = computed(() =>
  hasProgress.value
    ? `مرحله ${persianNumber.format(Number(props.currentStep))} از ${persianNumber.format(Number(props.totalSteps))}`
    : '',
)
</script>

<template>
  <main class="ui-v2-auth-flow" :class="{ 'ui-v2-auth-flow--focused': focused }">
    <header class="ui-v2-public-header">
      <strong>سامانه معاملات</strong>
      <img src="/uiux-v2-brand-mark.svg" width="28" height="28" alt="" aria-hidden="true" />
    </header>

    <div class="ui-v2-auth-flow__content">
      <div v-if="hasProgress" class="ui-v2-auth-progress" aria-label="پیشرفت فرایند">
        <span>{{ progressLabel }}</span>
        <span class="ui-v2-auth-progress__bars" aria-hidden="true">
          <span
            v-for="segment in progressSegments"
            :key="segment"
            :class="{ 'is-current': segment <= Number(currentStep) }"
          />
        </span>
      </div>

      <div class="ui-v2-auth-flow__heading">
        <h1>{{ title }}</h1>
        <p v-if="description">{{ description }}</p>
      </div>

      <slot />
    </div>
  </main>
</template>
