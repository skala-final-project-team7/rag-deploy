"""라우터 OpenAI transport 회귀 — feature17a 후속 (라우터 의도 분류 prompt 보강).

build_openai_routing_transport: OpenAIRoutingLLMProvider 에 주입되는 transport
callable. system prompt 에 4종 의도 정의·예시·구분 기준 + 출력 schema 강제 메시지
가 포함되고, response_format=json_object 가 적용되는지 회귀 보호.
"""

from __future__ import annotations

import sys
import types
from typing import Any

import pytest

from app.query.routing_transport import build_openai_routing_transport
from query_routing_agent.llm.providers import (
    OpenAITransportError,
    RoutingClassificationRequest,
)


class _FakeMessage:
    def __init__(self, content: str) -> None:
        self.content = content


class _FakeChoice:
    def __init__(self, content: str) -> None:
        self.message = _FakeMessage(content)


class _FakeResponse:
    def __init__(self, content: str) -> None:
        self.choices = [_FakeChoice(content)]


class _FakeChatClient:
    """OpenAI client 대체 — chat.completions.create 만 받는 최소 스텁."""

    def __init__(self, *, response_content: str = '{"intent":"operations_guide"}') -> None:
        self.captured_kwargs: dict[str, Any] | None = None
        self._response = _FakeResponse(response_content)
        self.chat = types.SimpleNamespace(completions=types.SimpleNamespace(create=self._create))

    def _create(self, **kwargs: Any) -> _FakeResponse:
        self.captured_kwargs = kwargs
        return self._response


def _install_fake_openai(monkeypatch: pytest.MonkeyPatch, client: _FakeChatClient) -> None:
    """``from openai import OpenAI`` 가 fake 를 반환하도록 monkeypatch."""

    class _RateLimitError(Exception):
        pass

    class _APIError(Exception):
        status_code = 500

    module = types.ModuleType("openai")
    module.OpenAI = lambda **kwargs: client  # type: ignore[attr-defined]
    module.RateLimitError = _RateLimitError  # type: ignore[attr-defined]
    module.APIError = _APIError  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "openai", module)


def _make_request(prompt: str = "query: 테스트 질문") -> RoutingClassificationRequest:
    return RoutingClassificationRequest(
        query="테스트 질문",
        prompt=prompt,
        routing_input={},
        model="gpt-4o-mini",
        temperature=0.0,
        timeout_seconds=30,
    )


# --- system prompt 보강 ---


def test_transport_includes_intent_definitions_in_system_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """4종 의도 라벨 정의·구분 기준이 system prompt 에 포함된다."""
    client = _FakeChatClient()
    _install_fake_openai(monkeypatch, client)

    transport = build_openai_routing_transport(api_key="sk-test")
    transport(_make_request())

    assert client.captured_kwargs is not None
    messages = client.captured_kwargs["messages"]
    assert messages[0]["role"] == "system"
    system_content = messages[0]["content"]
    # 4종 라벨이 모두 명시돼야 한다.
    assert "incident_response" in system_content
    assert "operations_guide" in system_content
    assert "policy_procedure" in system_content
    assert "history_lookup" in system_content
    # 한국어 가이드.
    assert "한국어" in system_content
    # 출력 schema 정합 — expanded_queries / metadata_filters 명시.
    assert "expanded_queries" in system_content
    assert "metadata_filters" in system_content


def test_transport_preserves_agent_user_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    """agent 의 build_routing_prompt 가 만든 prompt 는 user 메시지로 그대로 전달."""
    client = _FakeChatClient()
    _install_fake_openai(monkeypatch, client)

    agent_prompt = "query: IAM 정책 변경 절차는?\nhistory: new_topic\ncontext_summary: empty"
    transport = build_openai_routing_transport(api_key="sk-test")
    transport(_make_request(prompt=agent_prompt))

    messages = client.captured_kwargs["messages"]  # type: ignore[index]
    assert messages[1]["role"] == "user"
    assert messages[1]["content"] == agent_prompt


def test_transport_enforces_json_object_response_format(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """response_format=json_object 가 OpenAI 호출에 강제된다."""
    client = _FakeChatClient()
    _install_fake_openai(monkeypatch, client)

    transport = build_openai_routing_transport(api_key="sk-test")
    transport(_make_request())

    assert client.captured_kwargs["response_format"] == {"type": "json_object"}  # type: ignore[index]


def test_transport_passes_model_and_temperature(monkeypatch: pytest.MonkeyPatch) -> None:
    """request 의 model / temperature / timeout 이 OpenAI 호출에 전달된다."""
    client = _FakeChatClient()
    _install_fake_openai(monkeypatch, client)

    transport = build_openai_routing_transport(api_key="sk-test")
    request = _make_request()
    transport(request)

    captured = client.captured_kwargs
    assert captured is not None
    assert captured["model"] == "gpt-4o-mini"
    assert captured["temperature"] == 0.0


def test_transport_returns_content_string(monkeypatch: pytest.MonkeyPatch) -> None:
    """transport 가 OpenAI 응답 content 문자열을 그대로 반환한다 (agent 파싱 정합)."""
    content = '{"intent":"incident_response","confidence":0.9,"reason":"테스트"}'
    client = _FakeChatClient(response_content=content)
    _install_fake_openai(monkeypatch, client)

    transport = build_openai_routing_transport(api_key="sk-test")
    result = transport(_make_request())
    assert result == content


# --- 에러 매핑 ---


def test_transport_rate_limit_maps_to_openai_transport_error_429(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """OpenAI RateLimitError → OpenAITransportError(status_code=429) 매핑."""

    class _RateLimitError(Exception):
        pass

    class _RateLimitClient:
        def __init__(self) -> None:
            self.chat = types.SimpleNamespace(
                completions=types.SimpleNamespace(create=self._create)
            )

        def _create(self, **_kwargs: Any) -> Any:
            raise _RateLimitError("rate limit")

    module = types.ModuleType("openai")
    module.OpenAI = lambda **kwargs: _RateLimitClient()  # type: ignore[attr-defined]
    module.RateLimitError = _RateLimitError  # type: ignore[attr-defined]
    module.APIError = type("APIError", (Exception,), {})  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "openai", module)

    transport = build_openai_routing_transport(api_key="sk-test")
    with pytest.raises(OpenAITransportError) as exc_info:
        transport(_make_request())
    assert exc_info.value.status_code == 429
