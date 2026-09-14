# CLAUDE.md

Guidance for Claude Code (or any agent) working in this repository.

## What this is

Agent Gateway is a self-hosted, local-first proxy that sits between an agent loop
and the OpenAI / Anthropic APIs. Its job is context management for long-running
loops: masking stale tool output, compacting history, tracking loop state outside
the prompt, and reporting honest, unblended metrics about what it did.

## Non-negotiable rules

These are enforced in code and covered by tests. Do not weaken them to make a
change easier, and do not "fix" a failing test by loosening the rule it protects.

1. **Cache prefix immutability.** `core/cache_boundary.py` computes a SHA-256
   checksum of every frozen prefix on registration. Any code path that could
   cause a previously-registered prefix to be re-sent in mutated form must raise
   `CachePrefixMutationError`, not silently proceed.
2. **Strict metric separation.** Token Reduction %, Cost Reduction %, and Call
   Reduction % (`proxy/metrics.py::UnblendedMetrics`) are always computed and
   reported independently. Never introduce a combined/blended efficiency score.
3. **Real tokenization only.** Token counts come from named tokenizers
   (`cl100k_base` via tiktoken, `claude-bpe` via the bundled vocab in
   `core/vocab/`). Character counts (`TokenizerEngine.char_count`) are a
   secondary, storage-layer statistic only — never substitute them for a real
   token count in a metric that claims to be about tokens.
4. **Lossy passes default to False.** Every field in `LossyPassConfig`
   (`core/lossy_passes.py`) defaults off. If you add a new lossy transform, it
   must default off too, and it must be benchmarked in the faithfulness matrix
   before being described as safe at any aggression tier.
5. **The `.zclaw` codec never reaches the model.** `storage/zclaw.py` is a
   storage-layer-only concern. It must never be applied to text that gets sent
   to `adapters/openai_adapter.py` or `adapters/anthropic_adapter.py`, and its
   round-trip must stay byte-identical — this is checked in CI on the full
   55-payload corpus (`benchmarks/check_regressions.py`, `.github/workflows/ci.yml`).

## Honesty over hype

Report what benchmarks actually measure, including unflattering results (e.g.
pruning at MEDIUM/HIGH aggression driving task-completion retention to 0% —
see `BENCHMARKS.md`). Do not round up, do not claim a capability was verified
if the environment couldn't verify it (e.g. no Docker daemon available locally
means the Dockerfile is reviewed, not build-tested), and label every simulated
number (the cost model) as illustrative rather than observed.

## Working in this repo

- Run the test suite: `pytest tests/ -v`
- Run the benchmark suite: `python benchmarks/runner.py` (regenerates
  `BENCHMARKS.md` and `benchmarks/raw_results.json`)
- Run the CI regression guardrail locally: `python benchmarks/check_regressions.py`
- The corpus lives in `benchmarks/corpus/*.json` and is authored by
  `benchmarks/generate_corpus.py`, including regex-extracted ground-truth
  `facts` (numbers, dates, negations) used by the faithfulness matrix. If you
  add corpus payloads, keep the fact-extraction pattern consistent or the
  faithfulness percentages become meaningless.
- `website/` is the static product site; `docs/` is the interactive benchmark
  visualizer that reads `raw_results.json` directly via `fetch()` — no build
  step, no bundler. Keep both dependency-free.
