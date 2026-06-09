"""PageObject / Attachment 스키마 검증 (설계서 §7.1)."""

import pytest
from pydantic import ValidationError

from app.schemas.enums import ExtractedFormat
from app.schemas.page_object import Attachment, PageObject

_PAGE_KWARGS = dict(
    page_id="CONF-PAGE-12345",
    space_key="INFRA",
    title="S3 AccessDenied 트러블슈팅",
    body_html="<h2>증상</h2><p>버킷 정책 Principal 누락</p>",
    version_number=3,
    last_modified="2026-05-01T03:21:00+09:00",
    allowed_groups=["sre-team"],
    allowed_users=[],
    webui_link="/display/INFRA/S3-AccessDenied",
)

_ATTACHMENT_KWARGS = dict(
    attachment_id="CONF-ATT-99001",
    filename="prod_cost_2026Q1.xlsx",
    mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    extracted_text="[2026Q1 비용] 서비스: EKS | 월: 1월 | 비용(USD): 12340",
    extracted_format="sheet_serialized",
    download_url="https://confluence/download/attachments/99001/prod_cost_2026Q1.xlsx",
    parent_page_id="CONF-PAGE-12345",
    last_modified="2026-05-08T07:11:00+09:00",
)


def test_page_object_minimal_valid() -> None:
    page = PageObject(**_PAGE_KWARGS)
    assert page.page_id == "CONF-PAGE-12345"
    # 선택 필드는 빈 리스트 기본값
    assert page.labels == []
    assert page.ancestors == []
    assert page.attachments == []


def test_page_object_missing_required_field_raises() -> None:
    kwargs = dict(_PAGE_KWARGS)
    del kwargs["webui_link"]
    with pytest.raises(ValidationError):
        PageObject(**kwargs)


def test_is_acl_missing_true_when_both_empty() -> None:
    page = PageObject(**{**_PAGE_KWARGS, "allowed_groups": [], "allowed_users": []})
    assert page.is_acl_missing is True


def test_is_acl_missing_false_when_any_present() -> None:
    page_g = PageObject(**{**_PAGE_KWARGS, "allowed_groups": ["sre-team"], "allowed_users": []})
    page_u = PageObject(**{**_PAGE_KWARGS, "allowed_groups": [], "allowed_users": ["user_123"]})
    assert page_g.is_acl_missing is False
    assert page_u.is_acl_missing is False


def test_attachment_valid_and_format_enum() -> None:
    att = Attachment(**_ATTACHMENT_KWARGS)
    assert att.attachment_id == "CONF-ATT-99001"
    assert att.extracted_format is ExtractedFormat.SHEET_SERIALIZED
    assert att.file_size_bytes is None  # 선택 필드
    # ADR-0001: local_path는 선택 필드이며 기본값은 None.
    # download_url(사용자 노출 URL)과 의미가 분리되어 있다.
    assert att.local_path is None


def test_attachment_local_path_populated_separately() -> None:
    # local_path를 명시 주입해도 download_url은 그대로 유지된다 (ADR-0001)
    att = Attachment(**{**_ATTACHMENT_KWARGS, "local_path": "/tmp/x.xlsx"})
    assert att.local_path == "/tmp/x.xlsx"
    assert att.download_url == _ATTACHMENT_KWARGS["download_url"]


def test_page_object_with_attachments() -> None:
    page = PageObject(**{**_PAGE_KWARGS, "attachments": [_ATTACHMENT_KWARGS]})
    assert len(page.attachments) == 1
    assert isinstance(page.attachments[0], Attachment)
