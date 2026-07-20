import { PageHeader } from "@/components/ui";
import CalculatorClient from "./CalculatorClient";

export default function CalculatorPage() {
  return (
    <div>
      <PageHeader
        title="Carbon calculator"
        subtitle="Estimate the energy use and CO₂e of a single compute job for a chosen region and hardware."
      />
      <CalculatorClient />
    </div>
  );
}
