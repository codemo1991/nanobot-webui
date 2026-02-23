import { request } from '../api'
import type { CronJob, CronJobListResponse, CronStatusResponse } from '../types/cron'

export const cronApi = {
  // Get all cron jobs
  getJobs: (includeDisabled = false) =>
    request<CronJobListResponse>(`/cron/jobs?includeDisabled=${includeDisabled}`),

  // Get cron status
  getStatus: () =>
    request<CronStatusResponse>(`/cron/status`),

  // Create a new cron job (flat field format for backend)
  createJob: (job: {
    name: string;
    triggerType: string;
    triggerDateMs?: number;
    triggerIntervalSeconds?: number;
    triggerCronExpr?: string;
    triggerTz?: string;
    payloadKind: string;
    payloadMessage: string;
    payloadDeliver?: boolean;
    payloadChannel?: string;
    payloadTo?: string;
    deleteAfterRun?: boolean;
  }) =>
    request<CronJob>(`/cron/jobs`, {
      method: 'POST',
      body: JSON.stringify(job),
    }),

  // Update a cron job (flat field format for backend)
  updateJob: (jobId: string, updates: {
    name?: string;
    enabled?: boolean;
    triggerType?: string;
    triggerDateMs?: number;
    triggerIntervalSeconds?: number;
    triggerCronExpr?: string;
    triggerTz?: string;
    payloadKind?: string;
    payloadMessage?: string;
    payloadDeliver?: boolean;
    payloadChannel?: string;
    payloadTo?: string;
    deleteAfterRun?: boolean;
  }) =>
    request<CronJob>(`/cron/jobs/${jobId}`, {
      method: 'PATCH',
      body: JSON.stringify(updates),
    }),

  // Delete a cron job
  deleteJob: (jobId: string) =>
    request<{ deleted: boolean }>(`/cron/jobs/${jobId}`, {
      method: 'DELETE',
    }),

  // Manually run a cron job
  runJob: (jobId: string) =>
    request<{ success: boolean }>(`/cron/jobs/${jobId}/run`, {
      method: 'POST',
    }),
}
