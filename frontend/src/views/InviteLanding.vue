<script setup lang="ts">
import { ref, onMounted } from 'vue';
import { useRoute, useRouter } from 'vue-router';

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
    <div class="card">
      <h1>دعوت‌نامه اختصاصی</h1>
      
      <div v-if="loading" class="loading">
        <div class="spinner"></div>
        <p>در حال بررسی...</p>
      </div>
      
      <div v-else-if="error" class="error-box">
        <p>❌ {{ error }}</p>
      </div>
      
      <div v-else class="actions">
        <p class="welcome-text">شما به سامانه معاملاتی دعوت شده‌اید.</p>
        <p class="instruction">لطفاً روش ثبت‌نام خود را انتخاب کنید:</p>
        
        <a :href="botLink" class="btn telegram-btn">
          🔵 ثبت‌نام با تلگرام (توصیه شده)
        </a>
        
        <button @click="goToWebRegister" class="btn web-btn">
          🌐 ثبت‌نام از طریق وب
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.landing-container {
  display: flex;
  justify-content: center;
  align-items: center;
  min-height: 100vh;
  padding: 1rem;
  background: #f3f4f6;
}

.card {
  background: white;
  padding: 2rem;
  border-radius: 1.5rem;
  box-shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.1);
  text-align: center;
  max-width: 400px;
  width: 100%;
}

h1 {
  color: #1f2937;
  font-weight: 800;
  margin-bottom: 1.5rem;
}

.welcome-text {
  font-size: 1.1rem;
  font-weight: 700;
  color: #059669;
  margin-bottom: 0.5rem;
}

.instruction {
  color: #6b7280;
  margin-bottom: 2rem;
}

.btn {
  display: flex;
  justify-content: center;
  align-items: center;
  width: 100%;
  padding: 1rem;
  border-radius: 1rem;
  font-weight: 700;
  font-size: 1rem;
  text-decoration: none;
  margin-bottom: 1rem;
  transition: transform 0.1s;
  border: none;
  cursor: pointer;
}

.btn:active {
  transform: scale(0.98);
}

.telegram-btn {
  background: #0088cc;
  color: white;
  box-shadow: 0 4px 14px rgba(0, 136, 204, 0.3);
}

.web-btn {
  background: white;
  color: #1f2937;
  border: 1px solid #e5e7eb;
}

.loading {
  color: #6b7280;
}
.spinner {
  width: 40px; height: 40px;
  border: 4px solid #e5e7eb;
  border-top-color: #f59e0b;
  border-radius: 50%;
  animation: spin 1s linear infinite;
  margin: 0 auto 1rem;
}
@keyframes spin { to { transform: rotate(360deg); } }

.error-box {
  background: #fef2f2;
  color: #dc2626;
  padding: 1rem;
  border-radius: 1rem;
}
</style>