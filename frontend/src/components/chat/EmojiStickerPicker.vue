<template>
  <transition name="picker-slide">
    <section v-show="open" class="emoji-sticker-picker">
      <div class="picker-handle" aria-hidden="true"></div>

      <header class="picker-header">
        <div class="picker-title-group">
          <div class="picker-title">استیکرها و ایموجی‌ها</div>
          <div class="picker-subtitle" :class="{ 'limit-reached': isLimitReached }">
            {{ activeCategory.label }}
            <span class="picker-subtitle-separator">•</span>
            {{ currentStickerCount }} / {{ maxStickerCount }}
          </div>
        </div>

        <button class="picker-close" type="button" @click="setOpen(false)" aria-label="بستن پنل ایموجی">
          <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <line x1="18" y1="6" x2="6" y2="18"></line>
            <line x1="6" y1="6" x2="18" y2="18"></line>
          </svg>
        </button>
      </header>

      <div ref="scrollContainerRef" class="picker-grid-scroll" @scroll.passive="handleScroll">
        <section
          v-for="category in categories"
          :key="category.id"
          :ref="(element) => setSectionRef(category.id, element as HTMLElement | null)"
          class="picker-section"
          :data-category-id="category.id"
        >
          <div class="picker-section-header">
            <span class="picker-section-icon">{{ category.icon }}</span>
            <span class="picker-section-label">{{ category.label }}</span>
            <span class="picker-section-count">{{ category.emojis.length }}</span>
          </div>

          <div class="picker-grid" role="list">
            <button
              v-for="entry in category.emojis"
              :key="entry.emoji"
              type="button"
              class="emoji-cell"
              :aria-label="entry.name"
              :title="entry.name"
              @click="handleSelect(entry)"
            >
              <span class="emoji-cell-symbol">{{ entry.emoji }}</span>
            </button>
          </div>
        </section>
      </div>

      <div class="picker-footer">
        <button
          type="button"
          class="picker-backspace"
          aria-label="حذف"
          title="حذف"
          @click="emit('backspace')"
        >
          <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M21 4H8l-7 8 7 8h13a2 2 0 0 0 2-2V6a2 2 0 0 0-2-2z"></path>
            <line x1="18" y1="9" x2="12" y2="15"></line>
            <line x1="12" y1="9" x2="18" y2="15"></line>
          </svg>
        </button>

        <nav class="picker-tabs" aria-label="دسته‌بندی ایموجی‌ها">
          <button
            v-for="category in categories"
            :key="category.id"
            type="button"
            class="picker-tab"
            :class="{ active: category.id === activeCategoryId }"
            :aria-label="category.label"
            :title="category.label"
            @click="selectCategory(category.id)"
          >
            <span class="picker-tab-icon">{{ category.icon }}</span>
          </button>
        </nav>
      </div>
    </section>
  </transition>
</template>

<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import {
  buildFrequentEmojiCategory,
  recordEmojiStickerUsage,
  TELEGRAM_EMOJI_CATEGORIES,
  type EmojiStickerCategory,
  type EmojiStickerEntry,
} from '../../utils/emojiStickerCatalog'

const props = withDefaults(defineProps<{
  open: boolean
  currentUserId: number | null
  currentStickerCount?: number
  maxStickerCount?: number
  closeOnSelect?: boolean
}>(), {
  currentStickerCount: 0,
  maxStickerCount: 24,
  closeOnSelect: true,
})

const emit = defineEmits<{
  (e: 'update:open', value: boolean): void
  (e: 'select', emoji: string): void
  (e: 'insert', emoji: string): void
  (e: 'backspace'): void
}>()

const activeCategoryId = ref('frequent')
const scrollContainerRef = ref<HTMLElement | null>(null)
const usageVersion = ref(0)
const sectionRefs = new Map<string, HTMLElement>()

const categories = computed<EmojiStickerCategory[]>(() => {
  void usageVersion.value
  return [buildFrequentEmojiCategory(props.currentUserId), ...TELEGRAM_EMOJI_CATEGORIES]
})

const activeCategory = computed<EmojiStickerCategory>(() => {
  return categories.value.find((category) => category.id === activeCategoryId.value)
    ?? categories.value[0]
    ?? buildFrequentEmojiCategory(props.currentUserId)
})

const isLimitReached = computed(() => props.currentStickerCount >= props.maxStickerCount)

function setSectionRef(categoryId: string, element: HTMLElement | null) {
  if (element) {
    sectionRefs.set(categoryId, element)
    return
  }

  sectionRefs.delete(categoryId)
}

function syncActiveCategoryFromScroll() {
  const container = scrollContainerRef.value
  const firstCategoryId = categories.value[0]?.id ?? 'frequent'
  if (!container) {
    activeCategoryId.value = firstCategoryId
    return
  }

  const threshold = container.scrollTop + 28
  let nextCategoryId = firstCategoryId

  for (const category of categories.value) {
    const section = sectionRefs.get(category.id)
    if (!section) continue
    if (section.offsetTop <= threshold) {
      nextCategoryId = category.id
      continue
    }
    break
  }

  activeCategoryId.value = nextCategoryId
}

function resetScrollPosition() {
  nextTick(() => {
    if (scrollContainerRef.value) {
      scrollContainerRef.value.scrollTop = 0
    }
    syncActiveCategoryFromScroll()
  })
}

function setOpen(nextValue: boolean) {
  emit('update:open', nextValue)
}

function selectCategory(categoryId: string) {
  activeCategoryId.value = categoryId
  nextTick(() => {
    const container = scrollContainerRef.value
    const section = sectionRefs.get(categoryId)
    if (!container || !section) return

    container.scrollTo({
      top: Math.max(section.offsetTop - 6, 0),
      behavior: 'smooth',
    })
  })
}

function handleScroll() {
  syncActiveCategoryFromScroll()
}

function handleSelect(entry: EmojiStickerEntry) {
  recordEmojiStickerUsage(props.currentUserId, entry.emoji)
  usageVersion.value += 1
  emit('select', entry.emoji)
  emit('insert', entry.emoji)

  if (props.closeOnSelect) {
    setOpen(false)
  }
}

watch(() => props.open, (isOpen) => {
  if (!isOpen) return
  usageVersion.value += 1
  activeCategoryId.value = 'frequent'
  resetScrollPosition()
})

watch(() => props.currentUserId, () => {
  usageVersion.value += 1
  activeCategoryId.value = 'frequent'
  resetScrollPosition()
})
</script>

<style scoped>
.picker-slide-enter-active,
.picker-slide-leave-active {
  transition: opacity 0.24s ease, transform 0.24s cubic-bezier(0.2, 0, 0, 1);
}

.picker-slide-enter-from,
.picker-slide-leave-to {
  opacity: 0;
  transform: translateY(14px);
}

.emoji-sticker-picker {
  position: relative;
  margin: 8px -12px 0;
  display: flex;
  flex-direction: column;
  height: min(336px, 44vh);
  overflow: hidden;
  border: 1px solid rgba(15, 23, 42, 0.08);
  border-radius: 24px 24px 0 0;
  background:
    radial-gradient(circle at top right, rgba(51, 144, 236, 0.12), transparent 34%),
    linear-gradient(180deg, rgba(255, 255, 255, 0.98), rgba(244, 247, 251, 0.98));
  box-shadow: 0 -10px 28px rgba(15, 23, 42, 0.12);
  backdrop-filter: blur(18px);
  z-index: 1;
}

.picker-handle {
  width: 44px;
  height: 5px;
  margin: 10px auto 4px;
  border-radius: 999px;
  background: rgba(15, 23, 42, 0.12);
}

.picker-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 0 14px 10px;
}

.picker-title-group {
  min-width: 0;
}

.picker-title {
  font-size: 14px;
  font-weight: 700;
  color: #0f172a;
}

.picker-subtitle {
  margin-top: 2px;
  font-size: 11px;
  color: #64748b;
}

.picker-subtitle.limit-reached {
  color: #dc2626;
}

.picker-subtitle-separator {
  padding: 0 4px;
}

.picker-close {
  width: 32px;
  height: 32px;
  border: none;
  border-radius: 50%;
  background: rgba(148, 163, 184, 0.12);
  color: #64748b;
  display: flex;
  align-items: center;
  justify-content: center;
}

.picker-grid-scroll {
  flex: 1;
  overflow-y: auto;
  padding: 0 10px 12px;
  scroll-behavior: smooth;
  overscroll-behavior: contain;
}

.picker-section + .picker-section {
  margin-top: 10px;
}

.picker-section-header {
  position: sticky;
  top: 0;
  z-index: 1;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 8px 10px;
  background: linear-gradient(180deg, rgba(244, 247, 251, 0.98), rgba(244, 247, 251, 0.88));
  backdrop-filter: blur(10px);
}

.picker-section-icon {
  font-size: 16px;
  line-height: 1;
}

.picker-section-label {
  font-size: 12px;
  font-weight: 700;
  color: #334155;
}

.picker-section-count {
  margin-right: auto;
  font-size: 11px;
  font-variant-numeric: tabular-nums;
  color: #94a3b8;
}

.picker-grid {
  display: grid;
  grid-template-columns: repeat(8, minmax(0, 1fr));
  gap: 6px;
  direction: ltr;
}

.emoji-cell {
  aspect-ratio: 1;
  border: none;
  border-radius: 16px;
  background: transparent;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: transform 0.16s ease, background-color 0.16s ease;
}

.emoji-cell:active,
.emoji-cell:hover {
  background: rgba(51, 144, 236, 0.12);
  transform: translateY(-1px);
}

.emoji-cell-symbol {
  font-size: 28px;
  line-height: 1;
}

.picker-footer {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 10px calc(10px + env(safe-area-inset-bottom));
  border-top: 1px solid rgba(148, 163, 184, 0.16);
  background: rgba(255, 255, 255, 0.82);
}

.picker-backspace {
  width: 40px;
  height: 40px;
  border: none;
  border-radius: 14px;
  background: rgba(148, 163, 184, 0.12);
  color: #475569;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  transition: background-color 0.16s ease, color 0.16s ease, transform 0.16s ease;
}

.picker-backspace:hover,
.picker-backspace:active {
  background: rgba(51, 144, 236, 0.14);
  color: #3390ec;
  transform: translateY(-1px);
}

.picker-tabs {
  flex: 1;
  min-width: 0;
  display: grid;
  grid-template-columns: repeat(9, minmax(0, 1fr));
  gap: 6px;
}

.picker-tab {
  height: 38px;
  border: none;
  border-radius: 14px;
  background: transparent;
  color: #64748b;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: background-color 0.16s ease, color 0.16s ease, transform 0.16s ease;
}

.picker-tab.active {
  background: #ffffff;
  color: #3390ec;
  box-shadow: 0 8px 16px rgba(51, 144, 236, 0.16);
}

.picker-tab:active,
.picker-tab:hover {
  transform: translateY(-1px);
}

.picker-tab-icon {
  font-size: 18px;
  line-height: 1;
}

@media (min-width: 640px) {
  .emoji-sticker-picker {
    height: min(360px, 48vh);
  }

  .picker-grid {
    grid-template-columns: repeat(10, minmax(0, 1fr));
  }
}
</style>