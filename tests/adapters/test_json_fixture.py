"""JsonFixtureSourceAdapter — 매핑 단위 테스트 + samples 전체 로드 통합 테스트.

데이터 계층 검증: samples/*.json(Atlassian 응답 포맷) → PageObject 변환이
스키마(feature1)와 정합하는지, 92페이지 전체가 오류 없이 로드되는지 확인한다.
"""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.adapters.base import ActiveIds
from app.adapters.json_fixture import (
    JsonFixtureSourceAdapter,
    infer_extracted_format,
    parse_atlassian_datetime,
)
from app.schemas.enums import ExtractedFormat
from app.schemas.page_object import PageObject

SAMPLES_DIR = Path(__file__).resolve().parents[2] / "samples"

_RAW_PAGE = {
    "id": "100001",
    "type": "page",
    "status": "current",
    "title": "EKS 장애 대응 가이드",
    "space": {"key": "CLOUD", "name": "Cloud Platform"},
    "version": {"number": 19, "when": "2026-04-22T08:15:00.000+0900"},
    "ancestors": [
        {"id": "10000", "title": "Cloud 운영 문서"},
        {"id": "10010", "title": "EKS 운영"},
    ],
    "metadata": {"labels": {"results": [{"name": "eks"}, {"name": "장애대응"}]}},
    "body": {"storage": {"representation": "storage", "value": "<h2>EKS 장애 대응 절차</h2>"}},
    "_links": {"webui": "/display/CLOUD/EKS+장애+대응+가이드"},
    "attachments": [
        {
            "filename": "EKS_운영_상세_매뉴얼_v2.3.docx",
            "title": "EKS 운영 상세 매뉴얼 v2.3",
            "comment": "통합 매뉴얼",
            "content_type": (
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            ),
        }
    ],
}


# --- 헬퍼 단위 테스트 ---


def test_parse_atlassian_datetime_offset_without_colon() -> None:
    # Atlassian Confluence: '+0900' (콜론 없는 오프셋)
    dt = parse_atlassian_datetime("2026-04-22T08:15:00.000+0900")
    assert dt.year == 2026 and dt.tzinfo is not None


def test_parse_atlassian_datetime_offset_with_colon() -> None:
    # datadog 임포트: '+00:00' (마이크로초)
    dt = parse_atlassian_datetime("2026-05-11T01:12:59.114776+00:00")
    assert dt.year == 2026 and dt.tzinfo is not None


def test_infer_extracted_format() -> None:
    docx = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    xlsx = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    assert infer_extracted_format(docx) is ExtractedFormat.RAW_TEXT
    assert infer_extracted_format("application/pdf") is ExtractedFormat.RAW_TEXT
    assert infer_extracted_format(xlsx) is ExtractedFormat.SHEET_SERIALIZED
    assert infer_extracted_format("text/csv") is ExtractedFormat.SHEET_SERIALIZED


# --- 매핑 단위 테스트 ---


def test_map_page_field_mapping() -> None:
    adapter = JsonFixtureSourceAdapter(samples_dir=SAMPLES_DIR)
    page = adapter._map_page(_RAW_PAGE)
    assert isinstance(page, PageObject)
    assert page.page_id == "100001"
    assert page.space_key == "CLOUD"
    assert page.title == "EKS 장애 대응 가이드"
    assert page.body_html.startswith("<h2>")
    assert page.version_number == 19
    assert page.webui_link == "/display/CLOUD/EKS+장애+대응+가이드"
    assert page.labels == ["eks", "장애대응"]
    assert page.ancestors == ["Cloud 운영 문서", "EKS 운영"]


def test_map_page_synthesizes_acl() -> None:
    # 샘플 데이터엔 ACL 필드가 없음 → PoC 합성 (space_key 기반). is_acl_missing False
    adapter = JsonFixtureSourceAdapter(samples_dir=SAMPLES_DIR)
    page = adapter._map_page(_RAW_PAGE)
    assert page.allowed_groups  # 비어 있지 않음
    assert page.is_acl_missing is False


def test_map_page_attachment_mapping() -> None:
    adapter = JsonFixtureSourceAdapter(samples_dir=SAMPLES_DIR)
    page = adapter._map_page(_RAW_PAGE)
    assert len(page.attachments) == 1
    att = page.attachments[0]
    assert att.filename == "EKS_운영_상세_매뉴얼_v2.3.docx"
    assert att.extracted_format is ExtractedFormat.RAW_TEXT
    assert att.parent_page_id == "100001"
    # 텍스트 추출은 다운스트림(feature4) 책임 — 어댑터는 빈 문자열로 전달
    assert att.extracted_text == ""
    assert att.attachment_id  # 합성된 id


def test_map_page_without_attachments_key() -> None:
    raw = {k: v for k, v in _RAW_PAGE.items() if k != "attachments"}
    adapter = JsonFixtureSourceAdapter(samples_dir=SAMPLES_DIR)
    page = adapter._map_page(raw)
    assert page.attachments == []


# --- samples 전체 로드 통합 테스트 ---


@pytest.fixture
def adapter() -> JsonFixtureSourceAdapter:
    return JsonFixtureSourceAdapter(samples_dir=SAMPLES_DIR)


def test_loads_all_92_pages(adapter: JsonFixtureSourceAdapter) -> None:
    pages = list(adapter.fetch_pages())
    # confluence 57 + datadog 35 = 92
    assert len(pages) == 92
    assert all(isinstance(p, PageObject) for p in pages)


def test_all_pages_have_acl(adapter: JsonFixtureSourceAdapter) -> None:
    # PoC ACL 합성으로 92페이지 전부 색인 가능 상태여야 한다
    pages = list(adapter.fetch_pages())
    assert all(not p.is_acl_missing for p in pages)


def test_exactly_four_attachments(adapter: JsonFixtureSourceAdapter) -> None:
    pages = list(adapter.fetch_pages())
    attachments = [a for p in pages for a in p.attachments]
    assert len(attachments) == 4
    assert {a.extracted_format for a in attachments} == {
        ExtractedFormat.RAW_TEXT,
        ExtractedFormat.SHEET_SERIALIZED,
    }


def test_list_active_ids(adapter: JsonFixtureSourceAdapter) -> None:
    ids = adapter.list_active_ids()
    assert isinstance(ids, ActiveIds)
    assert len(ids.pages) == 92
    assert len(ids.attachments) == 4


def test_known_page_spot_check(adapter: JsonFixtureSourceAdapter) -> None:
    pages = {p.page_id: p for p in adapter.fetch_pages()}
    page = pages["100001"]
    assert page.title == "EKS 장애 대응 가이드"
    assert page.space_key == "CLOUD"
    assert "eks" in page.labels
    assert len(page.attachments) == 1


def test_fetch_pages_since_filter(adapter: JsonFixtureSourceAdapter) -> None:
    future = datetime(2030, 1, 1, tzinfo=UTC)
    past = datetime(2000, 1, 1, tzinfo=UTC)
    assert len(list(adapter.fetch_pages(since=future))) == 0
    assert len(list(adapter.fetch_pages(since=past))) == 92


def test_watch_changes_is_empty_for_fixture(adapter: JsonFixtureSourceAdapter) -> None:
    # 정적 JSON 픽스처는 실시간 변경이 없다
    assert list(adapter.watch_changes()) == []
