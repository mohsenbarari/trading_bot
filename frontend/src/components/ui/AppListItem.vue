<script setup lang="ts">
withDefaults(defineProps<{
  title: string
  description?: string
  meta?: string
  interactive?: boolean
}>(), {
  interactive: false,
})

const emit = defineEmits<{
  select: []
}>()
</script>

<template>
  <component
    :is="interactive ? 'button' : 'article'"
    :type="interactive ? 'button' : undefined"
    class="ui-list-item"
    :class="{ 'ui-list-item--interactive': interactive }"
    @click="interactive && emit('select')"
  >
    <span v-if="$slots.leading" class="ui-list-item__leading" aria-hidden="true">
      <slot name="leading" />
    </span>
    <span class="ui-list-item__copy">
      <strong><slot name="title">{{ title }}</slot></strong>
      <span v-if="description">{{ description }}</span>
    </span>
    <span v-if="meta || $slots.trailing" class="ui-list-item__trailing">
      <slot name="trailing">
        {{ meta }}
      </slot>
    </span>
  </component>
</template>
