import type { CanvasNode, CanvasEdge } from "@/types/api";
import type { TeammateDrawerConfig } from "@/components/drawer/TeammateDrawer";

type Canvas = { nodes: CanvasNode[]; edges: CanvasEdge[] };

/**
 * Insert a teammate immediately before the end node, preserving the single
 * linear chain: whoever pointed at `end` now points at the new teammate, and
 * the new teammate points at `end`. Returns a new canvas (no mutation), ready
 * to send to PUT /v1/playbooks/{id} where the backend recompiles + persists it.
 */
export function addTeammateToCanvas(
  canvas: Canvas,
  config: TeammateDrawerConfig,
  newId: string = `teammate_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`,
): Canvas {
  const end = canvas.nodes.find((n) => n.type === "end");
  if (!end) throw new Error("Canvas has no end node to insert before");

  const node: CanvasNode = {
    id: newId,
    type: "teammate",
    data: {
      role: config.role,
      objective: config.objective,
      tier: config.tier,
      knowledge: config.knowledge,
      connected_apps: config.connected_apps,
    },
  };

  const edges: CanvasEdge[] = canvas.edges.map((e) =>
    e.target === end.id ? { ...e, target: newId } : e,
  );
  edges.push({ source: newId, target: end.id });

  return { nodes: [...canvas.nodes, node], edges };
}
