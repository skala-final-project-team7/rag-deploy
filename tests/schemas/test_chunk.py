"""Chunk / ChunkMetadata 스키마 + make_chunk_id 멱등성 검증 (chunking-strategy.md §6)."""

import pytest
from pydantic import ValidationError

from app.schemas.chunk import Chunk, ChunkMetadata, make_chunk_id
from app.schemas.enums import ExtractedFormat, SourceType

_META_KWARGS = dict(
    chunk_id="abc123",
    page_id="CONF-PAGE-12345",
    page_title="S3 AccessDenied 트러블슈팅",
    section_header="증상",
    section_path="운영/AWS/S3 AccessDenied 트러블슈팅 > 증상",
    chunk_index=0,
    doc_type="troubleshoot",
    space_key="INFRA",
    allowed_groups=["sre-team"],
    allowed_users=[],
    webui_link="/display/INFRA/S3-AccessDenied#증상",
    last_modified="2026-05-01T03:21:00+09:00",
    source_type="page",
    token_count=412,
)


def test_make_chunk_id_is_deterministic() -> None:
    a = make_chunk_id("CONF-PAGE-1", 0)
    b = make_chunk_id("CONF-PAGE-1", 0)
    assert a == b


def test_make_chunk_id_varies_by_index_and_attachment() -> None:
    base = make_chunk_id("CONF-PAGE-1", 0)
    assert make_chunk_id("CONF-PAGE-1", 1) != base
    assert make_chunk_id("CONF-PAGE-1", 0, "CONF-ATT-9") != base


def test_make_chunk_id_is_sha1_hex() -> None:
    cid = make_chunk_id("CONF-PAGE-1", 0)
    assert len(cid) == 40
    int(cid, 16)  # 16진수로 파싱 가능해야 함


def test_chunk_metadata_body_chunk() -> None:
    meta = ChunkMetadata(**_META_KWARGS)
    assert meta.source_type is SourceType.PAGE
    # 본문 청크는 첨부 전용 필드가 None
    assert meta.attachment_id is None
    assert meta.attachment_filename is None
    assert meta.extracted_format is None
    assert meta.labels == []


def test_chunk_metadata_attachment_chunk() -> None:
    meta = ChunkMetadata(
        **{
            **_META_KWARGS,
            "source_type": "attachment",
            "doc_type": "xlsx",
            "attachment_id": "CONF-ATT-99001",
            "attachment_filename": "prod_cost_2026Q1.xlsx",
            "attachment_mime": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "extracted_format": "sheet_serialized",
        }
    )
    assert meta.source_type is SourceType.ATTACHMENT
    assert meta.attachment_id == "CONF-ATT-99001"
    assert meta.extracted_format is ExtractedFormat.SHEET_SERIALIZED


def test_chunk_metadata_missing_required_raises() -> None:
    kwargs = dict(_META_KWARGS)
    del kwargs["token_count"]
    with pytest.raises(ValidationError):
        ChunkMetadata(**kwargs)


def test_chunk_holds_text_and_metadata() -> None:
    chunk = Chunk(text="[증상] 버킷 정책 Principal 누락", metadata=ChunkMetadata(**_META_KWARGS))
    assert chunk.text.startswith("[증상]")
    assert chunk.metadata.page_id == "CONF-PAGE-12345"
