<script setup lang="ts">
import { ChevronLeft, Pencil } from 'lucide-vue-next'
import { AppIconButton } from '../ui'
import ProfilePresence from './ProfilePresence.vue'

withDefaults(defineProps<{
  displayName: string
  avatarUrl?: string | null
  avatarInitial?: string
  editable?: boolean
  avatarBusy?: boolean
  showPresence?: boolean
  presenceStatus?: string | null
  online?: boolean
  hideBackButton?: boolean
  backLabel?: string
  loading?: boolean
}>(), {
  avatarUrl: null,
  avatarInitial: '',
  editable: false,
  avatarBusy: false,
  showPresence: false,
  presenceStatus: null,
  online: false,
  hideBackButton: false,
  backLabel: 'بازگشت',
  loading: false,
})

const emit = defineEmits<{
  back: []
  'pick-avatar': []
}>()
</script>

<template>
  <div class="header-row profile-header-row" data-test="profile-identity-header">
    <div class="header-spacer">
      <div v-if="displayName || avatarUrl || editable || loading" class="profile-avatar-stack profile-avatar-stack--header">
        <button
          v-if="editable"
          type="button"
          class="profile-avatar profile-avatar-button profile-avatar-button--editable"
          data-test="profile-avatar-trigger"
          :disabled="avatarBusy"
          :aria-label="avatarUrl ? 'تغییر آواتار' : 'افزودن آواتار'"
          @click="emit('pick-avatar')"
        >
          <img v-if="avatarUrl" :src="avatarUrl" :alt="displayName" class="profile-avatar-image" />
          <template v-else>{{ avatarInitial }}</template>
          <span class="profile-avatar-edit-indicator" aria-hidden="true">
            <Pencil :size="12" />
          </span>
          <div v-if="avatarBusy" class="profile-avatar-busy">در حال ذخیره...</div>
        </button>
        <div v-else class="profile-avatar profile-avatar--readonly" data-test="profile-avatar-readonly">
          <img v-if="avatarUrl" :src="avatarUrl" :alt="displayName" class="profile-avatar-image" />
          <template v-else>{{ avatarInitial || '—' }}</template>
        </div>
        <ProfilePresence
          v-if="showPresence && presenceStatus"
          :status="presenceStatus"
          :online="online"
          own
        />
      </div>
    </div>
    <div class="header-title">
      <h2 v-if="displayName">
        <slot name="title">{{ displayName }}</slot>
      </h2>
      <h2 v-else-if="loading" class="skeleton-text-header">
        <div class="skeleton-box" style="width: 120px; height: 24px;"></div>
      </h2>
      <h2 v-else>پروفایل</h2>
    </div>
    <AppIconButton
      v-if="!hideBackButton"
      class="profile-nav-back"
      :label="backLabel"
      @click="emit('back')"
    >
      <ChevronLeft :size="20" />
    </AppIconButton>
  </div>
</template>

<style scoped>
.profile-header-row {
  /*
   * Keep the title track explicitly shrinkable. A bare `1fr` has an automatic
   * minimum, so a long account name could widen the whole route at 360px and
   * push the back control outside the viewport.
   */
  grid-template-columns: minmax(4rem, 5.5rem) minmax(0, 1fr) minmax(2.75rem, 5.5rem);
  align-items: center;
  min-width: 0;
  padding-bottom: 24px;
}

.profile-header-row > * {
  min-width: 0;
}

.header-title {
  min-width: 0;
}

.header-title h2 {
  margin: 0;
  overflow-wrap: anywhere;
  word-break: break-word;
}

.profile-nav-back {
  justify-self: end;
  box-sizing: border-box;
  inline-size: 2.75rem;
  block-size: 2.75rem;
  min-inline-size: 2.75rem;
  min-block-size: 2.75rem;
  border: 1px solid var(--ds-border-medium);
  border-radius: var(--ds-radius-full);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  background: var(--ds-bg-card);
  color: var(--ds-text-primary);
  box-shadow: var(--ds-shadow-sm);
  cursor: pointer;
  transition: background 0.16s ease, border-color 0.16s ease, box-shadow 0.16s ease, transform 0.16s ease;
}

.profile-nav-back:hover {
  background: var(--ds-bg-hover);
  border-color: var(--ds-primary-200);
  box-shadow: var(--ds-shadow-md);
}

.profile-nav-back:active {
  transform: translateY(1px);
}

.profile-nav-back:focus-visible {
  outline: 3px solid rgba(51, 144, 236, 0.22);
  outline-offset: 2px;
}

.profile-avatar-stack {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 7px;
}

.profile-avatar-stack--header {
  position: relative;
  width: 88px;
  height: 64px;
  padding-top: 0;
}

.profile-avatar {
  position: relative;
  width: 92px;
  height: 92px;
  border-radius: 50%;
  overflow: hidden;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(135deg, var(--ds-telegram-500), var(--ds-info-500) 58%, var(--ds-primary-500) 100%);
  color: var(--ds-bg-card);
  font-size: 2rem;
  font-weight: 900;
  flex-shrink: 0;
}

.profile-avatar-stack--header .profile-avatar {
  width: 64px;
  height: 64px;
  font-size: 1.35rem;
}

.profile-avatar-button {
  border: 0;
  padding: 0;
  appearance: none;
  cursor: pointer;
}

.profile-avatar-button:disabled {
  cursor: wait;
}

.profile-avatar-button--editable {
  box-shadow: var(--ds-shadow-lg);
}

.profile-avatar--readonly {
  box-shadow: var(--ds-shadow-md);
}

.profile-avatar-edit-indicator {
  position: absolute;
  left: 50%;
  bottom: 3px;
  width: 18px;
  height: 18px;
  transform: translateX(-50%);
  border-radius: 999px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  background: rgba(15, 23, 42, 0.86);
  color: var(--ds-bg-card);
  border: 1px solid rgba(255, 255, 255, 0.85);
  box-shadow: var(--ds-shadow-md);
}

.profile-avatar-image {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.profile-avatar-busy {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(15, 23, 42, 0.38);
  color: var(--ds-bg-card);
  font-size: 0.72rem;
  font-weight: 700;
}

.skeleton-box {
  border-radius: 8px;
  background: var(--ds-bg-muted, #e5e7eb);
}

@media (prefers-reduced-motion: reduce) {
  .profile-nav-back,
  .profile-avatar-button {
    transition: none;
  }
}
</style>
