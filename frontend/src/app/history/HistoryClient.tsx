"use client";

import { useEffect, useMemo, useState } from "react";
import { api, ApiError } from "@/lib/api";
import { regionName } from "@/lib/ci";
import type { DecisionRecord } from "@/lib/types";
import { Card } from "@/components/ui";
import { ErrorBanner, Spinner, Stat } from "@/components/form";

/** Format an ISO timestamp as a compact, locale-aware date + time. */
function formatWhen(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/** Format a recommended run time, or note that the job should run immediately. */
function formatRecommendedTime(iso: string | null): string {
  if (!iso) return "Run now";
  return formatWhen(iso);
}

interface Summary {
  totalSaving: number;
  topRegion: string | null;
  topRegionCount: number;
}

/** Derive headline stats from the logged decisions. */
function summarise(decisions: DecisionRecord[]): Summary {
  let totalSaving = 0;
  const counts = new Map<string, number>();
  for (const d of decisions) {
    totalSaving += d.predicted_saving_gco2e;
    counts.set(
      d.recommended_region,
      (counts.get(d.recommended_region) ?? 0) + 1,
    );
  }
  let topRegion: string | null = null;
  let topRegionCount = 0;
  for (const [region, count] of counts) {
    if (count > topRegionCount) {
      topRegion = region;
      topRegionCount = count;
    }
  }
  return { totalSaving, topRegion, topRegionCount };
}

export default function HistoryClient() {
  const [decisions, setDecisions] = useState<DecisionRecord[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let active = true;
    api
      .decisions(200)
      .then((res) => active && setDecisions(res.decisions))
      .catch((err) => {
        if (!active) return;
        setError(
          err instanceof ApiError ? err.message : "Failed to load decisions.",
        );
      })
      .finally(() => active && setLoading(false));
    return () => {
      active = false;
    };
  }, []);

  const summary = useMemo(
    () => (decisions ? summarise(decisions) : null),
    [decisions],
  );

  if (loading) {
    return (
      <Card>
        <div className="flex h-40 items-center justify-center text-sm text-muted">
          <Spinner />
          <span className="ml-2">Loading decisions…</span>
        </div>
      </Card>
    );
  }

  if (error) {
    return <ErrorBanner message={error} />;
  }

  if (!decisions || decisions.length === 0) {
    return (
      <Card className="border-dashed">
        <p className="text-sm text-muted">
          No scheduling decisions logged yet. Recommendations you generate on the{" "}
          <span className="font-medium text-fg">Schedule</span> page will appear
          here.
        </p>
      </Card>
    );
  }

  return (
    <div className="grid gap-6">
      <div className="grid gap-4 sm:grid-cols-3">
        <Card>
          <Stat
            label="Total predicted savings"
            value={`${summary!.totalSaving.toFixed(1)} gCO₂e`}
            sub={`across ${decisions.length} decision${
              decisions.length === 1 ? "" : "s"
            }`}
          />
        </Card>
        <Card>
          <Stat
            label="Most-recommended region"
            value={summary!.topRegion ? regionName(summary!.topRegion) : "—"}
            sub={
              summary!.topRegion
                ? `${summary!.topRegionCount} of ${decisions.length} decisions`
                : undefined
            }
          />
        </Card>
        <Card>
          <Stat label="Decisions logged" value={decisions.length} />
        </Card>
      </div>

      <Card className="overflow-x-auto p-0">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-border text-left text-xs uppercase tracking-wide text-muted">
              <th className="px-4 py-3 font-medium">Submitted</th>
              <th className="px-4 py-3 font-medium">Job</th>
              <th className="px-4 py-3 font-medium">Default region</th>
              <th className="px-4 py-3 font-medium">Recommended region</th>
              <th className="px-4 py-3 font-medium">Recommended time</th>
              <th className="px-4 py-3 text-right font-medium">
                Predicted saving
              </th>
            </tr>
          </thead>
          <tbody>
            {decisions.map((d, i) => {
              const shifted = d.recommended_region !== d.default_region;
              return (
                <tr
                  key={`${d.job_id}-${i}`}
                  className="border-b border-border/60 last:border-0"
                >
                  <td className="whitespace-nowrap px-4 py-3 text-muted">
                    {formatWhen(d.submitted_at)}
                  </td>
                  <td className="px-4 py-3 font-mono text-xs">{d.job_id}</td>
                  <td className="whitespace-nowrap px-4 py-3">
                    {regionName(d.default_region)}
                  </td>
                  <td className="whitespace-nowrap px-4 py-3">
                    {shifted ? (
                      <span className="font-medium text-brand-dark">
                        {regionName(d.recommended_region)}
                      </span>
                    ) : (
                      <span className="text-muted">
                        {regionName(d.recommended_region)}
                      </span>
                    )}
                  </td>
                  <td className="whitespace-nowrap px-4 py-3 text-muted">
                    {formatRecommendedTime(d.recommended_time)}
                  </td>
                  <td className="whitespace-nowrap px-4 py-3 text-right tabular-nums">
                    {d.predicted_saving_gco2e.toFixed(1)}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </Card>
    </div>
  );
}
