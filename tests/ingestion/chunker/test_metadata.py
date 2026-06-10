"""build_metadata — 청크 메타데이터 19종 부착 + 무결성 규칙 검증."""

from app.ingestion.chunker.base import ChunkDraft
from app.ingestion.chunker.metadata import build_metadata
from app.schemas.enums import DocType, SourceType
from app.schemas.page_object import PageObject

_PAGE = PageObject(
    page_id="CONF-PAGE-1",
    space_key="CLOUD",
    space_id="SP-100",
    space_name="Cloud Platform",
    title="EKS 장애 대응 가이드",
    body_html="<h2>증상</h2>",
    version_number=3,
    last_modified="2026-04-22T08:15:00+09:00",
    allowed_groups=["space:CLOUD"],
    allowed_users=[],
    webui_link="/display/CLOUD/eks",
    labels=["eks", "장애대응"],
    ancestors=["Cloud 운영 문서", "EKS 운영"],
)


def test_build_metadata_common_fields() -> None:
    draft = ChunkDraft(text="증상: 노드 조인 실패", section_header="증상")
    meta = build_metadata(_PAGE, draft, chunk_index=0, doc_type=DocType.INCIDENT)
    assert meta.page_id == "CONF-PAGE-1"
    assert meta.page_title == "EKS 장애 대응 가이드"
    assert meta.section_header == "증상"
    assert meta.chunk_index == 0
    assert meta.doc_type == "incident"
    assert meta.space_key == "CLOUD"
    # A8 잔여(2026-06-10) — PageObject.space_id/space_name 이 메타로 전파된다.
    assert meta.space_id == "SP-100"
    assert meta.space_name == "Cloud Platform"
    assert meta.allowed_groups == ["space:CLOUD"]
    assert meta.labels == ["eks", "장애대응"]
    assert meta.token_count > 0


def test_section_path_includes_ancestors() -> None:
    draft = ChunkDraft(text="본문", section_header="증상")
    meta = build_metadata(_PAGE, draft, chunk_index=0, doc_type=DocType.INCIDENT)
    assert meta.section_path == "Cloud 운영 문서 > EKS 운영 > 증상"


def test_body_chunk_has_page_source_type_and_null_attachment_fields() -> None:
    draft = ChunkDraft(text="본문", section_header="증상")
    meta = build_metadata(_PAGE, draft, chunk_index=0, doc_type=DocType.INCIDENT)
    assert meta.source_type is SourceType.PAGE
    assert meta.attachment_id is None
    assert meta.attachment_filename is None
    assert meta.extracted_format is None


def test_empty_section_header_becomes_untitled() -> None:
    # 무결성 규칙: section_header 빈 문자열 금지
    draft = ChunkDraft(text="본문", section_header="")
    meta = build_metadata(_PAGE, draft, chunk_index=0, doc_type=DocType.OPERATION)
    assert meta.section_header == "untitled"


def test_chunk_id_is_deterministic_per_index() -> None:
    draft = ChunkDraft(text="본문", section_header="증상")
    m0a = build_metadata(_PAGE, draft, chunk_index=0, doc_type=DocType.INCIDENT)
    m0b = build_metadata(_PAGE, draft, chunk_index=0, doc_type=DocType.INCIDENT)
    m1 = build_metadata(_PAGE, draft, chunk_index=1, doc_type=DocType.INCIDENT)
    assert m0a.chunk_id == m0b.chunk_id  # 동일 입력 → 동일 id
    assert m0a.chunk_id != m1.chunk_id  # 인덱스 다르면 id 다름
