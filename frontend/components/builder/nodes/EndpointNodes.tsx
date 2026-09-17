import { Handle, Position } from "@xyflow/react";
import { HANDLE_BASE } from "./handles";

export function StartNode() {
  return (
    <div
      data-testid="start-node"
      className="flex items-center gap-2 rounded-full border border-emerald-600/60 bg-emerald-500/10 px-4 py-1.5 text-xs font-semibold text-emerald-300"
    >
      <span className="h-2 w-2 rounded-full bg-emerald-400" />
      Start
      <Handle type="source" position={Position.Bottom} className={HANDLE_BASE} />
    </div>
  );
}

export function EndNode() {
  return (
    <div
      data-testid="end-node"
      className="flex items-center gap-2 rounded-full border border-zinc-600 bg-zinc-800 px-4 py-1.5 text-xs font-semibold text-zinc-300"
    >
      <Handle type="target" position={Position.Top} className={HANDLE_BASE} />
      <span className="h-2 w-2 rounded-full bg-zinc-400" />
      End
    </div>
  );
}
