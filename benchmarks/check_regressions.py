"""CI regression guardrail: cache-boundary immutability + latency ceiling.

Deliberately separate from `runner.py` (which produces the full
BENCHMARKS.md report): this script is meant to run on every PR and fail
fast, so its thresholds are generous ceilings meant to catch gross
regressions on slow/noisy CI runners, not tight baselines. Tightening
these into a true baseline-diff would require persisting prior results
as a build artifact -- worth doing later, out of scope for the initial
guardrail.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))

from agent_gateway.core.cache_boundary import (  # noqa: E402
    CacheBoundary,
    CachePrefixMutationError,
)
from agent_gateway.storage import zclaw  # noqa: E402
from benchmarks.runner import (  # noqa: E402
    load_corpus,
    run_latency_benchmark,
    run_round_trip_proof,
)

# Generous ceilings for gateway-side processing latency (ms). This is
# in-process overhead only, not upstream provider latency.
WARM_P99_CEILING_MS = 50.0
COLD_P99_CEILING_MS = 200.0


def check_boundary_immutability() -> list[str]:
    failures = []
    boundary = CacheBoundary(prefix_text="system prompt + tool schemas")
    try:
        boundary.verify("system prompt + tool schemas")
    except CachePrefixMutationError as exc:
        failures.append(f"Identical prefix incorrectly flagged as mutated: {exc}")

    try:
        boundary.verify("system prompt + tool schemas ")  # trailing space
        failures.append("Mutated prefix (trailing space) was NOT detected -- boundary check is broken.")
    except CachePrefixMutationError:
        pass  # expected

    return failures


def check_round_trip(corpus: list[dict]) -> list[str]:
    result = run_round_trip_proof(corpus)
    if result["pct_ok"] < 100.0:
        failed_ids = [r["id"] for r in result["results"] if not r["ok"]]
        return [f"Storage codec round-trip is not 100% ({result['pct_ok']:.1f}%): failed on {failed_ids}"]
    return []


def check_latency(corpus: list[dict]) -> list[str]:
    latency = run_latency_benchmark(corpus, n_requests=300)
    failures = []
    if latency["warm_p99_ms"] > WARM_P99_CEILING_MS:
        failures.append(
            f"Warm p99 latency {latency['warm_p99_ms']}ms exceeds ceiling {WARM_P99_CEILING_MS}ms"
        )
    if latency["cold_p99_ms"] > COLD_P99_CEILING_MS:
        failures.append(
            f"Cold p99 latency {latency['cold_p99_ms']}ms exceeds ceiling {COLD_P99_CEILING_MS}ms"
        )
    print(f"Measured: warm_p99={latency['warm_p99_ms']}ms, cold_p99={latency['cold_p99_ms']}ms")
    return failures


def main() -> None:
    corpus = load_corpus()
    all_failures: list[str] = []
    all_failures += check_boundary_immutability()
    all_failures += check_round_trip(corpus)
    all_failures += check_latency(corpus)

    if all_failures:
        print("REGRESSION GUARDRAIL FAILED:")
        for f in all_failures:
            print(f"  - {f}")
        sys.exit(1)

    print("Regression guardrail passed: boundary immutability, round-trip, and latency ceilings all OK.")


if __name__ == "__main__":
    main()
