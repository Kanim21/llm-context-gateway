import type { Node, Edge } from "@xyflow/react";
import type { CanvasEdge, CanvasNode } from "@/types/api";
import { DEFAULT_MODEL_ID, tierForModel } from "@/lib/models/modelRegistry";

/**
 * Free-form drag-and-drop builder <-> backend canvas_json.
 *
 * The backend compiler (agent_gateway/orchestrator/compiler.py) accepts only a
 * single linear chain: exactly one start + one end, every non-end node with
 * exactly one outgoing edge, no orphans, no cycles. The canvas here lets you
 * drop and wire nodes freely; `validateChain` mirrors those rules so we only
 * PUT / run a graph the backend will accept, and surface friendly errors
 * otherwise.
 *
 * Node type mapping (React Flow type <-> backend type):
 *   agent          <-> teammate     (produces a runtime step)
 *   approval_gate  <-> approval_gate (produces a runtime step)
 *   compression    <-> compression  (visual: configures always-on gateway
 *                                     compression; produces no step)
 *   dispatch       <-> dispatch     (visual: marks the output sink; no step)
 *   start / end    <-> start / end
 */

export type BuilderKind = "agent" | "compression" | "approval_gate" | "dispatch";

export const V_SPACING = 148;
export const CENTER_X = 260;

let seq = 0;
export function newNodeId(kind: string): string {
  seq += 1;
  return `${kind}_${Date.now().toString(36)}${seq}${Math.random().toString(36).slice(2, 6)}`;
}

export interface PaletteItem {
  kind: BuilderKind;
  flowType: string;
  title: string;
  subtitle: string;
  icon: string;
  /** Tailwind classes for the palette icon chip. */
  accent: string;
}

export const PALETTE: PaletteItem[] = [
  {
    kind: "agent",
    flowType: "agent",
    title: "LLM Reasoning Agent",
    subtitle: "OpenAI · Anthropic · DeepSeek",
    icon: "🧠",
    accent: "bg-indigo-500/15 text-indigo-300 ring-1 ring-inset ring-indigo-500/30",
  },
  {
    kind: "compression",
    flowType: "compression",
    title: "Context Compression Filter",
    subtitle: "Trim tokens before the model",
    icon: "⚡",
    accent: "bg-emerald-500/15 text-emerald-300 ring-1 ring-inset ring-emerald-500/30",
  },
  {
    kind: "approval_gate",
    flowType: "approval_gate",
    title: "Human Approval Gate",
    subtitle: "Pause for human review",
    icon: "⚠",
    accent: "bg-amber-500/15 text-amber-300 ring-1 ring-inset ring-amber-500/30",
  },
  {
    kind: "dispatch",
    flowType: "dispatch",
    title: "Output Dispatcher",
    subtitle: "Webhook · Blackboard",
    icon: "📤",
    accent: "bg-sky-500/15 text-sky-300 ring-1 ring-inset ring-sky-500/30",
  },
];

export const DND_MIME = "application/agent-gateway-node";

/** Build a fresh React Flow node for a palette kind, positioned at `position`. */
export function createNode(kind: BuilderKind, position: { x: number; y: number }): Node {
  const id = newNodeId(kind);
  if (kind === "agent") {
    return {
      id,
      type: "agent",
      position,
      data: {
        role: "New Agent",
        objective: "",
        model: DEFAULT_MODEL_ID,
        tier: tierForModel(DEFAULT_MODEL_ID),
        connected_apps: [],
        knowledge: [],
        toolOutputMasking: true,
        promptCacheHashing: true,
        tokenBudgetCap: 8000,
      },
    };
  }
  if (kind === "approval_gate") {
    return {
      id,
      type: "approval_gate",
      position,
      data: { label: "Review before continuing", allow_edit: true },
    };
  }
  if (kind === "compression") {
    return {
      id,
      type: "compression",
      position,
      data: { label: "Compression Filter", level: "balanced" },
    };
  }
  return {
    id,
    type: "dispatch",
    position,
    data: { label: "Output Dispatcher", target: "webhook" },
  };
}

type Canvas = { nodes: CanvasNode[]; edges: CanvasEdge[] };

function normalizeData(node: CanvasNode): Record<string, unknown> {
  const data = { ...(node.data ?? {}) };
  if (node.type === "teammate") {
    if (!data.model) {
      // Best-effort: keep the stored tier by picking a model with that tier.
      data.model = DEFAULT_MODEL_ID;
    }
    data.tier = data.tier ?? tierForModel(data.model as string | undefined);
    data.connected_apps = data.connected_apps ?? [];
    data.knowledge = data.knowledge ?? [];
    data.toolOutputMasking = data.toolOutputMasking ?? true;
    data.promptCacheHashing = data.promptCacheHashing ?? true;
    data.tokenBudgetCap = data.tokenBudgetCap ?? 8000;
  }
  return data;
}

/** Chain order following the compiler's rule: start, then first edge, stop at end. */
export function linearOrder(canvas: Canvas): string[] {
  const byId = new Map(canvas.nodes.map((n) => [n.id, n]));
  const outgoing = new Map<string, string[]>();
  canvas.nodes.forEach((n) => outgoing.set(n.id, []));
  canvas.edges.forEach((e) => outgoing.get(e.source)?.push(e.target));
  const start = canvas.nodes.find((n) => n.type === "start");
  const order: string[] = [];
  const seen = new Set<string>();
  let cur: string | undefined = start?.id;
  while (cur && byId.has(cur) && !seen.has(cur)) {
    seen.add(cur);
    order.push(cur);
    if (byId.get(cur)!.type === "end") break;
    cur = (outgoing.get(cur) ?? [])[0];
  }
  return order;
}

/** canvas_json -> React Flow nodes/edges, laid out top-to-bottom by chain order. */
export function toBuilderGraph(canvas: Canvas): { nodes: Node[]; edges: Edge[] } {
  const order = linearOrder(canvas);
  const orderIndex = new Map(order.map((id, i) => [id, i]));
  let extra = order.length;
  const nodes: Node[] = canvas.nodes.map((n) => {
    const idx = orderIndex.has(n.id) ? orderIndex.get(n.id)! : extra++;
    const isEndpoint = n.type === "start" || n.type === "end";
    return {
      id: n.id,
      type: n.type === "teammate" ? "agent" : n.type,
      position: { x: CENTER_X, y: idx * V_SPACING },
      data: normalizeData(n),
      deletable: !isEndpoint,
    };
  });
  const edges: Edge[] = canvas.edges.map((e, i) => ({
    id: `e${i}`,
    source: e.source,
    target: e.target,
    type: "flow",
    animated: true,
  }));
  return { nodes, edges };
}

/** React Flow nodes/edges -> canvas_json for POST/PUT. */
export function fromBuilderGraph(nodes: Node[], edges: Edge[]): Canvas {
  const outNodes: CanvasNode[] = nodes.map((n) => {
    const type = n.type === "agent" ? "teammate" : n.type ?? "";
    const data: Record<string, unknown> = { ...(n.data as Record<string, unknown>) };
    if (type === "teammate") {
      data.tier = tierForModel(data.model as string | undefined);
    }
    return { id: n.id, type, data };
  });
  const outEdges: CanvasEdge[] = edges.map((e) => ({ source: e.source, target: e.target }));
  return { nodes: outNodes, edges: outEdges };
}

export function nodeLabel(node: Node): string {
  const data = (node.data ?? {}) as Record<string, unknown>;
  if (node.type === "agent") return String(data.role || "Agent");
  if (node.type === "approval_gate") return String(data.label || "Approval gate");
  if (node.type === "compression") return String(data.label || "Compression filter");
  if (node.type === "dispatch") return String(data.label || "Output dispatcher");
  if (node.type === "start") return "Start";
  if (node.type === "end") return "End";
  return node.id;
}

/**
 * Client-side mirror of compile_canvas's structural rules. Returns [] when the
 * graph would compile; otherwise friendly, node-named messages.
 */
export function validateChain(nodes: Node[], edges: Edge[]): string[] {
  const errs: string[] = [];
  const byId = new Map(nodes.map((n) => [n.id, n]));
  const starts = nodes.filter((n) => n.type === "start");
  const ends = nodes.filter((n) => n.type === "end");

  if (starts.length !== 1) errs.push("A playbook needs exactly one Start node.");
  if (ends.length < 1) errs.push("A playbook needs an End node.");

  const outgoing = new Map<string, string[]>();
  nodes.forEach((n) => outgoing.set(n.id, []));
  edges.forEach((e) => outgoing.get(e.source)?.push(e.target));

  const seen = new Set<string>();
  const order: string[] = [];
  if (starts.length === 1) {
    let cur: string | undefined = starts[0].id;
    while (cur && byId.has(cur) && !seen.has(cur)) {
      seen.add(cur);
      order.push(cur);
      if (byId.get(cur)!.type === "end") break;
      cur = (outgoing.get(cur) ?? [])[0];
    }
  }

  nodes.forEach((n) => {
    if (n.type === "end") return;
    const out = (outgoing.get(n.id) ?? []).length;
    if (out === 0) errs.push(`Connect "${nodeLabel(n)}" to the next step.`);
    else if (out > 1) errs.push(`"${nodeLabel(n)}" leads to more than one step — playbooks run one step at a time.`);
  });

  if (starts.length === 1) {
    nodes.forEach((n) => {
      if (!seen.has(n.id)) errs.push(`"${nodeLabel(n)}" isn't connected to the chain.`);
    });
  }

  order.forEach((id) => {
    const n = byId.get(id)!;
    const data = (n.data ?? {}) as Record<string, unknown>;
    if (n.type === "agent") {
      if (!String(data.role ?? "").trim()) errs.push(`"${nodeLabel(n)}" needs a role.`);
      if (!String(data.objective ?? "").trim()) errs.push(`"${nodeLabel(n)}" needs a task/objective.`);
    } else if (n.type === "approval_gate") {
      if (!String(data.label ?? "").trim()) errs.push("An approval gate needs a label.");
    }
  });

  return Array.from(new Set(errs));
}
