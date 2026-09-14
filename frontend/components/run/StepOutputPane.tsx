import type { StepSnapshot } from "@/types/api";

export function StepOutputPane({ steps }: { steps: StepSnapshot[] }) {
  return (
    <div>
      {steps.map((step) => (
        <div key={step.step_index}>
          <div>{step.step_id} — {step.status}</div>
          {step.output_json && <p>{step.output_json.text}</p>}
        </div>
      ))}
    </div>
  );
}
