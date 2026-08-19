<script setup lang="ts">
import { AppButton, AppErrorState, AppLoadingState } from '../ui'
import type { ProfileSurfaceStatus } from './types'

withDefaults(defineProps<{
  status?: ProfileSurfaceStatus
  errorTitle?: string
  errorMessage?: string
  loadingLabel?: string
}>(), {
  status: 'ready',
  errorTitle: 'دریافت پروفایل انجام نشد',
  errorMessage: '',
  loadingLabel: 'در حال دریافت پروفایل',
})

const emit = defineEmits<{
  retry: []
}>()
</script>

<template>
  <div class="profile-page-shell" data-test="profile-page-shell" :data-status="status">
    <slot name="header" />
    <AppLoadingState v-if="status === 'loading'" :label="loadingLabel" />
    <AppErrorState
      v-else-if="status === 'error'"
      class="error-state"
      :title="errorTitle"
      :message="errorMessage"
    >
      <template #actions>
        <AppButton class="retry-btn" type="button" variant="secondary" @click="emit('retry')">
          تلاش دوباره
        </AppButton>
      </template>
    </AppErrorState>
    <slot v-else />
  </div>
</template>

<style scoped>
.profile-page-shell {
  display: flex;
  flex-direction: column;
  gap: var(--ds-section-gap, 1rem);
  min-height: 100%;
  min-width: 0;
}
</style>
