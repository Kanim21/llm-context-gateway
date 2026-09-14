# Visual Playbook Builder (frontend)

The Next.js UI for Agent Gateway's playbooks: build a linear chain of
teammates and approval gates on a canvas, start a run, watch it stream, and
approve, edit or reject at each gate.

## Running it

```bash
npm install
npm run dev     # http://localhost:3000
```

It talks to the gateway backend, which must be running separately:

```bash
uvicorn agent_gateway.proxy.server:app --port 8080
```

The backend URL defaults to `http://localhost:8080`. Point it somewhere else
with `NEXT_PUBLIC_GATEWAY_URL`. The backend only allows browser requests from
`http://localhost:3000`, so if you move either side, update the CORS origin in
`agent_gateway/proxy/server.py` too.

## Checks

```bash
npm test        # vitest
npm run build   # includes the TypeScript check
```

## Known v1 limitations (stated explicitly, not hidden)

- A crash mid-step re-runs that step from scratch; only gate-paused runs
  resume without re-work.
- Connected Apps affect only prompt framing — no live API calls, no OAuth.
- No branching, no parallel steps, no conditional routing.
- Single-user, no auth — anyone who can reach the frontend can run and
  approve any playbook.

Additionally, in this UI: teammates added through the "Add Teammate" drawer are
not saved to the playbook — there is no endpoint for that yet — so they live in
your browser tab only, and Run is disabled while any are present.
