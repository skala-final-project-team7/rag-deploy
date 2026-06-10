"""Query API 응답 스키마 — QueryResponse / Source / Verification.

--------------------------------------------------
작성자 : 최태성
작성목적 : 응답 포맷터가 생성하는 UI 렌더링용 응답 객체를 정의한다.
작성일 : 2026-05-15
변경사항 내역 (날짜, 변경목적, 변경내용 순)
  - 2026-05-15, 최초 작성, feature1 schemas — docs/api-spec.md 응답 객체 스키마 정합
  - 2026-05-26, feature13 코드 마이그레이션 — BE 통합 스펙(/ml/query) 정합.
    Source 에 page_id/space_id/space_name 추가 + ``to_bff_payload`` (relevanceScore
    0~1 / updatedAt KST / 필드 rename) 직렬화 헬퍼. 문장별 Verification 집계용
    VerificationSummary 모델 + ``from_sentences`` 신설(confidenceScore +
    verificationResult). 내부 모델 필드(score 0~100 / last_modified 등)는 유지하고
    BFF 노출 형식은 직렬화 단계에서만 변환한다.
--------------------------------------------------
[호환성]
  - Python 3.11.x, Pydantic 2.7+
--------------------------------------------------
"""

from datetime import UTC, datetime, timedelta, timezone
from typing import Any

from pydantic import BaseModel, Field

from app.schemas.enums import (
    Intent,
    LlmModel,
    SourceType,
    VerificationResult,
    VerificationStatus,
)

# BE 통합 스펙 §시간 표기 정책 — 출처 updatedAt 은 KST(+09:00) 절대 전환으로 노출한다.
_KST = timezone(timedelta(hours=9))


class Source(BaseModel):
    """인용 출처 카드 1건. 첨부 출처일 때만 attachment_* / download_url이 채워진다.

    내부 필드(score 0~100, space_key, confluence_url, last_modified 등)는 파이프라인
    계산용으로 유지하고, BFF/FE 노출 형식은 ``to_bff_payload`` 에서 변환한다
    (relevanceScore 0~1 / updatedAt KST / pageId·spaceId·spaceName).
    """

    title: str
    score: int  # Cross-Encoder 관련도 0~100
    path: str
    space_key: str
    source_type: SourceType
    confluence_url: str
    last_modified: datetime
    text_preview: str
    # feature13 — BE sources 항목 필드. 검색 노드(feature9-B)가 채우며 PoC/미채움 시 빈 값.
    page_id: str = ""
    space_id: str = ""
    space_name: str = ""
    attachment_filename: str | None = None
    attachment_mime: str | None = None
    download_url: str | None = None

    def to_bff_payload(self) -> dict[str, Any]:
        """``docs/api-spec.md`` §1-1 sources 항목 형식으로 직렬화.

        - ``relevanceScore``: 내부 score(0~100 정수) ÷ 100 → 0~1 float.
        - ``sourceUpdatedAt``: last_modified 를 KST(+09:00)로 절대 전환한 ISO 8601 문자열.
          naive datetime 은 UTC 로 간주해 전환한다. (api-spec v2.2.0 필드명)
        - 필드명: pageId / spaceId / spaceName / url.
        """
        dt = self.last_modified
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return {
            "title": self.title,
            "pageId": self.page_id,
            "spaceId": self.space_id,
            "spaceName": self.space_name,
            "url": self.confluence_url,
            "sourceUpdatedAt": dt.astimezone(_KST).isoformat(),
            "relevanceScore": self.score / 100,
        }


class Verification(BaseModel):
    """답변 문장 1개의 검증 결과 (설계서 §4.7)."""

    sentence_id: int
    status: VerificationStatus
    cited_chunks: list[int] = Field(default_factory=list)


class VerificationSummary(BaseModel):
    """문장별 검증 결과를 단일 값으로 집계한 SSE ``verification`` 이벤트 페이로드.

    ``docs/api-spec.md`` §1-1 "verification" 집계 규칙을 그대로 구현한다.
    """

    confidence_score: float
    verification_result: VerificationResult

    @classmethod
    def from_sentences(cls, verification: list[Verification]) -> "VerificationSummary":
        """문장별 ``Verification`` 목록 → 집계.

        - ``confidence_score`` = (PASS+SUPPORTED 문장 수) / 전체 문장 수. 0개면 0.0.
        - ``verification_result``:
          - NOT_SUPPORTED 비율 > 0.5 → NOT_SUPPORTED (답변 차단 분기와 동일 임계)
          - 그 외 NOT_SUPPORTED 문장 1개 이상 → PARTIALLY_SUPPORTED
          - 전 문장이 PASS/SUPPORTED → SUPPORTED
          - 문장 0개 → NOT_SUPPORTED (confidence 0.0 과 정합, 보수적)
        """
        total = len(verification)
        if total == 0:
            return cls(
                confidence_score=0.0,
                verification_result=VerificationResult.NOT_SUPPORTED,
            )
        not_supported = sum(
            1 for item in verification if item.status == VerificationStatus.NOT_SUPPORTED
        )
        supported = total - not_supported
        confidence = supported / total
        if not_supported / total > 0.5:
            result = VerificationResult.NOT_SUPPORTED
        elif not_supported >= 1:
            result = VerificationResult.PARTIALLY_SUPPORTED
        else:
            result = VerificationResult.SUPPORTED
        return cls(confidence_score=confidence, verification_result=result)

    def to_bff_payload(self) -> dict[str, Any]:
        """BE 통합 스펙 verification 이벤트 형식으로 직렬화."""
        return {
            "confidenceScore": self.confidence_score,
            "verificationResult": self.verification_result.value,
        }


class QueryResponse(BaseModel):
    """POST /ml/query의 완성형 응답 객체 (docs/api-spec.md).

    intent / used_llm / latency_ms / feedback_enabled 는 SSE ``meta`` 이벤트로
    송신된다(api-spec v2.2.0 §1-1 — 7종 이벤트 정본. routes._sse_payload).
    """

    answer: str
    intent: Intent
    used_llm: LlmModel
    latency_ms: int
    sources: list[Source] = Field(default_factory=list)
    verification: list[Verification] = Field(default_factory=list)
    feedback_enabled: bool = True
    # api-spec v2.2.0 §1-1 meta 이벤트 ``title`` (Required: N) — LLM 이 생성한 현재 대화
    # 제목. 라우트가 답변 산출 후 titler 로 채운다. None 이면 meta 에서 생략한다(optional).
    title: str | None = None
