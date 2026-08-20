<script setup lang="ts">
import { computed, useSlots } from 'vue'
import { buildChatFileUrl, getAvatarInitial } from '../../utils/chatFiles'

type BadgeTone = 'admin' | 'member' | 'creator' | 'target' | 'muted' | 'danger' | 'info'

export type ChatUserListRowBadge = {
  label: string
  tone?: BadgeTone
}

const props = withDefaults(defineProps<{
  tag?: 'div' | 'button'
  name: string
  subtitle?: string | null
  subtitleDir?: 'auto' | 'rtl' | 'ltr'
  avatarFileId?: string | null
  apiBaseUrl?: string
  interactive?: boolean
  selected?: boolean
  disabled?: boolean
  badges?: ChatUserListRowBadge[]
}>(), {
  tag: 'div',
  subtitle: null,
  subtitleDir: 'auto',
  avatarFileId: null,
  apiBaseUrl: '',
  interactive: false,
  selected: false,
  disabled: false,
  badges: () => [],
})

const emit = defineEmits<{
  (e: 'click', event: MouseEvent): void
}>()

const slots = useSlots()

const avatarUrl = computed(() => buildChatFileUrl(props.avatarFileId ?? null, props.apiBaseUrl))
const hasBadges = computed(() => props.badges.length > 0 || Boolean(slots.badges))
const hasSubtitle = computed(() => Boolean(props.subtitle) || Boolean(slots.subtitle))
const hasTrailing = computed(() => Boolean(slots.trailing))
const hasActions = computed(() => Boolean(slots.actions))

const rootClass = computed(() => ({
  'chat-user-row': true,
  'is-interactive': props.interactive,
  'is-selected': props.selected,
  'is-disabled': props.disabled,
  'has-actions': hasActions.value,
}))

const rootAttrs = computed(() => {
  if (props.tag !== 'button') return {}
  return {
    type: 'button',
    disabled: props.disabled,
  }
})

function handleClick(event: MouseEvent) {
  if (props.disabled) {
    event.preventDefault()
    return
  }
  emit('click', event)
}
</script>

<template>
  <component :is="tag" v-bind="rootAttrs" :class="rootClass" @click="handleClick">
    <div class="chat-user-row__avatar">
      <img v-if="avatarUrl" :src="avatarUrl" :alt="name" class="chat-user-row__avatar-image" />
      <template v-else>{{ getAvatarInitial(name) }}</template>
    </div>

    <div class="chat-user-row__copy">
      <div class="chat-user-row__title" :class="{ 'has-badges': hasBadges }">
        <span class="chat-user-row__name">{{ name }}</span>
        <span
          v-for="badge in badges"
          :key="`${badge.label}-${badge.tone || 'muted'}`"
          class="chat-user-row__badge"
          :class="badge.tone || 'muted'"
        >
          {{ badge.label }}
        </span>
        <slot name="badges" />
      </div>

      <div v-if="hasSubtitle" class="chat-user-row__subtitle" :dir="slots.subtitle ? undefined : subtitleDir">
        <slot name="subtitle">{{ subtitle }}</slot>
      </div>
    </div>

    <div v-if="hasTrailing" class="chat-user-row__trailing">
      <slot name="trailing" />
    </div>

    <div v-if="hasActions" class="chat-user-row__actions">
      <slot name="actions" />
    </div>
  </component>
</template>

<style scoped>
.chat-user-row {
  width: 100%;
  border: 0;
  border-radius: 0;
  background: var(--messenger-surface-panel, #ffffff);
  border-bottom: 1px solid var(--messenger-border-subtle, rgba(60, 60, 67, 0.18));
  padding: 12px 16px;
  display: flex;
  align-items: center;
  gap: 12px;
  text-align: right;
  box-shadow: none;
  min-height: var(--messenger-list-row-min-height, 64px);
  transition: background-color 0.2s ease;
}

.chat-user-row.is-interactive {
  cursor: pointer;
}

.chat-user-row.is-interactive:hover {
  background: var(--messenger-action-hover-bg, rgba(60, 60, 67, 0.08));
}

.chat-user-row.is-selected {
  border-color: transparent;
  background: var(--ds-primary-50, #fffbeb);
}

.chat-user-row.is-disabled {
  opacity: 0.7;
  cursor: default;
}

.chat-user-row__avatar {
  width: 48px;
  height: 48px;
  border-radius: 50%;
  overflow: hidden;
  background: var(--ds-primary-100, #fef3c7);
  color: var(--ds-primary-700, #b45309);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: 1rem;
  font-weight: 900;
  flex-shrink: 0;
}

.chat-user-row__avatar-image {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.chat-user-row__copy {
  min-width: 0;
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.chat-user-row__title {
  font-size: 0.96rem;
  font-weight: 900;
  color: #0f172a;
}

.chat-user-row__title.has-badges {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}

.chat-user-row__name {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.chat-user-row__subtitle {
  font-size: 0.76rem;
  color: #64748b;
  line-height: 1.7;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.chat-user-row__badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-height: 18px;
  padding: 0 7px;
  border-radius: 999px;
  font-size: 0.58rem;
  font-weight: 800;
  line-height: 1;
  direction: ltr;
}

.chat-user-row__badge.admin {
  background: rgba(245, 158, 11, 0.14);
  color: #b45309;
}

.chat-user-row__badge.member,
.chat-user-row__badge.muted {
  background: rgba(148, 163, 184, 0.16);
  color: #475569;
}

.chat-user-row__badge.creator {
  background: rgba(34, 197, 94, 0.12);
  color: #15803d;
}

.chat-user-row__badge.target {
  background: rgba(245, 158, 11, 0.14);
  color: var(--ds-primary-700, #b45309);
}

.chat-user-row__badge.danger {
  background: rgba(239, 68, 68, 0.12);
  color: #b91c1c;
}

.chat-user-row__badge.info {
  background: rgba(245, 158, 11, 0.14);
  color: var(--ds-primary-700, #b45309);
}

.chat-user-row__trailing {
  flex-shrink: 0;
  display: inline-flex;
  align-items: center;
  justify-content: center;
}

.chat-user-row__actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  justify-content: flex-end;
}

:deep(.chat-user-row__action-btn) {
  min-width: 82px;
  min-height: var(--ds-native-row-min-height, 48px);
  padding: 0 12px;
  border: 0;
  border-radius: 12px;
  background: rgba(226, 232, 240, 0.92);
  color: #334155;
  font: inherit;
  font-size: 0.74rem;
  font-weight: 800;
  line-height: 1;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  transition: background-color 0.18s ease, color 0.18s ease, transform 0.18s ease;
}

:deep(.chat-user-row__action-btn:hover) {
  transform: translateY(-1px);
}

:deep(.chat-user-row__action-btn--primary) {
  background: var(--ds-primary-100, #fef3c7);
  color: var(--ds-primary-700, #b45309);
}

:deep(.chat-user-row__action-btn--danger) {
  background: rgba(254, 226, 226, 0.96);
  color: #b91c1c;
}

:deep(.chat-user-row__action-btn:disabled) {
  opacity: 0.66;
  cursor: default;
  transform: none;
}

@media (max-width: 520px) {
  .chat-user-row.has-actions {
    flex-wrap: wrap;
  }

  .chat-user-row__actions {
    width: 100%;
    justify-content: flex-start;
  }

  :deep(.chat-user-row__action-btn) {
    min-width: 76px;
  }
}
</style>