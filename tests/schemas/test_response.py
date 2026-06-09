"""QueryResponse / Source / Verification 스키마 검증 (docs/api-spec.md)."""

from app.schemas.enums import (
    Intent,
    LlmModel,
    SourceType,
    VerificationResult,
    VerificationStatus,
)
from app.schemas.response import (
    QueryResponse,
    Source,
    Verification,
    VerificationSummary,
)

_PAGE_SOURCE = dict(
    title="S3 AccessDenied 트러블슈팅 > 원인",
    score=87,
    path="운영 / AWS / S3 AccessDenied 트러블슈팅 > 원인",
    space_key="INFRA",
    source_type="page",
    confluence_url="https://confluence/pages/12345#원인",
    last_modified="2026-05-01T03:21:00+09:00",
    text_preview="버킷 정책의 Principal 필드가 비어 있을 경우...",
)

_ATTACHMENT_SOURCE = dict(
    title="prod_cost_2026Q1.xlsx > [2026Q1 비용]",
    score=78,
    path="운영 / FinOps / 2026Q1 보고 > 비용 시트",
    space_key="FINOPS",
    source_type="attachment",
    confluence_url="https://confluence/pages/12345#attachments",
    last_modified="2026-05-08T07:11:00+09:00",
    text_preview="[2026Q1 비용] 서비스: EKS | 월: 1월 | 비용(USD): 12340...",
    attachment_filename="prod_cost_2026Q1.xlsx",
    attachment_mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    download_url="https://confluence/download/attachments/99001/prod_cost_2026Q1.xlsx",
)


def test_page_source_optional_attachment_fields_none() -> None:
    src = Source(**_PAGE_SOURCE)
    assert src.source_type is SourceType.PAGE
    assert src.attachment_filename is None
    assert src.download_url is None


def test_attachment_source_carries_attachment_fields() -> None:
    src = Source(**_ATTACHMENT_SOURCE)
    assert src.source_type is SourceType.ATTACHMENT
    assert src.attachment_filename == "prod_cost_2026Q1.xlsx"
    assert src.download_url is not None


def test_verification_model() -> None:
    v = Verification(sentence_id=2, status="SUPPORTED", cited_chunks=[2, 3])
    assert v.status is VerificationStatus.SUPPORTED
    assert v.cited_chunks == [2, 3]


def test_query_response_round_trip() -> None:
    response = QueryResponse(
        answer="S3 AccessDenied는 IAM 정책 누락으로 발생합니다 [#1].",
        sources=[Source(**_PAGE_SOURCE), Source(**_ATTACHMENT_SOURCE)],
        verification=[Verification(sentence_id=1, status="PASS", cited_chunks=[1])],
        intent="장애대응",
        used_llm="gpt-4o",
        latency_ms=4120,
    )
    assert response.intent is Intent.INCIDENT_RESPONSE
    assert response.used_llm is LlmModel.GPT_4O
    assert response.feedback_enabled is True  # 기본값

    dumped = response.model_dump(mode="json")
    restored = QueryResponse.model_validate(dumped)
    assert restored == response
    assert len(restored.sources) == 2


# --- feature13: BFF 직렬화 / verification 집계 ---


def test_source_to_bff_payload_maps_fields() -> None:
    """Source.to_bff_payload — relevanceScore 0~1, updatedAt KST, 필드 rename."""
    src = Source(**_PAGE_SOURCE, page_id="12345", space_id="98310", space_name="INFRA Space")
    payload = src.to_bff_payload()
    assert payload["pageId"] == "12345"
    assert payload["spaceId"] == "98310"
    assert payload["spaceName"] == "INFRA Space"
    assert payload["url"] == _PAGE_SOURCE["confluence_url"]
    # score 87 → relevanceScore 0.87 (0~1).
    assert payload["relevanceScore"] == 0.87
    # sourceUpdatedAt 은 KST(+09:00) ISO 8601 (api-spec v2.2.0 필드명).
    assert payload["sourceUpdatedAt"].endswith("+09:00")
    # 내부 전용 필드는 노출하지 않는다.
    assert "score" not in payload
    assert "text_preview" not in payload


def test_source_to_bff_payload_naive_datetime_treated_as_utc() -> None:
    """naive last_modified 는 UTC 로 간주해 KST(+09:00)로 전환한다."""
    src = Source(**{**_PAGE_SOURCE, "last_modified": "2026-05-01T00:00:00"})
    payload = src.to_bff_payload()
    # UTC 00:00 → KST 09:00.
    assert payload["sourceUpdatedAt"] == "2026-05-01T09:00:00+09:00"


def test_verification_summary_all_supported() -> None:
    """전 문장 PASS/SUPPORTED → SUPPORTED, confidence 1.0."""
    summary = VerificationSummary.from_sentences(
        [
            Verification(sentence_id=1, status="PASS"),
            Verification(sentence_id=2, status="SUPPORTED"),
        ]
    )
    assert summary.verification_result is VerificationResult.SUPPORTED
    assert summary.confidence_score == 1.0


def test_verification_summary_partial() -> None:
    """NOT_SUPPORTED 1개(비율 ≤ 0.5) → PARTIALLY_SUPPORTED."""
    summary = VerificationSummary.from_sentences(
        [
            Verification(sentence_id=1, status="PASS"),
            Verification(sentence_id=2, status="PASS"),
            Verification(sentence_id=3, status="NOT_SUPPORTED"),
        ]
    )
    assert summary.verification_result is VerificationResult.PARTIALLY_SUPPORTED
    assert round(summary.confidence_score, 4) == round(2 / 3, 4)


def test_verification_summary_blocked_ratio() -> None:
    """NOT_SUPPORTED 비율 > 0.5 → NOT_SUPPORTED (차단 임계 정합)."""
    summary = VerificationSummary.from_sentences(
        [
            Verification(sentence_id=1, status="NOT_SUPPORTED"),
            Verification(sentence_id=2, status="NOT_SUPPORTED"),
            Verification(sentence_id=3, status="PASS"),
        ]
    )
    assert summary.verification_result is VerificationResult.NOT_SUPPORTED


def test_verification_summary_empty_is_not_supported() -> None:
    """문장 0건 → confidence 0.0 / NOT_SUPPORTED (보수적)."""
    summary = VerificationSummary.from_sentences([])
    assert summary.confidence_score == 0.0
    assert summary.verification_result is VerificationResult.NOT_SUPPORTED


def test_verification_summary_to_bff_payload() -> None:
    """집계 → SSE verification 이벤트 형식."""
    summary = VerificationSummary(
        confidence_score=0.85, verification_result=VerificationResult.SUPPORTED
    )
    assert summary.to_bff_payload() == {
        "confidenceScore": 0.85,
        "verificationResult": "SUPPORTED",
    }
