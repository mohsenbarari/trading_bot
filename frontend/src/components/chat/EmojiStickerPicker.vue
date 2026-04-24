<template>
  <transition name="picker-slide">
    <section v-show="open" class="emoji-sticker-picker">
      <div class="picker-handle" aria-hidden="true"></div>

      <header class="picker-header">
        <div class="picker-title-group">
          <div class="picker-title">{{ activeCategory.label }}</div>
          <div class="picker-subtitle">{{ activeCategory.emojis.length }} ایموجی استاندارد</div>
        </div>

        <button class="picker-close" type="button" @click="setOpen(false)" aria-label="بستن پنل ایموجی">
          <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <line x1="18" y1="6" x2="6" y2="18"></line>
            <line x1="6" y1="6" x2="18" y2="18"></line>
          </svg>
        </button>
      </header>

      <div ref="scrollContainerRef" class="picker-grid-scroll">
        <div class="picker-grid" role="list">
          <button
            v-for="entry in activeCategory.emojis"
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
      </div>

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

const props = defineProps<{
  open: boolean
  currentUserId: number | null
}>()

const emit = defineEmits<{
  (e: 'update:open', value: boolean): void
  (e: 'select', emoji: string): void
}>()

const activeCategoryId = ref('frequent')
const scrollContainerRef = ref<HTMLElement | null>(null)
const usageVersion = ref(0)

const categories = computed<EmojiStickerCategory[]>(() => {
  void usageVersion.value
  return [buildFrequentEmojiCategory(props.currentUserId), ...TELEGRAM_EMOJI_CATEGORIES]
})

const activeCategory = computed(() => {
  return categories.value.find((category) => category.id === activeCategoryId.value) ?? categories.value[0]
})

function resetScrollPosition() {
  nextTick(() => {
    if (scrollContainerRef.value) {
      scrollContainerRef.value.scrollTop = 0
    }
  })
}

function setOpen(nextValue: boolean) {
  emit('update:open', nextValue)
}

function selectCategory(categoryId: string) {
  activeCategoryId.value = categoryId
}

function handleSelect(entry: EmojiStickerEntry) {
  recordEmojiStickerUsage(props.currentUserId, entry.emoji)
  usageVersion.value += 1
  emit('select', entry.emoji)
  setOpen(false)
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
})

watch(activeCategoryId, () => {
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
  position: absolute;
  left: 0;
  right: 0;
  bottom: calc(100% + 8px);
  display: flex;
  flex-direction: column;
  height: 336px;
  overflow: hidden;
  border: 1px solid rgba(15, 23, 42, 0.08);
  border-radius: 24px 24px 14px 14px;
  background:
    radial-gradient(circle at top right, rgba(51, 144, 236, 0.12), transparent 34%),
    linear-gradient(180deg, rgba(255, 255, 255, 0.98), rgba(244, 247, 251, 0.98));
  box-shadow: 0 24px 48px rgba(15, 23, 42, 0.18);
  backdrop-filter: blur(18px);
  z-index: 80;
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

.picker-tabs {
  display: grid;
  grid-template-columns: repeat(9, minmax(0, 1fr));
  gap: 6px;
  padding: 10px 10px calc(10px + env(safe-area-inset-bottom));
  border-top: 1px solid rgba(148, 163, 184, 0.16);
  background: rgba(255, 255, 255, 0.82);
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
    height: 360px;
  }

  .picker-grid {
    grid-template-columns: repeat(10, minmax(0, 1fr));
  }
}
</style>