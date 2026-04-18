import { ref } from 'vue'

const deferredPrompt = ref<any>(null)
const isInstallable = ref(false)
const isInstalled = ref(false)

if (typeof window !== 'undefined') {
  // Check if already installed
  window.addEventListener('load', () => {
    if ((window.navigator as any).standalone || window.matchMedia('(display-mode: standalone)').matches) {
      isInstalled.value = true
    }
  })

  window.addEventListener('beforeinstallprompt', (e) => {
    // Prevent the mini-infobar from appearing on mobile
    e.preventDefault()
    // Stash the event so it can be triggered later.
    deferredPrompt.value = e
    // Update UI notify the user they can install the PWA
    isInstallable.value = true
    console.log('PWA: Ready to install')
  })

  window.addEventListener('appinstalled', () => {
    // Clear the deferredPrompt so it can be garbage collected
    deferredPrompt.value = null
    isInstallable.value = false
    isInstalled.value = true
    console.log('PWA: Application installed successfully')
  })
}

export function usePWAInstall() {
  const installApp = async () => {
    if (!deferredPrompt.value) return false

    // Show the install prompt
    deferredPrompt.value.prompt()

    // Wait for the user to respond to the prompt
    const { outcome } = await deferredPrompt.value.userChoice
    console.log(`PWA: User response to the install prompt: ${outcome}`)

    // We've used the prompt, and can't use it again, throw it away
    deferredPrompt.value = null
    isInstallable.value = false
    
    return outcome === 'accepted'
  }

  return {
    isInstallable,
    isInstalled,
    installApp
  }
}
