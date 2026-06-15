import { ref } from 'vue'

const deferredPrompt = ref<any>(null)
const isInstallable = ref(false)
const isInstalled = ref(false)

function syncInstalledState() {
  if (typeof window === 'undefined') return

  const isStandaloneDisplay = typeof window.matchMedia === 'function'
    && window.matchMedia('(display-mode: standalone)').matches
  isInstalled.value = Boolean((window.navigator as any).standalone || isStandaloneDisplay)
}

if (typeof window !== 'undefined') {
  // Keep standalone state in sync even when the module loads after window.load.
  syncInstalledState()
  window.addEventListener('load', syncInstalledState)

  window.addEventListener('beforeinstallprompt', (e) => {
    // Prevent the mini-infobar from appearing on mobile
    e.preventDefault()
    // Stash the event so it can be triggered later.
    deferredPrompt.value = e
    ;(window as any).deferredPrompt = e
    // Update UI notify the user they can install the PWA
    isInstallable.value = true
    window.dispatchEvent(new Event('pwa-install-ready'))
    console.log('PWA: Ready to install')
  })

  window.addEventListener('appinstalled', () => {
    // Clear the deferredPrompt so it can be garbage collected
    deferredPrompt.value = null
    ;(window as any).deferredPrompt = null
    isInstallable.value = false
    syncInstalledState()
    isInstalled.value = true
    console.log('PWA: Application installed successfully')
  })
}

export function usePWAInstall() {
  const installApp = async () => {
    const prompt = deferredPrompt.value || (window as any).deferredPrompt
    if (!prompt) return false

    // Show the install prompt
    prompt.prompt()

    // Wait for the user to respond to the prompt
    const { outcome } = await prompt.userChoice
    console.log(`PWA: User response to the install prompt: ${outcome}`)

    // We've used the prompt, and can't use it again, throw it away
    deferredPrompt.value = null
    ;(window as any).deferredPrompt = null
    isInstallable.value = false
    
    return outcome === 'accepted'
  }

  return {
    isInstallable,
    isInstalled,
    installApp
  }
}
