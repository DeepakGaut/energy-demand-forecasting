"use client";

import { useEffect, useMemo, useState } from "react";
import { api, ApiError } from "@/lib/api";
import { ciColor, REGION_CODES, regionName } from "@/lib/ci";
import type { HardwareSpec, ScheduleResponse } from "@/lib/types";
import { Card } from "@/components/ui";
import {
  Button,
  ErrorBanner,
  Field,
  NumberInput,
  Select,
  Spinner,
  Stat,
  Toggle,
} from "@/components/form";

const DEFAULT_PUE = 1.67;

const FLEX_OPTIONS = [
  { hours: 0, label: "None (run now)" },
  { hours: 24, label: "1 day" },
  { hours: 48, label: "2 days" },
  { hours: 72, label: "3 days" },
  { hours: 168, label: "1 week" },
  { hours: 336, label: "2 weeks" },
];

export default function ScheduleClient() {
  const [hardware, setHardware] = useState<HardwareSpec[]>([]);
  const [hwError, setHwError] = useState<string | null>(null);
  const [hwLoading, setHwLoading] = useState(true);

  const [model, setModel] = useState("");
  const [region, setRegion] = useState<string>("ER");
  const [nCores, setNCores] = useState("32");
  const [memoryGb, setMemoryGb] = useState("64");
  const [runtimeHours, setRuntimeHours] = useState("10");
  const [pue, setPue] = useState(String(DEFAULT_PUE));
  const [flexHours, setFlexHours] = useState("168");
  const [urgency, setUrgency] = useState(false);

  const [result, setResult] = useState<ScheduleResponse | null>(null);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    let active = true;
    api
      .hardware()
      .then((res) => {
        if (!active) return;
        setHardware(res.hardware);
        if (res.hardware.length > 0) setModel(res.hardware[0].model_name);
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
  }, []);

  const selectedHw = useMemo(
    () => hardware.find((h) => h.model_name === model),
    [hardware, model],
  );
  const maxCores = selectedHw?.total_cores;
  const coresExceeded = maxCores !== undefined && Number(nCores) > maxCores;

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setSubmitError(null);
    setResult(null);
    setSubmitting(true);
    try {
      const res = await api.schedule({
        region,
        model_name: model,
        n_cores: Number(nCores),
        runtime_hours: Number(runtimeHours),
        memory_gb: Number(memoryGb),
        pue: Number(pue),
        flexibility_window_hours: urgency ? 0 : Number(flexHours),
        urgency_flag: urgency,
      });
      setResult(res);
    } catch (err) {
      setSubmitError(
        err instanceof ApiError ? err.message : "Scheduling failed.",
      );
    } finally {
      setSubmitting(false);
    }
  }

  if (hwLoading) {
    return (
      <Card>
        <div className="flex items-center gap-2 text-sm text-muted">
          <Spinner /> Loading hardware…
        </div>
      </Card>
    );
  }

  if (hwError) {
    return <ErrorBanner message={hwError} />;
  }

  return (
    <div className="grid gap-6 lg:grid-cols-2">
      <Card>
        <form onSubmit={onSubmit} className="grid gap-4">
          <Field
            label="Hardware"
            hint={
              selectedHw
                ? `${selectedHw.hardware_type} · ${selectedHw.tdp_watts} W · ${selectedHw.total_cores} cores`
                : undefined
            }
          >
            <Select value={model} onChange={(e) => setModel(e.target.value)}>
              {hardware.map((h) => (
                <option key={h.model_name} value={h.model_name}>
                  {h.model_name} ({h.hardware_type})
                </option>
              ))}
            </Select>
          </Field>

          <Field label="Default region" hint="Where the job would run without scheduling.">
            <Select value={region} onChange={(e) => setRegion(e.target.value)}>
              {REGION_CODES.map((code) => (
                <option key={code} value={code}>
                  {regionName(code)} ({code})
                </option>
              ))}
            </Select>
          </Field>

          <div className="grid grid-cols-2 gap-4">
            <Field label="Cores" hint={maxCores ? `Max ${maxCores}` : undefined}>
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
          </div>

          <div className="grid grid-cols-2 gap-4">
            <Field label="Runtime (hours)">
              <NumberInput
                min={0}
                step="0.5"
                value={runtimeHours}
                onChange={(e) => setRuntimeHours(e.target.value)}
              />
            </Field>
            <Field label="PUE">
              <NumberInput
                min={1}
                step="0.01"
                value={pue}
                onChange={(e) => setPue(e.target.value)}
              />
            </Field>
          </div>

          <Field
            label="Flexibility window"
            hint="How long the job may be deferred to find a greener time."
          >
            <Select
              value={flexHours}
              onChange={(e) => setFlexHours(e.target.value)}
              disabled={urgency}
            >
              {FLEX_OPTIONS.map((o) => (
                <option key={o.hours} value={o.hours}>
                  {o.label}
                </option>
              ))}
            </Select>
          </Field>

          <Toggle
            label="Urgent — must run now"
            hint="Disables time shifting; only a greener region may be suggested."
            checked={urgency}
            onChange={(e) => setUrgency(e.target.checked)}
          />

          {coresExceeded ? (
            <p className="text-xs text-ci-veryhigh">
              Requested cores exceed this device&apos;s {maxCores} total cores.
            </p>
          ) : null}

          <Button type="submit" loading={submitting} disabled={coresExceeded}>
            Get recommendation
          </Button>
        </form>
      </Card>

      <div className="grid gap-4">
        {submitError ? <ErrorBanner message={submitError} /> : null}

        {result ? (
          <RecommendationCard result={result} />
        ) : !submitError ? (
          <Card className="border-dashed">
            <p className="text-sm text-muted">
              Submit a job to see the scheduler&apos;s recommendation here.
            </p>
          </Card>
        ) : null}
      </div>
    </div>
  );
}

function RecommendationCard({ result }: { result: ScheduleResponse }) {
  const regionShift = result.recommended_region !== result.default_region;
  const timeShift = result.recommended_time !== null;
  const savingPct =
    result.baseline_carbon_gco2e > 0
      ? (result.predicted_saving_gco2e / result.baseline_carbon_gco2e) * 100
      : 0;

  const headline = result.run_now
    ? `Run now in ${regionName(result.recommended_region)}`
    : [
        regionShift
          ? `Move to ${regionName(result.recommended_region)} (${result.recommended_region})`
          : `Stay in ${regionName(result.recommended_region)}`,
        timeShift
          ? `at ${new Date(result.recommended_time as string)
              .toISOString()
              .slice(0, 10)}`
          : null,
      ]
        .filter(Boolean)
        .join(" ");

  return (
    <Card>
      <div className="flex items-center justify-between">
        <h2 className="font-semibold">Recommendation</h2>
        <span className="text-xs text-muted">Job {result.job_id.slice(0, 8)}</span>
      </div>

      <div
        className="mt-3 rounded-lg px-4 py-3 text-white"
        style={{ background: ciColor(result.current_ci_gco2e_per_kwh) }}
      >
        <div className="text-xs uppercase tracking-wide opacity-90">
          {result.run_now ? "No greener option found" : "Recommended action"}
        </div>
        <div className="mt-0.5 text-lg font-semibold">{headline}</div>
      </div>

      <div className="mt-4 grid grid-cols-2 gap-4">
        <Stat
          label="Predicted saving"
          value={`${result.predicted_saving_gco2e.toFixed(1)} gCO₂e`}
          sub={`${savingPct.toFixed(1)}% vs run-now`}
        />
        <Stat
          label="Confidence"
          value={`${(result.confidence * 100).toFixed(0)}%`}
        />
      </div>

      <dl className="mt-4 grid grid-cols-2 gap-x-4 gap-y-2 border-t border-border pt-4 text-sm">
        <dt className="text-muted">Default region</dt>
        <dd className="text-right">
          {regionName(result.default_region)} ({result.default_region})
        </dd>
        <dt className="text-muted">Current CI</dt>
        <dd className="text-right tabular-nums">
          {result.current_ci_gco2e_per_kwh.toFixed(1)} gCO₂e/kWh
        </dd>
        <dt className="text-muted">Baseline carbon</dt>
        <dd className="text-right tabular-nums">
          {result.baseline_carbon_gco2e.toFixed(1)} gCO₂e
        </dd>
        <dt className="text-muted">Region shift saving</dt>
        <dd className="text-right tabular-nums">
          {result.spatial_saving_gco2e.toFixed(1)} gCO₂e
        </dd>
        <dt className="text-muted">Time shift saving</dt>
        <dd className="text-right tabular-nums">
          {result.temporal_saving_gco2e.toFixed(1)} gCO₂e
        </dd>
        <dt className="text-muted">Energy</dt>
        <dd className="text-right tabular-nums">
          {result.energy_kwh.toFixed(3)} kWh
        </dd>
      </dl>

      {regionShift && timeShift ? (
        <p className="mt-4 rounded-lg bg-surface-muted px-3 py-2 text-xs text-muted">
          When both a region and time shift are recommended, the predicted saving
          is an additive approximation (region + time) measured against the same
          run-now baseline and may slightly overstate the true combined saving.
        </p>
      ) : null}
    </Card>
  );
}
