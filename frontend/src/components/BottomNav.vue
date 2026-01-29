<script setup lang="ts">
import { useRoute } from 'vue-router'
import { Home, TrendingUp, User } from 'lucide-vue-next'

const route = useRoute()

const navItems = [
  { name: 'dashboard', label: 'خانه', icon: Home, path: '/' },
  { name: 'market', label: 'بازار', icon: TrendingUp, path: '/market' },
  { name: 'profile', label: 'پروفایل', icon: User, path: '/profile' },
]
</script>

<template>
  <nav class="fixed bottom-0 left-0 right-0 z-50 px-6 pb-6 pt-2 pointer-events-none">
    <div class="glass-nav mx-auto max-w-md flex items-center justify-around py-3 px-2 rounded-2xl shadow-glass pointer-events-auto bg-white/80 backdrop-blur-md border border-white/40">
      
      <router-link
        v-for="item in navItems"
        :key="item.name"
        :to="item.path"
        class="flex flex-col items-center gap-1 p-2 rounded-xl transition-all duration-200 relative group"
        :class="route.name === item.name ? 'text-primary-600 scale-110' : 'text-gray-400 hover:text-gray-600'"
      >
        <component :is="item.icon" :size="24" :stroke-width="route.name === item.name ? 2.5 : 2" />
        <span class="text-[10px] font-medium" :class="route.name === item.name ? 'opacity-100' : 'opacity-0 h-0 w-0 overflow-hidden group-hover:opacity-100 group-hover:h-auto group-hover:w-auto transition-all'">
          {{ item.label }}
        </span>
        
        <!-- Active Indicator -->
        <div 
          v-if="route.name === item.name"
          class="absolute -bottom-1 w-1 h-1 bg-primary-500 rounded-full"
        ></div>
      </router-link>

    </div>
  </nav>
</template>

<style scoped>
.glass-nav {
  box-shadow: 0 8px 32px 0 rgba(31, 38, 135, 0.15);
}
</style>
