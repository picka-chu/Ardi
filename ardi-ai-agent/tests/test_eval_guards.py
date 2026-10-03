"""Offline guardrail eval: 50 real-ish customer messages.

Runs WITHOUT any API key. Asserts the safety layers hold for every input:
- sanitize_prompt_text strips control markers + override phrases
- language detection matches the labeled script
- hostile inputs come out inert (no ===MARKERS===, no override phrases)
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from textpolish import sanitize_prompt_text, detect_lang

with open(os.path.join(os.path.dirname(__file__), "eval_customer_messages.json"),
          encoding="utf-8") as f:
    CASES = json.load(f)


def test_eval_size():
    assert len(CASES) >= 50, f"eval set shrank to {len(CASES)}"


class TestEvalGuards:
    def test_all_inputs_survive_pipeline(self):
        for i, case in enumerate(CASES):
            out = sanitize_prompt_text(case["text"])
            assert isinstance(out, str), i
            assert "===" not in out, f"marker leaked in case {i}: {case['text'][:40]}"
            low = out.lower()
            assert "ignore previous instructions" not in low, i
            assert "ignore all prior instructions" not in low, i
            assert "disregard previous instructions" not in low, i

    def test_hostile_inputs_neutered(self):
        hostile = [c for c in CASES if c.get("hostile")]
        assert len(hostile) >= 8
        for case in hostile:
            out = sanitize_prompt_text(case["text"]).lower()
            assert "===" not in out
            assert "ignore previous instructions" not in out
            assert "ignore all prior instructions" not in out
            assert "disregard previous instructions" not in out
            assert "[system:" not in out

    def test_language_labels(self):
        for i, case in enumerate(CASES):
            if "lang" in case and case["lang"] != "unknown":
                assert detect_lang(case["text"]) == case["lang"], f"case {i}: {case['text'][:40]}"

    def test_system_prompt_markers_rejected_downstream(self):
        # Even if a marker survived, order validation must not accept junk.
        from ai.validation import validate_order_data
        import pytest
        with pytest.raises(ValueError):
            validate_order_data({"items": []})
        with pytest.raises(ValueError):
            validate_order_data({"items": [{"product": "   ", "quantity": 1}]})
