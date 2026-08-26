<script setup lang="ts">
import { ChevronLeft } from 'lucide-vue-next'
import { AppInsetGroup, AppListItem } from '../ui'
import type { ProfileActionItem } from './types'

withDefaults(defineProps<{
  title: string
  description?: string
  actions: ProfileActionItem[]
  sectionClass?: string
  loading?: boolean
}>(), {
  description: '',
  sectionClass: '',
  loading: false,
})

const emit = defineEmits<{
  select: [action: ProfileActionItem]
}>()
</script>

<template>
  <section v-if="actions.length > 0" class="profile-section" :class="sectionClass" data-test="profile-actions">
    <AppInsetGroup class="profile-menu-card" :title="title">
      <div class="profile-action-grid">
        <AppListItem
          v-for="action in actions"
          :key="action.key"
          class="profile-action-card"
          :class="[action.className, { 'profile-action-card--disabled': Boolean(action.disabled) }]"
          interactive
          :title="loading ? 'در حال بارگذاری...' : action.label"
          :description="action.description || undefined"
          :disabled="Boolean(action.disabled) || loading"
          @select="emit('select', action)"
        >
          <template v-if="action.icon" #leading>
            <span class="profile-action-card__icon" aria-hidden="true">
              <component :is="action.icon" :size="18" />
            </span>
          </template>
          <template #trailing>
            <ChevronLeft :size="18" aria-hidden="true" />
          </template>
        </AppListItem>
      </div>
      <slot />
    </AppInsetGroup>
  </section>
</template>

<style scoped>
.profile-action-grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  gap: 0;
  width: 100%;
}

.profile-action-card {
  width: 100%;
  min-height: var(--ds-native-row-min-height, 48px);
  border: 0;
  border-radius: 0;
  background: transparent;
  box-shadow: none;
}

.profile-action-card:active {
  transform: none;
}

.profile-action-card--disabled {
  opacity: 0.72;
}
</style>
