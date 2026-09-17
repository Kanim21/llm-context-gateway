"use client";

import { useState } from "react";
import { api } from "@/lib/api/client";

interface GateReviewPanelProps {
  runId: string;
  stepIndex: number;
  label: string;
  allowEdit: boolean;
  proposedOutput: { text: string };
  onDecided: () => void;
}

export function GateReviewPanel({ runId, stepIndex, label, allowEdit, proposedOutput, onDecided }: GateReviewPanelProps) {
  const [editedText, setEditedText] = useState(proposedOutput.text);
  // A double-click used to fire two decisions; the second one now gets a 409
  // from the backend, but it shouldn't leave the button clickable either.
  const [submitting, setSubmitting] = useState(false);

  const submit = async (decision: "approve" | "reject" | "edit") => {
    if (submitting) return;
    setSubmitting(true);
    try {
      await api.submitGateDecision(
        runId,
        decision === "edit"
          ? { decision: "edit", step_index: stepIndex, edited_output: { text: editedText } }
          : { decision, step_index: stepIndex },
      );
      onDecided();
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="fixed inset-y-0 right-0 z-20 flex w-full max-w-md animate-slide-in-right flex-col border-l border-zinc-800 bg-zinc-950 shadow-2xl">
      <div className="flex items-center gap-2 border-b border-zinc-800 px-5 py-4">
        <span className="flex h-6 items-center rounded-full bg-amber-500/15 px-2 text-[10px] font-semibold uppercase tracking-wide text-amber-400 ring-1 ring-inset ring-amber-500/40">
          ⚠ Human review
        </span>
      </div>

      <div className="flex-1 overflow-y-auto px-5 py-4">
        <h3 className="text-base font-semibold text-zinc-100">{label}</h3>

        <div className="mt-3 rounded-lg border border-zinc-800 bg-zinc-900 p-3">
          <p className="mb-1.5 text-xs font-medium uppercase tracking-wide text-zinc-500">Proposed output</p>
          <p className="whitespace-pre-wrap text-sm text-zinc-300">{proposedOutput.text}</p>
        </div>

        {allowEdit && (
          <div className="mt-4">
            <label htmlFor="edit-output" className="mb-1.5 block text-xs font-medium uppercase tracking-wide text-zinc-500">
              Edit output
            </label>
            <textarea
              id="edit-output"
              aria-label="Edit output"
              value={editedText}
              onChange={(e) => setEditedText(e.target.value)}
              rows={6}
              className="w-full resize-none rounded-lg border border-zinc-700 bg-zinc-900 px-3 py-2 text-sm text-zinc-100 outline-none focus:border-blue-500 focus:ring-1 focus:ring-blue-500"
            />
            <button
              onClick={() => submit("edit")}
              disabled={submitting}
              className="mt-2 w-full rounded-lg border border-zinc-700 bg-zinc-800 px-3 py-2 text-sm font-medium text-zinc-200 transition-colors hover:bg-zinc-700 disabled:cursor-not-allowed disabled:opacity-50"
            >
              Save Edit
            </button>
          </div>
        )}
      </div>

      <div className="flex gap-2 border-t border-zinc-800 px-5 py-4">
        <button
          onClick={() => submit("approve")}
          disabled={submitting}
          className="flex-1 rounded-lg bg-emerald-600 px-3 py-2 text-sm font-semibold text-white transition-colors hover:bg-emerald-500 disabled:cursor-not-allowed disabled:opacity-50"
        >
          Approve
        </button>
        <button
          onClick={() => submit("reject")}
          disabled={submitting}
          className="flex-1 rounded-lg bg-red-600/90 px-3 py-2 text-sm font-semibold text-white transition-colors hover:bg-red-500 disabled:cursor-not-allowed disabled:opacity-50"
        >
          Reject
        </button>
      </div>
    </div>
  );
}
