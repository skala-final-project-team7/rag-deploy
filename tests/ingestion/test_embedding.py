"""Dual Embedding 입력·멱등성 로직 검증 (feature5-A) — db-schema.md §1, app/CLAUDE.md §4.

pool_embedding_texts: Pool별 임베딩 입력 텍스트 구성.
should_skip_embedding: embedding_cache 기반 재임베딩 멱등성 판정.
"""

from datetime import datetime

from app.ingestion.embedding import pool_embedding_texts, should_skip_embedding
from app.ingestion.vector_store import CONTENT_POOL, LABEL_POOL, TITLE_POOL
from app.schemas.chunk import Chunk, ChunkMetadata
from app.schemas.enums import SourceType

_PAGE_METADATA = ChunkMetadata(
    chunk_id="chunk-abc123",
    page_id="CONF-PAGE-1",
    page_title="EKS 운영 가이드",
    section_header="개요",
    section_path="Cloud 운영 문서 > 개요",
    chunk_index=0,
    labels=["eks", "운영"],
    doc_type="operation",
    space_key="CLOUD",
    allowed_groups=["space:CLOUD"],
    allowed_users=[],
    webui_link="/display/CLOUD/eks",
    last_modified=datetime.fromisoformat("2026-04-22T08:15:00+09:00"),
    source_type=SourceType.PAGE,
    token_count=120,
)


def test_pool_embedding_texts_page_chunk() -> None:
    chunk = Chunk(text="EKS 클러스터 운영 본문입니다", metadata=_PAGE_METADATA)
    texts = pool_embedding_texts(chunk)
    # title_pool: page_title + section_header
    assert texts[TITLE_POOL] == "EKS 운영 가이드 개요"
    # content_pool: 청크 본문 텍스트
    assert texts[CONTENT_POOL] == "EKS 클러스터 운영 본문입니다"
    # label_pool: labels + space_key + doc_type
    assert texts[LABEL_POOL] == "eks 운영 CLOUD operation"


def test_pool_embedding_texts_attachment_chunk_uses_filename() -> None:
    metadata = _PAGE_METADATA.model_copy(
        update={
            "source_type": SourceType.ATTACHMENT,
            "attachment_filename": "EKS_운영_상세_매뉴얼_v2.3.docx",
            "section_header": "[시트1] 행 1~10",
        }
    )
    chunk = Chunk(text="첨부 본문", metadata=metadata)
    texts = pool_embedding_texts(chunk)
    # 첨부 청크의 title_pool은 attachment_filename + section_header
    assert texts[TITLE_POOL] == "EKS_운영_상세_매뉴얼_v2.3.docx [시트1] 행 1~10"
    assert texts[CONTENT_POOL] == "첨부 본문"


def test_should_skip_embedding_same_version() -> None:
    # 캐시 버전이 현재와 같으면 재임베딩을 스킵한다 (멱등성)
    assert should_skip_embedding(version_number=3, cached_version=3) is True


def test_should_skip_embedding_different_version() -> None:
    # 버전이 다르면 재임베딩이 필요하다
    assert should_skip_embedding(version_number=3, cached_version=2) is False


def test_should_skip_embedding_not_cached() -> None:
    # 캐시에 없으면(None) 재임베딩이 필요하다
    assert should_skip_embedding(version_number=3, cached_version=None) is False
