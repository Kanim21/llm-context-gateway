"use client";

import { useRouter } from "next/navigation";
import { TopNav } from "@/components/nav/TopNav";
import { TemplateGallery } from "@/components/templates/TemplateGallery";

export default function DashboardPage() {
  const router = useRouter();
  return (
    <>
      <TopNav />
      <main className="mx-auto w-full max-w-6xl flex-1 px-6 py-10">
        <div className="mb-8">
          <h1 className="text-2xl font-semibold tracking-tight text-zinc-100">Playbooks</h1>
          <p className="mt-1 text-sm text-zinc-500">
            Pick a template to open it on the canvas, add teammates, and run it end to end.
          </p>
        </div>
        <TemplateGallery onSelect={(id) => router.push(`/playbooks/${id}`)} />
      </main>
    </>
  );
}
