interface RoleStepProps {
  value: string;
  onChange: (value: string) => void;
}

export function RoleStep({ value, onChange }: RoleStepProps) {
  return (
    <div>
      <label htmlFor="role-input">Role</label>
      <input id="role-input" aria-label="Role" value={value} onChange={(e) => onChange(e.target.value)} />
    </div>
  );
}
