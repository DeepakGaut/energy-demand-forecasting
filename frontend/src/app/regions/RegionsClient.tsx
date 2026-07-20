"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { api, ApiError } from "@/lib/api";
import { regionName } from "@/lib/ci";
import type {
  CompareRegionsResponse,
  HardwareSpec,
  MultiRegionForecastResponse,
} from "@/lib/types";
import { Card } from "@/components/ui";
import { ForecastLineChart, RegionBarChart } from "@/components/charts";
import {
  Button,
  ErrorBanner,
  Field,
  NumberInput,
  Select,
  Spinner,
} from "@/components/form";

const DEFAULT_PUE = 1.67;

export default function RegionsClient() {
  const [hardware, setHardware] = useState<HardwareSpec[]>([]);
  const [hwError, setHwError] = useState<string | null>(null);
  const [hwLoading, setHwLoading] = useState(true);

  const [model, setModel] = useState("");
  const [nCores, setNCores] = useState("16");
  const [memoryGb, setMemoryGb] = useState("64");
  const [runtimeHours, setRuntimeHours] = useState("10");

  const [comparison, setComparison] = useState<CompareRegionsResponse | null>(
    null,
  );
  const [compareError, setCompareError] = useState<string | null>(null);
  const [comparing, setComparing] = useState(false);

  const [forecast, setForecast] = useState<MultiRegionForecastResponse | null>(
    null,
  );
  const [forecastError, setForecastError] = useState<string | null>(null);

  const selectedHw = useMemo(
    () => hardware.find((h) => h.model_name === model),
    [hardware, model],
  );
  const maxCores = selectedHw?.total_cores;
  const coresExceeded = maxCores !== undefined && Number(nCores) > maxCores;

  const loadComparison = useCallback(
    async (modelName: string) => {
      setCompareError(null);
      setComparing(true);
      try {
        const res = await api.compareRegions({
          model_name: modelName,
          n_cores: Number(nCores),
          runtime_hours: Number(runtimeHours),
          memory_gb: Number(memoryGb),
          pue: DEFAULT_PUE,
        });
        setComparison(res);
      } catch (err) {
        setCompareError(
          err instanceof ApiError ? err.message : "Failed to compare regions.",
        );
      } finally {
        setComparing(false);
      }
    },
    [nCores, runtimeHours, memoryGb],
  );

  // Load hardware, then run an initial comparison with the default device.
  useEffect(() => {
    let active = true;
    api
      .hardware()
      .then((res) => {
        if (!active) return;
        setHardware(res.hardware);
        if (res.hardware.length > 0) {
          const first = res.hardware[0].model_name;
          setModel(first);
          void loadComparison(first);
        }
      })
      .catch((err) => {
        if (!active) return;
        setHwError(
          err instanceof ApiError ? err.message : "Failed to load hardware list.",
        );
      })
      .finally(() => active && setHwLoading(false));
    return () => {
      active = false;
    };
    // Intentionally run once on mount; loadComparison uses current form defaults.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // The forecast is job-independent, so load it once on mount.
  useEffect(() => {
    let active = true;
    api
      .forecastCompare(60)
      .then((res) => active && setForecast(res))
      .catch((err) => {
        if (!active) return;
        setForecastError(
          err instanceof ApiError ? err.message : "Failed to load forecast.",
        );
      });
    return () => {
      active = false;
    };
  }, []);

  function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!coresExceeded) void loadComparison(model);
  }

  return (
    <div className="grid gap-6">
      <Card>
        <form
          onSubmit={onSubmit}
          className="grid items-end gap-4 sm:grid-cols-2 lg:grid-cols-6"
        >
          <div className="lg:col-span-2">
            <Field
              label="Hardware"
              hint={maxCores ? `Max ${maxCores} cores` : undefined}
            >
              <Select
                value={model}
                onChange={(e) => setModel(e.target.value)}
                disabled={hwLoading || !!hwError}
              >
                {hardware.map((h) => (
                  <option key={h.model_name} value={h.model_name}>
                    {h.model_name} ({h.hardware_type})
                  </option>
                ))}
              </Select>
            </Field>
          </div>
          <Field label="Cores">
            <NumberInput
              min={1}
              max={maxCores}
              value={nCores}
              onChange={(e) => setNCores(e.target.value)}
            />
          </Field>
          <Field label="Memory (GB)">
            <NumberInput
              min={0}
              value={memoryGb}
              onChange={(e) => setMemoryGb(e.target.value)}
            />
          </Field>
          <Field label="Runtime (h)">
            <NumberInput
              min={0}
              step="0.5"
              value={runtimeHours}
              onChange={(e) => setRuntimeHours(e.target.value)}
            />
          </Field>
          <Button type="submit" loading={comparing} disabled={coresExceeded}>
            Update
          </Button>
        </form>
        {coresExceeded ? (
          <p className="mt-2 text-xs text-ci-veryhigh">
            Requested cores exceed this device&apos;s {maxCores} total cores.
          </p>
        ) : null}
        {hwError ? (
          <div className="mt-3">
            <ErrorBanner message={hwError} />
          </div>
        ) : null}
      </Card>

      <Card>
        <div className="mb-4 flex items-baseline justify-between">
          <h2 className="font-semibold">Current carbon intensity by region</h2>
          {comparison ? (
            <span className="text-xs text-muted">
              Greenest:{" "}
              <span className="font-medium text-brand-dark">
                {regionName(comparison.greenest_region)}
              </span>{" "}
              · Worst: {regionName(comparison.worst_region)}
            </span>
          ) : null}
        </div>

        {compareError ? (
          <ErrorBanner message={compareError} />
        ) : comparison ? (
          <RegionBarChart ranking={comparison.ranking} />
        ) : (
          <ChartSkeleton />
        )}
      </Card>

      <Card>
        <div className="mb-1 flex items-baseline justify-between">
          <h2 className="font-semibold">60-day forecast by region</h2>
          {forecast ? (
            <span className="text-xs text-muted">
              from {forecast.forecasts[0]?.generated_from}
            </span>
          ) : null}
        </div>
        <p className="mb-4 max-w-3xl text-xs text-muted">
          Each line is a region&apos;s ARIMA-forecasted daily carbon intensity,
          remapped so day one is today. Values assume recent grid patterns
          persist; no data has been incorporated since the last observation.
        </p>

        {forecastError ? (
          <ErrorBanner message={forecastError} />
        ) : forecast ? (
          <ForecastLineChart forecasts={forecast.forecasts} />
        ) : (
          <ChartSkeleton />
        )}
      </Card>
    </div>
  );
}

function ChartSkeleton() {
  return (
    <div className="flex h-[300px] items-center justify-center text-sm text-muted">
      <Spinner />
      <span className="ml-2">Loading…</span>
    </div>
  );
}
