<script setup lang="ts">
import AppButton from './AppButton.vue'

withDefaults(defineProps<{
  open: boolean
  title: string
  description?: string
  closeLabel?: string
}>(), {
  description: '',
  closeLabel: 'بستن',
})

defineEmits<{
  close: []
}>()
</script>

<template>
  <Teleport to="body">
    <div v-if="open" class="ui-sheet-backdrop" @click.self="$emit('close')">
      <section class="ui-bottom-sheet" role="dialog" aria-modal="true" :aria-label="title">
        <header class="ui-bottom-sheet__header">
          <div>
            <h2>{{ title }}</h2>
            <p v-if="description">{{ description }}</p>
          </div>
          <AppButton variant="ghost" size="sm" @click="$emit('close')">{{ closeLabel }}</AppButton>
        </header>
        <div class="ui-bottom-sheet__body">
          <slot />
        </div>
        <footer v-if="$slots.actions" class="ui-bottom-sheet__actions">
          <slot name="actions" />
        </footer>
      </section>
    </div>
  </Teleport>
</template>
