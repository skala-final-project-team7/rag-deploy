"""OpenAI Streaming generator + plain text prompt 빌더 검증 — (A) Hybrid streaming.

stream_openai_answer: OpenAI Chat Completions streaming 으로 token chunk 를 yield
하는 sync generator. 실제 OpenAI streaming 호출은 mock 으로 대체하고, prompt 합성
정합·token 누적·빈 top_chunks 가드 분기를 검증한다.

2026-06-10 코드 리뷰 재점검(A7·P2-7) 회귀 포함:
  - try/finally 자원 정리 — 정상 소진·중도 close(GeneratorExit) 모두 stream.close() +
    client.close() 가 호출된다(fake 가 close 호출 여부를 기록).
  - ``conservative_guard=True`` 면 STREAMING_CONSERVATIVE_GUARD 가 system prompt 끝에
    덧붙는다(기본 False 는 무변경).
"""

from __future__ import annotations

import sys
import types
from collections.abc import Iterator
from datetime import datetime
from typing import Any

import pytest

from app.query.openai_streaming import (
    STREAMING_CONSERVATIVE_GUARD,
    StreamingTokenChunk,
    build_streaming_user_prompt,
    stream_openai_answer,
)
from app.schemas.chunk import Chunk, ChunkMetadata
from app.schemas.enums import DocType, SourceType


def _make_chunk(
    *,
    chunk_id: str = "chunk-1",
    page_title: str = "운영 가이드",
    text: str = "EKS 노드 장애 대응 절차는 다음과 같다.",
    space_key: str = "CLOUD",
) -> Chunk:
    metadata = ChunkMetadata(
        chunk_id=chunk_id,
        page_id="page-1",
        page_title=page_title,
        section_header="개요",
        section_path="개요",
        chunk_index=0,
        labels=["ops"],
        doc_type=DocType.OPERATION,
        space_key=space_key,
        allowed_groups=["space:CLOUD"],
        allowed_users=[],
        webui_link="https://confluence.example.com/page-1",
        last_modified=datetime(2026, 5, 1, 9, 0, 0),
        source_type=SourceType.PAGE,
        token_count=120,
    )
    return Chunk(text=text, metadata=metadata)


# --- prompt 빌더 ---


def test_user_prompt_includes_query_and_numbered_contexts() -> None:
    chunks = [
        _make_chunk(chunk_id="chunk-1", page_title="운영 가이드"),
        _make_chunk(chunk_id="chunk-2", page_title="장애 대응 가이드"),
    ]
    prompt = build_streaming_user_prompt(query="EKS 절차는?", top_chunks=chunks)
    assert "EKS 절차는?" in prompt
    # 1-based [#N] 매칭 — 검증 1단계 (verify_answer_rules) 와 정합.
    assert "[#1]" in prompt
    assert "[#2]" in prompt
    assert "운영 가이드" in prompt
    assert "장애 대응 가이드" in prompt


def test_user_prompt_handles_empty_chunks_safely() -> None:
    prompt = build_streaming_user_prompt(query="질문", top_chunks=[])
    assert "(컨텍스트 없음)" in prompt


def test_user_prompt_includes_chunk_text() -> None:
    chunks = [_make_chunk(text="alpha bravo charlie")]
    prompt = build_streaming_user_prompt(query="질문", top_chunks=chunks)
    assert "alpha bravo charlie" in prompt


# --- streaming generator ---


class _FakeDelta:
    def __init__(self, content: str | None) -> None:
        self.content = content


class _FakeStreamChoice:
    def __init__(self, content: str | None) -> None:
        self.delta = _FakeDelta(content)


class _FakeStreamChunk:
    def __init__(self, content: str | None) -> None:
        self.choices = [_FakeStreamChoice(content)]


class _FakeStream:
    """``create(stream=True)`` 반환 스트림 대체 — iteration + close() 호출 추적.

    2026-06-10(A7) — ``stream_openai_answer`` 가 try/finally 로 ``stream.close()`` 를
    호출하므로 fake 스트림도 close() 를 제공하고 호출 여부를 기록한다.
    """

    def __init__(self, tokens: list[str | None]) -> None:
        self._iterator: Iterator[_FakeStreamChunk] = iter(
            _FakeStreamChunk(token) for token in tokens
        )
        self.closed = False

    def __iter__(self) -> _FakeStream:
        return self

    def __next__(self) -> _FakeStreamChunk:
        return next(self._iterator)

    def close(self) -> None:
        self.closed = True


class _FakeStreamingClient:
    """OpenAI client 대체 — chat.completions.create(stream=True) + close() 최소 스텁."""

    def __init__(self, *, tokens: list[str | None]) -> None:
        self._tokens = tokens
        self.captured_kwargs: dict[str, Any] | None = None
        self.stream: _FakeStream | None = None
        self.closed = False
        self.chat = types.SimpleNamespace(completions=types.SimpleNamespace(create=self._create))

    def _create(self, **kwargs: Any) -> _FakeStream:
        self.captured_kwargs = kwargs
        self.stream = _FakeStream(self._tokens)
        return self.stream

    def close(self) -> None:
        self.closed = True


def _install_fake_openai_for_streaming(
    monkeypatch: pytest.MonkeyPatch, client: _FakeStreamingClient
) -> None:
    module = types.ModuleType("openai")
    module.OpenAI = lambda **kwargs: client  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "openai", module)


def test_streaming_yields_token_chunks(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _FakeStreamingClient(tokens=["답변 ", "시작 ", "[#1]"])
    _install_fake_openai_for_streaming(monkeypatch, client)

    tokens = list(
        stream_openai_answer(
            api_key="sk-test",
            model="gpt-4o",
            temperature=0.2,
            timeout_seconds=45,
            query="EKS 절차?",
            top_chunks=[_make_chunk()],
        )
    )
    assert tokens == [
        StreamingTokenChunk(text="답변 "),
        StreamingTokenChunk(text="시작 "),
        StreamingTokenChunk(text="[#1]"),
    ]
    # stream=True 가 OpenAI client 에 전달돼야 한다.
    assert client.captured_kwargs is not None
    assert client.captured_kwargs["stream"] is True


def test_streaming_skips_none_delta(monkeypatch: pytest.MonkeyPatch) -> None:
    # OpenAI chunk 마지막에는 delta.content=None 인 종료 chunk 가 올 수 있다 — skip.
    client = _FakeStreamingClient(tokens=["첫 토큰", None, "둘째 토큰", ""])
    _install_fake_openai_for_streaming(monkeypatch, client)

    tokens = list(
        stream_openai_answer(
            api_key="sk-test",
            model="gpt-4o",
            temperature=0.2,
            timeout_seconds=45,
            query="질문",
            top_chunks=[_make_chunk()],
        )
    )
    # None / 빈 문자열은 yield 되지 않는다.
    assert [t.text for t in tokens] == ["첫 토큰", "둘째 토큰"]


def test_streaming_requires_non_empty_top_chunks() -> None:
    # top_chunks 비면 RuntimeError — 호출자가 검색 0건 분기에서 가드해야 함.
    with pytest.raises(RuntimeError):
        list(
            stream_openai_answer(
                api_key="sk-test",
                model="gpt-4o",
                temperature=0.2,
                timeout_seconds=45,
                query="질문",
                top_chunks=[],
            )
        )


def test_streaming_passes_model_and_temperature(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _FakeStreamingClient(tokens=["ok"])
    _install_fake_openai_for_streaming(monkeypatch, client)

    list(
        stream_openai_answer(
            api_key="sk-test",
            model="gpt-4o-mini",
            temperature=0.3,
            timeout_seconds=30,
            query="질문",
            top_chunks=[_make_chunk()],
        )
    )
    assert client.captured_kwargs is not None
    assert client.captured_kwargs["model"] == "gpt-4o-mini"
    assert client.captured_kwargs["temperature"] == 0.3


def test_streaming_system_prompt_enforces_marker_rule(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # System prompt 가 [#N] 마커 규칙·plain text 출력·컨텍스트 외 단정 금지를 강제.
    client = _FakeStreamingClient(tokens=["ok"])
    _install_fake_openai_for_streaming(monkeypatch, client)

    list(
        stream_openai_answer(
            api_key="sk-test",
            model="gpt-4o",
            temperature=0.2,
            timeout_seconds=45,
            query="질문",
            top_chunks=[_make_chunk()],
        )
    )
    assert client.captured_kwargs is not None
    system_message = client.captured_kwargs["messages"][0]
    assert system_message["role"] == "system"
    assert "[#1]" in system_message["content"] or "[#" in system_message["content"]
    assert "plain text" in system_message["content"].lower()


# --- 2026-06-10 코드 리뷰(A7): try/finally 자원 정리 ---


def test_streaming_closes_stream_and_client_on_completion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """정상 소진 시 finally 가 stream.close() + client.close() 를 호출한다(A7)."""
    client = _FakeStreamingClient(tokens=["답변", "[#1]"])
    _install_fake_openai_for_streaming(monkeypatch, client)

    tokens = list(
        stream_openai_answer(
            api_key="sk-test",
            model="gpt-4o",
            temperature=0.2,
            timeout_seconds=45,
            query="질문",
            top_chunks=[_make_chunk()],
        )
    )
    assert [t.text for t in tokens] == ["답변", "[#1]"]
    assert client.stream is not None
    assert client.stream.closed is True
    assert client.closed is True


def test_streaming_closes_resources_on_early_generator_close(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """클라이언트 중도 disconnect 상당 — generator.close()(GeneratorExit) 경로에서도
    stream/client 가 정리된다(A7 — 커넥션 누수 방지)."""
    client = _FakeStreamingClient(tokens=["첫", "둘", "셋"])
    _install_fake_openai_for_streaming(monkeypatch, client)

    generator = stream_openai_answer(
        api_key="sk-test",
        model="gpt-4o",
        temperature=0.2,
        timeout_seconds=45,
        query="질문",
        top_chunks=[_make_chunk()],
    )
    assert next(generator).text == "첫"
    generator.close()  # 중도 종료 — finally 경로 강제.
    assert client.stream is not None
    assert client.stream.closed is True
    assert client.closed is True


# --- 2026-06-10 코드 리뷰(P2-7): conservative_guard 토글 ---


def test_conservative_guard_appends_streaming_guard_to_system_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """conservative_guard=True → STREAMING_CONSERVATIVE_GUARD 가 system prompt 끝에 덧붙는다."""
    client = _FakeStreamingClient(tokens=["ok"])
    _install_fake_openai_for_streaming(monkeypatch, client)

    list(
        stream_openai_answer(
            api_key="sk-test",
            model="gpt-4o",
            temperature=0.2,
            timeout_seconds=45,
            query="질문",
            top_chunks=[_make_chunk()],
            conservative_guard=True,
        )
    )
    assert client.captured_kwargs is not None
    system_content = client.captured_kwargs["messages"][0]["content"]
    assert system_content.endswith(STREAMING_CONSERVATIVE_GUARD)
    # 기본 프롬프트는 약화되지 않는다 — 마커 규칙 지시문이 그대로 선행한다.
    assert "[#1]" in system_content


def test_conservative_guard_off_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """기본(conservative_guard 미지정) 호출은 보수 지침을 덧붙이지 않는다(기존 동작 보존)."""
    client = _FakeStreamingClient(tokens=["ok"])
    _install_fake_openai_for_streaming(monkeypatch, client)

    list(
        stream_openai_answer(
            api_key="sk-test",
            model="gpt-4o",
            temperature=0.2,
            timeout_seconds=45,
            query="질문",
            top_chunks=[_make_chunk()],
        )
    )
    assert client.captured_kwargs is not None
    system_content = client.captured_kwargs["messages"][0]["content"]
    assert "보수성 강화 지침" not in system_content
