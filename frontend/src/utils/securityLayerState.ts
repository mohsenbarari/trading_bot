import { computed, ref } from 'vue'

const activeSecurityLayers = ref<ReadonlySet<string>>(new Set())

export const isSecurityLayerActive = computed(() => activeSecurityLayers.value.size > 0)

export function setSecurityLayerActive(layerId: string, active: boolean) {
  const next = new Set(activeSecurityLayers.value)
  if (active) next.add(layerId)
  else next.delete(layerId)
  activeSecurityLayers.value = next
}

export function resetSecurityLayerStateForTests() {
  activeSecurityLayers.value = new Set()
}
