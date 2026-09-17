import type { Node, Edge } from "@xyflow/react";
import type { CanvasEdge, CanvasNode } from "@/types/api";

export const NODE_SPACING_X = 280;

/**
 * Turns a stored canvas_json into React Flow's node/edge shape.
 *
 * Positions are laid out left to right by index. Playbooks are a single
 * linear chain in v1, so this is enough to keep the cards from stacking on
 * top of each other -- no layout algorithm needed.
 */
export function toFlowGraph(
  canvasJson: { nodes: CanvasNode[]; edges: CanvasEdge[] },
): { nodes: Node[]; edges: Edge[] } {
  const nodes: Node[] = canvasJson.nodes
    .filter((n) => n.type === "teammate" || n.type === "approval_gate")
    .map((n, index) => ({
      id: n.id,
      type: n.type,
      data: n.data,
      position: { x: index * NODE_SPACING_X, y: 0 },
    }));
  const edges: Edge[] = canvasJson.edges.map((e, i) => ({
    id: `e${i}`, source: e.source, target: e.target,
  }));
  return { nodes, edges };
}
