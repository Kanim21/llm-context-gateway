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

  const approve = async () => {
    await api.submitGateDecision(runId, { decision: "approve" });
    onDecided();
  };
  const reject = async () => {
    await api.submitGateDecision(runId, { decision: "reject" });
    onDecided();
  };
  const saveEdit = async () => {
    await api.submitGateDecision(runId, { decision: "edit", edited_output: { text: editedText } });
    onDecided();
  };

  return (
    <div>
      <h3>{label}</h3>
      <p>{proposedOutput.text}</p>
      <button onClick={approve}>Approve</button>
      <button onClick={reject}>Reject</button>
      {allowEdit && (
        <div>
          <label htmlFor="edit-output">Edit output</label>
          <textarea id="edit-output" aria-label="Edit output" value={editedText}
                    onChange={(e) => setEditedText(e.target.value)} />
          <button onClick={saveEdit}>Save Edit</button>
        </div>
      )}
    </div>
  );
}
