"use client";

import { useState } from "react";
import { api } from "@/lib/api/client";

interface GateReviewPanelProps {
  runId: string;
  label: string;
  allowEdit: boolean;
  proposedOutput: { text: string };
  onDecided: () => void;
}

export function GateReviewPanel({ runId, label, allowEdit, proposedOutput, onDecided }: GateReviewPanelProps) {
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
          ? { decision: "edit", edited_output: { text: editedText } }
          : { decision },
      );
      onDecided();
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div>
      <h3>{label}</h3>
      <p>{proposedOutput.text}</p>
      <button onClick={() => submit("approve")} disabled={submitting}>Approve</button>
      <button onClick={() => submit("reject")} disabled={submitting}>Reject</button>
      {allowEdit && (
        <div>
          <label htmlFor="edit-output">Edit output</label>
          <textarea id="edit-output" aria-label="Edit output" value={editedText}
                    onChange={(e) => setEditedText(e.target.value)} />
          <button onClick={() => submit("edit")} disabled={submitting}>Save Edit</button>
        </div>
      )}
    </div>
  );
}
