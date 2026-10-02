import copy
from open_webui.utils.response import merge_usage, update_usage_snapshot


def test_gemini_repeated_usage_is_not_another_model_call():
    chunk = {'prompt_tokens':5654,'completion_tokens':31,'total_tokens':5685}
    usage = update_usage_snapshot(None, chunk)
    assert update_usage_snapshot(usage, chunk)['total_tokens'] == 5685


def test_cumulative_text_chunks_keep_latest_not_sum():
    usage = None
    for count in [1,10,20,80,111,111]:
        usage = update_usage_snapshot(usage, {'prompt_tokens':5683,'completion_tokens':count,'total_tokens':5683+count})
    assert usage['input_tokens'] == 5683
    assert usage['output_tokens'] == 111
    assert usage['total_tokens'] == 5794


def test_multiple_real_tool_continuations_are_still_additive():
    total = None
    for input_count,output_count in [(100,10),(200,20),(300,30)]:
        before = total
        response_usage = None
        for completed in [1,output_count,output_count]:
            response_usage = update_usage_snapshot(response_usage, {'prompt_tokens':input_count,'completion_tokens':completed})
            total = merge_usage(before, response_usage)
    assert total['input_tokens'] == 600
    assert total['output_tokens'] == 60
    assert total['total_tokens'] == 660


def test_partial_snapshots_preserve_input_and_explicit_zero():
    usage = update_usage_snapshot(None, {'input_tokens':100})
    usage = update_usage_snapshot(usage, {'output_tokens':20})
    assert usage['total_tokens'] == 120
    usage = update_usage_snapshot(usage, {'output_tokens':0})
    assert usage['output_tokens'] == 0 and usage['total_tokens'] == 100


def test_provider_total_and_cache_details_are_snapshots():
    chunk = {'prompt_tokens':100,'completion_tokens':20,'total_tokens':150,'prompt_tokens_details':{'cached_tokens':60}}
    original = copy.deepcopy(chunk)
    usage = update_usage_snapshot(None,chunk)
    usage = update_usage_snapshot(usage,chunk)
    assert usage['total_tokens'] == 150
    assert usage['prompt_tokens_details']['cached_tokens'] == 60
    assert chunk == original
