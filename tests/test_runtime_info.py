import json

from cli.runtime_info import (
    format_usage_detail_value,
    format_usage_value,
    merge_usage,
    usage_from_json_line,
)


def test_claude_result_usage_includes_cache_fields():
    line = json.dumps(
        {
            "type": "result",
            "num_turns": 3,
            "total_cost_usd": 1.23,
            "usage": {
                "input_tokens": 10,
                "output_tokens": 5,
                "cache_read_input_tokens": 7,
                "cache_creation": {
                    "ephemeral_1h_input_tokens": 2,
                    "ephemeral_5m_input_tokens": 3,
                },
            },
        }
    )

    usage, is_final = usage_from_json_line(line, "claude")

    assert is_final is True
    assert usage["turns"] == 3
    assert usage["input_tokens"] == 10
    assert usage["output_tokens"] == 5
    assert usage["cache_read_input_tokens"] == 7
    assert usage["cache_creation_input_tokens"] == 5
    assert usage["cost_usd"] == 1.23
    assert format_usage_value(usage) == "10/5/7"


def test_codex_cached_input_tokens_are_cache_read():
    line = json.dumps(
        {
            "type": "turn.completed",
            "usage": {
                "input_tokens": 100,
                "cached_input_tokens": 40,
                "output_tokens": 9,
                "reasoning_output_tokens": 2,
                "total_tokens": 149,
            },
        }
    )

    usage, is_final = usage_from_json_line(line, "codex")

    assert is_final is True
    assert usage["turns"] == 1
    assert usage["cache_read_input_tokens"] == 40
    assert usage["cached_input_tokens"] == 40
    assert usage["reasoning_output_tokens"] == 2
    assert format_usage_value(usage) == "100/9/40"
    assert "reasoning 2" in format_usage_detail_value(usage)


def test_accumulate_turn_merges_all_token_fields():
    current = {"turns": 1, "input_tokens": 10, "output_tokens": 2, "cache_read_input_tokens": 4}
    update = {"turns": 1, "input_tokens": 20, "output_tokens": 3, "cache_read_input_tokens": 5}

    merged = merge_usage(current, update, accumulate_turn=True)

    assert merged["turns"] == 2
    assert merged["input_tokens"] == 30
    assert merged["output_tokens"] == 5
    assert merged["cache_read_input_tokens"] == 9
