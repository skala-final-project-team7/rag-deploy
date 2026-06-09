"""enums 값이 설계 문서(rag-pipeline-design.md / chunking-strategy.md)와 정합하는지 검증."""

from app.schemas.enums import (
    AttachmentType,
    DocType,
    ExtractedFormat,
    IngestionStage,
    IngestionStatus,
    Intent,
    LlmModel,
    SourceType,
    VerificationStatus,
)


def test_doc_type_has_six_body_types() -> None:
    assert {d.value for d in DocType} == {
        "incident",
        "operation",
        "faq",
        "meeting",
        "adr",
        "troubleshoot",
    }


def test_attachment_type_values() -> None:
    assert {a.value for a in AttachmentType} == {"pdf", "docx", "xlsx", "csv"}


def test_source_type_values() -> None:
    assert {s.value for s in SourceType} == {"page", "attachment"}


def test_extracted_format_values() -> None:
    assert {e.value for e in ExtractedFormat} == {"raw_text", "sheet_serialized"}


def test_intent_has_four_korean_intents() -> None:
    # 질의 라우터 출력 JSON 스키마와 정합 (설계서 §4.4.5)
    assert {i.value for i in Intent} == {"장애대응", "운영가이드", "정책절차", "이력조회"}


def test_verification_status_values() -> None:
    assert {v.value for v in VerificationStatus} == {"PASS", "SUPPORTED", "NOT_SUPPORTED"}


def test_ingestion_stage_values() -> None:
    assert {s.value for s in IngestionStage} == {
        "crawl",
        "analyze",
        "chunk",
        "embed",
        "upsert",
        "sync",
    }


def test_ingestion_status_includes_exception_codes() -> None:
    values = {s.value for s in IngestionStatus}
    assert "SUCCESS" in values
    # chunking-strategy.md §8 예외 상태 코드
    for code in (
        "PARTIAL_PARSE",
        "EMPTY_BODY",
        "INVALID_ACL",
        "UNSUPPORTED_ATTACH_TYPE",
        "ATTACH_ENCRYPTED",
        "LOW_QUALITY_ATTACH",
        "ATTACH_NO_HEADER",
        "OVERSIZE_ATOMIC",
        "TOKENIZER_FAIL",
    ):
        assert code in values


def test_llm_model_values() -> None:
    assert {m.value for m in LlmModel} == {"gpt-4o", "gpt-4o-mini"}


def test_enums_are_str_subclasses() -> None:
    # str Enum 이어야 JSON 직렬화·Qdrant payload 비교가 자연스럽다
    assert isinstance(DocType.INCIDENT, str)
    assert isinstance(Intent.INCIDENT_RESPONSE, str)
