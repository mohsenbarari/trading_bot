import { computed, ref } from 'vue'
import { defineStore } from 'pinia'

export interface ChatTransferJob {
  id: string
  roomKey: number
  messageId?: number | null
  kind: 'upload' | 'download'
  status: 'queued' | 'running' | 'complete' | 'failed' | 'cancelled'
  progress: number
  updatedAt: number
}

export const useChatTransferStore = defineStore('chatTransfers', () => {
  const jobs = ref<Record<string, ChatTransferJob>>({})

  const activeJobs = computed(() => Object.values(jobs.value).filter((job) => (
    job.status === 'queued' || job.status === 'running'
  )))

  function upsertJob(job: ChatTransferJob) {
    jobs.value = {
      ...jobs.value,
      [job.id]: job,
    }
  }

  function patchJob(jobId: string, patch: Partial<ChatTransferJob>) {
    const current = jobs.value[jobId]
    if (!current) return
    upsertJob({ ...current, ...patch, updatedAt: Date.now() })
  }

  function removeJob(jobId: string) {
    const next = { ...jobs.value }
    delete next[jobId]
    jobs.value = next
  }

  return {
    jobs,
    activeJobs,
    upsertJob,
    patchJob,
    removeJob,
  }
})

