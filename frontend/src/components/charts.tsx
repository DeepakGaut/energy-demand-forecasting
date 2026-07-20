"use client";

import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { ciColor, regionColor, regionName } from "@/lib/ci";
import type { RegionComparison, RegionForecast } from "@/lib/types";

const axisStyle = { fontSize: 12, fill: "var(--color-muted)" };

/** Ranking of regions by current carbon intensity, colored by CI severity. */
export function RegionBarChart({ ranking }: { ranking: RegionComparison[] }) {
  const data = ranking.map((r) => ({
    region: r.region,
    ci: r.ci_gco2e_per_kwh,
    carbon: r.carbon_gco2e,
    pct: r.pct_vs_worst,
  }));

  return (
    <ResponsiveContainer width="100%" height={300}>
      <BarChart data={data} margin={{ top: 8, right: 8, bottom: 8, left: 8 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border)" />
        <XAxis dataKey="region" tick={axisStyle} stroke="var(--color-border)" />
        <YAxis
          tick={axisStyle}
          stroke="var(--color-border)"
          label={{
            value: "gCO₂e/kWh",
            angle: -90,
            position: "insideLeft",
            style: axisStyle,
          }}
        />
        <Tooltip
          cursor={{ fill: "var(--color-surface-muted)" }}
          content={<BarTooltip />}
        />
        <Bar dataKey="ci" radius={[4, 4, 0, 0]}>
          {data.map((d) => (
            <Cell key={d.region} fill={ciColor(d.ci)} />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}

interface BarTooltipProps {
  active?: boolean;
  payload?: { payload: { region: string; ci: number; carbon: number; pct: number } }[];
}

function BarTooltip({ active, payload }: BarTooltipProps) {
  if (!active || !payload?.length) return null;
  const d = payload[0].payload;
  return (
    <div className="rounded-lg border border-border bg-surface px-3 py-2 text-xs shadow-md">
      <div className="font-medium">
        {regionName(d.region)} ({d.region})
      </div>
      <div className="mt-1 tabular-nums">{d.ci.toFixed(1)} gCO₂e/kWh</div>
      <div className="tabular-nums text-muted">
        {d.carbon.toFixed(1)} gCO₂e for this job
      </div>
      <div className="tabular-nums text-muted">
        {d.pct.toFixed(1)}% less than worst
      </div>
    </div>
  );
}

/** 60-day forecasted CI curve per region, one line each. */
export function ForecastLineChart({
  forecasts,
}: {
  forecasts: RegionForecast[];
}) {
  if (forecasts.length === 0) return null;

  // Pivot per-region series into one row per date: {date, NR, SR, ...}.
  const dates = forecasts[0].forecast.map((p) => p.date);
  const rows = dates.map((date, i) => {
    const row: Record<string, string | number> = { date };
    for (const f of forecasts) {
      row[f.region] = f.forecast[i]?.ci_gco2e_per_kwh;
    }
    return row;
  });

  return (
    <ResponsiveContainer width="100%" height={340}>
      <LineChart data={rows} margin={{ top: 8, right: 8, bottom: 8, left: 8 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border)" />
        <XAxis
          dataKey="date"
          tick={axisStyle}
          stroke="var(--color-border)"
          interval={Math.max(0, Math.floor(rows.length / 8) - 1)}
          tickFormatter={(v: string) => v.slice(5)}
        />
        <YAxis
          tick={axisStyle}
          stroke="var(--color-border)"
          label={{
            value: "gCO₂e/kWh",
            angle: -90,
            position: "insideLeft",
            style: axisStyle,
          }}
        />
        <Tooltip content={<LineTooltip />} />
        <Legend
          formatter={(value: string) => (
            <span className="text-xs text-fg">{regionName(value)}</span>
          )}
        />
        {forecasts.map((f) => (
          <Line
            key={f.region}
            type="monotone"
            dataKey={f.region}
            stroke={regionColor(f.region)}
            strokeWidth={2}
            dot={false}
          />
        ))}
      </LineChart>
    </ResponsiveContainer>
  );
}

interface LineTooltipProps {
  active?: boolean;
  label?: string;
  payload?: { dataKey: string; value: number; color: string }[];
}

function LineTooltip({ active, label, payload }: LineTooltipProps) {
  if (!active || !payload?.length) return null;
  const sorted = [...payload].sort((a, b) => a.value - b.value);
  return (
    <div className="rounded-lg border border-border bg-surface px-3 py-2 text-xs shadow-md">
      <div className="font-medium">{label}</div>
      <div className="mt-1 space-y-0.5">
        {sorted.map((p) => (
          <div key={p.dataKey} className="flex items-center gap-2">
            <span
              className="inline-block h-2 w-2 rounded-full"
              style={{ background: p.color }}
              aria-hidden
            />
            <span className="text-muted">{regionName(p.dataKey)}</span>
            <span className="ml-auto tabular-nums">{p.value.toFixed(1)}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
