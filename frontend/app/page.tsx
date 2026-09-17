"use client";

import { useRouter } from "next/navigation";
import { TemplateGallery } from "@/components/templates/TemplateGallery";

export default function DashboardPage() {
  const router = useRouter();
  return (
    <main>
      <h1>Playbooks</h1>
      <TemplateGallery onSelect={(id) => router.push(`/playbooks/${id}`)} />
    </main>
  );
}
