interface ObjectiveStepProps {
  value: string;
  onChange: (value: string) => void;
}

export function ObjectiveStep({ value, onChange }: ObjectiveStepProps) {
  return (
    <div>
      <label htmlFor="objective-input">Objective</label>
      <textarea id="objective-input" aria-label="Objective" value={value} onChange={(e) => onChange(e.target.value)} />
    </div>
  );
}
