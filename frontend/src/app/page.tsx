import Link from "next/link";
import { Card, PageHeader } from "@/components/ui";

const FEATURES = [
  {
    href: "/calculator",
    title: "Calculator",
    body: "Estimate a job's energy use and CO₂e for a chosen region, hardware and runtime — with the CI source (measured vs forecasted) shown, not hidden.",
  },
  {
    href: "/schedule",
    title: "Schedule",
    body: "Submit a job with a flexibility window and urgency flag; get a full recommendation — greener region and/or time, predicted saving and confidence.",
  },
  {
    href: "/regions",
    title: "Regions",
    body: "Rank all five regional grids by current carbon intensity for your job, and see each region's 60-day forecast curve.",
  },
  {
    href: "/history",
    title: "History",
    body: "Review past scheduling recommendations and total predicted carbon savings across every logged decision.",
  },
];

export default function Home() {
  return (
    <div>
      <PageHeader
        title="Carbon-aware compute scheduling"
        subtitle="EcoCompute estimates the carbon footprint of compute jobs and recommends greener regions and run-times across India's five regional electricity grids, using each grid's forecasted carbon intensity."
      />

      <div className="grid gap-4 sm:grid-cols-2">
        {FEATURES.map((f) => (
          <Link key={f.href} href={f.href} className="group">
            <Card className="h-full transition-shadow group-hover:shadow-md">
              <h2 className="font-semibold text-brand-dark">{f.title}</h2>
              <p className="mt-2 text-sm text-muted">{f.body}</p>
            </Card>
          </Link>
        ))}
      </div>
    </div>
  );
}
