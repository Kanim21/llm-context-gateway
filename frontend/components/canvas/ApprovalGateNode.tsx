import { Handle, Position } from "@xyflow/react";
import { useCanvasStore } from "@/lib/store/canvasStore";

interface ApprovalGateNodeProps {
  id: string;
  data: { label: string };
}

export function ApprovalGateNode({ id, data }: ApprovalGateNodeProps) {
  const state = useCanvasStore((s) => s.executionStateByStepId[id] ?? "idle");
  const awaiting = state === "awaiting_approval";

  return (
    <div
      data-testid="approval-gate-card"
      data-state={state}
      className={`w-64 rounded-xl border-2 border-dashed bg-amber-950/30 p-3.5 transition-shadow ${
        awaiting ? "border-amber-400 shadow-[0_0_18px_rgba(251,191,36,0.35)] animate-pulse-ring" : "border-amber-800/60"
      }`}
    >
      <Handle type="target" position={Position.Left} className="!h-2.5 !w-2.5 !border-amber-700 !bg-amber-900" />

      <div className="mb-1.5 flex items-center gap-2">
        <span className="flex h-6 items-center gap-1 rounded-full bg-amber-500/15 px-2 text-[10px] font-semibold uppercase tracking-wide text-amber-400 ring-1 ring-inset ring-amber-500/40">
          ⚠ Approval Gate
        </span>
      </div>

      <div className="font-semibold text-zinc-100">{data.label}</div>

      {awaiting && (
        <p className="mt-2 text-xs font-medium text-amber-400">Waiting on human review</p>
      )}

      <Handle type="source" position={Position.Right} className="!h-2.5 !w-2.5 !border-amber-700 !bg-amber-900" />
    </div>
  );
}
