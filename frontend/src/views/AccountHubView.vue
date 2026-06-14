<script setup lang="ts">
import { computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { Bell, ChevronLeft, Database, Settings, Smartphone, UserRound } from 'lucide-vue-next'
import { currentUserSummary, primeCurrentUserSummary } from '../utils/currentUser'

const router = useRouter()
const isAccountant = computed(() => currentUserSummary.value?.is_accountant === true)

const accountActions = computed(() => [
  {
    key: 'profile',
    title: 'پروفایل من',
    description: 'مشاهده و ویرایش اطلاعات حساب',
    icon: UserRound,
    action: () => router.push({ name: 'profile' }),
  },
  {
    key: 'settings',
    title: 'تنظیمات کاربری',
    description: 'نشست‌ها، حافظه و خروج از حساب',
    icon: Settings,
    action: () => router.push({ name: 'settings' }),
  },
  {
    key: 'sessions',
    title: 'نشست‌های فعال',
    description: 'بررسی و مدیریت دستگاه‌های فعال',
    icon: Smartphone,
    action: () => router.push({ name: 'settings', query: { section: 'sessions' } }),
    hidden: isAccountant.value,
  },
  {
    key: 'storage',
    title: 'حافظه و داده‌ها',
    description: 'پاک‌سازی فایل‌های دانلود شده پیام‌رسان',
    icon: Database,
    action: () => router.push({ name: 'settings', query: { section: 'storage' } }),
  },
  {
    key: 'notifications',
    title: 'اعلان‌ها',
    description: 'اعلان‌های سیستمی، بازار و معاملات',
    icon: Bell,
    action: () => router.push({ name: 'notifications' }),
  },
].filter(action => !action.hidden))

onMounted(() => {
  void primeCurrentUserSummary()
})
</script>

<template>
  <div class="ds-page account-hub-page">
    <header class="header-row">
      <div class="header-spacer"></div>
      <div class="header-title">
        <h2>حساب</h2>
      </div>
      <button class="back-button" type="button" @click="router.back()">
        <ChevronLeft :size="24" />
      </button>
    </header>

    <main class="account-hub-content">
      <section class="account-card">
        <h1>حساب کاربری</h1>
        <p>تنظیمات شخصی، اعلان‌ها و وضعیت دستگاه‌ها در این بخش قرار دارند.</p>
      </section>

      <section class="action-list">
        <button
          v-for="action in accountActions"
          :key="action.key"
          type="button"
          class="account-action"
          @click="action.action"
        >
          <span class="action-icon"><component :is="action.icon" :size="20" /></span>
          <span class="action-copy">
            <strong>{{ action.title }}</strong>
            <small>{{ action.description }}</small>
          </span>
          <ChevronLeft :size="18" class="action-chevron" />
        </button>
      </section>
    </main>
  </div>
</template>

<style scoped>
.account-hub-page {
  padding-bottom: 5rem;
}

.account-hub-content {
  width: 100%;
  max-width: var(--ds-page-max-width);
  margin: 0 auto;
  padding: var(--ds-card-padding);
  display: flex;
  flex-direction: column;
  gap: var(--ds-section-gap);
}

.account-card {
  background: var(--ds-bg-card);
  border: 1px solid var(--ds-border-accent);
  border-radius: var(--ds-radius-lg);
  box-shadow: var(--ds-shadow-md);
  padding: 1rem;
}

.account-card h1 {
  margin: 0 0 0.35rem;
  color: var(--ds-text-primary);
  font-size: var(--ds-font-xl);
  font-weight: 850;
}

.account-card p {
  margin: 0;
  color: var(--ds-text-secondary);
  font-size: var(--ds-font-sm);
  line-height: 1.8;
}

.action-list {
  display: grid;
  grid-template-columns: 1fr;
  gap: 0.65rem;
}

.account-action {
  width: 100%;
  min-height: 72px;
  display: grid;
  grid-template-columns: 44px 1fr 24px;
  align-items: center;
  gap: 0.75rem;
  direction: rtl;
  text-align: right;
  border: 1px solid var(--ds-border-accent);
  border-radius: var(--ds-radius-lg);
  background: var(--ds-bg-card);
  color: var(--ds-text-primary);
  box-shadow: var(--ds-shadow-sm);
  padding: 0.85rem;
  cursor: pointer;
  font-family: inherit;
  transition: all 0.18s ease;
}

.account-action:active {
  transform: scale(0.985);
  background: var(--ds-primary-50);
}

.action-icon {
  width: 44px;
  height: 44px;
  border-radius: var(--ds-radius-md);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  background: var(--ds-primary-50);
  color: var(--ds-primary-700);
}

.action-copy {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 0.2rem;
}

.action-copy strong {
  color: var(--ds-text-primary);
  font-size: var(--ds-font-md);
  font-weight: 850;
  line-height: 1.35;
}

.action-copy small {
  color: var(--ds-text-muted);
  font-size: var(--ds-font-xs);
  line-height: 1.6;
}

.action-chevron {
  color: var(--ds-text-placeholder);
}
</style>
