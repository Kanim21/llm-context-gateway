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
    <div>
      <label htmlFor="knowledge-input">Knowledge</label>
      <input id="knowledge-input" aria-label="Knowledge" value={knowledge}
             onChange={(e) => onKnowledgeChange(e.target.value)} />

      <label htmlFor="apps-input">Connected Apps</label>
      <input id="apps-input" aria-label="Connected Apps" value={connectedApps}
             onChange={(e) => onConnectedAppsChange(e.target.value)} />
    </div>
  );
}
