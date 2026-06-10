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
  - 2026-06-10, 코드 리뷰 재점검(A7·P2-7) 반영 — (1) try/finally 로 stream/client
    종료 보장(클라이언트 중도 disconnect 시 자원 누수 방지). (2) ``conservative_guard``
    파라미터 추가 — RAG_GENERATOR_CONSERVATIVE_GUARD 토글이 streaming 경로에도
    적용되도록 plain-text 계약 정합 보수 지침을 system prompt 에 덧붙인다.
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

# P2-7 — 보수성 강화 지침 (streaming / plain-text 계약 정합판).
# 비-streaming 경로의 ``openai_transport.CONSERVATIVE_SYSTEM_GUARD`` 와 동일 취지이나,
# 그쪽은 JSON contract(context_id / unsupported_gaps) 용어를 쓰므로 본 plain-text +
# ``[#N]`` 마커 계약에 맞게 다시 썼다. settings.generator_conservative_guard=True 일 때만
# system prompt 끝에 덧붙인다(기본 OFF — 기본 프롬프트를 약화하지 않고 강화만).
STREAMING_CONSERVATIVE_GUARD = "\n".join(
    [
        "[보수성 강화 지침]",
        "- 각 문장은 인용한 [#N] 청크의 내용에 명시적으로 등장하는 사실만 진술한다.",
        "- 컨텍스트에 없는 추론·일반 지식·배경 설명·권고를 답변 문장으로 추가하지 않는다.",
        "- 추측성 표현(아마도, ~일 수 있다, 일반적으로)을 사용하지 않는다.",
        "- 정확한 [#N] 인용을 달 수 없는 문장은 출력하지 않는다.",
    ]
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
    conservative_guard: bool = False,
) -> Iterator[StreamingTokenChunk]:
    """OpenAI Chat Completions streaming 으로 답변 토큰을 yield 한다.

    설계서 §4.6.4 정합 — 첫 토큰부터 사용자에게 즉시 송신 가능하도록 OpenAI 의
    ``stream=True`` 모드를 사용한다. 본 generator 는 동기 (sync) iterator 다 —
    async 컨텍스트(SSE 라우트)에서 직접 ``for`` 로 돌리면 이벤트 루프가 차단되므로
    호출자는 ``routes._iter_offloaded`` 같은 thread 오프로드 어댑터로 소비한다(A1).

    Args:
        api_key: OpenAI API key (외부 주입).
        model: 답변 생성 모델명 (예: ``gpt-4o``, ``gpt-4o-mini``).
        temperature: LLM temperature.
        timeout_seconds: 호출 타임아웃 (초).
        query: 사용자 질문.
        top_chunks: 검색·재순위화 결과 Top-K 청크.
        conservative_guard: True 면 ``STREAMING_CONSERVATIVE_GUARD`` 를 system
            prompt 끝에 덧붙인다(RAG_GENERATOR_CONSERVATIVE_GUARD 토글 — P2-7).

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

    system_prompt = _STREAMING_SYSTEM_PROMPT
    if conservative_guard:
        system_prompt = f"{system_prompt}\n\n{STREAMING_CONSERVATIVE_GUARD}"

    client = OpenAI(api_key=api_key, timeout=float(timeout_seconds))
    # try/finally — 정상 종료·중도 close(GeneratorExit)·상류 예외 모든 경로에서
    # stream 과 client 의 커넥션을 정리한다(A7 — 호출마다 생성하는 구조의 누수 방지).
    try:
        user_prompt = build_streaming_user_prompt(query=query, top_chunks=top_chunks)
        stream = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=temperature,
            stream=True,
        )
        try:
            for raw_chunk in stream:
                delta = _extract_delta_content(raw_chunk)
                if delta:
                    yield StreamingTokenChunk(text=delta)
        finally:
            stream.close()
    finally:
        client.close()


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
    "STREAMING_CONSERVATIVE_GUARD",
    "StreamingTokenChunk",
    "build_streaming_user_prompt",
    "stream_openai_answer",
]
