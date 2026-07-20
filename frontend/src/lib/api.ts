/**
 * Typed API client for the EcoCompute backend.
 *
 * Every page goes through these helpers rather than hand-rolling fetch calls, so
 * request/response shapes stay consistent and errors surface uniformly. The
 * backend base URL comes from NEXT_PUBLIC_API_BASE_URL (see .env.local).
 */
import type {
  CalculateRequest,
  CalculateResponse,
  CompareRegionsQuery,
  CompareRegionsResponse,
  DecisionsResponse,
  HardwareListResponse,
  MultiRegionForecastResponse,
  ScheduleRequest,
  ScheduleResponse,
} from "./types";

const BASE_URL = (
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000"
).replace(/\/+$/, "");

/** Error carrying the HTTP status so pages can render precise error states. */
export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly detail?: unknown,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

/** Drop undefined/null values and stringify the rest for a query string. */
function toQuery(params: Record<string, unknown>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null) {
      search.set(key, String(value));
    }
  }
  const qs = search.toString();
  return qs ? `?${qs}` : "";
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${BASE_URL}${path}`, {
      ...init,
      headers: {
        "Content-Type": "application/json",
        ...(init?.headers ?? {}),
      },
      cache: "no-store",
    });
  } catch {
    // Network failure / backend unreachable.
    throw new ApiError(
      "Cannot reach the backend. Is it running?",
      0,
    );
  }

  const text = await res.text();
  const body = text ? safeJsonParse(text) : null;

  if (!res.ok) {
    const detail =
      body && typeof body === "object" && "detail" in body
        ? (body as { detail: unknown }).detail
        : text;
    throw new ApiError(
      typeof detail === "string" ? detail : `Request failed (${res.status})`,
      res.status,
      detail,
    );
  }

  return body as T;
}

function safeJsonParse(text: string): unknown {
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

export const api = {
  health(): Promise<{ postgres: string; redis: string }> {
    return request("/health");
  },

  hardware(): Promise<HardwareListResponse> {
    return request("/hardware");
  },

  calculate(body: CalculateRequest): Promise<CalculateResponse> {
    return request("/calculate", { method: "POST", body: JSON.stringify(body) });
  },

  schedule(body: ScheduleRequest): Promise<ScheduleResponse> {
    return request("/schedule", { method: "POST", body: JSON.stringify(body) });
  },

  compareRegions(query: CompareRegionsQuery): Promise<CompareRegionsResponse> {
    return request(
      `/compare-regions${toQuery(query as unknown as Record<string, unknown>)}`,
    );
  },

  forecastCompare(horizon = 60): Promise<MultiRegionForecastResponse> {
    return request(`/forecast/compare${toQuery({ horizon })}`);
  },

  decisions(limit = 50): Promise<DecisionsResponse> {
    return request(`/decisions${toQuery({ limit })}`);
  },
};
