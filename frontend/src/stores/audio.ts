import { defineStore } from 'pinia'
import { ref } from 'vue'

export const useAudioStore = defineStore('audio', () => {
  const currentPlayingId = ref<number | null>(null)

  const setCurrentPlaying = (id: number | null) => {
    currentPlayingId.value = id
  }

  const stopAll = () => {
    currentPlayingId.value = null
  }

  return {
    currentPlayingId,
    setCurrentPlaying,
    stopAll
  }
})
