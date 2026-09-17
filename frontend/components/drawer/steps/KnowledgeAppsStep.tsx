interface KnowledgeAppsStepProps {
  knowledge: string;
  connectedApps: string;
  onKnowledgeChange: (value: string) => void;
  onConnectedAppsChange: (value: string) => void;
}

export function KnowledgeAppsStep({
  knowledge, connectedApps, onKnowledgeChange, onConnectedAppsChange,
}: KnowledgeAppsStepProps) {
  return (
    <div className="space-y-4">
      <div>
        <label htmlFor="knowledge-input" className="mb-1.5 block text-sm font-medium text-zinc-300">
          Knowledge
        </label>
        <p className="mb-2 text-xs text-zinc-500">Reference material this teammate should know about (optional).</p>
        <input
          id="knowledge-input"
          aria-label="Knowledge"
          value={knowledge}
          placeholder="e.g. ICP doc, pricing sheet"
          onChange={(e) => onKnowledgeChange(e.target.value)}
          className="w-full rounded-lg border border-zinc-700 bg-zinc-900 px-3 py-2 text-sm text-zinc-100 outline-none placeholder:text-zinc-600 focus:border-blue-500 focus:ring-1 focus:ring-blue-500"
        />
      </div>

      <div>
        <label htmlFor="apps-input" className="mb-1.5 block text-sm font-medium text-zinc-300">
          Connected Apps
        </label>
        <p className="mb-2 text-xs text-zinc-500">Tools this teammate can read from or act on (optional).</p>
        <input
          id="apps-input"
          aria-label="Connected Apps"
          value={connectedApps}
          placeholder="e.g. gmail, slack"
          onChange={(e) => onConnectedAppsChange(e.target.value)}
          className="w-full rounded-lg border border-zinc-700 bg-zinc-900 px-3 py-2 text-sm text-zinc-100 outline-none placeholder:text-zinc-600 focus:border-blue-500 focus:ring-1 focus:ring-blue-500"
        />
      </div>
    </div>
  );
}
