"use client";

import { CENTER_X, DND_MIME, PALETTE, createNode, type PaletteItem } from "@/lib/canvas/builderGraph";
import { useBuilderStore } from "@/lib/store/builderStore";

export function Palette() {
  const addNode = useBuilderStore((s) => s.addNode);
  const nodeCount = useBuilderStore((s) => s.nodes.length);

  const onDragStart = (event: React.DragEvent, item: PaletteItem) => {
    event.dataTransfer.setData(DND_MIME, item.kind);
    event.dataTransfer.effectAllowed = "move";
  };

  // Click-to-add is a keyboard/no-drag fallback; drops onto the canvas set
  // exact cursor coordinates instead.
  const onClickAdd = (item: PaletteItem) => {
    addNode(createNode(item.kind, { x: CENTER_X + 320, y: 40 + nodeCount * 40 }));
  };

  return (
    <aside className="flex w-64 shrink-0 flex-col border-r border-zinc-800 bg-zinc-950/70">
      <div className="border-b border-zinc-800 px-4 py-3">
        <h2 className="text-xs font-semibold uppercase tracking-wider text-zinc-500">Blocks</h2>
        <p className="mt-1 text-[11px] leading-snug text-zinc-600">Drag onto the canvas, then wire the handles.</p>
      </div>
      <div className="flex flex-col gap-2 overflow-y-auto p-3">
        {PALETTE.map((item) => (
          <div
            key={item.kind}
            draggable
            onDragStart={(e) => onDragStart(e, item)}
            onClick={() => onClickAdd(item)}
            role="button"
            tabIndex={0}
            onKeyDown={(e) => {
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                onClickAdd(item);
              }
            }}
            className="group flex cursor-grab items-start gap-2.5 rounded-lg border border-zinc-800 bg-zinc-900 p-2.5 transition-colors hover:border-zinc-600 active:cursor-grabbing"
          >
            <span className="mt-0.5 select-none text-zinc-600 group-hover:text-zinc-400" aria-hidden>
              ⠿
            </span>
            <span className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-md text-base ${item.accent}`} aria-hidden>
              {item.icon}
            </span>
            <span className="min-w-0">
              <span className="block truncate text-sm font-medium text-zinc-100">{item.title}</span>
              <span className="block truncate text-[11px] text-zinc-500">{item.subtitle}</span>
            </span>
          </div>
        ))}
      </div>
    </aside>
  );
}
