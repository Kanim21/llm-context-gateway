"use client";

import { useParams } from "next/navigation";
import { useRunEvents } from "@/lib/sse/useRunEvents";
import { RunControlBar } from "@/components/run/RunControlBar";
import { StepOutputPane } from "@/components/run/StepOutputPane";
import { GateReviewPanel } from "@/components/run/GateReviewPanel";

export default function RunPage() {
  const { runId } = useParams<{ id: string; runId: string }>();
  const { snapshot, latestEvent } = useRunEvents(runId);

  if (!snapshot) return <p>Loading…</p>;

  const gateEvent = latestEvent?.type === "gate_paused" ? latestEvent.data : null;

  return (
    <main>
      <RunControlBar status={snapshot.status} />
      <StepOutputPane steps={snapshot.steps} />
      {gateEvent && (
        <GateReviewPanel
          runId={runId}
          label={gateEvent.label}
          allowEdit={gateEvent.allow_edit}
          proposedOutput={gateEvent.proposed_output}
          onDecided={() => window.location.reload()}
        />
      )}
    </main>
  );
}
