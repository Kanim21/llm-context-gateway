import { Handle, Position, type NodeProps } from "@xyflow/react";
import { HANDLE_BASE } from "./handles";

const TARGET_LABEL: Record<string, string> = {
  webhook: "Webhook",
  blackboard: "Blackboard",
};

export function DispatchNode({ data, selected }: NodeProps) {
  const d = data as { label: string; target?: string };
  const target = d.target ?? "webhook";

  return (
    <div
      data-testid="dispatch-node"
      className={`w-64 rounded-xl border border-sky-800/50 bg-sky-950/20 p-3.5 transition-all ${
        selected ? "outline outline-2 outline-emerald-400/70" : "hover:border-sky-700"
      }`}
    >
      <Handle type="target" position={Position.Top} className={HANDLE_BASE} />

      <div className="flex items-center gap-2">
        <span className="flex h-7 w-7 items-center justify-center rounded-md bg-sky-500/15 text-sm ring-1 ring-inset ring-sky-500/30" aria-hidden>
          📤
        </span>
        <span className="truncate font-semibold text-zinc-100">{d.label || "Output Dispatcher"}</span>
      </div>
      <p className="mt-2 text-xs text-zinc-400">Delivers the final result downstream.</p>
      <div className="mt-2.5 border-t border-sky-900/40 pt-2">
        <span className="inline-flex items-center rounded-full bg-sky-500/15 px-2 py-0.5 text-[10px] font-medium text-sky-300 ring-1 ring-inset ring-sky-500/30">
          {TARGET_LABEL[target] ?? target}
        </span>
      </div>

      <Handle type="source" position={Position.Bottom} className={HANDLE_BASE} />
    </div>
  );
}
