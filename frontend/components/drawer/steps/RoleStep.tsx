interface RoleStepProps {
  value: string;
  onChange: (value: string) => void;
}

export function RoleStep({ value, onChange }: RoleStepProps) {
  const touched = value.length > 0;
  return (
    <div>
      <label htmlFor="role-input" className="mb-1.5 block text-sm font-medium text-zinc-300">
        Role
      </label>
      <p className="mb-2 text-xs text-zinc-500">What should this teammate be called on the canvas?</p>
      <input
        id="role-input"
        aria-label="Role"
        value={value}
        placeholder="e.g. Inbound Sales Qualifier"
        onChange={(e) => onChange(e.target.value)}
        className="w-full rounded-lg border border-zinc-700 bg-zinc-900 px-3 py-2 text-sm text-zinc-100 outline-none placeholder:text-zinc-600 focus:border-blue-500 focus:ring-1 focus:ring-blue-500"
      />
      {!touched && <p className="mt-1.5 text-xs text-amber-500">A role name helps you tell teammates apart.</p>}
    </div>
  );
}
