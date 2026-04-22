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

const props = defineProps<{
  showForwardModal: boolean
  sortedConversations: Conversation[]
}>()

const emit = defineEmits<{
  (e: 'close'): void
  (e: 'forward-to', target: ChatForwardTarget): void
}>()

const searchQuery = ref('')
const isLoading = ref(false)
const loadError = ref('')
const allUsers = ref<ForwardUser[]>([])
let fetchSequence = 0

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
    void loadForwardUsers()
    return
  }

  searchQuery.value = ''
  loadError.value = ''
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

function emitForwardTarget(target: ForwardTargetCandidate) {
  emit('forward-to', {
    kind: target.kind,
    id: target.id,
    title: target.title,
    subtitle: target.subtitle,
  })
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
              <p>گفتگوهای اخیر اول نمایش داده می‌شوند و بعد بقیه کاربران</p>
            </div>
            <button class="close-btn" @click="emit('close')">✕</button>
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
                  @click="emitForwardTarget(target)"
                >
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
                  @click="emitForwardTarget(target)"
                >
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
