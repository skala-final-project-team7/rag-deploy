"""Multi-Pool Vector Store payload 구성 검증 (feature5-A) — db-schema.md §1.2.

build_point_payload: Chunk를 Qdrant Point payload dict로 변환한다.
"""

from datetime import datetime

from app.ingestion.vector_store import (
    CONTENT_POOL,
    LABEL_POOL,
    POOL_NAMES,
    TITLE_POOL,
    build_point_payload,
)
from app.schemas.chunk import Chunk, ChunkMetadata
from app.schemas.enums import ExtractedFormat, SourceType

_LAST_MODIFIED = datetime.fromisoformat("2026-04-22T08:15:00+09:00")

_PAGE_METADATA = ChunkMetadata(
    chunk_id="chunk-abc123",
    page_id="CONF-PAGE-1",
    page_title="EKS 운영 가이드",
    section_header="개요",
    section_path="Cloud 운영 문서 > EKS 운영 > 개요",
    chunk_index=2,
    labels=["eks", "운영"],
    doc_type="operation",
    space_key="CLOUD",
    allowed_groups=["space:CLOUD"],
    allowed_users=["user:taesung"],
    webui_link="/display/CLOUD/eks",
    last_modified=_LAST_MODIFIED,
    source_type=SourceType.PAGE,
    token_count=120,
)

_DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _page_chunk(text: str = "EKS 클러스터 운영 본문") -> Chunk:
    return Chunk(text=text, metadata=_PAGE_METADATA)


def _attachment_chunk(text: str = "[시트1] 행 1~10") -> Chunk:
    metadata = _PAGE_METADATA.model_copy(
        update={
            "source_type": SourceType.ATTACHMENT,
            "attachment_id": "CONF-PAGE-1-att-0",
            "attachment_filename": "EKS_운영_상세_매뉴얼_v2.3.docx",
            "attachment_mime": _DOCX_MIME,
            "extracted_format": ExtractedFormat.RAW_TEXT,
        }
    )
    return Chunk(text=text, metadata=metadata)


def test_pool_names_match_db_schema() -> None:
    assert POOL_NAMES == (TITLE_POOL, CONTENT_POOL, LABEL_POOL)
    assert TITLE_POOL == "title_pool"
    assert CONTENT_POOL == "content_pool"
    assert LABEL_POOL == "label_pool"


def test_build_point_payload_common_fields() -> None:
    payload = build_point_payload(_page_chunk(), version_number=7)
    assert payload["page_id"] == "CONF-PAGE-1"
    assert payload["page_title"] == "EKS 운영 가이드"
    assert payload["section_header"] == "개요"
    assert payload["section_path"] == "Cloud 운영 문서 > EKS 운영 > 개요"
    assert payload["chunk_index"] == 2
    assert payload["labels"] == ["eks", "운영"]
    assert payload["doc_type"] == "operation"
    assert payload["space_key"] == "CLOUD"
    assert payload["allowed_groups"] == ["space:CLOUD"]
    assert payload["allowed_users"] == ["user:taesung"]
    assert payload["webui_link"] == "/display/CLOUD/eks"
    assert payload["last_modified"] == _LAST_MODIFIED.isoformat()


def test_build_point_payload_includes_chunk_id() -> None:
    # Qdrant Point ID는 UUID/uint64만 받으므로 SHA1 hex chunk_id를 직접 Point ID로 쓸 수
    # 없다. 어댑터가 uuid5로 매핑하고 원본 chunk_id는 payload에 보존한다 — 검색 결과에서
    # SearchHit.chunk_id로 복원하기 위해 payload 필드로 동봉된다 (db-schema.md §1.2).
    payload = build_point_payload(_page_chunk(), version_number=1)
    assert payload["chunk_id"] == "chunk-abc123"


def test_build_point_payload_injects_version_number() -> None:
    # version_number는 ChunkMetadata에 없어 부모 PageObject에서 별도 인자로 주입된다
    assert build_point_payload(_page_chunk(), version_number=7)["version_number"] == 7
    assert build_point_payload(_page_chunk(), version_number=1)["version_number"] == 1


def test_build_point_payload_defaults_is_deleted_false() -> None:
    # soft-delete 플래그(ADR 0003 항목 4)는 신규/재색인 upsert 에서 항상 False.
    # 삭제 확정은 store.soft_delete_by_* 가 True 로 set_payload 한다.
    assert build_point_payload(_page_chunk(), version_number=1)["is_deleted"] is False


def test_build_point_payload_page_chunk_has_null_attachment_fields() -> None:
    payload = build_point_payload(_page_chunk(), version_number=1)
    assert payload["source_type"] == "page"
    assert payload["attachment_id"] is None
    assert payload["attachment_filename"] is None
    assert payload["attachment_mime"] is None
    assert payload["extracted_format"] is None


def test_build_point_payload_attachment_chunk_fields() -> None:
    payload = build_point_payload(_attachment_chunk(), version_number=1)
    assert payload["source_type"] == "attachment"
    assert payload["attachment_id"] == "CONF-PAGE-1-att-0"
    assert payload["attachment_filename"] == "EKS_운영_상세_매뉴얼_v2.3.docx"
    assert payload["attachment_mime"] == _DOCX_MIME
    assert payload["extracted_format"] == "raw_text"


def test_build_point_payload_text_preview_truncated_to_200() -> None:
    payload = build_point_payload(_page_chunk("가" * 500), version_number=1)
    assert payload["text_preview"] == "가" * 200
    assert len(payload["text_preview"]) == 200


def test_build_point_payload_text_preview_keeps_short_text() -> None:
    payload = build_point_payload(_page_chunk("짧은 본문"), version_number=1)
    assert payload["text_preview"] == "짧은 본문"


def test_build_point_payload_stores_full_text() -> None:
    """feature17c-7 — payload 에 풀 텍스트(text)를 저장한다 (rerank·생성기 풀텍스트용).

    text_preview(200자)는 UI 미리보기로 유지하고, text 는 절단 없이 전체를 담는다.
    """
    payload = build_point_payload(_page_chunk("가" * 500), version_number=1)
    assert payload["text"] == "가" * 500
    assert len(payload["text"]) == 500
    # 미리보기는 여전히 200자로 절단 (UI 출처 카드용).
    assert payload["text_preview"] == "가" * 200


def test_build_point_payload_includes_token_count() -> None:
    # 5-A 후속(2026-05-18) — Chunk 재구성 정합. 청커가 산출한 token_count를
    # payload에 동봉해 검색 단계(_chunk_from_search_hit)에서 그대로 복원하도록 한다.
    # 픽스처는 token_count=120 (ChunkMetadata 필수 필드).
    payload = build_point_payload(_page_chunk(), version_number=1)
    assert payload["token_count"] == 120
