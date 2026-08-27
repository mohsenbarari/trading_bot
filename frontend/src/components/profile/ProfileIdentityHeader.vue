<script setup lang="ts">
import { Pencil } from 'lucide-vue-next'
import { AppBackButton } from '../ui'
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
  titleTag?: 'h1' | 'p'
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
  titleTag: 'h1',
})

const emit = defineEmits<{
  back: []
  'pick-avatar': []
}>()
</script>

<template>
  <div class="header-row profile-header-row" data-test="profile-identity-header">
    <div class="profile-header-main">
      <AppBackButton
        v-if="!hideBackButton"
        class="profile-nav-back"
        :label="backLabel"
        @click="emit('back')"
      />
      <div v-else class="header-back-spacer" aria-hidden="true"></div>
      <div class="header-title">
        <component
          :is="titleTag"
          v-if="displayName"
          class="profile-identity-title"
          :title="displayName"
          dir="auto"
        >
          <slot name="title">{{ displayName }}</slot>
        </component>
        <component
          :is="titleTag"
          v-else-if="loading"
          class="profile-identity-title skeleton-text-header"
        >
          <span class="skeleton-box" style="width: 120px; height: 24px;"></span>
        </component>
        <component :is="titleTag" v-else class="profile-identity-title">پروفایل</component>
      </div>
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
        </div>
      </div>
    </div>
    <ProfilePresence
      v-if="showPresence && presenceStatus"
      :status="presenceStatus"
      :online="online"
      own
    />
  </div>
</template>

<style scoped>
.profile-header-row {
  display: flex;
  flex-direction: column;
  align-items: stretch;
  gap: 0.4rem;
  min-width: 0;
  z-index: auto;
  padding: 0 0 1rem;
  background: transparent;
}

.profile-header-main {
  display: grid;
  grid-template-columns: 3rem minmax(0, 1fr) auto;
  align-items: center;
  gap: 0.5rem;
  min-width: 0;
}

.profile-header-row > *,
.profile-header-main > * {
  min-width: 0;
}

.header-title {
  min-width: 0;
  justify-content: flex-start;
  text-align: start;
}

.header-title .profile-identity-title {
  margin: 0;
  min-width: 0;
  color: var(--ds-text-primary);
  font-size: 1.25rem;
  font-weight: 800;
  letter-spacing: -0.02em;
  line-height: 1.25;
  display: -webkit-box;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
  overflow: hidden;
  overflow-wrap: anywhere;
}

.header-title .profile-identity-title:dir(ltr) {
  -webkit-line-clamp: 1;
  word-break: break-all;
}

.header-back-spacer {
  width: 3rem;
  height: 3rem;
}

.profile-nav-back {
  justify-self: start;
  box-sizing: border-box;
  inline-size: 3rem;
  block-size: 3rem;
  min-inline-size: 3rem;
  min-block-size: 3rem;
}

.profile-avatar-stack {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 7px;
}

.profile-avatar-stack--header {
  position: relative;
  width: 64px;
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
  background: var(--ds-primary-100);
  color: var(--ds-primary-700);
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
  box-shadow: none;
}

.profile-avatar--readonly {
  box-shadow: none;
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
  background: var(--ds-bg-subtle);
}

@media (prefers-reduced-motion: reduce) {
  .profile-nav-back,
  .profile-avatar-button {
    transition: none;
  }
}
</style>
