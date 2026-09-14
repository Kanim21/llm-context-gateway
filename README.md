# Agent Gateway

A self-hosted, local-first LLM proxy for context management in long-running
agent loops. It sits between your agent and the OpenAI / Anthropic APIs,
masking stale tool output, compacting history at a token threshold, and
tracking loop state in SQLite — without ever mutating the cached prefix your
provider bills against.

Full product overview: `website/index.html`. Interactive benchmark
visualizer: `docs/index.html` (reads `benchmarks/raw_results.json`). Measured
results and methodology: `BENCHMARKS.md`.

## Non-negotiable rules

1. **Cache prefix immutability** — every frozen prefix is SHA-256 checksummed
   on registration; any later mismatch raises `CachePrefixMutationError`.
2. **Strict metric separation** — Token Reduction %, Cost Reduction %, and
   Call Reduction % are always reported separately, never blended.
3. **Real tokenization only** — counts come from `cl100k_base` (tiktoken) for
   OpenAI and a community-mirrored `claude-bpe` vocab for Anthropic; character
   counts are secondary/storage-only.
4. **Lossy passes default to False** — structural stripping, eco trimming,
   pruning, numeric quantization, and query filtering all require explicit
   opt-in, with published faithfulness costs at every aggression tier.
5. **The `.zclaw` storage codec never reaches the model** — it lives strictly
   in the SQLite storage layer, with byte-identical round-trip proof on every
   corpus payload, checked in CI.

## Project layout

```
agent_gateway/
  core/
    cache_boundary.py       # SHA-256 prefix immutability + tokenizer engine
    observation_masking.py  # trailing-K masking with artifact receipts
    compaction.py            # hierarchical, prefix-preserving suffix summarization
    blackboard.py             # SQLite-backed loop state (goals, milestones, do-not-retry)
    guardrails.py             # duplicate-action, AST syntax, circuit-breaker checks
    routing.py                # flagship / tier-2 model routing
    lossy_passes.py           # opt-in lossy transforms (all default False)
    tool_pruning.py           # fail-open tool schema pruning
    semantic_dedup.py         # Jaccard-shingle near-duplicate detection
    branch_collapse.py        # collapsing abandoned exploration branches
    vocab/claude-bpe-tokenizer.json  # bundled offline Claude BPE vocab
  adapters/
    openai_adapter.py        # async relay to /v1/chat/completions
    anthropic_adapter.py     # async relay to /v1/messages, ephemeral cache_control
  proxy/
    server.py                # FastAPI app: /v1/chat/completions, /v1/messages, /v1/metrics, /healthz
    config.py                # GatewayConfig (Pydantic), all lossy defaults False
    metrics.py                # UnblendedMetrics, MetricsAccumulator
  storage/
    sqlite_store.py          # artifacts, blackboard state, metrics events
    zclaw.py                  # reversible n-gram + DEFLATE storage codec

benchmarks/
  corpus/                    # 55 hand-authored payloads (prose, code, html, tables,
                              # agent transcripts, adversarial negations), each with
                              # regex-extracted ground-truth facts
  generate_corpus.py         # builds benchmarks/corpus/*.json
  runner.py                  # round-trip proof, faithfulness matrix, latency,
                              # cost model, growth curve -> BENCHMARKS.md
  check_regressions.py       # CI guardrail (boundary, round-trip, latency ceilings)
  raw_results.json           # machine-readable output of the last runner.py run

tests/test_agent_gateway.py  # 69 tests across every module

deployment/
  Dockerfile                 # multi-stage, non-root, HEALTHCHECK
  docker-compose.yml

website/                     # static product site (index.html, style.css, app.js)
docs/                        # interactive benchmark visualizer (index.html, style.css, app.js)
```

## Getting started

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

pytest tests/ -v

# Set upstream credentials for the providers you want to relay to:
export OPENAI_API_KEY=...
export ANTHROPIC_API_KEY=...
uvicorn agent_gateway.proxy.server:app --host 0.0.0.0 --port 8080
```

Or via Docker Compose:

```bash
cd deployment && docker compose up --build
```

## Running the benchmarks

```bash
python benchmarks/generate_corpus.py   # regenerate the 55-payload corpus (idempotent)
python benchmarks/runner.py            # produces BENCHMARKS.md + benchmarks/raw_results.json
python benchmarks/check_regressions.py # fast CI guardrail: boundary, round-trip, latency
```

## Measured results (see `BENCHMARKS.md` for full detail)

- **Round-trip:** 55/55 (100%) corpus payloads byte-identical through `.zclaw`.
- **Storage reduction:** ~10.2% — a secondary, non-blended metric.
- **Faithfulness:** varies sharply by pass and aggression tier; pruning at
  MEDIUM/HIGH aggression drops task-completion retention to 0% on the
  adversarial corpus. This is reported, not hidden — lossy passes default off
  for exactly this reason.
- **Latency:** gateway-side processing overhead only (not upstream provider
  latency) — warm p50/p95/p99 sub-millisecond, cold p99 in the low
  single-digit milliseconds, measured over 1000 warm / 200 cold requests.
- **Cost model:** an illustrative analytical simulation (not observed
  billing) comparing four strategies across OpenAI and Anthropic pricing.
- **Growth curve:** 3 runs of 27 turns; average offload hit rate 100.0%
  (masked observations that were never re-hydrated for the rest of the run).

## Honesty notes

- The `claude-bpe` tokenizer is a community-mirrored approximation
  (`Xenova/claude-tokenizer`), not Anthropic's literal production vocabulary —
  Anthropic's SDK does not ship an offline tokenizer.
- The Docker image is reviewed but was not locally build-verified in this
  environment (no Docker daemon available); CI builds and publishes it to
  GHCR on every push to `main`.
- Cost figures are simulations against a fixed, documented loop shape, not
  observed provider invoices.

## License

Not yet specified.
