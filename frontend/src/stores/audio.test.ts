import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it } from 'vitest'
import { useAudioStore } from './audio'

describe('audio store', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  it('tracks the current playing message and clears it on stopAll', () => {
    const store = useAudioStore()

    expect(store.currentPlayingId).toBeNull()

    store.setCurrentPlaying(42)
    expect(store.currentPlayingId).toBe(42)

    store.stopAll()
    expect(store.currentPlayingId).toBeNull()
  })
})