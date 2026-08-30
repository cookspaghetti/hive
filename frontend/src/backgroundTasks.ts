export type BackgroundTaskStatus = "queued" | "running" | "completed" | "failed";

export interface BackgroundTask {
  id: string;
  kind: "reanalysis";
  title: string;
  detail: string;
  statusUrl: string;
  targetId: string;
  status: BackgroundTaskStatus;
  stage: string;
  progress: number;
  createdTs: number;
  completedTs?: number;
  error?: string;
}

export function trackBackgroundTask(task: BackgroundTask): void {
  window.dispatchEvent(new CustomEvent<BackgroundTask>("hive:background-task", { detail: task }));
}

