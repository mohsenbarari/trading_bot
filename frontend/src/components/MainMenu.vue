<script setup lang="ts">
import { computed } from 'vue';

const props = defineProps<{
  userRole: string;
  isTradePageVisible: boolean; // پراپرتی برای متن دکمه
}>()
const emit = defineEmits(['navigate', 'toggle-trade-view'])

// محاسبه متن دکمه بر اساس وضعیت صفحه معاملات
const toggleButtonText = computed(() => {
  return props.isTradePageVisible ? 'بستن صفحه معاملات' : 'مشاهده صفحه معاملات';
});

</script>

<template>
  <div class="main-menu-container">
    <button class="trade-button" @click="emit('navigate', 'trade')">
      <span>📈</span>
      معامله
    </button>
    
    <div v-if="userRole === 'مدیر ارشد'" class="admin-layout">
      <div class="bottom-row three-cols">
        <button @click="emit('toggle-trade-view')">
          <span>{{ isTradePageVisible ? '❌' : '👀' }}</span>
          {{ toggleButtonText }}
        </button>
        <button @click="emit('navigate', 'profile')">
          <span>👤</span>
          پنل کاربر
        </button>
        <button @click="emit('navigate', 'admin_panel')">
          <span>🔐</span>
          پنل مدیریت
        </button>
      </div>
      <div class="bottom-row single-col">
        <button @click="emit('navigate', 'create_invitation')">
          <span>➕</span>
          ارسال لینک دعوت 
        </button>
      </div>
    </div>

    <div v-else class="bottom-row standard-row">
       <button @click="emit('navigate', 'profile')">
        <span>👤</span>
        پنل کاربر
      </button>
      <button @click="emit('toggle-trade-view')">
        <span>{{ isTradePageVisible ? '❌' : '👀' }}</span>
        {{ toggleButtonText }}
      </button>
    </div>
    
    </div>
</template>

<style scoped>
/* === تمام استایل‌های قبلی بدون تغییر باقی می‌مانند === */
.main-menu-container {
  padding: 16px 16px 12px 16px;
  background-color: var(--bg-color);
  flex-shrink: 0;
  display: flex; /* برای چیدمان عمودی ردیف‌ها */
  flex-direction: column;
  gap: 12px; /* فاصله بین ردیف‌ها */
}

/* --- استایل دکمه بزرگ معامله (بدون تغییر) --- */
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
  /* margin-bottom حذف شد چون از gap در کانتینر استفاده می‌کنیم */
  box-shadow: 0 4px 14px rgba(0, 122, 255, 0.3);
  transition: all 0.2s ease-in-out;
}
.trade-button span {
  font-size: 24px;
}
.trade-button:hover {
  transform: translateY(-2px);
  box-shadow: 0 6px 20px rgba(0, 122, 255, 0.4);
}
.trade-button:active {
  transform: translateY(1px);
  box-shadow: 0 2px 8px rgba(0, 122, 255, 0.2);
}

/* --- کانتینر چیدمان ادمین --- */
.admin-layout {
  display: flex;
  flex-direction: column;
  gap: 10px; /* فاصله بین ردیف دوم و سوم ادمین */
}

/* --- استایل ردیف پایین (مشترک برای همه حالت‌ها) --- */
.bottom-row {
  display: grid;
  gap: 10px; 
}

/* حالت عادی: دو ستون مساوی */
.standard-row {
  grid-template-columns: repeat(2, 1fr);
}

/* استایل جدید: سه ستون مساوی برای ردیف دوم ادمین */
.three-cols {
  grid-template-columns: repeat(3, 1fr);
}

/* استایل جدید: یک ستون برای ردیف سوم ادمین */
.single-col {
  grid-template-columns: 1fr;
}
/* استایل دکمه سرتاسری در ردیف سوم ادمین */
.single-col button {
  padding: 15px 10px !important; 
  font-size: 15px !important; 
  flex-direction: row !important; 
  gap: 8px !important; 
}
.single-col button span {
    font-size: 20px !important; 
    margin-bottom: 0 !important; 
}


/* --- استایل دکمه‌های کوچک در ردیف پایین (مشترک برای همه) --- */
.bottom-row button {
  padding: 12px 5px; 
  font-size: 13px;  
  font-weight: 500; 
  background-color: var(--card-bg);
  color: var(--text-color);
  border: 1px solid var(--border-color);
  border-radius: 10px; 
  cursor: pointer;
  display: flex;
  flex-direction: column; 
  align-items: center;
  justify-content: center;
  gap: 4px; 
  transition: all 0.2s ease-in-out;
  box-shadow: 0 1px 4px rgba(0,0,0,0.04);
  min-height: 65px; 
  text-align: center; 
  line-height: 1.3; 
}

.bottom-row button span {
  font-size: 18px; 
  margin-bottom: 2px;
}

.bottom-row button:hover {
  transform: translateY(-2px);
  border-color: var(--primary-color);
  color: var(--primary-color);
  box-shadow: 0 3px 8px rgba(0,0,0,0.07);
}
.bottom-row button:active {
  transform: translateY(0px); 
  background-color: #f5f5f5; 
  box-shadow: 0 1px 4px rgba(0,0,0,0.04);
}

</style>