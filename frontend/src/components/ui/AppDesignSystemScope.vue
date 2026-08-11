<script setup lang="ts">
import { computed, type Component } from 'vue'
import '../../styles/design-system-v2.tokens.css'
import '../../styles/design-system-v2.components.css'
import '../../styles/design-system-v2.stage5-account-deletion.css'
import '../../styles/design-system-v2.stage5-accountant.css'
import '../../styles/design-system-v2.stage5-customer.css'
import '../../styles/design-system-v2.stage5-overlays.css'
import {
  UI_DESIGN_SYSTEM_PORTAL_SCOPE_VALUE,
  UI_DESIGN_SYSTEM_SCOPE_VALUE,
  type UiDesignSystemScopeKind,
} from './uiDesignSystemScope'

defineOptions({
  inheritAttrs: false,
})

const props = withDefaults(
  defineProps<{
    as?: string | Component
    kind?: UiDesignSystemScopeKind
  }>(),
  {
    as: 'div',
    kind: 'root',
  },
)

const scopeValue = computed(() =>
  props.kind === 'portal' ? UI_DESIGN_SYSTEM_PORTAL_SCOPE_VALUE : UI_DESIGN_SYSTEM_SCOPE_VALUE,
)
</script>

<template>
  <component :is="props.as" v-bind="$attrs" class="ui-v2-scope" :data-ui-system="scopeValue">
    <slot />
  </component>
</template>
