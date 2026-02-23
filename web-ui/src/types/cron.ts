// Cron job types

export type TriggerType = 'at' | 'every' | 'cron';

export type PayloadKind = 'agent_turn' | 'system_event';

export type JobStatus = 'pending' | 'running' | 'success' | 'failed' | 'disabled';

// Trigger configuration
export interface CronTrigger {
  type: TriggerType;
  dateMs?: number;
  intervalSeconds?: number;
  cronExpr?: string;
  tz?: string;
}

// Payload configuration
export interface CronPayload {
  kind: PayloadKind;
  message: string;
  deliver: boolean;
  channel?: string;
  to?: string;
}

// Cron job
export interface CronJob {
  id: string;
  name: string;
  enabled: boolean;
  trigger: CronTrigger;
  payload: CronPayload;
  nextRunAtMs?: number;
  lastRunAtMs?: number;
  lastStatus?: JobStatus;
  lastError?: string;
  deleteAfterRun: boolean;
  createdAtMs: number;
  updatedAtMs: number;
}

// Request types
export interface CreateCronJobRequest {
  name: string;
  trigger: CronTrigger;
  payload: CronPayload;
  deleteAfterRun?: boolean;
}

export interface UpdateCronJobRequest {
  name?: string;
  enabled?: boolean;
  trigger?: CronTrigger;
  payload?: CronPayload;
  deleteAfterRun?: boolean;
}

// Response types
export interface CronJobListResponse {
  jobs: CronJob[];
}

export interface CronStatusResponse {
  enabled: boolean;
  running: boolean;
  nextJobs: CronJob[];
}
