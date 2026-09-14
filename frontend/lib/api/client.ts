import type { GateDecision, PlaybookDetail, PlaybookSummary, RunSnapshot } from "@/types/api";

const BASE_URL = process.env.NEXT_PUBLIC_GATEWAY_URL ?? "http://localhost:8080";

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(`${BASE_URL}${path}`, {
    ...options,
    headers: { "Content-Type": "application/json", ...options.headers },
  });
  if (!response.ok) {
    throw new Error(`Request to ${path} failed with status ${response.status}`);
  }
  return response.json();
}

export const api = {
  listPlaybooks: () => request<PlaybookSummary[]>("/v1/playbooks", { method: "GET" }),

  getPlaybook: (id: string) => request<PlaybookDetail>(`/v1/playbooks/${id}`, { method: "GET" }),

  createPlaybook: (input: { name: string; description?: string; canvas_json: unknown }) =>
    request<PlaybookDetail>("/v1/playbooks", { method: "POST", body: JSON.stringify(input) }),

  startRun: (playbookId: string, inputText: string) =>
    request<RunSnapshot>(`/v1/playbooks/${playbookId}/runs`, {
      method: "POST",
      body: JSON.stringify({ input: { text: inputText } }),
    }),

  getRunSnapshot: (runId: string) =>
    request<RunSnapshot>(`/v1/playbooks/runs/${runId}`, { method: "GET" }),

  submitGateDecision: (runId: string, decision: GateDecision) =>
    request<RunSnapshot>(`/v1/playbooks/runs/${runId}/gate`, {
      method: "POST",
      body: JSON.stringify(decision),
    }),
};
