import type { Tier } from "@/types/api";

interface TierStepProps {
  value: Tier;
  onChange: (value: Tier) => void;
}

const TIERS: { value: Tier; label: string }[] = [
  { value: "speed", label: "Speed" },
  { value: "balanced", label: "Balanced" },
  { value: "brain", label: "Brain" },
];

export function TierStep({ value, onChange }: TierStepProps) {
  return (
    <fieldset>
      <legend>Tier</legend>
      {TIERS.map((tier) => (
        <label key={tier.value}>
          <input
            type="radio"
            name="tier"
            aria-label={tier.label}
            checked={value === tier.value}
            onChange={() => onChange(tier.value)}
          />
          {tier.label}
        </label>
      ))}
    </fieldset>
  );
}
