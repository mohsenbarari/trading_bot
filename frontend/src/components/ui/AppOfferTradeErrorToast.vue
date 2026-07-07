<script setup lang="ts">
defineProps<{
  message: string
}>()
</script>

<template>
  <div
    class="fixed top-4 left-1/2 -translate-x-1/2 z-50 bg-gradient-to-r from-red-500 to-red-600 text-white px-5 py-2.5 rounded-2xl text-sm font-bold shadow-lg shadow-red-500/25"
    data-test="offer-trade-error-toast"
  >
    {{ message }}
  </div>
</template>
