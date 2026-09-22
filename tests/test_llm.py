"""lib/llm.py 의 stream_complete 배선 테스트 (S1-1).

실제 API 를 호출하지 않고 anthropic 클라이언트를 스텁으로 대체한다.
이 모델(claude-sonnet-5)은 thinking 을 명시적으로 끄지 않으면 스스로 켜질 때가 있고,
그러면 max_tokens 를 thinking 이 다 써버려 답변 텍스트가 전혀 나오지 않는 것을
S1-1 실사용 테스트에서 실측했다 (input 8772 / max_tokens 4000 전부 thinking 으로 소진, stop_reason=max_tokens, text_stream 델타 0개).
그래서 stream_complete() 가 기본적으로 thinking 을 끄고 호출하는지를 반드시 회귀 테스트로 고정한다.
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import lib.llm as llm  # noqa: E402


class FakeStreamCtx:
    """client.messages.stream(...) 이 돌려주는 context manager 를 흉내낸다."""

    def __init__(self, chunks, stop_reason='end_turn', input_tokens=10, output_tokens=5, thinking_tokens=0):
        self.chunks = chunks
        self._final = MagicMock()
        self._final.stop_reason = stop_reason
        self._final.usage.input_tokens = input_tokens
        self._final.usage.output_tokens = output_tokens
        self._final.usage.output_tokens_details.thinking_tokens = thinking_tokens

    def __enter__(self):
        self.text_stream = iter(self.chunks)
        return self

    def __exit__(self, *a):
        return False

    def get_final_message(self):
        return self._final


class StreamCompleteTest(unittest.TestCase):
    def setUp(self):
        self.fake_client = MagicMock()
        self.fake_client.messages.stream.return_value = FakeStreamCtx(['안녕', '하세요'])
        self._orig_get_client = llm.get_client
        llm.get_client = lambda: self.fake_client

    def tearDown(self):
        llm.get_client = self._orig_get_client

    def test_thinking_disabled_by_default(self):
        llm.stream_complete(system='s', messages=[{'role': 'user', 'content': 'hi'}])
        _, kwargs = self.fake_client.messages.stream.call_args
        self.assertEqual(kwargs.get('thinking'), {'type': 'disabled'})

    def test_thinking_true_omits_the_flag(self):
        llm.stream_complete(system='s', messages=[{'role': 'user', 'content': 'hi'}], thinking=True)
        _, kwargs = self.fake_client.messages.stream.call_args
        self.assertNotIn('thinking', kwargs)

    def test_result_collects_deltas_and_usage(self):
        seen = []
        result = llm.stream_complete(system='s', messages=[{'role': 'user', 'content': 'hi'}], on_delta=seen.append)
        self.assertEqual(result['text'], '안녕하세요')
        self.assertEqual(seen, ['안녕', '하세요'])
        self.assertEqual(result['usage'], {'input_tokens': 10, 'output_tokens': 5, 'thinking_tokens': 0})

    def test_model_and_max_tokens_passed_through(self):
        llm.stream_complete(system='s', messages=[{'role': 'user', 'content': 'hi'}], model='claude-opus-5', max_tokens=999)
        _, kwargs = self.fake_client.messages.stream.call_args
        self.assertEqual(kwargs['model'], 'claude-opus-5')
        self.assertEqual(kwargs['max_tokens'], 999)


if __name__ == '__main__':
    unittest.main()
