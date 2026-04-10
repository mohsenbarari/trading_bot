/**
 * Audio Utility (Improved for Mobile Compatibility)
 * Generates notification sounds using the Web Audio API.
 * Optimized for iOS Safari (locking/unlocking) and Android (clipping prevention).
 */

let audioCtx: AudioContext | null = null;

/**
 * Initialize and fully unlock the AudioContext.
 * Must be called in response to a user gesture (click/touchstart).
 * This plays a silent sound to "prime" the browser's audio engine.
 */
export const unlockAudioContext = async () => {
    if (!audioCtx) {
        audioCtx = new (window.AudioContext || (window as any).webkitAudioContext)();
    }
    
    if (audioCtx.state === 'suspended') {
        await audioCtx.resume();
    }

    // Standard iOS Safari unlock mechanism: Play a tiny silent buffer
    const buffer = audioCtx.createBuffer(1, 1, 22050);
    const source = audioCtx.createBufferSource();
    source.buffer = buffer;
    source.connect(audioCtx.destination);
    source.start(0);
    
    console.log('AudioContext unlocked. State:', audioCtx.state);
};

// Re-export for compatibility
export const resumeAudioContext = unlockAudioContext;

/**
 * Plays a pleasant notification "ping" sound.
 */
export const playNotificationSound = () => {
    if (!audioCtx) return;

    if (audioCtx.state === 'suspended') {
        audioCtx.resume();
    }

    // Add a small Look-ahead delay (50ms) to ensure the hardware is ready 
    // and avoid the "pop" (clipping) sound on some Android devices.
    const now = audioCtx.currentTime + 0.05;

    // Note 1: G5
    playNote(783.99, now, 0.15);
    // Note 2: C6 (slightly after)
    playNote(1046.50, now + 0.1, 0.3);
};

function playNote(frequency: number, startTime: number, duration: number) {
    if (!audioCtx) return;

    const oscillator = audioCtx.createOscillator();
    const gainNode = audioCtx.createGain();

    oscillator.type = 'sine';
    oscillator.frequency.setValueAtTime(frequency, startTime);

    // Fade-in (10ms) to prevent "clipping" pop sound
    gainNode.gain.setValueAtTime(0, startTime);
    gainNode.gain.linearRampToValueAtTime(0.1, startTime + 0.01);
    
    // Fade-out (decay)
    gainNode.gain.exponentialRampToValueAtTime(0.001, startTime + duration);

    oscillator.connect(gainNode);
    gainNode.connect(audioCtx.destination);

    oscillator.start(startTime);
    oscillator.stop(startTime + duration);
}
