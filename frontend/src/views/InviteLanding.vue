<script setup lang="ts">
import { ref, onMounted } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { Globe2, Send } from 'lucide-vue-next';
import AppButton from '../components/ui/AppButton.vue';
import AppCard from '../components/ui/AppCard.vue';
import AppErrorState from '../components/ui/AppErrorState.vue';
import AppLoadingState from '../components/ui/AppLoadingState.vue';

const route = useRoute();
const router = useRouter();
const shortCode = route.params.code as string;

const loading = ref(true);
const error = ref('');
const token = ref('');
const botLink = ref('');

const apiBaseUrl = import.meta.env.VITE_API_BASE_URL || '';

onMounted(async () => {
  try {
    const res = await fetch(`${apiBaseUrl}/api/invitations/lookup/${shortCode}`);
    if (!res.ok) throw new Error('دعوت‌نامه نامعتبر یا منقضی شده است.');
    
    const data = await res.json();
    token.value = data.token;
    
    // Fetch bot username from config if possible, or hardcode/env
    // For now assuming we can construct link or get it from API
    // Let's just use the token to register via web, and hardcode bot link base
    // Actually best to get bot username from public config endpoint
    
    const configRes = await fetch(`${apiBaseUrl}/api/config`);
    const config = await configRes.json();
    
    botLink.value = `https://t.me/${config.bot_username}?start=${token.value}`;
    
  } catch (e: any) {
    error.value = e.message;
  } finally {
    loading.value = false;
  }
});

function goToWebRegister() {
  router.push(`/register?token=${token.value}`);
}
</script>

<template>
  <div class="landing-container">
    <AppCard class="invite-card">
      <h1>دعوت‌نامه اختصاصی</h1>
      
      <AppLoadingState v-if="loading" label="در حال بررسی دعوت‌نامه" />
      
      <AppErrorState v-else-if="error" title="دعوت‌نامه قابل استفاده نیست" :message="error" />
      
      <div v-else class="actions">
        <p class="welcome-text">شما به سامانه معاملاتی دعوت شده‌اید.</p>
        <p class="instruction">لطفاً روش ثبت‌نام خود را انتخاب کنید:</p>
        
        <a :href="botLink" class="invite-action telegram-btn">
          <Send :size="18" />
          <span>ثبت‌نام با تلگرام (توصیه شده)</span>
        </a>
        
        <AppButton variant="secondary" block @click="goToWebRegister">
          <template #icon>
            <Globe2 :size="18" />
          </template>
          ثبت‌نام از طریق وب
        </AppButton>
      </div>
    </AppCard>
  </div>
</template>

<style scoped>
.landing-container {
  display: flex;
  justify-content: center;
  align-items: center;
  min-height: 100vh;
  padding: 1rem;
  background: var(--ds-bg-page);
}

.invite-card {
  text-align: center;
  max-width: 400px;
  width: 100%;
}

h1 {
  margin: 0 0 1.5rem;
  color: var(--ds-text-primary);
  font-size: var(--ds-font-xl);
  font-weight: 850;
}

.welcome-text {
  margin: 0 0 0.5rem;
  color: var(--ds-success-700);
  font-size: var(--ds-font-md);
  font-weight: 800;
}

.instruction {
  color: var(--ds-text-muted);
  margin-bottom: 2rem;
}

.invite-action {
  display: flex;
  justify-content: center;
  align-items: center;
  gap: 0.45rem;
  width: 100%;
  min-height: 44px;
  padding: 0.75rem 1rem;
  border-radius: var(--ds-radius-md);
  font-size: var(--ds-font-sm);
  font-weight: 800;
  text-decoration: none;
  margin-bottom: 1rem;
  transition: transform 0.1s, box-shadow 0.18s ease;
  border: none;
  cursor: pointer;
}

.invite-action:active {
  transform: scale(0.98);
}

.telegram-btn {
  background: #0088cc;
  color: white;
  box-shadow: 0 4px 14px rgba(0, 136, 204, 0.3);
}

.telegram-btn:focus-visible {
  outline: none;
  box-shadow: 0 0 0 3px rgba(0, 136, 204, 0.22);
}
</style>
