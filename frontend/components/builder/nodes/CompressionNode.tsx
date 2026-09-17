import { Handle, Position, type NodeProps } from "@xyflow/react";
import { HANDLE_BASE } from "./handles";

const LEVEL_LABEL: Record<string, string> = {
  none: "Off",
  balanced: "Balanced",
  aggressive: "Aggressive",
};

export function CompressionNode({ data, selected }: NodeProps) {
  const d = data as { label: string; level?: string };
  const level = d.level ?? "balanced";

  return (
    <div
      data-testid="compression-node"
      className={`w-64 rounded-xl border border-emerald-800/50 bg-emerald-950/20 p-3.5 transition-all ${
        selected ? "outline outline-2 outline-emerald-400/70" : "hover:border-emerald-700"
      }`}
    >
      <Handle type="target" position={Position.Top} className={HANDLE_BASE} />

      <div className="flex items-center gap-2">
        <span className="flex h-7 w-7 items-center justify-center rounded-md bg-emerald-500/15 text-sm ring-1 ring-inset ring-emerald-500/30" aria-hidden>
          ⚡
        </span>
        <span className="truncate font-semibold text-zinc-100">{d.label || "Compression Filter"}</span>
      </div>
      <p className="mt-2 text-xs text-zinc-400">
        Gateway compression wraps the next agent&apos;s context.
      </p>
      <div className="mt-2.5 border-t border-emerald-900/40 pt-2">
        <span className="inline-flex items-center rounded-full bg-emerald-500/15 px-2 py-0.5 text-[10px] font-medium text-emerald-300 ring-1 ring-inset ring-emerald-500/30">
          {LEVEL_LABEL[level] ?? level}
        </span>
      </div>

      <Handle type="source" position={Position.Bottom} className={HANDLE_BASE} />
    </div>
  );
}
