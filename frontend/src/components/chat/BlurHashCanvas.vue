<script setup lang="ts">
import { ref, onMounted, watch } from 'vue'
import { decode } from 'blurhash'

const props = defineProps<{
  hash: string
  width?: number
  height?: number
  punch?: number
}>()

const canvasRef = ref<HTMLCanvasElement | null>(null)

function render() {
  const canvas = canvasRef.value
  if (!canvas || !props.hash) return
  
  const w = props.width || 32
  const h = props.height || 32
  
  try {
    const pixels = decode(props.hash, w, h, props.punch || 1)
    canvas.width = w
    canvas.height = h
    const ctx = canvas.getContext('2d')
    if (!ctx) return
    const imageData = ctx.createImageData(w, h)
    imageData.data.set(pixels)
    ctx.putImageData(imageData, 0, 0)
  } catch (e) {
    // Invalid blurhash, silently fail
  }
}

onMounted(render)
watch(() => props.hash, render)
</script>

<template>
  <canvas 
    ref="canvasRef" 
    class="blurhash-canvas"
  />
</template>

<style scoped>
.blurhash-canvas {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
  position: absolute;
  inset: 0;
  border-radius: inherit;
}
</style>
