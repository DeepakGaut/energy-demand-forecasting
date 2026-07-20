import type { ReactNode } from "react";

/** Page title + optional subtitle, consistent across all routes. */
export function PageHeader({
  title,
  subtitle,
}: {
  title: string;
  subtitle?: string;
}) {
  return (
    <div className="mb-8">
      <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
      {subtitle ? (
        <p className="mt-1 max-w-2xl text-sm text-muted">{subtitle}</p>
      ) : null}
    </div>
  );
}

/** A neutral surface panel. */
export function Card({
  children,
  className = "",
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <div
      className={
        "rounded-xl border border-border bg-surface p-5 shadow-sm " + className
      }
    >
      {children}
    </div>
  );
}

/** Placeholder shown on pages that are built in later Phase 6 days. */
export function ComingSoon({ note }: { note: string }) {
  return (
    <Card className="border-dashed">
      <p className="text-sm text-muted">{note}</p>
    </Card>
  );
}
