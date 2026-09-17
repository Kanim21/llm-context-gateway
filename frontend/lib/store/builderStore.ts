"use client";

import { create } from "zustand";
import {
  applyEdgeChanges,
  applyNodeChanges,
  type Connection,
  type Edge,
  type EdgeChange,
  type Node,
  type NodeChange,
} from "@xyflow/react";
import { tierForModel } from "@/lib/models/modelRegistry";

export type NodeRunState = "idle" | "running" | "done" | "awaiting_approval" | "failed";

export interface NodeSavings {
  raw: number;
  optimized: number;
}

interface BuilderState {
  nodes: Node[];
  edges: Edge[];
  selectedId: string | null;

  runState: Record<string, NodeRunState>;
  savingsByNode: Record<string, NodeSavings>;

  setGraph: (nodes: Node[], edges: Edge[]) => void;
  onNodesChange: (changes: NodeChange[]) => void;
  onEdgesChange: (changes: EdgeChange[]) => void;
  onConnect: (conn: Connection) => void;
  addNode: (node: Node) => void;
  removeNode: (id: string) => void;
  select: (id: string | null) => void;
  updateNodeData: (id: string, patch: Record<string, unknown>) => void;

  gateHandler: ((decision: "approve" | "reject") => void) | null;
  setGateHandler: (fn: ((decision: "approve" | "reject") => void) | null) => void;

  setNodeRunState: (id: string, state: NodeRunState) => void;
  setNodeSavings: (id: string, savings: NodeSavings) => void;
  resetRun: () => void;
}

const FLOW_EDGE = (source: string, target: string): Edge => ({
  id: `e_${source}__${target}`,
  source,
  target,
  type: "flow",
  animated: true,
});

export const useBuilderStore = create<BuilderState>((set, get) => ({
  nodes: [],
  edges: [],
  selectedId: null,
  runState: {},
  savingsByNode: {},
  gateHandler: null,

  setGraph: (nodes, edges) => set({ nodes, edges, selectedId: null }),

  onNodesChange: (changes) => set({ nodes: applyNodeChanges(changes, get().nodes) }),

  onEdgesChange: (changes) => set({ edges: applyEdgeChanges(changes, get().edges) }),

  // Linear chain: a node has at most one outgoing edge, so a new connection
  // from a source replaces whatever it pointed at before.
  onConnect: (conn) => {
    if (!conn.source || !conn.target || conn.source === conn.target) return;
    const kept = get().edges.filter((e) => e.source !== conn.source);
    set({ edges: [...kept, FLOW_EDGE(conn.source, conn.target)] });
  },

  addNode: (node) => set((s) => ({ nodes: [...s.nodes, node], selectedId: node.id })),

  removeNode: (id) =>
    set((s) => ({
      nodes: s.nodes.filter((n) => n.id !== id),
      edges: s.edges.filter((e) => e.source !== id && e.target !== id),
      selectedId: s.selectedId === id ? null : s.selectedId,
    })),

  select: (id) => set({ selectedId: id }),

  updateNodeData: (id, patch) =>
    set((s) => ({
      nodes: s.nodes.map((n) => {
        if (n.id !== id) return n;
        const data = { ...(n.data as Record<string, unknown>), ...patch };
        if ("model" in patch) data.tier = tierForModel(patch.model as string | undefined);
        return { ...n, data };
      }),
    })),

  setGateHandler: (fn) => set({ gateHandler: fn }),

  setNodeRunState: (id, state) =>
    set((s) => ({ runState: { ...s.runState, [id]: state } })),

  setNodeSavings: (id, savings) =>
    set((s) => ({ savingsByNode: { ...s.savingsByNode, [id]: savings } })),

  resetRun: () => set({ runState: {}, savingsByNode: {}, gateHandler: null }),
}));
