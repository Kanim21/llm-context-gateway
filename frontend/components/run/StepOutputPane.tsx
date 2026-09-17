"use client";

import { useEffect, useState } from "react";
import { api, type GatewayMetrics } from "@/lib/api/client";
import type { RunEvent, StepSnapshot } from "@/types/api";

const STATUS_LABEL: Record<StepSnapshot["status"], string> = {
  pending: "Pending",
  running: "Running",
  completed: "Completed",
  awaiting_approval: "Awaiting review",
  failed: "Failed",
  rejected: "Rejected",
};

const STATUS_DOT: Record<StepSnapshot["status"], string> = {
  pending: "bg-zinc-600",
  running: "bg-blue-400 animate-pulse",
  completed: "bg-emerald-500",
  awaiting_approval: "bg-amber-500",
  failed: "bg-red-500",
  rejected: "bg-zinc-500",
};

function describeEvent(event: RunEvent): string {
  switch (event.type) {
    case "step_started":
      return `${event.data.step_id} started`;
    case "step_completed":
      return `Step ${event.data.step_index} completed`;
    case "gate_paused":
      return `${event.data.step_id} paused for review`;
    case "run_completed":
      return "Run completed";
    case "run_rejected":
      return `Run rejected at step ${event.data.step_index}`;
    case "run_failed":
      return `Run failed at step ${event.data.step_index}: ${event.data.error}`;
    case "token":
      return "";
  }
}

interface StepOutputPaneProps {
  steps: StepSnapshot[];
  events?: RunEvent[];
}

export function StepOutputPane({ steps, events = [] }: StepOutputPaneProps) {
  const [open, setOpen] = useState(true);
  const [metrics, setMetrics] = useState<GatewayMetrics | null>(null);

  const runFinished = steps.some((s) => s.status === "completed") && steps.every(
    (s) => s.status === "completed" || s.status === "pending" || s.status === "failed" || s.status === "rejected",
  );

  useEffect(() => {
    if (typeof api.getMetrics !== "function") return;
    api.getMetrics().then(setMetrics).catch(() => {});
  }, [runFinished]);

  const eventLog = events.filter((e) => e.type !== "token" || false).map(describeEvent).filter(Boolean);

  return (
    <div className="border-t border-zinc-800 bg-zinc-950">
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center justify-between px-4 py-2 text-xs font-medium uppercase tracking-wide text-zinc-500 hover:text-zinc-300"
      >
        <span>Execution</span>
        <span>{open ? "Hide ▾" : "Show ▸"}</span>
      </button>

      {open && (
        <div className="grid max-h-72 grid-cols-1 gap-4 overflow-y-auto px-4 pb-4 md:grid-cols-3">
          <div className="md:col-span-2">
            <p className="mb-2 text-xs font-medium uppercase tracking-wide text-zinc-500">Steps</p>
            <div className="space-y-2">
              {steps.map((step) => (
                <div key={step.step_index} className="rounded-lg border border-zinc-800 bg-zinc-900/60 p-2.5">
                  <div className="flex items-center gap-2 text-sm">
                    <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${STATUS_DOT[step.status]}`} />
                    <span className="font-medium text-zinc-200">{step.step_id}</span>
                    <span className="text-zinc-500">— {STATUS_LABEL[step.status]}</span>
                  </div>
                  {step.output_json?.error ? (
                    <p role="alert" className="mt-1.5 text-sm text-red-400">
                      Something went wrong: {step.output_json.error}
                    </p>
                  ) : step.output_json?.text ? (
                    <p className="mt-1.5 whitespace-pre-wrap text-sm text-zinc-400">{step.output_json.text}</p>
                  ) : null}
                </div>
              ))}
            </div>

            {eventLog.length > 0 && (
              <div className="mt-3">
                <p className="mb-1.5 text-xs font-medium uppercase tracking-wide text-zinc-500">Stream</p>
                <ul className="space-y-1 text-xs text-zinc-500">
                  {eventLog.map((line, i) => (
                    <li key={i} className="truncate">• {line}</li>
                  ))}
                </ul>
              </div>
            )}
          </div>

          <div>
            <p className="mb-2 text-xs font-medium uppercase tracking-wide text-zinc-500">Token savings</p>
            {metrics ? (
              <div className="space-y-1.5">
                <MetricRow label="Token reduction" value={metrics.token_reduction_pct} />
                <MetricRow label="Cost reduction" value={metrics.cost_reduction_pct} />
                <MetricRow label="Call reduction" value={metrics.call_reduction_pct} />
              </div>
            ) : (
              <p className="text-xs text-zinc-600">Not available yet.</p>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

function MetricRow({ label, value }: { label: string; value: number }) {
  return (
    <div className="flex items-center justify-between rounded-lg border border-zinc-800 bg-zinc-900/60 px-2.5 py-1.5 text-xs">
      <span className="text-zinc-500">{label}</span>
      <span className="font-semibold text-emerald-400">{value.toFixed(1)}%</span>
    </div>
  );
}
