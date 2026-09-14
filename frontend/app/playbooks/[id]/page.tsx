"use client";

import { useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { api } from "@/lib/api/client";
import { PlaybookCanvas } from "@/components/canvas/PlaybookCanvas";
import { TeammateDrawer, type TeammateDrawerConfig } from "@/components/drawer/TeammateDrawer";
import { toFlowGraph, NODE_SPACING_X } from "@/lib/canvas/toFlowGraph";
import type { PlaybookDetail } from "@/types/api";
import type { Node } from "@xyflow/react";

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
        position: { x: (nodes.length + prev.length) * NODE_SPACING_X, y: 0 },
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
