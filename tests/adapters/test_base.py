"""DocumentSourceAdapter 추상 인터페이스 + ActiveIds / ChangeEvent 검증."""

import pytest

from app.adapters.base import ActiveIds, ChangeEvent, DocumentSourceAdapter


def test_document_source_adapter_is_abstract() -> None:
    # 추상 인터페이스는 직접 인스턴스화할 수 없다
    with pytest.raises(TypeError):
        DocumentSourceAdapter()  # type: ignore[abstract]


def test_active_ids_defaults_empty() -> None:
    ids = ActiveIds()
    assert ids.pages == set()
    assert ids.attachments == set()


def test_active_ids_holds_sets() -> None:
    ids = ActiveIds(pages={"100001", "100002"}, attachments={"100001-att-0"})
    assert "100001" in ids.pages
    assert len(ids.attachments) == 1


def test_change_event_model() -> None:
    ev = ChangeEvent(event_type="updated", page_id="100001")
    assert ev.event_type == "updated"
    assert ev.attachment_id is None
