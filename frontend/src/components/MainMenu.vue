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
      <div class="bottom-row two-cols">
        <button @click="emit('navigate', 'profile')">
          <span>👤</span>
          پنل کاربر
        </button>
        <button @click="emit('navigate', 'admin_panel')">
          <span>🔐</span>
          پنل مدیریت
        </button>
      </div>
    </div>

    <div v-else class="bottom-row two-cols">
       <button @click="emit('navigate', 'profile')">
        <span>👤</span>
        پنل کاربر
      </button>
      <button @click="emit('navigate', 'settings')">
        <span>⚙️</span>
        تنظیمات
      </button>
    </div>
    
    </div>
</template>

<style scoped>
/* === کانتینر اصلی فشرده‌تر شد === */
.main-menu-container {
  /* پدینگ بالا و فاصله بین ردیف‌ها کمتر شد */
  padding: 8px 12px; 
  background-color: var(--bg-color);
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  gap: 6px; /* کاهش فاصله بین ردیف دکمه‌ها */
  
  /* اضافه کردن یک خط جداکننده در بالا */
  border-top: 1px solid var(--border-color);
  /* (اختیاری) سایه برای برجستگی */
  /* box-shadow: 0 -2px 10px rgba(0,0,0,0.04); */
}

/* --- استایل دکمه بزرگ معامله (فشرده‌تر) --- */
.trade-button {
  width: 100%;
  padding: 14px; /* کاهش پدینگ */
  font-size: 16px; /* کاهش فونت */
  font-weight: 700;
  background: linear-gradient(45deg, #007aff, #0056b3);
  color: white;
  border: none;
  border-radius: 12px;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  box-shadow: 0 4px 14px rgba(0, 122, 255, 0.3);
  transition: all 0.2s ease-in-out;
}
.trade-button span {
  font-size: 18px; /* کاهش فونت آیکون */
}
.trade-button:hover {
  transform: translateY(-2px);
  box-shadow: 0 6px 20px rgba(0, 122, 255, 0.4);
}
.trade-button:active {
  transform: translateY(1px);
  box-shadow: 0 2px 8px rgba(0, 122, 255, 0.2);
}

/* --- کانتینر چیدمان ادمین (فشرده‌تر) --- */
.admin-layout {
  display: flex;
  flex-direction: column;
  gap: 6px; /* کاهش فاصله */
}

/* --- استایل ردیف پایین (مشترک) --- */
.bottom-row {
  display: grid;
  gap: 6px; /* کاهش فاصله */
}

/* حالت عادی: دو ستون */
.standard-row {
  grid-template-columns: repeat(2, 1fr);
}

/* استایل جدید: سه ستون برای ردیف دوم ادمین */
.three-cols {
  grid-template-columns: repeat(3, 1fr);
}

/* استایل: دو ستون کنار هم */
.two-cols {
  grid-template-columns: repeat(2, 1fr);
}

/* استایل جدید: یک ستون برای ردیف سوم ادمین */
.single-col {
  grid-template-columns: 1fr;
}

/* استایل دکمه سرتاسری (فشرده‌تر) */
.single-col button {
  padding: 10px !important; /* کاهش پدینگ */
  font-size: 13px !important; /* کاهش فونت */
  flex-direction: row !important; 
  gap: 8px !important; 
}
.single-col button span {
    font-size: 16px !important; /* کاهش فونت آیکون */
    margin-bottom: 0 !important; 
}


/* --- استایل دکمه‌های کوچک (فشرده‌تر) --- */
.bottom-row button {
  padding: 8px 5px; /* کاهش شدید پدینگ عمودی */
  font-size: 11px;  /* کاهش فونت */
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
  gap: 2px; /* کاهش فاصله آیکون و متن */
  transition: all 0.2s ease-in-out;
  box-shadow: 0 1px 4px rgba(0,0,0,0.04);
  /* min-height: 55px; */ /* حذف ارتفاع حداقلی */
  text-align: center; 
  line-height: 1.3; 
}

.bottom-row button span {
  font-size: 14px; /* کاهش فونت آیکون */
  margin-bottom: 0; /* حذف مارجین */
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