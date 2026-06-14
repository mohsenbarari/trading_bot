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
    <div v-if="open" class="ui-responsive-dialog-backdrop" @click.self="$emit('close')">
      <section class="ui-responsive-dialog" role="dialog" aria-modal="true" :aria-label="title">
        <header class="ui-responsive-dialog__header">
          <div>
            <h2>{{ title }}</h2>
            <p v-if="description">{{ description }}</p>
          </div>
          <AppButton variant="ghost" size="sm" @click="$emit('close')">{{ closeLabel }}</AppButton>
        </header>
        <div class="ui-responsive-dialog__body">
          <slot />
        </div>
        <footer v-if="$slots.actions" class="ui-responsive-dialog__actions">
          <slot name="actions" />
        </footer>
      </section>
    </div>
  </Teleport>
</template>
