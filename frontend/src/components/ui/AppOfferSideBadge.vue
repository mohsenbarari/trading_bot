<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{
  side: 'buy' | 'sell'
}>()

const label = computed(() => props.side === 'buy' ? 'خرید' : 'فروش')
</script>

<template>
  <span class="role-badge" :class="side">
    {{ label }}
  </span>
</template>
