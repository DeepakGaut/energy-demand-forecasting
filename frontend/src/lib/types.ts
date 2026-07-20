/**
 * TypeScript types mirroring the FastAPI backend's request/response models.
 * Kept in sync with backend/main.py — if a Pydantic model changes there, update
 * the matching type here.
 */

/** The five Indian grid regions the backend forecasts. */
export type RegionCode = "NR" | "SR" | "ER" | "WR" | "NER";

// ---------------------------------------------------------------------------
// GET /hardware
// ---------------------------------------------------------------------------

export interface HardwareSpec {
  model_name: string;
  hardware_type: string;
  tdp_watts: number;
  total_cores: number;
}

export interface HardwareListResponse {
  count: number;
  hardware: HardwareSpec[];
}

// ---------------------------------------------------------------------------
// POST /calculate
// ---------------------------------------------------------------------------

export interface CalculateRequest {
  region: string;
  model_name: string;
  n_cores: number;
  runtime_hours: number;
  usage_factor?: number;
  memory_gb?: number;
  pue?: number;
  /** ISO date (YYYY-MM-DD). Defaults to today; drives measured vs forecasted CI. */
  target_date?: string | null;
}

export interface CalculateResponse {
  region: string;
  model_name: string;
  hardware_type: string;
  tdp_watts: number;
  total_cores: number;
  power_per_core_watts: number;
  ci_gco2e_per_kwh: number;
  ci_timestamp: string;
  ci_source: "measured" | "forecasted";
  assumption: string | null;
  runtime_hours: number;
  n_cores: number;
  usage_factor: number;
  memory_gb: number;
  pue: number;
  energy_kwh: number;
  carbon_gco2e: number;
}

// ---------------------------------------------------------------------------
// POST /schedule
// ---------------------------------------------------------------------------

export interface ScheduleRequest {
  region: string;
  model_name: string;
  n_cores: number;
  runtime_hours: number;
  usage_factor?: number;
  memory_gb?: number;
  pue?: number;
  flexibility_window_hours?: number;
  urgency_flag?: boolean;
  job_id?: string | null;
}

export interface ScheduleResponse {
  job_id: string;
  default_region: string;
  recommended_region: string;
  recommended_time: string | null;
  run_now: boolean;
  urgency_flag: boolean;
  urgency_weight: number;
  flexibility_window_hours: number;
  model_name: string;
  hardware_type: string;
  energy_kwh: number;
  current_ci_gco2e_per_kwh: number;
  baseline_carbon_gco2e: number;
  predicted_saving_gco2e: number;
  temporal_saving_gco2e: number;
  spatial_saving_gco2e: number;
  confidence: number;
}

// ---------------------------------------------------------------------------
// GET /compare-regions
// ---------------------------------------------------------------------------

export interface CompareRegionsQuery {
  model_name: string;
  n_cores: number;
  runtime_hours: number;
  usage_factor?: number;
  memory_gb?: number;
  pue?: number;
}

export interface RegionComparison {
  region: string;
  ci_gco2e_per_kwh: number;
  ci_timestamp: string;
  carbon_gco2e: number;
  pct_vs_worst: number;
}

export interface CompareRegionsResponse {
  model_name: string;
  hardware_type: string;
  n_cores: number;
  runtime_hours: number;
  usage_factor: number;
  memory_gb: number;
  pue: number;
  energy_kwh: number;
  greenest_region: string;
  worst_region: string;
  ci_source: "measured" | "forecasted";
  assumption: string | null;
  ranking: RegionComparison[];
}

// ---------------------------------------------------------------------------
// GET /forecast/compare
// ---------------------------------------------------------------------------

export interface ForecastPoint {
  date: string;
  ci_gco2e_per_kwh: number;
}

export interface RegionForecast {
  region: string;
  model: string;
  generated_from: string;
  last_real_data: string;
  assumption: string;
  forecast: ForecastPoint[];
}

export interface MultiRegionForecastResponse {
  granularity: string;
  horizon_days: number;
  forecasts: RegionForecast[];
}

// ---------------------------------------------------------------------------
// GET /decisions
// ---------------------------------------------------------------------------

export interface DecisionRecord {
  job_id: string;
  submitted_at: string;
  default_region: string;
  recommended_region: string;
  recommended_time: string | null;
  predicted_saving_gco2e: number;
}

export interface DecisionsResponse {
  count: number;
  limit: number;
  decisions: DecisionRecord[];
}
