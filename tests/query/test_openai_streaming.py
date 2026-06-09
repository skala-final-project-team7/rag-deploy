"""OpenAI Streaming generator + plain text prompt 빌더 검증 — (A) Hybrid streaming.

stream_openai_answer: OpenAI Chat Completions streaming 으로 token chunk 를 yield
하는 sync generator. 실제 OpenAI streaming 호출은 mock 으로 대체하고, prompt 합성
정합·token 누적·빈 top_chunks 가드 분기를 검증한다.
"""

from __future__ import annotations

import sys
import types
from collections.abc import Iterator
from datetime import datetime
from typing import Any

import pytest

from app.query.openai_streaming import (
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


class _FakeStreamingClient:
    """OpenAI client 대체 — chat.completions.create(stream=True) 만 받는 최소 스텁."""

    def __init__(self, *, tokens: list[str | None]) -> None:
        self._tokens = tokens
        self.captured_kwargs: dict[str, Any] | None = None
        self.chat = types.SimpleNamespace(completions=types.SimpleNamespace(create=self._create))

    def _create(self, **kwargs: Any) -> Iterator[_FakeStreamChunk]:
        self.captured_kwargs = kwargs
        return iter(_FakeStreamChunk(token) for token in self._tokens)


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
