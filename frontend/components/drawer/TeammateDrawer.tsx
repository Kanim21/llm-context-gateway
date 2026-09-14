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
    <div>
      {step === "role" && <RoleStep value={role} onChange={setRole} />}
      {step === "objective" && <ObjectiveStep value={objective} onChange={setObjective} />}
      {step === "tier" && <TierStep value={tier} onChange={setTier} />}
      {step === "knowledge" && (
        <KnowledgeAppsStep
          knowledge={knowledge} connectedApps={connectedApps}
          onKnowledgeChange={setKnowledge} onConnectedAppsChange={setConnectedApps}
        />
      )}

      <div>
        {stepIndex > 0 && <button onClick={() => setStepIndex((i) => i - 1)}>Back</button>}
        <button onClick={handleNext}>{isLast ? "Save" : "Next"}</button>
      </div>
    </div>
  );
}
