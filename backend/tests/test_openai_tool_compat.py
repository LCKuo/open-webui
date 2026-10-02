import unittest

from open_webui.utils.openai_tool_compat import (
    apply_chat_completion_tool_compat,
    managed_agent_reasoning_effort,
)


class OpenAIToolCompatTests(unittest.TestCase):
    def test_gpt_56_tool_calls_disable_reasoning(self):
        payload = {'model': 'gpt-5.6-terra', 'tools': [{'type': 'function'}]}

        self.assertEqual(apply_chat_completion_tool_compat(payload)['reasoning_effort'], 'none')

    def test_plain_text_request_is_not_changed(self):
        payload = {'model': 'gpt-5.6-terra', 'messages': []}

        self.assertNotIn('reasoning_effort', apply_chat_completion_tool_compat(payload))

    def test_other_model_tool_setting_is_not_overridden(self):
        payload = {'model': 'moonshotai/kimi-k3', 'tools': [], 'reasoning_effort': 'low'}

        self.assertEqual(apply_chat_completion_tool_compat(payload)['reasoning_effort'], 'low')

    def test_personal_model_prefix_is_removed_for_agent_setting(self):
        self.assertEqual(
            managed_agent_reasoning_effort('own-d0a1d8c476.gpt-5.6-terra'),
            'none',
        )


if __name__ == '__main__':
    unittest.main()
