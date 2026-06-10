"""OpenAI Chat Completions transport callable 검증 — (B) 운영 OpenAI HTTP transport.

build_openai_chat_transport: agent OpenAIAnswerLLMProvider 의 transport 자리에 주입
되는 callable. 실제 OpenAI API 호출은 mock 으로 대체하고, payload 정규화·JSON 파싱·
에러 흡수 분기를 검증한다.
"""

from __future__ import annotations

import json
import sys
import types
from typing import Any

import pytest

from app.query.openai_transport import (
    GROUNDED_CITATION_RESPONSE_FORMAT,
    build_openai_chat_transport,
    select_generator_response_format,
)

# --- Fake OpenAI SDK ---


class _FakeMessage:
    def __init__(self, content: str) -> None:
        self.content = content


class _FakeChoice:
    def __init__(self, content: str) -> None:
        self.message = _FakeMessage(content)


class _FakeCompletion:
    def __init__(self, content: str) -> None:
        self.choices = [_FakeChoice(content)]


class _FakeClient:
    """OpenAI client 대체 — chat.completions.create + close 를 받는 최소 스텁.

    2026-06-10(A7): ``_transport`` 가 finally 에서 ``client.close()`` 를 호출하므로
    실 SDK 와 동일하게 close() 를 제공하고 호출 여부를 추적한다.
    """

    def __init__(
        self,
        *,
        response_content: str = '{"answer": "ok"}',
        raise_error: BaseException | None = None,
    ) -> None:
        self._response_content = response_content
        self._raise_error = raise_error
        self.captured_kwargs: dict[str, Any] | None = None
        self.closed = False
        self.chat = types.SimpleNamespace(completions=types.SimpleNamespace(create=self._create))

    def _create(self, **kwargs: Any) -> _FakeCompletion:
        self.captured_kwargs = kwargs
        if self._raise_error is not None:
            raise self._raise_error
        return _FakeCompletion(self._response_content)

    def close(self) -> None:
        self.closed = True


class _FakeAPITimeoutError(Exception):
    pass


class _FakeAPIStatusError(Exception):
    def __init__(self, message: str, status_code: int) -> None:
        super().__init__(message)
        self.status_code = status_code


class _FakeAPIError(Exception):
    pass


def _install_fake_openai(monkeypatch: pytest.MonkeyPatch, client: _FakeClient) -> None:
    """본 저장소의 transport callable 이 lazy import 하는 openai 모듈을 fake 로 대체."""
    module = types.ModuleType("openai")
    module.OpenAI = lambda **kwargs: client  # type: ignore[attr-defined]
    module.APITimeoutError = _FakeAPITimeoutError  # type: ignore[attr-defined]
    module.APIStatusError = _FakeAPIStatusError  # type: ignore[attr-defined]
    module.APIError = _FakeAPIError  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "openai", module)


def _payload(*, model: str = "gpt-4o") -> dict[str, Any]:
    return {
        "model": model,
        "temperature": 0.2,
        "timeout_seconds": 45,
        "messages": [
            {"role": "system", "content": "system instructions"},
            {"role": "developer", "content": "developer instructions"},
            {"role": "user", "content": "user question"},
        ],
    }


# --- 정상 동작 ---


def test_transport_returns_parsed_json(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _FakeClient(
        response_content=json.dumps(
            {"answer": "장애 대응 안내.", "sentences": [], "unsupported_gaps": []}
        )
    )
    _install_fake_openai(monkeypatch, client)

    transport = build_openai_chat_transport(api_key="sk-test")
    result = transport(_payload())

    assert result["answer"] == "장애 대응 안내."
    assert client.captured_kwargs is not None
    # JSON 강제 모드가 기본값
    assert client.captured_kwargs["response_format"] == {"type": "json_object"}


def test_transport_merges_developer_into_system_role(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # OpenAI Chat Completions 는 developer role 을 별도로 받지 않으므로 system 으로 합산.
    client = _FakeClient()
    _install_fake_openai(monkeypatch, client)

    transport = build_openai_chat_transport(api_key="sk-test")
    transport(_payload())

    assert client.captured_kwargs is not None
    messages = client.captured_kwargs["messages"]
    # system 1개 + user 1개 — developer 가 system 으로 합쳐졌다.
    roles = [m["role"] for m in messages]
    assert roles == ["system", "user"]
    assert "system instructions" in messages[0]["content"]
    assert "developer instructions" in messages[0]["content"]


def test_transport_passes_model_and_temperature(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _FakeClient()
    _install_fake_openai(monkeypatch, client)

    transport = build_openai_chat_transport(api_key="sk-test")
    transport(_payload(model="gpt-4o-mini"))

    assert client.captured_kwargs is not None
    assert client.captured_kwargs["model"] == "gpt-4o-mini"
    assert client.captured_kwargs["temperature"] == 0.2


# --- feature17c-14 환각 보수성 guard (system_prompt_suffix) ---


def test_transport_appends_system_prompt_suffix_when_provided(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """system_prompt_suffix 가 주어지면 합쳐진 system 메시지 끝에 덧붙는다."""
    client = _FakeClient()
    _install_fake_openai(monkeypatch, client)

    transport = build_openai_chat_transport(
        api_key="sk-test",
        system_prompt_suffix="[보수성 강화 지침] 미근거 문장 금지.",
    )
    transport(_payload())

    assert client.captured_kwargs is not None
    messages = client.captured_kwargs["messages"]
    assert [m["role"] for m in messages] == ["system", "user"]
    system_content = messages[0]["content"]
    # vendored system/developer 지침은 보존되고, suffix 가 마지막에 덧붙는다.
    assert "system instructions" in system_content
    assert "developer instructions" in system_content
    assert system_content.rstrip().endswith("미근거 문장 금지.")


def test_transport_omits_suffix_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """system_prompt_suffix 미지정(기본)이면 system 메시지는 기존 그대로(보강 없음)."""
    client = _FakeClient()
    _install_fake_openai(monkeypatch, client)

    transport = build_openai_chat_transport(api_key="sk-test")
    transport(_payload())

    assert client.captured_kwargs is not None
    system_content = client.captured_kwargs["messages"][0]["content"]
    # developer 합산 결과만 존재 — 보수성 guard 문구는 없다.
    assert "보수성 강화 지침" not in system_content


# --- 에러 흡수 ---


def test_transport_empty_content_raises_openai_transport_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from answer_generation_agent.generation.answer_generation import OpenAITransportError

    client = _FakeClient(response_content="")
    _install_fake_openai(monkeypatch, client)

    transport = build_openai_chat_transport(api_key="sk-test")
    with pytest.raises(OpenAITransportError) as exc_info:
        transport(_payload())
    assert exc_info.value.status_code == 500
    assert "empty" in exc_info.value.message.lower()


def test_transport_invalid_json_raises_openai_transport_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from answer_generation_agent.generation.answer_generation import OpenAITransportError

    client = _FakeClient(response_content="not a json")
    _install_fake_openai(monkeypatch, client)

    transport = build_openai_chat_transport(api_key="sk-test")
    with pytest.raises(OpenAITransportError):
        transport(_payload())


def test_transport_non_object_json_raises_openai_transport_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from answer_generation_agent.generation.answer_generation import OpenAITransportError

    client = _FakeClient(response_content='["not", "an", "object"]')
    _install_fake_openai(monkeypatch, client)

    transport = build_openai_chat_transport(api_key="sk-test")
    with pytest.raises(OpenAITransportError):
        transport(_payload())


def test_transport_timeout_raises_with_none_status_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from answer_generation_agent.generation.answer_generation import OpenAITransportError

    client = _FakeClient(raise_error=_FakeAPITimeoutError("timeout"))
    _install_fake_openai(monkeypatch, client)

    transport = build_openai_chat_transport(api_key="sk-test")
    with pytest.raises(OpenAITransportError) as exc_info:
        transport(_payload())
    # agent _openai_error_to_provider_error 가 status_code=None 을 timeout_error 로 분류.
    assert exc_info.value.status_code is None


def test_transport_status_error_preserves_status_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from answer_generation_agent.generation.answer_generation import OpenAITransportError

    client = _FakeClient(raise_error=_FakeAPIStatusError("rate limit", status_code=429))
    _install_fake_openai(monkeypatch, client)

    transport = build_openai_chat_transport(api_key="sk-test")
    with pytest.raises(OpenAITransportError) as exc_info:
        transport(_payload())
    assert exc_info.value.status_code == 429


def test_transport_generic_api_error_normalized_to_500(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from answer_generation_agent.generation.answer_generation import OpenAITransportError

    client = _FakeClient(raise_error=_FakeAPIError("unknown"))
    _install_fake_openai(monkeypatch, client)

    transport = build_openai_chat_transport(api_key="sk-test")
    with pytest.raises(OpenAITransportError) as exc_info:
        transport(_payload())
    assert exc_info.value.status_code == 500


# --- response_format 커스텀 주입 ---


def test_transport_custom_response_format_overrides_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # response_format 인자로 OpenAI 의 다른 모드 (text) 를 강제할 수 있다.
    client = _FakeClient(response_content='{"answer": "ok"}')
    _install_fake_openai(monkeypatch, client)

    transport = build_openai_chat_transport(
        api_key="sk-test",
        response_format={"type": "text"},
    )
    transport(_payload())
    assert client.captured_kwargs is not None
    assert client.captured_kwargs["response_format"] == {"type": "text"}


# --- feature17c-25 문장별 인용 구조 강제 (Structured Outputs) ---


def test_grounded_citation_schema_matches_parser_keys() -> None:
    """스키마가 parse_llm_response 키(answer/sentences[].{text,citations}/unsupported_gaps)와
    정합하고 strict 구조 규칙(additionalProperties:false, 전 키 required)을 지킨다."""
    schema = GROUNDED_CITATION_RESPONSE_FORMAT["json_schema"]
    assert schema["strict"] is True
    root = schema["schema"]
    assert root["additionalProperties"] is False
    assert set(root["required"]) == {"answer", "sentences", "unsupported_gaps"}
    assert set(root["properties"]) == {"answer", "sentences", "unsupported_gaps"}
    sentence = root["properties"]["sentences"]["items"]
    # 문장 객체는 text + citations 를 모두 required 로 가지며 citations 는 문자열 배열
    assert sentence["additionalProperties"] is False
    assert set(sentence["required"]) == {"text", "citations"}
    citations = sentence["properties"]["citations"]
    assert citations["type"] == "array"
    assert citations["items"] == {"type": "string"}


def test_select_generator_response_format_toggle() -> None:
    # 토글 ON → Structured Outputs 스키마, OFF → None(기존 json_object 폴백)
    assert select_generator_response_format(True) is GROUNDED_CITATION_RESPONSE_FORMAT
    assert select_generator_response_format(False) is None


def test_transport_forwards_citation_schema(monkeypatch: pytest.MonkeyPatch) -> None:
    """citation 스키마를 주입하면 OpenAI create 호출에 그대로 전달된다."""
    client = _FakeClient(
        response_content=json.dumps(
            {
                "answer": "IAM 변경 절차.",
                "sentences": [{"text": "Jira 티켓 생성.", "citations": ["ctx-001", "ctx-003"]}],
                "unsupported_gaps": [],
            }
        )
    )
    _install_fake_openai(monkeypatch, client)

    transport = build_openai_chat_transport(
        api_key="sk-test",
        response_format=GROUNDED_CITATION_RESPONSE_FORMAT,
    )
    result = transport(_payload())
    assert client.captured_kwargs is not None
    assert client.captured_kwargs["response_format"] is GROUNDED_CITATION_RESPONSE_FORMAT
    # 다중 인용 응답이 그대로 파싱된다(파서는 transport 밖이지만 dict 통과 확인).
    assert result["sentences"][0]["citations"] == ["ctx-001", "ctx-003"]
