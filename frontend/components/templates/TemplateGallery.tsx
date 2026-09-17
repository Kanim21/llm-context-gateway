"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api/client";
import type { PlaybookDetail, PlaybookSummary } from "@/types/api";

interface TemplateGalleryProps {
  onSelect: (playbookId: string) => void;
}

interface StepCounts {
  teammates: number;
  gates: number;
}

// Illustrative only -- there is no measured runtime data. ~40s per teammate
// call, ~90s per gate (a human has to look at it), floored at 1 minute.
function estimateRuntimeMinutes({ teammates, gates }: StepCounts): number {
  return Math.max(1, Math.round((teammates * 40 + gates * 90) / 60));
}

export function TemplateGallery({ onSelect }: TemplateGalleryProps) {
  const [templates, setTemplates] = useState<PlaybookSummary[]>([]);
  const [countsById, setCountsById] = useState<Record<string, StepCounts>>({});

  useEffect(() => {
    api.listPlaybooks().then((playbooks) => {
      const filtered = playbooks.filter((p) => p.is_template);
      setTemplates(filtered);

      // Best-effort enrichment: summaries don't carry step counts. Guarded so
      // this stays a no-op (not a crash) against a partial test double that
      // only stubs listPlaybooks.
      if (typeof api.getPlaybook !== "function") return;
      for (const template of filtered) {
        api
          .getPlaybook(template.id)
          .then((detail: PlaybookDetail) => {
            const steps = detail.definition_json.steps;
            setCountsById((prev) => ({
              ...prev,
              [template.id]: {
                teammates: steps.filter((s) => s.type === "teammate").length,
                gates: steps.filter((s) => s.type === "approval_gate").length,
              },
            }));
          })
          .catch(() => {});
      }
    });
  }, []);

  if (templates.length === 0) {
    return <p className="text-sm text-zinc-500">No templates available.</p>;
  }

  return (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
      {templates.map((template) => {
        const counts = countsById[template.id];
        return (
          <button
            key={template.id}
            onClick={() => onSelect(template.id)}
            className="group flex flex-col rounded-xl border border-zinc-800 bg-zinc-900/60 p-5 text-left transition-colors hover:border-zinc-700 hover:bg-zinc-900 focus:outline-none focus-visible:ring-2 focus-visible:ring-emerald-500"
          >
            <div className="mb-3 flex items-center justify-between">
              <span className="rounded-full bg-emerald-500/10 px-2.5 py-0.5 text-xs font-medium text-emerald-400 ring-1 ring-inset ring-emerald-500/30">
                Template
              </span>
              {counts && (
                <span className="text-xs text-zinc-500" title="Illustrative estimate based on step count">
                  ~{estimateRuntimeMinutes(counts)} min
                </span>
              )}
            </div>

            <div className="text-base font-semibold text-zinc-100">{template.name}</div>
            <div className="mt-1 flex-1 text-sm text-zinc-400">{template.description}</div>

            <div className="mt-4 flex items-center justify-between border-t border-zinc-800 pt-3">
              <span className="text-xs text-zinc-500">
                {counts ? `${counts.teammates} teammate${counts.teammates === 1 ? "" : "s"}` : " "}
                {counts && counts.gates > 0 ? ` · ${counts.gates} gate${counts.gates === 1 ? "" : "s"}` : ""}
              </span>
              <span className="text-sm font-medium text-zinc-300 transition-colors group-hover:text-emerald-400">
                Open Canvas →
              </span>
            </div>
          </button>
        );
      })}
    </div>
  );
}
