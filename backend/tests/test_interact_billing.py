from open_webui.utils.interact_billing import (
    current_user_question,
    estimate_reserved_tokens,
    image_usage_estimate,
    usage_token_counts,
)


def test_internal_context_summary_hides_transcript_from_usage_log():
    assert current_user_question(
        {"messages": [{"role": "user", "content": "full private transcript"}]},
        {"interact_channel": {"operation": "context-summary"}},
    ) == "[Internal channel context summary]"


def test_reserved_tokens_use_weighted_output_credits():
    input_tokens, max_output_tokens, reserved_tokens = estimate_reserved_tokens(
        {
            "messages": [{"role": "user", "content": "hello"}],
            "max_completion_tokens": 100,
        }
    )

    assert reserved_tokens == input_tokens + max_output_tokens * 5


def test_openai_reasoning_tokens_are_not_double_counted():
    input_tokens, output_tokens, compute_tokens, billable_tokens = usage_token_counts(
        {
            "prompt_tokens": 1_000,
            "completion_tokens": 500,
            "completion_tokens_details": {"reasoning_tokens": 200},
        },
        fallback_input_tokens=1,
    )

    assert (input_tokens, output_tokens, compute_tokens) == (1_000, 300, 200)
    assert billable_tokens == 1_000 + 300 * 5 + 200 * 5


def test_missing_usage_uses_estimated_input_and_output():
    assert usage_token_counts(
        None,
        fallback_input_tokens=800,
        fallback_output_tokens=120,
    ) == (800, 120, 0, 1_400)


def test_anthropic_cache_tokens_are_added_to_input():
    assert usage_token_counts(
        {
            "input_tokens": 100,
            "cache_creation_input_tokens": 200,
            "cache_read_input_tokens": 300,
            "output_tokens": 10,
        },
        fallback_input_tokens=1,
    ) == (600, 10, 0, 650)


def test_image_estimate_uses_weighted_compute_credits():
    usage = image_usage_estimate("prompt", 512, 512, 1)

    assert usage["billable_tokens"] == (
        usage["input_tokens"] + usage["output_tokens"] * 5 + usage["compute_tokens"] * 5
    )
