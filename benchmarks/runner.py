"""Benchmark runner: produces BENCHMARKS.md from real, local measurements.

Every number in the generated report comes from code in this repo
actually running against the 55-payload corpus in `benchmarks/corpus/`.
Nothing here is pre-filled to match a target -- if a measured value
misses an aspirational threshold from the design spec, the report says
so. Where a number cannot be measured locally (e.g. real provider
latency or live pricing), the report clearly labels it as an
analytical/illustrative simulation rather than an observed result.
"""

from __future__ import annotations

import json
import statistics
import sys
import time
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))

from agent_gateway.core.blackboard import Blackboard  # noqa: E402
from agent_gateway.core.cache_boundary import CacheBoundary, TokenizerEngine  # noqa: E402
from agent_gateway.core.compaction import (  # noqa: E402
    HierarchicalCompactor,
    SuffixTurn,
    extractive_fallback_summarizer,
)
from agent_gateway.core.guardrails import CircuitBreakerConfig, GuardrailChain  # noqa: E402
from agent_gateway.core.lossy_passes import (  # noqa: E402
    Aggression,
    apply_lossy_passes,
    LossyPassConfig,
)
from agent_gateway.core.observation_masking import Observation, ObservationMasker  # noqa: E402
from agent_gateway.storage.sqlite_store import SqliteStore  # noqa: E402
from agent_gateway.storage import zclaw  # noqa: E402

CORPUS_DIR = REPO_ROOT / "benchmarks" / "corpus"
OUTPUT_PATH = REPO_ROOT / "BENCHMARKS.md"


def load_corpus() -> list[dict]:
    items = []
    for path in sorted(CORPUS_DIR.glob("*.json")):
        items.append(json.loads(path.read_text()))
    return items


# ---------------------------------------------------------------------------
# 1. Round-trip proof on the storage codec
# ---------------------------------------------------------------------------


def run_round_trip_proof(corpus: list[dict]) -> dict:
    results = []
    for item in corpus:
        data = item["text"].encode("utf-8")
        r = zclaw.verify_round_trip(data)
        results.append(
            {
                "id": item["id"],
                "ok": r.ok,
                "original_bytes": r.original_len,
                "encoded_bytes": r.encoded_len,
                "detail": r.detail,
            }
        )
    n_ok = sum(1 for r in results if r["ok"])
    total_original = sum(r["original_bytes"] for r in results)
    total_encoded = sum(r["encoded_bytes"] for r in results if r["encoded_bytes"] >= 0)
    return {
        "results": results,
        "n_total": len(results),
        "n_ok": n_ok,
        "pct_ok": 100.0 * n_ok / len(results) if results else 0.0,
        "total_original_bytes": total_original,
        "total_encoded_bytes": total_encoded,
        "storage_reduction_pct": (
            100.0 * (1 - total_encoded / total_original) if total_original else 0.0
        ),
    }


# ---------------------------------------------------------------------------
# 2. Faithfulness matrix across all lossy passes + compaction
# ---------------------------------------------------------------------------


def _retention(original_facts: list[str], transformed_text: str) -> float:
    if not original_facts:
        return 1.0
    kept = sum(1 for f in original_facts if f and f in transformed_text)
    return kept / len(original_facts)


PASS_NAMES = [
    "structural_stripping",
    "eco_trimming",
    "pruning",
    "numeric_quantization",
    "query_filtering",
]
TIERS = [Aggression.LOW, Aggression.MEDIUM, Aggression.HIGH]


def run_faithfulness_matrix(corpus: list[dict]) -> dict:
    matrix: dict[str, dict] = {}

    for pass_name in PASS_NAMES:
        matrix[pass_name] = {}
        for tier in TIERS:
            config = LossyPassConfig(**{pass_name: True}, aggression=tier)
            num_retentions, date_retentions, neg_retentions, task_retentions = [], [], [], []
            for item in corpus:
                query = "status result outcome" if pass_name == "query_filtering" else ""
                out = apply_lossy_passes(item["text"], config, query=query)
                num_retentions.append(_retention(item["facts"]["numbers"], out))
                date_retentions.append(_retention(item["facts"]["dates"], out))
                neg_retentions.append(_retention(item["facts"]["negations"], out))
                if item["category"] == "agent_transcript":
                    conclusion = item["text"].split("Agent:")[-1].strip()[:40]
                    task_retentions.append(1.0 if conclusion in out else 0.0)
            matrix[pass_name][tier.name] = {
                "numbers_retained_pct": round(100 * statistics.mean(num_retentions), 1),
                "dates_retained_pct": round(100 * statistics.mean(date_retentions), 1),
                "negations_retained_pct": round(100 * statistics.mean(neg_retentions), 1),
                "task_completion_retained_pct": (
                    round(100 * statistics.mean(task_retentions), 1) if task_retentions else None
                ),
            }

    # Compaction (extractive fallback summarizer) faithfulness -- included
    # separately since it is not one of the opt-in lossy passes, but the
    # default summarizer IS lossy in practice and should be measured too.
    engine = TokenizerEngine()
    num_r, date_r, neg_r = [], [], []
    for item in corpus:
        summary = extractive_fallback_summarizer(item["text"], level=1)
        num_r.append(_retention(item["facts"]["numbers"], summary))
        date_r.append(_retention(item["facts"]["dates"], summary))
        neg_r.append(_retention(item["facts"]["negations"], summary))
    matrix["compaction_extractive_fallback"] = {
        "N/A (single tier)": {
            "numbers_retained_pct": round(100 * statistics.mean(num_r), 1),
            "dates_retained_pct": round(100 * statistics.mean(date_r), 1),
            "negations_retained_pct": round(100 * statistics.mean(neg_r), 1),
            "task_completion_retained_pct": None,
        }
    }
    return matrix


# ---------------------------------------------------------------------------
# 3. Wall-clock latency: gateway processing overhead, cold vs warm
# ---------------------------------------------------------------------------


def _pipeline_once(text: str, tokenizer_engine: TokenizerEngine, store: SqliteStore) -> None:
    masker = ObservationMasker(trailing_k=2, artifact_sink=_SinkAdapter(store))
    obs = [Observation(step=i, tool_name="t", raw_output=text[:200]) for i in range(4)]
    masker.mask(obs)

    compactor = HierarchicalCompactor(tokenizer_engine=tokenizer_engine, token_threshold=50, keep_recent_turns=1)
    turns = [SuffixTurn(index=i, text=text) for i in range(3)]
    compactor.maybe_compact("frozen prefix", turns)

    bb = Blackboard(store, "bench-conv")
    chain = GuardrailChain(blackboard=bb, circuit_breaker=CircuitBreakerConfig(max_turns=10_000))
    chain.check_all(tool_name="noop", arguments={"i": 1})
    chain.record_turn(tokens_used=10)


class _SinkAdapter:
    def __init__(self, store: SqliteStore) -> None:
        self.store = store

    def put(self, ref: str, content: str) -> None:
        self.store.put_artifact(ref, content.encode("utf-8"))


def run_latency_benchmark(corpus: list[dict], n_requests: int = 1000) -> dict:
    sample_texts = [item["text"] for item in corpus]

    # Warm: engine + store created once, reused across all requests.
    warm_engine = TokenizerEngine()
    warm_store = SqliteStore(":memory:")
    warm_times = []
    for i in range(n_requests):
        text = sample_texts[i % len(sample_texts)]
        t0 = time.perf_counter()
        _pipeline_once(text, warm_engine, warm_store)
        warm_times.append((time.perf_counter() - t0) * 1000)

    # Cold: fresh TokenizerEngine (reloads tiktoken + claude-bpe vocab from
    # disk) and a fresh in-memory store on every single request.
    cold_n = min(n_requests, 200)  # cold path is expensive; bounded for CI time
    cold_times = []
    for i in range(cold_n):
        text = sample_texts[i % len(sample_texts)]
        t0 = time.perf_counter()
        cold_engine = TokenizerEngine()
        cold_store = SqliteStore(":memory:")
        _pipeline_once(text, cold_engine, cold_store)
        cold_times.append((time.perf_counter() - t0) * 1000)

    def pctl(values: list[float], p: float) -> float:
        return round(statistics.quantiles(values, n=100)[int(p) - 1], 3) if len(values) >= 100 else round(sorted(values)[int(len(values) * p / 100)], 3)

    return {
        "n_requests_warm": n_requests,
        "n_requests_cold": cold_n,
        "warm_p50_ms": pctl(warm_times, 50),
        "warm_p95_ms": pctl(warm_times, 95),
        "warm_p99_ms": pctl(warm_times, 99),
        "cold_p50_ms": pctl(cold_times, 50),
        "cold_p95_ms": pctl(cold_times, 95),
        "cold_p99_ms": pctl(cold_times, 99),
        "note": (
            "Measures gateway-side processing overhead only (masking + "
            "compaction + guardrail checks), not upstream provider "
            "response time -- this benchmark does not call OpenAI or "
            "Anthropic over the network."
        ),
    }


# ---------------------------------------------------------------------------
# 4. Projected cost model (explicitly an analytical simulation)
# ---------------------------------------------------------------------------

# Illustrative example rates only -- these are placeholders for the shape
# of the model, NOT a current price quote. Anyone using this model for a
# real budgeting decision must replace them with current provider pricing
# pages before trusting the dollar figures.
ILLUSTRATIVE_PRICING_USD_PER_1M_TOKENS = {
    "openai": {"input": 2.50, "cached_input": 1.25, "output": 10.00},
    "anthropic": {"input": 3.00, "cached_input": 0.30, "output": 15.00},
}


@dataclass
class LoopShape:
    n_turns: int = 25
    avg_new_tokens_per_turn: int = 400
    avg_output_tokens_per_turn: int = 150
    # Token count at which agent_gateway's compaction (compaction.py) fires
    # and rewrites the suffix, capping how large the cacheable context is
    # allowed to grow. Provider caching alone has no such mechanism -- its
    # cached context grows for the life of the loop.
    compaction_ceiling_tokens: int = 2000


def simulate_cost(provider: str, loop: LoopShape, strategy: str) -> float:
    pricing = ILLUSTRATIVE_PRICING_USD_PER_1M_TOKENS[provider]
    total_cost = 0.0
    running_context = 0

    for turn in range(loop.n_turns):
        running_context += loop.avg_new_tokens_per_turn

        if strategy == "naive_loop":
            # No caching, no compaction: the full growing transcript is
            # sent, and billed, at full input price every turn.
            input_tokens = running_context
            cached_tokens = 0
        elif strategy == "naive_compression":
            # A generic lossy shrink applied every turn, but still with no
            # caching -- shrinks the constant, not the growth curve.
            input_tokens = int(running_context * 0.6)
            cached_tokens = 0
        elif strategy == "provider_caching_alone":
            # Everything except this turn's new content is an exact-prefix
            # cache hit -- a real discount, but the cached portion itself
            # still grows without bound for the life of the loop, and
            # nothing here stops the transcript from eventually exceeding
            # the model's context window.
            input_tokens = loop.avg_new_tokens_per_turn
            cached_tokens = max(0, running_context - loop.avg_new_tokens_per_turn)
        elif strategy == "agent_gateway":
            # Same per-turn new-content cost as pure caching (new content
            # is never free), but the cacheable context is capped by
            # compaction (compaction.py) instead of growing linearly --
            # this is what breaks O(N^2), not the caching itself.
            effective_context = min(running_context, loop.compaction_ceiling_tokens)
            input_tokens = loop.avg_new_tokens_per_turn
            cached_tokens = max(0, effective_context - loop.avg_new_tokens_per_turn)
        else:
            raise ValueError(strategy)

        total_cost += input_tokens / 1_000_000 * pricing["input"]
        total_cost += cached_tokens / 1_000_000 * pricing["cached_input"]
        total_cost += loop.avg_output_tokens_per_turn / 1_000_000 * pricing["output"]

    return total_cost


def run_cost_model() -> dict:
    loop = LoopShape()
    strategies = ["naive_loop", "naive_compression", "provider_caching_alone", "agent_gateway"]
    out = {}
    for provider in ("openai", "anthropic"):
        out[provider] = {s: round(simulate_cost(provider, loop, s), 4) for s in strategies}
    out["_loop_shape"] = loop.__dict__
    out["_pricing_used"] = ILLUSTRATIVE_PRICING_USD_PER_1M_TOKENS
    return out


# ---------------------------------------------------------------------------
# 5. Phase 0 growth curve + offload hit rate
# ---------------------------------------------------------------------------


def run_growth_curve(corpus: list[dict], n_runs: int = 3, n_turns: int = 27) -> dict:
    engine = TokenizerEngine()
    transcripts = [item["text"] for item in corpus if item["category"] == "agent_transcript"]
    if not transcripts:
        transcripts = [item["text"] for item in corpus]

    runs = []
    for run_idx in range(n_runs):
        store = SqliteStore(":memory:")
        masker = ObservationMasker(trailing_k=2, artifact_sink=_SinkAdapter(store))
        naive_tokens_per_turn = []
        gateway_tokens_per_turn = []
        observations: list[Observation] = []

        for turn in range(n_turns):
            text = transcripts[(run_idx * n_turns + turn) % len(transcripts)]
            observations.append(Observation(step=turn, tool_name="t", raw_output=text))

            naive_text = "\n".join(o.raw_output for o in observations)
            naive_tokens_per_turn.append(engine.count(naive_text, "cl100k_base"))

            masked = masker.mask(observations)
            gateway_text = "\n".join(m.text for m in masked)
            gateway_tokens_per_turn.append(engine.count(gateway_text, "cl100k_base"))

        stats = store.artifact_stats()
        runs.append(
            {
                "run": run_idx + 1,
                "naive_tokens_final_turn": naive_tokens_per_turn[-1],
                "gateway_tokens_final_turn": gateway_tokens_per_turn[-1],
                "naive_tokens_per_turn": naive_tokens_per_turn,
                "gateway_tokens_per_turn": gateway_tokens_per_turn,
                "offload_hit_rate": stats["offload_hit_rate"],
                "total_artifacts_offloaded": stats["total_artifacts"],
            }
        )

    avg_hit_rate = statistics.mean(r["offload_hit_rate"] for r in runs)
    return {"runs": runs, "n_turns": n_turns, "average_offload_hit_rate": avg_hit_rate}


# ---------------------------------------------------------------------------
# Report rendering
# ---------------------------------------------------------------------------


def render_markdown(
    round_trip: dict,
    faithfulness: dict,
    latency: dict,
    cost_model: dict,
    growth: dict,
) -> str:
    lines = []
    lines.append("# Agent Gateway -- Benchmark Report")
    lines.append("")
    lines.append(
        "All figures below are produced by `benchmarks/runner.py` running "
        "against the 55-payload corpus in `benchmarks/corpus/` on this "
        "machine. Token counts use named tokenizers only "
        "(`cl100k_base` via `tiktoken`, `claude-bpe` via a locally bundled "
        "BPE vocab) -- never a character-count proxy. Where a figure is a "
        "simulation rather than an observed measurement, it is labeled as "
        "such."
    )
    lines.append("")

    lines.append("## 1. Storage Codec Round-Trip Proof")
    lines.append("")
    lines.append(
        f"**{round_trip['n_ok']}/{round_trip['n_total']} payloads "
        f"({round_trip['pct_ok']:.1f}%) round-tripped byte-identical** "
        "through `storage/zclaw.py` (encode then decode, compared byte-for-byte)."
    )
    lines.append("")
    lines.append(
        f"- Total original bytes: {round_trip['total_original_bytes']:,}\n"
        f"- Total encoded bytes: {round_trip['total_encoded_bytes']:,}\n"
        f"- Storage-layer byte reduction: {round_trip['storage_reduction_pct']:.1f}% "
        "(secondary storage metric -- not a token count, see Sec 1 of the spec)"
    )
    failures = [r for r in round_trip["results"] if not r["ok"]]
    if failures:
        lines.append("")
        lines.append("**Failures:**")
        for f in failures:
            lines.append(f"- `{f['id']}`: {f['detail']}")
    lines.append("")

    lines.append("## 2. Faithfulness Matrix (Lossy Passes)")
    lines.append("")
    lines.append(
        "Measures the fraction of ground-truth numbers/dates/negation-phrases "
        "(extracted from each corpus payload at authoring time) that are "
        "still present verbatim after each pass. All these passes default "
        "OFF; this table exists so an operator who opts in knows exactly "
        "what they are trading away."
    )
    lines.append("")
    lines.append("| Pass | Tier | Numbers Retained | Dates Retained | Negations Retained | Task Completion Retained |")
    lines.append("|---|---|---|---|---|---|")
    for pass_name, tiers in faithfulness.items():
        for tier_name, m in tiers.items():
            tc = f"{m['task_completion_retained_pct']:.1f}%" if m["task_completion_retained_pct"] is not None else "n/a"
            lines.append(
                f"| {pass_name} | {tier_name} | {m['numbers_retained_pct']:.1f}% | "
                f"{m['dates_retained_pct']:.1f}% | {m['negations_retained_pct']:.1f}% | {tc} |"
            )
    lines.append("")

    lines.append("## 3. Wall-Clock Latency (Gateway Processing Overhead)")
    lines.append("")
    lines.append(latency["note"])
    lines.append("")
    lines.append("| | p50 (ms) | p95 (ms) | p99 (ms) | N |")
    lines.append("|---|---|---|---|---|")
    lines.append(
        f"| Cold (fresh TokenizerEngine per request) | {latency['cold_p50_ms']} | "
        f"{latency['cold_p95_ms']} | {latency['cold_p99_ms']} | {latency['n_requests_cold']} |"
    )
    lines.append(
        f"| Warm (engine reused across requests) | {latency['warm_p50_ms']} | "
        f"{latency['warm_p95_ms']} | {latency['warm_p99_ms']} | {latency['n_requests_warm']} |"
    )
    lines.append("")

    lines.append("## 4. Projected Cost Model (Analytical Simulation)")
    lines.append("")
    lines.append(
        "**This is a simulation, not a measurement.** It models a "
        f"{cost_model['_loop_shape']['n_turns']}-turn agent loop adding "
        f"~{cost_model['_loop_shape']['avg_new_tokens_per_turn']} new tokens "
        "of context per turn, under four strategies, using illustrative "
        "per-1M-token prices (`_pricing_used` below) that **must be replaced "
        "with current provider pricing** before this model informs any real "
        "budget decision."
    )
    lines.append("")
    lines.append("| Strategy | OpenAI (USD) | Anthropic (USD) |")
    lines.append("|---|---|---|")
    for strategy in ["naive_loop", "naive_compression", "provider_caching_alone", "agent_gateway"]:
        lines.append(f"| {strategy} | ${cost_model['openai'][strategy]:.4f} | ${cost_model['anthropic'][strategy]:.4f} |")
    lines.append("")
    lines.append(f"Illustrative rates used (USD / 1M tokens): `{json.dumps(cost_model['_pricing_used'])}`")
    lines.append("")

    lines.append("## 5. Phase 0 Growth Curve & Offload Hit Rate")
    lines.append("")
    lines.append(
        f"{growth['n_turns']}-turn synthetic agent loop, {len(growth['runs'])} independent runs. "
        "\"Naive\" resends every raw tool observation every turn; \"Gateway\" applies "
        "`observation_masking` with trailing K=2."
    )
    lines.append("")
    lines.append("| Run | Naive tokens (final turn) | Gateway tokens (final turn) | Offload hit rate | Artifacts offloaded |")
    lines.append("|---|---|---|---|---|")
    for r in growth["runs"]:
        lines.append(
            f"| {r['run']} | {r['naive_tokens_final_turn']:,} | {r['gateway_tokens_final_turn']:,} | "
            f"{100 * r['offload_hit_rate']:.1f}% | {r['total_artifacts_offloaded']} |"
        )
    lines.append("")
    lines.append(
        f"**Average offload hit rate across {len(growth['runs'])} runs: "
        f"{100 * growth['average_offload_hit_rate']:.1f}%** "
        "(fraction of collapsed observations whose full content was never "
        "re-read for the rest of the run)."
    )
    lines.append("")

    return "\n".join(lines)


def main() -> None:
    print("Loading corpus...")
    corpus = load_corpus()
    assert len(corpus) == 55, f"expected 55 corpus payloads, found {len(corpus)}"

    print("Running round-trip proof...")
    round_trip = run_round_trip_proof(corpus)

    print("Running faithfulness matrix...")
    faithfulness = run_faithfulness_matrix(corpus)

    print("Running latency benchmark (this takes a little while)...")
    latency = run_latency_benchmark(corpus, n_requests=1000)

    print("Running cost model simulation...")
    cost_model = run_cost_model()

    print("Running growth curve...")
    growth = run_growth_curve(corpus)

    report = render_markdown(round_trip, faithfulness, latency, cost_model, growth)
    OUTPUT_PATH.write_text(report)
    print(f"Wrote {OUTPUT_PATH}")

    raw_results_path = REPO_ROOT / "benchmarks" / "raw_results.json"
    raw_results_path.write_text(
        json.dumps(
            {
                "round_trip": round_trip,
                "faithfulness": faithfulness,
                "latency": latency,
                "cost_model": cost_model,
                "growth": growth,
            },
            indent=2,
            default=str,
        )
    )
    print(f"Wrote {raw_results_path}")


if __name__ == "__main__":
    main()
