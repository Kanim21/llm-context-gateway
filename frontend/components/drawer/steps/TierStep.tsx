import type { Tier } from "@/types/api";
import { TIER_MODEL_LABEL } from "@/lib/models/tierModel";

interface TierStepProps {
  value: Tier;
  onChange: (value: Tier) => void;
}

const TIERS: { value: Tier; label: string; blurb: string }[] = [
  { value: "speed", label: "Speed", blurb: "Fastest, cheapest — good for simple, high-volume steps." },
  { value: "balanced", label: "Balanced", blurb: "The default — solid quality at reasonable cost." },
  { value: "brain", label: "Brain", blurb: "Most capable — for judgment calls and nuanced writing." },
];

export function TierStep({ value, onChange }: TierStepProps) {
  return (
    <fieldset>
      <legend className="mb-1.5 text-sm font-medium text-zinc-300">Tier</legend>
      <p className="mb-2 text-xs text-zinc-500">How much horsepower should this teammate get?</p>
      <div className="space-y-2">
        {TIERS.map((tier) => (
          <label
            key={tier.value}
            className={`flex cursor-pointer items-start gap-3 rounded-lg border p-3 transition-colors ${
              value === tier.value ? "border-blue-500 bg-blue-500/10" : "border-zinc-700 bg-zinc-900 hover:border-zinc-600"
            }`}
          >
            <input
              type="radio"
              name="tier"
              aria-label={tier.label}
              checked={value === tier.value}
              onChange={() => onChange(tier.value)}
              className="mt-0.5 accent-blue-500"
            />
            <span>
              <span className="flex items-center gap-2 text-sm font-medium text-zinc-100">
                {tier.label}
                <span className="rounded-full bg-zinc-800 px-1.5 py-0.5 text-[10px] text-zinc-400">
                  {TIER_MODEL_LABEL[tier.value]}
                </span>
              </span>
              <span className="block text-xs text-zinc-500">{tier.blurb}</span>
            </span>
          </label>
        ))}
      </div>
    </fieldset>
  );
}
