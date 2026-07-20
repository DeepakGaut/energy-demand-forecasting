import { PageHeader } from "@/components/ui";
import HistoryClient from "./HistoryClient";

export default function HistoryPage() {
  return (
    <div>
      <PageHeader
        title="Decision history"
        subtitle="Past scheduling recommendations and total predicted carbon savings across every logged decision."
      />
      <HistoryClient />
    </div>
  );
}
