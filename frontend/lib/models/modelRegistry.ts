import type { Tier } from "@/types/api";

/**
 * The provider/model choices the builder's inspector offers. The backend
 * teammate config only persists a `tier` (speed | balanced | brain -- see
 * agent_gateway/proxy/config.py), so each concrete model maps to a routing
 * tier. The chosen model id is also persisted in node.data.model (an extra key
 * the compiler ignores) so the UI can show the exact model the user picked.
 */
export type Provider = "openai" | "anthropic" | "deepseek";

export interface ModelOption {
  id: string;
  label: string;
  provider: Provider;
  providerLabel: string;
  tier: Tier;
  /** Tailwind classes for the model pill on a node card. */
  pillClass: string;
}

export const MODELS: ModelOption[] = [
  {
    id: "gpt-4o-mini",
    label: "GPT-4o mini",
    provider: "openai",
    providerLabel: "OpenAI",
    tier: "speed",
    pillClass: "bg-emerald-500/10 text-emerald-300 ring-1 ring-inset ring-emerald-500/30",
  },
  {
    id: "gpt-4o",
    label: "GPT-4o",
    provider: "openai",
    providerLabel: "OpenAI",
    tier: "balanced",
    pillClass: "bg-emerald-500/10 text-emerald-300 ring-1 ring-inset ring-emerald-500/30",
  },
  {
    id: "claude-3-5-sonnet",
    label: "Claude 3.5 Sonnet",
    provider: "anthropic",
    providerLabel: "Anthropic",
    tier: "brain",
    pillClass: "bg-orange-500/10 text-orange-300 ring-1 ring-inset ring-orange-500/30",
  },
  {
    id: "deepseek-chat",
    label: "DeepSeek Chat",
    provider: "deepseek",
    providerLabel: "DeepSeek",
    tier: "balanced",
    pillClass: "bg-sky-500/10 text-sky-300 ring-1 ring-inset ring-sky-500/30",
  },
];

export const DEFAULT_MODEL_ID = "gpt-4o";

export function modelById(id: string | undefined): ModelOption {
  return (
    MODELS.find((m) => m.id === id) ??
    MODELS.find((m) => m.id === DEFAULT_MODEL_ID)!
  );
}

export function tierForModel(id: string | undefined): Tier {
  return modelById(id).tier;
}
