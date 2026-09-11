import { useParams } from "react-router-dom";
import { LiveLog } from "../components/LiveLog";

export function RunLogs() {
  const { runId = "" } = useParams();
  return (
    <div className="panel" style={{ marginTop: 12 }}>
      <LiveLog runId={runId} />
    </div>
  );
}
