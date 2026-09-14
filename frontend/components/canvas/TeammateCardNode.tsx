import { useCanvasStore } from "@/lib/store/canvasStore";

const GLOW_BY_STATE: Record<string, string> = {
  idle: "border-gray-300",
  running: "border-blue-500 shadow-lg shadow-blue-200 animate-pulse",
  done: "border-green-500",
  awaiting_approval: "border-amber-500 shadow-lg shadow-amber-200",
  failed: "border-red-500",
};

interface TeammateCardNodeProps {
  id: string;
  data: { role: string; objective: string };
}

export function TeammateCardNode({ id, data }: TeammateCardNodeProps) {
  const state = useCanvasStore((s) => s.executionStateByStepId[id] ?? "idle");
  return (
    <div
      data-testid="teammate-card"
      data-state={state}
      className={`rounded-lg border-2 bg-white p-3 ${GLOW_BY_STATE[state]}`}
    >
      <div className="font-semibold">{data.role}</div>
      <div className="text-sm text-gray-600">{data.objective}</div>
    </div>
  );
}
