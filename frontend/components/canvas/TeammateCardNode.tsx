import { Handle, Position } from "@xyflow/react";
import { useCanvasStore, type ExecutionState } from "@/lib/store/canvasStore";
import { TIER_BADGE_CLASSES, TIER_MODEL_LABEL } from "@/lib/models/tierModel";
import type { Tier } from "@/types/api";

const CARD_BORDER_BY_STATE: Record<ExecutionState, string> = {
  idle: "border-zinc-700",
  running: "border-blue-500 shadow-[0_0_16px_rgba(59,130,246,0.35)] animate-pulse-ring",
  done: "border-emerald-600",
  awaiting_approval: "border-amber-500 shadow-[0_0_16px_rgba(245,158,11,0.3)]",
  failed: "border-red-500 shadow-[0_0_16px_rgba(239,68,68,0.3)]",
};

const STATUS_DOT_BY_STATE: Record<ExecutionState, string> = {
  idle: "bg-zinc-600",
  running: "bg-blue-400 animate-pulse",
  done: "bg-emerald-500",
  awaiting_approval: "bg-amber-500",
  failed: "bg-red-500",
};

const STATUS_LABEL_BY_STATE: Record<ExecutionState, string> = {
  idle: "Idle",
  running: "Running",
  done: "Done",
  awaiting_approval: "Awaiting review",
  failed: "Failed",
};

interface TeammateCardNodeProps {
  id: string;
  data: { role: string; objective: string; tier?: Tier };
}

export function TeammateCardNode({ id, data }: TeammateCardNodeProps) {
  const state = useCanvasStore((s) => s.executionStateByStepId[id] ?? "idle");
  const tier = data.tier;

  return (
    <div
      data-testid="teammate-card"
      data-state={state}
      className={`w-64 rounded-xl border-2 bg-zinc-900 p-3.5 transition-shadow ${CARD_BORDER_BY_STATE[state]}`}
    >
      <Handle type="target" position={Position.Left} className="!h-2.5 !w-2.5 !border-zinc-600 !bg-zinc-800" />

      <div className="mb-2 flex items-center justify-between gap-2">
        <div className="flex min-w-0 items-center gap-2">
          <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md bg-zinc-800 text-sm" aria-hidden>
            🤖
          </span>
          <span className="truncate font-semibold text-zinc-100">{data.role}</span>
        </div>
        {tier && (
          <span
            className={`shrink-0 rounded-full px-2 py-0.5 text-[10px] font-medium ${TIER_BADGE_CLASSES[tier]}`}
            title={`Tier: ${tier}`}
          >
            {TIER_MODEL_LABEL[tier]}
          </span>
        )}
      </div>

      <p className="line-clamp-3 text-sm text-zinc-400">{data.objective}</p>

      <div className="mt-3 flex items-center gap-1.5 border-t border-zinc-800 pt-2">
        <span className={`h-1.5 w-1.5 rounded-full ${STATUS_DOT_BY_STATE[state]}`} />
        <span className="text-xs text-zinc-500">{STATUS_LABEL_BY_STATE[state]}</span>
      </div>

      <Handle type="source" position={Position.Right} className="!h-2.5 !w-2.5 !border-zinc-600 !bg-zinc-800" />
    </div>
  );
}
