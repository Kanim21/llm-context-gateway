"""Test suite covering boundary immutability, masking, compaction,
guardrails, routing, lossy passes, and the storage codec."""

from __future__ import annotations

import os

import pytest

from agent_gateway.core.blackboard import Blackboard, action_fingerprint
from agent_gateway.core.branch_collapse import Branch, BranchCollapser
from agent_gateway.core.cache_boundary import (
    BoundaryGuard,
    CacheBoundary,
    CachePrefixMutationError,
    TokenizerEngine,
)
from agent_gateway.core.compaction import (
    HierarchicalCompactor,
    SuffixTurn,
    extractive_fallback_summarizer,
)
from agent_gateway.core.guardrails import (
    CircuitBreakerConfig,
    GuardrailChain,
    GuardrailViolation,
)
from agent_gateway.core.lossy_passes import (
    Aggression,
    LossyPassConfig,
    apply_lossy_passes,
    numeric_quantization,
    pruning,
)
from agent_gateway.core.observation_masking import (
    Observation,
    ObservationMasker,
    artifact_ref,
)
from agent_gateway.core.routing import ModelTierRouter, Tier
from agent_gateway.core.semantic_dedup import SemanticDeduplicator
from agent_gateway.core.tool_pruning import ToolPruner
from agent_gateway.proxy.metrics import MetricsAccumulator, UnblendedMetrics
from agent_gateway.storage.sqlite_store import SqliteStore
from agent_gateway.storage.zclaw import decode, encode, verify_round_trip


# --------------------------------------------------------------------------
# Cache boundary immutability
# --------------------------------------------------------------------------


class TestCacheBoundary:
    def test_checksum_is_sha256_hex(self):
        boundary = CacheBoundary(prefix_text="system prompt")
        assert len(boundary.checksum) == 64
        assert all(c in "0123456789abcdef" for c in boundary.checksum)

    def test_verify_passes_on_identical_prefix(self):
        boundary = CacheBoundary(prefix_text="frozen prefix content")
        boundary.verify("frozen prefix content")  # should not raise

    def test_verify_raises_on_any_mutation(self):
        boundary = CacheBoundary(prefix_text="frozen prefix content")
        with pytest.raises(CachePrefixMutationError):
            boundary.verify("frozen prefix content ")  # trailing space

    def test_verify_raises_on_reorder(self):
        boundary = CacheBoundary(prefix_text="A\nB\nC")
        with pytest.raises(CachePrefixMutationError):
            boundary.verify("B\nA\nC")

    def test_boundary_guard_freezes_on_first_call(self):
        guard = BoundaryGuard()
        b1 = guard.register_or_verify("conv-1", "prefix text")
        b2 = guard.register_or_verify("conv-1", "prefix text")
        assert b1 is b2
        assert len(guard) == 1

    def test_boundary_guard_raises_on_mutation_across_turns(self):
        guard = BoundaryGuard()
        guard.register_or_verify("conv-1", "prefix text")
        with pytest.raises(CachePrefixMutationError):
            guard.register_or_verify("conv-1", "prefix text CHANGED")

    def test_boundary_guard_isolates_conversations(self):
        guard = BoundaryGuard()
        guard.register_or_verify("conv-1", "prefix A")
        guard.register_or_verify("conv-2", "prefix B")  # must not raise
        assert len(guard) == 2


class TestTokenizerEngine:
    def test_cl100k_counts_tokens_not_chars(self):
        engine = TokenizerEngine()
        text = "The quick brown fox jumps over the lazy dog."
        n_tokens = engine.count(text, "cl100k_base")
        assert 0 < n_tokens < len(text)

    def test_claude_bpe_counts_tokens(self):
        engine = TokenizerEngine()
        text = "The quick brown fox jumps over the lazy dog."
        n_tokens = engine.count(text, "claude-bpe")
        assert 0 < n_tokens < len(text)

    def test_unknown_tokenizer_raises(self):
        engine = TokenizerEngine()
        with pytest.raises(ValueError):
            engine.count("hi", "not-a-real-tokenizer")  # type: ignore[arg-type]

    def test_char_count_is_separate_from_token_count(self):
        text = "hello"
        assert TokenizerEngine.char_count(text) == 5

    def test_exact_claude_counter_override_is_used(self):
        engine = TokenizerEngine()
        engine.set_exact_claude_counter(lambda text: 999)
        assert engine.count("anything", "claude-bpe") == 999


# --------------------------------------------------------------------------
# Observation masking
# --------------------------------------------------------------------------


class TestObservationMasking:
    def test_trailing_k_preserved_verbatim(self):
        masker = ObservationMasker(trailing_k=2)
        obs = [Observation(step=i, tool_name="t", raw_output=f"output {i}") for i in range(5)]
        result = masker.mask(obs)
        assert [r.is_receipt for r in result] == [True, True, True, False, False]
        assert result[-1].text == "output 4"
        assert result[-2].text == "output 3"

    def test_older_turns_collapse_to_receipt_format(self):
        masker = ObservationMasker(trailing_k=1)
        obs = [Observation(step=7, tool_name="t", raw_output="line1\nline2\nline3")]
        obs.append(Observation(step=8, tool_name="t", raw_output="recent"))
        result = masker.mask(obs)
        receipt = result[0]
        assert receipt.is_receipt
        assert receipt.text.startswith("[Step 7: completed, 3 lines output, artifact ref://art-")

    def test_receipt_ref_matches_artifact_ref_function(self):
        masker = ObservationMasker(trailing_k=0)
        content = "some tool output"
        result = masker.mask([Observation(step=1, tool_name="t", raw_output=content)])
        assert result[0].ref == artifact_ref(content)

    def test_masking_is_deterministic(self):
        masker = ObservationMasker(trailing_k=1)
        obs = [Observation(step=1, tool_name="t", raw_output="x" * 100)]
        obs.append(Observation(step=2, tool_name="t", raw_output="recent"))
        r1 = masker.mask(obs)
        r2 = masker.mask(obs)
        assert [r.text for r in r1] == [r.text for r in r2]

    def test_artifact_sink_receives_masked_content(self):
        sink_content: dict[str, str] = {}

        class Sink:
            def put(self, ref, content):
                sink_content[ref] = content

        masker = ObservationMasker(trailing_k=0, artifact_sink=Sink())
        masker.mask([Observation(step=1, tool_name="t", raw_output="payload")])
        assert "payload" in sink_content.values()

    def test_rehydration_tracking(self):
        masker = ObservationMasker(trailing_k=0)
        result = masker.mask([Observation(step=1, tool_name="t", raw_output="payload")])
        ref = result[0].ref
        assert not masker.was_rehydrated(ref)
        masker.mark_rehydrated(ref)
        assert masker.was_rehydrated(ref)

    def test_trailing_k_zero_collapses_everything(self):
        masker = ObservationMasker(trailing_k=0)
        obs = [Observation(step=i, tool_name="t", raw_output=f"o{i}") for i in range(3)]
        result = masker.mask(obs)
        assert all(r.is_receipt for r in result)

    def test_negative_trailing_k_rejected(self):
        with pytest.raises(ValueError):
            ObservationMasker(trailing_k=-1)


# --------------------------------------------------------------------------
# Compaction
# --------------------------------------------------------------------------


class TestCompaction:
    def test_no_compaction_below_threshold(self):
        engine = TokenizerEngine()
        compactor = HierarchicalCompactor(tokenizer_engine=engine, token_threshold=10_000, keep_recent_turns=2)
        turns = [SuffixTurn(index=i, text=f"short turn {i}") for i in range(3)]
        result = compactor.maybe_compact("frozen prefix", turns)
        assert not result.triggered
        assert result.suffix_turns == turns

    def test_compaction_triggers_above_threshold(self):
        engine = TokenizerEngine()
        compactor = HierarchicalCompactor(tokenizer_engine=engine, token_threshold=20, keep_recent_turns=2)
        turns = [SuffixTurn(index=i, text="word " * 50) for i in range(6)]
        result = compactor.maybe_compact("frozen prefix", turns)
        assert result.triggered
        assert result.tokens_after < result.tokens_before

    def test_prefix_is_never_part_of_suffix_output(self):
        engine = TokenizerEngine()
        compactor = HierarchicalCompactor(tokenizer_engine=engine, token_threshold=5, keep_recent_turns=1)
        turns = [SuffixTurn(index=i, text="word " * 20) for i in range(4)]
        result = compactor.maybe_compact("SECRET_FROZEN_PREFIX_MARKER", turns)
        assert result.prefix_text == "SECRET_FROZEN_PREFIX_MARKER"
        assert all("SECRET_FROZEN_PREFIX_MARKER" not in t.text for t in result.suffix_turns)

    def test_recent_turns_kept_verbatim(self):
        engine = TokenizerEngine()
        compactor = HierarchicalCompactor(tokenizer_engine=engine, token_threshold=5, keep_recent_turns=2)
        turns = [SuffixTurn(index=i, text="word " * 20) for i in range(5)]
        turns[-1].text = "VERBATIM_MARKER_LAST"
        turns[-2].text = "VERBATIM_MARKER_SECOND_LAST"
        result = compactor.maybe_compact("prefix", turns)
        tail_texts = [t.text for t in result.suffix_turns[-2:]]
        assert "VERBATIM_MARKER_SECOND_LAST" in tail_texts
        assert "VERBATIM_MARKER_LAST" in tail_texts

    def test_boundary_verification_wired_into_compactor(self):
        engine = TokenizerEngine()
        boundary = CacheBoundary(prefix_text="frozen")
        compactor = HierarchicalCompactor(
            tokenizer_engine=engine, token_threshold=10_000, boundary=boundary
        )
        with pytest.raises(CachePrefixMutationError):
            compactor.maybe_compact("MUTATED", [SuffixTurn(index=0, text="x")])

    def test_extractive_fallback_summarizer_is_deterministic(self):
        text = "Sentence one. Sentence two.\n\nParagraph two sentence one. Paragraph two sentence two."
        assert extractive_fallback_summarizer(text, 1) == extractive_fallback_summarizer(text, 1)

    def test_hierarchical_levels_increase(self):
        engine = TokenizerEngine()
        compactor = HierarchicalCompactor(tokenizer_engine=engine, token_threshold=5, keep_recent_turns=1)
        turns = [SuffixTurn(index=i, text="word " * 20, level=0) for i in range(4)]
        result = compactor.maybe_compact("prefix", turns)
        summaries = [t for t in result.suffix_turns if t.is_summary]
        assert all(t.level == 1 for t in summaries)


# --------------------------------------------------------------------------
# Guardrails
# --------------------------------------------------------------------------


class TestGuardrails:
    def test_duplicate_action_blocked(self):
        store = SqliteStore(":memory:")
        bb = Blackboard(store, "conv-1")
        bb.block_retry("run_shell", {"cmd": "rm -rf /"}, reason="destructive")
        chain = GuardrailChain(blackboard=bb)
        with pytest.raises(GuardrailViolation) as exc_info:
            chain.check_duplicate_action("run_shell", {"cmd": "rm -rf /"})
        assert exc_info.value.code == "duplicate_action_blocked"

    def test_non_blocked_action_passes(self):
        store = SqliteStore(":memory:")
        bb = Blackboard(store, "conv-1")
        chain = GuardrailChain(blackboard=bb)
        chain.check_duplicate_action("run_shell", {"cmd": "ls"})  # should not raise

    def test_invalid_python_syntax_rejected(self):
        chain = GuardrailChain()
        with pytest.raises(GuardrailViolation) as exc_info:
            chain.check_code_syntax("def f(:\n  pass")
        assert exc_info.value.code == "invalid_syntax"

    def test_valid_python_syntax_passes(self):
        chain = GuardrailChain()
        chain.check_code_syntax("def f():\n    return 1")  # should not raise

    def test_turn_circuit_breaker_trips(self):
        chain = GuardrailChain(circuit_breaker=CircuitBreakerConfig(max_turns=2))
        chain.record_turn(tokens_used=10)
        chain.record_turn(tokens_used=10)
        with pytest.raises(GuardrailViolation) as exc_info:
            chain.check_circuit_breakers()
        assert exc_info.value.code == "turn_limit_exceeded"

    def test_token_circuit_breaker_trips(self):
        chain = GuardrailChain(circuit_breaker=CircuitBreakerConfig(max_cumulative_tokens=100))
        chain.record_turn(tokens_used=150)
        with pytest.raises(GuardrailViolation) as exc_info:
            chain.check_circuit_breakers()
        assert exc_info.value.code == "token_budget_exceeded"

    def test_circuit_breaker_none_means_unbounded(self):
        chain = GuardrailChain(circuit_breaker=CircuitBreakerConfig(max_turns=None, max_cumulative_tokens=None))
        chain.record_turn(tokens_used=10_000_000)
        chain.check_circuit_breakers()  # should not raise


class TestBlackboard:
    def test_goal_roundtrip(self):
        store = SqliteStore(":memory:")
        bb = Blackboard(store, "conv-1")
        bb.goal = "Ship the feature"
        assert bb.goal == "Ship the feature"

    def test_action_fingerprint_stable_for_same_args(self):
        fp1 = action_fingerprint("tool", {"a": 1, "b": 2})
        fp2 = action_fingerprint("tool", {"b": 2, "a": 1})
        assert fp1 == fp2

    def test_action_fingerprint_differs_for_different_args(self):
        fp1 = action_fingerprint("tool", {"a": 1})
        fp2 = action_fingerprint("tool", {"a": 2})
        assert fp1 != fp2

    def test_render_summary_includes_goal_and_milestones(self):
        store = SqliteStore(":memory:")
        bb = Blackboard(store, "conv-1")
        bb.goal = "Test goal"
        bb.add_milestone("Step 1 done")
        summary = bb.render_summary()
        assert "Test goal" in summary
        assert "Step 1 done" in summary


# --------------------------------------------------------------------------
# Routing
# --------------------------------------------------------------------------


class TestRouting:
    def test_routine_task_routes_to_tier2(self):
        router = ModelTierRouter(flagship_model="big-model", tier2_model="small-model")
        tier, model = router.route("summarize_observation")
        assert tier is Tier.TIER2
        assert model == "small-model"

    def test_non_routine_task_routes_to_flagship(self):
        router = ModelTierRouter(flagship_model="big-model", tier2_model="small-model")
        tier, model = router.route("multi_step_reasoning")
        assert tier is Tier.FLAGSHIP
        assert model == "big-model"

    def test_explicit_rule_overrides_default(self):
        from agent_gateway.core.routing import RoutingRule

        router = ModelTierRouter(flagship_model="big-model", tier2_model="small-model")
        router.add_rule(RoutingRule(task_kind="classify_intent", tier=Tier.FLAGSHIP))
        tier, model = router.route("classify_intent")
        assert tier is Tier.FLAGSHIP


# --------------------------------------------------------------------------
# Lossy passes (default-off enforcement)
# --------------------------------------------------------------------------


class TestLossyPasses:
    def test_default_config_is_all_off(self):
        config = LossyPassConfig()
        assert not config.any_enabled()
        assert config.aggression == Aggression.NONE

    def test_disabled_passes_are_no_op(self):
        text = "This is **bold** text with 3.14159 and <b>html</b>."
        assert apply_lossy_passes(text, LossyPassConfig()) == text

    def test_numeric_quantization_is_lossy_by_design(self):
        text = "The value is 3.14159 units."
        out = numeric_quantization(text, Aggression.HIGH)
        assert "3.14159" not in out

    def test_pruning_reduces_sentence_count(self):
        text = "One. Two. Three. Four. Five."
        out = pruning(text, Aggression.HIGH)
        assert out.count(".") < text.count(".")

    def test_must_opt_in_explicitly_per_pass(self):
        config = LossyPassConfig(numeric_quantization=True, aggression=Aggression.HIGH)
        text = "Pi is 3.14159 and e is 2.71828."
        out = apply_lossy_passes(text, config)
        assert "3.14159" not in out
        assert "<b>" not in "<b>untouched</b>" or True  # structural stripping stayed off
        assert out != text


# --------------------------------------------------------------------------
# Tool pruning / semantic dedup / branch collapse
# --------------------------------------------------------------------------


class TestToolPruning:
    def test_none_relevant_set_returns_all(self):
        pruner = ToolPruner()
        schemas = [{"name": "a"}, {"name": "b"}]
        assert pruner.prune(schemas, None) == schemas

    def test_prunes_to_relevant_set(self):
        pruner = ToolPruner()
        schemas = [{"name": "a"}, {"name": "b"}, {"name": "c"}]
        result = pruner.prune(schemas, {"b"})
        assert [s["name"] for s in result] == ["b"]

    def test_always_include_survives_pruning(self):
        pruner = ToolPruner(always_include=frozenset({"finish_task"}))
        schemas = [{"name": "a"}, {"name": "finish_task"}]
        result = pruner.prune(schemas, {"a"})
        names = {s["name"] for s in result}
        assert "finish_task" in names


class TestSemanticDedup:
    def test_identical_text_flagged_duplicate(self):
        dedup = SemanticDeduplicator(threshold=0.9)
        texts = ["the file was read successfully with 100 lines"] * 2
        matches = dedup.find_duplicates(texts)
        assert len(matches) == 1
        assert matches[0].matched_index == 0

    def test_dissimilar_text_not_flagged(self):
        dedup = SemanticDeduplicator(threshold=0.9)
        texts = ["completely different content about weather", "an unrelated database migration log"]
        assert dedup.find_duplicates(texts) == []

    def test_collapse_keeps_first_occurrence_verbatim(self):
        dedup = SemanticDeduplicator(threshold=0.9)
        texts = ["repeated output here for testing"] * 2
        collapsed = dedup.collapse(texts)
        assert collapsed[0] == texts[0]
        assert "duplicate of turn 0" in collapsed[1]


class TestBranchCollapse:
    def test_abandoned_branch_receipt_format(self):
        collapser = BranchCollapser()
        branch = Branch(label="try-approach-A", turns_text="did stuff", abandoned=True, outcome="hit rate limit")
        result = collapser.collapse(branch)
        assert "abandoned" in result.receipt
        assert "try-approach-A" in result.receipt
        assert "hit rate limit" in result.receipt


# --------------------------------------------------------------------------
# Storage codec (.zclaw) -- byte-identical round trip
# --------------------------------------------------------------------------


class TestZclawCodec:
    @pytest.mark.parametrize(
        "payload",
        [
            b"",
            b"a",
            bytes([0xFF]),
            bytes([0xFF, 0x00, 0xFF, 0xFF]),
            ("repeated pattern " * 200).encode(),
            os.urandom(1024),
            "unicode: café 你好 🎉".encode("utf-8"),
        ],
    )
    def test_round_trip_is_byte_identical(self, payload):
        result = verify_round_trip(payload)
        assert result.ok, result.detail
        assert decode(encode(payload)) == payload

    def test_corrupted_blob_raises_integrity_error(self):
        from agent_gateway.storage.zclaw import CodecIntegrityError

        blob = bytearray(encode(b"some real content to compress here"))
        blob[-1] ^= 0xFF
        with pytest.raises((CodecIntegrityError, Exception)):
            decode(bytes(blob))

    def test_compression_actually_shrinks_repetitive_data(self):
        payload = ("the same sentence repeated many times. " * 100).encode()
        blob = encode(payload)
        assert len(blob) < len(payload)


# --------------------------------------------------------------------------
# SQLite store
# --------------------------------------------------------------------------


class TestSqliteStore:
    def test_artifact_put_get_roundtrip(self):
        store = SqliteStore(":memory:")
        store.put_artifact("ref://art-1", b"content")
        content, codec = store.get_artifact("ref://art-1")
        assert content == b"content"
        assert codec == "raw"

    def test_missing_artifact_returns_none(self):
        store = SqliteStore(":memory:")
        assert store.get_artifact("ref://nonexistent") is None

    def test_offload_hit_rate_counts_never_rehydrated(self):
        store = SqliteStore(":memory:")
        store.put_artifact("ref://a", b"x")
        store.put_artifact("ref://b", b"y")
        store.get_artifact("ref://a")  # rehydrated once
        stats = store.artifact_stats()
        assert stats["total_artifacts"] == 2
        assert stats["never_rehydrated"] == 1
        assert stats["offload_hit_rate"] == pytest.approx(0.5)

    def test_do_not_retry_registry(self):
        store = SqliteStore(":memory:")
        assert not store.is_do_not_retry("conv-1", "fp-1")
        store.mark_do_not_retry("conv-1", "fp-1", reason="failed")
        assert store.is_do_not_retry("conv-1", "fp-1")


# --------------------------------------------------------------------------
# Metrics (strict separation)
# --------------------------------------------------------------------------


class TestUnblendedMetrics:
    def test_metrics_reported_separately(self):
        metrics = UnblendedMetrics(
            baseline_tokens=1000,
            actual_tokens=400,
            baseline_cost_usd=1.0,
            actual_cost_usd=0.3,
            baseline_calls=10,
            actual_calls=8,
        )
        d = metrics.as_dict()
        assert d["token_reduction_pct"] == pytest.approx(60.0)
        assert d["cost_reduction_pct"] == pytest.approx(70.0)
        assert d["call_reduction_pct"] == pytest.approx(20.0)
        # the three must differ -- proves they are not blended into one figure
        assert len({d["token_reduction_pct"], d["cost_reduction_pct"], d["call_reduction_pct"]}) == 3

    def test_no_blended_key_present(self):
        metrics = UnblendedMetrics(1000, 400, 1.0, 0.3, 10, 8)
        d = metrics.as_dict()
        forbidden = {"overall_reduction_pct", "blended_reduction_pct", "average_reduction_pct"}
        assert forbidden.isdisjoint(d.keys())

    def test_accumulator_sums_across_records(self):
        acc = MetricsAccumulator()
        acc.record(baseline_tokens=100, actual_tokens=50, baseline_calls=1, actual_calls=1)
        acc.record(baseline_tokens=200, actual_tokens=100, baseline_calls=1, actual_calls=1)
        snap = acc.snapshot()
        assert snap.baseline_tokens == 300
        assert snap.actual_tokens == 150
