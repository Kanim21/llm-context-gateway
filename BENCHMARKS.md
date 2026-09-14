# Agent Gateway -- Benchmark Report

All figures below are produced by `benchmarks/runner.py` running against the 55-payload corpus in `benchmarks/corpus/` on this machine. Token counts use named tokenizers only (`cl100k_base` via `tiktoken`, `claude-bpe` via a locally bundled BPE vocab) -- never a character-count proxy. Where a figure is a simulation rather than an observed measurement, it is labeled as such.

## 1. Storage Codec Round-Trip Proof

**55/55 payloads (100.0%) round-tripped byte-identical** through `storage/zclaw.py` (encode then decode, compared byte-for-byte).

- Total original bytes: 19,810
- Total encoded bytes: 17,792
- Storage-layer byte reduction: 10.2% (secondary storage metric -- not a token count, see Sec 1 of the spec)

## 2. Faithfulness Matrix (Lossy Passes)

Measures the fraction of ground-truth numbers/dates/negation-phrases (extracted from each corpus payload at authoring time) that are still present verbatim after each pass. All these passes default OFF; this table exists so an operator who opts in knows exactly what they are trading away.

| Pass | Tier | Numbers Retained | Dates Retained | Negations Retained | Task Completion Retained |
|---|---|---|---|---|---|
| structural_stripping | LOW | 97.8% | 100.0% | 94.5% | 100.0% |
| structural_stripping | MEDIUM | 97.8% | 100.0% | 94.5% | 100.0% |
| structural_stripping | HIGH | 97.8% | 100.0% | 94.5% | 100.0% |
| eco_trimming | LOW | 100.0% | 100.0% | 95.5% | 100.0% |
| eco_trimming | MEDIUM | 100.0% | 100.0% | 95.5% | 100.0% |
| eco_trimming | HIGH | 100.0% | 100.0% | 95.5% | 100.0% |
| pruning | LOW | 85.9% | 95.5% | 80.9% | 20.0% |
| pruning | MEDIUM | 80.4% | 93.6% | 68.2% | 0.0% |
| pruning | HIGH | 77.9% | 93.6% | 61.8% | 0.0% |
| numeric_quantization | LOW | 97.6% | 100.0% | 95.5% | 100.0% |
| numeric_quantization | MEDIUM | 98.8% | 100.0% | 95.5% | 100.0% |
| numeric_quantization | HIGH | 91.2% | 100.0% | 95.5% | 100.0% |
| query_filtering | LOW | 98.0% | 100.0% | 93.6% | 90.0% |
| query_filtering | MEDIUM | 98.0% | 100.0% | 93.6% | 90.0% |
| query_filtering | HIGH | 100.0% | 100.0% | 95.5% | 100.0% |
| compaction_extractive_fallback | N/A (single tier) | 74.7% | 88.6% | 78.8% | n/a |

## 3. Wall-Clock Latency (Gateway Processing Overhead)

Measures gateway-side processing overhead only (masking + compaction + guardrail checks), not upstream provider response time -- this benchmark does not call OpenAI or Anthropic over the network.

| | p50 (ms) | p95 (ms) | p99 (ms) | N |
|---|---|---|---|---|
| Cold (fresh TokenizerEngine per request) | 0.319 | 0.451 | 1.07 | 200 |
| Warm (engine reused across requests) | 0.157 | 0.249 | 0.309 | 1000 |

## 4. Projected Cost Model (Analytical Simulation)

**This is a simulation, not a measurement.** It models a 25-turn agent loop adding ~400 new tokens of context per turn, under four strategies, using illustrative per-1M-token prices (`_pricing_used` below) that **must be replaced with current provider pricing** before this model informs any real budget decision.

| Strategy | OpenAI (USD) | Anthropic (USD) |
|---|---|---|
| naive_loop | $0.3625 | $0.4462 |
| naive_compression | $0.2325 | $0.2902 |
| provider_caching_alone | $0.2125 | $0.1223 |
| agent_gateway | $0.1075 | $0.0971 |

Illustrative rates used (USD / 1M tokens): `{"openai": {"input": 2.5, "cached_input": 1.25, "output": 10.0}, "anthropic": {"input": 3.0, "cached_input": 0.3, "output": 15.0}}`

## 5. Phase 0 Growth Curve & Offload Hit Rate

27-turn synthetic agent loop, 3 independent runs. "Naive" resends every raw tool observation every turn; "Gateway" applies `observation_masking` with trailing K=2.

| Run | Naive tokens (final turn) | Gateway tokens (final turn) | Offload hit rate | Artifacts offloaded |
|---|---|---|---|---|
| 1 | 2,873 | 907 | 100.0% | 10 |
| 2 | 2,843 | 924 | 100.0% | 10 |
| 3 | 2,811 | 912 | 100.0% | 10 |

**Average offload hit rate across 3 runs: 100.0%** (fraction of collapsed observations whose full content was never re-read for the rest of the run).
