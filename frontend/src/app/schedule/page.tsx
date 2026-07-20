import { PageHeader } from "@/components/ui";
import ScheduleClient from "./ScheduleClient";

export default function SchedulePage() {
  return (
    <div>
      <PageHeader
        title="Schedule a job"
        subtitle="Submit a job with a flexibility window and urgency flag to get a greener region and/or run-time recommendation."
      />
      <ScheduleClient />
    </div>
  );
}
