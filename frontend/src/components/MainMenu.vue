<script setup lang="ts">
defineProps<{
  userRole: string;
}>()
const emit = defineEmits(['navigate'])
</script>

<template>
  <div class="main-menu-container">
    <button class="trade-button" @click="emit('navigate', 'trade')">
      <span>📈</span>
      معامله
    </button>
    <div class="bottom-grid">
      <div class="left-column">
        <!-- 
          نکته: دکمه "نسخه تحت وب" در داخل خود وب‌اپ معنایی ندارد.
          من به جای آن، دکمه "ساخت دعوت" را برای مدیر ارشد قرار داده‌ام 
          تا چیدمان حفظ شود و یک قابلیت کاربردی نیز اضافه گردد.
        -->
        <button v-if="userRole === 'SUPER_ADMIN'" @click="emit('navigate', 'create_invitation')">
          <span>➕</span>
          ساخت دعوت
        </button>
        <button @click="emit('navigate', 'settings')">
          <span>⚙️</span>
          تنظیمات
        </button>
      </div>
      <div class="right-column">
        <button class="profile-button" @click="emit('navigate', 'profile')">
          <span>👤</span>
          پنل کاربر
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.main-menu-container {
  padding: 16px 16px 12px 16px;
  background-color: var(--bg-color);
  flex-shrink: 0;
}

.trade-button {
  width: 100%;
  padding: 20px;
  font-size: 20px;
  font-weight: 700;
  background: linear-gradient(45deg, #007aff, #0056b3);
  color: white;
  border: none;
  border-radius: 12px;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 10px;
  margin-bottom: 12px;
  box-shadow: 0 4px 14px rgba(0, 122, 255, 0.3);
  transition: all 0.2s ease-in-out;
}
.trade-button:hover {
  transform: translateY(-2px);
  box-shadow: 0 6px 20px rgba(0, 122, 255, 0.4);
}
.trade-button span {
  font-size: 24px;
}

.bottom-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
}

.left-column, .right-column {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.left-column button, .right-column button {
  padding: 16px;
  font-size: 15px;
  font-weight: 600;
  background-color: var(--card-bg);
  color: var(--text-color);
  border: 1px solid var(--border-color);
  border-radius: 12px;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  transition: all 0.2s ease-in-out;
  width: 100%;
  box-shadow: 0 2px 8px rgba(0,0,0,0.05);
}
.left-column button:hover, .right-column button:hover {
  transform: translateY(-2px);
  border-color: var(--primary-color);
  color: var(--primary-color);
  box-shadow: 0 4px 12px rgba(0,0,0,0.08);
}

.profile-button {
  height: 100%;
}
</style>

