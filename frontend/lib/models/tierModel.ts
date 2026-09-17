import type { Tier } from "@/types/api";

/**
 * Display labels for each tier's underlying model. Mirrors
 * `GatewayConfig.playbook_tiers`'s default in agent_gateway/proxy/config.py --
 * a deployment that overrides that config will show a stale badge here, since
 * the frontend has no endpoint to read the live mapping from.
 */
export const TIER_MODEL_LABEL: Record<Tier, string> = {
  speed: "GPT-4o mini",
  balanced: "GPT-4o",
  brain: "Gemini 1.5 Pro",
};

export const TIER_BADGE_CLASSES: Record<Tier, string> = {
  speed: "bg-sky-500/10 text-sky-400 ring-1 ring-inset ring-sky-500/30",
  balanced: "bg-violet-500/10 text-violet-400 ring-1 ring-inset ring-violet-500/30",
  brain: "bg-fuchsia-500/10 text-fuchsia-400 ring-1 ring-inset ring-fuchsia-500/30",
};
