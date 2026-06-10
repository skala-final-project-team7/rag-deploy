"""첨부 파일 분석기 검증 (feature6 Phase 1).

설계서 §3.3.B 정합 회귀:
- ① 유형 판별: mime/확장자 → AttachmentType ∈ {pdf, docx, xlsx, csv}.
  미지원 → UNSUPPORTED_ATTACH_TYPE
- ② 텍스트 유효성: extracted_text 200자 미만 또는 동일 문자 반복 비율 > 80%
  → LOW_QUALITY_ATTACH
- ②' 위임(2026-06-10, 코드 리뷰 P1-3): extracted_text 가 비어 있고 파일 원천
  (local_path/download_url)이 있으면 품질 게이트를 건너뛰고 파일 기반 추출
  (chunk_attachment)로 위임 — SUCCESS. 원천이 전혀 없으면 종전대로 LOW_QUALITY_ATTACH.

분석기는 ①② 두 단계만 책임. 메타데이터 부착(③)은 chunker `build_attachment_metadata`
가, Adaptive Chunker 호출(④)은 Ingestion 그래프 노드가 책임 (책임 분리 정합).
"""

from __future__ import annotations

import dataclasses
from datetime import datetime

import pytest

from app.ingestion.attachment_analyzer import (
    AttachmentAnalysisResult,
    analyze_attachment,
)
from app.schemas.enums import AttachmentType, ExtractedFormat, IngestionStatus
from app.schemas.page_object import Attachment

# 길이 제한 회피용 별칭 — Office Open XML mime은 표준상 길어 한 줄에 들어가지 않는다.
_DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
_XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


# --- 픽스처 헬퍼 ---

_DEFAULT_TEXT = (
    "본 첨부 파일은 PoC 단계 운영 매뉴얼의 일부 발췌입니다. " * 7
)  # 200자 이상(약 238자) + 일반 텍스트, 어떤 문자도 비율 80% 미만


def _attachment(
    *,
    attachment_id: str = "ATT-1",
    filename: str = "manual.pdf",
    mime_type: str = "application/pdf",
    extracted_text: str = _DEFAULT_TEXT,
    extracted_format: ExtractedFormat = ExtractedFormat.RAW_TEXT,
    download_url: str = "file:///tmp/sample",
    local_path: str | None = None,
) -> Attachment:
    return Attachment(
        attachment_id=attachment_id,
        filename=filename,
        mime_type=mime_type,
        extracted_text=extracted_text,
        extracted_format=extracted_format,
        download_url=download_url,
        parent_page_id="P1",
        last_modified=datetime.fromisoformat("2026-04-22T08:15:00+09:00"),
        local_path=local_path,
    )


# --- ① 유형 판별: mime 기반 ---


@pytest.mark.parametrize(
    "mime,expected",
    [
        ("application/pdf", AttachmentType.PDF),
        (_DOCX_MIME, AttachmentType.DOCX),
        (_XLSX_MIME, AttachmentType.XLSX),
        ("text/csv", AttachmentType.CSV),
    ],
)
def test_mime_classification(mime: str, expected: AttachmentType) -> None:
    result = analyze_attachment(_attachment(mime_type=mime))
    assert result.attachment_type is expected
    assert result.status is IngestionStatus.SUCCESS
    assert result.analyzable is True


# --- ① 유형 판별: 확장자 fallback ---


@pytest.mark.parametrize(
    "filename,expected",
    [
        ("runbook.pdf", AttachmentType.PDF),
        ("policy.docx", AttachmentType.DOCX),
        ("metrics.xlsx", AttachmentType.XLSX),
        ("logs.csv", AttachmentType.CSV),
    ],
)
def test_extension_classification_when_mime_unknown(
    filename: str, expected: AttachmentType
) -> None:
    # mime이 octet-stream(미상)이면 확장자로 분류 fallback
    result = analyze_attachment(
        _attachment(filename=filename, mime_type="application/octet-stream")
    )
    assert result.attachment_type is expected
    assert result.status is IngestionStatus.SUCCESS


# --- ① 미지원 mime + 확장자 둘 다 ---


def test_unsupported_mime_and_extension() -> None:
    result = analyze_attachment(_attachment(filename="diagram.png", mime_type="image/png"))
    assert result.attachment_type is None
    assert result.status is IngestionStatus.UNSUPPORTED_ATTACH_TYPE
    assert result.analyzable is False
    # reason은 디버깅용 — 빈 문자열 아님
    assert result.reason


def test_unsupported_mime_with_unsupported_extension() -> None:
    result = analyze_attachment(_attachment(filename="movie.mp4", mime_type="video/mp4"))
    assert result.status is IngestionStatus.UNSUPPORTED_ATTACH_TYPE
    assert result.attachment_type is None


# --- ② 텍스트 유효성: 길이 ---


def test_text_below_200_chars_marked_low_quality() -> None:
    short_text = "짧은 텍스트 예시입니다." * 2  # 30자 미만
    result = analyze_attachment(_attachment(extracted_text=short_text))
    assert result.status is IngestionStatus.LOW_QUALITY_ATTACH
    assert result.analyzable is False
    # attachment_type은 분류에 성공했으면 채워져 있음 (status가 LOW_QUALITY여도 정합)
    assert result.attachment_type is AttachmentType.PDF
    assert "200" in result.reason or "length" in result.reason.lower()


def test_empty_text_without_file_source_marked_low_quality() -> None:
    """빈 extracted_text + 파일 원천(local_path/download_url) 없음 → 종전대로 LOW_QUALITY.

    P1-3 위임은 파일 원천이 있을 때만 적용된다 — 원천이 전혀 없으면 추출을 위임할
    곳이 없으므로 품질 게이트가 그대로 차단한다(기존 동작 보존).
    """
    result = analyze_attachment(_attachment(extracted_text="", download_url="", local_path=None))
    assert result.status is IngestionStatus.LOW_QUALITY_ATTACH
    assert result.analyzable is False


# --- ②' 파일 기반 추출 위임 (2026-06-10, 코드 리뷰 P1-3) ---


def test_empty_text_with_download_url_delegates_to_file_extraction() -> None:
    """빈 extracted_text + download_url → 품질 게이트를 건너뛰고 SUCCESS(추출 위임).

    어댑터가 텍스트를 채우지 않는 경로(atlassian: download_url→다운로더)에서 모든
    첨부가 LOW_QUALITY_ATTACH 로 스킵되던 회귀(첨부 ingest 사실상 비활성)를 보호한다.
    """
    result = analyze_attachment(_attachment(extracted_text=""))  # download_url 기본 채움.
    assert result.status is IngestionStatus.SUCCESS
    assert result.analyzable is True
    # 유형 판별(①)은 그대로 선행 — 위임 결과에도 attachment_type 이 채워진다.
    assert result.attachment_type is AttachmentType.PDF
    assert "위임" in result.reason


def test_empty_text_with_local_path_delegates_to_file_extraction() -> None:
    """빈 extracted_text + local_path(fixture 경로) → download_url 없이도 SUCCESS(위임)."""
    result = analyze_attachment(
        _attachment(extracted_text="", download_url="", local_path="/tmp/fixtures/manual.pdf")
    )
    assert result.status is IngestionStatus.SUCCESS
    assert result.analyzable is True


def test_short_nonempty_text_still_low_quality_even_with_file_source() -> None:
    """텍스트가 채워져 있으면(빈 문자열 아님) 파일 원천이 있어도 품질 게이트 그대로 적용."""
    result = analyze_attachment(
        _attachment(extracted_text="짧음", local_path="/tmp/fixtures/manual.pdf")
    )
    assert result.status is IngestionStatus.LOW_QUALITY_ATTACH


def test_unsupported_type_takes_precedence_over_delegation() -> None:
    """① 유형 판별 실패는 빈 텍스트 위임보다 우선 — UNSUPPORTED_ATTACH_TYPE 유지."""
    result = analyze_attachment(
        _attachment(filename="diagram.png", mime_type="image/png", extracted_text="")
    )
    assert result.status is IngestionStatus.UNSUPPORTED_ATTACH_TYPE


# --- ② 텍스트 유효성: 동일 문자 반복 ---


def test_repeated_single_char_marked_low_quality() -> None:
    """동일 문자 반복 비율 > 80% — 같은 문자가 압도적으로 빈출하는 케이스 (예: OCR 노이즈)."""
    # 'a' 195자 + 다른 문자 5자 → 'a'의 비율 195/200 = 0.975 > 0.8
    noisy = "a" * 195 + "bcdef"
    result = analyze_attachment(_attachment(extracted_text=noisy))
    assert result.status is IngestionStatus.LOW_QUALITY_ATTACH
    assert result.analyzable is False
    assert "반복" in result.reason or "repetition" in result.reason.lower()


def test_normal_text_passes_repetition_check() -> None:
    """일반 한국어 텍스트는 어떤 문자의 비율도 80% 미만이어야 한다 (false positive 회귀 보호)."""
    result = analyze_attachment(_attachment(extracted_text=_DEFAULT_TEXT))
    assert result.status is IngestionStatus.SUCCESS
    assert result.analyzable is True


def test_whitespace_excluded_from_repetition_ratio() -> None:
    """공백·개행이 압도적으로 많은 경우(공백 그 자체)는 반복 분기에서 제외해 의미 있는 문자만 평가.

    공백을 포함하면 들여쓰기·줄바꿈이 많은 정상 첨부에서도 false positive가 난다.
    """
    text_with_lots_of_spaces = ("alpha beta gamma " * 50).strip()  # 공백이 토큰 수만큼
    result = analyze_attachment(_attachment(extracted_text=text_with_lots_of_spaces))
    assert result.status is IngestionStatus.SUCCESS


# --- 분류 + 유효성 결합: 미지원 mime이 우선 ---


def test_unsupported_mime_takes_precedence_over_length_check() -> None:
    """미지원 mime은 텍스트가 정상 길이여도 UNSUPPORTED_ATTACH_TYPE으로 분기 (① 우선)."""
    result = analyze_attachment(
        _attachment(
            filename="diagram.png",
            mime_type="image/png",
            extracted_text=_DEFAULT_TEXT,
        )
    )
    assert result.status is IngestionStatus.UNSUPPORTED_ATTACH_TYPE


# --- AttachmentAnalysisResult 값 객체 회귀 ---


def test_result_is_immutable_frozen_dataclass() -> None:
    result = analyze_attachment(_attachment())
    # frozen=True 라 set 시도는 FrozenInstanceError 로 차단. 향후 분기 추가에서 우발적
    # 변경이 새지 않도록 회귀 보호한다.
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.status = IngestionStatus.LOW_QUALITY_ATTACH  # type: ignore[misc]


def test_result_carries_attachment_id() -> None:
    """결과에는 호출자가 jobs.py에 적재할 때 사용할 attachment_id가 동봉되어야 한다."""
    result = analyze_attachment(_attachment(attachment_id="CONF-ATT-42"))
    assert isinstance(result, AttachmentAnalysisResult)
    assert result.attachment_id == "CONF-ATT-42"
