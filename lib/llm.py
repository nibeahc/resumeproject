"""Anthropic API 래퍼. skills/*.md 를 system 프롬프트로 쓰는 completion 호출을 감싼다."""
from lib import config

_client = None


def get_client():
    """Anthropic 클라이언트를 만들어 재사용한다. 키가 없으면 config.ConfigError."""
    global _client
    if _client is None:
        import anthropic
        _client = anthropic.Anthropic(api_key=config.anthropic_api_key())
    return _client


def stream_complete(system, messages, model=None, max_tokens=4096, on_delta=None, thinking=False):
    """system + messages 로 completion 을 스트리밍 호출하고 결과를 모아 돌려준다.

    on_delta(text_chunk) 를 주면 각 청크가 올 때마다 호출한다 (SSE 중계 등에 쓴다).
    thinking=False(기본) 면 확장 사고를 끈다. 이 모델은 요청하지 않아도 thinking 을 켤 때가 있는데,
    그러면 max_tokens 를 thinking 이 다 써버려 답변 텍스트가 전혀 나오지 않을 수 있다 (실측 확인됨).
    자소서 문단 작성처럼 최종 텍스트만 필요한 호출은 꺼두는 편이 안전하다.
    반환값: {'text': str, 'stop_reason': str, 'usage': {'input_tokens', 'output_tokens', 'thinking_tokens'}}
    """
    client = get_client()
    parts = []
    kwargs = {} if thinking else {'thinking': {'type': 'disabled'}}
    with client.messages.stream(
        model=model or config.model(),
        max_tokens=max_tokens,
        system=system,
        messages=messages,
        **kwargs,
    ) as stream:
        for chunk in stream.text_stream:
            parts.append(chunk)
            if on_delta:
                on_delta(chunk)
        final = stream.get_final_message()
    return {
        'text': ''.join(parts),
        'stop_reason': final.stop_reason,
        'usage': {
            'input_tokens': final.usage.input_tokens,
            'output_tokens': final.usage.output_tokens,
            'thinking_tokens': final.usage.output_tokens_details.thinking_tokens,
        },
    }
