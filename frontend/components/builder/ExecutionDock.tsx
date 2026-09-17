"use client";

import { useEffect, useRef } from "react";
import type { RunTelemetry } from "@/lib/run/useBuilderRun";

const STATUS_STYLE: Record<string, string> = {
  running: "bg-blue-500/15 text-blue-300 ring-1 ring-inset ring-blue-500/30",
  paused: "bg-amber-500/15 text-amber-300 ring-1 ring-inset ring-amber-500/30",
  completed: "bg-emerald-500/15 text-emerald-300 ring-1 ring-inset ring-emerald-500/30",
  rejected: "bg-red-500/15 text-red-300 ring-1 ring-inset ring-red-500/30",
  failed: "bg-red-500/15 text-red-300 ring-1 ring-inset ring-red-500/30",
};

function fmt(n: number): string {
  return n.toLocaleString("en-US");
}

interface ExecutionDockProps {
  open: boolean;
  onToggle: () => void;
  telemetry: RunTelemetry;
}

export function ExecutionDock({ open, onToggle, telemetry }: ExecutionDockProps) {
  const { status, lines, rawTotal, optimizedTotal, reductionPct, usdSavedPer1M, gate, approve, reject } = telemetry;
  const termRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = termRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [lines]);

  const started = status !== null;
  const savedTokens = Math.max(0, rawTotal - optimizedTotal);

  return (
    <section className="shrink-0 border-t border-zinc-800 bg-zinc-950">
      <div className="flex items-center justify-between px-5 py-2">
        <button onClick={onToggle} className="flex items-center gap-2 text-sm font-medium text-zinc-300 hover:text-zinc-100">
          <span className="text-zinc-500">{open ? "▾" : "▸"}</span>
          Execution & Cost Telemetry
        </button>
        <div className="flex items-center gap-3">
          {reductionPct !== null && (
            <span className="text-xs text-zinc-500">
              gateway reduction <span className="font-semibold text-emerald-400">{Math.round(reductionPct * 100)}%</span>
            </span>
          )}
          <span className={`rounded-full px-2.5 py-0.5 text-xs font-medium ${status ? STATUS_STYLE[status] ?? "bg-zinc-800 text-zinc-400" : "bg-zinc-800 text-zinc-500"}`}>
            {status ?? "idle"}
          </span>
        </div>
      </div>

      {open && (
        <div className="grid gap-px border-t border-zinc-800 bg-zinc-800 md:grid-cols-[1fr_320px]">
          {/* Streaming terminal */}
          <div ref={termRef} className="max-h-56 min-h-[168px] overflow-y-auto bg-zinc-950 p-3 font-mono text-xs leading-relaxed text-zinc-400">
            {!started && <p className="text-zinc-600">Run the playbook to stream execution against the live gateway.</p>}
            {lines.map((line, i) => (
              <div key={i} className="whitespace-pre-wrap text-zinc-300">
                {line}
              </div>
            ))}
          </div>

          {/* Savings counters + gate actions */}
          <div className="bg-zinc-950 p-3">
            <div className="grid grid-cols-2 gap-2">
              <div className="rounded-lg border border-zinc-800 bg-zinc-900 p-2.5">
                <p className="text-[10px] uppercase tracking-wide text-zinc-500">Raw tokens (est.)</p>
                <p className="mt-0.5 font-mono text-lg text-zinc-100">{fmt(rawTotal)}</p>
              </div>
              <div className="rounded-lg border border-zinc-800 bg-zinc-900 p-2.5">
                <p className="text-[10px] uppercase tracking-wide text-zinc-500">Optimized (est.)</p>
                <p className="mt-0.5 font-mono text-lg text-emerald-300">{fmt(optimizedTotal)}</p>
              </div>
            </div>
            <div className="mt-2 rounded-lg border border-emerald-900/50 bg-emerald-950/20 p-2.5">
              <p className="text-[10px] uppercase tracking-wide text-emerald-500/80">Est. saved · {fmt(savedTokens)} tokens</p>
              <p className="mt-0.5 font-mono text-lg text-emerald-300">${fmt(Math.round(usdSavedPer1M))} <span className="text-xs text-emerald-500/70">/ 1M runs</span></p>
            </div>

            {gate && (
              <div className="mt-2 rounded-lg border border-amber-800/60 bg-amber-950/25 p-2.5">
                <p className="text-xs text-amber-300">{gate.label}</p>
                <div className="mt-2 flex gap-2">
                  <button onClick={approve} className="flex-1 rounded-md bg-emerald-500 px-2 py-1.5 text-xs font-semibold text-zinc-950 hover:bg-emerald-400">Approve</button>
                  <button onClick={reject} className="flex-1 rounded-md bg-red-500/90 px-2 py-1.5 text-xs font-semibold text-white hover:bg-red-500">Reject</button>
                </div>
              </div>
            )}
            <p className="mt-2 text-[10px] leading-snug text-zinc-600">
              Reduction % is the live gateway metric. Token counts and $ are estimated from streamed output.
            </p>
          </div>
        </div>
      )}
    </section>
  );
}
