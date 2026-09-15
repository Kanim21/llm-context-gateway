import { describe, it, expect } from "vitest";
import { addTeammateToCanvas } from "./addTeammateToCanvas";
import type { TeammateDrawerConfig } from "@/components/drawer/TeammateDrawer";

const base = {
  nodes: [
    { id: "start", type: "start", data: {} },
    { id: "t1", type: "teammate", data: { role: "Q", objective: "O", tier: "speed" } },
    { id: "end", type: "end", data: {} },
  ],
  edges: [
    { source: "start", target: "t1" },
    { source: "t1", target: "end" },
  ],
};

const config: TeammateDrawerConfig = {
  role: "Router", objective: "Route", tier: "speed", knowledge: [], connected_apps: [],
};

describe("addTeammateToCanvas", () => {
  it("inserts before end and rewires the chain (t1 -> new -> end)", () => {
    const out = addTeammateToCanvas(base, config, "teammate_new");
    expect(out.nodes.map((n) => n.id)).toContain("teammate_new");
    expect(out.edges).toContainEqual({ source: "t1", target: "teammate_new" });
    expect(out.edges).toContainEqual({ source: "teammate_new", target: "end" });
    // Still a single linear chain: exactly one edge into end.
    expect(out.edges.filter((e) => e.target === "end")).toHaveLength(1);
  });

  it("persists the tier/knowledge/apps the drawer collected", () => {
    const out = addTeammateToCanvas(
      base, { ...config, knowledge: ["kb"], connected_apps: ["slack"] }, "n",
    );
    const node = out.nodes.find((n) => n.id === "n")!;
    expect(node.data).toMatchObject({ tier: "speed", knowledge: ["kb"], connected_apps: ["slack"] });
  });

  it("does not mutate the input canvas", () => {
    const before = JSON.parse(JSON.stringify(base));
    addTeammateToCanvas(base, config, "n");
    expect(base).toEqual(before);
  });

  it("throws if there is no end node", () => {
    expect(() => addTeammateToCanvas({ nodes: [], edges: [] }, config)).toThrow(/end node/);
  });
});
