"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api/client";

type HealthState = "checking" | "up" | "down";

const HEALTH_DOT: Record<HealthState, string> = {
  checking: "bg-zinc-500",
  up: "bg-emerald-500",
  down: "bg-red-500",
};

const HEALTH_LABEL: Record<HealthState, string> = {
  checking: "Checking backend…",
  up: "Backend online",
  down: "Backend unreachable",
};

function useBackendHealth(pollMs = 15_000): HealthState {
  const [state, setState] = useState<HealthState>("checking");

  useEffect(() => {
    if (typeof api.healthCheck !== "function") return;
    let cancelled = false;
    const check = () => {
      api.healthCheck().then((ok) => {
        if (!cancelled) setState(ok ? "up" : "down");
      });
    };
    check();
    const id = setInterval(check, pollMs);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [pollMs]);

  return state;
}

interface TopNavProps {
  playbookName?: string;
  onRunPlaybook?: () => void;
  runDisabled?: boolean;
}

export function TopNav({ playbookName, onRunPlaybook, runDisabled }: TopNavProps) {
  const health = useBackendHealth();

  return (
    <header className="sticky top-0 z-30 flex items-center justify-between gap-4 border-b border-zinc-800 bg-zinc-950/80 px-6 py-3 backdrop-blur">
      <div className="flex min-w-0 items-center gap-4">
        <Link href="/" className="shrink-0 text-sm font-semibold tracking-tight text-zinc-100">
          Agent Gateway
        </Link>
        {playbookName && (
          <>
            <span className="text-zinc-700">/</span>
            <span className="truncate text-sm text-zinc-400">{playbookName}</span>
          </>
        )}
      </div>

      <div className="flex shrink-0 items-center gap-4">
        <div className="flex items-center gap-2" title={HEALTH_LABEL[health]}>
          <span className={`h-2 w-2 rounded-full ${HEALTH_DOT[health]}`} />
          <span className="hidden text-xs text-zinc-500 sm:inline">{HEALTH_LABEL[health]}</span>
        </div>
        {onRunPlaybook && (
          <button
            onClick={onRunPlaybook}
            disabled={runDisabled}
            className="rounded-md bg-emerald-500 px-4 py-1.5 text-sm font-medium text-zinc-950 transition-colors hover:bg-emerald-400 disabled:cursor-not-allowed disabled:bg-zinc-700 disabled:text-zinc-400"
          >
            Run Playbook
          </button>
        )}
      </div>
    </header>
  );
}
