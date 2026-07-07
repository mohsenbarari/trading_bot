<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{
  value: number | null | undefined
}>()

const formattedPrice = computed(() => props.value ? props.value.toLocaleString() : '---')
</script>

<template>
  <span class="price" data-test="offer-price">
    {{ formattedPrice }}
  </span>
</template>
