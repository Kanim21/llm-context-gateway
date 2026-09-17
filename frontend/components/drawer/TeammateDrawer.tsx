"use client";

import { useState } from "react";
import type { Tier } from "@/types/api";
import { RoleStep } from "./steps/RoleStep";
import { ObjectiveStep } from "./steps/ObjectiveStep";
import { TierStep } from "./steps/TierStep";
import { KnowledgeAppsStep } from "./steps/KnowledgeAppsStep";

export interface TeammateDrawerConfig {
  role: string;
  objective: string;
  tier: Tier;
  knowledge: string[];
  connected_apps: string[];
}

const STEPS = ["role", "objective", "tier", "knowledge"] as const;
const STEP_LABEL: Record<(typeof STEPS)[number], string> = {
  role: "Role",
  objective: "Objective",
  tier: "Tier",
  knowledge: "Knowledge & Apps",
};

interface TeammateDrawerProps {
  onSave: (config: TeammateDrawerConfig) => void;
}

export function TeammateDrawer({ onSave }: TeammateDrawerProps) {
  const [stepIndex, setStepIndex] = useState(0);
  const [role, setRole] = useState("");
  const [objective, setObjective] = useState("");
  const [tier, setTier] = useState<Tier>("balanced");
  const [knowledge, setKnowledge] = useState("");
  const [connectedApps, setConnectedApps] = useState("");

  const step = STEPS[stepIndex];
  const isLast = stepIndex === STEPS.length - 1;

  const handleNext = () => {
    if (isLast) {
      onSave({
        role, objective, tier,
        knowledge: knowledge ? [knowledge] : [],
        connected_apps: connectedApps ? [connectedApps] : [],
      });
      return;
    }
    setStepIndex((i) => i + 1);
  };

  return (
    <div className="fixed inset-y-0 right-0 z-20 flex w-full max-w-md animate-slide-in-right flex-col border-l border-zinc-800 bg-zinc-950 shadow-2xl">
      <div className="border-b border-zinc-800 px-5 py-4">
        <h2 className="text-base font-semibold text-zinc-100">Add Teammate</h2>
        <div className="mt-3 flex items-center gap-1.5">
          {STEPS.map((s, i) => (
            <div key={s} className="flex flex-1 items-center gap-1.5">
              <div
                className={`h-1.5 flex-1 rounded-full transition-colors ${
                  i <= stepIndex ? "bg-blue-500" : "bg-zinc-800"
                }`}
              />
            </div>
          ))}
        </div>
        <p className="mt-1.5 text-xs text-zinc-500">
          Step {stepIndex + 1} of {STEPS.length} · {STEP_LABEL[step]}
        </p>
      </div>

      <div className="flex-1 overflow-y-auto px-5 py-4">
        {step === "role" && <RoleStep value={role} onChange={setRole} />}
        {step === "objective" && <ObjectiveStep value={objective} onChange={setObjective} />}
        {step === "tier" && <TierStep value={tier} onChange={setTier} />}
        {step === "knowledge" && (
          <KnowledgeAppsStep
            knowledge={knowledge} connectedApps={connectedApps}
            onKnowledgeChange={setKnowledge} onConnectedAppsChange={setConnectedApps}
          />
        )}
      </div>

      <div className="flex gap-2 border-t border-zinc-800 px-5 py-4">
        {stepIndex > 0 && (
          <button
            onClick={() => setStepIndex((i) => i - 1)}
            className="flex-1 rounded-lg border border-zinc-700 bg-zinc-900 px-3 py-2 text-sm font-medium text-zinc-300 transition-colors hover:bg-zinc-800"
          >
            Back
          </button>
        )}
        <button
          onClick={handleNext}
          className="flex-1 rounded-lg bg-blue-600 px-3 py-2 text-sm font-semibold text-white transition-colors hover:bg-blue-500"
        >
          {isLast ? "Save" : "Next"}
        </button>
      </div>
    </div>
  );
}
