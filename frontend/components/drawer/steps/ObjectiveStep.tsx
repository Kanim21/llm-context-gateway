interface ObjectiveStepProps {
  value: string;
  onChange: (value: string) => void;
}

export function ObjectiveStep({ value, onChange }: ObjectiveStepProps) {
  const touched = value.length > 0;
  return (
    <div>
      <label htmlFor="objective-input" className="mb-1.5 block text-sm font-medium text-zinc-300">
        Objective
      </label>
      <p className="mb-2 text-xs text-zinc-500">Describe what this teammate is responsible for, in plain language.</p>
      <textarea
        id="objective-input"
        aria-label="Objective"
        value={value}
        rows={4}
        placeholder="e.g. Read each inbound lead and decide if it's worth a follow-up call."
        onChange={(e) => onChange(e.target.value)}
        className="w-full resize-none rounded-lg border border-zinc-700 bg-zinc-900 px-3 py-2 text-sm text-zinc-100 outline-none placeholder:text-zinc-600 focus:border-blue-500 focus:ring-1 focus:ring-blue-500"
      />
      {!touched && <p className="mt-1.5 text-xs text-amber-500">Objective is shown to the teammate as its instructions.</p>}
    </div>
  );
}
