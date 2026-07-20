import { PageHeader } from "@/components/ui";
import RegionsClient from "./RegionsClient";

export default function RegionsPage() {
  return (
    <div>
      <PageHeader
        title="Region comparison"
        subtitle="Rank all five regional grids by current carbon intensity and view their 60-day forecast curves."
      />
      <RegionsClient />
    </div>
  );
}
