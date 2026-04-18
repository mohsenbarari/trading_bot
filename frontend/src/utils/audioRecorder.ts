export class AudioRecorder {
  private mediaRecorder: MediaRecorder | null = null;
  private audioChunks: Blob[] = [];
  private stream: MediaStream | null = null;
  private startTime: number = 0;
  private timerInterval: number | null = null;
  public isRecording = false;

  constructor(private onTimeUpdate: (ms: number) => void) {}

  async start() {
    try {
      this.stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      this.mediaRecorder = new MediaRecorder(this.stream);
      this.audioChunks = [];

      this.mediaRecorder.addEventListener('dataavailable', (event) => {
        if (event.data.size > 0) {
          this.audioChunks.push(event.data);
        }
      });

      this.mediaRecorder.start(100); // collect data every 100ms
      this.isRecording = true;
      this.startTime = Date.now();
      
      this.timerInterval = window.setInterval(() => {
        this.onTimeUpdate(Date.now() - this.startTime);
      }, 100);

    } catch (error) {
      console.error('Error accessing microphone:', error);
      throw error;
    }
  }

  stop(): Promise<Blob | null> {
    return new Promise((resolve) => {
      if (!this.mediaRecorder || this.mediaRecorder.state === 'inactive') {
        resolve(null);
        return;
      }

      this.mediaRecorder.addEventListener('stop', () => {
        this.isRecording = false;
        if (this.timerInterval) clearInterval(this.timerInterval);
        
        // Ensure stream tracks are stopped to release mic
        if (this.stream) {
          this.stream.getTracks().forEach(track => track.stop());
        }
        
        const audioBlob = new Blob(this.audioChunks, { type: this.mediaRecorder?.mimeType || 'audio/webm' });
        resolve(audioBlob);
      });

      this.mediaRecorder.stop();
    });
  }

  cancel() {
    if (this.mediaRecorder && this.mediaRecorder.state !== 'inactive') {
      this.mediaRecorder.stop();
    }
    this.isRecording = false;
    if (this.timerInterval) clearInterval(this.timerInterval);
    if (this.stream) {
      this.stream.getTracks().forEach(track => track.stop());
    }
    this.audioChunks = [];
  }
}
