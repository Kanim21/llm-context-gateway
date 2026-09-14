import { create } from "zustand";

export type ExecutionState = "idle" | "running" | "done" | "awaiting_approval" | "failed";

interface CanvasStore {
  executionStateByStepId: Record<string, ExecutionState>;
  setExecutionState: (stepId: string, state: ExecutionState) => void;
  resetExecutionState: () => void;
}

export const useCanvasStore = create<CanvasStore>((set) => ({
  executionStateByStepId: {},
  setExecutionState: (stepId, state) =>
    set((s) => ({ executionStateByStepId: { ...s.executionStateByStepId, [stepId]: state } })),
  resetExecutionState: () => set({ executionStateByStepId: {} }),
}));
