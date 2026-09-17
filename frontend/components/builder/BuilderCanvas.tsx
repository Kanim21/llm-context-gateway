"use client";

import { useCallback } from "react";
import {
  Background,
  BackgroundVariant,
  Controls,
  ReactFlow,
  ReactFlowProvider,
  useReactFlow,
} from "@xyflow/react";
import { AgentNode } from "./nodes/AgentNode";
import { GateNode } from "./nodes/GateNode";
import { CompressionNode } from "./nodes/CompressionNode";
import { DispatchNode } from "./nodes/DispatchNode";
import { StartNode, EndNode } from "./nodes/EndpointNodes";
import { BuilderEdge } from "./BuilderEdge";
import { useBuilderStore } from "@/lib/store/builderStore";
import { DND_MIME, createNode, type BuilderKind } from "@/lib/canvas/builderGraph";

const nodeTypes = {
  agent: AgentNode,
  approval_gate: GateNode,
  compression: CompressionNode,
  dispatch: DispatchNode,
  start: StartNode,
  end: EndNode,
};

const edgeTypes = { flow: BuilderEdge };
const defaultEdgeOptions = { type: "flow" as const, animated: true };

function CanvasInner() {
  const { screenToFlowPosition } = useReactFlow();
  const nodes = useBuilderStore((s) => s.nodes);
  const edges = useBuilderStore((s) => s.edges);
  const onNodesChange = useBuilderStore((s) => s.onNodesChange);
  const onEdgesChange = useBuilderStore((s) => s.onEdgesChange);
  const onConnect = useBuilderStore((s) => s.onConnect);
  const addNode = useBuilderStore((s) => s.addNode);
  const select = useBuilderStore((s) => s.select);

  const onDrop = useCallback(
    (event: React.DragEvent) => {
      event.preventDefault();
      const kind = event.dataTransfer.getData(DND_MIME) as BuilderKind;
      if (!kind) return;
      const position = screenToFlowPosition({ x: event.clientX, y: event.clientY });
      addNode(createNode(kind, position));
    },
    [screenToFlowPosition, addNode],
  );

  const onDragOver = useCallback((event: React.DragEvent) => {
    event.preventDefault();
    event.dataTransfer.dropEffect = "move";
  }, []);

  return (
    <div className="h-full w-full" onDrop={onDrop} onDragOver={onDragOver}>
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        edgeTypes={edgeTypes}
        defaultEdgeOptions={defaultEdgeOptions}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        onConnect={onConnect}
        onNodeClick={(_, node) => select(node.id)}
        onPaneClick={() => select(null)}
        fitView
        fitViewOptions={{ padding: 0.35 }}
        proOptions={{ hideAttribution: true }}
        minZoom={0.25}
        deleteKeyCode={["Backspace", "Delete"]}
      >
        <Background variant={BackgroundVariant.Dots} gap={22} size={1} color="#27272a" />
        <Controls showInteractive={false} />
      </ReactFlow>
    </div>
  );
}

export function BuilderCanvas() {
  return (
    <ReactFlowProvider>
      <CanvasInner />
    </ReactFlowProvider>
  );
}
