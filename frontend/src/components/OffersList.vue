<script setup lang="ts">

import { Search } from 'lucide-vue-next';

// Define Props
const props = defineProps<{
  offers: any[];
  loading: boolean;
  limit?: number;
}>();

function getStatusBadge(type: string) {
  return type === 'buy' 
    ? { text: 'خرید', bg: 'bg-green-100', color: 'text-green-700' }
    : { text: 'فروش', bg: 'bg-red-100', color: 'text-red-700' }
}

function timeAgo(dateString: string) {
    if (!dateString) return '';
    // Simple placeholder, ideally use date-fns or moment-jalaali
    // If dateString is ISO, convert to relative time
    // For now, return as is (assuming Backend sends processed string or Date)
    return dateString;
}
</script>

<template>
    <div v-if="loading" class="space-y-3">
       <div v-for="i in (limit || 3)" :key="i" class="h-24 bg-white/50 rounded-2xl animate-pulse border border-amber-100/30"></div>
    </div>

    <div v-else-if="offers.length === 0" class="text-center py-10">
       <div class="w-16 h-16 bg-amber-50 rounded-2xl flex items-center justify-center mx-auto mb-4 text-amber-400">
          <Search :size="32" />
       </div>
       <p class="text-gray-400 font-medium">هیچ لفظ فعالی یافت نشد.</p>
    </div>

    <div v-else class="space-y-3">
      <div 
        v-for="offer in (limit ? offers.slice(0, limit) : offers)" 
        :key="offer.id"
        class="offer-card bg-white/70 backdrop-blur-sm p-4 rounded-2xl shadow-sm border border-amber-100/30 flex flex-col gap-3 transition-all hover:shadow-md hover:bg-white/90"
      >
        <!-- Top Row -->
        <div class="flex justify-between items-start">
           <div class="flex items-center gap-3">
              <div class="w-10 h-10 rounded-xl flex items-center justify-center font-bold"
                 :class="offer.offer_type === 'buy' ? 'bg-green-50 text-green-600' : 'bg-red-50 text-red-600'">
                 {{ offer.offer_type === 'buy' ? 'خ' : 'ف' }}
              </div>
              <div>
                 <h3 class="font-bold text-gray-800">{{ offer.commodity_name }}</h3>
                 <span class="text-xs text-gray-400">{{ timeAgo(offer.created_at) }}</span>
              </div>
           </div>
           <span class="px-2.5 py-1 rounded-lg text-xs font-bold" :class="getStatusBadge(offer.offer_type).bg + ' ' + getStatusBadge(offer.offer_type).color">
              {{ getStatusBadge(offer.offer_type).text }}
           </span>
        </div>

        <!-- Details Row -->
        <div class="grid grid-cols-2 gap-2 bg-amber-50/30 p-3 rounded-xl border border-amber-100/30">
           <div class="text-center border-l border-amber-200/30 pl-2">
              <p class="text-xs text-gray-400 mb-1">تعداد</p>
              <p class="font-bold text-gray-900">{{ offer.quantity }}</p>
           </div>
           <div class="text-center">
              <p class="text-xs text-gray-400 mb-1">قیمت واحد</p>
              <p class="font-bold text-gray-900">{{ offer.price ? offer.price.toLocaleString() : '---' }}</p>
           </div>
        </div>

        <!-- Action Button -->
         <button class="w-full py-2.5 rounded-xl font-bold text-sm transition-all active:scale-[0.98]"
           :class="offer.offer_type === 'buy' 
             ? 'bg-red-500 hover:bg-red-600 text-white shadow-sm shadow-red-500/10' 
             : 'bg-green-500 hover:bg-green-600 text-white shadow-sm shadow-green-500/10'">
            {{ offer.offer_type === 'buy' ? 'فروش به این خریدار' : 'خرید از این فروشنده' }}
         </button>

      </div>
    </div>
</template>
