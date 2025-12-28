<script setup lang="ts">
defineEmits(['navigate']);
</script>

<template>
  <div class="admin-panel-container">
    
    <div class="card management-card">
      <h2>پنل مدیریت</h2>
      <p>لطفاً بخش مورد نظر خود را انتخاب کنید:</p>
      
      <div class="button-group">
        <button class="management-button primary" @click="$emit('navigate', 'create_invitation')">
          ➕ ارسال لینک دعوت
        </button>
        
        <button class="management-button" @click="$emit('navigate', 'manage_commodities')">
          📦 مدیریت کالاها
        </button>

        <button class="management-button" @click="$emit('navigate', 'manage_users')">
          👥 مدیریت کاربران
        </button>
        
        <button class="management-button" @click="$emit('navigate', 'settings')">
          ⚙️ تنظیمات سیستم
        </button>
      </div>
    </div>

  </div>
</template>

<style scoped>
.admin-panel-container {
  display: flex;
  flex-direction: column;
  gap: 16px; 
}
.card.management-card {
  background-color: var(--card-bg);
  border-radius: 12px;
  padding: 20px;
  box-shadow: 0 4px 12px rgba(0,0,0,0.06);
}
h2 {
  margin-top: 0;
  margin-bottom: 8px;
}
p {
  font-size: 14px;
  color: var(--text-secondary);
  margin-bottom: 24px;
}
.button-group {
    display: grid;
    /* یک ستونه شبیه به بات */
    grid-template-columns: 1fr; 
    gap: 12px;
}
.management-button {
  width: 100%;
  padding: 14px;
  font-size: 16px;
  font-weight: 500;
  background-color: #f9fafb;
  color: var(--text-color);
  border: 1px solid var(--border-color);
  border-radius: 10px; 
  cursor: pointer;
  display: flex;
  align-items: center;
  /* متن را راست‌چین می‌کنیم و آیکون در سمت راست قرار می‌گیرد */
  justify-content: flex-start;
  direction: rtl; 
  gap: 10px;
  transition: all 0.2s ease-in-out;
}
.management-button:hover {
  border-color: var(--primary-color);
  color: var(--primary-color);
  background-color: #f0f9ff;
}
.management-button:active {
  background-color: #e0f2fe;
  transform: translateY(1px);
}
.management-button.primary {
  background: linear-gradient(135deg, #10b981, #059669);
  color: white;
  border-color: #10b981;
}
.management-button.primary:hover {
  background: linear-gradient(135deg, #059669, #047857);
  border-color: #059669;
  color: white;
}
</style>