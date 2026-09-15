"""Token-savings benchmark for the context compression engine.

Measures raw vs optimized token counts (and % reduction) that
HierarchicalCompactor achieves on realistic payloads: a multi-turn chat
history, a SQL schema dump, and a block of server logs.

Tokenization uses the vendored, offline ``claude-bpe`` vocab
(``core/vocab/claude-bpe-tokenizer.json``) instead of constructing
``TokenizerEngine`` -- whose ``__init__`` eagerly loads the ``cl100k``
tiktoken vocab over the network. ``claude-bpe`` is a real, named tokenizer
shipped with the repo, so this benchmark is deterministic and runs offline
in CI. Run ``pytest -s tests/test_token_savings_benchmark.py`` to see the
reduction table.

Summarization uses compaction.py's deterministic extractive fallback (no model
call), so these reductions are a conservative floor; production summarization
via a Tier-2 model compresses considerably more.
"""

from __future__ import annotations

import pytest

import agent_gateway.core.cache_boundary as cache_boundary
from agent_gateway.core.compaction import HierarchicalCompactor, SuffixTurn

TOKEN_THRESHOLD = 500
KEEP_RECENT_TURNS = 2


class _OfflineClaudeTokenizer:
    """TokenizerEngine-compatible counter using only the vendored, offline
    claude-bpe vocab. Avoids TokenizerEngine's eager cl100k/tiktoken network
    load, so the benchmark is self-contained and deterministic."""

    def __init__(self) -> None:
        from tokenizers import Tokenizer

        self._tok = Tokenizer.from_file(str(cache_boundary._CLAUDE_VOCAB_PATH))

    def count(self, text: str, tokenizer: str = "claude-bpe") -> int:  # noqa: ARG002
        return len(self._tok.encode(text).ids)


def _compactor() -> HierarchicalCompactor:
    return HierarchicalCompactor(
        tokenizer_engine=_OfflineClaudeTokenizer(),
        tokenizer_name="claude-bpe",
        token_threshold=TOKEN_THRESHOLD,
        keep_recent_turns=KEEP_RECENT_TURNS,
    )


# --------------------------------------------------------------------------
# Realistic payloads. Each returns a list[SuffixTurn] representing the
# conversation suffix (everything after the frozen prefix) at compaction time.
# --------------------------------------------------------------------------

def chat_history_turns() -> list[SuffixTurn]:
    """A long technical debugging conversation: short user prompts and verbose,
    multi-paragraph assistant analyses -- the kind of accumulated context an
    agent carries many turns deep, where compaction earns its keep."""
    user_msgs = [
        "The checkout page throws a 500 for EU customers only, starting after yesterday's "
        "deploy. Find out what changed and how to fix it.",
        "That lines up with the region error rates. What's the smallest safe fix we can ship "
        "today without rolling back the whole release?",
        "Do we have any test coverage that should have caught this before production?",
        "Good. Add monitoring too so we catch a regional spike quickly next time.",
        "What's the blast radius right now -- did any orders actually complete incorrectly?",
        "Ship the fix and the tests now; the alert can follow this afternoon.",
    ]
    assistant_msgs = [
        "I pulled the deploy diff and traced the failure. The release swapped the "
        "currency-formatting library for a stricter major version. The new version rejects "
        "locales that omit an explicit region, raising instead of falling back. Our EU carts "
        "store bare language codes like 'fr' rather than 'fr-FR', so formatting throws and the "
        "request handler turns that into a 500.\n\n"
        "The US path is unaffected because those carts store 'en-US', which is already fully "
        "qualified. That asymmetry is exactly why only EU traffic breaks while the global error "
        "rate barely moved. I confirmed it by replaying two captured EU requests against the new "
        "formatter in a scratch environment, and both reproduced the exception cleanly.",

        "The smallest safe fix is to normalise the stored locale at the formatting boundary "
        "before it reaches the library. We map a bare language code to a sensible default region "
        "-- 'fr' to 'fr-FR', 'de' to 'de-DE', and so on -- in a tiny adapter. It is a few lines, "
        "touches a single module, and needs no schema change or data migration.\n\n"
        "That is far less risky than reverting the entire release, which also carried unrelated "
        "changes we want to keep. I would gate the normalisation behind the same code path all "
        "providers already share, so the behaviour is identical regardless of which formatter is "
        "active. We can ship it under the existing feature flag and watch the EU error rate fall "
        "in real time as it rolls out.",

        "No, and that is the real gap. The formatting tests only ever exercised fully-qualified "
        "locales, so the bare-code path was never asserted and the stricter library slipped "
        "through CI unnoticed. The unit suite was green the whole time.\n\n"
        "I will add a regression test that feeds 'fr' and asserts a formatted string rather than "
        "an exception, plus a parametrized case for every locale we support in production. That "
        "way any future dependency bump that tightens locale handling fails loudly in CI instead "
        "of shipping. I will also add one end-to-end checkout test pinned to an EU locale so the "
        "whole request path is covered, not just the formatter in isolation.",

        "Agreed. I will add a per-region 5xx-rate alert that pages when any single region exceeds "
        "roughly three times its trailing-hour baseline. A locale- or region-specific failure "
        "shows up there long before it moves the global error rate enough to trip the existing "
        "threshold.\n\n"
        "I will also add a dashboard panel that breaks the 5xx rate out by region and by locale, "
        "so the next investigation starts from the signal rather than from a raw log tail. The "
        "alert routes to the on-call rotation and links straight to that panel and the relevant "
        "deploy, which should cut time-to-diagnosis substantially.",

        "No orders completed incorrectly. The request fails before payment authorization, so "
        "nothing was ever charged and no partial or malformed order reached the database. I "
        "verified there are no orphaned payment intents for the affected window.\n\n"
        "The practical impact is abandoned EU checkouts for roughly the last eighteen hours -- "
        "lost conversions rather than data corruption. Once the locale fix ships, those customers "
        "can complete checkout normally on their next attempt, and I will confirm the recovery by "
        "watching completed-order volume return to its usual EU baseline.",

        "Deploying now. The normalisation adapter and the parametrized locale tests are going out "
        "together behind the shared formatter path, and I am watching the EU 5xx rate as the "
        "rollout progresses.\n\n"
        "I have opened a separate follow-up for the per-region alert and the regional dashboard "
        "panel, scheduled for this afternoon so it gets its own review rather than riding along "
        "with the hotfix. I will report back once the EU error rate is at baseline and completed "
        "EU orders have recovered.",
    ]
    turns: list[SuffixTurn] = []
    idx = 0
    for user, assistant in zip(user_msgs, assistant_msgs):
        turns.append(SuffixTurn(index=idx, text=f"User: {user}"))
        idx += 1
        turns.append(SuffixTurn(index=idx, text=f"Assistant: {assistant}"))
        idx += 1
    return turns


def sql_schema_turns() -> list[SuffixTurn]:
    """Tool observations that each dump a chunk of an information_schema /
    DDL export -- the kind of large, repetitive context an agent accumulates
    while exploring a database."""
    tables = [
        ("customers", ["id BIGINT PRIMARY KEY", "email TEXT NOT NULL UNIQUE",
                        "full_name TEXT NOT NULL", "locale TEXT NOT NULL DEFAULT 'en-US'",
                        "created_at TIMESTAMPTZ NOT NULL", "updated_at TIMESTAMPTZ NOT NULL",
                        "deleted_at TIMESTAMPTZ", "marketing_opt_in BOOLEAN NOT NULL DEFAULT false"]),
        ("orders", ["id BIGINT PRIMARY KEY", "customer_id BIGINT NOT NULL REFERENCES customers(id)",
                    "status TEXT NOT NULL", "currency TEXT NOT NULL", "total_cents INTEGER NOT NULL",
                    "placed_at TIMESTAMPTZ NOT NULL", "fulfilled_at TIMESTAMPTZ",
                    "cancelled_at TIMESTAMPTZ", "notes TEXT"]),
        ("order_items", ["id BIGINT PRIMARY KEY", "order_id BIGINT NOT NULL REFERENCES orders(id)",
                         "sku TEXT NOT NULL", "quantity INTEGER NOT NULL", "unit_price_cents INTEGER NOT NULL",
                         "discount_cents INTEGER NOT NULL DEFAULT 0", "tax_cents INTEGER NOT NULL DEFAULT 0"]),
        ("payments", ["id BIGINT PRIMARY KEY", "order_id BIGINT NOT NULL REFERENCES orders(id)",
                      "processor TEXT NOT NULL", "processor_ref TEXT NOT NULL", "amount_cents INTEGER NOT NULL",
                      "status TEXT NOT NULL", "authorized_at TIMESTAMPTZ", "captured_at TIMESTAMPTZ"]),
        ("addresses", ["id BIGINT PRIMARY KEY", "customer_id BIGINT NOT NULL REFERENCES customers(id)",
                       "kind TEXT NOT NULL", "line1 TEXT NOT NULL", "line2 TEXT", "city TEXT NOT NULL",
                       "region TEXT", "postal_code TEXT NOT NULL", "country_code TEXT NOT NULL"]),
        ("shipments", ["id BIGINT PRIMARY KEY", "order_id BIGINT NOT NULL REFERENCES orders(id)",
                       "carrier TEXT NOT NULL", "tracking_number TEXT", "shipped_at TIMESTAMPTZ",
                       "delivered_at TIMESTAMPTZ", "status TEXT NOT NULL"]),
        ("refunds", ["id BIGINT PRIMARY KEY", "payment_id BIGINT NOT NULL REFERENCES payments(id)",
                     "amount_cents INTEGER NOT NULL", "reason TEXT", "created_at TIMESTAMPTZ NOT NULL"]),
        ("audit_log", ["id BIGINT PRIMARY KEY", "actor TEXT NOT NULL", "action TEXT NOT NULL",
                       "entity TEXT NOT NULL", "entity_id BIGINT NOT NULL", "at TIMESTAMPTZ NOT NULL",
                       "detail JSONB"]),
        ("sessions", ["id UUID PRIMARY KEY", "customer_id BIGINT REFERENCES customers(id)",
                      "ip INET NOT NULL", "user_agent TEXT", "started_at TIMESTAMPTZ NOT NULL",
                      "last_seen_at TIMESTAMPTZ NOT NULL"]),
        ("coupons", ["id BIGINT PRIMARY KEY", "code TEXT NOT NULL UNIQUE", "kind TEXT NOT NULL",
                     "value_cents INTEGER", "percent INTEGER", "expires_at TIMESTAMPTZ",
                     "max_redemptions INTEGER", "redeemed_count INTEGER NOT NULL DEFAULT 0"]),
    ]
    turns = []
    for i, (name, cols) in enumerate(tables):
        body = ",\n    ".join(cols)
        ddl = (f"-- observation: DESCRIBE {name}\n"
               f"CREATE TABLE {name} (\n    {body}\n);\n"
               f"CREATE INDEX idx_{name}_created ON {name} (created_at);")
        turns.append(SuffixTurn(index=i, text=ddl))
    return turns


def server_log_turns() -> list[SuffixTurn]:
    """Tool observations that each return a chunk of application logs -- highly
    repetitive lines plus an occasional stack trace."""
    base_lines = [
        "2026-02-11T09:{m:02d}:{s:02d}Z INFO  request_id={rid} GET /api/checkout 200 41ms",
        "2026-02-11T09:{m:02d}:{s:02d}Z INFO  request_id={rid} POST /api/cart 200 12ms",
        "2026-02-11T09:{m:02d}:{s:02d}Z WARN  request_id={rid} slow query orders.by_customer 812ms",
        "2026-02-11T09:{m:02d}:{s:02d}Z INFO  request_id={rid} POST /api/checkout 500 8ms locale=fr",
        "2026-02-11T09:{m:02d}:{s:02d}Z ERROR request_id={rid} ValueError: locale 'fr' missing region",
    ]
    trace = ("Traceback (most recent call last):\n"
             "  File \"checkout.py\", line 142, in handle\n"
             "    body = format_currency(total, locale)\n"
             "  File \"money.py\", line 88, in format_currency\n"
             "    raise ValueError(f\"locale {loc!r} missing region\")\n"
             "ValueError: locale 'fr' missing region")
    turns = []
    for i in range(12):
        lines = [ln.format(m=i, s=(i * 7) % 60, rid=f"r{i:03d}{j}") for j, ln in enumerate(base_lines)]
        block = "\n".join(lines)
        if i % 4 == 3:
            block += "\n" + trace
        turns.append(SuffixTurn(index=i, text=f"-- observation: tail -n 20 app.log (chunk {i})\n{block}"))
    return turns


PAYLOADS = {
    "chat_history": chat_history_turns,
    "sql_schema": sql_schema_turns,
    "server_logs": server_log_turns,
}

# Reduction floors (percent). Tuned below observed values with margin so the
# suite asserts a real, meaningful saving without being flaky.
# Reduction floors (percent), set below observed values with margin. They
# differ by payload on purpose: under the deterministic extractive fallback
# summarizer used here, structured data (schemas, logs) compresses much more
# than free-form prose. These are a CONSERVATIVE floor -- production routes
# summarization through a Tier-2 model (see compaction.py) that compresses
# substantially more than the offline fallback measured in this benchmark.
REDUCTION_FLOORS = {
    "chat_history": 15.0,
    "sql_schema": 35.0,
    "server_logs": 40.0,
}


def _measure(turns: list[SuffixTurn]):
    result = _compactor().maybe_compact("SYSTEM PROMPT (frozen, cached prefix)", turns)
    before, after = result.tokens_before, result.tokens_after
    pct = (100.0 * (before - after) / before) if before else 0.0
    return result, before, after, pct


@pytest.mark.parametrize("name", list(PAYLOADS))
def test_compression_yields_meaningful_token_savings(name):
    result, before, after, pct = _measure(PAYLOADS[name]())
    assert result.triggered, (
        f"{name}: compaction did not trigger (raw={before} <= threshold={TOKEN_THRESHOLD})"
    )
    assert after < before, f"{name}: optimized ({after}) not below raw ({before})"
    assert result.tokens_saved == before - after
    assert pct >= REDUCTION_FLOORS[name], (
        f"{name}: only {pct:.1f}% reduction (raw={before}, optimized={after}, "
        f"floor={REDUCTION_FLOORS[name]}%)"
    )


def test_recent_turns_are_kept_verbatim_after_compaction():
    """Savings must not come from dropping the most recent turns: the last
    KEEP_RECENT_TURNS turns survive unchanged."""
    turns = chat_history_turns()
    result, _, _, _ = _measure(turns)
    assert result.triggered
    kept = result.suffix_turns[-KEEP_RECENT_TURNS:]
    original_tail = turns[-KEEP_RECENT_TURNS:]
    assert [t.text for t in kept] == [t.text for t in original_tail]


def test_benchmark_report(capsys):
    """Emit the raw/optimized/reduction table and assert every payload nets a
    saving. Run with -s to see the table."""
    rows = []
    for name, gen in PAYLOADS.items():
        result, before, after, pct = _measure(gen())
        rows.append((name, before, after, result.tokens_saved, pct))

    with capsys.disabled():
        print("\nContext compression engine — token savings (tokenizer: claude-bpe)")
        print(f"{'payload':<16}{'raw':>8}{'optimized':>12}{'saved':>10}{'reduction':>12}")
        print("-" * 58)
        for name, before, after, saved, pct in rows:
            print(f"{name:<16}{before:>8}{after:>12}{saved:>10}{pct:>11.1f}%")
        total_before = sum(r[1] for r in rows)
        total_after = sum(r[2] for r in rows)
        total_pct = 100.0 * (total_before - total_after) / total_before
        print("-" * 58)
        print(f"{'TOTAL':<16}{total_before:>8}{total_after:>12}"
              f"{total_before - total_after:>10}{total_pct:>11.1f}%")

    assert all(after < before for _, before, after, _, _ in rows)
