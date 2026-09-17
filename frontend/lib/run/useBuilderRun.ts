"use client";

import { useEffect, useRef, useState } from "react";
import { api, BASE_URL } from "@/lib/api/client";
import { useBuilderStore } from "@/lib/store/builderStore";
import type { PlaybookDetail, RunEvent } from "@/types/api";

const CHARS_PER_TOKEN = 4;
// Blended input price ($/1M tokens) used only for the labelled ESTIMATE in the
// dock. The reduction % is the real, measured gateway figure from /v1/metrics.
const USD_PER_1M_TOKENS = 2.5;

const EVENT_TYPES: RunEvent["type"][] = [
  "step_started", "token", "step_completed", "gate_paused", "run_completed", "run_rejected", "run_failed",
];

export interface RunGate {
  stepIndex: number;
  label: string;
  allowEdit: boolean;
}

export interface RunTelemetry {
  active: boolean;
  status: string | null;
  lines: string[];
  rawTotal: number;
  optimizedTotal: number;
  reductionPct: number | null;
  usdSavedPer1M: number;
  gate: RunGate | null;
  approve: () => void;
  reject: () => void;
}

export function useBuilderRun(runId: string | null, playbook: PlaybookDetail | null): RunTelemetry {
  const [status, setStatus] = useState<string | null>(null);
  const [lines, setLines] = useState<string[]>([]);
  const [rawTotal, setRawTotal] = useState(0);
  const [optimizedTotal, setOptimizedTotal] = useState(0);
  const [reductionPct, setReductionPct] = useState<number | null>(null);
  const [gate, setGate] = useState<RunGate | null>(null);

  const setNodeRunState = useBuilderStore((s) => s.setNodeRunState);
  const setNodeSavings = useBuilderStore((s) => s.setNodeSavings);
  const resetRun = useBuilderStore((s) => s.resetRun);
  const setGateHandler = useBuilderStore((s) => s.setGateHandler);

  const stepTokens = useRef<Record<number, number>>({});
  const reductionRef = useRef<number | null>(null);

  const stepIdAt = (index: number): string | undefined =>
    playbook?.definition_json.steps[index]?.step_id;

  const push = (line: string) => setLines((prev) => [...prev, line]);

  useEffect(() => {
    if (!runId) return;
    let cancelled = false;
    let source: EventSource | null = null;

    resetRun();
    setStatus("running");
    setLines([`▶ run ${runId} started`]);
    setRawTotal(0);
    setOptimizedTotal(0);
    setGate(null);
    stepTokens.current = {};

    api.getMetrics()
      .then((m) => {
        if (cancelled) return;
        const pct = typeof m.token_reduction_pct === "number" ? m.token_reduction_pct : null;
        reductionRef.current = pct;
        setReductionPct(pct);
      })
      .catch(() => {});

    const record = (index: number, optimized: number) => {
      const reduction = reductionRef.current;
      const raw = reduction && reduction < 1 ? Math.round(optimized / (1 - reduction)) : optimized;
      setOptimizedTotal((t) => t + optimized);
      setRawTotal((t) => t + raw);
      const nodeId = stepIdAt(index);
      if (nodeId) setNodeSavings(nodeId, { raw, optimized });
    };

    const onEvent = (type: RunEvent["type"], data: Record<string, unknown>) => {
      if (cancelled) return;
      if (type === "step_started") {
        const idx = data.step_index as number;
        const nodeId = stepIdAt(idx);
        if (nodeId) setNodeRunState(nodeId, "running");
        push(`▶ step ${idx + 1} started`);
      } else if (type === "token") {
        const idx = data.step_index as number;
        stepTokens.current[idx] = (stepTokens.current[idx] ?? 0) + 1;
      } else if (type === "step_completed") {
        const idx = data.step_index as number;
        const nodeId = stepIdAt(idx);
        if (nodeId) setNodeRunState(nodeId, "done");
        const text = ((data.output as { text?: string })?.text ?? "");
        const optimized = stepTokens.current[idx] ?? Math.ceil(text.length / CHARS_PER_TOKEN);
        record(idx, optimized);
        push(`✓ step ${idx + 1} complete (${optimized} tok)`);
      } else if (type === "gate_paused") {
        const idx = data.step_index as number;
        const nodeId = stepIdAt(idx);
        if (nodeId) setNodeRunState(nodeId, "awaiting_approval");
        setStatus("paused");
        setGate({ stepIndex: idx, label: String(data.label ?? "Approval"), allowEdit: Boolean(data.allow_edit) });
        push(`⏸ awaiting approval: ${String(data.label ?? "")}`);
      } else if (type === "run_completed") {
        setStatus("completed");
        push("● run complete");
      } else if (type === "run_rejected") {
        setStatus("rejected");
        const nodeId = stepIdAt(data.step_index as number);
        if (nodeId) setNodeRunState(nodeId, "failed");
        push("✕ run rejected");
      } else if (type === "run_failed") {
        setStatus("failed");
        const nodeId = stepIdAt(data.step_index as number);
        if (nodeId) setNodeRunState(nodeId, "failed");
        push(`✕ run failed: ${String(data.error ?? "")}`);
      }
    };

    source = new EventSource(`${BASE_URL}/v1/playbooks/runs/${runId}/events`);
    for (const type of EVENT_TYPES) {
      source.addEventListener(type, (event) => {
        try {
          onEvent(type, JSON.parse((event as MessageEvent).data));
        } catch {
          /* ignore malformed frame */
        }
      });
    }
    source.onerror = () => {
      // Terminal frames arrive then the stream closes; that's expected.
    };

    return () => {
      cancelled = true;
      source?.close();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runId, playbook]);

  const submit = (decision: "approve" | "reject") => {
    if (!runId || !gate) return;
    api.submitGateDecision(runId, { decision, step_index: gate.stepIndex }).catch(() => {});
    setGate(null);
    setStatus("running");
  };

  useEffect(() => {
    setGateHandler(gate ? (d) => submit(d) : null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [gate, runId]);

  const usdSavedPer1M = Math.max(0, (rawTotal - optimizedTotal)) * USD_PER_1M_TOKENS;

  return {
    active: status === "running" || status === "paused",
    status,
    lines,
    rawTotal,
    optimizedTotal,
    reductionPct,
    usdSavedPer1M,
    gate,
    approve: () => submit("approve"),
    reject: () => submit("reject"),
  };
}
