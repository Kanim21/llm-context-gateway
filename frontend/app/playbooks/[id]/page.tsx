"use client";

import { useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { api } from "@/lib/api/client";
import { PlaybookCanvas } from "@/components/canvas/PlaybookCanvas";
import { TeammateDrawer, type TeammateDrawerConfig } from "@/components/drawer/TeammateDrawer";
import type { PlaybookDetail } from "@/types/api";
import type { Node, Edge } from "@xyflow/react";

function toFlowGraph(canvasJson: { nodes: any[]; edges: any[] }): { nodes: Node[]; edges: Edge[] } {
  const nodes: Node[] = canvasJson.nodes
    .filter((n) => n.type === "teammate" || n.type === "approval_gate")
    .map((n) => ({ id: n.id, type: n.type, data: n.data, position: { x: 0, y: 0 } }));
  const edges: Edge[] = canvasJson.edges.map((e: any, i: number) => ({
    id: `e${i}`, source: e.source, target: e.target,
  }));
  return { nodes, edges };
}

export default function PlaybookEditorPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const [playbook, setPlaybook] = useState<PlaybookDetail | null>(null);
  const [inputText, setInputText] = useState("");
  const [extraNodes, setExtraNodes] = useState<Node[]>([]);
  const [drawerOpen, setDrawerOpen] = useState(false);

  useEffect(() => {
    api.getPlaybook(id).then(setPlaybook);
  }, [id]);

  if (!playbook) return <p>Loading…</p>;

  const { nodes, edges } = toFlowGraph(playbook.canvas_json as any);
  const allNodes = [...nodes, ...extraNodes];

  const runNow = async () => {
    const run = await api.startRun(playbook.id, inputText);
    router.push(`/playbooks/${playbook.id}/run/${run.id}`);
  };

  const addTeammate = (config: TeammateDrawerConfig) => {
    setExtraNodes((prev) => [
      ...prev,
      {
        id: `teammate_${prev.length + nodes.length}`,
        type: "teammate",
        data: { role: config.role, objective: config.objective },
        position: { x: 0, y: 0 },
      },
    ]);
    setDrawerOpen(false);
  };

  return (
    <main>
      <h1>{playbook.name}</h1>
      <PlaybookCanvas nodes={allNodes} edges={edges} />
      <button onClick={() => setDrawerOpen(true)}>Add Teammate</button>
      {drawerOpen && <TeammateDrawer onSave={addTeammate} />}
      <input aria-label="Run input" value={inputText} onChange={(e) => setInputText(e.target.value)} />
      <button onClick={runNow}>Run</button>
    </main>
  );
}
