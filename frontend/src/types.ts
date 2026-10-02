export interface Metrics {
  input_tokens: number | null; output_tokens: number | null; requests: number | null;
  input_tps: number | null; output_tps: number | null; running: number | null;
  waiting: number | null; ttft_ms: number | null; latency_ms: number | null; acceptance: number | null;
}
export interface Model {
  id: string; name: string; quantization: string; engine: string; version: string;
  kvcached: boolean; speculative: string; installed: boolean; running: boolean;
  min_available_gib: number;
  ready: boolean; status: string; port: number; alias: string; runtime: string;
  metrics: Metrics | null; error?: string; metrics_error?: string; installation_error?: string;
  gpu_memory_mib: number | null; started_at: number | null; session: string | null;
}
export interface Job {
  id: string; model: string; action: string; state: string; stage: string;
  started_at: number; finished_at: number | null; error: string | null;
}
export interface GPUProcess {pid: number; user: string; name: string; memory_mib: number | null; managed: boolean; model?: string}
export interface Snapshot {
  preview?: boolean;
  ts: number; error: string | null;
  host: {hostname: string; user: string; memory: {total_gib: number; available_gib: number; used_gib: number; swap_used_gib: number};
    gpu: {name: string; utilization: number | null; temperature: number | null; power: number | null} | null;
    processes: GPUProcess[]; foreign_processes: GPUProcess[]; gpu_error: string | null};
  models: Model[]; job: Job | null;
  history: {ts: number; gpu_util: number | null; ram_used: number | null; models: Record<string, Metrics & {session: string | null}>}[];
  events: {id: number; ts: number; level: string; message: string}[];
}
export interface Reply {
  content: string; elapsed_s: number; request_output_tps: number | null;
  usage: {prompt_tokens?: number; completion_tokens?: number; total_tokens?: number}; finish_reason: string;
}
