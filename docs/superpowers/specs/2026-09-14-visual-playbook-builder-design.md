# Visual Playbook Builder — Design Spec

**Sub-project:** 2 of the Agent Gateway product (Sub-project 1, multi-provider LLM
routing, shipped as PR #1 at `github.com/Kanim21/llm-context-gateway`).

**Repo:** `/Users/macbook/agent-gateway`, main branch.

## Goal

A low-code, drag-and-drop UI that lets non-technical operations, sales, and
marketing users compose a sequence of AI "teammates" into a **Playbook** —
an ordered, human-reviewable workflow — without ever seeing developer
jargon (no DAGs, nodes, embeddings). Playbooks execute through the existing
multi-provider LLM Context Gateway (Sub-project 1), inherit its context
management (masking, compaction, cache-boundary immutability), and pause
for human approval at defined checkpoints.

## Audience and mental model

| Developer concept | User-facing term |
|---|---|
| Agent / LLM-backed worker | **Teammate** |
| System prompt / persona | **Role** |
| Task description | **Objective** |
| Model capability tier | **Brain/Speed** |
| Tool/integration | **Connected App** |
| Workflow definition | **Playbook** |
| Human review checkpoint | **Approval Gate** |
| Execution instance | **Run** |

All UI copy, error messages, and API-facing labels use the right column.
Internal code (this spec included) uses precise engineering terms freely.

## Non-goals (explicitly deferred)

- **Real Connected App integrations.** No OAuth, no live Slack/Gmail/HubSpot
  API calls in this phase. Selecting a Connected App only adds capability
  framing to the teammate's prompt. Real integrations arrive later as a
  dedicated sub-project built on MCP servers.
- **Multi-tenancy / auth.** Single-user, local-first, no login — matching
  the existing gateway's trust model. Tables carry a `workspace_id` column
  fixed to `"default"` so multi-tenancy can be layered on later without a
  schema rewrite, but no enforcement exists yet.
- **Branching or parallel execution.** v1 playbooks are strictly linear
  chains of steps, optionally interrupted by approval gates. No
  conditional edges, no fan-out/fan-in.
- **Persisted token-level replay.** Live token streaming is in-memory only.
  A dropped SSE connection reconnects via a state snapshot, not a replayed
  token history (see "SSE and reconnection" below).
- **Authoring the 3 launch templates' actual prompts/content.** This spec
  requires the schema to be expressive enough to represent Inbound Sales
  Triage, Multi-Channel Social Content, and Support Ticket Escalation as
  linear chains with at least one gate each; writing their actual Role/
  Objective text is implementation-plan content work, not architecture.

## Architecture overview

Single Python process (the existing `agent_gateway` FastAPI app, extended)
plus a new standalone Next.js frontend. No new services, no new databases.

```
Next.js frontend (canvas editor + run viewer)
        │  REST (typed via FastAPI's generated OpenAPI schema) + SSE
        ▼
agent_gateway (FastAPI, single process)
  ├── proxy/            (existing, unchanged: /v1/chat/completions, /v1/messages)
  ├── core/             (existing, unchanged: cache_boundary, compaction, blackboard,
  │                       provider_routing, observation_masking, guardrails, ...)
  └── orchestrator/      (NEW)
        ├── schema.py     — canonical Playbook execution schema (Pydantic)
        ├── compiler.py   — React-Flow canvas graph -> canonical schema, or errors
        ├── engine.py     — PlaybookRunner: sequential cursor, gate pause/resume
        ├── events.py     — in-memory per-run pub/sub (SSE only, not persisted)
        ├── store.py      — SQLite tables: playbooks, runs, step_records, gate_decisions
        ├── routes.py     — FastAPI router, mounted into the existing app
        └── templates/    — the 3 built-in playbooks, each a full record
                             ({name, description, canvas_json, definition_json})
```

**Core architectural principle:** a playbook run *is* one of the
long-running agent loops this gateway already exists to manage. Each run
registers with `core/blackboard.py` under its `run_id` as the loop
identifier; as step outputs accumulate into the run's transcript, the
existing masking/compaction pipeline (`core/observation_masking.py`,
`core/compaction.py`) applies to that transcript exactly as it would for
any other long-running loop, and `core/cache_boundary.py`'s immutability
guarantee covers it too. The orchestrator engine calls adapters and
`core/provider_routing.py::ProviderRegistry.resolve()` directly via
in-process function calls — no HTTP loopback to its own `/v1/chat/completions`
route.

## Data model

New SQLite tables (same database file the gateway already uses via
`storage/sqlite_store.py`; the orchestrator's `store.py` owns these tables
as a separate module — same storage engine, distinct responsibility). Every
table carries `workspace_id TEXT NOT NULL DEFAULT 'default'`.

```sql
CREATE TABLE playbooks (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL DEFAULT 'default',
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    schema_version INTEGER NOT NULL,
    definition_json TEXT NOT NULL,   -- canonical Playbook schema (see below)
    canvas_json TEXT NOT NULL,       -- raw React Flow graph: nodes, edges, positions
    is_template INTEGER NOT NULL DEFAULT 0,  -- 1 for the 3 seeded launch templates
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE playbook_runs (
    id TEXT PRIMARY KEY,
    playbook_id TEXT NOT NULL REFERENCES playbooks(id),
    workspace_id TEXT NOT NULL DEFAULT 'default',
    status TEXT NOT NULL,             -- running|paused|completed|failed|rejected
    current_step_index INTEGER NOT NULL DEFAULT 0,
    input_json TEXT NOT NULL DEFAULT '{}',  -- {"text": str} the user typed to kick off the run (the "Intake")
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE run_step_records (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES playbook_runs(id),
    step_index INTEGER NOT NULL,
    step_id TEXT NOT NULL,
    status TEXT NOT NULL,             -- pending|running|completed|failed|awaiting_approval|rejected
    output_json TEXT,                 -- NULL until completed; shape {"text": str}
    started_at TEXT,
    completed_at TEXT
);

CREATE TABLE run_gate_decisions (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES playbook_runs(id),
    step_index INTEGER NOT NULL,
    decision TEXT NOT NULL,           -- approve|reject|edit
    edited_output_json TEXT,          -- NULL unless decision == edit; shape {"text": str}
    decided_at TEXT NOT NULL
);
```

Per the durability decision: every step completion and every gate pause
writes its checkpoint row immediately, before the engine proceeds. Per the
SSE decision: token deltas are never written here, only step-level results.

## Playbook execution schema (canonical, decoupled from the canvas)

```python
class TeammateConfig(BaseModel):
    role: str
    objective: str
    tier: Literal["speed", "balanced", "brain"]
    connected_apps: list[str] = []   # e.g. ["gmail", "hubspot"] — v1 enum: gmail, slack, hubspot
    knowledge: list[str] = []        # free-text knowledge snippets

class GateConfig(BaseModel):
    label: str
    allow_edit: bool = True   # can a human edit the proposed text before approving

class PlaybookStep(BaseModel):
    step_id: str
    type: Literal["teammate", "approval_gate"]
    teammate: TeammateConfig | None = None   # set iff type == "teammate"
    gate: GateConfig | None = None            # set iff type == "approval_gate"

class Playbook(BaseModel):
    schema_version: int = 1
    id: str
    workspace_id: str = "default"
    name: str
    description: str = ""
    steps: list[PlaybookStep]
```

**Step output shape (v1):** every `teammate` step produces
`output_json = {"text": str}` — the model's full response, as plain text.
There is no structured/multi-field output in v1 (no JSON-mode parsing, no
per-field extraction), so a gate has nothing more granular to edit than
that single `text` value — hence `GateConfig.allow_edit` is a boolean, not
a list of field names. Structured, multi-field teammate output is a
natural v2 extension once a real need for it shows up.

Example (fragment):

```json
{
  "schema_version": 1,
  "id": "pb_abc123",
  "name": "Inbound Sales Triage",
  "steps": [
    {"step_id": "s1", "type": "teammate", "teammate": {
      "role": "Sales Qualifier",
      "objective": "Read the inbound lead and assess fit and urgency",
      "tier": "balanced",
      "connected_apps": ["gmail", "hubspot"],
      "knowledge": []
    }},
    {"step_id": "s2", "type": "approval_gate", "gate": {
      "label": "Review qualification before routing",
      "allow_edit": true
    }},
    {"step_id": "s3", "type": "teammate", "teammate": {"...": "..."}}
  ]
}
```

`connected_apps` is a flat list of strings (fixed v1 enum: `gmail`, `slack`,
`hubspot`) rather than an object, so it can later be widened to an
`mcp_servers: list[str]` shape for real integrations without breaking this
schema's version.

## Compiler (canvas graph → canonical schema)

Input: the raw React Flow graph (`canvas_json` — nodes with `type`,
`data`, `position`; edges with `source`/`target`). Output: either a valid
`Playbook` or a list of `{node_id, message}` validation errors, in
plain-language copy:

1. Exactly one node of type `start`. Zero or more than one → *"A playbook
   needs exactly one starting point."*
2. Every non-terminal node has exactly one outgoing edge. Zero → *"This
   step doesn't lead anywhere — connect it to the next step or mark it as
   the end."* More than one → *"This step has two next steps — Playbooks
   run one step at a time."*
3. No cycles: walking edges from `start`, revisiting a node is an error →
   *"This playbook loops back on itself — playbooks run start to finish,
   once."*
4. No orphan nodes (unreachable from `start`) → *"This step isn't
   connected to the playbook — attach it to the chain or remove it."*
5. Every `approval_gate` node has a non-empty `label`.
6. Every `teammate` node has non-empty `role` and `objective`.

`POST /v1/playbooks` runs the compiler and stores both `canvas_json` and
the resulting `definition_json` together, so re-opening a playbook in the
editor always reflects the exact graph that produced its current
execution schema.

## Execution engine (`PlaybookRunner`)

States: `playbook_runs.status ∈ {running, paused, completed, failed, rejected}`;
`run_step_records.status ∈ {pending, running, completed, failed, awaiting_approval, rejected}`.

**`start_run(playbook_id, input_text)`:** loads `definition_json`, creates
a `playbook_runs` row (`status=running`, `current_step_index=0`,
`input_json={"text": input_text}`), registers the run with
`core/blackboard.py` under `run_id`, and launches the execution loop as a
background task. `input_text` is the free-text intake a non-technical user
types into the "Run this playbook" box (e.g. the inbound lead email for
Inbound Sales Triage) — it is the only externally supplied content a run
carries, and it never changes once the run starts.

**Blackboard prompt assembly:** every teammate step's prompt is assembled
fresh from the run's blackboard state into one Markdown-structured string,
never a raw concatenation:

```
## Intake
{run.input_json.text}

## Previous Teammates
{for each prior completed teammate step, in order:}
### {step.teammate.role}
{step.output_json.text}

{omitted entirely if this is the first teammate step}

## Current Assignment
Role: {step.teammate.role}
Objective: {step.teammate.objective}
Knowledge: {step.teammate.knowledge, joined as a bullet list, omitted if empty}
Connected Apps: {step.teammate.connected_apps capability framing, omitted if empty}
```

This assembled string becomes the user-turn content sent to
`ProviderRegistry.resolve()`'s chosen adapter. The `## Previous Teammates`
section is exactly the run's growing transcript that
`core/observation_masking.py`/`core/compaction.py` manage as it grows —
those modules operate on this same Markdown-sectioned text, unchanged.

**Execution loop**, for `step = steps[current_step_index]`:
- `type == "teammate"`: mark the step record `running`, emit
  `step_started`. Resolve `tier` → concrete model name via the
  `playbook_tiers` config map (see below), then resolve that model through
  the existing `ProviderRegistry.resolve()` exactly as `/v1/chat/completions`
  does. Assemble the prompt per "Blackboard prompt assembly" above. Stream
  the completion; each chunk emits a `token`
  event (in-memory only, never persisted). On completion: persist the step
  record (`completed`, `output_json`), append the output to the blackboard
  transcript, emit `step_completed`, increment and persist
  `current_step_index`. If this was the last step, mark the run
  `completed` and emit `run_completed` instead of continuing the loop.
- `type == "approval_gate"`: mark the step record `awaiting_approval`, mark
  the run `paused`, emit `gate_paused` with the proposed output and
  `allow_edit`, and stop the loop — it resumes only via an external call.
- **On an unrecoverable step error** (e.g. the adapter raises after its own
  retry policy): mark the step record `failed` with the error message, mark
  the run `failed`, emit `run_failed`, and stop. Playbooks fail loudly, not
  silently.

**`resume_with_decision(run_id, decision, edited_output=None)`:** loads the
run and the step record at its paused `current_step_index`.
- `approve`: record the decision; advance `current_step_index`; set
  `status=running`; resume the loop.
- `edit`: same as approve, but the step record's `output_json` is
  overwritten with `edited_output` before advancing.
- `reject`: set the step record and run `status=rejected`; stop — no
  further steps run.

**Crash recovery:** on process startup, the orchestrator scans
`playbook_runs` for `status=running` (a row can only be in this state if
the process died mid-step — every other transition, including normal
completion, persists `paused`/`completed`/`failed`/`rejected` before
returning control). For each such run, before resuming: reset the
`run_step_records` row at the persisted `current_step_index` from
`running` to `pending` — it was interrupted mid-execution and never
actually completed, so leaving it `running` would show a step log that
claims work is in progress when nothing is. Only after that reset does the
orchestrator re-launch the execution loop at `current_step_index`,
re-running that step from its start. This is safe in v1 because steps have
no live side effects (Connected Apps are stub-only). Runs already `paused`
need no recovery action; they already wait durably on
`resume_with_decision`, and their paused step's record is correctly
`awaiting_approval`, not `running`.

## Brain/Speed tier mapping

Independent of `core/routing.py`'s existing flagship/tier-2 routing
(scoped to the proxy's own context-compaction heuristics — a different
concern). The orchestrator's tier map lives in the same
`AGENT_GATEWAY_CONFIG` JSON file introduced in Sub-project 1, as a new
top-level key:

```json
{
  "providers": { "...": "as defined in Sub-project 1" },
  "playbook_tiers": {
    "speed": "gpt-4o-mini",
    "balanced": "gpt-4o",
    "brain": "gemini-1.5-pro"
  }
}
```

The engine resolves a teammate's `tier` to a model name via this map, then
hands that model name to `ProviderRegistry.resolve()` unchanged — so a
`brain`-tier teammate transparently routes through Gemini or DeepSeek if
the operator's config says so, with no orchestrator-side provider logic.

## API surface

- `POST /v1/playbooks` — body `{name, description, canvas_json}`. `201` →
  `{id, definition_json}`. `422` → `{errors: [{node_id, message}]}`.
- `GET /v1/playbooks` — list of `{id, name, description, updated_at,
  is_template}`. The 3 seeded templates (see "Out-of-the-box templates"
  below) appear in this same list, flagged `is_template: true`, alongside
  any user-created playbooks — there is no separate templates endpoint.
- `GET /v1/playbooks/{id}` — full record (`canvas_json` + `definition_json`).
- `POST /v1/playbooks/{id}/runs` — body `{input: {text: str}}` (the
  Intake). `201` → `{run_id, status: "running"}`.
- `GET /v1/playbooks/runs/{run_id}` — snapshot:
  `{run_id, playbook_id, status, current_step_index, steps: [{step_id, status, output: {text}}]}`
  — this is what a reconnecting tab calls before resubscribing to SSE.
- `GET /v1/playbooks/runs/{run_id}/events` — SSE stream:
  `step_started {step_id, step_index}`, `token {step_id, delta}`,
  `step_completed {step_id, output: {text}}`,
  `gate_paused {step_id, label, allow_edit, proposed_output: {text}}`,
  `run_completed {}`, `run_failed {error}`.
- `POST /v1/playbooks/runs/{run_id}/gate` — body
  `{decision: "approve"|"reject"|"edit", edited_output?: {text}}` → `{status}`.

## SSE and reconnection

Live token streaming is in-memory only (`orchestrator/events.py`, a
per-run pub/sub keyed by `run_id`), never written to SQLite — avoiding
high-frequency disk writes. On page load or reconnect, the frontend calls
`GET /v1/playbooks/runs/{run_id}` for a snapshot of completed step outputs
and current status, then opens `GET .../events` for live updates on the
active or future steps. A step that was mid-stream when the connection
dropped is seen only in its finished form on reconnect (no partial-token
replay) — an accepted v1 trade-off.

**Stream termination:** the SSE response is a server-side generator over
the run's pub/sub topic. It explicitly closes — the generator returns,
ending the HTTP response — immediately after yielding any of the three
terminal-for-that-connection events: `run_completed`, `run_failed`, or
`gate_paused`. The server never leaves the connection open past one of
these events waiting for a client disconnect; a paused run's frontend
tab sees the stream close and knows to show the Approve/Reject/Edit UI
from the snapshot it already has, and a client that reconnects after a
gate decision opens a fresh `GET .../events` call, which is exactly the
snapshot-then-resubscribe reconnect path described above.

## Frontend architecture

New `frontend/` directory at the repo root (sibling to `website/`, `docs/`):

```
frontend/
  src/
    app/
      page.tsx                              # playbook dashboard
      playbooks/[id]/page.tsx               # canvas editor
      playbooks/[id]/run/[runId]/page.tsx   # live run view
    components/
      canvas/
        PlaybookCanvas.tsx        # React Flow wrapper
        TeammateCardNode.tsx      # avatar, role badge, tier badge, execution glow
        ApprovalGateNode.tsx
        StartNode.tsx / EndNode.tsx
      drawer/
        TeammateDrawer.tsx         # 4-step wizard shell
        steps/
          RoleStep.tsx
          ObjectiveStep.tsx
          TierStep.tsx
          KnowledgeAppsStep.tsx
      run/
        RunControlBar.tsx
        GateReviewPanel.tsx        # Approve / Reject / Edit UI
        StepOutputPane.tsx
      templates/
        TemplateGallery.tsx
    lib/
      api/
        client.ts                 # typed fetch, generated via openapi-typescript
                                    # against the FastAPI app's /openapi.json
      sse/
        useRunEvents.ts            # snapshot-fetch-then-subscribe reconnect hook
      store/
        canvasStore.ts             # Zustand — React Flow graph state
    types/
      api.ts                      # generated from /openapi.json
```

`useRunEvents` drives per-node execution state (`idle`/`running`/`done`/
`awaiting_approval`), keyed by `step_id`, which `TeammateCardNode` renders
as a glow/badge — state changes arrive only from SSE events plus the
initial snapshot fetch, no polling.

## Testing strategy

**Backend** (pytest, TDD, no real LLM calls — reusing the
`httpx.MockTransport` pattern from Sub-project 1):
- Compiler: one test per validation rule (valid chain; multiple starts;
  branching node; cycle; orphan node; empty gate label; empty
  teammate role/objective).
- Engine: linear run to completion against a fake adapter; pause-at-gate;
  reject; edit-then-resume; simulated-restart resume (construct a
  `playbook_runs` row with `status=running` directly, restart the engine,
  confirm it re-runs the interrupted step and completes); unrecoverable
  step error marks the run `failed`.
- Each of the 3 templates: compiles cleanly and executes end-to-end
  against a mock adapter.

**Frontend** (Vitest + React Testing Library): the 4 Teammate Drawer step
components; `TeammateCardNode` rendering each execution-glow state;
`useRunEvents`' reconnect behavior (snapshot fetch → subscribe).

## Out-of-the-box templates

Three templates ship as full records (`name`, `description`, `canvas_json`,
`definition_json`) as static JSON files under
`agent_gateway/orchestrator/templates/` — that directory is their source
of truth and the only place their content is authored/edited. On every app
startup, an idempotent seed routine upserts each template's file into the
`playbooks` table, keyed by a stable id embedded in the template file
itself (e.g. `tpl_inbound_sales_triage`): `INSERT ... ON CONFLICT(id) DO
UPDATE` so a template already present is refreshed to match its file
(picking up spec/copy edits shipped in a later release) rather than
duplicated, and a first-ever startup populates an empty table. This makes
the three templates appear directly in `GET /v1/playbooks` alongside
user-created playbooks — no separate templates-only listing endpoint is
needed, and `GET /v1/playbooks/templates` from earlier drafts of this spec
is dropped in favor of just `GET /v1/playbooks`. A user "using" a template
opens its seeded playbook and immediately has a runnable, pre-built
Playbook; editing it edits their own copy in the same table like any other
playbook (the seed only re-upserts the original template id, never a
user's edited copy of it, since those get their own generated ids at
creation). Each template must be a valid linear chain with at least one
approval gate, per the schema above:

- **Inbound Sales Triage** — qualify an inbound lead, gate before routing.
- **Multi-Channel Social Content** — draft platform-specific posts from one
  brief, gate before publishing.
- **Support Ticket Escalation** — triage and draft an escalation response,
  gate before sending.

Authoring their exact Role/Objective/Knowledge content is implementation-
plan work, not part of this spec.

## Known v1 limitations (stated explicitly, not hidden)

- A crash mid-step re-runs that step from scratch; only gate-paused runs
  resume without re-work.
- Connected Apps affect only prompt framing — no live API calls, no OAuth.
- No branching, no parallel steps, no conditional routing.
- Single-user, no auth — anyone who can reach the frontend can run and
  approve any playbook.
