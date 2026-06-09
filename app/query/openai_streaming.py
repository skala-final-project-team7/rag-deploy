"""답변 생성기 — OpenAI Chat Completions streaming (plain text + [#N]) [Storage].

--------------------------------------------------
작성자 : 최태성
작성목적 : 설계서 §4.6.4 SSE 토큰 스트리밍 정합 — agent 의 JSON contract
          (``{answer, sentences, unsupported_gaps}``) 와 token streaming 사이
          의 충돌을 해소하기 위해 hybrid 방식 (Plan v2 §4.A) 의 streaming 경로
          를 분리한다. LLM 에게 plain text 답변을 요청하면서 각 문장에 ``[#N]``
          마커를 포함하도록 강제하고, OpenAI Streaming API 로 토큰을 chunk 단위
          로 yield 한다. 검증 1단계는 ``[#N]`` 마커 기반으로 동작하므로 plain text
          fallback (설계서 §4.6.5) 과 정합.
작성일 : 2026-05-19
변경사항 내역 (날짜, 변경목적, 변경내용 순)
  - 2026-05-19, 최초 작성, (A) Hybrid streaming — plain text + [#N] 마커 prompt
    + OpenAI streaming generator.
--------------------------------------------------
[호환성]
  - Python 3.11.x, openai>=1.30.
  - NOTE: 본 모듈은 [Storage] 분류 — 외부 streaming API 어댑터. agent 인프라
          (prompt builder, normalize_generation_input) 는 재사용하지만, 답변
          생성 contract 는 JSON 대신 plain text 로 변경된다. 검증 1단계는 답변
          텍스트의 ``[#N]`` 마커로 동작하므로 호환.
--------------------------------------------------
"""

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from app.schemas.chunk import Chunk

# Plain text streaming 모드의 system prompt — agent 의 JSON contract 와 다른 별도
# 지시문. 설계서 §4.6.1 의 "모든 문장에 [#N] 형식 명시" + §3 "정확성 우선" 정합.
_STREAMING_SYSTEM_PROMPT = (
    "당신은 RAG 파이프라인의 답변 생성기입니다.\n"
    "제공된 컨텍스트 청크에만 근거해 한국어로 답변하세요.\n"
    "모든 핵심 문장 끝에 근거 청크 번호를 [#1], [#2] 형식으로 명시하세요.\n"
    "여러 청크를 동시에 인용할 때는 [#1][#2] 처럼 이어 붙이세요.\n"
    "컨텍스트에 없는 사실은 단정하지 말고 '확인할 수 없습니다' 로 표시하세요.\n"
    "출력은 자연어 plain text 만 사용하고 JSON 이나 코드 블록으로 감싸지 마세요."
)


@dataclass(slots=True)
class StreamingTokenChunk:
    """Streaming 답변의 단일 token chunk — SSE 라우트가 그대로 송신한다."""

    text: str


def build_streaming_user_prompt(
    *,
    query: str,
    top_chunks: list[Chunk],
) -> str:
    """Streaming 모드 user prompt 를 조립한다.

    top_chunks 를 1-based 번호 (``[#N]``) 와 함께 컨텍스트 블록으로 합친다. 본 함수가
    번호 부여 정책의 단일 진입점 — manage_generator non-streaming 의
    ``_chunk_to_top_context_payload`` 와 다른 단순한 1-based 정수 매칭이지만, 검증
    1단계 (``verify_answer_rules``)가 ``[#N]`` 마커를 정수로 추출하므로 정합한다.
    """
    context_lines: list[str] = []
    for index, chunk in enumerate(top_chunks, start=1):
        metadata = chunk.metadata
        title = metadata.page_title or metadata.attachment_filename or metadata.chunk_id
        context_lines.append(f"[#{index}] {title} ({metadata.space_key})\n{chunk.text}")
    context_block = "\n\n".join(context_lines) if context_lines else "(컨텍스트 없음)"
    return f"질문: {query}\n\n컨텍스트:\n{context_block}\n\n답변:"


def stream_openai_answer(
    *,
    api_key: str,
    model: str,
    temperature: float,
    timeout_seconds: int,
    query: str,
    top_chunks: list[Chunk],
) -> Iterator[StreamingTokenChunk]:
    """OpenAI Chat Completions streaming 으로 답변 토큰을 yield 한다.

    설계서 §4.6.4 정합 — 첫 토큰부터 사용자에게 즉시 송신 가능하도록 OpenAI 의
    ``stream=True`` 모드를 사용한다. 본 generator 는 동기 (sync) iterator 이므로
    SSE 라우트는 별도 async wrapping 없이 ``for chunk in iterator`` 로 소비 가능.

    Args:
        api_key: OpenAI API key (외부 주입).
        model: 답변 생성 모델명 (예: ``gpt-4o``, ``gpt-4o-mini``).
        temperature: LLM temperature.
        timeout_seconds: 호출 타임아웃 (초).
        query: 사용자 질문.
        top_chunks: 검색·재순위화 결과 Top-K 청크.

    Yields:
        ``StreamingTokenChunk`` — 단일 토큰 또는 토큰 조각 (OpenAI delta).

    Raises:
        RuntimeError: top_chunks 가 비어 있는 경우 (호출자가 검색 0건 분기에서
            본 함수에 도달하지 않도록 가드해야 함).
    """
    if not top_chunks:
        raise RuntimeError("stream_openai_answer requires non-empty top_chunks")

    # lazy import — openai 없는 환경에서도 모듈 import 가 깨지지 않게.
    from openai import OpenAI

    client = OpenAI(api_key=api_key, timeout=float(timeout_seconds))
    user_prompt = build_streaming_user_prompt(query=query, top_chunks=top_chunks)
    stream = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": _STREAMING_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        temperature=temperature,
        stream=True,
    )
    for raw_chunk in stream:
        delta = _extract_delta_content(raw_chunk)
        if delta:
            yield StreamingTokenChunk(text=delta)


def _extract_delta_content(raw_chunk: Any) -> str:
    """OpenAI streaming chunk 객체에서 delta content 텍스트를 추출한다."""
    choices = getattr(raw_chunk, "choices", None)
    if not choices:
        return ""
    delta = getattr(choices[0], "delta", None)
    if delta is None:
        return ""
    return str(getattr(delta, "content", "") or "")


__all__ = [
    "StreamingTokenChunk",
    "build_streaming_user_prompt",
    "stream_openai_answer",
]
