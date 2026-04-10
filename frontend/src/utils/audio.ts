/**
 * Audio Utility
 * Generates notification sounds using the Web Audio API. 
 * This avoids external asset dependencies and works instantly.
 */

let audioCtx: AudioContext | null = null;

/**
 * Initialize or resume the AudioContext.
 * Must be called in response to a user gesture (click/touch).
 */
export const resumeAudioContext = async () => {
  if (!audioCtx) {
    audioCtx = new (window.AudioContext || (window as any).webkitAudioContext)();
  }
  if (audioCtx.state === 'suspended') {
    await audioCtx.resume();
  }
};

/**
 * Plays a pleasant notification "ping" sound.
 */
export const playNotificationSound = () => {
  if (!audioCtx) return;
  if (audioCtx.state === 'suspended') {
      // Logic for background play if context was already authorized earlier
      audioCtx.resume();
  }

  const now = audioCtx.currentTime;
  
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
  
  // Quick fade out
  gainNode.gain.setValueAtTime(0.1, startTime);
  gainNode.gain.exponentialRampToValueAtTime(0.001, startTime + duration);

  oscillator.connect(gainNode);
  gainNode.connect(audioCtx.destination);

  oscillator.start(startTime);
  oscillator.stop(startTime + duration);
}
