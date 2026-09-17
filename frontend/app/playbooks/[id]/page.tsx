"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useParams } from "next/navigation";
import { api } from "@/lib/api/client";
import { TopNav } from "@/components/nav/TopNav";
import { Palette } from "@/components/builder/Palette";
import { BuilderCanvas } from "@/components/builder/BuilderCanvas";
import { Inspector } from "@/components/builder/Inspector";
import { ExecutionDock } from "@/components/builder/ExecutionDock";
import { useBuilderStore } from "@/lib/store/builderStore";
import { useBuilderRun } from "@/lib/run/useBuilderRun";
import { fromBuilderGraph, toBuilderGraph, validateChain } from "@/lib/canvas/builderGraph";
import type { PlaybookDetail } from "@/types/api";

export default function PlaybookEditorPage() {
  const { id } = useParams<{ id: string }>();
  const [playbook, setPlaybook] = useState<PlaybookDetail | null>(null);
  const [inputText, setInputText] = useState("");
  const [runId, setRunId] = useState<string | null>(null);
  const [runPlaybook, setRunPlaybook] = useState<PlaybookDetail | null>(null);
  const [dockOpen, setDockOpen] = useState(false);
  const [triedRun, setTriedRun] = useState(false);

  const nodes = useBuilderStore((s) => s.nodes);
  const edges = useBuilderStore((s) => s.edges);
  const setGraph = useBuilderStore((s) => s.setGraph);

  const hydratedRef = useRef(false);
  const lastSavedRef = useRef<string>("");

  const telemetry = useBuilderRun(runId, runPlaybook);

  // Load the playbook and seed the canvas once.
  useEffect(() => {
    let cancelled = false;
    api.getPlaybook(id).then((detail) => {
      if (cancelled) return;
      setPlaybook(detail);
      const graph = toBuilderGraph(detail.canvas_json);
      setGraph(graph.nodes, graph.edges);
      lastSavedRef.current = JSON.stringify(fromBuilderGraph(graph.nodes, graph.edges));
      hydratedRef.current = true;
    });
    return () => {
      cancelled = true;
    };
  }, [id, setGraph]);

  const liveErrors = useMemo(() => validateChain(nodes, edges), [nodes, edges]);

  // Debounced persistence: only PUT a graph the backend will accept, and skip
  // position-only changes (positions aren't part of canvas_json).
  useEffect(() => {
    if (!hydratedRef.current || !playbook) return;
    const canvas = fromBuilderGraph(nodes, edges);
    const json = JSON.stringify(canvas);
    if (json === lastSavedRef.current) return;
    if (validateChain(nodes, edges).length > 0) return;
    const t = setTimeout(() => {
      api
        .updatePlaybook(playbook.id, { name: playbook.name, description: playbook.description, canvas_json: canvas })
        .then(() => {
          lastSavedRef.current = json;
        })
        .catch(() => {});
    }, 800);
    return () => clearTimeout(t);
  }, [nodes, edges, playbook]);

  const runNow = async () => {
    if (!playbook) return;
    setTriedRun(true);
    if (validateChain(nodes, edges).length > 0) {
      setDockOpen(true);
      return;
    }
    const canvas = fromBuilderGraph(nodes, edges);
    const updated = await api.updatePlaybook(playbook.id, {
      name: playbook.name,
      description: playbook.description,
      canvas_json: canvas,
    });
    lastSavedRef.current = JSON.stringify(canvas);
    setRunPlaybook(updated);
    const run = await api.startRun(updated.id, inputText);
    setDockOpen(true);
    setRunId(run.id);
  };

  if (!playbook) {
    return (
      <div className="flex h-screen items-center justify-center bg-zinc-950 text-sm text-zinc-500">Loading playbook…</div>
    );
  }

  const showErrors = triedRun && liveErrors.length > 0;

  return (
    <div className="flex h-screen flex-col bg-zinc-950">
      <TopNav playbookName={playbook.name} onRunPlaybook={runNow} runDisabled={liveErrors.length > 0} />

      {/* Kick-off input + validation strip */}
      <div className="flex items-center gap-3 border-b border-zinc-800 bg-zinc-950/60 px-5 py-2.5">
        <p className="mr-auto min-w-0 truncate text-sm text-zinc-500">
          {liveErrors.length === 0 ? "Chain is valid — ready to run." : `${liveErrors.length} issue${liveErrors.length > 1 ? "s" : ""} to resolve before running.`}
        </p>
        <input
          aria-label="Run input"
          value={inputText}
          onChange={(e) => setInputText(e.target.value)}
          placeholder="Kick-off input for this run…"
          className="w-72 rounded-md border border-zinc-700 bg-zinc-900 px-3 py-1.5 text-sm text-zinc-100 outline-none placeholder:text-zinc-600 focus:border-emerald-500 focus:ring-1 focus:ring-emerald-500"
        />
      </div>

      {showErrors && (
        <div className="border-b border-amber-900/50 bg-amber-950/30 px-5 py-2 text-xs text-amber-300">
          {liveErrors.slice(0, 3).map((e, i) => (
            <span key={i} className="mr-3">• {e}</span>
          ))}
        </div>
      )}

      <div className="flex min-h-0 flex-1">
        <Palette />
        <div className="relative min-h-0 flex-1">
          <BuilderCanvas />
        </div>
        <Inspector />
      </div>

      <ExecutionDock open={dockOpen} onToggle={() => setDockOpen((o) => !o)} telemetry={telemetry} />
    </div>
  );
}
