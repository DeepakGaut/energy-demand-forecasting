"use client";

import { useEffect, useMemo, useState } from "react";
import { api, ApiError } from "@/lib/api";
import { REGION_CODES, regionName } from "@/lib/ci";
import type { CalculateResponse, HardwareSpec } from "@/lib/types";
import { Card } from "@/components/ui";
import {
  Button,
  CiBadge,
  ErrorBanner,
  Field,
  NumberInput,
  Select,
  SourcePill,
  Spinner,
  Stat,
  TextInput,
} from "@/components/form";

const DEFAULT_PUE = 1.67;

export default function CalculatorClient() {
  const [hardware, setHardware] = useState<HardwareSpec[]>([]);
  const [hwError, setHwError] = useState<string | null>(null);
  const [hwLoading, setHwLoading] = useState(true);

  const [model, setModel] = useState("");
  const [region, setRegion] = useState<string>("NR");
  const [nCores, setNCores] = useState("16");
  const [memoryGb, setMemoryGb] = useState("64");
  const [runtimeHours, setRuntimeHours] = useState("10");
  const [pue, setPue] = useState(String(DEFAULT_PUE));
  const [targetDate, setTargetDate] = useState("");

  const [result, setResult] = useState<CalculateResponse | null>(null);
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
  const coresExceeded =
    maxCores !== undefined && Number(nCores) > maxCores;

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setSubmitError(null);
    setResult(null);
    setSubmitting(true);
    try {
      const res = await api.calculate({
        region,
        model_name: model,
        n_cores: Number(nCores),
        runtime_hours: Number(runtimeHours),
        memory_gb: Number(memoryGb),
        pue: Number(pue),
        target_date: targetDate || null,
      });
      setResult(res);
    } catch (err) {
      setSubmitError(
        err instanceof ApiError ? err.message : "Calculation failed.",
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
          <Field label="Hardware" hint={
            selectedHw
              ? `${selectedHw.hardware_type} · ${selectedHw.tdp_watts} W · ${selectedHw.total_cores} cores`
              : undefined
          }>
            <Select value={model} onChange={(e) => setModel(e.target.value)}>
              {hardware.map((h) => (
                <option key={h.model_name} value={h.model_name}>
                  {h.model_name} ({h.hardware_type})
                </option>
              ))}
            </Select>
          </Field>

          <Field label="Region">
            <Select value={region} onChange={(e) => setRegion(e.target.value)}>
              {REGION_CODES.map((code) => (
                <option key={code} value={code}>
                  {regionName(code)} ({code})
                </option>
              ))}
            </Select>
          </Field>

          <div className="grid grid-cols-2 gap-4">
            <Field
              label="Cores"
              hint={maxCores ? `Max ${maxCores}` : undefined}
            >
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
            <Field label="PUE" hint="Data-centre overhead factor">
              <NumberInput
                min={1}
                step="0.01"
                value={pue}
                onChange={(e) => setPue(e.target.value)}
              />
            </Field>
          </div>

          <Field
            label="Target date (optional)"
            hint="Within ~60 days uses the forecasted CI; leave blank for today."
          >
            <TextInput
              type="date"
              value={targetDate}
              onChange={(e) => setTargetDate(e.target.value)}
            />
          </Field>

          {coresExceeded ? (
            <p className="text-xs text-ci-veryhigh">
              Requested cores exceed this device&apos;s {maxCores} total cores.
            </p>
          ) : null}

          <Button type="submit" loading={submitting} disabled={coresExceeded}>
            Calculate footprint
          </Button>
        </form>
      </Card>

      <div className="grid gap-4">
        {submitError ? <ErrorBanner message={submitError} /> : null}

        {result ? (
          <Card>
            <div className="flex items-center justify-between">
              <h2 className="font-semibold">Estimated footprint</h2>
              <SourcePill source={result.ci_source} />
            </div>

            <div className="mt-4 grid grid-cols-2 gap-4">
              <Stat
                label="Carbon"
                value={`${result.carbon_gco2e.toFixed(1)} gCO₂e`}
                sub={
                  result.carbon_gco2e >= 1000
                    ? `${(result.carbon_gco2e / 1000).toFixed(3)} kgCO₂e`
                    : undefined
                }
              />
              <Stat
                label="Energy"
                value={`${result.energy_kwh.toFixed(3)} kWh`}
              />
            </div>

            <div className="mt-4 border-t border-border pt-4">
              <div className="text-xs uppercase tracking-wide text-muted">
                Carbon intensity
              </div>
              <div className="mt-1">
                <CiBadge ci={result.ci_gco2e_per_kwh} />
              </div>
              <div className="mt-1 text-xs text-muted">
                {regionName(result.region)} ({result.region}) ·{" "}
                {new Date(result.ci_timestamp).toISOString().slice(0, 10)}
              </div>
            </div>

            <dl className="mt-4 grid grid-cols-2 gap-x-4 gap-y-2 border-t border-border pt-4 text-sm">
              <dt className="text-muted">Device</dt>
              <dd className="text-right">
                {result.model_name} ({result.hardware_type})
              </dd>
              <dt className="text-muted">TDP</dt>
              <dd className="text-right tabular-nums">{result.tdp_watts} W</dd>
              <dt className="text-muted">Power / core</dt>
              <dd className="text-right tabular-nums">
                {result.power_per_core_watts.toFixed(2)} W
              </dd>
              <dt className="text-muted">Cores × runtime</dt>
              <dd className="text-right tabular-nums">
                {result.n_cores} × {result.runtime_hours} h
              </dd>
              <dt className="text-muted">PUE</dt>
              <dd className="text-right tabular-nums">{result.pue}</dd>
            </dl>

            {result.assumption ? (
              <p className="mt-4 rounded-lg bg-surface-muted px-3 py-2 text-xs text-muted">
                {result.assumption}
              </p>
            ) : null}
          </Card>
        ) : !submitError ? (
          <Card className="border-dashed">
            <p className="text-sm text-muted">
              Fill in the job details and calculate to see its estimated carbon
              footprint here.
            </p>
          </Card>
        ) : null}
      </div>
    </div>
  );
}
