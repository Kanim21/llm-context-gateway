"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api/client";
import type { PlaybookSummary } from "@/types/api";

interface TemplateGalleryProps {
  onSelect: (playbookId: string) => void;
}

export function TemplateGallery({ onSelect }: TemplateGalleryProps) {
  const [templates, setTemplates] = useState<PlaybookSummary[]>([]);

  useEffect(() => {
    api.listPlaybooks().then((playbooks) => setTemplates(playbooks.filter((p) => p.is_template)));
  }, []);

  return (
    <div>
      {templates.map((template) => (
        <button key={template.id} onClick={() => onSelect(template.id)}>
          <div>{template.name}</div>
          <div>{template.description}</div>
        </button>
      ))}
    </div>
  );
}
