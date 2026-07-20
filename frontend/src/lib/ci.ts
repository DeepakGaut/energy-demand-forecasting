/**
 * Carbon-intensity color coding and region metadata.
 *
 * The whole UI communicates "how green is this?" through color. `ciColor` maps a
 * gCO2e/kWh value onto a continuous green -> amber -> red scale so charts, badges
 * and result cards all speak the same visual language.
 */
import type { RegionCode } from "./types";

/** Human-readable names for the five Indian grid regions. */
export const REGION_NAMES: Record<RegionCode, string> = {
  NR: "Northern",
  SR: "Southern",
  ER: "Eastern",
  WR: "Western",
  NER: "North-Eastern",
};

export const REGION_CODES: RegionCode[] = ["NR", "SR", "ER", "WR", "NER"];

/**
 * Fixed, visually distinct colors per region for multi-series charts (e.g. the
 * 60-day forecast lines), where a single CI-based color can't represent a line
 * that spans many values.
 */
export const REGION_COLORS: Record<RegionCode, string> = {
  NR: "#2563eb", // blue
  SR: "#7c3aed", // violet
  ER: "#dc2626", // red
  WR: "#ea580c", // orange
  NER: "#16a34a", // green
};

export function regionColor(code: string): string {
  return REGION_COLORS[code as RegionCode] ?? "#64748b";
}

export function regionName(code: string): string {
  return REGION_NAMES[code as RegionCode] ?? code;
}

/**
 * Domain of the color scale (gCO2e/kWh). Chosen to bracket the real data range
 * (~240 greenest to ~925 dirtiest) with a little headroom so the extremes don't
 * saturate immediately.
 */
const CI_MIN = 200;
const CI_MAX = 950;

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

/**
 * Map a CI value to a color on a green (low) -> amber -> red (high) scale.
 * Returns an `hsl(...)` string suitable for inline styles, chart fills, etc.
 */
export function ciColor(ci: number): string {
  const t = (clamp(ci, CI_MIN, CI_MAX) - CI_MIN) / (CI_MAX - CI_MIN);
  // Hue 140 (green) at t=0 down to hue 0 (red) at t=1.
  const hue = 140 * (1 - t);
  return `hsl(${Math.round(hue)}, 68%, 42%)`;
}

export type CiLevel = "very-low" | "low" | "medium" | "high" | "very-high";

/** Bucket a CI value into a coarse level with a human label. */
export function ciLevel(ci: number): { level: CiLevel; label: string } {
  if (ci < 300) return { level: "very-low", label: "Very low carbon" };
  if (ci < 500) return { level: "low", label: "Low carbon" };
  if (ci < 650) return { level: "medium", label: "Moderate carbon" };
  if (ci < 800) return { level: "high", label: "High carbon" };
  return { level: "very-high", label: "Very high carbon" };
}
