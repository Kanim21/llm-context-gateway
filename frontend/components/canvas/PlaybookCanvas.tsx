"use client";

import { ReactFlow, Background, BackgroundVariant, Controls, type Node, type Edge } from "@xyflow/react";
import { TeammateCardNode } from "./TeammateCardNode";
import { ApprovalGateNode } from "./ApprovalGateNode";
import { PulseEdge } from "./PulseEdge";

const nodeTypes = {
  teammate: TeammateCardNode,
  approval_gate: ApprovalGateNode,
};

const edgeTypes = {
  default: PulseEdge,
};

const defaultEdgeOptions = { type: "default" as const };

interface PlaybookCanvasProps {
  nodes: Node[];
  edges: Edge[];
}

export function PlaybookCanvas({ nodes, edges }: PlaybookCanvasProps) {
  return (
    <div className="h-full w-full bg-zinc-950">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        edgeTypes={edgeTypes}
        defaultEdgeOptions={defaultEdgeOptions}
        fitView
        fitViewOptions={{ padding: 0.3 }}
        proOptions={{ hideAttribution: true }}
        minZoom={0.3}
      >
        <Background variant={BackgroundVariant.Dots} gap={24} size={1} color="#27272a" />
        <Controls showInteractive={false} />
      </ReactFlow>
    </div>
  );
}
