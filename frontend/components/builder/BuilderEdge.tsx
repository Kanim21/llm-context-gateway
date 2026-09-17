import { BaseEdge, getBezierPath, type EdgeProps } from "@xyflow/react";
import { useBuilderStore } from "@/lib/store/builderStore";

/** Animated bezier edge; brightens while execution is crossing it. */
export function BuilderEdge({
  id, sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition, source, target, markerEnd,
}: EdgeProps) {
  const [path] = getBezierPath({ sourceX, sourceY, sourcePosition, targetX, targetY, targetPosition });
  const sState = useBuilderStore((s) => s.runState[source] ?? "idle");
  const tState = useBuilderStore((s) => s.runState[target] ?? "idle");
  const active =
    (sState === "running" || sState === "done") &&
    (tState === "running" || tState === "awaiting_approval");

  return <BaseEdge id={id} path={path} markerEnd={markerEnd} className={active ? "edge-active" : "edge-flow"} />;
}
