"use client";

import { useEffect, useMemo, useState } from "react";
import { useParams } from "next/navigation";
import { api } from "@/lib/api/client";
import { useRunEvents } from "@/lib/sse/useRunEvents";
import { useCanvasStore } from "@/lib/store/canvasStore";
import { toFlowGraph } from "@/lib/canvas/toFlowGraph";
import { deriveGateInfo } from "@/lib/run/deriveGateInfo";
import { executionStateFromEvent, executionStatesFromSnapshot } from "@/lib/run/executionStates";
import { PlaybookCanvas } from "@/components/canvas/PlaybookCanvas";
import { RunControlBar } from "@/components/run/RunControlBar";
import { StepOutputPane } from "@/components/run/StepOutputPane";
import { GateReviewPanel } from "@/components/run/GateReviewPanel";
import type { PlaybookDetail } from "@/types/api";

export default function RunPage() {
  const { runId } = useParams<{ id: string; runId: string }>();
  const { snapshot, events, latestEvent } = useRunEvents(runId);
  const [playbook, setPlaybook] = useState<PlaybookDetail | null>(null);

  const playbookId = snapshot?.playbook_id;
  useEffect(() => {
    if (!playbookId) return;
    let cancelled = false;
    api.getPlaybook(playbookId).then((detail) => {
      if (!cancelled) setPlaybook(detail);
    });
    return () => { cancelled = true; };
  }, [playbookId]);

  // Seed the canvas glow from the snapshot the page loaded with, so a step
  // that already ran still shows as done after a reload.
  useEffect(() => {
    if (!snapshot) return;
    const states = executionStatesFromSnapshot(snapshot);
    const { setExecutionState } = useCanvasStore.getState();
    for (const [stepId, state] of Object.entries(states)) setExecutionState(stepId, state);
  }, [snapshot]);

  // Then keep it moving as live events arrive.
  useEffect(() => {
    if (!playbook || events.length === 0) return;
    const { setExecutionState } = useCanvasStore.getState();
    for (const event of events) {
      const change = executionStateFromEvent(event, playbook);
      if (change) setExecutionState(change.stepId, change.state);
    }
  }, [events, playbook]);

  // Don't let one run's glow leak into the next run you look at.
  useEffect(() => () => useCanvasStore.getState().resetExecutionState(), [runId]);

  const graph = useMemo(
    () => (playbook ? toFlowGraph(playbook.canvas_json) : null),
    [playbook],
  );

  if (!snapshot) return <p>Loading…</p>;

  const gate = deriveGateInfo(snapshot, playbook, latestEvent);

  return (
    <main>
      <RunControlBar status={snapshot.status} />
      {graph && <PlaybookCanvas nodes={graph.nodes} edges={graph.edges} />}
      <StepOutputPane steps={snapshot.steps} />
      {gate && (
        <GateReviewPanel
          runId={runId}
          label={gate.label}
          allowEdit={gate.allow_edit}
          proposedOutput={gate.proposed_output}
          onDecided={() => window.location.reload()}
        />
      )}
    </main>
  );
}
