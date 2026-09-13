/**
 * Typed client for the reconcile-agent FastAPI service.
 *
 * Every value the dashboard renders comes through here. There is no fixture
 * data, no sample JSON and no fallback constant anywhere in this module: if the
 * API is unreachable the caller gets an ApiError and renders an error state.
 */
import type {
  Health,
  IntegrationsResponse,
  RunDetail,
  RunSummary,
  Scenario,
  SeedResponse,
  StartRunResponse,
} from "./types";

export const API_BASE = (
  process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8000"
).replace(/\/$/, "");

export class ApiError extends Error {
  readonly status: number;
  readonly url: string;

  constructor(message: string, status: number, url: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.url = url;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const url = `${API_BASE}${path}`;
  let response: Response;
  try {
    response = await fetch(url, { cache: "no-store", ...init });
  } catch {
    throw new ApiError(
      `Cannot reach the API at ${API_BASE}. Is the FastAPI service running?`,
      0,
      url,
    );
  }
  if (!response.ok) {
    let detail = `HTTP ${response.status}`;
    try {
      const body = (await response.json()) as { detail?: string };
      if (body?.detail) detail = body.detail;
    } catch {
      /* response had no JSON body; the status line is the whole story */
    }
    throw new ApiError(detail, response.status, url);
  }
  return (await response.json()) as T;
}

export const api = {
  health: () => request<Health>("/api/health"),
  integrations: () => request<IntegrationsResponse>("/api/integrations"),
  scenarios: () => request<Scenario[]>("/api/scenarios"),
  seed: () => request<SeedResponse>("/api/seed", { method: "POST" }),
  runs: (limit = 50) => request<RunSummary[]>(`/api/runs?limit=${limit}`),
  run: (runId: string) => request<RunDetail>(`/api/runs/${runId}`),
  startRun: (injectFault?: string) =>
    request<StartRunResponse>(
      `/api/run${injectFault ? `?inject_fault=${encodeURIComponent(injectFault)}` : ""}`,
      { method: "POST" },
    ),
  startEval: (injectFault?: string) =>
    request<StartRunResponse>(
      `/api/eval${injectFault ? `?inject_fault=${encodeURIComponent(injectFault)}` : ""}`,
      { method: "POST" },
    ),
  streamUrl: (runId: string, since = 0) =>
    `${API_BASE}/api/stream/${runId}?since=${since}`,
};

export function formatPercent(value: number): string {
  return `${Math.round(value * 100)}%`;
}

export function formatClock(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

export function formatDuration(seconds: number | null): string {
  if (seconds == null) return "—";
  if (seconds < 60) return `${seconds.toFixed(1)}s`;
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds % 60);
  return `${m}m ${s}s`;
}
