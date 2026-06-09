"""응답 포맷터 — 검증된 답변·출처·검증 결과를 QueryResponse(UI JSON)로 변환 [Pipeline].

--------------------------------------------------
작성자 : 최태성
작성목적 : LINA RAG 파이프라인 Query 단계의 마지막 단계. 생성·검증을 거친 답변을
          QueryResponse로 변환하고, api-spec.md "표준 분기 응답" 규칙을 적용한다 —
          Cross-Encoder 최고 점수가 낮으면 저신뢰 분기(feedback_enabled=false),
          NOT_SUPPORTED 비율이 절반을 넘으면 답변을 차단하고 저신뢰 응답으로 대체한다
          (rag-pipeline-design.md §6 4.8, api-spec.md).
작성일 : 2026-05-15
변경사항 내역 (날짜, 변경목적, 변경내용 순)
  - 2026-05-15, 최초 작성, feature11-Pipeline — format_response (순수 변환 로직)
--------------------------------------------------
[호환성]
  - Python 3.11.x, Pydantic 2.7+
  - NOTE: 본 모듈은 "생성된 답변을 응답으로 변환"하는 순수 함수다. Source 객체 생성
          (Chunk + Cross-Encoder 점수 → Source)은 feature9-B, 검색 0건 early-exit와
          RagState→인자 추출 노드 래퍼는 Query 그래프 조립(feature11 통합)이 담당한다.
--------------------------------------------------
"""

from app.schemas.enums import Intent, LlmModel, VerificationStatus
from app.schemas.response import QueryResponse, Source, Verification

# api-spec.md "표준 분기 응답" 임계값
# feature17c-2 (2026-05-20): Cross-Encoder temperature scaling(T=4)로 Source.score
# 분포가 펴져(강관련 ~88 / 중관련 ~77 / 무관 ~51) saturation(전부 100)이 해소됨에 따라
# 저신뢰 임계값을 20→55 로 재조정 (select_reranked LOW_CONFIDENCE_THRESHOLD 0.55 와 정합).
LOW_CONFIDENCE_SCORE = 55  # Cross-Encoder 최고 점수(0~100)가 이 미만이면 저신뢰 분기
VERIFICATION_BLOCK_RATIO = 0.5  # NOT_SUPPORTED 비율이 이 값을 초과하면 답변 차단

# 답변 차단 시 대체하는 저신뢰 응답 메시지.
BLOCKED_ANSWER_MESSAGE = (
    "검증 결과 답변의 상당 부분이 출처로 뒷받침되지 않아 답변 제공을 보류합니다. "
    "아래 참고 출처를 직접 확인해 주세요."
)


def _is_low_confidence(sources: list[Source]) -> bool:
    """Cross-Encoder 최고 점수가 임계 미만이거나 출처가 없으면 저신뢰 분기."""
    if not sources:
        return True
    return max(source.score for source in sources) < LOW_CONFIDENCE_SCORE


def _not_supported_ratio(verification: list[Verification]) -> float:
    """검증 문장 중 NOT_SUPPORTED 비율. 검증 결과가 없으면 0.0."""
    if not verification:
        return 0.0
    not_supported = sum(
        1 for item in verification if item.status is VerificationStatus.NOT_SUPPORTED
    )
    return not_supported / len(verification)


def format_response(
    answer: str,
    sources: list[Source],
    verification: list[Verification],
    intent: Intent,
    used_llm: LlmModel,
    latency_ms: int,
) -> QueryResponse:
    """생성·검증을 거친 답변을 QueryResponse로 변환한다 (rag-pipeline-design.md §6 4.8).

    api-spec.md "표준 분기 응답" 규칙을 적용한다:
    - NOT_SUPPORTED 비율이 VERIFICATION_BLOCK_RATIO를 초과하면 답변을 차단하고
      BLOCKED_ANSWER_MESSAGE로 대체한다 (feedback_enabled=False). 차단이 저신뢰보다 우선한다.
    - Cross-Encoder 최고 점수가 LOW_CONFIDENCE_SCORE 미만이면 저신뢰 분기로 보아
      feedback_enabled=False로 둔다 (답변 자체는 '참고용'으로 유지).
    - 그 외에는 답변을 그대로 두고 feedback_enabled=True.
    출처·검증 결과는 어느 분기에서도 투명성을 위해 그대로 응답에 담는다.

    Args:
        answer: 생성기가 만든 답변 텍스트 (`[#n]` 인용 마커 포함 가능).
        sources: 인용 출처 카드 목록 (Cross-Encoder 점수 포함).
        verification: 문장별 검증 결과.
        intent: 질의 라우터가 분류한 의도.
        used_llm: 답변 생성에 사용된 LLM.
        latency_ms: 질의 처리 지연 시간(ms).

    Returns:
        UI 렌더링용 QueryResponse.
    """
    is_blocked = _not_supported_ratio(verification) > VERIFICATION_BLOCK_RATIO
    is_low_confidence = is_blocked or _is_low_confidence(sources)
    final_answer = BLOCKED_ANSWER_MESSAGE if is_blocked else answer
    return QueryResponse(
        answer=final_answer,
        intent=intent,
        used_llm=used_llm,
        latency_ms=latency_ms,
        sources=sources,
        verification=verification,
        feedback_enabled=not is_low_confidence,
    )
