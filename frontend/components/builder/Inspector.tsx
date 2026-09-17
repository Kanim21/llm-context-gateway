"use client";

import { MODELS } from "@/lib/models/modelRegistry";
import { useBuilderStore } from "@/lib/store/builderStore";

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <span className="mb-1 block text-[11px] font-medium uppercase tracking-wide text-zinc-500">{label}</span>
      {children}
    </label>
  );
}

const inputCls =
  "w-full rounded-md border border-zinc-700 bg-zinc-900 px-2.5 py-1.5 text-sm text-zinc-100 outline-none placeholder:text-zinc-600 focus:border-emerald-500 focus:ring-1 focus:ring-emerald-500";

function Toggle({ checked, onChange, label, hint }: { checked: boolean; onChange: (v: boolean) => void; label: string; hint: string }) {
  return (
    <button
      type="button"
      onClick={() => onChange(!checked)}
      className="flex w-full items-start justify-between gap-3 rounded-md border border-zinc-800 bg-zinc-900 px-3 py-2 text-left transition-colors hover:border-zinc-700"
    >
      <span className="min-w-0">
        <span className="block text-sm text-zinc-200">{label}</span>
        <span className="block text-[11px] text-zinc-500">{hint}</span>
      </span>
      <span className={`mt-0.5 flex h-5 w-9 shrink-0 items-center rounded-full p-0.5 transition-colors ${checked ? "bg-emerald-500" : "bg-zinc-700"}`}>
        <span className={`h-4 w-4 rounded-full bg-white transition-transform ${checked ? "translate-x-4" : ""}`} />
      </span>
    </button>
  );
}

export function Inspector() {
  const selectedId = useBuilderStore((s) => s.selectedId);
  const node = useBuilderStore((s) => s.nodes.find((n) => n.id === s.selectedId) ?? null);
  const update = useBuilderStore((s) => s.updateNodeData);
  const removeNode = useBuilderStore((s) => s.removeNode);
  const select = useBuilderStore((s) => s.select);

  if (!node || !selectedId) {
    return (
      <aside className="hidden w-80 shrink-0 flex-col border-l border-zinc-800 bg-zinc-950/70 lg:flex">
        <div className="flex flex-1 items-center justify-center px-6 text-center text-sm text-zinc-600">
          Select a node to configure it.
        </div>
      </aside>
    );
  }

  const d = node.data as Record<string, unknown>;
  const isEndpoint = node.type === "start" || node.type === "end";
  const patch = (p: Record<string, unknown>) => update(selectedId, p);

  return (
    <aside className="flex w-80 shrink-0 animate-slide-in-right flex-col border-l border-zinc-800 bg-zinc-950/90">
      <div className="flex items-center justify-between border-b border-zinc-800 px-4 py-3">
        <h2 className="text-sm font-semibold text-zinc-100">Inspector</h2>
        <button onClick={() => select(null)} className="rounded p-1 text-zinc-500 hover:bg-zinc-800 hover:text-zinc-300" aria-label="Close inspector">
          ✕
        </button>
      </div>

      <div className="flex flex-col gap-4 overflow-y-auto p-4">
        {node.type === "agent" && (
          <>
            <Field label="Agent name & role">
              <input className={inputCls} value={String(d.role ?? "")} onChange={(e) => patch({ role: e.target.value })} placeholder="e.g. Strategic Researcher" />
            </Field>
            <Field label="Task / system prompt">
              <textarea className={`${inputCls} min-h-[110px] resize-y`} value={String(d.objective ?? "")} onChange={(e) => patch({ objective: e.target.value })} placeholder="Describe what this agent should do…" />
            </Field>
            <Field label="Provider & model">
              <select className={inputCls} value={String(d.model ?? "")} onChange={(e) => patch({ model: e.target.value })}>
                {MODELS.map((m) => (
                  <option key={m.id} value={m.id}>
                    {m.providerLabel} · {m.label}
                  </option>
                ))}
              </select>
            </Field>

            <div className="space-y-2">
              <span className="block text-[11px] font-medium uppercase tracking-wide text-zinc-500">Gateway context compression</span>
              <Toggle checked={Boolean(d.toolOutputMasking)} onChange={(v) => patch({ toolOutputMasking: v })} label="Tool-output masking" hint="Drop verbose tool payloads from context" />
              <Toggle checked={Boolean(d.promptCacheHashing)} onChange={(v) => patch({ promptCacheHashing: v })} label="Prompt-cache hashing" hint="Deterministic prefix hashing for cache hits" />
              <Field label={`Token-budget cap · ${(Number(d.tokenBudgetCap ?? 8000) / 1000).toFixed(0)}k`}>
                <input type="range" min={1000} max={32000} step={1000} value={Number(d.tokenBudgetCap ?? 8000)} onChange={(e) => patch({ tokenBudgetCap: Number(e.target.value) })} className="w-full accent-emerald-500" />
              </Field>
            </div>
          </>
        )}

        {node.type === "approval_gate" && (
          <>
            <Field label="Gate label">
              <input className={inputCls} value={String(d.label ?? "")} onChange={(e) => patch({ label: e.target.value })} placeholder="Review before continuing" />
            </Field>
            <Toggle checked={Boolean(d.allow_edit)} onChange={(v) => patch({ allow_edit: v })} label="Allow edits on approval" hint="Reviewer can edit the output before it continues" />
          </>
        )}

        {node.type === "compression" && (
          <>
            <Field label="Label">
              <input className={inputCls} value={String(d.label ?? "")} onChange={(e) => patch({ label: e.target.value })} />
            </Field>
            <Field label="Compression level">
              <select className={inputCls} value={String(d.level ?? "balanced")} onChange={(e) => patch({ level: e.target.value })}>
                <option value="none">None</option>
                <option value="balanced">Balanced</option>
                <option value="aggressive">Aggressive</option>
              </select>
            </Field>
            <p className="text-[11px] leading-snug text-zinc-500">
              Compression is applied by the gateway to every request; this node marks where it wraps the chain.
            </p>
          </>
        )}

        {node.type === "dispatch" && (
          <>
            <Field label="Label">
              <input className={inputCls} value={String(d.label ?? "")} onChange={(e) => patch({ label: e.target.value })} />
            </Field>
            <Field label="Target">
              <select className={inputCls} value={String(d.target ?? "webhook")} onChange={(e) => patch({ target: e.target.value })}>
                <option value="webhook">Webhook</option>
                <option value="blackboard">Blackboard</option>
              </select>
            </Field>
          </>
        )}

        {isEndpoint && <p className="text-sm text-zinc-500">The {node.type} node marks where the playbook {node.type === "start" ? "begins" : "ends"}.</p>}
      </div>

      {!isEndpoint && (
        <div className="mt-auto border-t border-zinc-800 p-4">
          <button onClick={() => removeNode(selectedId)} className="w-full rounded-md border border-red-900/60 bg-red-950/30 px-3 py-1.5 text-sm font-medium text-red-300 transition-colors hover:bg-red-950/60">
            Delete node
          </button>
        </div>
      )}
    </aside>
  );
}
