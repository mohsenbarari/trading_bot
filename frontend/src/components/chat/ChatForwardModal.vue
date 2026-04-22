<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { apiFetchJson } from '../../utils/auth'
import type { ChatForwardTarget, Conversation } from '../../types/chat'

type ForwardUser = {
  id: number
  account_name: string
  mobile_number: string
}

type ForwardTargetCandidate = ChatForwardTarget & {
  key: string
  isConversation: boolean
  conversationIndex: number | null
  searchText: string
}

const USER_FETCH_LIMIT = 5000
const MAX_FORWARD_TARGETS = 10

const props = defineProps<{
  showForwardModal: boolean
  sortedConversations: Conversation[]
}>()

const emit = defineEmits<{
  (e: 'close'): void
  (e: 'forward-to', targets: ChatForwardTarget[]): void
}>()

const searchQuery = ref('')
const isLoading = ref(false)
const loadError = ref('')
const allUsers = ref<ForwardUser[]>([])
const selectedTargets = ref<Map<string, ChatForwardTarget>>(new Map())
const limitFlash = ref(false)
let fetchSequence = 0
let limitFlashTimer: ReturnType<typeof setTimeout> | null = null

function normalizeSearchValue(value: string | null | undefined) {
  return (value || '').trim().toLowerCase()
}

function getAvatarInitial(name: string) {
  return name ? name.charAt(0).toUpperCase() : '?'
}

function buildSearchText(parts: Array<string | null | undefined>) {
  return normalizeSearchValue(parts.filter(Boolean).join(' '))
}

async function loadForwardUsers() {
  const requestId = ++fetchSequence
  isLoading.value = true
  loadError.value = ''

  try {
    const users = await apiFetchJson(`/api/users-public/search?limit=${USER_FETCH_LIMIT}`) as ForwardUser[]
    if (requestId !== fetchSequence) return
    allUsers.value = Array.isArray(users) ? users : []
  } catch (error) {
    if (requestId !== fetchSequence) return
    loadError.value = error instanceof Error ? error.message : 'خطا در دریافت کاربران'
  } finally {
    if (requestId === fetchSequence) {
      isLoading.value = false
    }
  }
}

watch(() => props.showForwardModal, (visible) => {
  if (visible) {
    searchQuery.value = ''
    selectedTargets.value = new Map()
    limitFlash.value = false
    void loadForwardUsers()
    return
  }

  searchQuery.value = ''
  loadError.value = ''
  selectedTargets.value = new Map()
  limitFlash.value = false
  if (limitFlashTimer) {
    clearTimeout(limitFlashTimer)
    limitFlashTimer = null
  }
})

const orderedTargets = computed<ForwardTargetCandidate[]>(() => {
  const targets: ForwardTargetCandidate[] = []
  const seenIds = new Set<number>()
  const userMap = new Map(allUsers.value.map(user => [user.id, user]))

  props.sortedConversations.forEach((conversation, index) => {
    if (conversation.other_user_is_deleted) return

    const user = userMap.get(conversation.other_user_id)
    const title = user?.account_name || conversation.other_user_name
    const subtitle = user?.mobile_number || null

    targets.push({
      key: `user-${conversation.other_user_id}`,
      kind: 'user',
      id: conversation.other_user_id,
      title,
      subtitle,
      isConversation: true,
      conversationIndex: index,
      searchText: buildSearchText([title, subtitle]),
    })

    seenIds.add(conversation.other_user_id)
  })

  const remainingUsers = allUsers.value
    .filter(user => !seenIds.has(user.id))
    .sort((left, right) => left.account_name.localeCompare(right.account_name, 'fa'))

  remainingUsers.forEach((user) => {
    targets.push({
      key: `user-${user.id}`,
      kind: 'user',
      id: user.id,
      title: user.account_name,
      subtitle: user.mobile_number,
      isConversation: false,
      conversationIndex: null,
      searchText: buildSearchText([user.account_name, user.mobile_number]),
    })
  })

  return targets
})

const filteredTargets = computed(() => {
  const query = normalizeSearchValue(searchQuery.value)
  if (!query) return orderedTargets.value
  return orderedTargets.value.filter(target => target.searchText.includes(query))
})

const recentTargets = computed(() => filteredTargets.value.filter(target => target.isConversation))
const otherTargets = computed(() => filteredTargets.value.filter(target => !target.isConversation))

const selectedCount = computed(() => selectedTargets.value.size)
const canAddMore = computed(() => selectedCount.value < MAX_FORWARD_TARGETS)
const selectedList = computed(() => Array.from(selectedTargets.value.values()))

function isTargetSelected(target: ForwardTargetCandidate) {
  return selectedTargets.value.has(target.key)
}

function flashLimit() {
  limitFlash.value = true
  if (limitFlashTimer) clearTimeout(limitFlashTimer)
  limitFlashTimer = setTimeout(() => {
    limitFlash.value = false
    limitFlashTimer = null
  }, 900)
}

function toggleTarget(target: ForwardTargetCandidate) {
  const next = new Map(selectedTargets.value)
  if (next.has(target.key)) {
    next.delete(target.key)
  } else {
    if (next.size >= MAX_FORWARD_TARGETS) {
      flashLimit()
      return
    }
    next.set(target.key, {
      kind: target.kind,
      id: target.id,
      title: target.title,
      subtitle: target.subtitle,
    })
  }
  selectedTargets.value = next
}

function removeSelected(key: string) {
  if (!selectedTargets.value.has(key)) return
  const next = new Map(selectedTargets.value)
  next.delete(key)
  selectedTargets.value = next
}

function confirmForward() {
  if (selectedList.value.length === 0) return
  emit('forward-to', selectedList.value)
}
</script>

<template>
  <Teleport to="body">
    <Transition name="modal-slide">
      <div v-if="showForwardModal" class="forward-modal-overlay" @click="emit('close')">
        <div class="forward-modal" @click.stop>
          <div class="forward-modal-header">
            <div class="header-copy">
              <h3>هدایت پیام</h3>
              <p>
                تا ۱۰ گفتگو/کاربر را انتخاب کنید
                <span v-if="selectedCount > 0" class="header-count" :class="{ 'is-flash': limitFlash }">
                  ({{ selectedCount }}/{{ MAX_FORWARD_TARGETS }})
                </span>
              </p>
            </div>
            <button class="close-btn" @click="emit('close')">✕</button>
          </div>

          <div v-if="selectedList.length > 0" class="forward-modal-chips">
            <button
              v-for="target in selectedList"
              :key="target.kind + '-' + target.id"
              class="selected-chip"
              @click="removeSelected(target.kind + '-' + target.id)"
            >
              <span class="chip-title">{{ target.title }}</span>
              <span class="chip-remove">✕</span>
            </button>
          </div>

          <div class="forward-modal-search">
            <input
              v-model="searchQuery"
              type="text"
              placeholder="جستجو با نام کاربری یا شماره تماس..."
              class="forward-search-input"
            />
          </div>

          <div class="forward-modal-body">
            <div v-if="isLoading" class="forward-modal-state">در حال دریافت کاربران...</div>

            <div v-else-if="loadError && filteredTargets.length === 0" class="forward-modal-state error">
              <span>{{ loadError }}</span>
              <button class="retry-btn" @click="loadForwardUsers">تلاش مجدد</button>
            </div>

            <div v-else-if="filteredTargets.length === 0" class="forward-modal-state empty">
              کاربری یافت نشد
            </div>

            <template v-else>
              <div v-if="recentTargets.length > 0" class="forward-section">
                <div class="forward-section-title">گفتگوهای اخیر</div>
                <button
                  v-for="target in recentTargets"
                  :key="target.key"
                  class="forward-target-item"
                  :class="{ 'is-selected': isTargetSelected(target), 'is-disabled': !canAddMore && !isTargetSelected(target) }"
                  @click="toggleTarget(target)"
                >
                  <div class="select-indicator" :class="{ checked: isTargetSelected(target) }">
                    <svg v-if="isTargetSelected(target)" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round">
                      <polyline points="20 6 9 17 4 12" />
                    </svg>
                  </div>
                  <div class="target-avatar conversation">
                    {{ getAvatarInitial(target.title) }}
                  </div>
                  <div class="target-copy">
                    <div class="target-title-row">
                      <span class="target-title">{{ target.title }}</span>
                      <span class="target-chip">گفتگو</span>
                    </div>
                    <span v-if="target.subtitle" class="target-subtitle" dir="ltr">{{ target.subtitle }}</span>
                  </div>
                </button>
              </div>

              <div v-if="otherTargets.length > 0" class="forward-section">
                <div class="forward-section-title">سایر کاربران</div>
                <button
                  v-for="target in otherTargets"
                  :key="target.key"
                  class="forward-target-item"
                  :class="{ 'is-selected': isTargetSelected(target), 'is-disabled': !canAddMore && !isTargetSelected(target) }"
                  @click="toggleTarget(target)"
                >
                  <div class="select-indicator" :class="{ checked: isTargetSelected(target) }">
                    <svg v-if="isTargetSelected(target)" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round">
                      <polyline points="20 6 9 17 4 12" />
                    </svg>
                  </div>
                  <div class="target-avatar">
                    {{ getAvatarInitial(target.title) }}
                  </div>
                  <div class="target-copy">
                    <span class="target-title">{{ target.title }}</span>
                    <span v-if="target.subtitle" class="target-subtitle" dir="ltr">{{ target.subtitle }}</span>
                  </div>
                </button>
              </div>
            </template>
          </div>

          <div class="forward-modal-footer">
            <button
              class="forward-send-btn"
              :disabled="selectedCount === 0"
              @click="confirmForward"
            >
              <span>هدایت به {{ selectedCount > 0 ? selectedCount : '' }} {{ selectedCount > 0 ? 'مقصد' : 'مقصد' }}</span>
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
                <line x1="22" y1="2" x2="11" y2="13" />
                <polygon points="22 2 15 22 11 13 2 9 22 2" />
              </svg>
            </button>
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.forward-modal-overlay {
  position: fixed;
  inset: 0;
  background: rgba(15, 23, 42, 0.42);
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 16px;
  z-index: 200;
  backdrop-filter: blur(10px);
}

.forward-modal {
  background: #ffffff;
  width: min(100%, 460px);
  max-height: min(86vh, 760px);
  border-radius: 24px;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  box-shadow: 0 26px 60px rgba(15, 23, 42, 0.2);
}

.forward-modal-header {
  padding: 18px 18px 14px;
  border-bottom: 1px solid #eef2f7;
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 16px;
}

.header-copy h3 {
  margin: 0;
  font-size: 19px;
  color: #111827;
}

.header-copy p {
  margin: 6px 0 0;
  font-size: 13px;
  color: #6b7280;
  line-height: 1.5;
}

.close-btn {
  width: 40px;
  height: 40px;
  border: none;
  border-radius: 50%;
  background: #f3f4f6;
  color: #6b7280;
  font-size: 18px;
  cursor: pointer;
  flex: none;
}

.forward-modal-search {
  padding: 14px 18px;
  border-bottom: 1px solid #f3f4f6;
}

.forward-search-input {
  width: 100%;
  height: 44px;
  border: 1px solid #e5e7eb;
  border-radius: 999px;
  padding: 0 16px;
  font: inherit;
  background: #f8fafc;
  color: #111827;
  outline: none;
}

.forward-search-input:focus {
  border-color: #10b981;
  background: #ffffff;
}

.forward-modal-body {
  flex: 1;
  overflow-y: auto;
  padding: 8px 0 14px;
}

.forward-modal-state {
  padding: 36px 20px;
  text-align: center;
  color: #6b7280;
}

.forward-modal-state.error {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 12px;
}

.retry-btn {
  border: none;
  border-radius: 999px;
  background: #10b981;
  color: white;
  padding: 10px 18px;
  font: inherit;
  cursor: pointer;
}

.forward-section {
  padding-top: 4px;
}

.forward-section-title {
  padding: 10px 18px 8px;
  font-size: 12px;
  font-weight: 700;
  color: #6b7280;
}

.forward-target-item {
  width: 100%;
  border: none;
  background: transparent;
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px 18px;
  text-align: right;
  cursor: pointer;
}

.forward-target-item:hover {
  background: #f8fafc;
}

.forward-target-item.is-selected {
  background: #ecfdf5;
}

.forward-target-item.is-disabled {
  opacity: 0.45;
}

.select-indicator {
  width: 22px;
  height: 22px;
  min-width: 22px;
  border-radius: 50%;
  border: 2px solid #d1d5db;
  background: #ffffff;
  display: flex;
  align-items: center;
  justify-content: center;
  color: white;
  transition: background 0.15s ease, border-color 0.15s ease, transform 0.15s ease;
}

.select-indicator.checked {
  background: #10b981;
  border-color: #10b981;
  transform: scale(1.05);
}

.select-indicator svg {
  width: 14px;
  height: 14px;
}

.forward-modal-chips {
  padding: 10px 14px 0;
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}

.selected-chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 6px 10px;
  border-radius: 999px;
  border: 1px solid #10b981;
  background: #ecfdf5;
  color: #065f46;
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
  max-width: 200px;
}

.selected-chip .chip-title {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.selected-chip .chip-remove {
  font-size: 11px;
  opacity: 0.75;
}

.header-count {
  color: #065f46;
  font-weight: 700;
  margin-right: 4px;
  transition: color 0.2s ease;
}

.header-count.is-flash {
  color: #dc2626;
  animation: limit-shake 0.4s ease;
}

@keyframes limit-shake {
  0%, 100% { transform: translateX(0); }
  25% { transform: translateX(-3px); }
  75% { transform: translateX(3px); }
}

.forward-modal-footer {
  padding: 12px 16px;
  border-top: 1px solid #f1f5f9;
  background: #ffffff;
}

.forward-send-btn {
  width: 100%;
  height: 48px;
  border: none;
  border-radius: 999px;
  background: linear-gradient(135deg, #0f766e, #10b981);
  color: white;
  font: inherit;
  font-weight: 700;
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  transition: opacity 0.15s ease, transform 0.08s ease;
}

.forward-send-btn:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}

.forward-send-btn:not(:disabled):active {
  transform: scale(0.98);
}

.forward-send-btn svg {
  width: 18px;
  height: 18px;
  transform: scaleX(-1); /* point toward right in RTL */
}

.target-avatar {
  width: 46px;
  height: 46px;
  min-width: 46px;
  border-radius: 50%;
  background: linear-gradient(135deg, #0f766e, #10b981);
  color: white;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 17px;
  font-weight: 700;
}

.target-avatar.conversation {
  background: linear-gradient(135deg, #2563eb, #0ea5e9);
}

.target-copy {
  min-width: 0;
  display: flex;
  flex: 1;
  flex-direction: column;
  align-items: flex-start;
  gap: 3px;
}

.target-title-row {
  width: 100%;
  display: flex;
  align-items: center;
  gap: 8px;
}

.target-title {
  font-size: 15px;
  font-weight: 600;
  color: #111827;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.target-subtitle {
  font-size: 13px;
  color: #6b7280;
}

.target-chip {
  flex: none;
  border-radius: 999px;
  background: #e0f2fe;
  color: #0369a1;
  padding: 2px 8px;
  font-size: 11px;
  font-weight: 700;
}

.modal-slide-enter-active,
.modal-slide-leave-active {
  transition: opacity 0.25s ease;
}

.modal-slide-enter-active .forward-modal,
.modal-slide-leave-active .forward-modal {
  transition: transform 0.25s cubic-bezier(0.16, 1, 0.3, 1), opacity 0.25s ease;
}

.modal-slide-enter-from,
.modal-slide-leave-to {
  opacity: 0;
}

.modal-slide-enter-from .forward-modal {
  transform: translateY(40px) scale(0.96);
  opacity: 0;
}

.modal-slide-leave-to .forward-modal {
  transform: translateY(16px) scale(0.98);
  opacity: 0;
}

@media (max-width: 640px) {
  .forward-modal-overlay {
    padding: 10px;
  }

  .forward-modal {
    width: 100%;
    max-height: 92vh;
    border-radius: 20px;
  }
}
</style>
