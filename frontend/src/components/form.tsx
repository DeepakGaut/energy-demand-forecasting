import type { InputHTMLAttributes, ReactNode, SelectHTMLAttributes } from "react";
import { ciColor, ciLevel } from "@/lib/ci";

/** Labelled form row with optional hint text. */
export function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: ReactNode;
}) {
  return (
    <label className="block">
      <span className="mb-1 block text-sm font-medium text-fg">{label}</span>
      {children}
      {hint ? <span className="mt-1 block text-xs text-muted">{hint}</span> : null}
    </label>
  );
}

const controlClasses =
  "w-full rounded-lg border border-border bg-surface px-3 py-2 text-sm " +
  "outline-none transition-colors focus:border-brand focus:ring-2 focus:ring-brand/20 " +
  "disabled:cursor-not-allowed disabled:opacity-60";

export function TextInput(props: InputHTMLAttributes<HTMLInputElement>) {
  return <input {...props} className={controlClasses} />;
}

export function NumberInput(props: InputHTMLAttributes<HTMLInputElement>) {
  return <input type="number" {...props} className={controlClasses} />;
}

export function Select({
  children,
  ...props
}: SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select {...props} className={controlClasses}>
      {children}
    </select>
  );
}

/** Styled checkbox with an inline label, used for the urgency toggle. */
export function Toggle({
  label,
  hint,
  ...props
}: InputHTMLAttributes<HTMLInputElement> & { label: string; hint?: string }) {
  return (
    <label className="flex items-start gap-2">
      <input
        type="checkbox"
        {...props}
        className="mt-0.5 h-4 w-4 rounded border-border text-brand accent-brand"
      />
      <span>
        <span className="block text-sm font-medium text-fg">{label}</span>
        {hint ? <span className="block text-xs text-muted">{hint}</span> : null}
      </span>
    </label>
  );
}

export function Button({
  children,
  loading = false,
  variant = "primary",
  ...props
}: InputHTMLAttributes<HTMLButtonElement> & {
  loading?: boolean;
  variant?: "primary" | "secondary";
} & { type?: "button" | "submit" | "reset" }) {
  const base =
    "inline-flex items-center justify-center gap-2 rounded-lg px-4 py-2 text-sm font-medium " +
    "transition-colors disabled:cursor-not-allowed disabled:opacity-60";
  const styles =
    variant === "primary"
      ? "bg-brand text-white hover:bg-brand-dark"
      : "border border-border bg-surface text-fg hover:bg-surface-muted";
  return (
    <button
      {...props}
      disabled={loading || props.disabled}
      className={`${base} ${styles}`}
    >
      {loading ? <Spinner /> : null}
      {children}
    </button>
  );
}

export function Spinner() {
  return (
    <span
      className="inline-block h-4 w-4 animate-spin rounded-full border-2 border-current border-t-transparent"
      aria-hidden
    />
  );
}

/** Red error banner for failed API calls. */
export function ErrorBanner({ message }: { message: string }) {
  return (
    <div
      role="alert"
      className="rounded-lg border border-ci-veryhigh/30 bg-ci-veryhigh/10 px-4 py-3 text-sm text-ci-veryhigh"
    >
      {message}
    </div>
  );
}

/** A labelled statistic used inside result cards. */
export function Stat({
  label,
  value,
  sub,
}: {
  label: string;
  value: ReactNode;
  sub?: ReactNode;
}) {
  return (
    <div>
      <div className="text-xs uppercase tracking-wide text-muted">{label}</div>
      <div className="mt-0.5 text-xl font-semibold tabular-nums">{value}</div>
      {sub ? <div className="text-xs text-muted">{sub}</div> : null}
    </div>
  );
}

/** Carbon-intensity value with a color dot and severity label. */
export function CiBadge({ ci }: { ci: number }) {
  const { label } = ciLevel(ci);
  return (
    <span className="inline-flex items-center gap-2 text-sm">
      <span
        className="inline-block h-2.5 w-2.5 rounded-full"
        style={{ background: ciColor(ci) }}
        aria-hidden
      />
      <span className="font-medium tabular-nums">{ci.toFixed(1)}</span>
      <span className="text-muted">gCO₂e/kWh · {label}</span>
    </span>
  );
}

/** Small pill indicating whether CI is measured or forecasted. */
export function SourcePill({ source }: { source: "measured" | "forecasted" }) {
  const forecasted = source === "forecasted";
  return (
    <span
      className={
        "inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium " +
        (forecasted
          ? "bg-brand-soft text-brand-dark"
          : "bg-surface-muted text-muted")
      }
    >
      {forecasted ? "Forecasted CI" : "Measured CI"}
    </span>
  );
}
