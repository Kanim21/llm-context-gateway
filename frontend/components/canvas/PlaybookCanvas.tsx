"use client";

import { ReactFlow, Background, Controls, type Node, type Edge } from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { TeammateCardNode } from "./TeammateCardNode";
import { ApprovalGateNode } from "./ApprovalGateNode";

const nodeTypes = {
  teammate: TeammateCardNode,
  approval_gate: ApprovalGateNode,
};

interface PlaybookCanvasProps {
  nodes: Node[];
  edges: Edge[];
}

export function PlaybookCanvas({ nodes, edges }: PlaybookCanvasProps) {
  return (
    <div style={{ width: "100%", height: "600px" }}>
      <ReactFlow nodes={nodes} edges={edges} nodeTypes={nodeTypes} fitView>
        <Background />
        <Controls />
      </ReactFlow>
    </div>
  );
}
