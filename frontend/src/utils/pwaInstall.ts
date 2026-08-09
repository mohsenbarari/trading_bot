import { ref } from 'vue'

interface BeforeInstallPromptEvent extends Event {
  prompt: () => Promise<void>
  userChoice: Promise<{ outcome: 'accepted' | 'dismissed' | string }>
}

type PWAWindow = Window & { deferredPrompt?: BeforeInstallPromptEvent | null }
type PWANavigator = Navigator & { standalone?: boolean }

const deferredPrompt = ref<BeforeInstallPromptEvent | null>(null)
const isInstallable = ref(false)
const isInstalled = ref(false)

function syncInstalledState() {
  if (typeof window === 'undefined') return

  const isStandaloneDisplay =
    typeof window.matchMedia === 'function' &&
    window.matchMedia('(display-mode: standalone)').matches
  isInstalled.value = Boolean((window.navigator as PWANavigator).standalone || isStandaloneDisplay)
}

if (typeof window !== 'undefined') {
  // Keep standalone state in sync even when the module loads after window.load.
  syncInstalledState()
  window.addEventListener('load', syncInstalledState)

  window.addEventListener('beforeinstallprompt', (event) => {
    const promptEvent = event as BeforeInstallPromptEvent
    // Prevent the mini-infobar from appearing on mobile
    promptEvent.preventDefault()
    // Stash the event so it can be triggered later.
    deferredPrompt.value = promptEvent
    ;(window as PWAWindow).deferredPrompt = promptEvent
    // Update UI notify the user they can install the PWA
    isInstallable.value = true
    window.dispatchEvent(new Event('pwa-install-ready'))
  })

  window.addEventListener('appinstalled', () => {
    // Clear the deferredPrompt so it can be garbage collected
    deferredPrompt.value = null
    ;(window as PWAWindow).deferredPrompt = null
    isInstallable.value = false
    syncInstalledState()
    isInstalled.value = true
  })
}

export function usePWAInstall() {
  const installApp = async () => {
    const prompt = deferredPrompt.value || (window as PWAWindow).deferredPrompt
    if (!prompt) return false

    try {
      // Both values are browser-owned promises. Await both so a rejected
      // prompt cannot escape as a detached unhandled rejection.
      await prompt.prompt()
      const { outcome } = await prompt.userChoice
      return outcome === 'accepted'
    } catch {
      return false
    } finally {
      // The browser prompt event is single-use regardless of its outcome.
      deferredPrompt.value = null
      ;(window as PWAWindow).deferredPrompt = null
      isInstallable.value = false
    }
  }

  return {
    isInstallable,
    isInstalled,
    installApp,
  }
}
