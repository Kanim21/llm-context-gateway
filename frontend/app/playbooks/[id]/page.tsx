"use client";

import { useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { api } from "@/lib/api/client";
import { PlaybookCanvas } from "@/components/canvas/PlaybookCanvas";
import { TeammateDrawer, type TeammateDrawerConfig } from "@/components/drawer/TeammateDrawer";
import { toFlowGraph } from "@/lib/canvas/toFlowGraph";
import { addTeammateToCanvas } from "@/lib/canvas/addTeammateToCanvas";
import type { PlaybookDetail } from "@/types/api";

export default function PlaybookEditorPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const [playbook, setPlaybook] = useState<PlaybookDetail | null>(null);
  const [inputText, setInputText] = useState("");
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    api.getPlaybook(id).then(setPlaybook);
  }, [id]);

  if (!playbook) return <p>Loading…</p>;

  const { nodes, edges } = toFlowGraph(playbook.canvas_json);

  const runNow = async () => {
    const run = await api.startRun(playbook.id, inputText);
    router.push(`/playbooks/${playbook.id}/run/${run.id}`);
  };

  // Persist an added teammate onto the playbook so it survives reload and is
  // included in runs -- no more tab-local-only canvas state.
  const addTeammate = async (config: TeammateDrawerConfig) => {
    setSaving(true);
    try {
      const canvas_json = addTeammateToCanvas(playbook.canvas_json, config);
      const updated = await api.updatePlaybook(playbook.id, {
        name: playbook.name,
        description: playbook.description,
        canvas_json,
      });
      setPlaybook(updated);
      setDrawerOpen(false);
    } finally {
      setSaving(false);
    }
  };

  return (
    <main>
      <h1>{playbook.name}</h1>
      <PlaybookCanvas nodes={nodes} edges={edges} />
      <button onClick={() => setDrawerOpen(true)}>Add Teammate</button>
      {drawerOpen && <TeammateDrawer onSave={addTeammate} />}
      <input aria-label="Run input" value={inputText} onChange={(e) => setInputText(e.target.value)} />
      <button onClick={runNow} disabled={saving}>Run</button>
    </main>
  );
}
