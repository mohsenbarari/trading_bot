<script setup lang="ts">

const props = defineProps<{
  currentSearchIndex: number
  totalResults: number
  showInChatSearchList: boolean
}>()

const emit = defineEmits<{
  (e: 'next'): void
  (e: 'prev'): void
  (e: 'toggle-list'): void
}>()
</script>

<template>
  <div class="search-bottom-bar" dir="ltr">
    <!-- Left side: Calendar (Optional feature placeholder) -->
    <button class="nav-btn" v-if="!showInChatSearchList">
      <svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="2">
        <rect x="3" y="4" width="18" height="18" rx="2" ry="2"></rect>
        <line x1="16" y1="2" x2="16" y2="6"></line>
        <line x1="8" y1="2" x2="8" y2="6"></line>
        <line x1="3" y1="10" x2="21" y2="10"></line>
      </svg>
    </button>
    <div v-else></div> <!-- Layout Spacer -->
    
    <!-- Count -->
    <div class="search-count-badge" v-if="!showInChatSearchList">
       {{ currentSearchIndex + 1 }} از {{ totalResults }}
    </div>

    <!-- Right side: Navigation & List Toggle -->
    <div class="right-navs">
       <button class="nav-btn" v-ripple @click="$emit('prev')" v-if="!showInChatSearchList">
         <svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
           <polyline points="18 15 12 9 6 15"></polyline>
         </svg>
       </button>
       <button class="nav-btn" v-ripple @click="$emit('next')" v-if="!showInChatSearchList">
         <svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
           <polyline points="6 9 12 15 18 9"></polyline>
         </svg>
       </button>
       <button class="nav-btn toggle-list-btn" v-ripple @click="$emit('toggle-list')">
         <!-- Chat Icon -> Return to chat, List Icon -> Show list -->
         <svg v-if="showInChatSearchList" viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"></path>
         </svg>
         <svg v-else viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2">
            <line x1="8" y1="6" x2="21" y2="6"></line>
            <line x1="8" y1="12" x2="21" y2="12"></line>
            <line x1="8" y1="18" x2="21" y2="18"></line>
            <line x1="3" y1="6" x2="3.01" y2="6"></line>
            <line x1="3" y1="12" x2="3.01" y2="12"></line>
            <line x1="3" y1="18" x2="3.01" y2="18"></line>
         </svg>
       </button>
    </div>
  </div>
</template>

<style scoped>
.search-bottom-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  height: 56px;
  background: white;
  padding: 0 12px;
  border-top: 1px solid #e0e0e0;
  width: 100%;
  flex-shrink: 0;
  direction: ltr; /* Ensure layout remains exactly as expected */
}

.search-count-badge {
  font-size: 14px;
  color: #555;
  font-weight: 500;
  background: #f1f2f6;
  padding: 4px 12px;
  border-radius: 12px;
  direction: rtl; /* For Persian text */
}

.right-navs {
  display: flex;
  align-items: center;
  gap: 4px;
}

.nav-btn {
  background: none;
  border: none;
  cursor: pointer;
  padding: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 50%;
  transition: background 0.2s;
  color: #707579;
  width: 40px;
  height: 40px;
}

.nav-btn:hover {
  background: rgba(0, 0, 0, 0.05);
}

.nav-btn:active {
  background: rgba(0, 0, 0, 0.1);
  color: #3390ec;
}

.toggle-list-btn {
  margin-left: 8px; /* Give some visual separation */
}
</style>
