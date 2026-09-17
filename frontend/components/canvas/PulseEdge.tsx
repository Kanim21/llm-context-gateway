import { BaseEdge, EdgeLabelRenderer, getBezierPath, type EdgeProps } from "@xyflow/react";
import { useCanvasStore } from "@/lib/store/canvasStore";

/**
 * A bezier edge that pulses while execution is actively flowing across it --
 * source step already running/done and target step running/awaiting review.
 */
export function PulseEdge({
  id, sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition, source, target, markerEnd,
}: EdgeProps) {
  const [path, labelX, labelY] = getBezierPath({
    sourceX, sourceY, sourcePosition, targetX, targetY, targetPosition,
  });

  const sourceState = useCanvasStore((s) => s.executionStateByStepId[source] ?? "idle");
  const targetState = useCanvasStore((s) => s.executionStateByStepId[target] ?? "idle");
  const active =
    (sourceState === "running" || sourceState === "done") &&
    (targetState === "running" || targetState === "awaiting_approval");

  return (
    <>
      <BaseEdge id={id} path={path} markerEnd={markerEnd} className={active ? "edge-active" : undefined} />
      {active && (
        <EdgeLabelRenderer>
          <div
            style={{ transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)` }}
            className="absolute h-2 w-2 rounded-full bg-blue-400 shadow-[0_0_8px_2px_rgba(59,130,246,0.8)]"
          />
        </EdgeLabelRenderer>
      )}
    </>
  );
}
