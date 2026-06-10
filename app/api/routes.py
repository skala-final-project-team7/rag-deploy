"""POST /ml/query — SSE 라우트 핸들러 [Pipeline].

--------------------------------------------------
작성자 : 최태성
작성목적 : feature11 통합 Phase 2 — Query 그래프(`app/pipeline/query_graph.py`)
          위에 얇은 HTTP 계층을 얹어 BFF가 호출하는 SSE 엔드포인트를 제공한다.
          BFF가 전달한 userId/groups 로 ACL 필터를 만들고 RagState 를 구성한 뒤
          run_query → SSE 송신까지 한 흐름으로 처리한다. api-spec.md "POST /ml/query"
          정합으로 token / sources / verification / done 이벤트(+ feature19 status)를
          송신한다.
작성일 : 2026-05-18
변경사항 내역 (날짜, 변경목적, 변경내용 순)
  - 2026-05-18, 최초 작성, feature11 통합 Phase 2 — query_route + SSE
    이벤트 생성기 + JWT/ACL 시스템 단 강제
  - 2026-05-19, feature14 SSE token streaming — ``QueryRequest.stream`` 분기.
  - 2026-05-19, feature15 streaming Rate Limit fallback — 설계서 §4.6.5.
  - 2026-05-19, feature17a — Rate Limit fallback Prometheus 카운터.
  - 2026-05-22, feature19 — SSE 진행 표시용 ``status`` 이벤트 *추가*. streaming
    경로에만 적용. phase 7종(connecting → acl_filtering → searching → answering
    → streaming → verifying → formatting).
  - 2026-05-26, feature13 코드 마이그레이션 — BE 통합 스펙(/ml/query) 정합:
    (1) 엔드포인트 ``/api/v1/rag/query`` → ``/ml/query`` 완전 전환.
    (2) 요청 본문 재정의 — ``question``/``userId``/``groups``/``spaceKey``/
        ``conversationId``/``history``/``stream``. JWT 미수신 → ``extract_principal``
        호출 제거, userId/groups 직접 사용. ``spaceKey`` 는 RagState 에 passthrough
        (검색 필터 반영은 후속). ``accessToken``/``cloudId`` 는 api-spec v2.2.0 에서
        ``/ml/query`` 가 아닌 수집 단계(``/ml/ingest``)로 이관됨 — 본 경로 미수신.
    (3) SSE 이벤트 형식 변경 — ``token``=``{"content": ...}``, ``sources``=
        ``{"sources": [...]}`` 래핑(relevanceScore 0~1 / sourceUpdatedAt KST / pageId·
        spaceId·spaceName), ``verification``=집계 ``{"confidenceScore",
        "verificationResult"}``, ``done``=``{}``. ``meta`` 이벤트는 api-spec v2.2.0
        정합으로 유지(intent/used_llm/feedback_enabled/latency_ms, title 은 ML 미생성
        으로 생략). 추후 BE 통합 목표 계약에서 제거 예정.
    (4) 오류는 HTTP 에러 JSON 대신 SSE ``error`` 이벤트로 전달하고 스트림 종료.
        RETRIEVAL_EMPTY/LOW_CONFIDENCE/VERIFICATION_BLOCKED 는 종전대로 200 SSE
        내부 분기로 처리한다.
  - 2026-06-04, 명세 정합 — 최종 ``/ml/query`` 요청 계약 반영: (1) ``spaceKey`` 요청 필드
    제거(RagState passthrough·search_node 하드 스코프 함께 제거 — 검색 스코프는 라우터
    추정 ``metadata_filters`` 로만). (2) ``stream`` 요청 필드 재도입 — 클라이언트가 스트리밍
    여부를 제어하고, ``stream=False`` 면 단일 ``token`` 1회 송신. ``accessToken``/``cloudId``
    는 수신하지 않는다(수집 단계 이관 — 종전과 동일).
  - 2026-06-04, **api-spec v2.4.0 정본 정합** — 업로드된 LINA API Spec v2.4.0 기준 재정렬:
    (1) ``stream`` 기본값 True → **False**(§2-1 표 "기본 false, BFF 는 항상 true"). (2)
    ``history[].role`` 정규화 UPPER → **lowercase**(``user``/``assistant`` — Enum 정책의 명시적
    예외, boundary 변환 없음 — `app/schemas/rag_state.py`).
  - 2026-06-10, 코드 리뷰 재점검(A1·A14·A15·P2-7) 반영 — (1) 그래프 invoke/OpenAI
    streaming/검증/제목 생성 등 동기 블로킹 호출을 ``asyncio.to_thread`` 로 오프로드
    (이벤트 루프 비차단 — 동시 SSE/healthz/keep-alive 보호). (2) 비-streaming 경로에도
    ``status`` 이벤트 송신(스펙 §1-1 불변식 #1). (3) SSE ``error.message`` 를 errorCode 별
    고정 안내 문구로 통일(내부 예외 원문은 서버 로그 전용). (4) 보수 가드 토글을
    streaming 경로에도 전달(``openai_streaming`` 의 plain-text 정합 가드).
  - 2026-06-10, **A5 — backend-template 정합(append-only 토큰 확정)**. BFF ChatService
    (backend-template@eafd6b3)가 token.content 를 무조건 이어 붙여 영속함을 확인 —
    "빈 token=클리어"·"차단문 token 재전송=덮어쓰기" 시맨틱은 BFF/FE 에 존재하지 않아
    (1) Rate Limit fallback 의 빈 clear token 제거, 부분 답변이 이미 송신된 경우 가시적
    구분 안내문 token 을 1회 송신 후 fallback 답변을 이어 보낸다. (2) 차단 분기의
    token 재전송 제거 — 차단 신호는 ``verification`` 이벤트(NOT_SUPPORTED·낮은
    confidenceScore)로 전달되고 BFF 가 그대로 영속한다(렌더링 정책은 FE 소관).
    비-streaming 경로는 종전대로 차단 안내문이 단일 token 으로 나간다(검증 후 송신).
--------------------------------------------------
[호환성]
  - Python 3.11.x, FastAPI 0.111+, sse-starlette 2.1+
--------------------------------------------------
"""

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator, Iterable, Iterator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from sse_starlette.sse import EventSourceResponse

from app.api.errors import ErrorCode
from app.metrics import llm_fallback_total
from app.pipeline.nodes import verify_pipeline_node
from app.pipeline.query_graph import run_query
from app.query.acl import ACLViolationError, build_acl_filter
from app.query.formatter import format_response
from app.query.openai_streaming import stream_openai_answer
from app.query.titler import fallback_title, generate_conversation_title
from app.schemas.enums import Intent, LlmModel
from app.schemas.rag_state import HistoryTurn, RagState
from app.schemas.response import QueryResponse, VerificationSummary

_LOGGER = logging.getLogger(__name__)

router = APIRouter()


class QueryRequest(BaseModel):
    """``POST /ml/query`` 요청 본문 (docs/api-spec.md, BE 통합 스펙 §2-1).

    BFF 는 camelCase JSON 을 보낸다(``userId``/``conversationId`` 등).
    ``populate_by_name=True`` 로 snake_case 필드명 입력도 허용한다(테스트 편의).
    """

    model_config = ConfigDict(populate_by_name=True)

    question: str = Field(..., min_length=1, description="사용자 자연어 질문")
    user_id: str = Field(
        ..., min_length=1, alias="userId", description="ACL Pre-filtering 사용자 식별자"
    )
    groups: list[str] = Field(default_factory=list, description="사용자 그룹 — ACL should-OR 필터")
    conversation_id: str | None = Field(
        default=None, alias="conversationId", description="대화 컨텍스트 ID"
    )
    history: list[HistoryTurn] = Field(
        default_factory=list, description="이전 대화 이력 [{role, content}] (BFF가 DB에서 조회)"
    )
    # 명세 v2.4.0 §2-1 — 클라이언트가 SSE 토큰 스트리밍 여부를 제어한다(**기본 False**, BFF 는
    # 항상 True 로 호출). True 면 토큰을 다중 송신하고, False 면 답변을 단일 ``token`` 이벤트로
    # 1회 송신한다(어느 쪽이든 응답은 SSE). 단, ``stream=True`` 라도 PoC 환경(OpenAI 키/
    # generator_provider 없음)이면 서버가 내부적으로 비-streaming 으로 자동 fallback 한다.
    stream: bool = Field(
        default=False, description="SSE 토큰 스트리밍 여부(기본 false, BFF는 항상 true)"
    )


def get_graph(request: Request) -> Any:
    """FastAPI Depends — lifespan에서 만든 컴파일된 그래프를 반환한다.

    테스트에서는 ``app.dependency_overrides[get_graph] = lambda: test_graph`` 로
    교체할 수 있다.
    """
    return request.app.state.graph


# FastAPI Annotated 패턴 — Depends를 함수 인자 기본값으로 쓰는 B008 회피.
GraphDep = Annotated[Any, Depends(get_graph)]


def _token_event(text: str) -> dict[str, str]:
    """답변 텍스트 → SSE ``token`` 이벤트. data 는 ``{"content": "<텍스트>"}`` JSON."""
    return {"event": "token", "data": json.dumps({"content": text}, ensure_ascii=False)}


def _error_event(code: ErrorCode, message: str) -> dict[str, str]:
    """SSE ``error`` 이벤트. data 는 ``{"errorCode", "message"}`` JSON (api-spec v2.2.0 §1-1).

    필드명은 ``errorCode`` 다 — 공통 Wrapper 의 정수 ``code`` 와 혼동 금지(SSE 에는 HTTP
    정수 code 가 없다). 값은 ``ErrorCode`` 의 ML_* 3종(ML_SERVER_ERROR / ML_TIMEOUT /
    ML_CONNECTION_ERROR) 중 하나이며 BFF 가 그대로 FE 로 중계한다(§2-1).
    """
    payload = {"errorCode": code.value, "message": message}
    return {"event": "error", "data": json.dumps(payload, ensure_ascii=False)}


# A5 — Rate Limit fallback 시 부분 답변과 재생성 답변 사이에 끼워 보내는 구분 안내문.
# BFF/FE 는 token 을 append-only 누적하므로(backend-template ChatService 확인) 이 문구가
# 화면·영속 본문에 그대로 포함된다 — 자연스러운 한국어 연결문으로 유지한다.
_FALLBACK_RESTART_NOTICE = "\n\n(일시적인 응답 한도로 답변을 처음부터 다시 생성합니다)\n\n"

# SSE error.message 는 사용자 노출용 고정 안내 문구만 보낸다(BFF→FE passthrough — §2-1).
# 내부 예외 원문(상류 응답 본문·내부 URL·request-id 등 포함 가능)은 서버 로그 전용.
_ERROR_MESSAGES: dict[ErrorCode, str] = {
    ErrorCode.ML_SERVER_ERROR: "답변 생성 중 오류가 발생했습니다",
    ErrorCode.ML_TIMEOUT: "답변 생성이 제한 시간 내에 완료되지 않았습니다",
    ErrorCode.ML_CONNECTION_ERROR: "답변 생성 서비스에 연결하지 못했습니다",
}


def _error_event_for(exc: Exception) -> dict[str, str]:
    """예외 → 분류된 errorCode + 고정 안내 문구의 SSE ``error`` 이벤트."""
    code = _classify_ml_error(exc)
    return _error_event(code, _ERROR_MESSAGES[code])


async def _iter_offloaded(iterable: Iterable[Any]) -> AsyncIterator[Any]:
    """동기 iterator 를 worker thread 에서 한 항목씩 소비하는 비동기 어댑터.

    OpenAI streaming 처럼 항목마다 네트워크 대기가 있는 동기 iterator 를 async
    제너레이터 안에서 직접 ``for`` 로 돌리면 이벤트 루프가 차단된다(코드 리뷰 A1).
    ``next()`` 호출을 ``asyncio.to_thread`` 로 오프로드해 대기 중에도 다른 코루틴
    (다른 SSE 스트림·healthz·keep-alive ping)이 진행되게 한다. 클라이언트 중도
    disconnect(GeneratorExit) 시에도 finally 에서 원본 iterator 를 close 해
    업스트림 자원(HTTP 스트림)을 정리한다.
    """
    iterator: Iterator[Any] = iter(iterable)
    sentinel = object()
    try:
        while True:
            item = await asyncio.to_thread(next, iterator, sentinel)
            if item is sentinel:
                return
            yield item
    finally:
        close = getattr(iterator, "close", None)
        if close is not None:
            await asyncio.to_thread(close)


def _classify_ml_error(exc: Exception) -> ErrorCode:
    """상류 예외를 api-spec §1-1 SSE 에러 코드(ML_* 3종) 로 분류한다.

    openai 패키지를 import 하지 않고 예외 타입/클래스명으로 판별한다 — PoC(openai
    미설치) 환경에서도 동작. ``APITimeoutError`` 는 이름에 "Timeout", ``APIConnectionError``
    는 "Connection" 을 포함하므로 표준 ``TimeoutError`` / ``ConnectionError`` 와 함께 잡힌다.

    Returns:
        ML_TIMEOUT(타임아웃) / ML_CONNECTION_ERROR(연결 실패) / ML_SERVER_ERROR(그 외 내부 오류).
    """
    name = type(exc).__name__
    if isinstance(exc, TimeoutError) or "Timeout" in name:
        return ErrorCode.ML_TIMEOUT
    if isinstance(exc, ConnectionError) or "Connection" in name:
        return ErrorCode.ML_CONNECTION_ERROR
    return ErrorCode.ML_SERVER_ERROR


def _sse_payload(response: QueryResponse) -> list[dict[str, str]]:
    """QueryResponse → SSE 이벤트 시퀀스 (api-spec.md "SSE 이벤트 순서").

    이벤트 순서 (api-spec v2.2.0 §1-1 정합):
        1. ``token`` — 답변 텍스트. data=``{"content": ...}``. 비-streaming은 1회(전체 답변).
        2. ``sources`` — 출처 카드 배열. data=``{"sources": [...]}``. 0건이면 빈 배열 1회.
        3. ``verification`` — 집계 검증 결과 ``{"confidenceScore", "verificationResult"}``.
           **검색 0건(RETRIEVAL_EMPTY)이면 생략한다**(스펙 §1-1 "0건 처리" — 검증할 근거 없음).
           0건은 검증 단계를 수행하지 않아 ``verification`` 목록이 비므로 이를 신호로 쓴다.
        4. ``meta`` — 현재 구현 호환용 메타데이터(intent/used_llm/feedback_enabled/latency_ms
           + title). ``title`` 은 None 이면 생략(스펙상 optional, Required: N). 추후 제거 예정.
        5. ``done`` — 종료 마커. data=``{}`` (messageId는 BFF가 주입).
    """
    sources_payload = {"sources": [source.to_bff_payload() for source in response.sources]}
    meta_payload: dict[str, Any] = {
        "intent": response.intent.value,
        "used_llm": response.used_llm.value,
        "feedback_enabled": response.feedback_enabled,
        "latency_ms": response.latency_ms,
    }
    # title(Required: N) — 생성된 경우에만 포함하고 스펙 예시 순서대로 맨 뒤에 둔다.
    if response.title:
        meta_payload["title"] = response.title
    events: list[dict[str, str]] = [
        _token_event(response.answer),
        {"event": "sources", "data": json.dumps(sources_payload, ensure_ascii=False)},
    ]
    # 검색 0건(RETRIEVAL_EMPTY)에서는 검증을 수행하지 않아 verification 목록이 비며,
    # 이때 verification 이벤트를 생략한다(스펙 §1-1 "0건 처리" — 검증할 근거 없음).
    # 검증 문장이 하나라도 있으면 집계해 1회 송신한다.
    if response.verification:
        verification_payload = VerificationSummary.from_sentences(
            response.verification
        ).to_bff_payload()
        events.append(
            {
                "event": "verification",
                "data": json.dumps(verification_payload, ensure_ascii=False),
            }
        )
    events.append({"event": "meta", "data": json.dumps(meta_payload, ensure_ascii=False)})
    events.append({"event": "done", "data": json.dumps({})})
    return events


def _resolve_title(request: Request, *, question: str, answer: str) -> str:
    """meta.title 로 쓸 대화 제목을 만든다 (api-spec v2.2.0 §1-1, Required: N).

    실 LLM 이 가용하면(PoC fallback 조건이 아니면) GPT-4o-mini(보조 모델)로 생성하고,
    그렇지 않거나 호출이 실패하면 질문 앞부분에서 결정론적 fallback 제목을 만든다.
    ``generate_conversation_title`` 은 모듈 전역으로 참조하므로 테스트에서 monkeypatch
    할 수 있다. 어떤 경우에도 예외를 밖으로 내보내지 않는다(제목 실패가 답변 스트림을
    깨뜨리면 안 됨 — title 은 optional).
    """
    if _should_fallback_to_non_streaming(request):
        return fallback_title(question)
    settings = request.app.state.settings
    try:
        return generate_conversation_title(
            question=question,
            answer=answer,
            api_key=settings.openai_api_key.get_secret_value(),
            model=settings.llm_aux_model,
        )
    except Exception:  # noqa: BLE001 — 제목 생성 실패는 fallback 으로 흡수(스트림 보호).
        _LOGGER.warning("title generation failed; using fallback", exc_info=True)
        return fallback_title(question)


async def _non_streaming_event_stream(
    *, request: Request, state: RagState, graph: Any
) -> AsyncIterator[dict[str, str]]:
    """비-streaming SSE 흐름 — run_query 실행 후 token/sources/(verification)/meta/done 송신.

    그래프/상류 예외는 HTTP 에러가 아니라 SSE ``error`` 이벤트로 전달하고 종료한다
    (api-spec v2.2.0 §1-1 오류 처리 정합 — errorCode 는 ML_* 3종으로 분류).
    스펙 §1-1 불변식 #1(phase 진입 시 status 1회) 정합 — 단일 invoke 구조라 그래프
    내부 단계를 개별 추적하지 않고 connecting/acl_filtering/searching 을 invoke 직전,
    formatting 을 직후에 송신한다(streaming 경로의 "단일 phase 통합" 절충과 동일).
    """
    yield _status_event("connecting")
    yield _status_event("acl_filtering")
    yield _status_event("searching")
    try:
        # run_query 는 검색·생성·검증까지 수행하는 동기 블로킹 호출 — 이벤트 루프를
        # 막지 않도록 worker thread 로 오프로드한다(코드 리뷰 A1).
        response = await asyncio.to_thread(run_query, state, graph=graph)
    except ACLViolationError:
        # 시스템 단 안전망 — build_acl_filter는 항상 유효 필터를 만들므로 정상 흐름에선
        # 도달하지 않지만, 그래프 내부 버그/우회 시 ACL 위반이 표면화되어야 한다.
        # 내부 상세는 로그 전용 — 클라이언트에는 고정 문구만 보낸다(코드 리뷰 A15).
        _LOGGER.exception("ACL violation surfaced in non-streaming query")
        yield _error_event(
            ErrorCode.ML_SERVER_ERROR, "접근 권한 처리 중 오류가 발생했습니다"
        )
        return
    except Exception as exc:  # noqa: BLE001 — 상류 LLM/네트워크 예외 광범위 캐치 (PoC)
        _LOGGER.exception("non-streaming query failed")
        yield _error_event_for(exc)
        return
    yield _status_event("formatting")
    # meta.title — 답변 산출 후 제목 생성(실패 시 fallback). api-spec §1-1 Required: N.
    # 제목 생성도 OpenAI 동기 호출(최대 10s) — 동일하게 오프로드.
    response.title = await asyncio.to_thread(
        _resolve_title, request, question=state.query, answer=response.answer or ""
    )
    for event in _sse_payload(response):
        yield event


def _resolve_used_llm(model: str) -> LlmModel:
    """generator_config.model 문자열을 LlmModel enum 으로 안전 변환.

    enum 에 없는 모델명 (예: ``gpt-4o-2024-05-13``) 이면 GPT_4O 로 fallback. 내부 메트릭
    정합용 — 응답 객체 스키마(``used_llm: LlmModel``)를 강제하기 위한 단순 정합.
    """
    try:
        return LlmModel(model)
    except ValueError:
        return LlmModel.GPT_4O


def _should_fallback_to_non_streaming(request: Request) -> bool:
    """stream=True 가 들어와도 PoC 환경이면 비-streaming 으로 자동 fallback.

    fallback 조건 (OR):
      - ``app.state.deps.generator_provider`` 가 None — PoC 경로는 fake provider 자동
        주입이라 OpenAI streaming 호출 자체가 불가능.
      - ``app.state.deps.generator_config`` 가 None — streaming 분기가 모델/온도/타임아웃을
        generator_config 에서 읽으므로 없으면 streaming 진입 불가(외부 사용자 정의
        generator_node 만 주입한 경우).
      - ``app.state.settings.openai_api_key`` 가 빈 SecretStr — 키 없이는 호출 실패.

    Returns:
        True 면 stream=True 무시하고 기존 run_query 흐름으로 처리해야 한다.
    """
    deps = getattr(request.app.state, "deps", None)
    if deps is None or getattr(deps, "generator_provider", None) is None:
        return True
    if getattr(deps, "generator_config", None) is None:
        return True
    settings = getattr(request.app.state, "settings", None)
    if settings is None:
        return True
    api_key_value = settings.openai_api_key.get_secret_value()
    return not api_key_value


# feature19 — SSE 진행 표시용 ``status`` 이벤트.
# 핵심 이벤트(token/sources/verification/done)와 별개로, RAG 라이프사이클 단계 진입 시
# 진행 phase 를 1회 push 한다. status 를 무시하는 클라이언트도 그대로 동작한다(추가 전용).
# streaming/비-streaming 양 경로 모두 송신한다(스펙 §1-1 불변식 #1 — 코드 리뷰 A14).
# 비-streaming 은 단일 invoke 구조라 connecting/acl_filtering/searching → (invoke) →
# formatting 의 4종으로 축약 송신한다(그래프 내부 단계 미추적 절충).
# done/error 는 핵심 done 이벤트 + SSE error 이벤트로 표현하며 status 로는 만들지 않는다.
_STATUS_MESSAGES: dict[str, str] = {
    "connecting": "연결 중이에요",
    "acl_filtering": "접근 권한을 확인하고 있어요",
    "searching": "관련 문서를 검색하고 있어요",
    "answering": "답변을 준비하고 있어요",
    "streaming": "답변을 작성하고 있어요",
    "verifying": "답변 근거를 검증하고 있어요",
    "formatting": "답변을 정리하고 있어요",
}


def _status_event(phase: str) -> dict[str, str]:
    """진행 phase → SSE ``status`` 이벤트 dict.

    ``data`` 는 다른 JSON 이벤트와 동일하게 ``json.dumps(..., ensure_ascii=False)`` 로
    직렬화한 ``{"phase": "<phase>", "message": "<한국어 메시지>"}`` 객체다.
    """
    payload = {"phase": phase, "message": _STATUS_MESSAGES[phase]}
    return {"event": "status", "data": json.dumps(payload, ensure_ascii=False)}


async def _streaming_event_stream(
    *,
    request: Request,
    state: RagState,
) -> AsyncIterator[dict[str, str]]:
    """SSE 토큰 스트리밍 흐름 (설계서 §4.6.4) — 상류 예외를 SSE error 이벤트로 흡수."""
    try:
        async for event in _streaming_event_stream_inner(request=request, state=state):
            yield event
    except Exception as exc:  # noqa: BLE001 — 상류 LLM/네트워크 예외 광범위 캐치 (PoC)
        # 내부 예외 원문은 로그 전용 — 클라이언트에는 분류된 errorCode + 고정 문구만(A15).
        _LOGGER.exception("streaming query failed")
        yield _error_event_for(exc)


async def _streaming_event_stream_inner(
    *,
    request: Request,
    state: RagState,
) -> AsyncIterator[dict[str, str]]:
    """SSE 토큰 스트리밍 본문.

    1. ``app.state.streaming_graph`` 로 history → router → search → (empty | rerank)
       까지 실행 → RagState 갱신 (top_chunks + sources 채워짐).
    2. RETRIEVAL_EMPTY 분기 (top_chunks 비어 있고 answer 가 표준 메시지) → 기존
       _sse_payload 로 token 1회 + sources/verification/done 송신.
    3. rerank 결과 있음 → ``stream_openai_answer`` 호출, token chunk 다중 yield.
    4. 누적 답변에 대해 ``verify_pipeline_node`` (1+2단계) 호출 후 ``format_response``
       로 저신뢰/차단 분기 적용 → sources/verification/done 송신.
    """
    started = time.perf_counter_ns()

    # feature19 status — connecting / acl_filtering.
    yield _status_event("connecting")
    yield _status_event("acl_filtering")

    streaming_graph = request.app.state.streaming_graph
    # feature19 status — searching. 그래프 내부 history/router/search/rerank 4단계를
    # 절충안으로 단일 phase 하나로 통합한다(astream 전환 없이 invoke 직전 1회 송신).
    yield _status_event("searching")
    # 그래프 invoke 는 임베딩·Qdrant 검색·rerank 까지 수행하는 동기 블로킹 호출 —
    # worker thread 로 오프로드해 이벤트 루프를 보호한다(코드 리뷰 A1).
    result_dict = await asyncio.to_thread(streaming_graph.invoke, state)
    rerank_state = RagState.model_validate(result_dict)

    intent = rerank_state.intent or Intent.OPERATION_GUIDE
    settings = request.app.state.settings
    deps = request.app.state.deps

    # 검색 0건 분기 — empty_retrieval 노드가 answer/used_llm/intent 를 채워준다.
    # answering/streaming/verifying 를 건너뛰고 formatting 으로 직행한다(feature19).
    if not rerank_state.top_chunks:
        used_llm = rerank_state.used_llm or LlmModel.GPT_4O_MINI
        yield _status_event("formatting")
        elapsed_ms = (time.perf_counter_ns() - started) // 1_000_000
        response = format_response(
            answer=rerank_state.answer or "",
            sources=rerank_state.sources,
            verification=rerank_state.verification,
            intent=intent,
            used_llm=used_llm,
            latency_ms=int(elapsed_ms),
        )
        # meta.title — 0건 분기도 제목을 채운다(질문 기반). api-spec §1-1 Required: N.
        response.title = await asyncio.to_thread(
            _resolve_title, request, question=state.query, answer=response.answer or ""
        )
        for event in _sse_payload(response):
            yield event
        return

    # rerank 분기 — OpenAI streaming 으로 token chunk 다중 송신.
    api_key = settings.openai_api_key.get_secret_value()
    generator_config = deps.generator_config
    # generator_config 가 None 이면 (외부 사용자 정의 generator_node 만 주입한 경우)
    # _should_fallback_to_non_streaming 단계에서 이미 걸러져야 한다 — 본 분기 도달 시
    # generator_config 는 반드시 존재한다고 가정한다.
    primary_model = generator_config.model
    fallback_model = generator_config.fallback_model
    temperature = generator_config.temperature
    timeout_seconds = generator_config.timeout_seconds
    # 답변 생성 입력 질의 — 비-streaming generator(manage_generator)와 동일하게 히스토리
    # 관리자가 만든 contextualized_question 을 우선 사용하고, 없으면 원문 query 로 fallback.
    # 후속 질문에서 스트리밍/비-streaming 답변이 같은 질의로 생성되도록 정합한다
    # (generator.py `_build_generation_input_payload` 의 contextualized_query 와 동일 규칙).
    answer_query = (
        rerank_state.history_decision.contextualized_question
        if rerank_state.history_decision is not None
        and rerank_state.history_decision.contextualized_question
        else state.query
    )

    # lazy import — openai 없는 환경 (PoC) 에서도 모듈 로드 가능. 본 분기는 운영
    # 모드에서만 도달.
    from openai import RateLimitError

    # feature19 status — answering. 프롬프트 구성 / stream_openai_answer 호출 직전.
    yield _status_event("answering")

    # P2-7 — 보수 가드 토글을 streaming 경로에도 적용(비-streaming 의
    # CONSERVATIVE_SYSTEM_GUARD 와 동일 취지, plain-text 계약 정합 문구).
    conservative_guard = bool(settings.generator_conservative_guard)

    accumulated_tokens: list[str] = []
    used_model = primary_model
    # feature19 status — streaming. 첫 token chunk 송신 직전 1회만 송신(fallback 재시도
    # 시에도 중복 송신하지 않도록 플래그로 한 번만 보낸다).
    streaming_status_sent = False
    try:
        # 동기 OpenAI streaming iterator 는 _iter_offloaded 로 항목별 thread 오프로드(A1).
        async for token_chunk in _iter_offloaded(
            stream_openai_answer(
                api_key=api_key,
                model=primary_model,
                temperature=temperature,
                timeout_seconds=timeout_seconds,
                query=answer_query,
                top_chunks=rerank_state.top_chunks,
                conservative_guard=conservative_guard,
            )
        ):
            if not streaming_status_sent:
                yield _status_event("streaming")
                streaming_status_sent = True
            accumulated_tokens.append(token_chunk.text)
            yield _token_event(token_chunk.text)
    except RateLimitError:
        # 설계서 §4.6.5 — 429 시 fallback_model 로 1회 재시도.
        _LOGGER.warning(
            "answer streaming rate-limited, falling back to fallback_model=%s",
            fallback_model,
        )
        # feature17a — Prometheus 카운터로 streaming 경로 Rate Limit fallback 빈도 가시화.
        llm_fallback_total.labels(
            from_model=primary_model,
            to_model=fallback_model,
            reason="rate_limit_error",
        ).inc()
        if accumulated_tokens:
            # A5 — BFF(backend-template ChatService)는 token.content 를 append-only 로
            # 누적·영속한다(클리어/덮어쓰기 시맨틱 없음). 따라서 빈 token 으로 clear 를
            # 기대하지 않고, 이미 송신된 부분 답변과 fallback 재생성 답변 사이에 가시적
            # 구분 안내문을 1회 송신한다(FE 화면·BFF 영속 모두 자연스러운 연결문이 됨).
            # 내부 accumulated_tokens 는 비워 검증/제목은 fallback 답변만 대상으로 한다.
            accumulated_tokens.clear()
            yield _token_event(_FALLBACK_RESTART_NOTICE)
        used_model = fallback_model
        async for token_chunk in _iter_offloaded(
            stream_openai_answer(
                api_key=api_key,
                model=fallback_model,
                temperature=temperature,
                timeout_seconds=timeout_seconds,
                query=answer_query,
                top_chunks=rerank_state.top_chunks,
                conservative_guard=conservative_guard,
            )
        ):
            if not streaming_status_sent:
                yield _status_event("streaming")
                streaming_status_sent = True
            accumulated_tokens.append(token_chunk.text)
            yield _token_event(token_chunk.text)

    answer = "".join(accumulated_tokens)
    rerank_state.answer = answer
    rerank_state.used_llm = _resolve_used_llm(used_model)

    # 검증 1+2단계 — verify_pipeline_node 에 deps 의 verify_llm_evaluator 주입.
    # api-spec v2.2.0 §1-1 이벤트 순서 불변식 #2 — 모든 ``token`` 은 ``verifying`` phase
    # 이전에 와야 한다(verifying 이후 token 금지). 따라서 검증/포맷팅으로 답변이 차단·대체될
    # 수 있는지 먼저 판정한 뒤, 대체 token 을 verifying status 송신보다 앞서 내보낸다.
    # status 는 진행 표시용이라 실제 검증 연산 직후에 보내도 무방하다(검색 4단계도 단일
    # ``searching`` phase 로 묶어 송신하는 것과 동일한 절충).
    # 검증 2단계는 OpenAI evaluator 동기 호출을 포함 — 동일하게 오프로드(A1).
    await asyncio.to_thread(
        verify_pipeline_node, rerank_state, llm_evaluator=deps.verify_llm_evaluator
    )

    elapsed_ms = (time.perf_counter_ns() - started) // 1_000_000
    response = format_response(
        answer=rerank_state.answer,
        sources=rerank_state.sources,
        verification=rerank_state.verification,
        intent=intent,
        used_llm=rerank_state.used_llm,
        latency_ms=int(elapsed_ms),
    )
    # meta.title — 스트리밍된 실제 답변을 맥락으로 제목 생성. api-spec §1-1 Required: N.
    response.title = await asyncio.to_thread(
        _resolve_title, request, question=state.query, answer=answer
    )
    # A5 — 차단 분기여도 token 재전송을 하지 않는다. BFF(backend-template ChatService)는
    # token 을 append-only 로 누적·영속하므로 재전송 시 "원본 답변+차단문" 연결문이
    # 그대로 저장·표시된다(덮어쓰기 시맨틱 없음 — 확인: 2026-06-10). 차단 신호는 바로
    # 아래 verification 이벤트(NOT_SUPPORTED + 낮은 confidenceScore)로 전달되며 BFF 가
    # confidence/verificationResult 를 영속한다. 비-streaming 경로는 검증 후 단일 token
    # 이라 종전대로 차단 안내문(BLOCKED_ANSWER_MESSAGE)이 답변 본문으로 나간다.
    # feature19 status — verifying → formatting (검증은 위에서 이미 수행, status 는 표시용).
    yield _status_event("verifying")
    yield _status_event("formatting")
    # token 이벤트는 이미 송신했으므로 sources / verification / done 만 송신.
    for event in _sse_payload(response)[1:]:
        yield event


@router.post("/ml/query")
async def query_route(payload: QueryRequest, request: Request, graph: GraphDep) -> Any:
    """사용자 질의를 받아 ACL 기반 검색·답변·검증을 수행하고 SSE로 응답한다.

    docs/api-spec.md "POST /ml/query" 정합:
      1. BFF가 전달한 ``userId``/``groups`` 로 ``build_acl_filter`` (Qdrant should-OR).
      2. ``RagState`` 구성(question/userId/groups/conversationId/history) 후 ``stream``
         요청 필드에 따라 SSE 로 응답한다:
         - ``stream=True`` + 운영(OpenAI 가용): ``_streaming_event_stream`` 으로 token 다중 송신.
         - ``stream=False`` 또는 PoC(OpenAI 키/generator_provider 없음): ``run_query``
           비-streaming 흐름(token 1회). 외부 SSE 이벤트 계약은 동일.
      3. 오류는 SSE ``error`` 이벤트로 전달하고 스트림을 종료한다.
    """
    state = RagState(
        query=payload.question,
        user_id=payload.user_id,
        groups=payload.groups,
        conversation_id=payload.conversation_id,
        history=payload.history,
        acl_filter=build_acl_filter(payload.user_id, payload.groups),
    )

    # api-spec v2.2.0 §1-1 — 항상 SSE 스트리밍. 단, PoC 환경(OpenAI 키 / generator_provider
    # 없음)이면 서버 내부적으로 비-streaming 흐름으로 자동 fallback 한다(외부 SSE 이벤트
    # 계약은 동일). SSE 응답 헤더는 스펙 §1-1 "연결·타임아웃" 정합 — Cache-Control: no-cache
    # (sse-starlette 기본 no-store 를 명시 override) + X-Accel-Buffering: no(프록시 버퍼링 비활성).
    sse_headers = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    if payload.stream and not _should_fallback_to_non_streaming(request):
        return EventSourceResponse(
            _streaming_event_stream(request=request, state=state),
            headers=sse_headers,
        )

    return EventSourceResponse(
        _non_streaming_event_stream(request=request, state=state, graph=graph),
        headers=sse_headers,
    )


@router.get("/ml/rag/health")
async def rag_health() -> dict[str, str]:
    """RAG Pipeline 헬스체크 (api-spec v2.2.0 §2-4-1).

    BFF 가 RAG Pipeline 서버(질의/응답 생성)가 정상 응답 가능한지 확인하는 용도.
    내부 의존성(Vector DB / LLM 등) 상세 상태는 보고하지 않고, 서버가 요청을 받아
    응답할 수 있는 상태인지만 ``{"status": "UP"}`` 로 알린다(§2-4 공통 규칙).
    """
    return {"status": "UP"}
