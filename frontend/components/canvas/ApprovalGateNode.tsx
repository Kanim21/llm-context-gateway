import { useCanvasStore } from "@/lib/store/canvasStore";

interface ApprovalGateNodeProps {
  id: string;
  data: { label: string };
}

export function ApprovalGateNode({ id, data }: ApprovalGateNodeProps) {
  const state = useCanvasStore((s) => s.executionStateByStepId[id] ?? "idle");
  const border = state === "awaiting_approval" ? "border-amber-500 shadow-lg shadow-amber-200" : "border-gray-300";
  return (
    <div data-testid="approval-gate-card" data-state={state}
         className={`rounded-lg border-2 border-dashed bg-amber-50 p-3 ${border}`}>
      <div className="text-xs uppercase tracking-wide text-amber-700">Approval Gate</div>
      <div className="font-semibold">{data.label}</div>
    </div>
  );
}
